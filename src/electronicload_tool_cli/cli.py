"""Standalone Core adapter and bundled Common v3 Worker entry points."""

import argparse
import json
import sys
from importlib.metadata import version

from electronicload_tool_core.discovery import discover_resources
from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.identity import MODEL_ID
from electronicload_tool_core.readonly import COMMANDS, execute as read_execute
from electronicload_tool_core.control import CONTROL_COMMANDS, execute as control_execute
from .worker import manifest, run_worker
from .worker_client import request as worker_request, wait_ready


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
    command_names = (*COMMANDS, *CONTROL_COMMANDS, "list-resources", "manifest", "worker",
                     "send-command", "status", "stop", "wait-ready")
    command = argv[0] if argv and argv[0] in command_names else None
    parser = _Parser(
        prog="electronicload-tool",
        description="Electronic Load Tool: diagnostics, fixed CC, offline CV/CR preview and Worker v3.",
        allow_abbrev=False,
    )
    parser.json_errors = json_output
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {version('electronicload-tool')}")
    subparsers = parser.add_subparsers(dest="command")
    for name in command_names:
        sub = subparsers.add_parser(name, allow_abbrev=False)
        sub.json_errors = json_output
        if name == "list-resources":
            sub.add_argument("--live-only", action="store_true", help="Probe USB INSTR via *IDN?.")
            sub.add_argument("--json", action="store_true")
            continue
        if name == "manifest":
            sub.add_argument("--json", action="store_true")
            continue
        if name in ("send-command", "status", "stop", "wait-ready"):
            sub.add_argument("--url", required=True)
            sub.add_argument("--json", action="store_true")
            if name == "send-command":
                sub.add_argument("--worker-command", required=True)
                sub.add_argument("--arguments-json", default="{}")
                sub.add_argument("--context-json", required=True)
                sub.add_argument("--job-id")
            if name == "wait-ready":
                sub.add_argument("--wait-timeout-ms", type=int, default=10000)
            continue
        if name == "worker":
            sub.add_argument("--mode", choices=("simulate", "live"), default="simulate")
            sub.add_argument("--resource")
            sub.add_argument("--backend", default="system")
            sub.add_argument("--allow-input-on", action="store_true")
            sub.add_argument("--control-port", type=int, default=0)
            continue
        modes = sub.add_mutually_exclusive_group()
        modes.add_argument("--dry-run", action="store_true")
        modes.add_argument("--simulate", action="store_true")
        sub.add_argument("--resource", help="Explicit USB INSTR resource; required for LIVE.")
        sub.add_argument("--backend", default="system")
        sub.add_argument("--model", default=MODEL_ID)
        sub.add_argument("--json", action="store_true")
        sub.add_argument("--validation-allow-pending-live-support", action="store_true",
                         help=argparse.SUPPRESS)
        if name == "set-cc":
            sub.add_argument("--current", required=True, type=float)
            sub.add_argument("--range", required=True, choices=("low", "high"))
        elif name == "set-cv":
            sub.add_argument("--voltage", required=True, type=float)
            sub.add_argument("--range", required=True, choices=("low", "high"))
        elif name == "set-cr":
            sub.add_argument("--resistance", required=True, help="Resistance in ohms (offline only).")
            sub.add_argument("--range", required=True, choices=("low", "high"))
        elif name == "input-on":
            sub.add_argument("--confirm", action="store_true")
    try:
        if json_output and any(flag in argv for flag in ("--help", "-h", "--version")):
            raise LoadError("argument_error", "Request help or version separately from --json.")
        args = parser.parse_args(argv)
        if args.command is None:
            parser.print_help()
            return 0
        if args.command == "manifest":
            print(json.dumps(manifest(version("electronicload-tool")), allow_nan=False))
            return 0
        if args.command == "worker":
            if not 0 <= args.control_port <= 65535:
                raise LoadError("argument_error", "Worker port must be 0-65535.")
            return run_worker(mode=args.mode, resource=args.resource,
                              backend=args.backend,
                              allow_input_on=args.allow_input_on,
                              port=args.control_port)
        if args.command in ("send-command", "status", "stop", "wait-ready"):
            if args.command == "wait-ready":
                status = wait_ready(args.url, args.wait_timeout_ms)
                print(json.dumps(status, allow_nan=False))
                return 0
            if args.command == "send-command":
                try:
                    arguments = json.loads(args.arguments_json)
                    context = json.loads(args.context_json)
                except (ValueError, UnicodeError) as exc:
                    raise LoadError("argument_error", "Worker request JSON is invalid.") from exc
                body = {"schema_version": 3, "command": args.worker_command,
                        "arguments": arguments, "context": context}
                if args.job_id is not None:
                    body["job_id"] = args.job_id
                url_path = "/command"
            elif args.command == "stop":
                body = {"schema_version": 3}
                url_path = "/stop"
            else:
                body = None
                url_path = "/status"
            code, response = worker_request(args.url, url_path, body)
            print(json.dumps(response, allow_nan=False))
            return 0 if 200 <= code < 300 else 2 if code in (400, 409, 429) else 3
        if args.command == "list-resources":
            result = discover_resources(live_only=args.live_only)
        elif args.command in CONTROL_COMMANDS:
            result = control_execute(
                args.command, mode=mode, resource=args.resource, backend=args.backend,
                model_id=args.model,
                current=getattr(args, "current", None),
                voltage=getattr(args, "voltage", None),
                resistance=getattr(args, "resistance", None),
                range_name=getattr(args, "range", None),
                confirmed=getattr(args, "confirm", False),
                validation=args.validation_allow_pending_live_support,
            )
        else:
            result = read_execute(args.command, mode=mode, resource=args.resource,
                                  backend=args.backend, model_id=args.model,
                                  validation=args.validation_allow_pending_live_support)
    except LoadError as exc:
        return _emit_error(exc, command, mode, json_output)
    except KeyboardInterrupt:
        return _emit_error(LoadError("interrupted", "Request interrupted.", 3),
                           command, mode, json_output)
    if args.json:
        payload = {"ok": True, "command": args.command, "mode": mode, "result": result}
        if args.command != "list-resources":
            payload["model_id"] = args.model
        print(json.dumps(payload, allow_nan=False))
    elif args.command == "list-resources":
        print("Live resources:" if result["live_only"] else "Resources:")
        if not result["resources"]:
            print("  No live resources found" if result["live_only"] else "  <none>")
        for item in result["resources"]:
            print(f"  {item['resource']}")
            if "idn" in item:
                print(f"    IDN: {item['idn']}")
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
