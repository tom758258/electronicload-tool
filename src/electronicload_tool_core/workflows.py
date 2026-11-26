"""Bounded measurement and fixed-CC workflows."""

import csv
import math
import operator
from decimal import Decimal
from pathlib import Path
from threading import TIMEOUT_MAX
from time import monotonic, monotonic_ns, sleep

from . import control
from .errors import LoadError
from .hardware_validation import sequence_fixed_setup, sequence_input_on
from .identity import MODEL_ID, parse_identity
from .readonly import BASIC_MEASUREMENTS, QUERIES, read_measurement
from .simulator import ControlSimulator
from .visa import (open_request_session, open_session, require_trusted_session,
                   validate_request_connection)

CONTRIBUTOR_SCOPE = "workflow"
MEASUREMENT_COMMANDS = ("measure-log", "measure-until")
CC_COMMANDS = ("cc-timed-load", "cc-discharge")
CC_OPTIONS = ("range", "confirm_input_on")
ARGUMENTS = {
    "measure-log": (("interval_s",), ("count", "duration_s", "csv")),
    "measure-until": (("interval_s", "metric", "operator", "threshold", "timeout_s"), ("csv",)),
    "cc-timed-load": (("current", "duration_s"), CC_OPTIONS),
    "cc-discharge": (("current", "cutoff_voltage", "timeout_s"),
                     (*CC_OPTIONS, "interval_s", "cutoff_capacity_ah", "csv")),
}
COMMANDS = tuple(ARGUMENTS)
OPERATORS = {"lt": operator.lt, "le": operator.le, "gt": operator.gt, "ge": operator.ge}
MAX_INTERVAL = 3600
MIN_INTERVAL = Decimal("0.001")
MAX_SAMPLES = 10000
UNTRUSTED = {"query_failed", "write_failed", "invalid_response", "invalid_range_readback",
             "invalid_identity", "identity_mismatch", "trace_session_untrusted"}
CONTRIBUTOR_CURRENT = Decimal("0.1")
CONTRIBUTOR_CAPACITY = Decimal("0.00010")
CONTRIBUTOR_VOLTAGE = Decimal("4.5")
CONTRIBUTOR_INTERVAL = Decimal("0.5")
CONTRIBUTOR_TIMED_SECONDS = 5
CONTRIBUTOR_DISCHARGE_SECONDS = 6
CONTRIBUTOR_MAX_ON_SECONDS = 10
CONTRIBUTOR_IO_TIMEOUT_MS = 500
# The contributor accepts sampled current evidence, not a continuous external trace.
CURRENT_TOLERANCE = Decimal("0.01")
CURRENT_SAMPLE_GAP_S = 1.0
CURRENT_FIRST_SAMPLE_S = 2.0


class _BoundedCCSession:
    """Limit physical Workflow I/O once the CC Input ON write is dispatched."""

    def __init__(self, session):
        self.session = session
        self.deadline = None
        self.on_dispatch = None
        self.off_write_started_ns = None
        self.off_reply_received_ns = None
        self.timeout_ms = None
        self.sample_deadline = None

    def arm(self, on_dispatch):
        self.session.timeout = self.timeout_ms = CONTRIBUTOR_IO_TIMEOUT_MS
        self.on_dispatch = on_dispatch

    def _budget(self, *, query=False):
        if self.deadline is None:
            return
        deadline = min(self.deadline, self.sample_deadline) if self.sample_deadline is not None else self.deadline
        remaining = deadline - monotonic()
        # A VISA query can include a write, the configured delay and a read.
        travel = ((remaining - self.session.query_delay) / 2 if query else remaining)
        if travel < 0.001:
            raise LoadError("workflow_timeout", "Workflow reached its maximum Input ON deadline.", 3)
        timeout_ms = min(CONTRIBUTOR_IO_TIMEOUT_MS, int(travel * 1000))
        if timeout_ms != self.timeout_ms:
            self.session.timeout = self.timeout_ms = timeout_ms

    def query(self, command):
        self._budget(query=True)
        response = self.session.query(command)
        if (self.off_write_started_ns is not None and command == "INP:STAT?"
                and str(response).strip() == "0"):
            # Record the received OFF reply, even if Core rejects it as late.
            # This is diagnostic evidence, never an accepted OFF confirmation.
            self.off_reply_received_ns = monotonic_ns()
        return response

    def write(self, command):
        if command == "INP ON" and self.on_dispatch is not None:
            started_ns = monotonic_ns()
            self.deadline = started_ns / 1e9 + CONTRIBUTOR_MAX_ON_SECONDS
            self.on_dispatch(started_ns)
            self.on_dispatch = None
        else:
            self._budget()
            if command == "INP OFF" and self.deadline is not None:
                self.off_write_started_ns = monotonic_ns()
        return self.session.write(command)


