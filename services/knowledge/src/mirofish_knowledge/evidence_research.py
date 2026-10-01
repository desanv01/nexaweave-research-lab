"""Trusted read-only host connecting scoped edge pages to retained passages."""
from __future__ import annotations

import asyncio
import inspect
import re
import threading
import time
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timezone

from mirofish_storage.source import SourceStore
from mirofish_storage.store import NotFound as SourceNotFound, ProjectStore
from mirofish_storage.validation import principal_id

from .bindings import ScopeBindingStore
from .contracts import GraphPageRequest, KnowledgeScope
from .operations import Ledger
from .provider import _native
from .read_runtime import _DirectPageProvider
from .research_contracts import (Citation, ClaimCandidate, PassageCoverage, ResearchFact,
                                 ResearchRequest, ResearchResult, ScopeCoverage)

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEADLINE_SECONDS = 30.0
# Surviving off-thread cleanup after caller timeout still occupies a slot.
_WORKERS = threading.BoundedSemaphore(2)
_EPISODE_RECORDING_QUERY = (
    "MATCH (e:Episodic) WHERE e.group_id = $group_id AND e.uuid IN $ids "
    "RETURN e.uuid AS uuid, e.group_id AS row_group, e.created_at AS created_at "
    "ORDER BY e.uuid ASC LIMIT $limit"
)


class ResearchFailure(RuntimeError):
    def __init__(self, code="research_unavailable"):
        self.code = code
        super().__init__(code)


def _tokens(text):
    return frozenset(re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE))


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def temporal_edge(fact, request, now):
    """Return status and visible timestamps; never expose future invalidation."""
    created, valid, invalid, expired = fact.created_at, fact.valid_at, fact.invalid_at, fact.expired_at
    for timestamp in (created, valid, invalid, expired):
        if timestamp is not None and not _aware(timestamp):
            return "unknown", None
    if (request.valid_at is not None or request.recorded_before is not None) and created is None:
        return "unknown", None
    if created is not None and created > (request.recorded_before or now):
        return "excluded", None
    if request.recorded_before is not None:
        if created is None:
            return "unknown", None
        if created > request.recorded_before:
            return "excluded", None
        # expired_at is Graphiti's recording time for invalidation. Without it,
        # an invalid_at cannot be assigned to a historical recording cutoff.
        if invalid is not None and expired is None:
            return "unknown", None
        if expired is not None and expired > request.recorded_before:
            invalid = expired = None
    effective_valid = request.valid_at or now
    if request.valid_at is not None and valid is None:
        return "unknown", None
    if valid is not None and valid > effective_valid:
        return "excluded", None
    if invalid is not None and invalid <= effective_valid:
        return "excluded", None
    # A recording-only snapshot excludes edges already expired at that cutoff;
    # a valid-time request instead evaluates the half-open assertion interval.
    if request.valid_at is None and expired is not None and expired <= (request.recorded_before or now):
        return "excluded", None
    return "eligible", (created, valid, invalid, expired)


def passage_coverage(facts):
    revisions = {}
    for fact in facts:
        for citation in fact.citations:
            key = (citation.project_id, citation.source_revision)
            metadata = (citation.source_sha256, citation.source_codepoint_length)
            if key not in revisions:
                revisions[key] = (metadata, [])
            if revisions[key][0] != metadata:
                raise ResearchFailure()
            revisions[key][1].append((citation.start, citation.end))
    result = []
    for (project, revision), ((digest, length), spans) in sorted(revisions.items(), key=lambda item: tuple(map(str, item[0]))):
        covered = 0
        end = 0
        for start, stop in sorted(spans):
            covered += max(0, stop - max(start, end))
            end = max(end, stop)
        result.append(PassageCoverage(project_id=project, source_revision=revision,
            source_sha256=digest, retained_codepoints=length, retrieved_codepoints=covered,
            retrieved_passage_fraction=covered / length))
    return tuple(result)


