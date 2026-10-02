"""Owned retained source workflow. Graphiti is imported only after budget start."""
import asyncio
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from .contracts import OntologySpec
from .source_library import SourceSettings, SourceLibrary, SourceError, encoded, uuid_value, scope_value
from .source_bridge import SourceIngestionBridge, BridgeError
from .ingestion import KnowledgeIngestionCoordinator
from .operations import Ledger, NotFound, Tombstoned, Conflict, Busy, InvalidTransition, _receipt
from .stdio import _object
from mirofish_execution.budget import (BudgetLedger, BudgetError, _transaction,
    _money, ReservationState)
from mirofish_execution.budgeted_ingestion import BudgetedIngestion
from mirofish_storage import NotFound as SourceNotFound, Conflict as SourceConflict

MAX_BYTES = 256 * 1024
ENVELOPE_OVERHEAD = 1024
ERROR_CODES = {"invalid_request", "not_found", "source_denied", "tombstoned", "conflict",
    "busy", "cancelled", "uncertain", "model_calls_disabled", "budget_denied", "source_unavailable", "result_too_large"}


def validate_payload(method, value):
    if type(value) is not dict or len(encoded(value)) > MAX_BYTES:
        raise ValueError
    if method == "status":
        if set(value) != {"operation_id"}:
            raise ValueError
        return uuid_value(value["operation_id"]), None, None
    if (method not in {"plan", "execute"} or set(value) != {"schema_version", "source_revision", "operation_id", "ontology"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ValueError
    ontology = value["ontology"]
    if (type(ontology) is not dict or set(ontology) != {"schema_version", "revision", "entity_types", "edge_types"}
            or type(ontology["schema_version"]) is not int or ontology["schema_version"] != 1):
        raise ValueError
    uuid_value(ontology["revision"])
    spec = OntologySpec.model_validate_json(encoded(ontology))
    # Reject missing/defaulted nested fields as well as coercion.
    if spec.model_dump(mode="json") != ontology:
        raise ValueError
    return uuid_value(value["operation_id"]), uuid_value(value["source_revision"]), spec


@dataclass(frozen=True, repr=False)
class IngestionSettings:
    source: SourceSettings
    enabled: bool = False
    model_calls_authorized: bool = False
    account_id: object = None
    ceiling_microusd: object = None
    provider_environment: object = None

    @classmethod
    def from_environment(cls):
        # No provider SDK/config import or construction on the cold paths.
        source = SourceSettings.from_environment()
        account = os.environ.get("KNOWLEDGE_INGESTION_ACCOUNT_ID")
        account = None if account is None else uuid_value(account)
        ceiling = os.environ.get("KNOWLEDGE_INGESTION_CEILING_MICROUSD")
        if ceiling is not None:
            if not ceiling.isascii() or not ceiling.isdecimal() or str(int(ceiling)) != ceiling:
                raise ValueError
            ceiling = _money(int(ceiling))
        return cls(source, os.environ.get("KNOWLEDGE_INGESTION_ENABLED") == "true",
                   os.environ.get("KNOWLEDGE_MODEL_CALLS_AUTHORIZED") == "true", account, ceiling,
                   dict(os.environ))

    def authorize_execution(self):
        if self.enabled is not True or self.model_calls_authorized is not True or self.account_id is None:
            raise SourceError("model_calls_disabled")
        uuid_value(str(self.account_id))
        _money(self.ceiling_microusd)
        values = self.provider_environment or {}
        for key in ("KNOWLEDGE_LLM_BASE_URL", "KNOWLEDGE_LLM_MODEL", "KNOWLEDGE_LLM_API_KEY",
                    "KNOWLEDGE_EMBEDDING_BASE_URL", "KNOWLEDGE_EMBEDDING_MODEL", "KNOWLEDGE_EMBEDDING_API_KEY",
                    "KNOWLEDGE_EMBEDDING_DIMENSION", "KNOWLEDGE_NEO4J_URI", "KNOWLEDGE_NEO4J_USER", "KNOWLEDGE_NEO4J_PASSWORD"):
            value = values.get(key)
            if type(value) is not str or not 1 <= len(value) <= 1024 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise SourceError("model_calls_disabled")
        try:
            dimension = values["KNOWLEDGE_EMBEDDING_DIMENSION"]
            if not dimension.isascii() or not dimension.isdecimal() or str(int(dimension)) != dimension or not 1 <= int(dimension) <= 4096:
                raise ValueError
            profile = values.get("KNOWLEDGE_OPERATING_PROFILE", "hybrid")
            if profile not in {"hybrid", "local_only"}:
                raise ValueError
            for key in ("KNOWLEDGE_LLM_BASE_URL", "KNOWLEDGE_EMBEDDING_BASE_URL", "KNOWLEDGE_NEO4J_URI"):
                endpoint = urlsplit(values[key])
                bolt = key == "KNOWLEDGE_NEO4J_URI"
                if (endpoint.scheme not in ({"bolt", "neo4j"} if bolt else {"http", "https"})
                        or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
                        or (bolt and endpoint.path) or (endpoint.port is not None and not 1 <= endpoint.port <= 65535)):
                    raise ValueError
                if profile == "local_only":
                    from .local_transport import local_endpoint
                    local_endpoint(values[key], bolt=bolt)
        except Exception:
            raise SourceError("model_calls_disabled") from None


class LazyGraphitiProvider:
    def __init__(self, settings, *, factory=None):
        self.settings, self.factory, self.provider = settings, factory, None
        self._initialization_lock = asyncio.Lock()
        self._failed = False
        self._closed = False

    async def _ready_provider(self):
        # The coordinator calls this only after durable budget start and write
        # admission. Publish an adapter only after successful initialization.
        async with self._initialization_lock:
            if self._closed or self._failed:
                raise SourceError("uncertain")
            if self.provider is not None:
                return self.provider
            candidate = None
            try:
                self.settings.authorize_execution()
                if self.factory is not None:
                    # Constructor-only test seam; lifecycle-aware fixtures still
                    # initialize. Existing minimal deterministic fakes need none.
                    candidate = self.factory()
                    initialize = getattr(candidate, "initialize", None)
                    if initialize is not None:
                        await initialize()
                else:
                    from .provider import GraphitiKnowledgeProvider, ProviderConfig
                    candidate = GraphitiKnowledgeProvider(ProviderConfig.from_env())
                    await candidate.initialize()
            except BaseException:
                # Failed/cancelled initialization is terminal for this wrapper.
                # The real adapter already attempts close on initialize failure;
                # its idempotent close also covers allocated fixture resources.
                self._failed = True
                if candidate is not None and hasattr(candidate, "close"):
                    try:
                        await candidate.close()
                    except BaseException:
                        pass  # Preserve the original failure, never dispatch.
                raise
            self.provider = candidate
            return candidate

    async def ingest(self, scope, source, ontology):
        provider = await self._ready_provider()
        return await provider.ingest(scope, source, ontology)

    async def completion_proof(self, scope, source, ontology):
        if self.provider is None or self._closed or self._failed:
            raise SourceError("uncertain")
        return await self.provider.completion_proof(scope, source, ontology)

    async def close(self):
        async with self._initialization_lock:
            if self._closed:
                return
            self._closed = True
            provider, self.provider = self.provider, None
            if provider is not None and hasattr(provider, "close"):
                await provider.close()


def plan_result(plan):
    return {"schema_version": 1, "scope": plan.scope.model_dump(mode="json"),
        "operation_id": str(plan.source.operation_id), "episode_id": str(plan.scope.episode_uuid(plan.source.operation_id)),
        "fingerprint": plan.request_fingerprint, "evidence_ids": [str(v) for v in plan.source.evidence_ids],
        "source_revision": str(plan.source.source_revision), "source_sha256": plan.source.source_sha256,
        "source_byte_length": plan.source_byte_length, "source_codepoint_length": plan.source_codepoint_length,
        "ontology_revision": str(plan.ontology.revision), "eligibility_codepoint_limit": 32768,
        "spending_authorized": False, "graph_ingestion_executed": False, "model_calls_made": False,
        "actual_usage_microusd": None}


class SourceIngestionHost:
    def __init__(self, settings, *, connection_factory=None, provider_factory=None):
        self.settings = settings
        self.connect = connection_factory or settings.source.connect
        self.authority = SourceLibrary(settings.source, connection_factory=self.connect)
        self.bridge = SourceIngestionBridge(self.connect)
        self.budget = BudgetLedger(self.connect)
        self.ledger = Ledger(self.connect)
        self.provider_factory = provider_factory

    def _reservation(self, scope, operation):
        account_id = self.settings.account_id
        if account_id is None:
            return None
        # Existing ledger's typed row parser, fixed SQL and short read transaction.
        # No account creation, reservation or mutation here.
        with _transaction(self.connect) as conn:
            account = self.budget._account(conn, self.settings.source.principal, account_id)
            if account[2] != scope.project_id:
                raise SourceError("budget_denied")
            row = self.budget._row(conn, account_id, operation)
        if row is not None and (row.scope_group_id != scope.group_id or row.episode_id != scope.episode_uuid(operation)):
            raise SourceError("uncertain")
        return row

    def status(self, operation):
        scope = self.authority.authorize()
        try:
            record = self.ledger.get(scope, operation)
        except NotFound:
            record = None
        reservation = self._reservation(scope, operation)
        if record is None and reservation is None:
            raise SourceError("not_found")
        fingerprint = record.fingerprint if record else reservation.fingerprint
        evidence = reservation.evidence_ids if reservation else (record.receipt.evidence_ids if record.receipt else ())
        receipt = None
        if record is not None and record.receipt is not None:
            receipt = _receipt(record.receipt, scope, operation, fingerprint)
            if receipt.evidence_ids != evidence:
                raise SourceError("uncertain")
        if reservation is not None:
            if reservation.fingerprint != fingerprint:
                raise SourceError("uncertain")
            if reservation.receipt is not None:
                valid = _receipt(reservation.receipt, scope, operation, fingerprint)
                if valid.evidence_ids != evidence or receipt != valid:
                    raise SourceError("uncertain")
        state = record.state.value if record else "not_admitted"
        if (state == "completed") != (receipt is not None):
            raise SourceError("uncertain")
        if reservation is not None and reservation.state == ReservationState.settled and state != "completed":
            raise SourceError("uncertain")
        return {"schema_version": 1, "scope": scope.model_dump(mode="json"),
            "operation_id": str(operation), "episode_id": str(scope.episode_uuid(operation)),
            "fingerprint": fingerprint, "evidence_ids": [str(v) for v in evidence],
            "state": state, "budget_state": reservation.state.value if reservation else "not_reserved",
            "ceiling_microusd": reservation.ceiling_microusd if reservation else None,
            "receipt": receipt.json_value() if receipt else None,
            "graph_ingestion_executed": state == "completed", "model_calls_made": None,
            "actual_usage_microusd": None}

    async def _ingest(self, revision, operation, ontology):
        lazy = LazyGraphitiProvider(self.settings, factory=self.provider_factory)
        coordinator = KnowledgeIngestionCoordinator(self.ledger, lazy, timeout_seconds=60)
        budgeted = BudgetedIngestion(self.bridge, self.budget, coordinator)
        try:
            receipt = await budgeted.ingest(self.settings.source.principal, self.settings.source.display_graph_id,
                revision, operation, ontology, account_id=self.settings.account_id,
                ceiling_microusd=self.settings.ceiling_microusd)
            return receipt
        finally:
            # Cleanup tasks must finish within their existing bounded semantics
            # before asyncio.run tears down this one-shot child's loop.
            tasks = tuple(budgeted._cleanup_tasks | coordinator._cleanup_tasks)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await lazy.close()

    def execute(self, method, payload):
        operation, revision, ontology = validate_payload(method, payload)
        scope = self.authority.authorize()
        if method == "status":
            return self.status(operation)
        plan = self.bridge.plan(self.settings.source.principal, self.settings.source.display_graph_id,
                                revision, operation, ontology)
        if plan.scope != scope:
            raise SourceError("source_denied")
        if method == "plan":
            return plan_result(plan)
        self.settings.authorize_execution()
        # Explicit existing account's persisted principal/project/cap, before any
        # provider construction. Reserve repeats admission under the account lock.
        account = self.budget.status(self.settings.source.principal, self.settings.account_id)
        if account.project_id != scope.project_id or account.principal != self.settings.source.principal:
            raise SourceError("budget_denied")
        _money(account.cap_microusd)
        prior = self._reservation(scope, operation)
        if prior is not None:
            if (prior.fingerprint != plan.request_fingerprint or prior.ceiling_microusd != self.settings.ceiling_microusd
                    or prior.evidence_ids != plan.source.evidence_ids):
                raise SourceError("conflict")
            if prior.state == ReservationState.uncertain:
                raise SourceError("uncertain")
            if prior.state == ReservationState.started:
                raise SourceError("busy")
            if prior.state == ReservationState.released:
                raise SourceError("cancelled")
        try:
            existing = self.ledger.get(scope, operation)
        except NotFound:
            existing = None
        if existing is not None:
            if existing.fingerprint != plan.request_fingerprint:
                raise SourceError("conflict")
            if existing.state.value in {"running", "uncertain", "cancelled"}:
                raise SourceError({"running": "busy", "uncertain": "uncertain", "cancelled": "cancelled"}[existing.state.value])
        if prior is not None and prior.state == ReservationState.settled:
            saved = self.status(operation)
            if saved["state"] != "completed" or saved["receipt"] is None:
                raise SourceError("uncertain")
            return saved
        receipt = asyncio.run(self._ingest(revision, operation, ontology))
        valid = _receipt(receipt, plan.scope, operation, plan.request_fingerprint)
        if valid.evidence_ids != plan.source.evidence_ids:
            raise SourceError("uncertain")
        result = self.status(operation)
        if result["state"] != "completed" or result["budget_state"] != "settled" or result["receipt"] != valid.json_value():
            raise SourceError("uncertain")
        return result

    def dispatch(self, raw):
        request_id = None
        try:
            if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES + ENVELOPE_OVERHEAD:
                raise ValueError
            value = _object(raw)
            if (set(value) != {"version", "request_id", "method", "scope", "payload"}
                    or type(value["version"]) is not int or value["version"] != 1):
                raise ValueError
            request_id = str(uuid_value(value["request_id"]))
            if scope_value(value["scope"]) != self.settings.source.scope:
                raise SourceError("source_denied")
            result = self.execute(value["method"], value["payload"])
            if len(encoded(result)) > MAX_BYTES:
                raise SourceError("result_too_large")
            return encoded({"version": 1, "request_id": request_id, "ok": True, "result": result})
        except SourceError as error:
            code = error.code if error.code in ERROR_CODES else "source_unavailable"
        except BridgeError as error:
            code = {"invalid_ingestion_plan": "invalid_request", "ingestion_source_not_found": "not_found",
                "ingestion_not_authorized": "source_denied", "ingestion_conflict": "conflict",
                "ingestion_uncertain": "uncertain"}.get(error.code, "source_unavailable")
        except BudgetError as error:
            code = {"budget_denied": "budget_denied", "budget_conflict": "conflict",
                "budget_busy": "busy", "budget_uncertain": "uncertain", "invalid_budget": "model_calls_disabled"}.get(error.code, "source_unavailable")
        except Tombstoned:
            code = "tombstoned"
        except (NotFound, SourceNotFound):
            code = "not_found"
        except (Conflict, SourceConflict):
            code = "conflict"
        except Busy:
            code = "busy"
        except InvalidTransition:
            code = "cancelled"
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            code = "invalid_request"
        except Exception:
            code = "source_unavailable"
        return encoded({"version": 1, "request_id": request_id, "ok": False, "error": {"code": code}})
