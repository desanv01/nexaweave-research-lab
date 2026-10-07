"""Immutable, bounded evidence research DTOs; requests never confer authority."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .contracts import KnowledgeScope, StrictModel, _aware
from nexaweave_storage.validation import display_id


class ResearchRequest(StrictModel):
    schema_version: Literal[1] = 1
    display_graph_ids: tuple[str, ...] = Field(min_length=1, max_length=5)
    text: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=100)
    valid_at: datetime | None = None
    recorded_before: datetime | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("invalid research version")
        return value

    @field_validator("display_graph_ids")
    @classmethod
    def identities(cls, value):
        for item in value:
            display_id(item)
        if len(set(value)) != len(value):
            raise ValueError("duplicate display graph")
        return value

    @field_validator("text")
    @classmethod
    def query(cls, value):
        if not value.strip():
            raise ValueError("empty research query")
        value.encode("utf-8")
        return value

    @field_validator("valid_at", "recorded_before")
    @classmethod
    def aware(cls, value):
        return _aware(value)


class Citation(StrictModel):
    evidence_id: UUID
    project_id: UUID
    source_revision: UUID
    source_name: str = Field(max_length=256)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_byte_length: int = Field(gt=0, le=1048576)
    source_codepoint_length: int = Field(gt=0, le=1048576)
    source_recorded_at: datetime
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    offset_unit: Literal["unicode_codepoint"]
    excerpt: str = Field(min_length=1, max_length=32768)
    excerpt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    declared_page: int | None = Field(default=None, gt=0)

    @field_validator("source_recorded_at")
    @classmethod
    def aware(cls, value):
        return _aware(value)

    @model_validator(mode="after")
    def passage(self):
        import hashlib
        if (not self.start < self.end <= self.source_codepoint_length
                or len(self.excerpt) != self.end - self.start
                or len(self.excerpt.encode("utf-8")) > 32768
                or hashlib.sha256(self.excerpt.encode("utf-8")).hexdigest() != self.excerpt_sha256):
            raise ValueError("invalid evidence passage")
        return self


class ResearchFact(StrictModel):
    provider_id: str
    kind: Literal["edge"] = "edge"
    scope: KnowledgeScope
    claim_class: Literal["source", "assumption", "inference", "simulation", "analysis"]
    name: str | None = Field(default=None, max_length=256)
    fact: str = Field(min_length=1, max_length=32768)
    source_node_id: str
    target_node_id: str
    episode_ids: tuple[str, ...] = Field(max_length=100)
    evidence_ids: tuple[UUID, ...] = Field(max_length=100)
    valid_at: datetime | None = None
    invalid_at: datetime | None = None
    expired_at: datetime | None = None
    created_at: datetime | None = None
    rank_basis: Literal["lexical_token_overlap"] = "lexical_token_overlap"
    overlap_tokens: int = Field(ge=0)
    query_tokens: int = Field(ge=0)
    citations: tuple[Citation, ...] = Field(max_length=100)
    unavailable_evidence_ids: tuple[UUID, ...] = Field(max_length=100)


class ScopeCoverage(StrictModel):
    display_graph_id: str
    scope: KnowledgeScope
    pages: int = Field(ge=0, le=5)
    scanned: int = Field(ge=0, le=500)
    eligible: int = Field(ge=0, le=500)
    excluded: int = Field(ge=0, le=500)
    unknown: int = Field(ge=0, le=500)
    returned: int = Field(ge=0, le=100)
    truncated: bool


class PassageCoverage(StrictModel):
    project_id: UUID
    source_revision: UUID
    source_sha256: str
    retained_codepoints: int = Field(gt=0)
    retrieved_codepoints: int = Field(ge=0)
    retrieved_passage_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)


class ClaimCandidate(StrictModel):
    scope: KnowledgeScope
    subject_id: str
    predicate: str
    provider_ids: tuple[str, ...] = Field(min_length=2, max_length=100)
    evidence_ids: tuple[UUID, ...] = Field(max_length=10000)
    basis: Literal["same_subject_predicate_differing_target_or_text"] = "same_subject_predicate_differing_target_or_text"
    interpretation: Literal["candidate_for_review_no_truth_judgment"] = "candidate_for_review_no_truth_judgment"


class ResearchResult(StrictModel):
    schema_version: Literal[1] = 1
    source_claims: tuple[ResearchFact, ...] = Field(max_length=100)
    simulation_observations: tuple[ResearchFact, ...] = Field(max_length=100)
    other_claims: tuple[ResearchFact, ...] = Field(max_length=100)
    scopes: tuple[ScopeCoverage, ...] = Field(max_length=5)
    passage_coverage: tuple[PassageCoverage, ...] = Field(max_length=10000)
    competing_claim_candidates: tuple[ClaimCandidate, ...] = Field(max_length=50)
    linked_citations: int = Field(ge=0)
    resolved_citations: int = Field(ge=0)
    unavailable_citations: int = Field(ge=0)
    historical: bool
    historical_semantics: Literal["retained_edges_not_bitemporal_reconstruction"] = "retained_edges_not_bitemporal_reconstruction"
    rank_basis: Literal["lexical_token_overlap"] = "lexical_token_overlap"


class ResearchError(StrictModel):
    error: Literal["invalid_request", "research_unavailable", "research_deadline", "research_busy", "result_too_large", "invalid_configuration"]
