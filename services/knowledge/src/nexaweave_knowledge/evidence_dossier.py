"""Sequential model-free dossiers over the accepted authorized research service."""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import unicodedata

from .evidence_research import EvidenceResearchService, ResearchFailure, passage_coverage
from .report_contracts import (DossierClaim, DossierReference, DossierRequest,
    DossierSection, DossierSummary, EvidenceDossier, ResearchTrace)
from .research_contracts import ResearchRequest, ResearchResult

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DEADLINE_SECONDS = 60.0
LIMITATIONS = (
    "Each query is individually authorized and guarded; queries are not an atomic graph snapshot.",
    "Retrieved passage coverage is not document understanding or ingestion completeness.",
    "Lexical ranking and bounded scans do not establish exhaustive graph coverage.",
    "Reference integrity is separate from semantic claim support, which is not reviewed.",
    "Source claims are retained assertions; simulation observations are not real-world predictions.",
    "Competing claims are review candidates, not contradiction, truth or confidence decisions.",
    "Historical reads filter retained edges conservatively; they do not reconstruct bitemporal history.",
    "Digests identify these records for review; they are not signatures or authenticity proofs.",
)


class DossierFailure(RuntimeError):
    CODES = frozenset({"invalid_request", "invalid_configuration", "dossier_unavailable",
                       "dossier_deadline", "dossier_busy", "result_too_large"})

    def __init__(self, code="dossier_unavailable"):
        self.code = code if type(code) is str and code in self.CODES else "dossier_unavailable"
        super().__init__(self.code)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def plain_markdown(value):
    """Encode punctuation before Markdown parsing, including bare-URL syntax.

    Entities become text during rendering, never active Markdown/HTML tokens.
    Control/format characters are represented visibly rather than interpreted.
    The exact original text remains in the structured records.
    """
    result = []
    for char in value:
        code = ord(char)
        if unicodedata.category(char).startswith("C") or char in "\u2028\u2029":
            result.append(f" U+{code:04X} ")
        elif code < 128 and not (char.isalnum() or char == " "):
            result.append(f"&#{code};")
        else:
            result.append(char)
    return "".join(result)


def render_markdown(request, sections, claims, references, summary, traces):
    claim_by_key = {claim.key: claim for claim in claims}
    ref_by_key = {ref.key: ref for ref in references}
    lines = ["# " + plain_markdown(request.title), "", "Model-free retained evidence dossier.", "",
             "Semantic claim support: not reviewed. Semantic judge used: false.", "",
             f"Queries: {summary.query_count}. Distinct scoped facts: {summary.distinct_scoped_facts}.",
             f"Reference links: {summary.reference_links}; resolved: {summary.resolved_references}; unavailable: {summary.unavailable_references}.", ""]
    for section in sections:
        lines.extend(["## " + plain_markdown(section.heading), ""])
        trace = traces[section.ordinal - 1]
        lines.extend(["Research query: " + plain_markdown(trace.query),
                      "Query digest: " + trace.request_sha256,
                      "Response digest: " + trace.response_sha256,
                      "Valid at: " + plain_markdown(trace.valid_at.isoformat() if trace.valid_at else "current"),
                      "Recorded before: " + plain_markdown(trace.recorded_before.isoformat() if trace.recorded_before else "current"), ""])
        for label, keys in (("Source claims", section.source_claim_keys),
                            ("Simulation observations", section.simulation_observation_keys),
                            ("Other layer claims", section.other_claim_keys)):
            lines.extend(["### " + label, ""])
            if not keys:
                lines.extend(["No retained facts returned by this query.", ""])
            for key in keys:
                claim = claim_by_key[key]
                lines.extend(["Claim " + plain_markdown(key) + ": " + plain_markdown(claim.text),
                              "Origin: " + plain_markdown(claim.scope.model_dump_json()),
                              "Reference integrity: " + plain_markdown(claim.reference_integrity) + ". Support: not reviewed."])
                for ref_key in claim.reference_keys:
                    ref = ref_by_key[ref_key]
                    if ref.citation is None:
                        lines.append("Reference " + plain_markdown(ref.key) + ": unavailable.")
                    else:
                        citation = ref.citation
                        lines.append("Reference " + plain_markdown(ref.key) + ": " + plain_markdown(citation.source_name)
                            + "; revision " + plain_markdown(str(citation.source_revision))
                            + "; SHA256 " + citation.source_sha256
                            + f"; Unicode codepoints {citation.start} to {citation.end} (exclusive).")
                        lines.append("Retained excerpt: " + plain_markdown(citation.excerpt))
                lines.append("")
        lines.extend(["### Competing claim review candidates", ""])
        if not trace.competing_claim_candidates:
            lines.extend(["No competing claim candidates returned by this query.", ""])
        for candidate in trace.competing_claim_candidates:
            lines.extend(["Scope: " + plain_markdown(candidate.scope.model_dump_json()),
                          "Subject: " + plain_markdown(candidate.subject_id)
                          + "; predicate: " + plain_markdown(candidate.predicate),
                          "Retained fact IDs: " + plain_markdown(", ".join(candidate.provider_ids)),
                          "Candidate for review; no truth judgment.", ""])
        lines.extend(["### Query scan counts", ""])
        for coverage in trace.scopes:
            lines.append("Display " + plain_markdown(coverage.display_graph_id)
                + f": scanned {coverage.scanned}; eligible {coverage.eligible}; excluded {coverage.excluded};"
                + f" unknown {coverage.unknown}; returned {coverage.returned}; truncated {str(coverage.truncated).lower()}.")
        lines.append("")
    lines.extend(["## Retrieved passage coverage", ""])
    for coverage in summary.passage_coverage:
        lines.append("Revision " + plain_markdown(str(coverage.source_revision))
            + f": {coverage.retrieved_codepoints} of {coverage.retained_codepoints} retained Unicode codepoints.")
    lines.extend(["", "## Limitations", ""])
    lines.extend("- " + text for text in LIMITATIONS)
    return "\n".join(lines) + "\n"


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DossierFailure("dossier_deadline")
    return remaining


