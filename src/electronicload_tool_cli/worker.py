"""Minimal Common Worker Protocol v3 multi-job, in-memory, single-slot runtime."""

import datetime as dt
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from electronicload_tool_core.control import (CONTROL_COMMANDS, execute as control_execute,
                                              validate_current, validate_voltage)
from electronicload_tool_core.errors import LoadError
from electronicload_tool_core.identity import MODEL_ID
from electronicload_tool_core.readonly import COMMANDS as READ_COMMANDS, _read
from electronicload_tool_core.simulator import ControlSimulator
from electronicload_tool_core.visa import open_session, validate_connection

SCHEMA = 3
COMMANDS = READ_COMMANDS + CONTROL_COMMANDS


def _utc():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _error(category, code, message, details=None):
    return {"category": category, "code": code, "message": message, "details": details or {}}


def manifest(version: str) -> dict:
    return {"event": "tool_manifest", "schema_version": 2,
            "tool_id": "electronicload", "tool_version": version,
            "worker_protocol": {"compatibility_policy": "v3-only", "schema_versions": [3]},
            "execution": {"model": "multi_job", "context_binding": "per_command"},
            "features": {"common.async_jobs": {"contract_version": 1}},
            "commands": {command: {"kind": "async_job"} for command in COMMANDS}}


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
        self.server = None
        self.port = port
        self.host = "127.0.0.1"
        self.fatal_error = None

    def urls(self):
        base = f"http://{self.host}:{self.port}"
        return {f"{key}_url": f"{base}/{key}" for key in ("status", "command", "stop")}

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
                    "features": {"common.async_jobs": {"contract_version": 1}},
                    **self.urls()}

    def _execute(self, command, arguments, context):
        if command in CONTROL_COMMANDS:
            if context["mode"] == "live" and self.mode != "live":
                raise LoadError("invalid_mode", "Live command not available in simulated Worker.")
            return control_execute(
                command, mode=context["mode"], resource=self.resource,
                backend=self.backend, model_id=MODEL_ID,
                current=arguments.get("current"), voltage=arguments.get("voltage"),
                range_name=arguments.get("range"),
                confirmed=arguments.get("confirm_input_on", False),
                stop=self.stop, simulator=self.simulator,
            )
        if context["mode"] == "simulate":
            return _read(self.simulator, command)
        if context["mode"] == "dry_run":
            from electronicload_tool_core.readonly import execute
            return execute(command, mode="dry-run")
        with open_session(self.resource) as (session, runtime):
            result = _read(session, command)
            result["visa"] = runtime
            return result

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
        try:
            result = self._execute(job["command"], arguments, context)
            outcome = {"state": "succeeded", "ok": True, "result": result, "error": None}
        except LoadError as exc:
            outcome = {"state": "failed", "ok": False, "result": None,
                       "error": _error("execution", exc.code, str(exc), exc.details)}
        except Exception as exc:
            outcome = {"state": "failed", "ok": False, "result": None,
                       "error": _error("worker", "execution_failed", "Command execution failed.",
                                       {"exception_type": type(exc).__name__})}
        terminal = {"worker_job_id": job["worker_job_id"], "job_id": job["job_id"],
                    "command": job["command"], "timestamp_utc": _utc(), **outcome}
        with self.lock:
            self.active_job = None
            self.last_job = terminal
        self.emit("job_finished", **terminal)
        if self.stop.is_set():
            self._shutdown_after_stop_reply()


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
    permitted = ({"current", "range"} if command == "set-cc" else
                 {"voltage", "range"} if command == "set-cv" else
                 {"confirm_input_on"} if command == "input-on" else set())
    if set(raw) - permitted:
        raise LoadError("argument_error", "Unsupported command arguments.")
    if command == "set-cc":
        if (set(raw) != permitted or not isinstance(raw["range"], str)
                or type(raw["current"]) not in (int, float)):
            raise LoadError("argument_error", "CC requires current and range.")
        validate_current(raw["current"], raw["range"])
    if command == "set-cv":
        if (set(raw) != permitted or not isinstance(raw["range"], str)
                or type(raw["voltage"]) not in (int, float)):
            raise LoadError("argument_error", "CV requires voltage and range.")
        validate_voltage(raw["voltage"], raw["range"])
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

        def _body(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 65536:
                    raise ValueError("Content length is missing or too large.")
                value = json.loads(self.rfile.read(size)) if size else {}
                if not isinstance(value, dict):
                    raise ValueError("JSON body must be an object.")
                return value
            except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
                raise LoadError("invalid_request", "Invalid JSON request.") from exc

        def do_GET(self):
            if urlsplit(self.path).path == "/status":
                self._send(200, runtime.snapshot())
            else:
                self._send(404, {"status": "error", "error": _error("validation", "not_found", "Unknown endpoint.")})

        def do_POST(self):
            path = urlsplit(self.path).path
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
                    runtime.stop.set()
                    with runtime.lock:
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
                    job = {"worker_job_id": uuid.uuid4().hex,
                           "job_id": job_id, "command": command,
                           "state": "queued", "timestamp_utc": _utc()}
                    runtime.active_job = job
                runtime.emit("job_accepted", worker_job_id=job["worker_job_id"], job_id=job_id, command=command)
                thread = threading.Thread(target=runtime.run_job, args=(job, arguments, context), daemon=True)
                runtime.job_thread = thread
                thread.start()
                self._send(202, {"schema_version": 3, "run_id": runtime.run_id,
                                 "status": "accepted", "command": command,
                                 "job_id": job_id, "kind": kind,
                                 "worker_job_id": job["worker_job_id"], "error": None})
            except LoadError as exc:
                rejection = exc.code in {"busy", "stopping", "input_on_not_allowed", "confirmation_required"}
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
                         features={"common.async_jobs": {"contract_version": 1}})
            server.serve_forever(poll_interval=0.05)
            if runtime.job_thread is not None:
                runtime.job_thread.join()
            runtime.emit("summary", ok=runtime.fatal_error is None,
                         fatal_error=runtime.fatal_error,
                         last_job=runtime.last_job, exit_code=0 if runtime.fatal_error is None else 3)
            return 0 if runtime.fatal_error is None else 3
    except OSError as exc:
        runtime.emit("error", error=_error("worker", "listen_failed", "Worker HTTP bind failed.",
                                           {"exception_type": type(exc).__name__}))
        runtime.emit("summary", ok=False,
                     fatal_error=_error("worker", "listen_failed", "Worker HTTP bind failed."),
                     exit_code=3)
        return 3
