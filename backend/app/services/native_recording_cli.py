"""Trusted argv bootstrap; bounded JSON requests never select a filesystem path.

Launch this saved script directly to avoid Flask/app/engine initialization.
"""
from __future__ import annotations

import argparse
import json
import sys

try:
    from .native_recording_contracts import MAX_WIRE, RecordingAnchors, RecordingError
    from .native_recordings import NativeRecording
except ImportError:
    from native_recording_contracts import MAX_WIRE, RecordingAnchors, RecordingError
    from native_recordings import NativeRecording


def serve(recording, source, output):
    """One bounded request per line; stop on oversized frames without draining."""
    for _ in range(1000):
        raw = source.readline(MAX_WIRE + 2)
        if not raw:
            return 0
        if len(raw) > MAX_WIRE or not raw.endswith(b"\n"):
            error = {"ok": False, "error": "invalid_request",
                     "recording_revision": recording._revision,
                     "anchors": recording._anchors.wire(), "recording_admitted": True,
                     "continuation_supported": False, "branch_execution_supported": False}
            output.write(json.dumps(error, separators=(",", ":")).encode("ascii") + b"\n")
            output.flush()
            return 2
        try:
            result = {"ok": True, "result": recording.read(raw)}
        except RecordingError as error:
            result = {"ok": False, "error": error.code,
                      "recording_revision": recording._revision,
                      "anchors": recording._anchors.wire(),
                      "recording_admitted": True,
                      "continuation_supported": False, "branch_execution_supported": False}
        output.write(json.dumps(result, ensure_ascii=True, allow_nan=False,
                                separators=(",", ":")).encode("ascii") + b"\n")
        output.flush()
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read an owned immutable native recording")
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--revision", required=True)
    for field in ("graph-id", "simulation-id", "run-id", "branch-id", "project-id"):
        parser.add_argument("--" + field, required=True)
    parser.add_argument("--project-revision", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        anchors = RecordingAnchors(args.graph_id, args.simulation_id, args.run_id,
                                   args.branch_id, args.project_id, args.project_revision)
        recording = NativeRecording(args.bundle, anchors=anchors, expected_revision=args.revision)
        return serve(recording, sys.stdin.buffer, sys.stdout.buffer)
    except RecordingError as error:
        result = {"ok": False, "error": error.code, "recording_admitted": False,
                  "continuation_supported": False, "branch_execution_supported": False}
        # Only validated trusted bootstrap provenance can appear on a failed
        # admission. Never echo raw paths or malformed argv identifiers.
        if "anchors" in locals():
            result["anchors"] = anchors.wire()
        if len(args.revision) == 64 and all(c in "0123456789abcdef" for c in args.revision):
            result["recording_revision"] = args.revision
        sys.stdout.buffer.write(json.dumps(result,
                                          separators=(",", ":")).encode("ascii") + b"\n")
        sys.stdout.buffer.flush()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
