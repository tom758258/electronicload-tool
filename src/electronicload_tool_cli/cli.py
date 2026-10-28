"""Command-line adapter for Core read-only operations."""

import argparse
import json
import sys
from importlib.metadata import version

from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.identity import MODEL_ID
from electronicload_tool_core.readonly import COMMANDS, execute


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if self.json_errors:
            raise LoadError("argument_error", message)
        super().error(message)


def _emit_error(error: LoadError, command: str | None, mode: str, json_output: bool) -> int:
    if json_output:
        print(json.dumps({"ok": False, "command": command, "mode": mode, "error": error.as_dict()}))
    else:
        print(f"{error.code}: {error}", file=sys.stderr)
        if error.details:
            print(json.dumps(error.details), file=sys.stderr)
    return error.exit_code


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_output = "--json" in argv
    mode = "dry-run" if "--dry-run" in argv else "simulate" if "--simulate" in argv else "live"
    command = argv[0] if argv and argv[0] in COMMANDS else None
    parser = _Parser(
        prog="electronicload-tool",
        description="Electronic Load Tool. Read-only queries, plans, and simulation.",
        allow_abbrev=False,
    )
    parser.json_errors = json_output
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {version('electronicload-tool')}",
    )
    subparsers = parser.add_subparsers(dest="command")
    for name in COMMANDS:
        sub = subparsers.add_parser(name, allow_abbrev=False)
        sub.json_errors = json_output
        modes = sub.add_mutually_exclusive_group()
        modes.add_argument("--dry-run", action="store_true", help="Print a plan without hardware I/O.")
        modes.add_argument("--simulate", action="store_true", help="Use deterministic simulated data.")
        sub.add_argument("--resource", help="Explicit USB INSTR resource; required for LIVE.")
        sub.add_argument("--backend", default="system", help="VISA backend (system only).")
        sub.add_argument("--model", default=MODEL_ID, help="Registered profile or expected live model.")
        sub.add_argument("--json", action="store_true", help="Emit one JSON result or error object.")
        sub.add_argument(
            "--validation-allow-pending-live-support", action="store_true", help=argparse.SUPPRESS
        )
    try:
        if json_output and any(flag in argv for flag in ("--help", "-h", "--version")):
            raise LoadError("argument_error", "Request help or version separately from --json.")
        args = parser.parse_args(argv)
        if args.command is None:
            parser.print_help()
            return 0
        result = execute(
            args.command, mode=mode, resource=args.resource, backend=args.backend,
            model_id=args.model, validation=args.validation_allow_pending_live_support,
        )
    except LoadError as exc:
        return _emit_error(exc, command, mode, json_output)
    except KeyboardInterrupt:
        return _emit_error(LoadError("interrupted", "Request interrupted.", 3), command, mode, json_output)
    if args.json:
        print(json.dumps({
            "ok": True, "command": args.command, "mode": mode,
            "model_id": args.model, "result": result,
        }, allow_nan=False))
    else:
        print(f"{args.command} ({mode})")
        if args.command == "measure" and mode != "dry-run":
            print(f"Voltage: {result['voltage_v']:g} V")
            print(f"Current: {result['current_a']:g} A")
            print(f"Power: {result['power_w']:g} W")
        else:
            for key, value in result.items():
                print(f"{key}: {json.dumps(value) if isinstance(value, (list, dict)) else value}")
    return 0
