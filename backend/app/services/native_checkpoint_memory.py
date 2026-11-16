"""Bounded checkpoint state for the pinned native CAMEL chat history."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import time

from camel.memories import ChatHistoryMemory, MemoryRecord, ScoreBasedContextCreator
from camel.memories.blocks.chat_history_block import ChatHistoryBlock
from camel.storages.key_value_storages.in_memory import InMemoryKeyValueStorage
from camel.types import OpenAIBackendRole, RoleType

from .native_recording_contracts import (
    IDENTIFIER,
    MAX_JSON,
    RecordingError,
    _strict_json_text,
    strict_json,
)

_KIND = "native_camel_memory"
_FIELDS = {"kind", "version", "agent_id", "window_size", "keep_rate", "token_limit", "records"}
_RECORD_FIELDS = {"uuid", "message", "role_at_backend", "extra_info", "timestamp", "agent_id"}
_MESSAGE_FIELDS = {"__class__", "role_name", "role_type", "content"}
_TEXT_OPTIONAL = {"image_list", "video_bytes", "image_detail", "video_detail", "parsed"}
_BASE_ROLES = {"system", "developer", "user", "assistant"}
_FUNCTION_FIELDS = {"func_name", "args", "result", "tool_call_id", "mask_output"}
_DETAIL_LEVELS = ("auto", "low", "high")
_MAX_DEPTH = 16
_MAX_NODES = 100000
_ROLE_NAME_PATH = ("records", None, "message", "role_name")
_SYSTEM_ROLE_NAME_TAG = {"$camel_role_name": "system"}
_JSON_LITERALS = {"t": "true", "f": "false", "n": "null"}
_JSON_SPACE = (" ", "\t", "\n", "\r")
_JSON_SHORT_ESCAPES = ('"', "\\", "/", "b", "f", "n", "r", "t")
_JSON_HEX = frozenset("0123456789abcdefABCDEF")
_JSON_CHECK_INTERVAL = 1024


class _MemoryAdmissionBudget:
    """One value-node allowance shared by the cohort and rendered arguments."""

    __slots__ = ("_deadline_monotonic", "_remaining_nodes")

    def __init__(self, deadline_monotonic, *, max_nodes=100000):
        if (type(max_nodes) is not int or not 1 <= max_nodes <= _MAX_NODES
                or type(deadline_monotonic) not in (int, float)):
            raise RecordingError("invalid_request")
        try:
            if not math.isfinite(deadline_monotonic):
                raise RecordingError("invalid_request")
        except OverflowError:
            raise RecordingError("invalid_request") from None
        self._deadline_monotonic = deadline_monotonic
        self._remaining_nodes = max_nodes
        self.check()

    def check(self):
        _check_cut_deadline(self._deadline_monotonic)

    def charge_node(self):
        self.check()
        if not self._remaining_nodes:
            raise RecordingError("limit_exceeded")
        self._remaining_nodes -= 1


class _MemoryJSONPreflight:
    """Walk the JSON grammar without allocating values, keys or tokens."""

    def __init__(self, text, budget, byte_limit):
        self._text = text
        self._budget = budget
        self._length = len(text)
        self._offset = 0
        self._next_check = _JSON_CHECK_INTERVAL
        # Each code point costs at least one byte. Charge the UTF-8 surcharge
        # in strings, the only grammar position that permits non-ASCII.
        self._remaining_bytes = byte_limit - self._length
        if self._remaining_bytes < 0:
            raise RecordingError("limit_exceeded")

    def check(self):
        self._value(0)
        self._skip_space()
        if self._offset != self._length:
            raise RecordingError()

    def _peek(self):
        if self._offset < self._length:
            return self._text[self._offset]
        return ""

    def _advance(self, count=1):
        self._offset += count
        if self._offset >= self._next_check:
            self._budget.check()
            self._next_check = self._offset + _JSON_CHECK_INTERVAL

    def _take(self, char):
        if self._peek() != char:
            return False
        self._advance()
        return True

    def _expect(self, char):
        if not self._take(char):
            raise RecordingError()

    def _skip_space(self):
        while self._peek() in _JSON_SPACE:
            self._advance()

    def _value(self, depth):
        if depth > _MAX_DEPTH:
            raise RecordingError("limit_exceeded")
        self._budget.charge_node()
        self._skip_space()
        char = self._peek()
        if char == "{":
            self._advance()
            self._object(depth)
        elif char == "[":
            self._advance()
            self._array(depth)
        elif char == '"':
            self._string()
        elif char in _JSON_LITERALS:
            literal = _JSON_LITERALS[char]
            if not self._text.startswith(literal, self._offset):
                raise RecordingError()
            self._advance(len(literal))
        else:
            self._number()

    def _object(self, depth):
        self._skip_space()
        if self._take("}"):
            return
        while True:
            # A name has no value-node charge. Each admitted name requires
            # the member value charged immediately below.
            self._string()
            self._skip_space()
            self._expect(":")
            self._value(depth + 1)
            self._skip_space()
            if self._take("}"):
                return
            self._expect(",")
            self._skip_space()

    def _array(self, depth):
        self._skip_space()
        if self._take("]"):
            return
        while True:
            self._value(depth + 1)
            self._skip_space()
            if self._take("]"):
                return
            self._expect(",")

    def _string(self):
        self._expect('"')
        while True:
            char = self._peek()
            if not char or ord(char) < 32:
                raise RecordingError()
            self._advance()
            if char == '"':
                return
            if char == "\\":
                self._escape()
            elif not char.isascii():
                self._charge_utf8(ord(char))

    def _escape(self):
        char = self._peek()
        if char in _JSON_SHORT_ESCAPES:
            self._advance()
            return
        self._expect("u")
        for _ in range(4):
            if self._peek() not in _JSON_HEX:
                raise RecordingError()
            self._advance()

    def _charge_utf8(self, codepoint):
        if 0xD800 <= codepoint <= 0xDFFF:
            raise RecordingError()
        if codepoint < 0x800:
            self._remaining_bytes -= 1
        elif codepoint < 0x10000:
            self._remaining_bytes -= 2
        else:
            self._remaining_bytes -= 3
        if self._remaining_bytes < 0:
            raise RecordingError("limit_exceeded")

    def _digits(self):
        start = self._offset
        while "0" <= self._peek() <= "9":
            self._advance()
        if self._offset == start:
            raise RecordingError()

    def _number(self):
        negative = self._take("-")
        start = self._offset
        if not self._take("0"):
            self._digits()
        integer_end = self._offset
        fraction = self._take(".")
        if fraction:
            self._digits()
        if self._peek() in ("e", "E"):
            self._advance()
            if self._peek() in ("+", "-"):
                self._advance()
            self._digits()
        elif not fraction:
            self._check_integer(start, integer_end, negative)

    def _check_integer(self, start, end, negative):
        limit = "9223372036854775808" if negative else "9223372036854775807"
        width = end - start
        if width > len(limit):
            raise RecordingError()
        if width == len(limit):
            for offset, limit_digit in enumerate(limit, start):
                digit = self._text[offset]
                if digit > limit_digit:
                    raise RecordingError()
                if digit < limit_digit:
                    break


def _parse_memory_json(text, budget, byte_limit):
    if type(text) is not str or type(budget) is not _MemoryAdmissionBudget:
        raise RecordingError("invalid_request")
    budget.check()
    _MemoryJSONPreflight(text, budget, byte_limit).check()
    budget.check()
    value = _strict_json_text(text)
    budget.check()
    return value


def _parse_bounded_memory_json(text, budget):
    return _parse_memory_json(text, budget, MAX_JSON)


class NativeMemoryCaptureBudget:
    """One nonrefillable allowance for the memory artifact at a held cut."""

    __slots__ = ("_remaining_bytes", "_remaining_nodes", "_deadline_monotonic")

    def __init__(self, *, max_bytes, deadline_monotonic):
        if (type(max_bytes) is not int or not 0 < max_bytes <= MAX_JSON
                or type(deadline_monotonic) not in (int, float)):
            raise RecordingError("invalid_request")
        try:
            if not math.isfinite(deadline_monotonic):
                raise RecordingError("invalid_request")
        except OverflowError:
            raise RecordingError("invalid_request") from None
        self._remaining_bytes = max_bytes
        self._remaining_nodes = _MAX_NODES
        self._deadline_monotonic = deadline_monotonic
        self.check()

    @property
    def remaining_bytes(self):
        return self._remaining_bytes

    @property
    def remaining_nodes(self):
        return self._remaining_nodes

    @property
    def deadline_monotonic(self):
        return self._deadline_monotonic

    def check(self):
        if time.monotonic() >= self._deadline_monotonic:
            raise RecordingError("limit_exceeded")

    def charge_bytes(self, amount):
        self.check()
        if type(amount) is not int or amount < 0:
            raise RecordingError("invalid_request")
        if amount > self._remaining_bytes:
            raise RecordingError("limit_exceeded")
        self._remaining_bytes -= amount

    def charge_node(self):
        self.check()
        if self._remaining_nodes == 0:
            raise RecordingError("limit_exceeded")
        self._remaining_nodes -= 1


def _check_owner(agent_id):
    if type(agent_id) is not str or not IDENTIFIER.fullmatch(agent_id):
        raise RecordingError("invalid_request")


def _token_limit(creator):
    if type(creator) is not ScoreBasedContextCreator:
        raise RecordingError()
    limit = creator.token_limit
    if type(limit) is not int or not 0 < limit < 2**63:
        raise RecordingError()
    return limit


def _charge_bytes(budget, minimum):
    budget[0] -= minimum
    if budget[0] < 0:
        raise RecordingError("limit_exceeded")


def _preflight_tree(value, bounds, capture_budget, path=()):
    """Reject impossible live payloads before UTF-8 work or copying."""
    if len(path) > _MAX_DEPTH:
        raise RecordingError("limit_exceeded")
    if capture_budget is None:
        bounds[1] -= 1
        if bounds[1] < 0:
            raise RecordingError("limit_exceeded")
    else:
        capture_budget.charge_node()

    if path == _ROLE_NAME_PATH:
        # OASIS stores a real enum in this slot for data-driven system history.
        if value is OpenAIBackendRole.SYSTEM:
            value = _SYSTEM_ROLE_NAME_TAG
        elif type(value) is not str:
            raise RecordingError()

    kind = type(value)
    if kind is str:
        # Each character needs at least one JSON byte, plus the quotes.
        _charge_bytes(bounds, len(value) + 2)
    elif value is None:
        _charge_bytes(bounds, 4)
    elif kind is bool:
        _charge_bytes(bounds, 4 if value else 5)
    elif kind is int:
        if not -(2**63) <= value < 2**63:
            raise RecordingError()
        _charge_bytes(bounds, len(str(value)))
    elif kind is float:
        if not math.isfinite(value):
            raise RecordingError()
        _charge_bytes(bounds, 1)
    elif kind is list:
        _charge_bytes(bounds, 2 + max(0, len(value) - 1))
        for item in value:
            _preflight_tree(item, bounds, capture_budget, path + (None,))
    elif kind is dict:
        _charge_bytes(bounds, 2 + len(value) + max(0, len(value) - 1))
        for key, item in value.items():
            if type(key) is not str or len(key) > 256:
                raise RecordingError()
            _charge_bytes(bounds, len(key) + 2)
            _preflight_tree(item, bounds, capture_budget, path + (key,))
    else:
        raise RecordingError()


def _encode_system_role_name(value):
    # Preflight admits this value only in records[*].message.role_name.
    if value is OpenAIBackendRole.SYSTEM:
        return _SYSTEM_ROLE_NAME_TAG
    raise TypeError


def _encode_payload(payload, byte_limit, budget):
    try:
        _preflight_tree(payload, [byte_limit, _MAX_NODES], budget)
        if budget is not None:
            budget.check()
        encoded = bytearray()
        encoder = json.JSONEncoder(
            ensure_ascii=False, allow_nan=False, separators=(",", ":"),
            default=_encode_system_role_name,
        )
        for chunk in encoder.iterencode(payload):
            if budget is not None:
                budget.check()
            remaining = byte_limit - len(encoded)
            if len(chunk) > remaining:
                raise RecordingError("limit_exceeded")
            data = chunk.encode("utf-8")
            if len(data) > remaining:
                raise RecordingError("limit_exceeded")
            encoded.extend(data)
        if budget is not None:
            budget.check()
        return bytes(encoded)
    except RecordingError:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise RecordingError() from None


def _nonempty_string(value):
    return type(value) is str and bool(value)


def _check_tool_calls(tool_calls, budget, argument_limit=None):
    if type(tool_calls) is not list:
        raise RecordingError()
    for call in tool_calls:
        if budget is not None:
            budget.check()
        if (type(call) is not dict or call.keys() != {"id", "type", "function"}
                or not _nonempty_string(call["id"]) or call["type"] != "function"):
            raise RecordingError()
        function = call["function"]
        if (type(function) is not dict or function.keys() != {"name", "arguments"}
                or not _nonempty_string(function["name"])
                or type(function["arguments"]) is not str):
            raise RecordingError()
        if argument_limit is None:
            limit = MAX_JSON if budget is None else budget.remaining_bytes
            arguments = strict_json(function["arguments"], limit)
        else:
            arguments = _parse_memory_json(function["arguments"], budget, argument_limit)
        if budget is not None:
            budget.check()
        if type(arguments) is not dict:
            raise RecordingError()
        del arguments


def _check_function_message(message, backend_role):
    if (message.keys() - _MESSAGE_FIELDS - _TEXT_OPTIONAL - _FUNCTION_FIELDS
            or not _nonempty_string(message.get("func_name"))
            or not _nonempty_string(message.get("tool_call_id"))
            or "mask_output" not in message
            or (message["mask_output"] is not None and type(message["mask_output"]) is not bool)
            or ("args" in message and type(message["args"]) is not dict)):
        raise RecordingError()
    if backend_role == "assistant":
        if "args" not in message or "result" in message:
            raise RecordingError()
    elif backend_role == "function":
        # The pinned encoder omits a None result. This subset requires a
        # recorded, bounded JSON result for a tool response.
        if message.get("result") is None:
            raise RecordingError()
    else:
        raise RecordingError()


def _decode_message(message, backend_role, budget, argument_limit=None):
    if (type(message) is not dict or not _MESSAGE_FIELDS <= message.keys()
            or type(message["role_type"]) is not str or type(message["content"]) is not str
            or any(message.get(key) is not None for key in ("image_list", "video_bytes", "parsed"))
            or any(message.get(key, "auto") not in _DETAIL_LEVELS
                   for key in ("image_detail", "video_detail"))):
        raise RecordingError()
    role_name = message["role_name"]
    if (type(role_name) is dict and role_name == _SYSTEM_ROLE_NAME_TAG
            and message["__class__"] == "BaseMessage" and backend_role == "system"):
        message["role_name"] = OpenAIBackendRole.SYSTEM
    elif not _nonempty_string(role_name):
        raise RecordingError()

    try:
        RoleType(message["role_type"])
    except ValueError:
        raise RecordingError() from None

    if message["__class__"] == "FunctionCallingMessage":
        _check_function_message(message, backend_role)
        return
    if (message["__class__"] != "BaseMessage" or backend_role not in _BASE_ROLES
            or message.keys() - _MESSAGE_FIELDS - _TEXT_OPTIONAL - {"tool_calls"}):
        raise RecordingError()
    # BaseMessage flattens its metadata into the record. tool_calls is the
    # supported metadata field because it is forwarded in assistant context.
    if "tool_calls" in message:
        if backend_role != "assistant":
            raise RecordingError()
        _check_tool_calls(message["tool_calls"], budget, argument_limit)


def _check_record(record, agent_id, budget, argument_limit=None):
    if budget is not None:
        budget.check()
    if type(record) is not dict or record.keys() != _RECORD_FIELDS:
        raise RecordingError()
    if (type(record["uuid"]) is not str
            or type(record["role_at_backend"]) is not str
            or type(record["timestamp"]) not in (int, float)
            or type(record["agent_id"]) is not str
            or record["agent_id"] not in ("", agent_id)
            or type(record["extra_info"]) is not dict
            or any(type(value) is not str for value in record["extra_info"].values())):
        raise RecordingError()

    _decode_message(record["message"], record["role_at_backend"], budget, argument_limit)
    if budget is not None:
        budget.check()
    # Run the real codec as a second guard on a copy of the validated subset.
    try:
        MemoryRecord.from_dict(deepcopy(record)).to_openai_message()
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
        raise RecordingError() from None
    if budget is not None:
        budget.check()


def _validated_payload(raw, agent_id, token_limit, byte_limit=MAX_JSON, budget=None):
    if type(raw) is not bytes:
        raise RecordingError("invalid_request")
    if budget is not None:
        budget.check()
    payload = strict_json(raw, byte_limit)
    if budget is not None:
        budget.check()
    return _validate_memory_payload(payload, agent_id, token_limit, budget)


def _validate_memory_payload(payload, agent_id, token_limit, budget, *, argument_limit=None):
    if (type(payload) is not dict or payload.keys() != _FIELDS
            or payload["kind"] != _KIND
            or type(payload["version"]) is not int or payload["version"] != 1
            or payload["agent_id"] != agent_id
            or type(payload["token_limit"]) is not int or payload["token_limit"] != token_limit
            or type(payload["records"]) is not list):
        raise RecordingError()

    window_size = payload["window_size"]
    keep_rate = payload["keep_rate"]
    if (window_size is not None and (type(window_size) is not int or window_size < 0)
            or type(keep_rate) not in (int, float) or not 0 <= keep_rate <= 1):
        raise RecordingError()
    for record in payload["records"]:
        _check_record(record, agent_id, budget, argument_limit)
    return payload


def _admit_detached_memory_frame(frame, *, expected_agent_id, token_limit, budget):
    """Validate one authenticated owner frame and return only its record count."""
    try:
        _check_owner(expected_agent_id)
        if (type(frame) is not memoryview or not frame.readonly or not frame.c_contiguous
                or frame.ndim != 1 or frame.itemsize != 1
                or type(token_limit) is not int or not 0 < token_limit < 2**63
                or type(budget) is not _MemoryAdmissionBudget):
            raise RecordingError("invalid_request")
        frame_size = frame.nbytes
        if not 0 < frame_size <= MAX_JSON:
            raise RecordingError("limit_exceeded")
        budget.check()
        payload = _parse_bounded_memory_json(str(frame, "utf-8"), budget)
        _validate_memory_payload(payload, expected_agent_id, token_limit, budget,
                                 argument_limit=frame_size)
        budget.check()
        return len(payload["records"])
    except RecordingError as error:
        refusal_code = error.code
    except (RuntimeError, TypeError, ValueError, OverflowError):
        refusal_code = "invalid_recording"
    finally:
        payload = None
        frame = None
    # UnicodeDecodeError can retain a copy of the frame in its context.
    raise RecordingError(refusal_code)


def capture_native_memory(memory, *, expected_agent_id, budget=None):
    """Materialize all history while the caller holds the native round fence."""
    if budget is not None and type(budget) is not NativeMemoryCaptureBudget:
        raise RecordingError("invalid_request")
    byte_limit = MAX_JSON if budget is None else budget.remaining_bytes
    if budget is not None:
        budget.check()
    _check_owner(expected_agent_id)
    if type(memory) is not ChatHistoryMemory or memory.agent_id != expected_agent_id:
        raise RecordingError()
    block = memory._chat_history_block
    if type(block) is not ChatHistoryBlock or type(block.storage) is not InMemoryKeyValueStorage:
        raise RecordingError()
    limit = _token_limit(memory.get_context_creator())
    # The pinned load() deep-copies its entire backing list. Validate and
    # encode the backing list at the fence before allocating a separate copy.
    raw = _encode_payload({
        "kind": _KIND,
        "version": 1,
        "agent_id": memory.agent_id,
        "window_size": memory._window_size,
        "keep_rate": block.keep_rate,
        "token_limit": limit,
        "records": block.storage.memory_list,
    }, byte_limit, budget)
    _validated_payload(raw, expected_agent_id, limit, byte_limit, budget)
    if budget is not None:
        budget.charge_bytes(len(raw))
    return raw


def restore_native_memory(raw, *, expected_agent_id, context_creator):
    """Build detached native memory using the caller's trusted context creator."""
    _check_owner(expected_agent_id)
    payload = _validated_payload(raw, expected_agent_id, _token_limit(context_creator))
    memory = ChatHistoryMemory(
        context_creator=context_creator,
        window_size=payload["window_size"],
        agent_id=expected_agent_id,
    )
    block = memory._chat_history_block
    block.keep_rate = payload["keep_rate"]
    block.storage.save(payload["records"])
    return memory


