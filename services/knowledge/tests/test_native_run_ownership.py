"""Pure native-run wire and policy contracts; no database is opened."""
from __future__ import annotations

from dataclasses import replace
import subprocess
import sys
from uuid import uuid4

import pytest

from nexaweave_execution.native_run_contracts import (InvalidNativeRun, NativeChildIdentity,
    NativeRunReceipt, NativeRunRequest)
from nexaweave_execution.native_run_coordinator import NativeObservation


def request(**changes):
    value = dict(schema_version=1, principal="owner", project_id=uuid4(),
                 project_revision=1, simulation_id="sim_1", run_id=uuid4(),
                 artifact_sha256="a" * 64, runtime_sha256="b" * 64,
                 platforms=("twitter", "reddit"), seed=42, max_rounds=24)
    value.update(changes)
    return NativeRunRequest.from_wire(value)


def test_canonical_request_and_bounds():
    value = request()
    assert NativeRunRequest.from_wire(value.to_wire()) == value
    assert len(value.fingerprint) == 64
    assert NativeRunRequest.from_wire(dict(reversed(list(value.to_wire().items())))).fingerprint == value.fingerprint
    assert replace(value, max_rounds=1).fingerprint != value.fingerprint
    assert replace(value, project_revision=2).fingerprint != value.fingerprint


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"principal": "owner\nsecret"},
    {"project_revision": 0}, {"project_revision": True},
    {"simulation_id": "../escape"}, {"artifact_sha256": "A" * 64},
    {"runtime_sha256": "x" * 64}, {"platforms": ("reddit", "twitter")},
    {"platforms": ("twitter", "twitter")}, {"seed": True},
    {"max_rounds": 0}, {"max_rounds": 25}, {"max_rounds": True},
])
def test_request_rejects_malformed_fields(change):
    with pytest.raises(InvalidNativeRun, match="^invalid_native_run$"):
        request(**change)


def test_unknown_wire_key_and_receipt_shape_rejected():
    value = request()
    wire = value.to_wire()
    wire["command"] = "not permitted"
    with pytest.raises(InvalidNativeRun):
        NativeRunRequest.from_wire(wire)
    child = NativeChildIdentity.from_wire(dict(instance_id=uuid4(), process_id=123,
                                               process_fingerprint="c" * 64))
    receipt = NativeRunReceipt.from_wire(dict(run_id=value.run_id, attempt_id=uuid4(),
        instance_id=child.instance_id, request_fingerprint=value.fingerprint,
        outcome="completed", evidence_sha256="d" * 64))
    assert NativeRunReceipt.from_wire(receipt.to_wire()) == receipt
    with pytest.raises(InvalidNativeRun):
        NativeRunReceipt.from_wire({**receipt.to_wire(), "private": "data"})
    with pytest.raises(InvalidNativeRun):
        NativeObservation.validated(NativeObservation("unknown", receipt))
    with pytest.raises(InvalidNativeRun):
        NativeObservation.validated(NativeObservation("failed", receipt))


def test_plain_contract_import_does_not_load_runtime_dependencies():
    code = ("import sys; import nexaweave_execution.native_run_contracts; "
            "forbidden = {'psycopg', 'temporalio', 'graphiti_core', 'oasis', 'camel'}; "
            "assert not (forbidden & set(sys.modules))")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                            text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