def claim_candidates(facts):
    groups = {}
    for fact in facts:
        # Missing predicate cannot establish comparable claims.
        if fact.name:
            groups.setdefault((fact.scope.group_id, fact.source_node_id, fact.name), []).append(fact)
    result = []
    for key, items in sorted(groups.items()):
        if len({(item.target_node_id, item.fact) for item in items}) > 1:
            result.append(ClaimCandidate(scope=items[0].scope, subject_id=key[1], predicate=key[2],
                provider_ids=tuple(sorted(item.provider_id for item in items)),
                evidence_ids=tuple(sorted({citation.evidence_id for item in items for citation in item.citations}))))
    return tuple(result)


class EvidenceResearchService:
    """Own one driver per operation. Factories and identity come from trusted host."""
    def __init__(self, principal, connection_factory, driver_factory, *, trusted_scope=None):
        self.principal = principal_id(principal)
        if not callable(connection_factory) or not callable(driver_factory):
            raise ValueError("research factories required")
        self._bindings = ScopeBindingStore(connection_factory)
        self._ledger = Ledger(connection_factory)
        self._sources = SourceStore(connection_factory)
        self._projects = ProjectStore(connection_factory)
        self._driver_factory = driver_factory
        self._trusted_scope = (KnowledgeScope.model_validate(trusted_scope.model_dump(warnings=False))
                               if trusted_scope is not None else None)
        self._inflight = False
        self._lock = threading.Lock()

    async def research(self, request):
        try:
            if not isinstance(request, ResearchRequest):
                raise ValueError
            request = ResearchRequest.model_validate(request.model_dump(warnings=False))
        except (ValueError, TypeError, AttributeError):
            raise ResearchFailure("invalid_request") from None
        with self._lock:
            if self._inflight or not _WORKERS.acquire(blocking=False):
                raise ResearchFailure("research_busy")
            self._inflight = True
        deadline = time.monotonic() + DEADLINE_SECONDS

        def work():
            try:
                return self._work(request, deadline)
            finally:
                with self._lock:
                    self._inflight = False
                _WORKERS.release()

        # Shield keeps guards/driver cleanup alive if the caller cancels or expires.
        task = asyncio.create_task(asyncio.to_thread(work))
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        try:
            return await asyncio.wait_for(asyncio.shield(task), DEADLINE_SECONDS)
        except TimeoutError:
            raise ResearchFailure("research_deadline") from None
        except ResearchFailure:
            raise
        except Exception:
            raise ResearchFailure() from None

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ResearchFailure("research_deadline")
        return remaining

    def _work(self, request, deadline):
        try:
            records = []
            for display in request.display_graph_ids:
                self._remaining(deadline)
                record = self._bindings.resolve(self.principal, display)
                if record.principal != self.principal or record.display_graph_id != display:
                    raise ResearchFailure()
                scope = KnowledgeScope.model_validate(record.scope.model_dump(warnings=False))
                records.append((display, scope))
            identities = {(scope.workspace_id, scope.project_id, scope.graph_id) for _, scope in records}
            if len(identities) != 1 or len({scope.group_id for _, scope in records}) != len(records):
                raise ResearchFailure()
            if self._trusted_scope is not None:
                anchor = self._trusted_scope
                if identities != {(anchor.workspace_id, anchor.project_id, anchor.graph_id)}:
                    raise ResearchFailure()
            # Confirm project ownership even when the graph has no evidence links.
            self._remaining(deadline)
            project = self._projects.get(self.principal, records[0][1].project_id)
            if (project.principal != self.principal or project.project_id != records[0][1].project_id
                    or project.workspace_id != records[0][1].workspace_id):
                raise ResearchFailure()
            with ExitStack() as guards:
                for _, scope in sorted(records, key=lambda item: item[1].group_id):
                    self._remaining(deadline)
                    guards.enter_context(self._ledger.read_scope(scope))
                # Re-resolve under all guards, detecting binding/tombstone changes.
                for display, scope in records:
                    self._remaining(deadline)
                    if self._bindings.resolve(self.principal, display).scope != scope:
                        raise ResearchFailure()
                return asyncio.run(self._fetch(request, records, deadline))
        except ResearchFailure:
            raise
        except Exception:
            raise ResearchFailure() from None

    async def _fetch(self, request, records, deadline):
        self._remaining(deadline)
        driver = self._driver_factory()
        try:
            provider = _DirectPageProvider(driver)
            query_tokens = _tokens(request.text)
            collected, coverage = [], []
            history_evidence, revision_metadata = {}, {}

            def citation_for(scope, evidence_id):
                self._remaining(deadline)
                try:
                    item = self._sources.resolve_evidence(self.principal, scope.project_id, evidence_id)
                except SourceNotFound:
                    self._remaining(deadline)
                    return None
                if item.principal != self.principal or item.project_id != scope.project_id or item.evidence_id != evidence_id:
                    raise ResearchFailure()
                data = asdict(item)
                data.pop("principal")
                citation = Citation.model_validate(data)
                metadata = (citation.source_sha256, citation.source_byte_length,
                            citation.source_codepoint_length, citation.source_recorded_at,
                            citation.source_name)
                previous = revision_metadata.setdefault(citation.source_revision, metadata)
                if previous != metadata:
                    raise ResearchFailure()
                self._remaining(deadline)
                return citation

            async def provenance_status(scope, fact):
                if not fact.episode_ids or not fact.evidence_ids:
                    return "unknown"
                ids = tuple(sorted(set(fact.episode_ids)))
                rows = await asyncio.wait_for(provider._rows(_EPISODE_RECORDING_QUERY,
                    group_id=scope.group_id, ids=list(ids), limit=len(ids) + 1), self._remaining(deadline))
                if len(rows) > len(ids):
                    raise ResearchFailure()
                times = {}
                for row in rows:
                    episode_id = row["uuid"]
                    if row["row_group"] != scope.group_id or episode_id not in ids or episode_id in times:
                        raise ResearchFailure()
                    times[episode_id] = _native(row["created_at"])
                statuses = []
                for episode_id in ids:
                    timestamp = times.get(episode_id)
                    statuses.append("unknown" if not _aware(timestamp) else
                                    "excluded" if timestamp > request.recorded_before else "eligible")
                # Check every linked source, not just citations on the final top_k.
                # Cache status only: excluded/unknown excerpts are never retained
                # for output, and ranking never admits an unproven historical fact.
                for evidence_id in fact.evidence_ids:
                    key = (scope.project_id, evidence_id)
                    if key not in history_evidence:
                        citation = citation_for(scope, evidence_id)
                        history_evidence[key] = ("unknown" if citation is None else "excluded"
                            if citation.source_recorded_at > request.recorded_before else "eligible")
                    statuses.append(history_evidence[key])
                return "excluded" if "excluded" in statuses else "unknown" if "unknown" in statuses else "eligible"

            now = datetime.now(timezone.utc)
            for display, scope in records:
                cursor, scanned, excluded, unknown, eligible, pages = None, 0, 0, 0, 0, 0
                seen = set()
                for _ in range(5):
                    page = await asyncio.wait_for(provider.page(scope, GraphPageRequest(kind="edge", limit=100, cursor=cursor)),
                                                  self._remaining(deadline))
                    pages += 1
                    scanned += len(page.facts)
                    for fact in page.facts:
                        if fact.scope != scope or fact.kind != "edge":
                            raise ResearchFailure()
                        key = (scope.group_id, fact.kind, fact.provider_id)
                        if key in seen:
                            continue
                        seen.add(key)
                        status, timestamps = temporal_edge(fact, request, now)
                        if status == "unknown":
                            unknown += 1
                            continue
                        if status == "excluded":
                            excluded += 1
                            continue
                        if (not fact.fact or not fact.source_node_id or not fact.target_node_id
                                or len(fact.evidence_ids) > 100 or len(fact.episode_ids) > 100):
                            raise ResearchFailure()
                        if request.recorded_before is not None:
                            status = await provenance_status(scope, fact)
                            if status == "unknown":
                                unknown += 1
                                continue
                            if status == "excluded":
                                excluded += 1
                                continue
                        eligible += 1
                        created, valid, invalid, expired = timestamps
                        collected.append(ResearchFact(provider_id=fact.provider_id, scope=scope,
                            claim_class=scope.layer.value, name=fact.name, fact=fact.fact,
                            source_node_id=fact.source_node_id, target_node_id=fact.target_node_id,
                            episode_ids=fact.episode_ids, evidence_ids=fact.evidence_ids,
                            created_at=created, valid_at=valid, invalid_at=invalid, expired_at=expired,
                            overlap_tokens=len(query_tokens & _tokens(fact.fact + " " + (fact.name or ""))),
                            query_tokens=len(query_tokens), citations=(), unavailable_evidence_ids=()))
                    cursor = page.next_cursor
                    if cursor is None:
                        break
                coverage.append(ScopeCoverage(display_graph_id=display, scope=scope, pages=pages,
                    scanned=scanned, eligible=eligible, excluded=excluded, unknown=unknown,
                    returned=0, truncated=cursor is not None))
            selected = sorted(collected, key=lambda fact: (-fact.overlap_tokens, fact.scope.group_id,
                                                          fact.kind, fact.provider_id))[:request.top_k]
            resolved, cache = [], {}
            response_estimate = sum(len(fact.model_dump_json().encode("utf-8")) for fact in selected)
            if response_estimate > MAX_RESPONSE_BYTES:
                raise ResearchFailure("result_too_large")
            for fact in selected:
                citations, missing = [], []
                for evidence_id in fact.evidence_ids:
                    self._remaining(deadline)
                    if evidence_id not in cache:
                        citation = citation_for(fact.scope, evidence_id)
                        # Retained source revisions are immutable through the
                        # accepted API. Fail safely if evidence changes between
                        # historical admission and final citation resolution.
                        if request.recorded_before is not None and (citation is None
                                or citation.source_recorded_at > request.recorded_before):
                            raise ResearchFailure()
                        cache[evidence_id] = citation
                    self._remaining(deadline)
                    citation = cache[evidence_id]
                    if citation is None:
                        missing.append(evidence_id)
                    else:
                        citations.append(citation)
                        response_estimate += len(citation.model_dump_json().encode("utf-8"))
                        if response_estimate > MAX_RESPONSE_BYTES:
                            raise ResearchFailure("result_too_large")
                resolved.append(fact.model_copy(update={"citations": tuple(citations),
                                                        "unavailable_evidence_ids": tuple(missing)}))
            self._remaining(deadline)
            result = ResearchResult(
                source_claims=tuple(fact for fact in resolved if fact.claim_class == "source"),
                simulation_observations=tuple(fact for fact in resolved if fact.claim_class == "simulation"),
                other_claims=tuple(fact for fact in resolved if fact.claim_class not in {"source", "simulation"}),
                scopes=tuple(item.model_copy(update={"returned": sum(fact.scope == item.scope for fact in resolved)}) for item in coverage),
                passage_coverage=passage_coverage(resolved), competing_claim_candidates=claim_candidates(resolved),
                linked_citations=sum(len(fact.evidence_ids) for fact in resolved),
                resolved_citations=sum(len(fact.citations) for fact in resolved),
                unavailable_citations=sum(len(fact.unavailable_evidence_ids) for fact in resolved),
                historical=request.valid_at is not None or request.recorded_before is not None)
            if len(result.model_dump_json().encode("utf-8")) > MAX_RESPONSE_BYTES:
                raise ResearchFailure("result_too_large")
            return result
        finally:
            closing = driver.close()
            if inspect.isawaitable(closing):
                await asyncio.wait_for(closing, timeout=3)