def _validate_contributor_cc(command, settings, mode):
    expected = {"current": CONTRIBUTOR_CURRENT, "range": "low"}
    if command == "cc-timed-load":
        expected["duration_s"] = CONTRIBUTOR_TIMED_SECONDS
    else:
        expected.update(cutoff_voltage=CONTRIBUTOR_VOLTAGE, cutoff_capacity_ah=CONTRIBUTOR_CAPACITY,
                        interval_s=CONTRIBUTOR_INTERVAL, timeout_s=CONTRIBUTOR_DISCHARGE_SECONDS)
    if "csv" in settings or any(settings.get(key) != value for key, value in expected.items()):
        raise LoadError("unsupported_live_scope", "Use the bounded Contributor CC trial settings.")
    if mode == "live" and not settings.get("confirm_input_on"):
        raise LoadError("confirmation_required", "Confirm the 5 V, 0.8 A limited source and independent 10 s cutoff.")


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
        if field in settings and settings[field] <= 0:
            raise LoadError("argument_error", f"{field} must be positive.")
    if "interval_s" in settings:
        interval = settings["interval_s"]
        if not MIN_INTERVAL <= interval <= MAX_INTERVAL:
            raise LoadError("argument_error", f"interval_s must be from {MIN_INTERVAL} to {MAX_INTERVAL} seconds.")
    if command == "measure-log":
        if ("count" in settings) == ("duration_s" in settings):
            raise LoadError("argument_error", "Supply exactly one of count or duration_s.")
        if "count" in settings:
            count = settings["count"]
            if type(count) is not int or not 1 <= count <= MAX_SAMPLES:
                raise LoadError("argument_error", f"count must be an integer from 1 to {MAX_SAMPLES}.")
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


