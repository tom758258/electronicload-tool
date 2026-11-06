"""Fixed CC, offline CV/CR/CW preview and deliberate input switching for IT8812C.

No hidden write retry, automatic input OFF or VISA connection selection.
"""

from decimal import Decimal, InvalidOperation
import math
import re
from threading import Event
from time import monotonic, sleep

from .errors import LoadError, communication_error
from .identity import MODEL_ID, parse_identity
from .visa import open_session, validate_connection
from .simulator import ControlSimulator

CONTROL_COMMANDS = ("set-cc", "set-cv", "set-cr", "set-cw", "input-on", "input-off")
# Exact IT8812C Product LIVE control commands; the model, USB/System VISA,
# Explicit Input ON authorization remains enforced.
PRODUCT_LIVE_COMMANDS = frozenset({"set-cc", "input-on", "input-off"})
RANGE_LIMITS = {"low": 6.0, "high": 60.0}
RANGE_RESOLUTION = {"low": Decimal("0.0001"), "high": Decimal("0.001")}
CV_RANGE_LIMITS = {"low": Decimal("18"), "high": Decimal("120")}
CV_RESOLUTION = {"low": Decimal("0.001"), "high": Decimal("0.01")}
CV_MIN_VOLTAGE = Decimal("0.1")
CR_RANGE_LIMITS = {
    "low": (Decimal("0.05"), Decimal("10")),
    "high": (Decimal("10"), Decimal("7500")),
}
# Approved offline CW limits; not evidence of physical programming support.
CW_MAX_POWER = Decimal("250")
CW_RESOLUTION = Decimal("0.01")
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


def validate_current(value: object, range_name: str) -> Decimal:
    if range_name not in RANGE_LIMITS:
        raise LoadError("invalid_range", "Range must be low or high.")
    current = _decimal(value)
    if current < 0 or current > Decimal(str(RANGE_LIMITS[range_name])):
        raise LoadError("current_out_of_range", "Current is outside the selected range.")
    if current % RANGE_RESOLUTION[range_name] != 0:
        raise LoadError("invalid_current_resolution", "Current exceeds the range's programming resolution.")
    return current



def validate_voltage(value: object, range_name: str) -> Decimal:
    if range_name not in CV_RANGE_LIMITS:
        raise LoadError("invalid_range", "Range must be low or high.")
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise LoadError("invalid_voltage", "Voltage must be a finite numeric value.")
    try:
        voltage = Decimal(str(value))
    except InvalidOperation as exc:
        raise LoadError("invalid_voltage", "Voltage must be finite.") from exc
    if not voltage.is_finite():
        raise LoadError("invalid_voltage", "Voltage must be finite.")
    if not CV_MIN_VOLTAGE <= voltage <= CV_RANGE_LIMITS[range_name]:
        raise LoadError("voltage_out_of_range", "Voltage is outside the selected range.")
    if voltage % CV_RESOLUTION[range_name] != 0:
        raise LoadError("invalid_voltage_resolution", "Voltage exceeds range resolution.")
    return voltage


def _offline_cv_settings(session) -> dict:
    # 18/120 readback is only a simulator convention, NOT physical IT8812C evidence.
    raw_range = _number(session, "VOLT:RANG?")
    voltage = _number(session, "VOLT?")
    if raw_range not in (18.0, 120.0):
        raise LoadError("invalid_range_readback", "Unknown simulated CV range.", 3,
                        range_readback_v=raw_range, voltage_readback_v=voltage)
    name = "low" if raw_range == 18.0 else "high"
    if not CV_MIN_VOLTAGE <= Decimal(str(voltage)) <= CV_RANGE_LIMITS[name]:
        raise LoadError("voltage_out_of_range", "Simulated CV readback is out of range.", 3)
    return {"range": name, "range_readback_v": raw_range, "voltage_v": voltage}


