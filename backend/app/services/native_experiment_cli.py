"""One bounded local request, actual PostgreSQL authority, no app bootstrap."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys

# Saved-script bootstrap to the fixed repository storage package, never wire paths.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "knowledge" / "src"))
    from native_experiment_contracts import (ExperimentError, MAX_REQUEST, canonical,
        cohort_from_manifest, read_manifest)
    from native_experiments import NativeExperimentComparator
else:
    from .native_experiment_contracts import (ExperimentError, MAX_REQUEST, canonical,
        cohort_from_manifest, read_manifest)
    from .native_experiments import NativeExperimentComparator


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ExperimentError("invalid_binding")


def _appstore_dsn():
    # Saved-script entry: use the fixed shared resolver without Flask startup.
    source = Path(__file__).resolve().parents[1] / "utils" / "branding.py"
    spec = importlib.util.spec_from_file_location("_nexaweave_cli_branding", source)
    if spec is None or spec.loader is None:
        raise ExperimentError("authority_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.appstore_dsn()


def main(argv=None):
    try:
        parser = _Parser(description="Compare existing owned native runs", add_help=False)
        parser.add_argument("--principal", required=True)
        parser.add_argument("--manifest", required=True)
        parser.add_argument("--manifest-sha256", required=True)
        args = parser.parse_args(argv)
        cohort = cohort_from_manifest(read_manifest(args.manifest, args.manifest_sha256), args.principal)
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if not raw or len(raw) > MAX_REQUEST:
            raise ExperimentError("invalid_request")
        dsn = _appstore_dsn()
        if not dsn:
            raise ExperimentError("authority_unavailable")
        import psycopg
        def connect():
            return psycopg.connect(dsn, connect_timeout=3)
        result = NativeExperimentComparator(cohort=cohort, connection_factory=connect).compare(raw)
        reply, status = {"ok": True, "result": result}, 0
    except ExperimentError as error:
        reply, status = {"ok": False, "error": error.code}, 2
    except Exception:
        reply, status = {"ok": False, "error": "authority_unavailable"}, 2
    sys.stdout.buffer.write(canonical(reply) + b"\n")
    sys.stdout.buffer.flush()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
