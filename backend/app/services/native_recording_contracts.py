"""Standard-library-only contracts for inert native recordings."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

MAX_WIRE = 8192
MAX_JSON = 2 * 1024 * 1024
MAX_EVENTS = 20000
MAX_EVENT_BYTES = 16384
MAX_PAGE = 100
HEX = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


class RecordingError(ValueError):
    """Only fixed public codes; never carry filesystem/database details."""
    def __init__(self, code="invalid_recording"):
        if code not in {"invalid_request", "invalid_recording", "source_unavailable",
                        "destination_unavailable", "invalid_cursor", "limit_exceeded"}:
            code = "invalid_recording"
        self.code = code
        super().__init__(code)


def bounded_tree(value, depth=0, budget=None):
    if budget is None:
        budget = [100000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 16:
        raise RecordingError("limit_exceeded")
    if value is None or type(value) in (bool, int, str):
        if type(value) is str and len(value.encode("utf-8")) > MAX_JSON:
            raise RecordingError("limit_exceeded")
        if type(value) is int and not -(2**63) <= value < 2**63:
            raise RecordingError()
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            bounded_tree(item, depth + 1, budget)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or len(key) > 256:
                raise RecordingError()
            bounded_tree(item, depth + 1, budget)
        return
    raise RecordingError()


def strict_json(raw, limit=MAX_JSON):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RecordingError()
            result[key] = value
        return result

    def nonfinite(_value):
        raise RecordingError()

    try:
        if type(raw) is str:
            raw = raw.encode("utf-8")
        if type(raw) is not bytes or len(raw) > limit:
            raise RecordingError("limit_exceeded")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=nonfinite)
        bounded_tree(value)
        return value
    except RecordingError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise RecordingError() from None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


@dataclass(frozen=True)
class RecordingAnchors:
    graph_id: str
    simulation_id: str
    run_id: str
    branch_id: str
    project_id: str
    project_revision: int

    def __post_init__(self):
        if (any(type(value) is not str or not IDENTIFIER.fullmatch(value)
                for value in (self.graph_id, self.simulation_id, self.run_id,
                              self.branch_id, self.project_id))
                or type(self.project_revision) is not int
                or not 1 <= self.project_revision < 2**63):
            raise RecordingError("invalid_request")

    def wire(self):
        return dict(self.__dict__)


def request_from_wire(raw):
    try:
        request = strict_json(raw, MAX_WIRE)
        if type(request) is not dict:
            raise RecordingError()
        required = {"version", "operation", "platform", "limit"}
        if (not required <= set(request) or set(request) - required - {"cursor"}
                or type(request["version"]) is not int or request["version"] != 1
                or type(request["operation"]) is not str
                or request["operation"] not in ("playback", "metrics")
                or type(request["platform"]) is not str
                or request["platform"] not in ("twitter", "reddit")
                or type(request["limit"]) is not int
                or not 1 <= request["limit"] <= MAX_PAGE
                or (request.get("cursor") is not None and
                    (type(request["cursor"]) is not str
                     or not 1 <= len(request["cursor"]) <= 512))
                or (request["operation"] == "metrics" and request.get("cursor") is not None)):
            raise RecordingError()
        return request
    except RecordingError:
        raise RecordingError("invalid_request") from None
