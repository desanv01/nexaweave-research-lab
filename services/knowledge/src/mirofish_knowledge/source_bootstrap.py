"""Fixed installed PG-only sibling bootstrap: exactly one bounded frame."""
import sys
from pathlib import Path

from mirofish_knowledge.source_library import SourceSettings, SourceLibrary, MAX_BYTES, ENVELOPE_OVERHEAD
from mirofish_knowledge.stdio import _read_exact, PipeProtocolError


def serve_once(library, input_stream, output_stream):
    length = int.from_bytes(_read_exact(input_stream, 4), "big")
    if not 0 < length <= MAX_BYTES + ENVELOPE_OVERHEAD:
        raise PipeProtocolError
    raw = _read_exact(input_stream, length)
    if input_stream.read(1) != b"":
        raise PipeProtocolError
    body = library.dispatch(raw)
    if type(body) is not bytes or not 0 < len(body) <= MAX_BYTES + ENVELOPE_OVERHEAD:
        raise PipeProtocolError
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
        if directory.name != "mirofish_knowledge" or "site-packages" not in directory.parts:
            raise ValueError
        for name in ("mirofish_knowledge", "mirofish_knowledge.source_library", "mirofish_knowledge.stdio",
                     "mirofish_knowledge.bindings", "mirofish_knowledge.contracts", "mirofish_knowledge.operations"):
            if Path(sys.modules[name].__file__).resolve().parent != directory:
                raise ValueError
        storage = directory.parent / "mirofish_storage"
        for name in ("mirofish_storage", "mirofish_storage.source", "mirofish_storage.store", "mirofish_storage.validation"):
            if Path(sys.modules[name].__file__).resolve().parent != storage:
                raise ValueError
        if any(name in sys.modules for name in ("mirofish_knowledge.provider", "mirofish_knowledge.read_runtime",
                                                "graphiti_core", "neo4j")):
            raise ValueError
        serve_once(SourceLibrary(SourceSettings.from_environment()), sys.stdin.buffer, sys.stdout.buffer)
        return 0
    except Exception:
        print("knowledge source unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
