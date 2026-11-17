"""Reviewed ontology validation and revision authority for the installed source child."""

from collections.abc import Callable
from copy import deepcopy
import re
from typing import Any, Dict

from nexaweave_storage.store import Conflict, NotFound, ProjectRecord, ProjectStore, StorageError
from nexaweave_storage.validation import InvalidProject, display_id, principal_id

from .bindings import ScopeBindingStore
from .contracts import KnowledgeScope, Layer
from .operations import (
    Conflict as BindingConflict,
    NotFound as BindingNotFound,
    StorageError as BindingStorageError,
    Tombstoned,
)


MAX_ONTOLOGY_TYPES = 10
MAX_ONTOLOGY_ATTRIBUTES = 10
MAX_ONTOLOGY_SOURCE_TARGETS = 10
RESERVED_ONTOLOGY_ATTRIBUTE_NAMES = frozenset({
    "uuid",
    "name",
    "group_id",
    "graph_id",
    "name_embedding",
    "summary",
    "created_at",
})

_ENTITY_NAME = re.compile(r"[A-Z][A-Za-z0-9]*")
_EDGE_NAME = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*")
_ATTRIBUTE_NAME = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
_TYPE_FIELDS = {"name", "description", "attributes"}
_ATTRIBUTE_FIELDS = {"name", "type", "description"}


def _reviewed_text(value: Any) -> bool:
    if type(value) is not str or not value.strip() or "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _validate_reviewed_attributes(attributes: Any, path: str) -> None:
    if type(attributes) is not list or not 1 <= len(attributes) <= MAX_ONTOLOGY_ATTRIBUTES:
        raise ValueError(f"{path} must contain 1 to {MAX_ONTOLOGY_ATTRIBUTES} attributes")

    names: set[str] = set()
    for index, attribute in enumerate(attributes):
        field = f"{path}[{index}]"
        if type(attribute) is not dict or attribute.keys() != _ATTRIBUTE_FIELDS:
            raise ValueError(f"{field} must contain name, type and description")
        name = attribute["name"]
        if (type(name) is not str or not _ATTRIBUTE_NAME.fullmatch(name)
                or name in RESERVED_ONTOLOGY_ATTRIBUTE_NAMES):
            raise ValueError(f"{field}.name must be a non-reserved snake_case name")
        if name in names:
            raise ValueError(f"{field}.name is a duplicate attribute name")
        if type(attribute["type"]) is not str or attribute["type"] != "text":
            raise ValueError(f"{field}.type must be text")
        if not _reviewed_text(attribute["description"]):
            raise ValueError(f"{field}.description must be nonempty text")
        names.add(name)


def _validate_reviewed_types(
    entries: Any, path: str, name_pattern: re.Pattern[str],
    required: set[str], optional: set[str],
) -> set[str]:
    if type(entries) is not list or len(entries) > MAX_ONTOLOGY_TYPES:
        raise ValueError(f"{path} must be a list of at most {MAX_ONTOLOGY_TYPES} types")

    names: set[str] = set()
    for index, entry in enumerate(entries):
        field = f"{path}[{index}]"
        if (type(entry) is not dict or not required <= entry.keys()
                or entry.keys() - required - optional):
            raise ValueError(f"{field} has invalid type fields")
        name = entry["name"]
        if type(name) is not str or not name_pattern.fullmatch(name):
            raise ValueError(f"{field}.name is not a canonical type name")
        if name in names:
            raise ValueError(f"{field}.name is a duplicate type name")
        if not _reviewed_text(entry["description"]) or len(entry["description"]) > 100:
            raise ValueError(f"{field}.description must be nonempty text of at most 100 characters")
        _validate_reviewed_attributes(entry["attributes"], f"{field}.attributes")
        if "examples" in entry:
            examples = entry["examples"]
            if type(examples) is not list or not all(_reviewed_text(example) for example in examples):
                raise ValueError(f"{field}.examples must be a list of nonempty text values")
        names.add(name)
    return names


