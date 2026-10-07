"""Installed sibling bootstrap, exactly one bounded evidence frame."""
import asyncio
import sys
from pathlib import Path

from nexaweave_knowledge.read_runtime import ReadSettings
from nexaweave_knowledge.evidence_dispatcher import EvidenceCommandDispatcher, ENVELOPE_OVERHEAD
from nexaweave_knowledge.stdio import _read_exact, _object, PipeProtocolError


async def serve_once(dispatcher, *, principal, input_stream, output_stream):
    try:
        length = int.from_bytes(_read_exact(input_stream, 4), "big")
        if not 0 < length <= 32768 + ENVELOPE_OVERHEAD:
            raise PipeProtocolError
        raw = _read_exact(input_stream, length)
        if input_stream.read(1) != b"":
            raise PipeProtocolError
        _object(raw)
    except PipeProtocolError:
        body = dispatcher._error(None, "invalid_request")
    else:
        body = await dispatcher.dispatch(raw, principal=principal)
    if type(body) is not bytes or not 0 < len(body) <= 4 * 1024 * 1024 + ENVELOPE_OVERHEAD:
        raise PipeProtocolError
    # Length checks precede frame allocation; no clipped success.
    view = memoryview(len(body).to_bytes(4, "big") + body)
    while view:
        written = output_stream.write(view)
        if type(written) is not int or written <= 0:
            raise PipeProtocolError
        view = view[written:]
    output_stream.flush()


def main():
    try:
        directory = Path(__file__).resolve().parent
        if directory.name != "nexaweave_knowledge" or "site-packages" not in directory.parts:
            raise ValueError
        # Reject an installed-looking launch redirected to source/editable modules.
        for name in ("nexaweave_knowledge", "nexaweave_knowledge.read_runtime",
                     "nexaweave_knowledge.evidence_dispatcher"):
            if Path(sys.modules[name].__file__).resolve().parent != directory:
                raise ValueError
        settings = ReadSettings.from_environment()
        asyncio.run(serve_once(EvidenceCommandDispatcher(settings), principal=settings.principal,
            input_stream=sys.stdin.buffer, output_stream=sys.stdout.buffer))
        return 0
    except Exception:
        print("knowledge evidence unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
