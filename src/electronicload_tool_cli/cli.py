"""Standalone Core adapter and bundled Common v3 Worker entry points."""

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from importlib.metadata import version
from pathlib import Path

from electronicload_tool_core.discovery import discover_resources
from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.identity import MODEL_ID
from electronicload_tool_core.protection import (ARGUMENTS as PROTECTION_ARGUMENTS,
                                                COMMANDS as PROTECTION_COMMANDS,
                                                DEFAULTS as PROTECTION_DEFAULTS,
                                                LIMITS as PROTECTION_LIMITS, TRIALS as PROTECTION_TRIALS,
                                                execute as protection_execute)
from electronicload_tool_core.readback import (COMMANDS as READBACK_COMMANDS,
                                               CONTROL_STATUS_COMMANDS, SCOPE as READBACK_SCOPE)
from electronicload_tool_core.readonly import (ASRL_READONLY_COMMANDS, ASRL_READONLY_SCOPE,
                                               BASIC_MEASUREMENTS, COMMANDS, ERROR_QUEUE_COMMAND,
                                               MAX_ERROR_READS, NATIVE_MEASUREMENTS, SYSTEM_QUERIES,
                                               execute as read_execute)
from electronicload_tool_core.sequence import COMMAND as SEQUENCE_COMMAND, execute as sequence_execute
from electronicload_tool_core.control import (CONTROL_COMMANDS, execute as control_execute,
                                              validate_power, TRANSIENT_FIELDS, LIST_COMMANDS,
                                              FIXED_RANGE_COMMANDS, FIXED_RANGE_CHOICES,
                                              TRANSIENT_SETUP_COMMANDS, AUXILIARY_COMMANDS,
                                              AVERAGE_COMMANDS, CR_COMMANDS, MEASUREMENT_VALIDATION_SCOPE)
from electronicload_tool_core.trace import (CONFIG_FIELDS, CSV_FIELDS, SAMPLE_FIELDS,
                                           TRACE_COMMANDS, execute as trace_execute)
from electronicload_tool_core.trigger import TRIGGER_SOURCES
from electronicload_tool_core.visa import SERIAL_CHOICES, SerialOptions
from electronicload_tool_core.workflows import (ARGUMENTS as WORKFLOW_ARGUMENTS,
                                                COMMANDS as WORKFLOW_COMMANDS, CONTRIBUTOR_SCOPE,
                                                MAX_INTERVAL, MAX_SAMPLES, MEASUREMENT_COMMANDS,
                                                MIN_INTERVAL, OPERATORS,
                                                execute as workflow_execute)
from .json_input import decode_decimal_json
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


def _current_token(value: str) -> str:
    # Retain the parser's numeric syntax check without rounding the setpoint.
    try:
        float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Current must be numeric.") from exc
    return value


