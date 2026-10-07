"""Frozen dossier records. Request data never grants scope or provider authority."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .contracts import KnowledgeScope, StrictModel, _aware
from .research_contracts import Citation, ClaimCandidate, PassageCoverage, ScopeCoverage
from nexaweave_storage.validation import display_id


class DossierSectionRequest(StrictModel):
    heading: str = Field(min_length=1, max_length=256)
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=100)

    @field_validator("heading", "query")
    @classmethod
    def text(cls, value):
        if not value.strip():
            raise ValueError("empty dossier text")
        value.encode("utf-8")
        return value


class DossierRequest(StrictModel):
    schema_version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=256)
    display_graph_ids: tuple[str, ...] = Field(min_length=1, max_length=5)
    sections: tuple[DossierSectionRequest, ...] = Field(min_length=1, max_length=6)
    valid_at: datetime | None = None
    recorded_before: datetime | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("invalid dossier version")
        return value

    @field_validator("title")
    @classmethod
    def title_text(cls, value):
        if not value.strip():
            raise ValueError("empty dossier title")
        value.encode("utf-8")
        return value

    @field_validator("display_graph_ids")
    @classmethod
    def identities(cls, value):
        for item in value:
            display_id(item)
        if len(set(value)) != len(value):
            raise ValueError("duplicate display graph")
        return value

    @field_validator("valid_at", "recorded_before")
    @classmethod
    def aware(cls, value):
        return _aware(value)


class DossierReference(StrictModel):
    key: str = Field(pattern=r"^ref_[0-9a-f]{64}$")
    claim_key: str = Field(pattern=r"^claim_[0-9a-f]{64}$")
    scope: KnowledgeScope
    provider_id: str
    evidence_id: UUID
    status: Literal["resolved", "unavailable"]
    citation: Citation | None

    @model_validator(mode="after")
    def integrity(self):
        if self.status == "resolved":
            if (self.citation is None or self.citation.project_id != self.scope.project_id
                    or self.citation.evidence_id != self.evidence_id):
                raise ValueError("invalid dossier reference")
        elif self.citation is not None:
            raise ValueError("invalid unavailable reference")
        return self


class DossierClaim(StrictModel):
    key: str = Field(pattern=r"^claim_[0-9a-f]{64}$")
    scope: KnowledgeScope
    provider_id: str
    kind: Literal["edge"] = "edge"
    claim_class: Literal["source", "assumption", "inference", "simulation", "analysis"]
    text: str = Field(min_length=1, max_length=32768)
    predicate: str | None = Field(max_length=256)
    source_node_id: str
    target_node_id: str
    episode_ids: tuple[str, ...] = Field(max_length=100)
    evidence_ids: tuple[UUID, ...] = Field(max_length=100)
    reference_keys: tuple[str, ...] = Field(max_length=100)
    created_at: datetime | None
    valid_at: datetime | None
    invalid_at: datetime | None
    expired_at: datetime | None
    claim_support_status: Literal["not_reviewed"] = "not_reviewed"
    reference_integrity: Literal["resolved", "partly_unavailable", "unavailable", "no_evidence_links"]

    @field_validator("created_at", "valid_at", "invalid_at", "expired_at")
    @classmethod
    def aware(cls, value):
        return _aware(value)

    @model_validator(mode="after")
    def origin(self):
        if self.claim_class != self.scope.layer.value:
            raise ValueError("invalid claim layer")
        if len(self.evidence_ids) != len(self.reference_keys):
            raise ValueError("invalid claim references")
        return self


class DossierSection(StrictModel):
    ordinal: int = Field(ge=1, le=6)
    heading: str = Field(min_length=1, max_length=256)
    source_claim_keys: tuple[str, ...] = Field(max_length=100)
    simulation_observation_keys: tuple[str, ...] = Field(max_length=100)
    other_claim_keys: tuple[str, ...] = Field(max_length=100)


class ResearchTrace(StrictModel):
    ordinal: int = Field(ge=1, le=6)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(ge=1, le=100)
    display_graph_ids: tuple[str, ...]
    valid_at: datetime | None
    recorded_before: datetime | None
    scopes: tuple[ScopeCoverage, ...] = Field(max_length=5)
    passage_coverage: tuple[PassageCoverage, ...] = Field(max_length=10000)
    competing_claim_candidates: tuple[ClaimCandidate, ...] = Field(max_length=50)
    linked_citations: int = Field(ge=0)
    resolved_citations: int = Field(ge=0)
    unavailable_citations: int = Field(ge=0)
    historical: bool
    historical_semantics: Literal["retained_edges_not_bitemporal_reconstruction"]
    rank_basis: Literal["lexical_token_overlap"]


class DossierSummary(StrictModel):
    section_count: int = Field(ge=1, le=6)
    query_count: int = Field(ge=1, le=6)
    distinct_scoped_facts: int = Field(ge=0, le=600)
    reference_links: int = Field(ge=0, le=60000)
    resolved_references: int = Field(ge=0, le=60000)
    unavailable_references: int = Field(ge=0, le=60000)
    query_reference_links: int = Field(ge=0, le=60000)
    query_resolved_references: int = Field(ge=0, le=60000)
    query_unavailable_references: int = Field(ge=0, le=60000)
    scanned_per_query_sum: int = Field(ge=0, le=15000)
    unknown_per_query_sum: int = Field(ge=0, le=15000)
    truncated_query_scopes: int = Field(ge=0, le=30)
    passage_coverage: tuple[PassageCoverage, ...] = Field(max_length=60000)
    coverage_label: Literal["retrieved_passage_union_per_retained_revision"] = "retrieved_passage_union_per_retained_revision"


class EvidenceDossier(StrictModel):
    schema_version: Literal[1] = 1
    mode: Literal["model_free_evidence_dossier"] = "model_free_evidence_dossier"
    request: DossierRequest
    sections: tuple[DossierSection, ...] = Field(min_length=1, max_length=6)
    claims: tuple[DossierClaim, ...] = Field(max_length=600)
    references: tuple[DossierReference, ...] = Field(max_length=60000)
    research_trace: tuple[ResearchTrace, ...] = Field(min_length=1, max_length=6)
    summary: DossierSummary
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    records_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_generated: Literal[False] = False
    semantic_judge_used: Literal[False] = False
    claim_support_status: Literal["not_reviewed"] = "not_reviewed"
    consistency: Literal["individually_guarded_queries_not_atomic_snapshot"] = "individually_guarded_queries_not_atomic_snapshot"
    limitations: tuple[str, ...]
    markdown: str


class DossierError(StrictModel):
    error: Literal["invalid_request", "invalid_configuration", "dossier_unavailable", "dossier_deadline", "dossier_busy", "result_too_large"]
