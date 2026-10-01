"""Saved source bundle CLI. No revision fallback or publication operation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Import only the fixed sibling standard-library tool, never application code.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from release.source_bundle import BundleError, build_bundle, verify_bundle


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise BundleError("arguments_invalid")


def main(argv=None) -> int:
    try:
        parser = SafeParser(description=__doc__, allow_abbrev=False, add_help=False)
        commands = parser.add_subparsers(dest="operation", required=True, parser_class=SafeParser)
        build = commands.add_parser("build", allow_abbrev=False, add_help=False)
        build.add_argument("--repository", required=True)
        build.add_argument("--revision", required=True)
        build.add_argument("--output", required=True)
        verify = commands.add_parser("verify", allow_abbrev=False, add_help=False)
        verify.add_argument("--artifact", required=True)
        verify.add_argument("--expected-sha256", required=True)
        verify.add_argument("--revision", required=True)
        args = parser.parse_args(argv)
        if args.operation == "build":
            result = build_bundle(args.repository, args.revision, args.output)
        else:
            result = verify_bundle(args.artifact, args.expected_sha256, args.revision)
        print(json.dumps({"ok": True, **result}, sort_keys=True, separators=(",", ":")))
        return 0
    except BundleError as error:
        code = str(error)
    except Exception:
        code = "operation_failed"
    print(json.dumps({"ok": False, "error": code, "qualified_release": False,
                      "all44_accepted": False}, sort_keys=True, separators=(",", ":")))
    return 2


if __name__ == "__main__":
    sys.exit(main())
