"""Focused tests of the trusted save boundary using in-memory store doubles."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from unittest.mock import ANY, Mock
from uuid import uuid4

import pytest

from app.services.reviewed_ontology_host import ReviewedOntologyHost
from nexaweave_knowledge.bindings import BindingRecord, ScopeBindingStore
from nexaweave_knowledge.contracts import KnowledgeScope, Layer
from nexaweave_knowledge.operations import NotFound as BindingNotFound, Tombstoned
from nexaweave_storage.store import Conflict, NotFound, ProjectRecord, ProjectStore
from nexaweave_storage.validation import canonical_payload


def _record(scope, revision, snapshot, evidence):
    checked, evidence, digest = canonical_payload(snapshot, evidence, "proj_one")
    return ProjectRecord(
        "owner", scope.workspace_id, scope.project_id, "proj_one", revision,
        json.dumps(checked), json.dumps(evidence), digest, datetime.now(timezone.utc),
    )


@pytest.fixture
def reviewed_host():
    scope = KnowledgeScope(
        workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(), layer=Layer.source,
    )
    connection = Mock(side_effect=AssertionError("unexpected database connection"))
    host = ReviewedOntologyHost(
        principal="owner", display_graph_id="graph_one", scope=scope,
        connection_factory=connection,
    )
    host._bindings = Mock(spec=ScopeBindingStore)
    host._bindings.resolve.return_value = BindingRecord(
        "owner", "graph_one", scope, datetime.now(timezone.utc),
    )
    host._bindings.resolve_for_write.return_value = host._bindings.resolve.return_value
    host._projects = Mock(spec=ProjectStore)
    original = _record(scope, 1, {
        "project_id": "proj_one", "name": "Project", "status": "ontology_generated",
        "created_at": "2026-11-17T00:00:00", "updated_at": "2026-11-17T00:01:00",
        "files": [{"filename": "source.txt", "size": 3}], "total_text_length": 3,
        "ontology": {"entity_types": [], "edge_types": []}, "analysis_summary": "Keep this",
        "graph_id": "graph_one", "graph_build_task_id": None, "zep_batch_id": None,
        "zep_batch_operation_id": None, "simulation_requirement": "Keep that too",
        "chunk_size": 500, "chunk_overlap": 50, "error": None,
    }, [{
        "evidence_id": str(uuid4()), "source_revision": str(uuid4()),
        "sha256": "a" * 64, "object_key": "objects/source", "byte_length": 3,
    }])
    host._projects.get.return_value = original
    return host, scope, original


def _ontology():
    return {"entity_types": [{
        "name": "Speaker", "description": "Public speaker",
        "attributes": [{"name": "role", "type": "text", "description": "Public role"}],
    }], "edge_types": []}


def test_reviewed_save_copies_ontology_through_the_owned_revision_update(reviewed_host):
    host, scope, original = reviewed_host
    ontology = _ontology()
    before = deepcopy(ontology)
    proposed = {**original.snapshot, "ontology": before}
    saved = _record(scope, 2, proposed, original.evidence)
    conn = object()

    def update(*args, authorize_revision_write):
        authorize_revision_write(conn)
        return saved

    host._projects.update.side_effect = update

    assert host.save(expected_revision=1, ontology=ontology) is saved
    host._bindings.resolve.assert_called_once_with("owner", "graph_one")
    host._projects.get.assert_called_once_with("owner", scope.project_id)
    host._projects.update.assert_called_once_with(
        "owner", scope.project_id, 1, proposed, original.evidence, authorize_revision_write=ANY,
    )
    host._bindings.resolve_for_write.assert_called_once_with(conn, "owner", "graph_one", scope)
    ontology["entity_types"][0]["attributes"][0]["description"] = "Changed by caller"
    assert host._projects.update.call_args.args[3]["ontology"] == before
    assert original.snapshot["ontology"] == {"entity_types": [], "edge_types": []}
    assert saved.evidence == original.evidence


def test_reviewed_save_refuses_stale_and_losing_revision_updates(reviewed_host):
    host, scope, original = reviewed_host
    current = _record(scope, 2, original.snapshot, original.evidence)
    host._projects.get.return_value = current

    with pytest.raises(Conflict):
        host.save(expected_revision=1, ontology=_ontology())
    host._projects.update.assert_not_called()

    host._projects.update.side_effect = Conflict()
    with pytest.raises(Conflict):
        host.save(expected_revision=2, ontology=_ontology())
    host._projects.update.assert_called_once()

    host._bindings.resolve_for_write.side_effect = Tombstoned("tombstoned")
    host._projects.update.side_effect = lambda *args, authorize_revision_write: (
        authorize_revision_write(object())
    )
    with pytest.raises(NotFound):
        host.save(expected_revision=2, ontology=_ontology())


@pytest.mark.parametrize("missing", [True, False])
def test_reviewed_save_refuses_missing_or_wrong_graph_binding(reviewed_host, missing):
    host, scope, _ = reviewed_host
    if missing:
        host._bindings.resolve.side_effect = BindingNotFound("missing")
    else:
        host._bindings.resolve.return_value = BindingRecord(
            "owner", "graph_one", scope.model_copy(update={"graph_id": uuid4()}),
            datetime.now(timezone.utc),
        )

    with pytest.raises(NotFound):
        host.save(expected_revision=1, ontology=_ontology())
    host._projects.get.assert_not_called()
    host._projects.update.assert_not_called()
