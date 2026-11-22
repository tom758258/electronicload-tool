"""Minimal Common Worker Protocol v3 multi-job, in-memory, single-slot runtime."""

import datetime as dt
from decimal import Decimal, InvalidOperation
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from electronicload_tool_core.control import (CONTROL_COMMANDS, execute as control_execute,
                                              resolve_fixed_range, validate_power,
                                              validate_fixed_cc_slew, validate_von, validate_input_timer,
                                              validate_average, validate_vdrop, CR_COMMANDS,
                                              FIXED_RANGE_COMMANDS, FIXED_RANGE_CHOICES,
                                              TRANSIENT_FIELDS, TRANSIENT_SETUP_COMMANDS, validate_transient,
                                              LIST_COMMANDS, LIST_FIELDS,
                                              validate_list_cc, validate_list_slot)
from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.identity import MODEL_ID
from electronicload_tool_core.protection import (COMMANDS as PROTECTION_COMMANDS,
                                                execute as protection_execute,
                                                validate_arguments as validate_protection_arguments)
from electronicload_tool_core.readonly import (COMMANDS as READ_COMMANDS, ERROR_QUEUE_COMMAND,
                                               NATIVE_MEASUREMENTS, SYSTEM_QUERIES, execute as read_execute,
                                               validate_error_queue_all, validate_metric)
from electronicload_tool_core.sequence import (COMMAND as SEQUENCE_COMMAND,
                                               execute as sequence_execute, validate_document)
from electronicload_tool_core.simulator import ControlSimulator, Simulator
from electronicload_tool_core.trace import (CONFIG_FIELDS, CSV_FIELDS, TRACE_COMMANDS,
                                           execute as trace_execute,
                                           validate_configuration, validate_csv)
from electronicload_tool_core.trigger import validate_setup as validate_trigger_setup
from electronicload_tool_core.visa import TraceSessionLost, mark_session_untrusted, validate_connection
from .json_input import decode_decimal_json

SCHEMA = 3
COMMANDS = READ_COMMANDS + CONTROL_COMMANDS + TRACE_COMMANDS + PROTECTION_COMMANDS + (SEQUENCE_COMMAND,)
FEATURES = {"common.async_jobs": {"contract_version": 1},
            "common.job_cancel": {"contract_version": 1, "commands": ["trace-start"]}}
TRACE_RECOVERY_COMMANDS = {"input-state", "input-off", "trace-configure"}


def _utc():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _error(category, code, message, details=None):
    return {"category": category, "code": code, "message": message, "details": details or {}}


def manifest(version: str) -> dict:
    return {"event": "tool_manifest", "schema_version": 2,
            "tool_id": "electronicload", "tool_version": version,
            "worker_protocol": {"compatibility_policy": "v3-only", "schema_versions": [3]},
            "execution": {"model": "multi_job", "context_binding": "per_command"},
            "features": FEATURES,
            "commands": {command: {"kind": "async_job",
                                   **({"range_options": list(FIXED_RANGE_CHOICES)}
                                      if command in FIXED_RANGE_COMMANDS else {})}
                         for command in COMMANDS}}


