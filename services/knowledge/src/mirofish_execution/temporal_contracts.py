"""Bounded identifier-only Temporal wire contracts; no runtime dependencies."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

_DISPLAY = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_PRINCIPAL = re.compile(r"[\x20-\x7e]{1,128}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_GROUP = re.compile(r"mf1_[0-9a-f]{64}\Z")
_REQUEST_FIELDS = frozenset({"schema_version", "principal", "display_graph_id",
    "source_revision", "operation_id", "account_id", "ontology_revision",
    "ceiling_microusd", "request_fingerprint"})
_RECEIPT_FIELDS = frozenset({"schema_version", "group_id", "episode_id",
    "fingerprint", "evidence_ids"})


class InvalidTemporalRequest(ValueError):
    def __init__(self):
        super().__init__("invalid_temporal_request")


def _canonical_uuid(value: object) -> str:
    try:
        if type(value) is UUID:
            parsed = value
        elif type(value) is str:
            parsed = UUID(value)
            if value != str(parsed):
                raise ValueError
        else:
            raise ValueError
    except (TypeError, ValueError):
        raise InvalidTemporalRequest() from None
    return str(parsed)


@dataclass(frozen=True)
class TemporalIngestionRequest:
    schema_version: int
    principal: str
    display_graph_id: str
    source_revision: str
    operation_id: str
    account_id: str
    ontology_revision: str
    ceiling_microusd: int
    request_fingerprint: str

    @classmethod
    def from_wire(cls, value: object) -> TemporalIngestionRequest:
        if isinstance(value, cls):
            value = asdict(value)
        if not isinstance(value, Mapping) or set(value) != _REQUEST_FIELDS:
            raise InvalidTemporalRequest()
        try:
            principal = value["principal"]
            display = value["display_graph_id"]
            money = value["ceiling_microusd"]
            fingerprint = value["request_fingerprint"]
            if (type(principal) is not str or not _PRINCIPAL.fullmatch(principal)
                    or not principal.strip(" ")
                    or type(value["schema_version"]) is not int or value["schema_version"] != 1
                    or type(display) is not str or not _DISPLAY.fullmatch(display)
                    or type(money) is not int or not 1 <= money <= 2**63 - 1
                    or type(fingerprint) is not str or not _HEX.fullmatch(fingerprint)):
                raise ValueError
            return cls(1, principal, display, _canonical_uuid(value["source_revision"]),
                       _canonical_uuid(value["operation_id"]),
                       _canonical_uuid(value["account_id"]),
                       _canonical_uuid(value["ontology_revision"]), money, fingerprint)
        except (KeyError, TypeError, ValueError):
            raise InvalidTemporalRequest() from None

    def to_wire(self) -> dict[str, object]:
        return asdict(self.from_wire(self))

    @property
    def semantic_digest(self) -> str:
        canonical = json.dumps(self.to_wire(), sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True)
        return hashlib.sha256(canonical.encode("ascii")).hexdigest()

    @property
    def workflow_id(self) -> str:
        return ("mf-ingest-v1-" + UUID(self.account_id).hex + "-"
                + UUID(self.operation_id).hex + "-" + self.semantic_digest[:24])


@dataclass(frozen=True)
class TemporalReceipt:
    schema_version: int
    group_id: str
    episode_id: str
    fingerprint: str
    evidence_ids: tuple[str, ...]

    @classmethod
    def from_wire(cls, value: object, request: TemporalIngestionRequest) -> TemporalReceipt:
        request = TemporalIngestionRequest.from_wire(request)
        if isinstance(value, cls):
            value = asdict(value)
        if not isinstance(value, Mapping) or set(value) != _RECEIPT_FIELDS:
            raise InvalidTemporalRequest()
        try:
            evidence = value["evidence_ids"]
            if (type(value["schema_version"]) is not int or value["schema_version"] != 1
                    or type(value["group_id"]) is not str or not _GROUP.fullmatch(value["group_id"])
                    or type(value["fingerprint"]) is not str
                    or value["fingerprint"] != request.request_fingerprint
                    or not isinstance(evidence, (tuple, list)) or len(evidence) > 100):
                raise ValueError
            episode = _canonical_uuid(value["episode_id"])
            expected = uuid5(NAMESPACE_URL,
                f"mirofish:episode:v1:{value['group_id']}:{UUID(request.operation_id)}")
            if episode != str(expected):
                raise ValueError
            ids = tuple(_canonical_uuid(v) for v in evidence)
            if len(set(ids)) != len(ids):
                raise ValueError
            return cls(1, value["group_id"], episode, value["fingerprint"], ids)
        except (KeyError, TypeError, ValueError):
            raise InvalidTemporalRequest() from None

    def to_wire(self) -> dict[str, object]:
        return asdict(self)
