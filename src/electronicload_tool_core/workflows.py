"""Bounded, simulator-only measurement and fixed-CC workflows."""

import csv
import math
import operator
from decimal import Decimal
from pathlib import Path
from time import monotonic, sleep

from . import control
from .errors import LoadError
from .hardware_validation import sequence_fixed_setup, sequence_input_on
from .identity import MODEL_ID, parse_identity
from .readonly import BASIC_MEASUREMENTS, read_measurement
from .simulator import ControlSimulator
from .visa import require_trusted_session, validate_connection

CC_COMMANDS = ("cc-timed-load", "cc-discharge")
CC_OPTIONS = ("range", "confirm_input_on")
ARGUMENTS = {
    "measure-log": (("interval_s", "count"), ("csv",)),
    "measure-until": (("interval_s", "metric", "operator", "threshold", "timeout_s"), ("csv",)),
    "cc-timed-load": (("current", "duration_s"), CC_OPTIONS),
    "cc-discharge": (("current", "cutoff_voltage", "timeout_s"),
                     (*CC_OPTIONS, "interval_s", "cutoff_capacity_ah", "csv")),
}
COMMANDS = tuple(ARGUMENTS)
OPERATORS = {"lt": operator.lt, "le": operator.le, "gt": operator.gt, "ge": operator.ge}
MAX_SECONDS = 86400
MAX_INTERVAL = 3600
MIN_INTERVAL = Decimal("0.001")
MAX_SAMPLES = 10000
UNTRUSTED = {"query_failed", "write_failed", "invalid_response", "invalid_range_readback",
             "invalid_identity", "identity_mismatch", "trace_session_untrusted"}


def _number(value, field):
    if type(value) in (int, float, Decimal):
        number = Decimal(str(value)) if type(value) is float else Decimal(value)
        if number.is_finite() and math.isfinite(float(number)):
            return number
    raise LoadError("argument_error", f"{field} must be a finite number.")


def validate_arguments(command, arguments, *, mode):
    """Validate everything that does not depend on the simulator or filesystem."""
    if command not in ARGUMENTS or mode not in ("live", "dry-run", "simulate"):
        raise LoadError("argument_error", "Unknown Workflow command or mode.")
    required, optional = ARGUMENTS[command]
    if (not isinstance(arguments, dict) or not set(required) <= arguments.keys()
            or arguments.keys() - set(required) - set(optional)):
        raise LoadError("argument_error", f"Supply the supported arguments for {command}.")
    settings = dict(arguments)
    if "csv" in settings:
        path = settings["csv"]
        if not isinstance(path, str) or not path.strip() or "\0" in path:
            raise LoadError("argument_error", "CSV requires a non-empty file path.")
    for field in ("current", "interval_s", "threshold", "duration_s", "timeout_s",
                  "cutoff_voltage", "cutoff_capacity_ah"):
        if field in settings:
            settings[field] = _number(settings[field], field)
    if command == "cc-discharge":
        settings.setdefault("interval_s", Decimal(1))
        if not 0 < settings["cutoff_voltage"] <= control.CV_RANGE_LIMITS["high"]:
            raise LoadError("argument_error", "Cutoff voltage must be greater than zero and within the model limit.")
        if "cutoff_capacity_ah" in settings and settings["cutoff_capacity_ah"] <= 0:
            raise LoadError("argument_error", "Capacity cutoff must be positive.")
    if command == "measure-until":
        for field, choices in (("metric", BASIC_MEASUREMENTS), ("operator", OPERATORS)):
            if not isinstance(settings[field], str) or settings[field] not in choices:
                raise LoadError("argument_error", f"{field} must be one of {', '.join(choices)}.")
    for field in ("timeout_s", "duration_s"):
        if field in settings and not MIN_INTERVAL <= settings[field] <= MAX_SECONDS:
            raise LoadError("argument_error", f"{field} must be from {MIN_INTERVAL} to {MAX_SECONDS} seconds.")
    if "interval_s" in settings:
        interval = settings["interval_s"]
        if not MIN_INTERVAL <= interval <= MAX_INTERVAL:
            raise LoadError("argument_error", f"interval_s must be from {MIN_INTERVAL} to {MAX_INTERVAL} seconds.")
        if "timeout_s" in settings and settings["timeout_s"] > interval * MAX_SAMPLES:
            raise LoadError("argument_error", f"A timeout may allow at most {MAX_SAMPLES} samples.")
    if command == "measure-log":
        count = settings["count"]
        if type(count) is not int or not 1 <= count <= MAX_SAMPLES:
            raise LoadError("argument_error", f"count must be an integer from 1 to {MAX_SAMPLES}.")
        if (count - 1) * interval >= MAX_SECONDS:
            raise LoadError("argument_error", f"The last sample must be scheduled before {MAX_SECONDS} seconds.")
    if command in CC_COMMANDS:
        if settings["current"] <= 0:
            raise LoadError("argument_error", "Load current must be positive.")
        settings["range"] = control.resolve_fixed_range(
            "set-cc", settings["current"], settings.get("range", "auto")
        )
        confirmed = settings.get("confirm_input_on", False)
        if type(confirmed) is not bool:
            raise LoadError("argument_error", "confirm_input_on must be a boolean.")
        if mode == "simulate" and not confirmed:
            raise LoadError("confirmation_required", "Workflow Input ON requires explicit run confirmation.")
    return settings