def _measure(session, command, settings, quantities, stop, result, record_row, *, load_started=None):
    started = monotonic()
    time_limit = settings.get("duration_s", settings.get("timeout_s"))
    time_origin = started if load_started is None else load_started
    deadline = time_origin + float(time_limit) if time_limit is not None else math.inf
    interval = float(settings["interval_s"])
    tick = 0
    discharge = command == "cc-discharge"
    ampere_seconds = Decimal(0)
    cutoff_ah = settings.get("cutoff_capacity_ah")
    cutoff_ampere_seconds = cutoff_ah * 3600 if cutoff_ah is not None else None
    previous_time = previous_current = None
    while True:
        due = started + tick * interval
        _wait(stop, max(0, min(due, deadline) - monotonic()))
        if monotonic() >= deadline:
            break
        cutoff_reason = None
        if discharge:
            reading = {}
            sampled = None
            # Check each cutoff before asking for the next measurement.
            for quantity in quantities:
                if monotonic() >= deadline or (stop is not None and stop.is_set()):
                    break
                values = _sample(session, (quantity,))
                observed = monotonic()
                if observed >= deadline:
                    break
                reading.update(values)
                sampled = observed
                if quantity == "voltage" and Decimal(str(values["voltage_v"])) <= settings["cutoff_voltage"]:
                    cutoff_reason = "voltage_cutoff"
                    break
                if quantity == "current":
                    current = Decimal(str(values["current_a"]))
                    if load_started is not None:
                        if (abs(current - CONTRIBUTOR_CURRENT) > CURRENT_TOLERANCE
                                or read_measurement(session, "input-state")["input_state"] != "ON"):
                            raise LoadError("workflow_load_unconfirmed", "Discharge current or Input ON was lost.", 3)
                        if monotonic() >= deadline:
                            raise LoadError("workflow_timeout", "Discharge sample exceeded its deadline.", 3)
                    if previous_time is not None:
                        elapsed = Decimal(str(observed - previous_time))
                        ampere_seconds += (previous_current + current) * elapsed / 2
                    previous_time, previous_current = observed, current
                    if cutoff_ampere_seconds is not None and ampere_seconds >= cutoff_ampere_seconds:
                        cutoff_reason = "capacity_cutoff"
                        break
        else:
            reading = _sample(session, quantities)
            sampled = monotonic()
            if sampled >= deadline:
                sampled = None
        if sampled is None:
            _check_stop(stop)
            break
        if discharge:
            capacity_ah = float(ampere_seconds / 3600)
            if not math.isfinite(capacity_ah):
                raise LoadError("capacity_overflow", "Integrated capacity exceeds the numeric range.", 3)
            result["capacity_ah"] = capacity_ah
        result["sample_count"] += 1
        result["last_measurement"] = reading
        if record_row is not None:
            record_row({"sample_index": result["sample_count"], "elapsed_s": sampled - started,
                        **reading, **({"capacity_ah": result["capacity_ah"]} if discharge else {})})
        _check_stop(stop)
        if command == "measure-log" and result["sample_count"] == settings.get("count"):
            return "completed"
        if command == "measure-until":
            field = BASIC_MEASUREMENTS[settings["metric"]][0]
            if OPERATORS[settings["operator"]](Decimal(str(reading[field])), settings["threshold"]):
                return "condition_met"
        if cutoff_reason is not None:
            return cutoff_reason
        if result["sample_count"] == MAX_SAMPLES:
            return "sample_limit_reached"
        # Missed intervals are skipped to keep the sampling cadence without catch-up bursts.
        tick = max(tick + 1, math.floor((monotonic() - started) / interval) + 1)
    if "duration_s" in settings:
        return "duration_elapsed"
    raise LoadError("workflow_timeout", "Workflow reached its time limit.", 3)


def _run_cc(session, settings, stop, result, run_load, *, on_write_started=None):
    cleanup = result["cleanup"]
    setup_in_progress = True
    initial_off = False
    remote_attempted = False
    failure = None
    timed = isinstance(session, _BoundedCCSession)

    def observe(state):
        result["input_state"] = state

    def remote_attempt():
        nonlocal remote_attempted
        _check_stop(stop)
        remote_attempted = True
        observe("UNKNOWN")

    def dispatched(started_ns):
        cleanup["required"] = True
        observe("UNKNOWN")
        result["on_write_started_ns"] = started_ns
        if on_write_started is not None:
            on_write_started(started_ns)

    def input_on_attempt():
        _check_stop(stop)
        if timed:
            session.arm(dispatched)
            _check_stop(stop)
        else:
            cleanup["required"] = True
            observe("UNKNOWN")

    try:
        _check_stop(stop)
        require_trusted_session(session.session if timed else session)
        parse_identity(control._query(session, "*IDN?"))
        observe(read_measurement(session, "input-state")["input_state"])
        if result["input_state"] != "OFF":
            raise LoadError("input_not_off", "Switch off the load before starting the Workflow.")
        initial_off = True
        # The established Sequence setup selects and verifies Fixed CC Low
        # itself, including the original mode's interlocks, before Input ON.
        if timed:
            control._fixed_setup_guard(session, stop, fixed_only=True, on_input_state=observe)
            if control._check_mode_change(session):
                raise LoadError("unsafe_mode", "Disable CR-LED before Contributor CC setup.")
        cutoff = settings.get("cutoff_voltage")
        if cutoff is not None:
            result["initial_voltage_v"] = _sample(session, ("voltage",))["voltage_v"]
            _check_stop(stop)
        if cutoff is not None and Decimal(str(result["initial_voltage_v"])) <= cutoff:
            result["stop_reason"] = "voltage_cutoff"
        else:
            cc = {"current": settings["current"], "range": settings["range"]}
            sequence_fixed_setup(session, "set-cc", cc, stop, on_remote_attempt=remote_attempt,
                                 on_input_state=observe)
            remote_attempted = False
            sequence_input_on(session, ("set-cc", cc), stop, on_attempt=input_on_attempt,
                              on_remote_attempt=remote_attempt, on_input_state=observe)
            observe("ON")
            setup_in_progress = False
            if timed:
                _check_stop(stop)
            result["stop_reason"] = run_load()
    except LoadError as exc:
        failure = LoadError("stopping", str(exc), 3, **exc.details) if exc.code == "stopping" else exc
    except KeyboardInterrupt:
        failure = LoadError("interrupted", "Workflow was interrupted.", 3,
                            communication_untrusted=setup_in_progress)
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
            if timed:
                off_confirmed_ns = monotonic_ns()
                if off_confirmed_ns - result["on_write_started_ns"] > CONTRIBUTOR_MAX_ON_SECONDS * 10**9:
                    raise LoadError("workflow_timeout", "Input OFF was confirmed after the maximum loading time.", 3)
                result.update(off_write_started_ns=session.off_write_started_ns, off_confirmed_ns=off_confirmed_ns)
        except (Exception, KeyboardInterrupt) as exc:
            if timed and session.off_write_started_ns is not None:
                result["off_write_started_ns"] = session.off_write_started_ns
                if session.off_reply_received_ns is not None:
                    result["off_reply_received_ns"] = session.off_reply_received_ns
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
    remaining = float(duration_s)
    deadline = monotonic() + remaining
    # Leave headroom for the platform timer's absolute-deadline conversion.
    wait_chunk = TIMEOUT_MAX / 2
    while remaining > wait_chunk:
        _wait(stop, wait_chunk)
        remaining = deadline - monotonic()
    _wait(stop, max(0, remaining))
    return "duration_elapsed"


