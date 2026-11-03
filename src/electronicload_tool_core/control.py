"""Fixed-CC and deliberate input switching for the single verified IT8812C profile.

No hidden write retry, automatic input OFF or VISA connection selection.
"""

from decimal import Decimal, InvalidOperation
import math
import re
from threading import Event
from time import monotonic, sleep

from .errors import LoadError, communication_error
from .identity import MODEL_ID, parse_identity
from .safety import SafetyLimits, load_safety_config
from .visa import open_session, validate_connection
from .simulator import ControlSimulator

CONTROL_COMMANDS = ("set-cc", "input-on", "input-off")
# These commands are not yet Product LIVE validated. Validation is opt-in and
# never permitted from the Worker. Offline and simulator access is independent.
PRODUCT_LIVE_COMMANDS = frozenset()
RANGE_LIMITS = {"low": 6.0, "high": 60.0}
RANGE_RESOLUTION = {"low": Decimal("0.0001"), "high": Decimal("0.001")}
INPUT_STATE_CONFIRM_TIMEOUT_S = 2.0
INPUT_STATE_POLL_INTERVAL_S = 0.1
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?\Z")


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise LoadError("invalid_current", "Current must be a finite numeric value.")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise LoadError("invalid_current", "Current must be a finite numeric value.") from exc
    if not result.is_finite():
        raise LoadError("invalid_current", "Current must be finite.")
    return result


def validate_current(value: object, range_name: str, limits: SafetyLimits) -> Decimal:
    if range_name not in RANGE_LIMITS:
        raise LoadError("invalid_range", "Range must be low or high.")
    current = _decimal(value)
    if current < 0 or current > Decimal(str(RANGE_LIMITS[range_name])):
        raise LoadError("current_out_of_range", "Current is outside the selected range.")
    if current > Decimal(str(limits.max_current)):
        raise LoadError("safety_limit_exceeded", "Current exceeds the configured safety limit.")
    if current % RANGE_RESOLUTION[range_name] != 0:
        raise LoadError("invalid_current_resolution", "Current exceeds the range's programming resolution.")
    return current


def _query(session, query: str) -> str:
    try:
        return str(session.query(query)).strip()
    except Exception as exc:
        raise communication_error("query_failed", f"Query {query} failed.", exc) from exc


def _write(session, command: str) -> None:
    try:
        session.write(command)
    except Exception as exc:
        raise communication_error("write_failed", "An instrument write failed.", exc) from exc


def _number(session, query: str) -> float:
    raw = _query(session, query)
    if not NUMBER.fullmatch(raw):
        raise LoadError("invalid_response", "Instrument returned an invalid numeric value.", 3,
                        query=query)
    number = float(raw)
    if not math.isfinite(number):
        raise LoadError("invalid_response", "Instrument returned a non-finite value.", 3,
                        query=query)
    return number


def _flag(session, query: str) -> str:
    raw = _query(session, query)
    if raw not in {"0", "1"}:
        raise LoadError("invalid_response", "Instrument returned an unknown state.", 3,
                        query=query)
    return "ON" if raw == "1" else "OFF"


def _input_off(session) -> None:
    if _flag(session, "INP:STAT?") != "OFF":
        raise LoadError("input_not_off", "Input must be confirmed OFF before configuration.")


def _confirm_input_state(session, expected: str) -> bool:
    """Observe a write's state transition without sending the write again.

    Keep the 2-second polling budget separate from the existing VISA timeout
    for an individual query, which can itself take up to 5 seconds.
    """
    deadline = monotonic() + INPUT_STATE_CONFIRM_TIMEOUT_S
    while True:
        observed = _flag(session, "INP:STAT?")
        now = monotonic()
        if observed == expected:
            return now <= deadline
        if now >= deadline:
            return False
        sleep(min(INPUT_STATE_POLL_INTERVAL_S, deadline - now))