class Runtime:
    def __init__(self, *, mode, resource=None, backend="system",
                 allow_input_on=False, port=0):
        self.mode = mode
        self.resource = resource
        self.backend = backend
        self.allow_input_on = allow_input_on
        self.run_id = uuid.uuid4().hex
        self.stop = threading.Event()
        self.stop_reply_sent = threading.Event()
        self.lock = threading.Lock()
        self.shutdown_started = False
        self.emit_lock = threading.Lock()
        self.active_job = None
        self.last_job = None
        self.job_thread = None
        self.simulator = ControlSimulator()
        # TRACE checks its own SCPI status without consuming the diagnostic FIFO.
        self.system_query_simulator = Simulator()
        self.server = None
        self.port = port
        self.host = "127.0.0.1"
        self.fatal_error = None
        self.trace_cancel = None
        self.trace_safety = None

    def urls(self):
        base = f"http://{self.host}:{self.port}"
        return {f"{key}_url": f"{base}/{key}" for key in ("status", "command", "stop", "cancel")}

    def emit(self, event, **values):
        payload = {"event": event, "schema_version": SCHEMA,
                   "timestamp_utc": _utc(), "run_id": self.run_id, **values}
        with self.emit_lock:
            print(json.dumps(payload, allow_nan=False), flush=True)

    def snapshot(self):
        with self.lock:
            return {"schema_version": SCHEMA, "timestamp_utc": _utc(),
                    "run_id": self.run_id, "service": "electronicload-tool",
                    "worker_state": ("failed" if self.fatal_error else
                                     "stopping" if self.stop.is_set() else "running"),
                    "fatal_error": self.fatal_error,
                    "active_job": dict(self.active_job) if self.active_job else None,
                    "last_job": dict(self.last_job) if self.last_job else None,
                    "features": FEATURES,
                    "trace_safety": dict(self.trace_safety) if self.trace_safety else None,
                    **self.urls()}

    def _execute(self, command, arguments, context):
        # Common v3 uses dry_run; Core uses dry-run.
        core_mode = "dry-run" if context["mode"] == "dry_run" else context["mode"]
        if command == SEQUENCE_COMMAND:
            return sequence_execute(arguments["document"], mode=core_mode, resource=self.resource,
                                    backend=self.backend, confirmed=arguments.get("confirm_input_on", False),
                                    simulator=self.simulator, stop=self.stop)
        if command in PROTECTION_COMMANDS:
            return protection_execute(command, mode=core_mode, resource=self.resource,
                                      backend=self.backend, arguments=arguments,
                                      simulator=self.simulator, stop=self.stop)
        if command in TRACE_COMMANDS:
            return trace_execute(
                command, mode=core_mode, resource=self.resource, backend=self.backend,
                arguments=arguments, simulator=self.simulator, stop=self.stop, cancel=self.trace_cancel,
            )
        if command in CONTROL_COMMANDS:
            if context["mode"] == "live" and self.mode != "live":
                raise LoadError("invalid_mode", "Live command not available in simulated Worker.")
            return control_execute(
                command, mode=core_mode, resource=self.resource,
                backend=self.backend, model_id=MODEL_ID,
                current=arguments.get("current"), voltage=arguments.get("voltage"),
                resistance=arguments.get("resistance"),
                vdrop=arguments.get("vdrop"),
                power=arguments.get("power"),
                range_name=(None if command in {*TRANSIENT_SETUP_COMMANDS, "set-list-cc"}
                            else arguments.get("range")),
                confirmed=arguments.get("confirm_input_on", False),
                stop=self.stop, simulator=self.simulator,
                transient=arguments if command in TRANSIENT_SETUP_COMMANDS else None,
                list_configuration=arguments if command == "set-list-cc" else None,
                slot=arguments.get("slot"),
                rise_slew=arguments.get("rise_slew") if command == "set-cc" else None,
                fall_slew=arguments.get("fall_slew") if command == "set-cc" else None,
                latch=arguments.get("latch"),
                seconds=arguments.get("seconds"),
                count=arguments.get("count"),
                trigger_source=arguments.get("source"),
                trigger_interval=arguments.get("interval_s"),
            )
        simulator = self.system_query_simulator if command in SYSTEM_QUERIES else self.simulator
        return read_execute(command, mode=core_mode, resource=self.resource,
                            backend=self.backend, model_id=MODEL_ID,
                            metric=arguments.get("metric"), simulator=simulator,
                            all_errors=arguments.get("all", False))

    def _shutdown_after_stop_reply(self):
        self.stop_reply_sent.wait()
        with self.lock:
            if self.shutdown_started:
                return
            self.shutdown_started = True
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def run_job(self, job, arguments, context):
        with self.lock:
            job["state"] = "running"
            job["timestamp_utc"] = _utc()
        self.emit("job_started", worker_job_id=job["worker_job_id"],
                  job_id=job["job_id"], command=job["command"])
        command = job["command"]
        session_lost = False
        try:
            result = self._execute(command, arguments, context)
            outcome = {"state": "succeeded", "ok": True, "result": result, "error": None}
        except LoadError as exc:
            session_lost = isinstance(exc, TraceSessionLost) or (
                self.trace_safety is not None and command in {"input-state", "input-off"}
                and exc.code in {"query_failed", "write_failed", "invalid_response", "invalid_identity"}
            )
            state = "cancelled" if exc.code == "trace_cancelled" else "failed"
            outcome = {"state": state, "ok": False, "result": None,
                       "error": _error("execution", exc.code, str(exc), exc.details)}
        except Exception as exc:
            outcome = {"state": "failed", "ok": False, "result": None,
                       "error": _error("worker", "execution_failed", "Command execution failed.",
                                       {"exception_type": type(exc).__name__})}
        terminal = {"worker_job_id": job["worker_job_id"], "job_id": job["job_id"],
                    "command": command, "timestamp_utc": _utc(), **outcome}
        with self.lock:
            self._record_trace_safety(command, context["mode"], outcome, session_lost)
            self.last_job = terminal
            self.active_job = None
            self.trace_cancel = None
        self.emit("job_finished", **terminal)
        if self.stop.is_set():
            self._shutdown_after_stop_reply()

    def _record_trace_safety(self, command, mode, outcome, session_lost):
        error = outcome["error"]
        if session_lost:
            if mode == "simulate":
                mark_session_untrusted(self.simulator)
            self.fatal_error = _error(
                "worker", "trace_session_untrusted",
                "Instrument communication is untrusted. Safe shutdown is unconfirmed.",
                {"domain_error": error},
            )
            self.trace_safety = {"state": "session_untrusted", "code": "trace_session_untrusted",
                                 "feed": "UNKNOWN", "input_state": "UNKNOWN"}
        elif error and error["details"].get("feed_stop_unconfirmed"):
            input_state = self.trace_safety["input_state"] if self.trace_safety else "UNKNOWN"
            self.trace_safety = {"state": "restricted_recovery", "code": error["code"],
                                 "feed": error["details"]["feed"], "input_state": input_state}
        elif self.trace_safety is not None and mode != "dry_run":
            if command in {"input-state", "input-off"}:
                result = outcome["result"]
                self.trace_safety["input_state"] = result["input_state"] if result else "UNKNOWN"
            elif command == "trace-configure":
                if outcome["ok"]:
                    self.trace_safety = None
                elif error["code"] == "input_not_off":
                    self.trace_safety["input_state"] = "ON"

    def check_trace_admission(self, command, context):
        if self.fatal_error:
            raise LoadError("trace_session_untrusted", self.fatal_error["message"])
        if self.trace_safety is None:
            return
        if (command not in TRACE_RECOVERY_COMMANDS or
            (command == "trace-configure" and
             (self.trace_safety["input_state"] != "OFF" or context["mode"] == "dry_run"))):
            raise LoadError("trace_recovery_required",
                            "TRACE recovery requires confirmed Input OFF and successful configuration.",
                            trace_safety=dict(self.trace_safety))


