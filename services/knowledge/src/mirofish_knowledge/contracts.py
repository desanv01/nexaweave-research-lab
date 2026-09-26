"""Strict v1 application contracts. These models do not confer authorization."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid5, NAMESPACE_URL

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, create_model


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Layer(StrEnum):
    source = "source"
    assumption = "assumption"
    inference = "inference"
    simulation = "simulation"
    analysis = "analysis"


class KnowledgeScope(StrictModel):
    schema_version: Literal[1] = 1
    workspace_id: UUID
    project_id: UUID
    graph_id: UUID
    run_id: UUID | None = None
    branch_id: UUID | None = None
    layer: Layer

    @property
    def group_id(self) -> str:
        canonical = self.model_dump(mode="json", exclude={"schema_version"})
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return "mf1_" + digest

    def episode_uuid(self, operation_id: UUID) -> UUID:
        return uuid5(NAMESPACE_URL, f"mirofish:episode:v1:{self.group_id}:{operation_id}")


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("timestamp must include timezone")
    return value


class SourceEnvelope(StrictModel):
    schema_version: Literal[1] = 1
    source_revision: UUID
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ontology_revision: UUID
    operation_id: UUID
    source_kind: Literal["document", "event"]
    content: str = Field(min_length=1, max_length=32768)
    source_name: str = Field(min_length=1, max_length=256)
    recorded_at: datetime
    asserted_valid_at: datetime | None = None
    evidence_ids: tuple[UUID, ...] = Field(default=(), max_length=100)

    @field_validator("recorded_at", "asserted_valid_at")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value)

    @model_validator(mode="after")
    def content_hash_matches(self):
        if hashlib.sha256(self.content.encode("utf-8")).hexdigest() != self.source_sha256:
            raise ValueError("source_sha256 does not match UTF-8 content")
        return self


IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
RESERVED = frozenset({"uuid", "name", "group_id", "graph_id", "labels", "attributes", "summary", "created_at", "valid_at", "invalid_at", "expired_at", "episodes", "source_node_uuid", "target_node_uuid", "fact", "fact_embedding", "name_embedding", "entity_edges", "source", "content"})
PYDANTIC_RESERVED = frozenset(dir(BaseModel)) | {"model_config", "model_fields", "model_dump", "model_validate"}
TYPE_MAP = {"text": str, "integer": int, "number": float, "boolean": bool}


class AttributeSpec(StrictModel):
    name: str
    type: Literal["text", "integer", "number", "boolean"]
    description: str = Field(min_length=1, max_length=500)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if (not IDENTIFIER.fullmatch(value) or value.lower() in RESERVED
                or value in PYDANTIC_RESERVED or value.startswith("model_")):
            raise ValueError("invalid or reserved ontology attribute")
        return value


class EntityTypeSpec(StrictModel):
    name: str
    description: str = Field(min_length=1, max_length=500)
    attributes: tuple[AttributeSpec, ...] = Field(default=(), max_length=20)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if not IDENTIFIER.fullmatch(value) or value in {"Entity", "Episodic", "Community", "Saga"}:
            raise ValueError("invalid entity type")
        return value


class TypePair(StrictModel):
    source: str
    target: str


class EdgeTypeSpec(StrictModel):
    name: str
    description: str = Field(min_length=1, max_length=500)
    attributes: tuple[AttributeSpec, ...] = Field(default=(), max_length=20)
    source_targets: tuple[TypePair, ...] = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if not IDENTIFIER.fullmatch(value):
            raise ValueError("invalid edge type")
        return value


class OntologySpec(StrictModel):
    schema_version: Literal[1] = 1
    revision: UUID
    entity_types: tuple[EntityTypeSpec, ...] = Field(min_length=1, max_length=50)
    edge_types: tuple[EdgeTypeSpec, ...] = Field(max_length=50)

    @model_validator(mode="after")
    def validate_types(self):
        names = [item.name for item in self.entity_types]
        edges = [item.name for item in self.edge_types]
        if len(set(names)) != len(names) or len(set(edges)) != len(edges):
            raise ValueError("duplicate type")
        for item in (*self.entity_types, *self.edge_types):
            attributes = [attribute.name for attribute in item.attributes]
            if len(set(attributes)) != len(attributes):
                raise ValueError("duplicate attribute")
        for edge in self.edge_types:
            for pair in edge.source_targets:
                if pair.source not in names or pair.target not in names:
                    raise ValueError("unknown source/target type pair")
        return self

    def graphiti_types(self):
        entity_models = {}
        edge_models = {}
        edge_map: dict[tuple[str, str], list[str]] = {}
        for item in self.entity_types:
            fields = {a.name: (TYPE_MAP[a.type] | None, Field(default=None, description=a.description)) for a in item.attributes}
            entity_models[item.name] = create_model(item.name, __doc__=item.description, **fields)
        for item in self.edge_types:
            fields = {a.name: (TYPE_MAP[a.type] | None, Field(default=None, description=a.description)) for a in item.attributes}
            edge_models[item.name] = create_model(item.name, __doc__=item.description, **fields)
            for pair in item.source_targets:
                edge_map.setdefault((pair.source, pair.target), []).append(item.name)
        return entity_models, edge_models, edge_map


class SearchQuery(StrictModel):
    schema_version: Literal[1] = 1
    text: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=100)
    valid_at: datetime | None = None
    recorded_before: datetime | None = None

    @field_validator("valid_at", "recorded_before")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value)


class FactResult(StrictModel):
    schema_version: Literal[1] = 1
    provider_id: str
    scope: KnowledgeScope
    kind: Literal["node", "edge", "episode"]
    name: str | None = None
    fact: str | None = None
    source_node_id: str | None = None
    target_node_id: str | None = None
    episode_ids: tuple[str, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    valid_at: datetime | None = None
    invalid_at: datetime | None = None
    created_at: datetime | None = None
    attributes: dict[str, object] = Field(default_factory=dict)
    score: float | None = None


class SearchResult(StrictModel):
    schema_version: Literal[1] = 1
    facts: tuple[FactResult, ...]


class IngestResult(StrictModel):
    schema_version: Literal[1] = 1
    episode_id: str
    already_exists: bool
    facts: tuple[FactResult, ...]
