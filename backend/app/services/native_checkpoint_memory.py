"""Bounded checkpoint state for the pinned native CAMEL chat history."""

from __future__ import annotations

from copy import deepcopy
import json
import math

from camel.memories import ChatHistoryMemory, MemoryRecord, ScoreBasedContextCreator
from camel.memories.blocks.chat_history_block import ChatHistoryBlock
from camel.storages.key_value_storages.in_memory import InMemoryKeyValueStorage
from camel.types import OpenAIBackendRole, RoleType

from .native_recording_contracts import (
    IDENTIFIER,
    MAX_JSON,
    RecordingError,
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


def _preflight_tree(value, budget, path=()):
    """Reject impossible live payloads before UTF-8 work or copying."""
    budget[1] -= 1
    if budget[1] < 0 or len(path) > _MAX_DEPTH:
        raise RecordingError("limit_exceeded")

    if path == _ROLE_NAME_PATH:
        # OASIS stores a real enum in this slot for data-driven system history.
        if value is OpenAIBackendRole.SYSTEM:
            value = _SYSTEM_ROLE_NAME_TAG
        elif type(value) is not str:
            raise RecordingError()

    kind = type(value)
    if kind is str:
        # Each character needs at least one JSON byte, plus the quotes.
        _charge_bytes(budget, len(value) + 2)
    elif value is None:
        _charge_bytes(budget, 4)
    elif kind is bool:
        _charge_bytes(budget, 4 if value else 5)
    elif kind is int:
        if not -(2**63) <= value < 2**63:
            raise RecordingError()
        _charge_bytes(budget, len(str(value)))
    elif kind is float:
        if not math.isfinite(value):
            raise RecordingError()
        _charge_bytes(budget, 1)
    elif kind is list:
        _charge_bytes(budget, 2 + max(0, len(value) - 1))
        for item in value:
            _preflight_tree(item, budget, path + (None,))
    elif kind is dict:
        _charge_bytes(budget, 2 + len(value) + max(0, len(value) - 1))
        for key, item in value.items():
            if type(key) is not str or len(key) > 256:
                raise RecordingError()
            _charge_bytes(budget, len(key) + 2)
            _preflight_tree(item, budget, path + (key,))
    else:
        raise RecordingError()


def _encode_system_role_name(value):
    # Preflight admits this value only in records[*].message.role_name.
    if value is OpenAIBackendRole.SYSTEM:
        return _SYSTEM_ROLE_NAME_TAG
    raise TypeError


def _encode_payload(payload):
    try:
        _preflight_tree(payload, [MAX_JSON, _MAX_NODES])
        encoded = bytearray()
        encoder = json.JSONEncoder(
            ensure_ascii=False, allow_nan=False, separators=(",", ":"),
            default=_encode_system_role_name,
        )
        for chunk in encoder.iterencode(payload):
            remaining = MAX_JSON - len(encoded)
            if len(chunk) > remaining:
                raise RecordingError("limit_exceeded")
            data = chunk.encode("utf-8")
            if len(data) > remaining:
                raise RecordingError("limit_exceeded")
            encoded.extend(data)
        return bytes(encoded)
    except RecordingError:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise RecordingError() from None


def _nonempty_string(value):
    return type(value) is str and bool(value)


def _check_tool_calls(tool_calls):
    if type(tool_calls) is not list:
        raise RecordingError()
    for call in tool_calls:
        if (type(call) is not dict or call.keys() != {"id", "type", "function"}
                or not _nonempty_string(call["id"]) or call["type"] != "function"):
            raise RecordingError()
        function = call["function"]
        if (type(function) is not dict or function.keys() != {"name", "arguments"}
                or not _nonempty_string(function["name"])
                or type(function["arguments"]) is not str
                or type(strict_json(function["arguments"])) is not dict):
            raise RecordingError()


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


def _decode_message(message, backend_role):
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
        _check_tool_calls(message["tool_calls"])


def _check_record(record, agent_id):
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

    _decode_message(record["message"], record["role_at_backend"])
    # Run the real codec as a second guard on a copy of the validated subset.
    try:
        MemoryRecord.from_dict(deepcopy(record)).to_openai_message()
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
        raise RecordingError() from None


def _validated_payload(raw, agent_id, token_limit):
    if type(raw) is not bytes:
        raise RecordingError("invalid_request")
    payload = strict_json(raw)
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
        _check_record(record, agent_id)
    return payload


def capture_native_memory(memory, *, expected_agent_id):
    """Materialize all history while the caller holds the native round fence."""
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
    })
    _validated_payload(raw, expected_agent_id, limit)
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