def _timed_current_samples(session, stop, result, start_ns):
    deadline_ns = start_ns + CONTRIBUTOR_TIMED_SECONDS * 10**9
    samples = result["current_samples"]
    gap_ns = int(CURRENT_SAMPLE_GAP_S * 10**9)
    first_ns = int(CURRENT_FIRST_SAMPLE_S * 10**9)
    session.sample_deadline = deadline_ns / 1e9
    try:
        # Never start a final query after the authorized OFF schedule.
        # Query intervals are evidence bounds, not exact physical sample times.
        minimum_query_ns = int(max(0.05, 2 * session.session.query_delay + 0.05) * 1e9)
        while deadline_ns - monotonic_ns() > minimum_query_ns:
            _check_stop(stop)
            sample_started_ns = monotonic_ns()
            current = _sample(session, ("current",))["current_a"]
            input_started_ns = monotonic_ns()
            state = read_measurement(session, "input-state")["input_state"]
            completed_ns = monotonic_ns()
            elapsed_ns = completed_ns - start_ns
            samples.append({"sample_started_ns": sample_started_ns, "input_query_started_ns": input_started_ns,
                            "sample_completed_ns": completed_ns, "elapsed_s": elapsed_ns / 1e9,
                            "current_a": current, "input_state": state})
            if (completed_ns >= deadline_ns or state != "ON"
                    or abs(Decimal(str(current)) - CONTRIBUTOR_CURRENT) > CURRENT_TOLERANCE):
                raise LoadError("workflow_load_unconfirmed", "Timed Load lost current or Input ON.", 3)
            if (len(samples) == 1 and elapsed_ns > first_ns
                    or input_started_ns - sample_started_ns > gap_ns
                    or completed_ns - input_started_ns > gap_ns
                    or len(samples) > 1 and (
                        input_started_ns - samples[-2]["sample_started_ns"] > gap_ns
                        or completed_ns - samples[-2]["input_query_started_ns"] > gap_ns)):
                raise LoadError("workflow_load_unconfirmed", "Timed Load sample coverage is insufficient.", 3)
            # The existing interval is a sample-start cadence, not a
            # full extra sleep after the two queries. Count query time.
            now_ns = monotonic_ns()
            wait_ns = max(0, int(CONTRIBUTOR_INTERVAL * 10**9) - (now_ns - sample_started_ns))
            if deadline_ns - now_ns <= wait_ns + minimum_query_ns:
                break
            if wait_ns:
                _wait(stop, wait_ns / 1e9)
        if len(samples) < 2 or deadline_ns - samples[-1]["sample_started_ns"] > gap_ns:
            raise LoadError("workflow_load_unconfirmed", "Timed Load final sample is too far from scheduled OFF.", 3)
    finally:
        session.sample_deadline = None
    while (remaining_ns := deadline_ns - monotonic_ns()) > 0:
        _wait(stop, remaining_ns / 1e9)
    _check_stop(stop)
    return "duration_elapsed"


