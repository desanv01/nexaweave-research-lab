"""Owned retained source to offline knowledge ingestion plan.

This module does not create providers or authorize model spending. A trusted host
must supply both the identities and a durable budget authorization callback.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable
from uuid import UUID

import psycopg

from mirofish_storage import ProjectStore, SourceStore
from mirofish_storage.store import NotFound as ProjectNotFound, StorageError as ProjectStorageError
from mirofish_storage.validation import InvalidProject, principal_id, uuid_value

from .bindings import BindingRecord, ScopeBindingStore, _identity
from .contracts import KnowledgeScope, Layer, OntologySpec, SourceEnvelope
from .ingestion import IngestionUncertain, KnowledgeIngestionCoordinator
from .operations import (Busy, CompletionReceipt, Conflict, InvalidTransition,
                         NotFound, StorageError, Tombstoned, _receipt,
                         request_fingerprint)


class BridgeError(RuntimeError):
    code = "ingestion_plan_error"
    def __init__(self):
        super().__init__(self.code)


class BridgeInvalid(BridgeError):
    code = "invalid_ingestion_plan"


class BridgeNotFound(BridgeError):
    code = "ingestion_source_not_found"


class BridgeDenied(BridgeError):
    code = "ingestion_not_authorized"


class BridgeUnavailable(BridgeError):
    code = "ingestion_store_unavailable"


class BridgeConflict(BridgeError):
    code = "ingestion_conflict"


class BridgeUncertain(BridgeError):
    code = "ingestion_uncertain"


@dataclass(frozen=True)
class IngestionPlan:
    principal: str
    display_graph_id: str
    scope: KnowledgeScope
    source: SourceEnvelope
    ontology: OntologySpec
    request_fingerprint: str
    source_byte_length: int
    source_codepoint_length: int
    source_provenance: str = "retained_extracted_text"


def _ontology(value: object) -> OntologySpec:
    try:
        if not isinstance(value, OntologySpec):
            raise ValueError
        return OntologySpec.model_validate(value.model_dump(warnings=False))
    except (AttributeError, TypeError, ValueError):
        raise BridgeInvalid() from None


def _scope(value: object) -> KnowledgeScope:
    try:
        if not isinstance(value, KnowledgeScope):
            raise ValueError
        result = KnowledgeScope.model_validate(value.model_dump(warnings=False))
        if (result.layer != Layer.source or result.run_id is not None
                or result.branch_id is not None):
            raise ValueError
        return result
    except (AttributeError, TypeError, ValueError):
        raise BridgeInvalid() from None


class SourceIngestionBridge:
    def __init__(self, connection_factory: Callable[[], psycopg.Connection]):
        if not callable(connection_factory):
            raise BridgeInvalid()
        self._bindings = ScopeBindingStore(connection_factory)
        self._projects = ProjectStore(connection_factory)
        self._sources = SourceStore(connection_factory)

    def plan(self, principal: str, display_graph_id: str, source_revision: UUID,
             operation_id: UUID, ontology: OntologySpec) -> IngestionPlan:
        try:
            principal, display_graph_id = _identity(principal, display_graph_id)
            principal_id(principal)
            source_revision, operation_id = uuid_value(source_revision), uuid_value(operation_id)
            ontology = _ontology(ontology)
            binding = self._bindings.resolve(principal, display_graph_id)
            if not isinstance(binding, BindingRecord) or binding.principal != principal or binding.display_graph_id != display_graph_id:
                raise BridgeInvalid()
            scope = _scope(binding.scope)
            project = self._projects.get(principal, scope.project_id)
            if project.project_id != scope.project_id or project.workspace_id != scope.workspace_id or project.principal != principal:
                raise BridgeNotFound()
            retained = self._sources.get_source(principal, scope.project_id, source_revision)
            if retained.project_id != scope.project_id or retained.source_revision != source_revision:
                raise BridgeNotFound()
            if not retained.passages or retained.codepoint_length > 32768:
                raise BridgeInvalid()
            evidence_ids = tuple(item.evidence_id for item in retained.passages)
            source = SourceEnvelope(
                source_revision=retained.source_revision,
                source_sha256=retained.text_sha256,
                ontology_revision=ontology.revision,
                operation_id=operation_id,
                source_kind="document",
                content=retained.text,
                source_name=retained.name,
                recorded_at=retained.recorded_at,
                asserted_valid_at=None,
                evidence_ids=evidence_ids,
            )
            source = SourceEnvelope.model_validate(source.model_dump(warnings=False))
            fingerprint = request_fingerprint(scope, source, ontology)
            return IngestionPlan(principal, display_graph_id, scope, source, ontology,
                                 fingerprint, retained.byte_length,
                                 retained.codepoint_length)
        except BridgeError:
            raise
        except (ProjectNotFound, NotFound):
            raise BridgeNotFound() from None
        except Tombstoned:
            raise BridgeDenied() from None
        except (ProjectStorageError, StorageError, psycopg.Error):
            raise BridgeUnavailable() from None
        except (InvalidProject, AttributeError, TypeError, ValueError, Conflict):
            raise BridgeInvalid() from None

    async def dispatch(self, principal: str, display_graph_id: str,
                       source_revision: UUID, operation_id: UUID,
                       ontology: OntologySpec,
                       coordinator: KnowledgeIngestionCoordinator, *,
                       authorize_model_call: Callable[[IngestionPlan], bool] | None = None,
                       ) -> CompletionReceipt:
        """Freshly plan, require host budget authorization, then use the coordinator."""
        # psycopg reads are synchronous. Cancellation while they finish in the
        # worker prevents this coroutine from authorizing or dispatching.
        plan = await asyncio.to_thread(self.plan, principal, display_graph_id,
                                       source_revision, operation_id, ontology)
        if not isinstance(coordinator, KnowledgeIngestionCoordinator):
            raise BridgeInvalid()
        if authorize_model_call is None:
            raise BridgeDenied()
        try:
            authorized = authorize_model_call(plan)
        except Exception:
            raise BridgeDenied() from None
        if authorized is not True:
            raise BridgeDenied()
        try:
            receipt = await coordinator.ingest(plan.scope, plan.source, plan.ontology)
            validated = _receipt(receipt, plan.scope, plan.source.operation_id,
                                 plan.request_fingerprint)
            if validated.evidence_ids != plan.source.evidence_ids:
                raise BridgeUncertain()
            return validated
        except BridgeError:
            raise
        except IngestionUncertain:
            raise BridgeUncertain() from None
        except (Busy, Conflict, InvalidTransition):
            raise BridgeConflict() from None
        except (StorageError, psycopg.Error):
            raise BridgeUnavailable() from None
        except (AttributeError, TypeError, ValueError):
            raise BridgeUncertain() from None