def validate_resistance(value: object, range_name: str) -> Decimal:
    if not isinstance(range_name, str) or range_name not in CR_RANGE_LIMITS:
        raise LoadError("invalid_range", "Range must be low or high.")
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise LoadError("invalid_resistance", "Resistance must be a finite numeric value.")
    try:
        resistance = Decimal(str(value))
    except InvalidOperation as exc:
        raise LoadError("invalid_resistance", "Resistance must be finite.") from exc
    if not resistance.is_finite():
        raise LoadError("invalid_resistance", "Resistance must be finite.")
    minimum, maximum = CR_RANGE_LIMITS[range_name]
    if not minimum <= resistance <= maximum:
        raise LoadError("resistance_out_of_range", "Resistance is outside the selected range.")
    # The published 16-bit resolution does not establish a fixed decimal step.
    return resistance


def _offline_cr_settings(session) -> dict:
    # These range ceilings are simulator conventions, not physical SCPI evidence.
    raw_range = _number(session, "RES:RANG?")
    if raw_range not in (10.0, 7500.0):
        raise LoadError("invalid_range_readback", "Unknown simulated CR range.", 3,
                        range_readback_ohm=raw_range)
    name = "low" if raw_range == 10.0 else "high"
    resistance = validate_resistance(_query(session, "RES?"), name)
    return {"range": name, "range_readback_ohm": raw_range,
            "resistance_ohm": float(resistance)}