def _range_name(value: float) -> str:
    """Classify the requested numeric range using IT8812C range selection.

    IT8800 Programming Guide: CURR:RANG accepts 0..MAX and selects the
    smallest range containing that value (highest resolution on overlap).
    CURR:RANG? returns an NR3 numeric value, not necessarily a range ceiling.
    A zero reply alone is *not* proof that a requested nonzero CC level applied.
    """
    if 0 <= value <= RANGE_LIMITS["low"]:
        return "low"
    if RANGE_LIMITS["low"] < value <= RANGE_LIMITS["high"]:
        return "high"
    raise LoadError("invalid_range_readback", "Current range readback is outside model limits.",
                    3, range_readback_a=value)


def _current_within(actual: float, wanted: Decimal, range_name: str) -> bool:
    # One half of the documented programming resolution, plus float conversion epsilon.
    return abs(Decimal(str(actual)) - wanted) <= RANGE_RESOLUTION[range_name] / 2


def _settings(session) -> dict:
    # Read both numeric settings before classifying range: if either readback
    # fails, preserve the evidence that explains a rejected setting.
    fixed = _query(session, "FUNC:MODE?").upper()
    function = _query(session, "FUNC?").upper()
    raw_range = _number(session, "CURR:RANG?")
    current = _number(session, "CURR?")
    try:
        range_name = _range_name(raw_range)
    except LoadError as exc:
        exc.details["current_readback_a"] = current
        raise
    return {"function_mode": fixed, "function": function, "range": range_name,
            "range_readback_a": raw_range, "current_a": current}


def _check_settings(session, settings: dict, limits: SafetyLimits) -> None:
    if settings["function_mode"] not in {"FIX", "FIXED"}:
        raise LoadError("unsafe_mode", "Input ON requires fixed mode.")
    if settings["function"] not in {"CURR", "CURRENT"}:
        raise LoadError("unsafe_mode", "Input ON requires CC mode.")
    actual = settings["current_a"]
    if actual < 0 or actual > RANGE_LIMITS[settings["range"]] or actual > limits.max_current:
        raise LoadError("safety_limit_exceeded", "Current exceeds range or safety limits.")


def _check_input_on_conditions(session, limits: SafetyLimits, stop: Event | None) -> dict:
    if stop is not None and stop.is_set():
        raise LoadError("stopping", "Input ON was blocked because Worker is stopping.")
    state = _flag(session, "INP:STAT?")
    if state != "OFF":
        raise LoadError("already_on", "Input is already ON.")
    settings = _settings(session)
    _check_settings(session, settings, limits)
    if _flag(session, "TRAN?") != "OFF" or _flag(session, "INP:SHOR:STAT?") != "OFF":
        raise LoadError("unsafe_mode", "Transient and Short must be OFF.")
    if stop is not None and stop.is_set():
        raise LoadError("stopping", "Input ON was blocked because Worker is stopping.")
    return settings