def _context(request, mode):
    context = request.get("context")
    if not isinstance(context, dict) or "mode" not in context:
        raise LoadError("invalid_context", "Per-command context with mode is required.")
    requested_mode = context.get("mode")
    if requested_mode not in {"live", "simulate", "dry_run"}:
        raise LoadError("invalid_context", "Unsupported execution mode.")
    allowed = {"mode", "expected_model_id"} if requested_mode == "live" else {"mode", "planning_model_id"}
    if set(context) - allowed:
        raise LoadError("invalid_context", "Unknown or forbidden context keys.")
    if requested_mode == "live":
        if mode != "live":
            raise LoadError("invalid_context", "LIVE requests require a live Worker.")
        if context.get("expected_model_id", MODEL_ID) != MODEL_ID:
            raise LoadError("unsupported_model", "Expected model is not registered.")
    elif context.get("planning_model_id") != MODEL_ID:
        raise LoadError("invalid_context", "Planning model must be itech-it8812c.")
    if requested_mode == "simulate" and mode != "simulate":
        raise LoadError("invalid_context", "Simulation requests require a simulated Worker.")
    return context


def _arguments(command, raw, runtime):
    if not isinstance(raw, dict):
        raise LoadError("argument_error", "Arguments must be an object.")
    if command == SEQUENCE_COMMAND:
        return raw
    if command in PROTECTION_COMMANDS:
        validate_protection_arguments(command, raw, json_numbers=True)
        return raw
    permitted = ({"current", "range", "rise_slew", "fall_slew"} if command == "set-cc" else
                 {"voltage", "range"} if command == "set-cv" else
                 {"resistance", "range"} if command == "set-cr" else
                 {"resistance", "range", "vdrop"} if command == "set-cr-led" else
                 {"power"} if command == "set-cw" else
                 {"voltage", "latch"} if command == "set-von" else
                 {"seconds"} if command == "set-input-timer" else
                 {"count"} if command == "set-average" else
                 {"source", "interval_s"} if command == "trigger-configure" else
                 CONFIG_FIELDS if command == "trace-configure" else
                 CSV_FIELDS if command == "trace-start" else
                 {"metric"} if command in NATIVE_MEASUREMENTS else
                 {"all"} if command == ERROR_QUEUE_COMMAND else
                 TRANSIENT_FIELDS if command in TRANSIENT_SETUP_COMMANDS else
                 LIST_FIELDS if command == "set-list-cc" else
                 {"slot"} if command in {"list-save", "list-recall"} else
                 {"confirm_input_on"} if command == "input-on" else set())
    if set(raw) - permitted:
        raise LoadError("argument_error", "Unsupported command arguments.")
    if command == "trace-configure":
        if set(raw) != CONFIG_FIELDS or type(raw["interval_s"]) not in (int, float, Decimal):
            raise LoadError("argument_error", "TRACE requires source, points and a numeric interval_s.")
        validate_configuration(**raw)
    if command == "trace-start":
        validate_csv(raw)
    if command in NATIVE_MEASUREMENTS and "metric" in raw:
        if not isinstance(raw["metric"], str):
            raise LoadError("argument_error", "Metric must be a string when supplied.")
        validate_metric(command, raw["metric"])
    if command == ERROR_QUEUE_COMMAND:
        validate_error_queue_all(raw.get("all", False))
    if command == "set-average":
        validate_average(raw.get("count"))
    if command == "trigger-configure":
        if "interval_s" in raw and type(raw["interval_s"]) not in (int, float, Decimal):
            raise LoadError("argument_error", "Trigger interval_s must be a JSON number.")
        validate_trigger_setup(raw.get("source"), raw.get("interval_s"))
    if command == "set-input-timer":
        validate_input_timer(raw.get("seconds"))
    if command == "set-list-cc":
        validate_list_cc(raw)
    if command in {"list-save", "list-recall"}:
        validate_list_slot(raw.get("slot"))
    if command in TRANSIENT_SETUP_COMMANDS:
        for key, value in raw.items():
            if key not in {"transient_mode", "range"} and type(value) not in (int, float, Decimal):
                raise LoadError("argument_error", "Transient values must be JSON numbers.")
        validate_transient(command, raw)
    if command == "set-cc":
        if (not isinstance(raw.get("range"), str)
                or type(raw.get("current")) not in (int, float, Decimal)):
            raise LoadError("argument_error", "CC requires current and range.")
        for field in ("rise_slew", "fall_slew"):
            if field in raw and type(raw[field]) not in (int, float, Decimal):
                raise LoadError("argument_error", "Slew rates must be JSON numbers.")
        range_name = resolve_fixed_range(command, raw["current"], raw["range"])
        validate_fixed_cc_slew(range_name, raw.get("rise_slew"), raw.get("fall_slew"))
    if command == "set-von":
        if set(raw) != permitted or type(raw["voltage"]) not in (int, float, Decimal):
            raise LoadError("argument_error", "VON requires voltage as a JSON number and latch.")
        validate_von(raw["voltage"], raw["latch"])
    if command == "set-cv":
        if (set(raw) != permitted or not isinstance(raw["range"], str)
                or type(raw["voltage"]) not in (int, float, Decimal)):
            raise LoadError("argument_error", "CV requires voltage and range.")
        resolve_fixed_range(command, raw["voltage"], raw["range"])
    if command in CR_COMMANDS:
        if (set(raw) != permitted or not isinstance(raw["range"], str)
                or type(raw["resistance"]) not in (int, float, Decimal)):
            raise LoadError("argument_error", "CR requires resistance and range.")
        resolve_fixed_range(command, raw["resistance"], raw["range"])
        if command == "set-cr-led":
            if type(raw["vdrop"]) not in (int, float, Decimal):
                raise LoadError("argument_error", "Vdrop must be a JSON number.")
            validate_vdrop(raw["vdrop"])
    if command == "set-cw":
        if set(raw) != permitted or type(raw["power"]) not in (int, float, Decimal):
            raise LoadError("argument_error", "CW requires power as a JSON number.")
        validate_power(raw["power"])
    if command == "input-on" and "confirm_input_on" in raw:
        if type(raw["confirm_input_on"]) is not bool:
            raise LoadError("argument_error", "confirm_input_on must be boolean.")
    return raw