def validate_power(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise LoadError("invalid_power", "Power must be a finite numeric value.")
    try:
        power = Decimal(str(value))
    except InvalidOperation as exc:
        raise LoadError("invalid_power", "Power must be finite.") from exc
    if not power.is_finite():
        raise LoadError("invalid_power", "Power must be finite.")
    if not 0 <= power <= CW_MAX_POWER:
        raise LoadError("power_out_of_range", "Power must be between 0 and 250 W.")
    # Compare without replacing the original value: never round a requested setpoint.
    if power != power.quantize(CW_RESOLUTION):
        raise LoadError("invalid_power_resolution", "Power must be a multiple of 0.01 W.")
    return power


def _offline_cw_settings(session) -> dict:
    # POW? is an internal simulator convention, not a physical SCPI contract.
    return {"power_w": float(validate_power(_query(session, "POW?")))}


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


def _settings(session, *, allow_cv: bool = False, allow_cr: bool = False,
              allow_cw: bool = False) -> dict:
    fixed = _query(session, "FUNC:MODE?").upper()
    function = _query(session, "FUNC?").upper()
    if function in {"VOLT", "VOLTAGE"}:
        if not allow_cv:
            raise LoadError("unsupported_live_scope",
                            "Physical CV Input ON requires hardware validation.")
        return {"function_mode": fixed, "function": function,
                **_offline_cv_settings(session)}
    if function in {"RES", "RESISTANCE"}:
        if not allow_cr:
            raise LoadError("unsupported_live_scope",
                            "Physical CR Input ON requires hardware validation.")
        return {"function_mode": fixed, "function": function,
                **_offline_cr_settings(session)}
    if function in {"POW", "POWER"}:
        if not allow_cw:
            raise LoadError("unsupported_live_scope",
                            "Physical CW Input ON requires hardware validation.")
        return {"function_mode": fixed, "function": function,
                **_offline_cw_settings(session)}
    if function not in {"CURR", "CURRENT"}:
        raise LoadError("unsafe_mode", "Unsupported fixed load function.")
    # Preserve verified CC range/current readback and diagnostics.
    raw_range = _number(session, "CURR:RANG?")
    current = _number(session, "CURR?")
    try:
        range_name = _range_name(raw_range)
    except LoadError as exc:
        exc.details["current_readback_a"] = current
        raise
    return {"function_mode": fixed, "function": function, "range": range_name,
            "range_readback_a": raw_range, "current_a": current}

def _check_settings(session, settings: dict) -> None:
    if settings["function_mode"] not in {"FIX", "FIXED"}:
        raise LoadError("unsafe_mode", "Input ON requires fixed mode.")
    if settings["function"] in {"VOLT", "VOLTAGE"}:
        validate_voltage(settings["voltage_v"], settings["range"])
        return
    if settings["function"] in {"RES", "RESISTANCE"}:
        validate_resistance(settings["resistance_ohm"], settings["range"])
        return
    if settings["function"] in {"POW", "POWER"}:
        validate_power(settings["power_w"])
        return
    if settings["function"] not in {"CURR", "CURRENT"}:
        raise LoadError("unsafe_mode", "Input ON requires CC, CV, CR or CW mode.")
    actual = settings["current_a"]
    if actual < 0 or actual > RANGE_LIMITS[settings["range"]]:
        raise LoadError("safety_limit_exceeded", "Current exceeds the model range.")

def _check_input_on_conditions(session, stop: Event | None, *, allow_cv: bool,
                               allow_cr: bool, allow_cw: bool) -> dict:
    if stop is not None and stop.is_set():
        raise LoadError("stopping", "Input ON was blocked because Worker is stopping.")
    state = _flag(session, "INP:STAT?")
    if state != "OFF":
        raise LoadError("already_on", "Input is already ON.")
    settings = _settings(session, allow_cv=allow_cv, allow_cr=allow_cr, allow_cw=allow_cw)
    _check_settings(session, settings)
    if _flag(session, "TRAN?") != "OFF" or _flag(session, "INP:SHOR:STAT?") != "OFF":
        raise LoadError("unsafe_mode", "Transient and Short must be OFF.")
    if stop is not None and stop.is_set():
        raise LoadError("stopping", "Input ON was blocked because Worker is stopping.")
    return settings


def _perform(session, command: str, *, current: object | None,
             voltage: object | None, resistance: object | None, power: object | None,
             range_name: str | None, confirmed: bool, stop: Event | None,
             allow_cv: bool, allow_cr: bool, allow_cw: bool) -> dict:
    parse_identity(_query(session, "*IDN?"))
    if command == "set-cc":
        requested = validate_current(current, range_name)
        completed = []
        # IT8812C hardware rejects nonzero CURR after CURR:RANG MIN
        # (error 120). A numeric selector equal to the requested low-range
        # setpoint was verified on physical hardware with Input OFF.
        # Preserve the high-range MAX path.
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
    if command == "set-cv":
        requested = validate_voltage(voltage, range_name)
        _input_off(session)
        # No real CV range SCPI is sent: the guide has conflicting descriptions.
        # The simulation model applies settings internally until hardware proof.
        if not isinstance(session, ControlSimulator):
            raise LoadError("unsupported_live_scope", "CV LIVE range semantics unverified.")
        session.configure_cv(requested, range_name)
        settings = _settings(session, allow_cv=True)
        if (settings["function_mode"] not in {"FIX", "FIXED"}
                or settings["function"] not in {"VOLT", "VOLTAGE"}
                or settings["range"] != range_name
                or abs(Decimal(str(settings["voltage_v"])) - requested)
                > CV_RESOLUTION[range_name] / 2):
            raise LoadError("readback_mismatch", "CV simulation did not match readback.", 3,
                            requested_voltage_v=float(requested), observed=settings)
        _input_off(session)
        return {"input_state": "OFF", **settings}
    if command == "set-cr":
        requested = validate_resistance(resistance, range_name)
        _input_off(session)
        if not isinstance(session, ControlSimulator):
            raise LoadError("unsupported_live_scope", "CR LIVE requires hardware validation.")
        session.configure_cr(requested, range_name)
        settings = _settings(session, allow_cr=True)
        if (settings["function_mode"] not in {"FIX", "FIXED"}
                or settings["function"] not in {"RES", "RESISTANCE"}
                or settings["range"] != range_name
                or validate_resistance(_query(session, "RES?"), range_name) != requested):
            raise LoadError("readback_mismatch", "CR simulation did not match readback.", 3,
                            requested_resistance_ohm=float(requested), observed=settings)
        _input_off(session)
        return {"input_state": "OFF", **settings}
    if command == "set-cw":
        requested = validate_power(power)
        _input_off(session)
        if not isinstance(session, ControlSimulator):
            raise LoadError("unsupported_live_scope", "CW LIVE requires hardware validation.")
        session.configure_cw(requested)
        settings = _settings(session, allow_cw=True)
        if (settings["function_mode"] not in {"FIX", "FIXED"}
                or settings["function"] not in {"POW", "POWER"}
                or Decimal(str(settings["power_w"])) != requested):
            raise LoadError("readback_mismatch", "CW simulation did not match readback.", 3,
                            requested_power_w=float(requested), observed=settings)
        _input_off(session)
        return {"input_state": "OFF", **settings}
    if command == "input-on":
        if not confirmed:
            raise LoadError("confirmation_required", "Input ON needs explicit confirmation.")
        _check_input_on_conditions(session, stop, allow_cv=allow_cv, allow_cr=allow_cr,
                                   allow_cw=allow_cw)
        # The remote transition might change hardware state. Recheck every
        # condition afterward, including the Worker stop gate, before INP ON.
        _write(session, "SYST:REM")
        settings = _check_input_on_conditions(session, stop, allow_cv=allow_cv, allow_cr=allow_cr,
                                              allow_cw=allow_cw)
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
            current: object | None = None, voltage: object | None = None,
            resistance: object | None = None, power: object | None = None,
            range_name: str | None = None,
            confirmed: bool = False, validation: bool = False,
            stop: Event | None = None, simulator: ControlSimulator | None = None) -> dict:
    if command not in CONTROL_COMMANDS or mode not in {"live", "simulate", "dry-run"}:
        raise LoadError("argument_error", "Unknown control command or mode.")
    if model_id != MODEL_ID:
        raise LoadError("unsupported_model", "The requested profile is not registered.")
    validate_connection(resource, backend, required=mode == "live")
    if command == "set-cc" and current is None:
        raise LoadError("argument_error", "CC requires current and low/high range.")
    if command == "set-cv" and voltage is None:
        raise LoadError("argument_error", "CV requires voltage and low/high range.")
    if command == "set-cr" and resistance is None:
        raise LoadError("argument_error", "CR requires resistance and low/high range.")
    if command == "set-cw" and (power is None or range_name is not None):
        raise LoadError("argument_error", "CW requires power and does not accept a range.")
    if mode == "live" and command == "set-cv":
        raise LoadError("unsupported_live_scope",
                        "CV LIVE range programming requires hardware confirmation.")
    if mode == "live" and command == "set-cr":
        raise LoadError("unsupported_live_scope",
                        "CR LIVE programming requires hardware confirmation.")
    if mode == "live" and command == "set-cw":
        raise LoadError("unsupported_live_scope",
                        "CW LIVE programming requires hardware confirmation.")
    if mode == "live" and command not in PRODUCT_LIVE_COMMANDS and not validation:
        raise LoadError("unsupported_live_scope", "Control is pending LIVE validation.")
    if command == "set-cc":
        validate_current(current, range_name)
    if command == "set-cv":
        validate_voltage(voltage, range_name)
    if command == "set-cr":
        validate_resistance(resistance, range_name)
    if command == "set-cw":
        validate_power(power)
    if mode == "dry-run":
        if command == "set-cw":
            return {"command": command, "hardware_access": False,
                    "power_w": float(validate_power(power)),
                    "input_on_requires_confirmation": False}
        result = {"command": command, "hardware_access": False, "range": range_name,
                "current_a": float(_decimal(current)) if current is not None else None,
                "voltage_v": float(validate_voltage(voltage, range_name))
                if command == "set-cv" else None,
                "input_on_requires_confirmation": command == "input-on"}
        if command == "set-cr":
            result["resistance_ohm"] = float(validate_resistance(resistance, range_name))
        return result
    if mode == "simulate":
        return _perform(simulator or ControlSimulator(), command, current=current,
                        voltage=voltage, resistance=resistance, power=power, range_name=range_name,
                        confirmed=confirmed, stop=stop, allow_cv=True, allow_cr=True, allow_cw=True)
    with open_session(resource) as (session, runtime):
        result = _perform(session, command, current=current, voltage=voltage,
                          resistance=resistance, power=power,
                          range_name=range_name, confirmed=confirmed,
                          stop=stop, allow_cv=False, allow_cr=False, allow_cw=False)
        result["visa"] = runtime
    return result