def _perform(session, command: str, *, current: object | None,
             range_name: str | None, limits: SafetyLimits | None,
             confirmed: bool, stop: Event | None) -> dict:
    parse_identity(_query(session, "*IDN?"))
    if command == "set-cc":
        if limits is None:
            raise LoadError("missing_safety_config", "CC requires a safety config.")
        requested = validate_current(current, range_name, limits)
        completed = []
        # IT8812C hardware rejects nonzero CURR after CURR:RANG MIN
        # (error 120). A numeric selector equal to the requested low-range
        # setpoint was verified on physical hardware with Input OFF.
        # Leave the high-range MAX path unchanged; it is not yet LIVE-tested.
        range_command = (f"CURR:RANG {requested:f}" if range_name == "low"
                         else "CURR:RANG MAX")
        sequence = (("FUNC:MODE FIX", "fixed_mode"), ("FUNC CURR", "cc_mode"),
                    (range_command, "range"), (f"CURR {requested:f}", "current"))
        try:
            # Remote is a control-state change: never send it for a read-only
            # operation or before a fresh physical Input OFF confirmation.
            _input_off(session)
            _write(session, "SYST:REM")
            for action, name in sequence:
                _input_off(session)
                _write(session, action)
                completed.append(name)
            settings = _settings(session)
            if (settings["function_mode"] not in {"FIX", "FIXED"}
                or settings["function"] not in {"CURR", "CURRENT"}
                or settings["range"] != range_name
                or not _current_within(settings["current_a"], requested, range_name)):
                raise LoadError("readback_mismatch", "CC setting did not match readback.", 3,
                                requested_current_a=float(requested),
                                requested_range=range_name,
                                observed=settings)
            # A successful readback of settings does not itself prove Input OFF.
            _input_off(session)
        except LoadError as exc:
            exc.details["completed_steps"] = completed
            raise
        return {"input_state": "OFF", **settings}
    if command == "input-on":
        if not confirmed:
            raise LoadError("confirmation_required", "Input ON needs explicit confirmation.")
        if limits is None:
            raise LoadError("missing_safety_config", "Input ON requires a safety config.")
        _check_input_on_conditions(session, limits, stop)
        # The remote transition might change hardware state. Recheck every
        # condition afterward, including the Worker stop gate, before INP ON.
        _write(session, "SYST:REM")
        settings = _check_input_on_conditions(session, limits, stop)
        try:
            _write(session, "INP ON")
            confirmed_on = _confirm_input_state(session, "ON")
        except LoadError as exc:
            exc.details.update(input_state="UNKNOWN", input_on_attempted=True)
            raise
        if not confirmed_on:
            raise LoadError("input_on_not_confirmed", "Input ON was not confirmed.", 3,
                            input_state="UNKNOWN", input_on_attempted=True)
        return {"input_state": "ON", **settings}
    # The explicit OFF operation must also enter remote mode to be effective,
    # and never assumes VISA close or remote/local release switches Input OFF.
    try:
        _write(session, "SYST:REM")
        _write(session, "INP OFF")
        confirmed_off = _confirm_input_state(session, "OFF")
    except LoadError as exc:
        exc.details["input_state"] = "UNKNOWN"
        raise
    if not confirmed_off:
        raise LoadError("input_off_not_confirmed", "Input OFF was not confirmed.", 3,
                        input_state="UNKNOWN")
    return {"input_state": "OFF"}


def execute(command: str, *, mode: str = "live", resource: str | None = None,
            backend: str = "system", model_id: str = MODEL_ID,
            safety_config: str | None = None, limits: SafetyLimits | None = None,
            current: object | None = None, range_name: str | None = None,
            confirmed: bool = False, validation: bool = False,
            stop: Event | None = None, simulator: ControlSimulator | None = None) -> dict:
    if command not in CONTROL_COMMANDS or mode not in {"live", "simulate", "dry-run"}:
        raise LoadError("argument_error", "Unknown control command or mode.")
    if model_id != MODEL_ID:
        raise LoadError("unsupported_model", "The requested profile is not registered.")
    validate_connection(resource, backend, required=mode == "live")
    if command == "set-cc" and current is None:
        raise LoadError("argument_error", "CC requires current and low/high range.")
    if mode == "live":
        if command not in PRODUCT_LIVE_COMMANDS and not validation:
            raise LoadError("unsupported_live_scope", "Control is pending LIVE validation.")
        effective_limits = limits or (load_safety_config(safety_config)
                                       if command != "input-off" else None)
    else:
        effective_limits = limits or SafetyLimits(60.0)
    if command == "set-cc":
        validate_current(current, range_name, effective_limits)
    if mode == "dry-run":
        return {"command": command, "hardware_access": False, "range": range_name,
                "current_a": float(_decimal(current)) if current is not None else None,
                "input_on_requires_confirmation": command == "input-on"}
    if mode == "simulate":
        return _perform(simulator or ControlSimulator(), command, current=current,
                        range_name=range_name, limits=effective_limits,
                        confirmed=confirmed, stop=stop)
    with open_session(resource) as (session, runtime):
        result = _perform(session, command, current=current, range_name=range_name,
                          limits=effective_limits, confirmed=confirmed, stop=stop)
        result["visa"] = runtime
    return result