def _check_stop(stop):
    if stop is not None and stop.is_set():
        raise LoadError("stopping", "Workflow was stopped.", 3)


def _wait(stop, seconds):
    if stop is None:
        # Console Ctrl+C can interrupt sleep on Windows Python versions whose locks cannot.
        sleep(seconds)
    else:
        stop.wait(seconds)
    _check_stop(stop)


def _sample(session, quantities):
    try:
        return read_measurement(session, "measure", quantities=quantities)
    except KeyboardInterrupt as exc:
        raise LoadError("interrupted", "Workflow measurement was interrupted.", 3,
                        communication_untrusted=True) from exc


def _measure(session, command, settings, quantities, stop, result, writer):
    started = monotonic()
    deadline = started + float(settings.get("timeout_s", MAX_SECONDS))
    interval = float(settings["interval_s"])
    tick = 0
    discharge = command == "cc-discharge"
    capacity = Decimal(0)
    previous_time = previous_current = None
    while True:
        due = started + tick * interval
        _wait(stop, max(0, min(due, deadline) - monotonic()))
        if monotonic() >= deadline:
            break
        reading = _sample(session, quantities)
        sampled = monotonic()
        if sampled >= deadline:
            _check_stop(stop)
            break
        if discharge:
            current = Decimal(str(reading["current_a"]))
            if previous_time is not None:
                capacity += (previous_current + current) * Decimal(str(sampled - previous_time)) / 7200
            previous_time, previous_current = sampled, current
            capacity_ah = float(capacity)
            if not math.isfinite(capacity_ah):
                raise LoadError("capacity_overflow", "Integrated capacity exceeds the numeric range.", 3)
            result["capacity_ah"] = capacity_ah
        result["sample_count"] += 1
        result["last_measurement"] = reading
        if writer is not None:
            writer.writerow({"sample_index": result["sample_count"], "elapsed_s": sampled - started,
                             **reading, **({"capacity_ah": result["capacity_ah"]} if discharge else {})})
        _check_stop(stop)
        if command == "measure-log" and result["sample_count"] == settings["count"]:
            return "completed"
        if command == "measure-until":
            field = BASIC_MEASUREMENTS[settings["metric"]][0]
            if OPERATORS[settings["operator"]](Decimal(str(reading[field])), settings["threshold"]):
                return "condition_met"
        if discharge:
            if Decimal(str(reading["voltage_v"])) <= settings["cutoff_voltage"]:
                return "voltage_cutoff"
            if "cutoff_capacity_ah" in settings and capacity >= settings["cutoff_capacity_ah"]:
                return "capacity_cutoff"
        # Missed intervals are skipped to keep the sampling cadence without catch-up bursts.
        tick = max(tick + 1, math.floor((monotonic() - started) / interval) + 1)
    raise LoadError("workflow_timeout", "Workflow reached its time limit.", 3)


