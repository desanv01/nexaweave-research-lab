"""Fixed installed one-frame bootstrap. No Graphiti/model imports on cold paths."""
import sys
from pathlib import Path


def serve_once(host, input_stream, output_stream):
    from mirofish_knowledge.stdio import _read_exact, PipeProtocolError
    from mirofish_knowledge.source_ingestion_host import MAX_BYTES, ENVELOPE_OVERHEAD
    length = int.from_bytes(_read_exact(input_stream, 4), "big")
    if not 0 < length <= MAX_BYTES + ENVELOPE_OVERHEAD:
        raise PipeProtocolError
    raw = _read_exact(input_stream, length)
    if input_stream.read(1) != b"":
        raise PipeProtocolError
    body = host.dispatch(raw)
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
        from mirofish_knowledge.source_ingestion_host import SourceIngestionHost, IngestionSettings
        directory = Path(__file__).resolve().parent
        if directory.name != "mirofish_knowledge" or "site-packages" not in directory.parts:
            raise ValueError
        for name, module in tuple(sys.modules.items()):
            for package in ("mirofish_knowledge", "mirofish_storage", "mirofish_execution"):
                if name == package or name.startswith(package + "."):
                    file = getattr(module, "__file__", None)
                    expected = directory.parent / package
                    if file is None or not Path(file).resolve().is_relative_to(expected):
                        raise ValueError
        if any(name in sys.modules for name in ("mirofish_knowledge.provider", "graphiti_core", "neo4j", "camel", "openai")):
            raise ValueError
        serve_once(SourceIngestionHost(IngestionSettings.from_environment()), sys.stdin.buffer, sys.stdout.buffer)
        return 0
    except Exception:
        print("knowledge ingestion unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