def _handler(runtime):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _send(self, status, payload):
            data = json.dumps(payload, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self, *, strict=False):
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 65536:
                    raise ValueError("Content length is missing or too large.")
                body = self.rfile.read(size)
                decode = decode_decimal_json if strict else json.loads
                value = decode(body) if size else {}
                if not isinstance(value, dict):
                    raise ValueError("JSON body must be an object.")
                # Preserve the CW numeric token until Core validates the exact step/bounds.
                # Keep existing Common schema tokens and other commands' decoding unchanged.
                if value.get("command") == "set-cw" and isinstance(value.get("arguments"), dict):
                    if type(value["arguments"].get("power")) is float:
                        value["arguments"]["power"] = json.loads(
                            body, parse_float=Decimal)["arguments"]["power"]
                if (value.get("command") in (*FIXED_RANGE_COMMANDS, *TRANSIENT_SETUP_COMMANDS, "set-von")
                        and isinstance(value.get("arguments"), dict)):
                    # Core must validate the original decimal tokens.
                    value["arguments"] = json.loads(body, parse_float=Decimal)["arguments"]
                if value.get("command") in (*LIST_COMMANDS, *TRACE_COMMANDS, *PROTECTION_COMMANDS,
                                            "trigger-configure", SEQUENCE_COMMAND):
                    # Preserve nested numeric tokens; reject duplicate fields/nonfinite JSON.
                    value = decode_decimal_json(body)
                return value
            except (ValueError, UnicodeError, json.JSONDecodeError, InvalidOperation,
                    RecursionError) as exc:
                raise LoadError("invalid_request", "Invalid JSON request.") from exc

        def do_GET(self):
            if urlsplit(self.path).path == "/status":
                self._send(200, runtime.snapshot())
            else:
                self._send(404, {"status": "error", "error": _error("validation", "not_found", "Unknown endpoint.")})

        def _cancel(self):
            worker_job_id = None
            try:
                req = self._body(strict=True)
                candidate = req.get("worker_job_id")
                if isinstance(candidate, str) and candidate.strip():
                    worker_job_id = candidate
                if (type(req.get("schema_version")) is not int or req["schema_version"] != SCHEMA
                    or worker_job_id is None or set(req) - {"schema_version", "worker_job_id", "reason"}
                    or ("reason" in req and not isinstance(req["reason"], str))):
                    raise LoadError("invalid_request", "Invalid job cancellation request.")
                with runtime.lock:
                    job = runtime.active_job
                    if (job is None or job["worker_job_id"] != worker_job_id
                        or runtime.trace_cancel is None or runtime.stop.is_set()):
                        result = (409, "rejected", _error(
                            "admission", "job_not_cancellable", "No matching cancellable TRACE job."))
                    else:
                        runtime.trace_cancel.set()
                        result = (202, "accepted", None)
            except LoadError as exc:
                result = (400, "error", _error("validation", exc.code, str(exc), exc.details))
            except Exception:
                result = (500, "error", _error("worker", "cancel_failed", "Cancellation handling failed."))
            status, status_value, error = result
            self._send(status, {"schema_version": SCHEMA, "run_id": runtime.run_id,
                                "status": status_value, "worker_job_id": worker_job_id, "error": error})

        def do_POST(self):
            path = urlsplit(self.path).path
            if path == "/cancel":
                self._cancel()
                return
            if path not in {"/stop", "/command"}:
                self._send(404, {"status": "error", "error": _error("validation", "not_found", "Unknown endpoint.")})
                return
            command = job_id = kind = None
            try:
                req = self._body()
                if type(req.get("schema_version")) is not int or req["schema_version"] != 3:
                    raise LoadError("invalid_schema", "Worker schema_version must be integer 3.")
                if path == "/stop":
                    if set(req) - {"schema_version", "reason"} or ("reason" in req and not isinstance(req["reason"], str)):
                        raise LoadError("invalid_request", "Invalid stop arguments.")
                    with runtime.lock:
                        runtime.stop.set()
                        if runtime.trace_cancel is not None:
                            runtime.trace_cancel.set()
                        busy = runtime.active_job is not None
                    try:
                        self._send(202, {"schema_version": 3, "run_id": runtime.run_id,
                                         "status": "accepted", "error": None})
                    finally:
                        runtime.stop_reply_sent.set()
                        if not busy:
                            runtime._shutdown_after_stop_reply()
                    return
                if set(req) - {"schema_version", "command", "arguments", "job_id", "context"}:
                    raise LoadError("invalid_request", "Unknown request fields.")
                command, job_id = req.get("command"), req.get("job_id")
                if not isinstance(command, str) or not command or command not in COMMANDS:
                    raise LoadError("unknown_command", "Unsupported Worker command.")
                kind = "async_job"
                if job_id is not None and not isinstance(job_id, str):
                    raise LoadError("invalid_request", "job_id must be a string.")
                context = _context(req, runtime.mode)
                arguments = _arguments(command, req.get("arguments", {}), runtime)
                if command == SEQUENCE_COMMAND:
                    if set(arguments) - {"document", "confirm_input_on"}:
                        raise LoadError("argument_error", "Sequence accepts only document and confirm_input_on.")
                    confirmed = arguments.get("confirm_input_on", False)
                    if type(confirmed) is not bool:
                        raise LoadError("argument_error", "confirm_input_on must be boolean.")
                    _, _, contains_on = validate_document(arguments.get("document"))
                    if contains_on and context["mode"] != "dry_run" and not confirmed:
                        raise LoadError("confirmation_required", "Sequence Input ON requires explicit run confirmation.")
                if command == "input-on" and context["mode"] == "live":
                    if not runtime.allow_input_on:
                        raise LoadError("input_on_not_allowed", "Worker Input ON is disabled.")
                    if arguments.get("confirm_input_on") is not True:
                        raise LoadError("confirmation_required", "Input ON confirmation is required.")
                with runtime.lock:
                    if runtime.stop.is_set():
                        raise LoadError("stopping", "Worker is stopping.")
                    if runtime.active_job is not None:
                        raise LoadError("busy", "Worker is busy.")
                    runtime.check_trace_admission(command, context)
                    job = {"worker_job_id": uuid.uuid4().hex,
                           "job_id": job_id, "command": command,
                           "state": "queued", "timestamp_utc": _utc()}
                    runtime.active_job = job
                    if command == "trace-start":
                        runtime.trace_cancel = threading.Event()
                runtime.emit("job_accepted", worker_job_id=job["worker_job_id"], job_id=job_id, command=command)
                thread = threading.Thread(target=runtime.run_job, args=(job, arguments, context), daemon=True)
                runtime.job_thread = thread
                thread.start()
                self._send(202, {"schema_version": 3, "run_id": runtime.run_id,
                                 "status": "accepted", "command": command,
                                 "job_id": job_id, "kind": kind,
                                 "worker_job_id": job["worker_job_id"], "error": None})
            except LoadError as exc:
                rejection = exc.code in {"busy", "stopping", "input_on_not_allowed", "confirmation_required",
                                         "trace_session_untrusted", "trace_recovery_required"}
                status = 409 if rejection else 400
                self._send(status, {"schema_version": 3, "run_id": runtime.run_id,
                                    "status": "rejected" if rejection else "error",
                                    "command": command if isinstance(command, str) else None,
                                    "job_id": job_id if isinstance(job_id, str) else None,
                                    "kind": kind, "worker_job_id": None,
                                    "error": _error("admission" if rejection else "validation", exc.code, str(exc), exc.details)})

    return Handler


