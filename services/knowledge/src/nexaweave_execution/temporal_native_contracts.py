"""Pure identifier-only native Temporal identity and receipt validation."""
from __future__ import annotations

from .native_run_contracts import (InvalidNativeRun, NativeRunReceipt,
                                   NativeRunRequest)


def native_workflow_id(value: NativeRunRequest) -> str:
    request = NativeRunRequest.from_wire(value)
    return "mf-native-v1-" + request.run_id.hex + "-" + request.fingerprint


def qualified_receipt(value: object, request: NativeRunRequest) -> NativeRunReceipt:
    request = NativeRunRequest.from_wire(request)
    receipt = NativeRunReceipt.from_wire(value)
    if (receipt.run_id != request.run_id
            or receipt.request_fingerprint != request.fingerprint):
        raise InvalidNativeRun()
    return receipt
