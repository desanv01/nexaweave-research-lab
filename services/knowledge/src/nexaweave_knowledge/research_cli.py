"""One local JSON research operation; trusted configuration is read only in main."""
from __future__ import annotations

import asyncio
import json
import sys

from .evidence_research import EvidenceResearchService, ResearchFailure, MAX_RESPONSE_BYTES
from .read_runtime import ReadSettings
from .research_contracts import ResearchError, ResearchRequest

MAX_REQUEST_BYTES = 16384


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
        return ResearchRequest.model_validate_json(json.dumps(data, ensure_ascii=True, allow_nan=False))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ResearchFailure("invalid_request") from None


def main(*, stdin=None, stdout=None, settings_factory=None):
    stdin = sys.stdin.buffer if stdin is None else stdin
    binary_output = stdout is None
    stdout = sys.stdout.buffer if binary_output else stdout
    try:
        request = parse_request(stdin.read(MAX_REQUEST_BYTES + 1))
        try:
            settings = (settings_factory or ReadSettings.from_environment)()
        except Exception:
            raise ResearchFailure("invalid_configuration") from None
        service = EvidenceResearchService(settings.principal, settings.connection, settings.driver,
                                          trusted_scope=settings.scope)
        result = asyncio.run(service.research(request))
        payload = result.model_dump_json()
        if len(payload.encode("utf-8")) + 1 > MAX_RESPONSE_BYTES:
            raise ResearchFailure("result_too_large")
        status = 0
    except ResearchFailure as error:
        payload = ResearchError(error=error.code).model_dump_json()
        status = 1
    except Exception:
        payload = ResearchError(error="research_unavailable").model_dump_json()
        status = 1
    stdout.write((payload + "\n").encode("utf-8") if binary_output else payload + "\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