def validate_reviewed_ontology(ontology: Any) -> Dict[str, Any]:
    """Return a detached copy of a reviewed ontology or raise ``ValueError``."""
    if type(ontology) is not dict or ontology.keys() != {"entity_types", "edge_types"}:
        raise ValueError("ontology must contain entity_types and edge_types")
    entity_names = _validate_reviewed_types(
        ontology["entity_types"], "entity_types", _ENTITY_NAME, _TYPE_FIELDS, {"examples"},
    )
    _validate_reviewed_types(
        ontology["edge_types"], "edge_types", _EDGE_NAME, _TYPE_FIELDS | {"source_targets"}, set(),
    )
    allowed_endpoints = entity_names | {"Entity"}
    for index, edge in enumerate(ontology["edge_types"]):
        path = f"edge_types[{index}].source_targets"
        targets = edge["source_targets"]
        if type(targets) is not list or not 1 <= len(targets) <= MAX_ONTOLOGY_SOURCE_TARGETS:
            raise ValueError(f"{path} must contain 1 to {MAX_ONTOLOGY_SOURCE_TARGETS} endpoint pairs")
        seen: set[tuple[str, str]] = set()
        for target_index, endpoints in enumerate(targets):
            field = f"{path}[{target_index}]"
            if type(endpoints) is not dict or endpoints.keys() != {"source", "target"}:
                raise ValueError(f"{field} must contain source and target")
            source, target = endpoints["source"], endpoints["target"]
            if type(source) is not str or type(target) is not str:
                raise ValueError(f"{field} endpoints must be strings")
            if source not in allowed_endpoints or target not in allowed_endpoints:
                raise ValueError(f"{field} references an unknown entity type")
            pair = (source, target)
            if pair in seen:
                raise ValueError(f"{field} is a duplicate endpoint pair")
            seen.add(pair)
    return deepcopy(ontology)


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

    def _current_owned_project(self):
        self._authorize_binding(self._bindings.resolve)
        current = self._projects.get(self._principal, self._scope.project_id)
        if (current.principal != self._principal
                or current.project_id != self._scope.project_id
                or current.workspace_id != self._scope.workspace_id):
            raise NotFound()
        snapshot = current.snapshot
        if snapshot["graph_id"] is not None and snapshot["graph_id"] != self._display_graph_id:
            raise NotFound()
        return current, snapshot

    def load(self) -> ProjectRecord:
        """Read the current canonical value, including unreviewed legacy values."""
        current, _ = self._current_owned_project()
        self._authorize_binding(self._bindings.resolve)
        return current

    def save(self, *, expected_revision: int, ontology: object) -> ProjectRecord:
        """Persist only the ontology and return the new canonical revision."""
        if type(expected_revision) is not int or not 1 <= expected_revision <= 2_147_483_646:
            raise InvalidProject()
        current, snapshot = self._current_owned_project()
        if current.revision != expected_revision:
            raise Conflict()
        snapshot["ontology"] = validate_reviewed_ontology(ontology)
        # ProjectStore rechecks the same revision while holding the project row
        # lock, so another successful save cannot be overwritten after get().
        return self._projects.update(
            self._principal, self._scope.project_id, expected_revision, snapshot, current.evidence,
            authorize_revision_write=self._authorize_revision_write,
        )


class ReviewedOntologySource:
    """One ontology command over the fixed installed source frame."""

    def __init__(self, settings):
        self.settings = settings

    def dispatch(self, raw):
        from .source_library import (
            ENVELOPE_OVERHEAD, ERROR_CODES, MAX_BYTES, SourceError, encoded, scope_value, uuid_value,
        )
        from .stdio import _object

        request_id = None
        try:
            if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES + ENVELOPE_OVERHEAD:
                raise ValueError
            value = _object(raw)
            if (set(value) != {"version", "request_id", "method", "scope", "payload"}
                    or type(value["version"]) is not int or value["version"] != 1):
                raise ValueError
            request_id = str(uuid_value(value["request_id"]))
            settings = self.settings
            if scope_value(value["scope"]) != settings.scope:
                raise SourceError("source_denied")

            method, payload = value["method"], value["payload"]
            if type(payload) is not dict or len(encoded(payload)) > MAX_BYTES:
                raise ValueError
            if method == "ontology_load":
                if payload:
                    raise ValueError
            elif method == "ontology_save":
                if (set(payload) != {"schema_version", "expected_revision", "ontology"}
                        or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
                        or type(payload["expected_revision"]) is not int
                        or not 1 <= payload["expected_revision"] <= 2_147_483_646
                        or type(payload["ontology"]) is not dict):
                    raise ValueError
            else:
                raise ValueError

            host = ReviewedOntologyHost(
                principal=settings.principal, display_graph_id=settings.display_graph_id,
                scope=settings.scope, connection_factory=settings.connect,
            )
            if method == "ontology_load":
                record = host.load()
            else:
                record = host.save(expected_revision=payload["expected_revision"],
                                   ontology=payload["ontology"])
            result = {"schema_version": 1, "scope": settings.scope.model_dump(mode="json"),
                      "project_revision": record.revision, "ontology": record.snapshot["ontology"]}
            if len(encoded(result)) > MAX_BYTES:
                raise SourceError("result_too_large")
            return encoded({"version": 1, "request_id": request_id, "ok": True, "result": result})
        except SourceError as error:
            code = error.code if error.code in ERROR_CODES else "source_unavailable"
        except NotFound:
            code = "not_found"
        except Conflict:
            code = "conflict"
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            code = "invalid_request"
        except Exception:
            code = "source_unavailable"
        return encoded({"version": 1, "request_id": request_id, "ok": False, "error": {"code": code}})