def _workflow_number(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("Expected a number.") from exc


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_output = ("--json" in argv or
                   (argv and argv[0] in (SEQUENCE_COMMAND, *WORKFLOW_COMMANDS)
                    and not {"--help", "-h"}.intersection(argv)))
    mode = "dry-run" if "--dry-run" in argv else "simulate" if "--simulate" in argv else "live"
    command_names = (*COMMANDS, *CONTROL_COMMANDS, *TRACE_COMMANDS, *PROTECTION_COMMANDS, SEQUENCE_COMMAND,
                     *WORKFLOW_COMMANDS,
                     "list-resources", "manifest", "worker",
                     "send-command", "status", "stop", "wait-ready")
    command = argv[0] if argv and argv[0] in command_names else None
    parser = _Parser(
        prog="electronicload-tool",
        description=("Electronic Load Tool with fixed CC, offline CV/CR/CW/Transient/LIST CC, "
                     "Software Sequence, basic workflows and Worker v3."),
        allow_abbrev=False,
    )
    parser.json_errors = json_output
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {version('electronicload-tool')}")
    subparsers = parser.add_subparsers(dest="command")
    for name in command_names:
        description = ("Read and remove the next entry from the instrument's SCPI Error Queue."
                       if name == ERROR_QUEUE_COMMAND else None)
        if name in WORKFLOW_COMMANDS:
            description = "Use --simulate to run or --dry-run to preview. Results are JSON."
            if name == "measure-log":
                description += " Use exactly one of --count and --duration-s."
            if name != "cc-timed-load":
                description += f" Sampling stops after at most {MAX_SAMPLES:,} samples."
        sub = subparsers.add_parser(name, allow_abbrev=False, description=description)
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
        sub.add_argument("--resource", help=("Optional USB INSTR resource for connection option validation."
                                             if name in WORKFLOW_COMMANDS and name not in MEASUREMENT_COMMANDS else
                                             "Explicit USB INSTR, or ASRL INSTR for contributor validation."))
        sub.add_argument("--backend", default="system")
        sub.add_argument("--model", default=MODEL_ID)
        sub.add_argument("--json", action="store_true")
        if name not in WORKFLOW_COMMANDS or name in MEASUREMENT_COMMANDS:
            sub.add_argument("--validation-connection", choices=("asrl",),
                             help="Use explicit ASRL INSTR for standalone contributor validation.")
            sub.add_argument("--serial-baud-rate", type=int, default=argparse.SUPPRESS)
            sub.add_argument("--serial-data-bits", type=int, default=argparse.SUPPRESS)
            for field, choices in SERIAL_CHOICES.items():
                sub.add_argument("--serial-" + field.replace("_", "-"), choices=tuple(choices),
                                 default=argparse.SUPPRESS)
        if name in NATIVE_MEASUREMENTS or name == "set-average" or name in MEASUREMENT_COMMANDS:
            scope = CONTRIBUTOR_SCOPE if name in MEASUREMENT_COMMANDS else MEASUREMENT_VALIDATION_SCOPE
            sub.add_argument("--hardware-validation", choices=(scope,),
                             help="Explicit CLI-only contributor hardware validation.")
        if name in WORKFLOW_COMMANDS:
            required, optional = WORKFLOW_ARGUMENTS[name]
            choices = {"metric": tuple(BASIC_MEASUREMENTS), "operator": tuple(OPERATORS),
                       "range": FIXED_RANGE_CHOICES}
            types = {"count": int, "csv": str, **dict.fromkeys(choices, str)}
            workflow_help = {
                "count": f"Number of samples (1 to {MAX_SAMPLES:,}).",
                "interval_s": f"Sampling interval in seconds ({MIN_INTERVAL} to {MAX_INTERVAL}).",
                "duration_s": "Duration in seconds (positive and finite, with no fixed maximum).",
                "timeout_s": "Timeout in seconds (positive and finite, with no fixed maximum).",
            }
            for field in (*required, *optional):
                if field == "confirm_input_on":
                    sub.add_argument("--confirm", dest=field, action="store_true",
                                     help="Confirm simulated Input ON.")
                else:
                    sub.add_argument("--" + field.replace("_", "-"), required=field in required,
                                     default=argparse.SUPPRESS, choices=choices.get(field),
                                     type=types.get(field, _workflow_number), help=workflow_help.get(field))
            continue
        sub.add_argument("--validation-allow-pending-live-support", action="store_true",
                         help=argparse.SUPPRESS)
        if name in ASRL_READONLY_COMMANDS:
            sub.add_argument("--hardware-validation", choices=(ASRL_READONLY_SCOPE,),
                             help="Explicit CLI-only IT8812C ASRL read-only validation.")
        if name == SEQUENCE_COMMAND:
            sub.add_argument("--file", required=True, help="UTF-8 Software Sequence JSON document.")
            sub.add_argument("--hardware-validation", choices=(SEQUENCE_COMMAND,),
                             help="Explicit CLI-only IT8812C contributor hardware validation.")
        if name in NATIVE_MEASUREMENTS:
            sub.add_argument("--metric", choices=tuple(NATIVE_MEASUREMENTS[name]))
        if name in SYSTEM_QUERIES:
            sub.add_argument("--hardware-validation", choices=("system-query",),
                             help="Explicit CLI-only IT8812C contributor validation.")
        if name == ERROR_QUEUE_COMMAND:
            sub.add_argument("--all", action="store_true",
                             help=f"Remove entries until empty is confirmed or {MAX_ERROR_READS} reads are made.")
        if name in READBACK_COMMANDS or (name in CONTROL_STATUS_COMMANDS and name != "transient-status"):
            sub.add_argument("--hardware-validation", choices=(READBACK_SCOPE,),
                             help="Explicit CLI-only contributor hardware validation.")
        elif name in CONTROL_COMMANDS and name not in (*LIST_COMMANDS, *AUXILIARY_COMMANDS,
                                                       *AVERAGE_COMMANDS):
            sub.add_argument("--hardware-validation", choices=("input-timer", "cv", "cr", "cw", "transient-cc",
                                                               "transient-cv", "transient-cr", "transient-cw",
                                                               "cr-led", "current-slow-rise", "trigger",
                                                               *((READBACK_SCOPE,) if name == "transient-status" else ()),
                                                               *(("protection",) if name == "input-on" else ())),
                             help="Explicit contributor hardware-validation scope; not Product LIVE.")
            sub.add_argument("--validation-settings-json",
                             help="Expected complete setup for validation enable, ON and trigger.")
        elif name in {"set-list-cc", "list-trigger", "list-exit"}:
            sub.add_argument("--hardware-validation", choices=("trigger",),
                             help="Explicit CLI-only Trigger hardware validation.")
            if name == "list-trigger":
                sub.add_argument("--validation-settings-json", help="Expected LIST and Trigger Setup.")
        if name in TRACE_COMMANDS:
            sub.add_argument("--hardware-validation", choices=("trace",),
                             help="Explicit CLI-only IT8812C TRACE hardware validation; not Product LIVE.")
        if name in PROTECTION_COMMANDS:
            sub.add_argument("--hardware-validation", choices=("protection",),
                             help="Explicit CLI-only IT8812C contributor validation.")
        if name == "protection-status":
            sub.add_argument("--events", action="store_true", default=argparse.SUPPRESS,
                             help="Read and consume the Questionable Event register.")
            sub.add_argument("--validation-snapshot", action="store_true",
                             help="Verify Fixed CC readiness and include its settings for restoration.")
        elif name == "protection-set":
            sub.add_argument("--software-current-enabled", choices=("on", "off"), default=argparse.SUPPRESS)
            for field in PROTECTION_LIMITS:
                sub.add_argument("--" + field.replace("_", "-"), default=argparse.SUPPRESS,
                                 help="Seconds" if field.endswith("delay") else
                                 "Amperes" if "current" in field else "Watts")
            sub.add_argument("--restore-defaults", choices=tuple(PROTECTION_DEFAULTS),
                             default=argparse.SUPPRESS)
        if name in {"protection-set", "protection-clear", "input-on", SEQUENCE_COMMAND}:
            sub.add_argument("--confirm", action="store_true")
        if name == "input-on":
            sub.add_argument("--protection-trial", choices=tuple(PROTECTION_TRIALS),
                             help="Select a case for contributor Protection validation.")
        if name == "trace-configure":
            sub.add_argument("--source", required=True, choices=tuple(SAMPLE_FIELDS))
            sub.add_argument("--points", required=True, type=int)
            sub.add_argument("--interval-s", required=True, help="Capture interval in seconds.")
        elif name == "trace-start":
            sub.add_argument("--csv", default=argparse.SUPPRESS, help="Create a new CSV at this path.")
            sub.add_argument("--csv-format", choices=("data", "full"), default=argparse.SUPPRESS)
            sub.add_argument("--csv-time-columns", default=argparse.SUPPRESS, metavar="elapsed,delta")
        elif name == "set-average":
            sub.add_argument("--count", required=True, type=int, help="Integer average count, 2 through 16.")
            sub.add_argument("--confirm", action="store_true",
                             help="Confirm a contributor hardware Average change.")
        elif name == "set-input-timer":
            sub.add_argument("--seconds", required=True, type=int, help="Integer seconds, 1 through 60000.")
        elif name == "trigger-configure":
            sub.add_argument("--source", required=True, type=str.upper, choices=TRIGGER_SOURCES)
            sub.add_argument("--interval-s", help="Trigger Timer interval in seconds.")
        elif name == "set-cc":
            sub.add_argument("--current", required=True, type=_current_token)
            sub.add_argument("--range", required=True, choices=FIXED_RANGE_CHOICES)
            sub.add_argument("--rise-slew", help="Rising slew in A/us; pair with --fall-slew (offline only).")
            sub.add_argument("--fall-slew", help="Falling slew in A/us; pair with --rise-slew (offline only).")
        elif name == "set-cv":
            sub.add_argument("--voltage", required=True)
            sub.add_argument("--range", required=True, choices=FIXED_RANGE_CHOICES)
        elif name in CR_COMMANDS:
            sub.add_argument("--resistance", required=True, help="Resistance in ohms (offline only).")
            sub.add_argument("--range", required=True, choices=FIXED_RANGE_CHOICES)
            if name == "set-cr-led":
                sub.add_argument("--vdrop", required=True, help="LED voltage drop in volts (offline only).")
        elif name == "set-cw":
            sub.add_argument("--power", required=True, help="Power in watts (offline only).")
        elif name == "set-von":
            sub.add_argument("--voltage", required=True, help="VON threshold in volts (offline only).")
            sub.add_argument("--latch", required=True, choices=("on", "off"))
        elif name in TRANSIENT_SETUP_COMMANDS:
            sub.add_argument("--transient-mode", required=True,
                             choices=("continuous", "pulse", "toggle"))
            if name != "set-transient-cw":
                sub.add_argument("--range", required=True, choices=FIXED_RANGE_CHOICES)
            fields = (("level-a", "level-b", "rise-slew", "fall-slew")
                      if name == "set-transient-cc" else ("level-a", "level-b"))
            unit = {"CC": "Amperes", "CV": "Volts", "CR": "Ohms",
                    "CW": "Watts"}[TRANSIENT_SETUP_COMMANDS[name]]
            for field in fields:
                sub.add_argument(f"--{field}", required=True,
                                 help="A/us" if "slew" in field else unit)
            for field in ("width-a", "width-b", "pulse-width"):
                sub.add_argument(f"--{field}", default=argparse.SUPPRESS, help="Seconds")
        elif name == "set-list-cc":
            data = sub.add_mutually_exclusive_group(required=True)
            data.add_argument("--arguments-json", help="Complete LIST configuration JSON.")
            data.add_argument("--file", help="UTF-8 JSON file containing the same LIST configuration.")
        elif name in {"list-save", "list-recall"}:
            sub.add_argument("--slot", required=True, type=int, help="Instrument LIST slot, 1 through 7.")
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
                    arguments = (decode_decimal_json(args.arguments_json)
                                 if args.worker_command in (*LIST_COMMANDS, *TRACE_COMMANDS,
                                                            *PROTECTION_COMMANDS, "trigger-configure",
                                                            SEQUENCE_COMMAND, *WORKFLOW_COMMANDS)
                                 else json.loads(args.arguments_json))
                    context = json.loads(args.context_json)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise LoadError("argument_error", "Worker request JSON is invalid.") from exc
                # Validate CW before forwarding a float-rounded JSON number to the Worker.
                if args.worker_command == "set-cw" and isinstance(arguments, dict):
                    if type(arguments.get("power")) is float:
                        power = json.loads(args.arguments_json, parse_float=str)["power"]
                        arguments["power"] = float(validate_power(power))
                body = {"schema_version": 3, "command": args.worker_command,
                        "arguments": arguments, "context": context}
                if args.job_id is not None:
                    body["job_id"] = args.job_id
                if args.worker_command in {*FIXED_RANGE_COMMANDS, *TRANSIENT_SETUP_COMMANDS,
                                           *LIST_COMMANDS, *TRACE_COMMANDS, *PROTECTION_COMMANDS,
                                           "set-von", "trigger-configure", SEQUENCE_COMMAND,
                                           *WORKFLOW_COMMANDS}:
                    # Forward original numeric tokens; Core in the Worker validates them.
                    # Parsing above already guarantees a complete JSON value.
                    del body["arguments"]
                    body = (json.dumps(body)[:-1] + ',"arguments":'
                            + args.arguments_json + '}').encode("utf-8")
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
        serial = {name.removeprefix("serial_"): value for name, value in vars(args).items()
                  if name.startswith("serial_")}
        asrl = (SerialOptions(**serial) if getattr(args, "validation_connection", None) == "asrl"
                else None)
        scope = getattr(args, "hardware_validation", None)
        if serial and asrl is None and scope != ASRL_READONLY_SCOPE:
            raise LoadError("argument_error", "Serial options require ASRL contributor validation.")
        if args.command == "list-resources":
            result = discover_resources(live_only=args.live_only)
        elif args.command == SEQUENCE_COMMAND:
            try:
                document = decode_decimal_json(Path(args.file).read_text(encoding="utf-8-sig"))
            except (OSError, ValueError, RecursionError) as exc:
                raise LoadError("argument_error", "Cannot read a valid Sequence JSON document.") from exc
            result = sequence_execute(document, mode=mode, model_id=args.model,
                                      backend=args.backend, resource=args.resource,
                                      confirmed=args.confirm,
                                      hardware_validation=args.hardware_validation, asrl=asrl)
            args.json = True
        elif args.command in WORKFLOW_COMMANDS:
            required, optional = WORKFLOW_ARGUMENTS[args.command]
            arguments = {field: getattr(args, field) for field in (*required, *optional)
                         if hasattr(args, field)}
            result = workflow_execute(args.command, mode=mode, model_id=args.model,
                                      backend=args.backend, resource=args.resource, arguments=arguments,
                                      hardware_validation=scope, asrl=asrl)
            args.json = True
        elif (args.command in PROTECTION_COMMANDS
              or getattr(args, "hardware_validation", None) == "protection"):
            if getattr(args, "validation_settings_json", None) is not None:
                raise LoadError("argument_error", "Use --protection-trial for contributor Input ON.")
            fields = PROTECTION_ARGUMENTS.get(args.command, {"confirm"})
            arguments = {key: value for key, value in vars(args).items() if key in fields}
            if "software_current_enabled" in arguments:
                arguments["software_current_enabled"] = arguments["software_current_enabled"] == "on"
            result = protection_execute(
                args.command, mode=mode, resource=args.resource, backend=args.backend, model_id=args.model,
                arguments=arguments, hardware_validation=args.hardware_validation == "protection",
                snapshot=getattr(args, "validation_snapshot", False),
                trial=getattr(args, "protection_trial", None), asrl=asrl,
            )
        elif args.command in TRACE_COMMANDS:
            fields = CONFIG_FIELDS if args.command == "trace-configure" else CSV_FIELDS
            result = trace_execute(args.command, mode=mode, resource=args.resource,
                                   backend=args.backend, model_id=args.model,
                                   hardware_validation=args.hardware_validation, asrl=asrl,
                                   arguments={key: value for key, value in vars(args).items()
                                              if key in fields})
        elif args.command in CONTROL_COMMANDS:
            if getattr(args, "protection_trial", None) is not None:
                raise LoadError("argument_error", "--protection-trial requires --hardware-validation protection.")
            list_configuration = None
            if args.command == "set-list-cc":
                try:
                    data = (Path(args.file).read_text(encoding="utf-8-sig")
                            if args.file is not None else args.arguments_json)
                    list_configuration = decode_decimal_json(data)
                except (OSError, ValueError, UnicodeError, RecursionError) as exc:
                    raise LoadError("argument_error", "Cannot read a valid LIST JSON configuration.") from exc
            validation_settings = None
            if getattr(args, "validation_settings_json", None) is not None:
                try:
                    validation_settings = (decode_decimal_json(args.validation_settings_json)
                                           if args.hardware_validation == "trigger" else
                                           json.loads(args.validation_settings_json, parse_float=Decimal))
                except (ValueError, UnicodeError) as exc:
                    raise LoadError("argument_error", "Validation settings JSON is invalid.") from exc
                if not isinstance(validation_settings, dict):
                    raise LoadError("argument_error", "Validation settings must be an object.")
            result = control_execute(
                args.command, mode=mode, resource=args.resource, backend=args.backend,
                model_id=args.model,
                current=getattr(args, "current", None),
                voltage=getattr(args, "voltage", None),
                resistance=getattr(args, "resistance", None),
                vdrop=getattr(args, "vdrop", None),
                power=getattr(args, "power", None),
                range_name=(None if args.command in TRANSIENT_SETUP_COMMANDS
                            else getattr(args, "range", None)),
                confirmed=getattr(args, "confirm", False),
                validation=args.validation_allow_pending_live_support,
                hardware_validation=getattr(args, "hardware_validation", None),
                validation_settings=validation_settings,
                transient={key: value for key, value in vars(args).items()
                           if key in TRANSIENT_FIELDS} if args.command in TRANSIENT_SETUP_COMMANDS else None,
                list_configuration=list_configuration,
                slot=getattr(args, "slot", None),
                rise_slew=getattr(args, "rise_slew", None) if args.command == "set-cc" else None,
                fall_slew=getattr(args, "fall_slew", None) if args.command == "set-cc" else None,
                latch=getattr(args, "latch", None),
                seconds=getattr(args, "seconds", None),
                count=getattr(args, "count", None),
                trigger_source=getattr(args, "source", None),
                trigger_interval=getattr(args, "interval_s", None),
                asrl=asrl,
            )
        else:
            result = read_execute(args.command, mode=mode, resource=args.resource,
                                  backend=args.backend, model_id=args.model,
                                  validation=args.validation_allow_pending_live_support,
                                  metric=getattr(args, "metric", None),
                                  all_errors=getattr(args, "all", False),
                                  hardware_validation=getattr(args, "hardware_validation", None),
                                  serial_options=SerialOptions(**serial) if serial and asrl is None else None,
                                  asrl=asrl)
    except LoadError as exc:
        return _emit_error(exc, command, mode, json_output)
    except KeyboardInterrupt:
        return _emit_error(LoadError("interrupted", "Request interrupted.", 3),
                           command, mode, json_output)
    if asrl is not None:
        result = {**result, "transport": "asrl", "validation_only": True}
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