@dataclass(frozen=True)
class _AgentMemoryOwner:
    platform_id: int
    camel_id: str
    agent: object
    action: object
    memory: object
    block: object
    storage: object

    def same_binding(self, other):
        return (self.platform_id == other.platform_id and self.camel_id == other.camel_id
                and self.agent is other.agent and self.action is other.action
                and self.memory is other.memory and self.block is other.block
                and self.storage is other.storage)


def _valid_id(value):
    return type(value) is int and 0 <= value < 2**63


def _check_cut_deadline(deadline_monotonic):
    if time.monotonic() >= deadline_monotonic:
        raise RecordingError("limit_exceeded")


def resolve_native_memory_cohort(graph, expected_graph, channel, prepared_agent_ids,
                                 deadline_monotonic):
    """Resolve the existing live CAMEL bindings at an owned capture read point."""
    _check_cut_deadline(deadline_monotonic)
    mappings = getattr(graph, "agent_mappings", None)
    if (graph is not expected_graph or getattr(graph, "backend", None) != "igraph"
            or type(mappings) is not dict or len(mappings) != len(prepared_agent_ids)
            or graph.get_num_nodes() != len(prepared_agent_ids)
            or not all(_valid_id(key) for key in mappings)
            or set(mappings) != set(prepared_agent_ids)):
        raise RecordingError()

    owners = []
    camel_ids = set()
    object_ids = set()
    for platform_id in prepared_agent_ids:
        _check_cut_deadline(deadline_monotonic)
        agent = mappings[platform_id]
        if (graph.get_agent(platform_id) is not agent
                or not _valid_id(getattr(agent, "social_agent_id", None))
                or agent.social_agent_id != platform_id):
            raise RecordingError()
        action = getattr(getattr(agent, "env", None), "action", None)
        if (getattr(action, "channel", None) is not channel
                or not _valid_id(getattr(action, "agent_id", None))
                or action.agent_id != platform_id):
            raise RecordingError()

        camel_id = getattr(agent, "agent_id", None)
        memory = getattr(agent, "memory", None)
        if (type(camel_id) is not str or not IDENTIFIER.fullmatch(camel_id)
                or camel_id in camel_ids or type(memory) is not ChatHistoryMemory
                or type(memory.agent_id) is not str or memory.agent_id != camel_id):
            raise RecordingError()
        block = memory._chat_history_block
        if type(block) is not ChatHistoryBlock or type(block.storage) is not InMemoryKeyValueStorage:
            raise RecordingError()
        storage = block.storage
        for value in (agent, action, memory, block, storage):
            identity = id(value)
            if identity in object_ids:
                raise RecordingError()
            object_ids.add(identity)
        camel_ids.add(camel_id)
        owners.append(_AgentMemoryOwner(platform_id, camel_id, agent, action, memory, block, storage))
    return tuple(owners)