def _run_cc(session, settings, stop, result, run_load):
    cleanup = result["cleanup"]
    setup_in_progress = True
    initial_off = False
    remote_attempted = False
    failure = None

    def observe(state):
        result["input_state"] = state

    def remote_attempt():
        nonlocal remote_attempted
        _check_stop(stop)
        remote_attempted = True
        observe("UNKNOWN")

    def input_on_attempt():
        _check_stop(stop)
        cleanup["required"] = True
        observe("UNKNOWN")

    try:
        _check_stop(stop)
        require_trusted_session(session)
        parse_identity(control._query(session, "*IDN?"))
        observe(read_measurement(session, "input-state")["input_state"])
        if result["input_state"] != "OFF":
            raise LoadError("input_not_off", "Switch off the load before starting the Workflow.")
        initial_off = True
        cc = {"current": settings["current"], "range": settings["range"]}
        sequence_fixed_setup(session, "set-cc", cc, stop, on_remote_attempt=remote_attempt,
                             on_input_state=observe)
        remote_attempted = False
        sequence_input_on(session, ("set-cc", cc), stop, on_attempt=input_on_attempt,
                          on_remote_attempt=remote_attempt, on_input_state=observe)
        observe("ON")
        setup_in_progress = False
        result["stop_reason"] = run_load()
    except LoadError as exc:
        failure = LoadError("stopping", str(exc), 3, **exc.details) if exc.code == "stopping" else exc
    except KeyboardInterrupt:
        failure = LoadError("interrupted", "Workflow was interrupted.", 3,
                            communication_untrusted=setup_in_progress)
    except OSError as exc:
        failure = LoadError("workflow_csv_failed", "Could not write Workflow CSV.", 3,
                            exception_type=type(exc).__name__)
    except Exception as exc:
        failure = LoadError("execution_failed", "Workflow execution failed.", 3,
                            exception_type=type(exc).__name__)
    if failure and (failure.code in UNTRUSTED or failure.details.get("communication_untrusted")):
        observe("UNKNOWN")
        cleanup["reason"] = "session_untrusted"
    elif cleanup["required"]:
        cleanup["attempted"] = True
        try:
            observe(control._turn_input_off(session)["input_state"])
        except (Exception, KeyboardInterrupt) as exc:
            observe("UNKNOWN")
            if isinstance(exc, LoadError):
                cleanup["error"] = exc.as_dict()
            elif isinstance(exc, KeyboardInterrupt):
                cleanup["error"] = {"code": "interrupted", "message": "OFF cleanup was interrupted."}
            else:
                cleanup["error"] = LoadError("execution_failed", "OFF cleanup failed.", 3,
                                             exception_type=type(exc).__name__).as_dict()
    cleanup["off_confirmed"] = result["input_state"] == "OFF"
    if not cleanup["off_confirmed"] and (
        cleanup["required"] or remote_attempted or initial_off or "reason" in cleanup
    ):
        state = "Input is ON." if result["input_state"] == "ON" else "Input OFF is unconfirmed."
        raise LoadError("workflow_cleanup_unconfirmed", f"{state} Independently switch off the load now.",
                        3, manual_action_required=True,
                        original_error=failure.as_dict() if failure else None)
    if failure:
        raise failure


def _wait_load(stop, duration_s):
    _wait(stop, float(duration_s))
    return "duration_elapsed"