def _run(session, command, settings, stop, result, quantities, record_row, *, require_input_off=False,
         on_write_started=None):
    if command in CC_COMMANDS:
        if require_input_off:
            session = _BoundedCCSession(session)

        def run_load():
            start = result["on_write_started_ns"] / 1e9 if require_input_off else None
            if command == "cc-timed-load":
                if start is not None:
                    return _timed_current_samples(session, stop, result, result["on_write_started_ns"])
                return _wait_load(stop, settings["duration_s"])
            return _measure(session, command, settings, quantities, stop, result, record_row, load_started=start)

        _run_cc(session, settings, stop, result, run_load, on_write_started=on_write_started)
        if require_input_off and command == "cc-timed-load":
            off_ns = result["off_write_started_ns"]
            final_sample = result["current_samples"][-1]
            tail_ns = off_ns - final_sample["sample_started_ns"]
            result["load_evidence_tail_ns"] = tail_ns
            if (off_ns < result["on_write_started_ns"] + CONTRIBUTOR_TIMED_SECONDS * 10**9
                    or off_ns < final_sample["sample_completed_ns"]
                    or tail_ns > int(CURRENT_SAMPLE_GAP_S * 10**9)):
                raise LoadError("workflow_load_unconfirmed", "OFF schedule or final sample coverage is insufficient.", 3)
            # Contributor functional acceptance: schedule plus bounded
            # instrument samples, not proof of uninterrupted physical load.
            result["load_duration_confirmed"] = True
            result["load_evidence_resolution_ns"] = int(CURRENT_SAMPLE_GAP_S * 10**9)
            result["measured_current_a"] = final_sample["current_a"]
        if require_input_off and command == "cc-discharge" and result["stop_reason"] != "capacity_cutoff":
            raise LoadError("capacity_cutoff_unconfirmed", "Contributor discharge did not reach the capacity cutoff.", 3)
    else:
        _check_stop(stop)
        require_trusted_session(session)
        parse_identity(control._query(session, "*IDN?"))
        if require_input_off:
            result.update(read_measurement(session, "input-state"))
            if result["input_state"] != "OFF":
                raise LoadError("input_not_off", "Input must be confirmed OFF before Workflow sampling.")
        result["stop_reason"] = _measure(session, command, settings, quantities, stop, result, record_row)


