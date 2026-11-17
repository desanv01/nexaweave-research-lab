"""Trusted, revision-checked persistence for a source project's reviewed ontology."""

from collections.abc import Callable

from nexaweave_knowledge.bindings import ScopeBindingStore
from nexaweave_knowledge.contracts import KnowledgeScope, Layer
from nexaweave_knowledge.operations import (
    Conflict as BindingConflict,
    NotFound as BindingNotFound,
    StorageError as BindingStorageError,
    Tombstoned,
)
from nexaweave_storage.store import Conflict, NotFound, ProjectRecord, ProjectStore, StorageError
from nexaweave_storage.validation import InvalidProject, display_id, principal_id

from ..utils.ontology import validate_reviewed_ontology


class ReviewedOntologyHost:
    """Save metadata through the existing project revision compare-and-swap."""

    def __init__(
        self, *, principal: str, display_graph_id: str, scope: KnowledgeScope,
        connection_factory: Callable,
    ):
        if not callable(connection_factory) or type(scope) is not KnowledgeScope:
            raise InvalidProject()
        self._principal = principal_id(principal)
        self._display_graph_id = display_id(display_graph_id)
        if type(scope.schema_version) is not int:
            raise InvalidProject()
        try:
            scope = KnowledgeScope.model_validate(scope.model_dump(warnings=False))
        except (ValueError, TypeError, AttributeError):
            raise InvalidProject() from None
        if scope.layer != Layer.source or scope.run_id is not None or scope.branch_id is not None:
            raise NotFound()
        self._scope = scope
        self._bindings = ScopeBindingStore(connection_factory)
        self._projects = ProjectStore(connection_factory)

    def _authorize_binding(self, resolve):
        try:
            binding = resolve(self._principal, self._display_graph_id)
        except (BindingNotFound, Tombstoned):
            raise NotFound() from None
        except BindingConflict:
            raise Conflict() from None
        except BindingStorageError:
            raise StorageError() from None
        if (binding.principal != self._principal
                or binding.display_graph_id != self._display_graph_id
                or binding.scope != self._scope):
            raise NotFound()

    def _authorize_revision_write(self, conn):
        self._authorize_binding(lambda principal, graph: self._bindings.resolve_for_write(
            conn, principal, graph, self._scope,
        ))

    def save(self, *, expected_revision: int, ontology: object) -> ProjectRecord:
        """Persist only the ontology and return the new canonical revision."""
        if type(expected_revision) is not int or not 1 <= expected_revision <= 2_147_483_646:
            raise InvalidProject()
        self._authorize_binding(self._bindings.resolve)
        current = self._projects.get(self._principal, self._scope.project_id)
        if (current.principal != self._principal
                or current.project_id != self._scope.project_id
                or current.workspace_id != self._scope.workspace_id):
            raise NotFound()
        if current.revision != expected_revision:
            raise Conflict()

        snapshot = current.snapshot
        if snapshot["graph_id"] is not None and snapshot["graph_id"] != self._display_graph_id:
            raise NotFound()
        snapshot["ontology"] = validate_reviewed_ontology(ontology)
        # ProjectStore rechecks the same revision while holding the project row
        # lock, so another successful save cannot be overwritten after get().
        return self._projects.update(
            self._principal, self._scope.project_id, expected_revision, snapshot, current.evidence,
            authorize_revision_write=self._authorize_revision_write,
        )