def run_worker(*, mode="simulate", resource=None, backend="system",
               allow_input_on=False, port=0):
    if mode not in {"simulate", "live"}:
        raise LoadError("invalid_mode", "Worker requires live or simulate mode.")
    validate_connection(resource, backend, required=mode == "live")
    runtime = Runtime(mode=mode, resource=resource, backend=backend,
                      allow_input_on=allow_input_on, port=port)
    try:
        with ThreadingHTTPServer((runtime.host, port), _handler(runtime)) as server:
            runtime.server = server
            runtime.port = server.server_port
            runtime.emit("ready", service="electronicload-tool", **runtime.urls(),
                         features=FEATURES)
            server.serve_forever(poll_interval=0.05)
            if runtime.job_thread is not None:
                runtime.job_thread.join()
            trace_shutdown = ({"trace_safety": {**runtime.trace_safety,
                                               "safe_shutdown_unconfirmed": True}}
                              if runtime.trace_safety else {})
            runtime.emit("summary", ok=runtime.fatal_error is None,
                         fatal_error=runtime.fatal_error,
                         last_job=runtime.last_job, exit_code=0 if runtime.fatal_error is None else 3,
                         **trace_shutdown)
            return 0 if runtime.fatal_error is None else 3
    except OSError as exc:
        runtime.emit("error", error=_error("worker", "listen_failed", "Worker HTTP bind failed.",
                                           {"exception_type": type(exc).__name__}))
        runtime.emit("summary", ok=False,
                     fatal_error=_error("worker", "listen_failed", "Worker HTTP bind failed."),
                     exit_code=3)
        return 3