def execute(command, *, mode="live", arguments, model_id=MODEL_ID, backend="system",
            resource=None, simulator=None, stop=None, hardware_validation=None, asrl=None,
            on_write_started=None):
    settings = validate_arguments(command, arguments, mode=mode)
    if model_id != MODEL_ID:
        raise LoadError("unsupported_model", "The requested model profile is not registered.")
    contributor = hardware_validation == CONTRIBUTOR_SCOPE
    if hardware_validation is not None and (not contributor or mode == "simulate" or simulator is not None):
        raise LoadError("argument_error", "Workflow contributor validation accepts only live or dry-run.")
    if asrl is not None and not contributor:
        raise LoadError("argument_error", "ASRL Workflow options require the contributor scope.")
    if mode == "live" and not contributor:
        raise LoadError("unsupported_live_scope", "Product LIVE is not open for Workflows.")
    validate_request_connection(resource, backend, mode=mode, asrl=asrl, allowed=contributor)
    contributor_cc = contributor and command in CC_COMMANDS
    if contributor_cc:
        _validate_contributor_cc(command, settings, mode)
    quantities = tuple(BASIC_MEASUREMENTS)
    if command == "measure-until":
        quantities = (settings["metric"],)
    if contributor_cc and command == "cc-timed-load":
        quantities = ("current",)
    marker = {"validation_only": True, "validation_scope": hardware_validation} if contributor else {}
    if mode == "dry-run":
        queries = list(QUERIES["input-state"])
        if contributor_cc:
            queries += ["FUNC:MODE?", "FUNC?", "CURR:RANG?", "CURR?", "TRAN?", "INP:SHOR:STAT?", "INP:TIM?"]
        queries += [BASIC_MEASUREMENTS[item][1] for item in quantities]
        return {"arguments": {key: str(value) if isinstance(value, Decimal) else value
                              for key, value in settings.items() if key not in {"confirm_input_on", "csv"}},
                "csv_requested": "csv" in settings, "hardware_access": False,
                "input_on_requires_confirmation": command in CC_COMMANDS, **marker,
                **({"queries": queries} if contributor else {}),
                **({"planned_writes": ["SYST:REM", "FUNC:MODE FIX", "FUNC CURR", "CURR:RANG 0.1", "CURR 0.1",
                                       "SYST:REM", "INP ON", "SYST:REM", "INP OFF"],
                    "max_input_on_s": CONTRIBUTOR_MAX_ON_SECONDS} if contributor_cc else {}),
                **({"operator_current_trace_required": False,
                    "load_evidence_source": "instrument_current_samples",
                    "sample_interval_s": float(CONTRIBUTOR_INTERVAL)}
                   if contributor_cc and command == "cc-timed-load" else {})}

    if simulator is not None and not isinstance(simulator, ControlSimulator):
        raise LoadError("argument_error", "Workflow execution requires a ControlSimulator.")
    result = {"stop_reason": None, **marker}
    if command in CC_COMMANDS:
        result.update(current_a=float(settings["current"]), range=settings["range"],
                      input_state="UNKNOWN",
                      cleanup={"required": False, "attempted": False, "off_confirmed": False})
    if command == "cc-timed-load":
        result["duration_s"] = float(settings["duration_s"])
        if contributor_cc:
            result.update(load_duration_confirmed=False, current_samples=[],
                          load_evidence_source="instrument_current_samples")
    else:
        result.update(sample_count=0, last_measurement=None)
    if command == "cc-discharge":
        result["capacity_ah"] = 0.0

    def run(record_row):
        if not contributor:
            session = simulator if simulator is not None else ControlSimulator()
            _run(session, command, settings, stop, result, quantities, record_row)
            return
        with open_request_session(resource, asrl, open_session) as (session, runtime):
            _run(session, command, settings, stop, result, quantities, record_row, require_input_off=True,
                 on_write_started=on_write_started)
        result["visa"] = runtime

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
                if command == "cc-discharge":
                    # Keep rows in memory and skip export if OFF cleanup is unconfirmed.
                    rows = []
                    failure = None
                    try:
                        run(rows.append)
                    except LoadError as exc:
                        if exc.code == "workflow_cleanup_unconfirmed":
                            raise
                        failure = exc
                    try:
                        writer.writerows(rows)
                    except (OSError, csv.Error, KeyboardInterrupt) as exc:
                        if failure is None:
                            raise
                        failure.details["csv_export_error"] = type(exc).__name__
                    if failure is not None:
                        raise failure
                else:
                    run(writer.writerow)
            result["csv_written"] = True
        else:
            run(None)
    except (LoadError, OSError, csv.Error, KeyboardInterrupt) as exc:
        error = exc
        if not isinstance(exc, LoadError) and isinstance(exc.__context__, LoadError):
            # A CSV failure during unwind must preserve execution and safety evidence.
            error = exc.__context__
            error.details["csv_close_error"] = type(exc).__name__
        elif isinstance(exc, KeyboardInterrupt):
            error = LoadError("interrupted", "Workflow was interrupted.", 3)
        elif isinstance(exc, (OSError, csv.Error)):
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