def capture_dense_twitter_topology(graph, owners, deadline_monotonic):
    """Describe the verified dense, directed, zero-edge graph at the held cut."""
    _check_cut_deadline(deadline_monotonic)
    from igraph import Graph
    from oasis.social_agent.agent_graph import AgentGraph

    _check_cut_deadline(deadline_monotonic)
    if type(graph) is not AgentGraph or graph.backend != "igraph":
        raise RecordingError()
    native = graph.graph
    mappings = graph.agent_mappings
    count = len(owners)
    if (not 1 <= count <= 128 or type(native) is not Graph
            or type(mappings) is not dict or len(mappings) != count):
        raise RecordingError()
    _check_cut_deadline(deadline_monotonic)
    if not native.is_directed() or native.vcount() != count or native.ecount() != 0:
        raise RecordingError()

    digest = hashlib.sha256(b"NTZ1")
    try:
        for index in range(count):
            _check_cut_deadline(deadline_monotonic)
            vertex = native.vs[index]
            name = vertex["name"]
            owner = owners[index]
            platform_id = owner.platform_id
            agent_id = getattr(owner.agent, "social_agent_id", None)
            if (type(vertex.index) is not int or vertex.index != index
                    or type(name) is not int or name != index
                    or type(platform_id) is not int or platform_id != index
                    or mappings.get(index) is not owner.agent
                    or graph.get_agent(index) is not owner.agent
                    or type(agent_id) is not int or agent_id != index):
                raise RecordingError()
            digest.update(bytes((name,)))
    except (IndexError, KeyError):
        raise RecordingError() from None
    _check_cut_deadline(deadline_monotonic)
    return {"version": 1, "directed": True, "vertex_count": count, "edge_count": 0,
            "sha256": digest.hexdigest()}
