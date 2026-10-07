"""One bounded local dossier request; configuration comes from trusted ReadSettings."""
from __future__ import annotations

import asyncio
import json
import sys

from .evidence_dossier import DossierFailure, EvidenceDossierService, MAX_RESPONSE_BYTES
from .read_runtime import ReadSettings
from .report_contracts import DossierError, DossierRequest

MAX_REQUEST_BYTES = 32768


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def parse_request(raw):
    try:
        if type(raw) is not bytes or len(raw) > MAX_REQUEST_BYTES:
            raise ValueError
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        return DossierRequest.model_validate_json(json.dumps(data, ensure_ascii=True, allow_nan=False))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise DossierFailure("invalid_request") from None


def main(*, stdin=None, stdout=None, settings_factory=None):
    stdin = sys.stdin.buffer if stdin is None else stdin
    binary_output = stdout is None
    stdout = sys.stdout.buffer if binary_output else stdout
    try:
        request = parse_request(stdin.read(MAX_REQUEST_BYTES + 1))
        try:
            settings = (settings_factory or ReadSettings.from_environment)()
            service = EvidenceDossierService(settings.principal, settings.connection, settings.driver,
                                              trusted_scope=settings.scope)
        except Exception:
            raise DossierFailure("invalid_configuration") from None
        result = asyncio.run(service.build(request))
        payload = result.model_dump_json()
        if len(payload.encode("utf-8")) + 1 > MAX_RESPONSE_BYTES:
            raise DossierFailure("result_too_large")
        status = 0
    except DossierFailure as error:
        payload = DossierError(error=error.code).model_dump_json()
        status = 1
    except Exception:
        payload = DossierError(error="dossier_unavailable").model_dump_json()
        status = 1
    stdout.write((payload + "\n").encode("utf-8") if binary_output else payload + "\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