def _run(session, command, settings, stop, result, quantities, writer):
    if command in CC_COMMANDS:
        def run_load():
            if command == "cc-timed-load":
                return _wait_load(stop, settings["duration_s"])
            return _measure(session, command, settings, quantities, stop, result, writer)

        _run_cc(session, settings, stop, result, run_load)
    else:
        _check_stop(stop)
        require_trusted_session(session)
        parse_identity(control._query(session, "*IDN?"))
        result["stop_reason"] = _measure(session, command, settings, quantities, stop, result, writer)


def execute(command, *, mode="live", arguments, model_id=MODEL_ID, backend="system",
            resource=None, simulator=None, stop=None):
    settings = validate_arguments(command, arguments, mode=mode)
    if model_id != MODEL_ID:
        raise LoadError("unsupported_model", "The requested model profile is not registered.")
    if mode == "live":
        raise LoadError("unsupported_live_scope", "Product LIVE is not open for Workflows.")
    validate_connection(resource, backend, required=False)
    if mode == "dry-run":
        return {"arguments": {key: str(value) if isinstance(value, Decimal) else value
                              for key, value in settings.items() if key not in {"confirm_input_on", "csv"}},
                "csv_requested": "csv" in settings, "hardware_access": False,
                "input_on_requires_confirmation": command in CC_COMMANDS}

    if simulator is not None and not isinstance(simulator, ControlSimulator):
        raise LoadError("argument_error", "Workflow execution requires a ControlSimulator.")
    session = simulator if simulator is not None else ControlSimulator()
    result = {"stop_reason": None}
    quantities = tuple(BASIC_MEASUREMENTS)
    if command in CC_COMMANDS:
        result.update(current_a=float(settings["current"]), range=settings["range"],
                      input_state="UNKNOWN",
                      cleanup={"required": False, "attempted": False, "off_confirmed": False})
    if command == "cc-timed-load":
        result["duration_s"] = float(settings["duration_s"])
    else:
        result.update(sample_count=0, last_measurement=None)
    if command == "cc-discharge":
        quantities = ("voltage", "current")
        result["capacity_ah"] = 0.0
    elif command == "measure-until":
        quantities = (settings["metric"],)

    started = monotonic()
    csv_created = False
    try:
        _check_stop(stop)
        if "csv" in settings:
            path = Path(settings["csv"])
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                file = path.open("x", encoding="utf-8", newline="")
            except FileExistsError as exc:
                raise LoadError("workflow_csv_exists", "Workflow CSV already exists.", 3) from exc
            csv_created = True
            with file:
                fields = ["sample_index", "elapsed_s", *(BASIC_MEASUREMENTS[item][0] for item in quantities)]
                if command == "cc-discharge":
                    fields.append("capacity_ah")
                writer = csv.DictWriter(file, fields)
                writer.writeheader()
                file.flush()
                _run(session, command, settings, stop, result, quantities, writer)
            result["csv_written"] = True
        else:
            _run(session, command, settings, stop, result, quantities, None)
    except (LoadError, OSError, KeyboardInterrupt) as exc:
        error = exc
        if isinstance(exc, KeyboardInterrupt):
            error = LoadError("interrupted", "Workflow was interrupted.", 3)
        elif isinstance(exc, OSError):
            if isinstance(exc.__context__, LoadError):
                # Keep the original execution or cleanup error if closing CSV also fails.
                error = exc.__context__
                error.details["csv_close_error"] = type(exc).__name__
            else:
                error = LoadError("workflow_csv_failed", "Could not create or write Workflow CSV.", 3,
                                  exception_type=type(exc).__name__)
        cause = error.details.get("original_error") or {}
        reason = {"workflow_timeout": "timeout", "stopping": "stopped", "interrupted": "interrupted"}
        result["stop_reason"] = result["stop_reason"] or reason.get(cause.get("code", error.code), "error")
        error.details.update(result, elapsed_s=monotonic() - started)
        if csv_created:
            error.details["csv_incomplete"] = True
        if error is exc:
            raise
        raise error from exc
    return {**result, "elapsed_s": monotonic() - started}