class EvidenceDossierService:
    """Authority/factories belong to the trusted host, never the dossier request.

    Uses the existing research worker/cleanup guards. No new executor, retry or
    background work is introduced by dossier assembly.
    """
    def __init__(self, principal, connection_factory, driver_factory, *, trusted_scope=None):
        self._research = EvidenceResearchService(principal, connection_factory, driver_factory,
                                                 trusted_scope=trusted_scope)
        self._lock = threading.Lock()
        self._inflight = False

    async def build(self, request):
        try:
            if not isinstance(request, DossierRequest):
                raise ValueError
            request = DossierRequest.model_validate(request.model_dump(warnings=False))
        except (ValueError, TypeError, AttributeError):
            raise DossierFailure("invalid_request") from None
        with self._lock:
            if self._inflight:
                raise DossierFailure("dossier_busy")
            self._inflight = True
        deadline = time.monotonic() + DEADLINE_SECONDS
        try:
            return await self._build(request, deadline)
        except DossierFailure:
            raise
        except TimeoutError:
            raise DossierFailure("dossier_deadline") from None
        except ResearchFailure as error:
            code = {"research_deadline": "dossier_deadline", "research_busy": "dossier_busy",
                    "result_too_large": "result_too_large"}.get(error.code, "dossier_unavailable")
            raise DossierFailure(code) from None
        except Exception:
            raise DossierFailure() from None
        finally:
            with self._lock:
                self._inflight = False

    async def _build(self, request, deadline):
        claims, references, facts, sections, traces = {}, {}, {}, [], []
        evidence_records, revision_records = {}, {}
        binding_scopes = None
        accumulated_bytes = 0
        for ordinal, section_request in enumerate(request.sections, 1):
            query = ResearchRequest(display_graph_ids=request.display_graph_ids,
                text=section_request.query, top_k=section_request.top_k,
                valid_at=request.valid_at, recorded_before=request.recorded_before)
            response = await asyncio.wait_for(self._research.research(query), _remaining(deadline))
            # Revalidate nested DTOs, including potentially model_copy-mutated
            # objects. Production uses the real service, not a caller response.
            response = ResearchResult.model_validate_json(response.model_dump_json())
            _remaining(deadline)
            accumulated_bytes += len(response.model_dump_json().encode("utf-8"))
            if accumulated_bytes > MAX_RESPONSE_BYTES:
                raise DossierFailure("result_too_large")
            scope_map = {item.display_graph_id: item.scope for item in response.scopes}
            if (tuple(scope_map) != request.display_graph_ids or len(scope_map) != len(response.scopes)
                    or len({scope.group_id for scope in scope_map.values()}) != len(scope_map)):
                raise DossierFailure()
            if binding_scopes is None:
                binding_scopes = scope_map
            elif binding_scopes != scope_map:
                raise DossierFailure()
            section_keys = []
            all_response_facts = []
            for group, allowed_classes in ((response.source_claims, {"source"}),
                    (response.simulation_observations, {"simulation"}),
                    (response.other_claims, {"assumption", "inference", "analysis"})):
                keys = []
                for fact in group:
                    _remaining(deadline)
                    if (fact.scope not in scope_map.values() or fact.claim_class not in allowed_classes
                            or fact.claim_class != fact.scope.layer.value):
                        raise DossierFailure()
                    key = "claim_" + digest(dict(scope=fact.scope.model_dump(mode="json"),
                                                   kind=fact.kind, provider_id=fact.provider_id))
                    if key in keys or key in {k for prior in section_keys for k in prior}:
                        raise DossierFailure()
                    evidence_ids = fact.evidence_ids
                    citation_map = {c.evidence_id: c for c in fact.citations}
                    missing = set(fact.unavailable_evidence_ids)
                    if (len(set(evidence_ids)) != len(evidence_ids)
                            or len(citation_map) != len(fact.citations)
                            or len(missing) != len(fact.unavailable_evidence_ids)
                            or missing & citation_map.keys()
                            or set(evidence_ids) != missing | citation_map.keys()):
                        raise DossierFailure()
                    ref_keys = []
                    for evidence_id in evidence_ids:
                        citation = citation_map.get(evidence_id)
                        status = "resolved" if citation is not None else "unavailable"
                        retained_key = (fact.scope.project_id, evidence_id)
                        # The evidence identity may be linked by several scoped
                        # facts, but its immutable retained data must agree.
                        retained_data = citation.model_dump(mode="json") if citation else None
                        if retained_key in evidence_records and evidence_records[retained_key] != retained_data:
                            raise DossierFailure()
                        evidence_records[retained_key] = retained_data
                        if citation:
                            revision_key = (citation.project_id, citation.source_revision)
                            metadata = (citation.source_sha256, citation.source_byte_length,
                                citation.source_codepoint_length, citation.source_name, citation.source_recorded_at)
                            if revision_records.setdefault(revision_key, metadata) != metadata:
                                raise DossierFailure()
                        ref_key = "ref_" + digest(dict(claim_key=key, scope=fact.scope.model_dump(mode="json"),
                            provider_id=fact.provider_id, evidence_id=str(evidence_id)))
                        ref = DossierReference(key=ref_key, claim_key=key, scope=fact.scope,
                            provider_id=fact.provider_id, evidence_id=evidence_id, status=status, citation=citation)
                        if ref_key in references and references[ref_key] != ref:
                            raise DossierFailure()
                        references[ref_key] = ref
                        ref_keys.append(ref_key)
                    integrity = ("no_evidence_links" if not evidence_ids else "resolved" if not missing
                                 else "unavailable" if not citation_map else "partly_unavailable")
                    claim = DossierClaim(key=key, scope=fact.scope, provider_id=fact.provider_id,
                        claim_class=fact.claim_class, text=fact.fact, predicate=fact.name,
                        source_node_id=fact.source_node_id, target_node_id=fact.target_node_id,
                        episode_ids=fact.episode_ids, evidence_ids=evidence_ids,
                        reference_keys=tuple(ref_keys), created_at=fact.created_at, valid_at=fact.valid_at,
                        invalid_at=fact.invalid_at, expired_at=fact.expired_at, reference_integrity=integrity)
                    if key in claims and claims[key] != claim:
                        raise DossierFailure()
                    claims[key], facts[key] = claim, fact
                    keys.append(key)
                    all_response_facts.append(fact)
                section_keys.append(tuple(keys))
            if (len(all_response_facts) > query.top_k
                    or response.linked_citations != sum(len(f.evidence_ids) for f in all_response_facts)
                    or response.resolved_citations != sum(len(f.citations) for f in all_response_facts)
                    or response.unavailable_citations != sum(len(f.unavailable_evidence_ids) for f in all_response_facts)
                    or response.passage_coverage != passage_coverage(all_response_facts)
                    or response.historical != (request.valid_at is not None or request.recorded_before is not None)):
                raise DossierFailure()
            for item in response.scopes:
                if item.returned != sum(f.scope == item.scope for f in all_response_facts):
                    raise DossierFailure()
            for candidate in response.competing_claim_candidates:
                matching = [f for f in all_response_facts if f.scope == candidate.scope
                            and f.source_node_id == candidate.subject_id and f.name == candidate.predicate]
                if (set(candidate.provider_ids) != {f.provider_id for f in matching}
                        or len(candidate.provider_ids) != len(matching)
                        or len({(f.target_node_id, f.fact) for f in matching}) < 2
                        or set(candidate.evidence_ids) != {c.evidence_id for f in matching for c in f.citations}):
                    raise DossierFailure()
            sections.append(DossierSection(ordinal=ordinal, heading=section_request.heading,
                source_claim_keys=section_keys[0], simulation_observation_keys=section_keys[1],
                other_claim_keys=section_keys[2]))
            traces.append(ResearchTrace(ordinal=ordinal, request_sha256=digest(query.model_dump(mode="json")),
                response_sha256=digest(response.model_dump(mode="json")), query=query.text,
                top_k=query.top_k, display_graph_ids=query.display_graph_ids, valid_at=query.valid_at,
                recorded_before=query.recorded_before, scopes=response.scopes,
                passage_coverage=response.passage_coverage, competing_claim_candidates=response.competing_claim_candidates,
                linked_citations=response.linked_citations, resolved_citations=response.resolved_citations,
                unavailable_citations=response.unavailable_citations, historical=response.historical,
                historical_semantics=response.historical_semantics, rank_basis=response.rank_basis))
        _remaining(deadline)
        ordered_claims = tuple(claims[key] for key in sorted(claims))
        ordered_refs = tuple(references[key] for key in sorted(references))
        summary = DossierSummary(section_count=len(sections), query_count=len(traces),
            distinct_scoped_facts=len(claims), reference_links=len(references),
            resolved_references=sum(ref.status == "resolved" for ref in ordered_refs),
            unavailable_references=sum(ref.status == "unavailable" for ref in ordered_refs),
            query_reference_links=sum(t.linked_citations for t in traces),
            query_resolved_references=sum(t.resolved_citations for t in traces),
            query_unavailable_references=sum(t.unavailable_citations for t in traces),
            scanned_per_query_sum=sum(s.scanned for t in traces for s in t.scopes),
            unknown_per_query_sum=sum(s.unknown for t in traces for s in t.scopes),
            truncated_query_scopes=sum(s.truncated for t in traces for s in t.scopes),
            passage_coverage=passage_coverage(tuple(facts.values())))
        record_data = dict(sections=[s.model_dump(mode="json") for s in sections],
            claims=[c.model_dump(mode="json") for c in ordered_claims],
            references=[r.model_dump(mode="json") for r in ordered_refs], summary=summary.model_dump(mode="json"))
        result = EvidenceDossier(request=request, sections=tuple(sections), claims=ordered_claims,
            references=ordered_refs, research_trace=tuple(traces), summary=summary,
            input_sha256=digest(request.model_dump(mode="json")),
            trace_sha256=digest([t.model_dump(mode="json") for t in traces]), records_sha256=digest(record_data),
            limitations=LIMITATIONS, markdown=render_markdown(request, sections, ordered_claims, ordered_refs, summary, traces))
        if len(result.model_dump_json().encode("utf-8")) + 1 > MAX_RESPONSE_BYTES:
            raise DossierFailure("result_too_large")
        _remaining(deadline)
        return result
