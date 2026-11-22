"""Fixed CC, offline CV/CR/CW/Transient/LIST and deliberate Input switching for IT8812C.

No hidden write retry, automatic input OFF or VISA connection selection.
"""

from decimal import Decimal, InvalidOperation, localcontext
from datetime import datetime, timezone
import math
import re
from threading import Event
from time import monotonic, sleep

from .errors import LoadError, communication_error
from .identity import MODEL_ID, parse_identity
from .visa import open_session, require_trusted_session, validate_connection
from .simulator import ControlSimulator, list_settings, transient_settings
from .trigger import TRIGGER_COMMANDS, software_trigger, validate_setup as validate_trigger_setup

TRANSIENT_SETUP_COMMANDS = {"set-transient-cc": "CC", "set-transient-cv": "CV",
                            "set-transient-cr": "CR", "set-transient-cw": "CW"}
TRANSIENT_FUNCTIONS = {"CC": "CURR", "CV": "VOLT", "CR": "RES", "CW": "POW"}
TRANSIENT_COMMANDS = (*TRANSIENT_SETUP_COMMANDS, "transient-enable", "transient-disable",
                      "transient-trigger", "transient-status")
LIST_COMMANDS = ("set-list-cc", "list-save", "list-recall", "list-trigger",
                 "list-status", "list-exit")
LIST_FIELDS = frozenset({"range", "repeat_count", "steps"})
AUXILIARY_COMMANDS = ("set-von", "von-status", "short-on", "short-off", "short-status")
INPUT_TIMER_COMMANDS = ("set-input-timer", "input-timer-enable", "input-timer-disable",
                        "input-timer-status")
AVERAGE_COMMANDS = ("set-average", "average-status")
CR_COMMANDS = ("set-cr", "set-cr-led")
SLOW_RISE_COMMANDS = ("current-slow-rise-enable", "current-slow-rise-disable",
                      "current-slow-rise-status")
LED_QUERY = "RES:LED:STAT?"
VDROP_QUERY = "RES:VDR?"
SLOW_RISE_QUERY = "CURR:SLEW:STAT?"
RESISTANCE_FUNCTIONS = {"RES", "RESISTANCE"}
CONTROL_COMMANDS = ("set-cc", "set-cv", "set-cr", "set-cw", "input-on", "input-off",
                    *TRANSIENT_COMMANDS, *LIST_COMMANDS, *AUXILIARY_COMMANDS, *INPUT_TIMER_COMMANDS,
                    *AVERAGE_COMMANDS, "set-cr-led", "cr-led-status", *SLOW_RISE_COMMANDS,
                    *TRIGGER_COMMANDS)
FIXED_RANGE_COMMANDS = ("set-cc", "set-cv", *CR_COMMANDS)
FIXED_RANGE_CHOICES = ("auto", "low", "high")
TRANSIENT_FIELDS = frozenset({"transient_mode", "range", "level_a", "level_b", "rise_slew",
                              "fall_slew", "width_a", "width_b", "pulse_width"})
# IT8800 User Manual V2.7, IT8812C table (printed p.40): slew bounds,
# not a documented decimal step. Programming Guide V2.1 pp.48,50-51.
SLEW_LIMITS = {"low": (Decimal("0.0001"), Decimal("0.25")),
               "high": (Decimal("0.001"), Decimal("2.5"))}
# IT8811/12/12B/12C User Manual V2.7, section 3.18.
LIST_WIDTH_LIMITS = (Decimal("0.00002"), Decimal("3600"))
# Exact IT8812C Product LIVE control commands; the model, USB/System VISA,
# Explicit Input ON authorization remains enforced.
PRODUCT_LIVE_COMMANDS = frozenset({"set-cc", "input-on", "input-off"})
RANGE_LIMITS = {"low": 6.0, "high": 60.0}
RANGE_RESOLUTION = {"low": Decimal("0.0001"), "high": Decimal("0.001")}
CV_RANGE_LIMITS = {"low": Decimal("18"), "high": Decimal("120")}
CV_RESOLUTION = {"low": Decimal("0.001"), "high": Decimal("0.01")}
CV_MIN_VOLTAGE = Decimal("0.1")
# Offline VON bound: IT8800 VOLT:ON 0..MAX and IT8812C rated input voltage.
VON_MAX_VOLTAGE = Decimal("120")
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
    if current != current.quantize(RANGE_RESOLUTION[range_name]):
        raise LoadError("invalid_current_resolution", "Current exceeds the range's programming resolution.")
    return current


def validate_fixed_cc_slew(range_name: str, rise_slew: object, fall_slew: object) -> dict:
    if rise_slew is None and fall_slew is None:
        return {}
    if rise_slew is None or fall_slew is None:
        raise LoadError("argument_error", "Supply both rising and falling slew rates.")
    low, high = SLEW_LIMITS[range_name]
    settings = {}
    for field, value in (("rise_slew", rise_slew), ("fall_slew", fall_slew)):
        try:
            slew = _decimal(value)
        except LoadError as exc:
            raise LoadError("invalid_slew", "Slew must be a finite number.", field=field) from exc
        if not low <= slew <= high:
            raise LoadError("slew_out_of_range", "Slew is outside the selected range.", field=field)
        settings[f"{field}_a_per_us"] = str(slew)
    return settings


def validate_input_timer(seconds: object) -> int:
    if type(seconds) is not int or not 1 <= seconds <= 60000:
        raise LoadError("invalid_input_timer", "Timer seconds must be an integer from 1 through 60000.")
    return seconds


def validate_average(count: object) -> int:
    if type(count) is not int or not 2 <= count <= 16:
        raise LoadError("invalid_average_count", "Average count must be an integer from 2 through 16.")
    return count


def validate_von(voltage: object, latch: object) -> Decimal:
    if voltage is None or latch not in ("on", "off"):
        raise LoadError("argument_error", "VON requires voltage and latch (on or off).")
    try:
        threshold = _decimal(voltage)
    except LoadError as exc:
        raise LoadError("invalid_voltage", "VON threshold must be a finite number.") from exc
    if not 0 <= threshold <= VON_MAX_VOLTAGE:
        raise LoadError("voltage_out_of_range", "VON threshold must be 0 through 120 V.")
    return threshold


def validate_transient_cc(arguments: dict) -> dict:
    """Validate a complete offline request before selecting range or changing state."""
    if not isinstance(arguments, dict):
        raise LoadError("argument_error", "Transient setup requires an object.")
    mode = arguments.get("transient_mode")
    widths = {"continuous": {"width_a", "width_b"}, "pulse": {"pulse_width"}, "toggle": set()}
    if not isinstance(mode, str) or mode not in widths:
        raise LoadError("argument_error", "Transient mode must be continuous, pulse or toggle.")
    required = {"transient_mode", "range", "level_a", "level_b", "rise_slew", "fall_slew"}
    if set(arguments) != required | widths[mode]:
        raise LoadError("argument_error", "Supply exactly the fields required by the transient mode.")
    selected = arguments["range"]
    if not isinstance(selected, str) or selected not in {"auto", "low", "high"}:
        raise LoadError("invalid_range", "Transient range must be auto, low or high.")
    values = {}
    for field in required - {"transient_mode", "range"} | widths[mode]:
        try:
            values[field] = _decimal(arguments[field])
        except LoadError as exc:
            raise LoadError("invalid_transient_value", "Transient values must be finite numbers.",
                            field=field) from exc
    for field in widths[mode]:
        value = values[field]
        if not Decimal("0.00002") <= value <= Decimal("3600"):
            raise LoadError("width_out_of_range", "Width must be 0.00002 through 3600 seconds.",
                            field=field)
        if value != value.quantize(Decimal("0.000001")):
            raise LoadError("invalid_width_resolution", "Width must be a multiple of 0.000001 s.",
                            field=field)
    errors = {}
    for candidate in ("low", "high") if selected == "auto" else (selected,):
        try:
            for field in ("level_a", "level_b"):
                # Avoid Decimal context rounding even for long numeric tokens.
                with localcontext() as context:
                    context.prec = max(28, len(values[field].as_tuple().digits) + 8)
                    validate_current(values[field], candidate)
                # A tiny remainder can underflow; equality still rejects off-step input.
                if values[field] != values[field].quantize(RANGE_RESOLUTION[candidate]):
                    raise LoadError("invalid_current_resolution",
                                    "Current exceeds the range's programming resolution.")
            for field in ("rise_slew", "fall_slew"):
                low, high = SLEW_LIMITS[candidate]
                if not low <= values[field] <= high:
                    raise LoadError("slew_out_of_range", "Slew is outside the selected range.",
                                    field=field)
        except LoadError as exc:
            if selected != "auto":
                raise
            errors[candidate] = exc.code
        else:
            return {"transient_mode": mode, "range": candidate, **values}
    raise LoadError("no_matching_range", "Neither range accepts the complete transient setup.",
                    ranges=errors)


def validate_list_slot(slot: object) -> int:
    if type(slot) is not int or not 1 <= slot <= 7:
        raise LoadError("invalid_list_slot", "LIST slot must be an integer from 1 through 7.")
    return slot


def validate_list_cc(arguments: dict) -> dict:
    """Complete offline LIST validation; never infer a LIST width decimal step.

    IT8800 Programming Guide V2.1 pp.60–61: LIST current range, steps,
    count and per-step level/slew/seconds. User Manual V2.7 pp.31–32:
    LIST width bounds; pp.40–41: IT8812C current resolution and slew bounds.
    The T1/T2 1-us resolution is not documented as a LIST width step.
    """
    if not isinstance(arguments, dict) or set(arguments) != LIST_FIELDS:
        raise LoadError("argument_error", "LIST requires exactly range, repeat_count and steps.")
    selected = arguments["range"]
    if not isinstance(selected, str) or selected not in {"auto", "low", "high"}:
        raise LoadError("invalid_range", "LIST range must be auto, low or high.")
    repeat = arguments["repeat_count"]
    if type(repeat) is not int or not 1 <= repeat <= 65536:
        raise LoadError("invalid_repeat_count", "Repeat count must be an integer from 1 to 65536.")
    raw_steps = arguments["steps"]
    if not isinstance(raw_steps, list) or not 2 <= len(raw_steps) <= 84:
        raise LoadError("invalid_list_steps", "LIST requires 2 through 84 steps.")
    fields = {"current_a", "slew_a_per_us", "width_s"}
    steps = []
    minimum_width, maximum_width = LIST_WIDTH_LIMITS
    for index, raw in enumerate(raw_steps, 1):
        if not isinstance(raw, dict) or set(raw) != fields:
            raise LoadError("argument_error", "Each LIST step requires current_a, slew_a_per_us, width_s.",
                            step=index)
        step = {}
        for field in fields:
            value = raw[field]
            if type(value) not in (int, float, Decimal):
                raise LoadError("invalid_list_value", "LIST step values must be finite JSON numbers.",
                                step=index, field=field)
            try:
                step[field] = _decimal(value)
            except LoadError as exc:
                raise LoadError("invalid_list_value", "LIST step values must be finite JSON numbers.",
                                step=index, field=field) from exc
        if not minimum_width <= step["width_s"] <= maximum_width:
            raise LoadError("width_out_of_range", "LIST width must be 0.00002 through 3600 seconds.",
                            step=index)
        steps.append(step)
    errors = {}
    for candidate in ("low", "high") if selected == "auto" else (selected,):
        try:
            for index, step in enumerate(steps, 1):
                current = step["current_a"]
                if not 0 <= current <= Decimal(str(RANGE_LIMITS[candidate])):
                    raise LoadError("current_out_of_range", "LIST current is outside the range.",
                                    step=index)
                # Equality prevents silent rounding, including long/tiny JSON tokens.
                if current != current.quantize(RANGE_RESOLUTION[candidate]):
                    raise LoadError("invalid_current_resolution", "LIST current exceeds range resolution.",
                                    step=index)
                minimum, maximum = SLEW_LIMITS[candidate]
                if not minimum <= step["slew_a_per_us"] <= maximum:
                    raise LoadError("slew_out_of_range", "LIST slew is outside the range.", step=index)
        except LoadError as exc:
            if selected != "auto":
                raise
            errors[candidate] = exc.code
        else:
            return {"range": candidate, "repeat_count": repeat, "steps": steps}
    raise LoadError("no_matching_range", "Neither range accepts the complete LIST.", ranges=errors)


def _list_selected(session) -> None:
    if (session.mode != "LIST" or session.function != "CURR"
            or session.list_configuration is None):
        raise LoadError("list_not_selected", "Select a configured CC LIST first.")
    validate_list_cc(session.list_configuration)


def _list_simulation(session, command, configuration, slot, stop):
    """Control state only; no physical LIST engine, SCPI, timer or completion claim."""
    if command == "list-status":
        return session.list_status()
    expected_settings = list_settings(configuration) if command == "set-list-cc" else None

    def check_stop():
        if stop is not None and stop.is_set():
            raise LoadError("stopping", "LIST control was blocked because Worker is stopping.")

    check_stop()
    if command in {"set-list-cc", "list-recall"} and session.input_timer_enabled:
        raise LoadError("input_timer_enabled", "Disable Input Timer before selecting LIST.")
    if _flag(session, "TRAN?") != "OFF":
        raise LoadError("transient_enabled", "Disable Transient before LIST control.")
    if command == "list-trigger":
        _list_selected(session)
        if _flag(session, "INP:STAT?") != "ON":
            raise LoadError("input_not_on", "LIST trigger requires Input ON.")
        if _flag(session, "INP:SHOR:STAT?") != "OFF":
            raise LoadError("unsafe_mode", "Short must be OFF.")
        software_trigger(session.trigger_source)
        check_stop()
        previous = session.list_last_trigger
        session.list_last_trigger = {"sequence": previous["sequence"] + 1 if previous else 1,
                                     "accepted_at_utc": datetime.now(timezone.utc).isoformat(),
                                     "source": "software"}
    else:
        _input_off(session)
        if command == "list-recall":
            if slot not in session.list_slots:
                raise LoadError("list_slot_empty", "No simulated LIST is stored in that slot.")
            configuration = validate_list_cc(session.list_slots[slot])
        elif command == "list-save":
            if session.list_configuration is None:
                raise LoadError("list_not_configured", "Configure LIST before saving.")
            configuration = validate_list_cc(session.list_configuration)
        elif command == "list-exit":
            _list_selected(session)
        check_stop()
        if command in {"set-list-cc", "list-recall"}:
            _leave_cr_led(session, _check_mode_change(session), stop)
            session.list_configuration = configuration
            session.list_last_trigger = None
            session.list_slot = slot
            session.mode = "LIST"
            session.function = "CURR"
            # Replace inactive setup, as Fixed setup does; never disable active Transient.
            session.transient_configuration = None
            session.last_trigger = None
        elif command == "list-save":
            session.list_slots[slot] = configuration
            session.list_slot = slot
        elif command == "list-exit":
            session.mode = "FIX"
    status = session.list_status()
    if expected_settings is not None and (not status["selected"]
                                          or status["configuration"] != expected_settings):
        raise LoadError("readback_mismatch", "LIST setup did not match simulated readback.", 3)
    return status


def _transient_simulation(session, command, configuration, stop):
    """Control state only: no SCPI, waveform, timer or physical completion claim."""
    if command == "transient-status":
        return session.transient_status()
    function = TRANSIENT_SETUP_COMMANDS.get(command)
    expected_settings = transient_settings(function, configuration) if function else None
    if stop is not None and stop.is_set():
        raise LoadError("stopping", "Transient control was blocked because Worker is stopping.")
    if (command in {*TRANSIENT_SETUP_COMMANDS, "transient-enable"}
            and session.input_timer_enabled):
        raise LoadError("input_timer_enabled", "Disable Input Timer before selecting Transient.")
    if session.mode == "LIST":
        raise LoadError("list_selected", "Exit LIST explicitly before Transient control.")
    if command == "transient-trigger":
        if not session.transient_enabled or session.transient_configuration is None:
            raise LoadError("transient_not_enabled", "Trigger requires enabled Transient setup.")
        if _flag(session, "INP:STAT?") != "ON":
            raise LoadError("input_not_on", "Trigger requires Input ON.")
        if _flag(session, "INP:SHOR:STAT?") != "OFF":
            raise LoadError("unsafe_mode", "Short must be OFF.")
        software_trigger(session.trigger_source)
        if stop is not None and stop.is_set():
            raise LoadError("stopping", "Transient trigger was blocked because Worker is stopping.")
        previous = session.last_trigger
        session.last_trigger = {"sequence": previous["sequence"] + 1 if previous else 1,
                                "accepted_at_utc": datetime.now(timezone.utc).isoformat(),
                                "source": "software"}
    else:
        _input_off(session)
        if command in TRANSIENT_SETUP_COMMANDS:
            if session.transient_enabled:
                raise LoadError("transient_enabled", "Disable Transient before setup.")
            _leave_cr_led(session, _check_mode_change(session), stop)
            session.transient_configuration = dict(configuration)
            session.last_trigger = None
            session.mode = "TRAN"
            session.transient_function = function
            session.function = TRANSIENT_FUNCTIONS[function]
        elif command == "transient-enable":
            if session.transient_configuration is None:
                raise LoadError("transient_not_configured", "Enable requires Transient setup.")
            _leave_cr_led(session, _check_mode_change(session), stop)
            session.transient_enabled = True
        elif command == "transient-disable":
            session.transient_enabled = False
    status = session.transient_status()
    if expected_settings is not None and (status["function"] != function or status["enabled"]
                                          or status["configuration"] != expected_settings):
        raise LoadError("readback_mismatch", "Transient setup did not match simulated readback.", 3)
    if command in {"transient-enable", "transient-disable"}:
        enabled = command == "transient-enable"
        if status["enabled"] != enabled or (_flag(session, "TRAN?") == "ON") != enabled:
            raise LoadError("readback_mismatch", "Transient enable state did not match readback.", 3)
    return status


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
    if voltage != voltage.quantize(CV_RESOLUTION[range_name]):
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


def validate_vdrop(value: object) -> Decimal:
    try:
        voltage = _decimal(value)
    except LoadError as exc:
        raise LoadError("invalid_vdrop", "Vdrop must be a finite nonnegative number.") from exc
    if voltage < 0:
        raise LoadError("invalid_vdrop", "Vdrop must be a finite nonnegative number.")
    return voltage


def resolve_fixed_range(command: str, value: object, selection: str) -> str:
    """Validate the original setpoint and resolve software Auto before any I/O."""
    validators = {"set-cc": validate_current, "set-cv": validate_voltage,
                  **dict.fromkeys(CR_COMMANDS, validate_resistance)}
    if command not in validators:
        raise LoadError("argument_error", "Fixed range requires CC, CV or CR.")
    if not isinstance(selection, str) or selection not in FIXED_RANGE_CHOICES:
        raise LoadError("invalid_range", "Range must be auto, low or high.")
    errors = {}
    for candidate in ("low", "high") if selection == "auto" else (selection,):
        try:
            validators[command](value, candidate)
        except LoadError as exc:
            # Invalid types/nonfinite values retain their established error codes.
            if selection != "auto" or exc.code in {
                    "invalid_current", "invalid_voltage", "invalid_resistance"}:
                raise
            errors[candidate] = exc.code
        else:
            return candidate
    raise LoadError("no_matching_range", "Neither range accepts the Fixed setpoint.", ranges=errors)


def _cr_settings(session, *, expected=None) -> dict:
    # Physical range interpretation is restricted to contributor validation.
    raw_range = _decimal_readback(session, "RES:RANG?")
    ranges = {limits[1]: name for name, limits in CR_RANGE_LIMITS.items()}
    if raw_range not in ranges:
        raise LoadError("invalid_range_readback", "Unrecognized CR range.", 3,
                        range_readback_ohm=str(raw_range))
    name = ranges[raw_range]
    resistance = validate_resistance(_query(session, "RES?"), name)
    settings = {"range": name, "range_readback_ohm": float(raw_range),
                "resistance_ohm": float(resistance)}
    if expected is not None and (name, resistance) != expected:
        raise LoadError("readback_mismatch", "CR range or resistance did not match readback.",
                        3, observed=settings)
    return settings


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


def validate_transient(command: str, arguments: dict) -> dict:
    """Validate one complete setup; CV/CR/CW have no slew or documented width step."""
    if command == "set-transient-cc":
        return validate_transient_cc(arguments)
    if command not in TRANSIENT_SETUP_COMMANDS or not isinstance(arguments, dict):
        raise LoadError("argument_error", "Transient setup requires a command and object.")
    mode = arguments.get("transient_mode")
    widths = {"continuous": {"width_a", "width_b"}, "pulse": {"pulse_width"}, "toggle": set()}
    if not isinstance(mode, str) or mode not in widths:
        raise LoadError("argument_error", "Transient mode must be continuous, pulse or toggle.")
    required = {"transient_mode", "level_a", "level_b"}
    if command != "set-transient-cw":
        required.add("range")
    if set(arguments) != required | widths[mode]:
        raise LoadError("argument_error", "Supply exactly the fields required by the transient mode.")
    selected = arguments.get("range")
    if command != "set-transient-cw" and (
            not isinstance(selected, str) or selected not in FIXED_RANGE_CHOICES):
        raise LoadError("invalid_range", "Transient range must be auto, low or high.")
    values = {}
    for field in {"level_a", "level_b"} | widths[mode]:
        try:
            values[field] = _decimal(arguments[field])
        except LoadError as exc:
            raise LoadError("invalid_transient_value", "Transient values must be finite numbers.",
                            field=field) from exc
    for field in widths[mode]:
        if not Decimal("0.00002") <= values[field] <= Decimal("3600"):
            raise LoadError("width_out_of_range", "Width must be 0.00002 through 3600 seconds.",
                            field=field)
    validator = {"set-transient-cv": validate_voltage, "set-transient-cr": validate_resistance,
                 "set-transient-cw": validate_power}[command]
    errors = {}
    for candidate in ("low", "high") if selected == "auto" else (selected,):
        try:
            for field in ("level_a", "level_b"):
                with localcontext() as context:
                    context.prec = max(28, len(values[field].as_tuple().digits) + 8)
                    if command == "set-transient-cw":
                        validator(values[field])
                    else:
                        validator(values[field], candidate)
        except LoadError as exc:
            if selected != "auto":
                raise
            errors[candidate] = exc.code
        else:
            return {"transient_mode": mode, **({"range": candidate} if selected else {}), **values}
    raise LoadError("no_matching_range", "Neither range accepts the complete transient setup.",
                    ranges=errors)


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


def _decimal_readback(session, query):
    raw = _query(session, query)
    try:
        if NUMBER.fullmatch(raw):
            return Decimal(raw)
    except InvalidOperation:
        pass
    raise LoadError("invalid_response", "Instrument returned an invalid number.", 3, query=query)


def _load_mode(session):
    return _query(session, "FUNC:MODE?").upper(), _query(session, "FUNC?").upper()


def _check_mode_change(session, *, fixed_cc=False, force_led_off=False):
    """Check interlocks before changing the selected load mode."""
    if not fixed_cc and _flag(session, SLOW_RISE_QUERY) == "ON":
        raise LoadError("current_slow_rise_enabled",
                        "Disable Current Slow Rise before selecting a non-Fixed-CC mode.")
    if force_led_off:
        return True
    mode, function = _load_mode(session)
    if mode not in {"FIX", "FIXED", "TRAN", "LIST"}:
        raise LoadError("unsafe_mode", "Cannot determine the current load mode.")
    if mode in {"FIX", "FIXED"} and function not in {
            "CURR", "CURRENT", "VOLT", "VOLTAGE", "RES", "RESISTANCE", "POW", "POWER"}:
        raise LoadError("unsafe_mode", "Cannot determine the current Fixed load function.")
    if mode in {"FIX", "FIXED"} and function in RESISTANCE_FUNCTIONS:
        try:
            return _flag(session, LED_QUERY) == "ON"
        except LoadError as exc:
            exc.details.update(led_enabled=None, completed_steps=[])
            raise
    return False


def _mutation_off(session, stop):
    _input_off(session)
    if stop is not None and stop.is_set():
        raise LoadError("stopping", "Configuration was blocked because Worker is stopping.")


def _fixed_setup_guard(session, stop, *, fixed_only=False):
    _mutation_off(session, stop)
    mode = _query(session, "FUNC:MODE?").upper()
    if mode == "LIST":
        raise LoadError("list_selected", "Exit LIST explicitly before Fixed setup.")
    if mode not in ({"FIX", "FIXED"} if fixed_only else {"FIX", "FIXED", "TRAN"}):
        raise LoadError("unsafe_mode", "Cannot determine the current load mode.")
    if _flag(session, "TRAN?") != "OFF":
        raise LoadError("transient_enabled", "Disable Transient before Fixed setup.")
    if not isinstance(session, ControlSimulator) and _flag(session, "INP:SHOR:STAT?") != "OFF":
        raise LoadError("unsafe_mode", "Setup requires Short OFF.")


def _check_cc_origin_without_led_access(session, *, before_remote):
    # FUNC? belongs to the verified CC path. Scopes without CR-LED access
    # cannot distinguish ordinary CR from CR-LED.
    function = _query(session, "FUNC?").upper()
    if function in RESISTANCE_FUNCTIONS:
        raise LoadError("unsupported_live_scope",
                        "This CC scope cannot verify LED OFF when switching from CR.",
                        function=function, before_remote=before_remote)
    if function not in {"CURR", "CURRENT", "VOLT", "VOLTAGE", "POW", "POWER"}:
        raise LoadError("unsafe_mode", "Cannot determine the current load function.")


def _set_led(session, enabled, stop):
    _mutation_off(session, stop)
    try:
        _write(session, f"RES:LED:STAT {int(enabled)}")
        observed = _flag(session, LED_QUERY) == "ON"
    except LoadError as exc:
        exc.details.update(led_enabled=None)
        raise
    if observed != enabled:
        raise LoadError("readback_mismatch", "LED state did not match the requested setting.", 3,
                        led_enabled=observed)


def _leave_cr_led(session, required, stop):
    if required:
        try:
            _set_led(session, False, stop)
        except LoadError as exc:
            exc.details.setdefault("completed_steps", [])
            raise


def _fixed_cr_mode(session):
    mode, function = _load_mode(session)
    fixed_cr = (mode in {"FIX", "FIXED"} and function in RESISTANCE_FUNCTIONS
                and _flag(session, "TRAN?") == "OFF")
    return {"function_mode": mode, "function": function, "fixed_cr": fixed_cr}


def _cr_vdrop(session):
    vdrop = _decimal_readback(session, VDROP_QUERY)
    if vdrop < 0:
        raise LoadError("invalid_response", "Instrument returned a negative Vdrop.", 3,
                        query=VDROP_QUERY)
    return str(vdrop)


def _cr_status(session, *, expected=None):
    mode = _fixed_cr_mode(session)
    vdrop = _cr_vdrop(session)
    return {**mode, "led_enabled": _flag(session, LED_QUERY) == "ON", "vdrop_v": vdrop,
            **_cr_settings(session, expected=expected)}


def _cr_sequence(resistance, range_name):
    return ("FUNC:MODE FIX", "FUNC RES", f"RES:RANG {CR_RANGE_LIMITS[range_name][1]}",
            f"RES {resistance:f}")


def _cr_setup(session, resistance, range_name, vdrop, stop):
    """Configure Fixed CR, optionally enabling LED after the setpoints apply."""
    physical = not isinstance(session, ControlSimulator)
    completed = []

    def guard():
        _fixed_setup_guard(session, stop)
        _check_mode_change(session, force_led_off=True)

    expected = (range_name, resistance)
    try:
        guard()
        maximum = None
        if physical and vdrop is not None:
            maximum = _decimal_readback(session, f"{VDROP_QUERY} MAX")
            if maximum < 0:
                raise LoadError("invalid_response", "Instrument returned a negative Vdrop maximum.", 3)
            if vdrop > maximum:
                raise LoadError("vdrop_out_of_range", "Vdrop exceeds the instrument maximum.",
                                vdrop_max_v=str(maximum))
        _mutation_off(session, stop)
        _write(session, "SYST:REM")
        completed.append("remote")
        guard()
        if vdrop is None:
            _set_led(session, False, stop)
            completed.append("led_off")
        if physical:
            for write in _cr_sequence(resistance, range_name):
                _mutation_off(session, stop)
                _write(session, write)
                completed.append(write)
        else:
            session.configure_cr(resistance, range_name)
            completed.append("cr_settings")
        if vdrop is not None:
            if not _fixed_cr_mode(session)["fixed_cr"]:
                raise LoadError("readback_mismatch", "Fixed CR mode was not confirmed.", 3)
            _cr_settings(session, expected=expected)
            _mutation_off(session, stop)
            _write(session, f"RES:VDR {vdrop}")
            completed.append("vdrop")
            if _decimal_readback(session, VDROP_QUERY) != vdrop:
                raise LoadError("readback_mismatch", "Vdrop did not match the requested setting.", 3)
            _set_led(session, True, stop)
            completed.append("led_on")
        settings = _cr_status(session, expected=expected)
        if (not settings["fixed_cr"]
                or settings["led_enabled"] != (vdrop is not None)
                or (vdrop is not None and Decimal(settings["vdrop_v"]) != vdrop)):
            raise LoadError("readback_mismatch", "CR setting did not match readback.", 3,
                            observed=settings)
        _input_off(session)
        if maximum is not None:
            settings["vdrop_max_v"] = str(maximum)
        return {"input_state": "OFF", **settings}
    except LoadError as exc:
        exc.details["completed_steps"] = completed
        raise


def _slow_rise(session, command, stop):
    if command == "current-slow-rise-status":
        return {"enabled": _flag(session, SLOW_RISE_QUERY) == "ON"}
    enable = command == "current-slow-rise-enable"

    def guard():
        _mutation_off(session, stop)
        if enable:
            mode, function = _load_mode(session)
            if (mode not in {"FIX", "FIXED"} or function not in {"CURR", "CURRENT"}
                    or _flag(session, "TRAN?") != "OFF"):
                raise LoadError("unsafe_mode", "Enabling Current Slow Rise requires Fixed CC.")

    guard()
    _mutation_off(session, stop)
    _write(session, "SYST:REM")
    guard()
    _mutation_off(session, stop)
    try:
        _write(session, f"CURR:SLEW:STAT {int(enable)}")
        observed = _flag(session, SLOW_RISE_QUERY) == "ON"
        _input_off(session)
    except LoadError as exc:
        exc.details["enabled"] = None
        raise
    if observed != enable:
        raise LoadError("readback_mismatch", "Current Slow Rise did not match the requested state.",
                        3, enabled=observed)
    return {"enabled": observed, "input_state": "OFF"}


def _average(session, command, count, stop):
    parse_identity(_query(session, "*IDN?"))
    if command == "set-average":
        if stop is not None and stop.is_set():
            raise LoadError("stopping", "Worker is stopping.")
        _input_off(session)
        _write(session, "SYST:REM")
        _input_off(session)
        if stop is not None and stop.is_set():
            raise LoadError("stopping", "Worker is stopping.")
        action = f"SENS:AVER:COUN {count}"
        try:
            _write(session, action)
        except LoadError as exc:
            exc.details["command"] = action
            raise
    observed = _average_count(session)
    if command == "set-average" and observed != count:
        raise LoadError("readback_mismatch", "Average count did not match readback.", 3,
                        requested_average_count=count, observed_average_count=observed)
    return {"average_count": observed}


def _average_count(session):
    query = "SENS:AVER:COUN?"
    try:
        response = _query(session, query)
    except LoadError as exc:
        exc.details["query"] = query
        raise
    try:
        observed = Decimal(response) if NUMBER.fullmatch(response) else Decimal("NaN")
    except InvalidOperation:
        observed = Decimal("NaN")
    if (not observed.is_finite() or not 2 <= observed <= 16
            or observed != observed.to_integral_value()):
        raise LoadError("invalid_response", "Average readback must be an integer from 2 through 16.",
                        3, query=query)
    return int(observed)


def _short_simulation(session: ControlSimulator, command: str, stop: Event | None) -> dict:
    query = "INP:SHOR:STAT?"
    if command == "short-status":
        return {"short_state": _flag(session, query)}
    enable = command == "short-on"
    if enable:
        if session.input_timer_enabled:
            raise LoadError("input_timer_enabled", "Disable Input Timer before Short ON.")
        if _flag(session, "INP:STAT?") != "ON":
            raise LoadError("input_not_on", "Short ON requires confirmed Input ON.")
        if (session.mode != "FIX" or session.function not in TRANSIENT_FUNCTIONS.values()
                or _flag(session, "TRAN?") != "OFF"):
            raise LoadError("unsafe_mode", "Short ON requires Fixed CC, CV, CR or CW.")
        if _flag(session, query) != "OFF":
            raise LoadError("already_short_on", "Short is already ON.")
        if stop is not None and stop.is_set():
            raise LoadError("stopping", "Short ON was blocked because Worker is stopping.")
    session.short_on = enable
    state = _flag(session, query)
    if state != ("ON" if enable else "OFF"):
        raise LoadError("short_state_not_confirmed", "Short state was not confirmed.", 3,
                        short_state="UNKNOWN")
    return {"short_state": state}


def _input_timer_simulation(session, command, seconds, stop):
    if command != "input-timer-status":
        if stop is not None and stop.is_set():
            raise LoadError("stopping", "Input Timer mutation blocked because Worker is stopping.")
        _input_off(session)
        if command != "input-timer-disable":
            if (session.mode != "FIX" or session.function not in TRANSIENT_FUNCTIONS.values()
                    or _flag(session, "TRAN?") != "OFF"):
                raise LoadError("unsafe_mode", "Input Timer requires Fixed CC, CV, CR or CW.")
        if command == "input-timer-enable" and _flag(session, "INP:SHOR:STAT?") != "OFF":
            raise LoadError("unsafe_mode", "Short must be OFF before enabling Input Timer.")
        if command == "set-input-timer":
            session.input_timer_seconds = seconds
        else:
            session.input_timer_enabled = command == "input-timer-enable"
    return {"seconds": session.input_timer_seconds, "enabled": session.input_timer_enabled}


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
                **_cr_settings(session)}
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
    if isinstance(session, ControlSimulator) and session.mode == "LIST":
        _list_selected(session)
        if _flag(session, "TRAN?") != "OFF" or _flag(session, "INP:SHOR:STAT?") != "OFF":
            raise LoadError("unsafe_mode", "Transient and Short must be OFF.")
        if stop is not None and stop.is_set():
            raise LoadError("stopping", "Input ON was blocked because Worker is stopping.")
        return {"function_mode": "LIST", "function": "CURR", **session.list_settings()}
    if isinstance(session, ControlSimulator) and session.transient_configuration is not None:
        if (not session.transient_enabled or session.mode != "TRAN"
                or session.function != TRANSIENT_FUNCTIONS.get(session.transient_function)):
            raise LoadError("transient_not_enabled", "Enable Transient before Input ON.")
        validate_transient("set-transient-" + session.transient_function.lower(),
                           session.transient_configuration)
        if _flag(session, "INP:SHOR:STAT?") != "OFF":
            raise LoadError("unsafe_mode", "Short must be OFF.")
        if stop is not None and stop.is_set():
            raise LoadError("stopping", "Input ON was blocked because Worker is stopping.")
        return {"function_mode": "TRAN", "function": session.function, **session.transient_settings()}
    settings = _settings(session, allow_cv=allow_cv, allow_cr=allow_cr, allow_cw=allow_cw)
    _check_settings(session, settings)
    if _flag(session, "TRAN?") != "OFF" or _flag(session, "INP:SHOR:STAT?") != "OFF":
        raise LoadError("unsafe_mode", "Transient and Short must be OFF.")
    if stop is not None and stop.is_set():
        raise LoadError("stopping", "Input ON was blocked because Worker is stopping.")
    return settings


def _set_fixed_cc(session, current, range_name, stop, cc_mode_scope=None):
    requested = validate_current(current, range_name)
    simulated = isinstance(session, ControlSimulator)
    guard_mode = cc_mode_scope is not None or simulated
    allow_led_cleanup = cc_mode_scope == "cr-led" or simulated
    setup_stop = stop if guard_mode else None
    completed = []

    def check_origin(*, before_remote):
        if guard_mode:
            _fixed_setup_guard(session, stop, fixed_only=cc_mode_scope == "sequence")
        if cc_mode_scope == "sequence":
            if _check_mode_change(session, fixed_cc=True):
                raise LoadError("cr_led_enabled", "Disable CR-LED before Sequence setup.")
            return False
        if allow_led_cleanup:
            return _check_mode_change(session, fixed_cc=True)
        _check_cc_origin_without_led_access(session, before_remote=before_remote)
        return False

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
        check_origin(before_remote=True)
        _mutation_off(session, setup_stop)
        _write(session, "SYST:REM")
        if guard_mode:
            completed.append("remote")
        if check_origin(before_remote=False):
            _set_led(session, False, stop)
            completed.append("led_off")
        for action, name in sequence:
            if cc_mode_scope == "sequence":
                _fixed_setup_guard(session, stop, fixed_only=True)
            _mutation_off(session, setup_stop)
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


def _turn_input_on(session, check, *, on_attempt=None):
    check()
    # Remote can change the state observed by the first safety check.
    _write(session, "SYST:REM")
    settings = check()
    try:
        if on_attempt is not None:
            on_attempt()
        _write(session, "INP ON")
        if not _confirm_input_state(session, "ON"):
            raise LoadError("input_on_not_confirmed", "Input ON was not confirmed.", 3)
    except LoadError as exc:
        exc.details.update(input_state="UNKNOWN", input_on_attempted=True)
        raise
    return {**settings, "input_state": "ON"}


def _turn_input_off(session):
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


def _perform(session, command: str, *, current: object | None,
             voltage: object | None, resistance: object | None, power: object | None,
             range_name: str | None, confirmed: bool, stop: Event | None,
             allow_cv: bool, allow_cr: bool, allow_cw: bool,
             vdrop: Decimal | None = None, cc_mode_scope: str | None = None) -> dict:
    parse_identity(_query(session, "*IDN?"))
    if command == "cr-led-status":
        return _cr_status(session)
    if command in SLOW_RISE_COMMANDS:
        return _slow_rise(session, command, stop)
    if command in CR_COMMANDS:
        return _cr_setup(session, validate_resistance(resistance, range_name), range_name, vdrop, stop)
    if command == "set-cc":
        return _set_fixed_cc(session, current, range_name, stop, cc_mode_scope)
    if command == "set-cv":
        requested = validate_voltage(voltage, range_name)
        _input_off(session)
        # No real CV range SCPI is sent: the guide has conflicting descriptions.
        # The simulation model applies settings internally until hardware proof.
        if not isinstance(session, ControlSimulator):
            raise LoadError("unsupported_live_scope", "CV LIVE range semantics unverified.")
        _leave_cr_led(session, _check_mode_change(session), stop)
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
    if command == "set-cw":
        requested = validate_power(power)
        _input_off(session)
        if not isinstance(session, ControlSimulator):
            raise LoadError("unsupported_live_scope", "CW LIVE requires hardware validation.")
        _leave_cr_led(session, _check_mode_change(session), stop)
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
        def check():
            return _check_input_on_conditions(session, stop, allow_cv=allow_cv,
                                              allow_cr=allow_cr, allow_cw=allow_cw)

        return _turn_input_on(session, check)
    return _turn_input_off(session)


def execute(command: str, *, mode: str = "live", resource: str | None = None,
            backend: str = "system", model_id: str = MODEL_ID,
            current: object | None = None, voltage: object | None = None,
            resistance: object | None = None, power: object | None = None,
            range_name: str | None = None,
            confirmed: bool = False, validation: bool = False,
            stop: Event | None = None, simulator: ControlSimulator | None = None,
            transient: dict | None = None, hardware_validation: str | None = None,
            validation_settings: dict | None = None,
            list_configuration: dict | None = None, slot: int | None = None,
            rise_slew: object | None = None, fall_slew: object | None = None,
            latch: str | None = None, seconds: int | None = None,
            count: int | None = None, vdrop: object | None = None,
            trigger_source: str | None = None, trigger_interval: object | None = None) -> dict:
    if command not in CONTROL_COMMANDS or mode not in {"live", "simulate", "dry-run"}:
        raise LoadError("argument_error", "Unknown control command or mode.")
    if model_id != MODEL_ID:
        raise LoadError("unsupported_model", "The requested profile is not registered.")
    validate_connection(resource, backend, required=mode == "live")
    if mode == "simulate":
        require_trusted_session(simulator)
    if hardware_validation == "readback":
        from .readback import CONTROL_STATUS_COMMANDS, execute as execute_readback

        if (command not in CONTROL_STATUS_COMMANDS or confirmed
                or any(value is not None for value in (current, voltage, resistance, power,
                    range_name, validation_settings, transient, list_configuration, slot, rise_slew,
                    fall_slew, latch, seconds, count, vdrop, trigger_source, trigger_interval))):
            raise LoadError("argument_error", "Readback validation accepts only status commands without arguments.")
        return execute_readback(command, mode=mode, resource=resource,
                                hardware_validation=hardware_validation)
    if command in TRIGGER_COMMANDS or hardware_validation == "trigger":
        if any(value is not None for value in (current, voltage, resistance, power,
                range_name, slot, rise_slew, fall_slew, latch, seconds, count, vdrop)):
            raise LoadError("argument_error", "Unexpected Trigger Setup arguments.")
        if command != "trigger-configure" and (trigger_source is not None or trigger_interval is not None):
            raise LoadError("argument_error", "Trigger Source and Interval require trigger-configure.")
        if hardware_validation == "trigger":
            from .hardware_validation import execute_trigger

            return execute_trigger(command, mode=mode, resource=resource, source=trigger_source,
                                   interval=trigger_interval, transient=transient,
                                   list_configuration=list_configuration, expected=validation_settings,
                                   confirmed=confirmed, stop=stop)
        if (hardware_validation is not None or validation_settings is not None or confirmed
                or transient is not None or list_configuration is not None):
            raise LoadError("argument_error", "Unexpected Trigger Setup arguments.")
        if mode == "live":
            raise LoadError("unsupported_live_scope", "Trigger Setup is offline only.")
        settings = (validate_trigger_setup(trigger_source, trigger_interval)
                    if command == "trigger-configure" else {})
        if mode == "dry-run":
            return {"command": command, "hardware_access": False,
                    **{key: str(value) for key, value in settings.items()}}
        simulated = simulator or ControlSimulator()
        if settings:
            _mutation_off(simulated, stop)
            simulated.trigger_source = settings["source"]
            simulated.trigger_interval = settings.get("interval_s", simulated.trigger_interval)
        return {"source": simulated.trigger_source, "interval_s": str(simulated.trigger_interval),
                "state_source": "simulate"}
    if trigger_source is not None or trigger_interval is not None:
        raise LoadError("argument_error", "Trigger Source and Interval require trigger-configure.")
    if command == "set-cr-led":
        if (any(value is not None for value in (current, voltage, power, transient,
                list_configuration, slot, rise_slew, fall_slew, latch, seconds, count)) or confirmed):
            raise LoadError("argument_error", "CR-LED requires only resistance, range and Vdrop.")
        vdrop = validate_vdrop(vdrop)
    elif vdrop is not None:
        raise LoadError("argument_error", "Vdrop applies only to set-cr-led.")
    if command == "cr-led-status" or command in SLOW_RISE_COMMANDS:
        if (any(value is not None for value in (current, voltage, resistance, power, range_name,
                transient, list_configuration, slot, rise_slew, fall_slew, latch, seconds, count))
                or confirmed):
            raise LoadError("argument_error", "This command does not accept setup arguments.")
        if hardware_validation is None:
            if validation_settings is not None:
                raise LoadError("argument_error", "Validation settings require --hardware-validation.")
            if mode == "live":
                raise LoadError("unsupported_live_scope", "This command is offline only.")
            if mode == "dry-run":
                return {"command": command, "hardware_access": False}
            simulated = simulator or ControlSimulator()
            if command == "cr-led-status":
                return _cr_status(simulated)
            return _slow_rise(simulated, command, stop)
    if command in AVERAGE_COMMANDS:
        if (any(value is not None for value in (current, voltage, resistance, power, range_name,
                transient, hardware_validation, validation_settings, list_configuration, slot,
                rise_slew, fall_slew, latch, seconds)) or confirmed
                or (command == "average-status" and count is not None)):
            raise LoadError("argument_error", "Unexpected Average arguments.")
        if command == "set-average":
            count = validate_average(count)
        if mode == "live":
            raise LoadError("unsupported_live_scope", "Measurement Average is offline only.")
        if mode == "dry-run":
            return {"command": command, "hardware_access": False,
                    **({"average_count": count} if count is not None else {})}
        return _average(simulator or ControlSimulator(), command, count, stop)
    if count is not None:
        raise LoadError("argument_error", "Count applies only to set-average.")
    if seconds is not None and command != "set-input-timer":
        raise LoadError("argument_error", "Seconds applies only to set-input-timer.")
    if hardware_validation == "input-timer":
        if any(value is not None for value in (voltage, resistance, power, transient,
                                               list_configuration, slot, rise_slew, fall_slew, latch)):
            raise LoadError("argument_error", "Unexpected Timer validation arguments.")
        from .hardware_validation import execute_input_timer

        return execute_input_timer(command, mode=mode, resource=resource, seconds=seconds,
                                   current=current, range_name=range_name, expected=validation_settings,
                                   confirmed=confirmed, stop=stop)
    if command in INPUT_TIMER_COMMANDS:
        if mode == "live":
            raise LoadError("unsupported_live_scope", "Input Timer is offline only.")
        if (any(value is not None for value in (current, voltage, resistance, power, range_name,
                transient, hardware_validation, validation_settings, list_configuration, slot,
                rise_slew, fall_slew, latch)) or confirmed):
            raise LoadError("argument_error", "Unexpected Input Timer arguments.")
        if command == "set-input-timer":
            seconds = validate_input_timer(seconds)
        if mode == "dry-run":
            return {"command": command, "hardware_access": False,
                    **({"seconds": seconds} if seconds is not None else {})}
        return _input_timer_simulation(simulator or ControlSimulator(), command, seconds, stop)
    has_slew = rise_slew is not None or fall_slew is not None
    if has_slew and command != "set-cc":
        raise LoadError("argument_error", "Fixed slew arguments require set-cc.")
    if latch is not None and command != "set-von":
        raise LoadError("argument_error", "Latch applies only to set-von.")
    if has_slew or command in AUXILIARY_COMMANDS:
        if mode == "live":
            raise LoadError("unsupported_live_scope", "Fixed slew, VON and Short are offline only.")
        if hardware_validation is not None:
            raise LoadError("argument_error", "Offline controls do not take hardware-validation scope.")
    if command in AUXILIARY_COMMANDS:
        if (any(value is not None for value in (current, resistance, power, range_name, transient,
                                               validation_settings, list_configuration, slot))
                or (command != "set-von" and voltage is not None) or confirmed):
            raise LoadError("argument_error", "Unexpected VON or Short arguments.")
        threshold = validate_von(voltage, latch) if command == "set-von" else None
        if mode == "dry-run":
            result = {"command": command, "hardware_access": False}
            if threshold is not None:
                result.update(threshold_v=str(threshold), latch=latch)
            return result
        simulated = simulator or ControlSimulator()
        if threshold is not None:
            _input_off(simulated)
            simulated.von_settings = (threshold, latch)
        if command in {"set-von", "von-status"}:
            from .readback import von_status

            result = von_status(simulated)
            if threshold is not None and (Decimal(result["threshold_v"]), result["latch"]) != (
                    threshold, latch):
                raise LoadError("readback_mismatch", "VON settings did not match readback.", 3)
            return result
        return _short_simulation(simulated, command, stop)
    if command in LIST_COMMANDS:
        if (any(value is not None for value in (current, voltage, resistance, power, range_name,
                                                transient, hardware_validation, validation_settings))
                or confirmed):
            raise LoadError("argument_error", "LIST commands accept only their LIST arguments.")
        configuration = validate_list_cc(list_configuration) if command == "set-list-cc" else None
        if command != "set-list-cc" and list_configuration is not None:
            raise LoadError("argument_error", "Complete LIST data applies only to set-list-cc.")
        if command in {"list-save", "list-recall"}:
            validate_list_slot(slot)
        elif slot is not None:
            raise LoadError("argument_error", "Slot applies only to LIST Save/Recall.")
        if mode == "live":
            raise LoadError("unsupported_live_scope", "LIST CC is offline only.")
        if mode == "dry-run":
            result = {"command": command, "hardware_access": False}
            if configuration is not None:
                preview = ControlSimulator()
                preview.list_configuration = configuration
                result.update(preview.list_settings())
            if slot is not None:
                result["slot"] = slot
            return result
        return _list_simulation(simulator or ControlSimulator(), command, configuration, slot, stop)
    if list_configuration is not None or slot is not None:
        raise LoadError("argument_error", "LIST arguments require a LIST command.")
    if hardware_validation is not None:
        from .hardware_validation import execute as validate_hardware

        return validate_hardware(command, scope=hardware_validation, mode=mode,
                                 resource=resource, current=current, voltage=voltage,
                                 resistance=resistance, power=power, range_name=range_name,
                                 transient=transient, expected=validation_settings,
                                 confirmed=confirmed, stop=stop, vdrop=vdrop)
    if validation_settings is not None:
        raise LoadError("argument_error", "Validation settings require --hardware-validation.")
    if command in TRANSIENT_COMMANDS:
        if any(value is not None for value in (current, voltage, resistance, power)):
            raise LoadError("argument_error", "Fixed setpoints do not apply to Transient commands.")
        if range_name is not None:
            raise LoadError("argument_error", "Supply Transient range inside the complete setup.")
        configuration = (validate_transient(command, transient)
                         if command in TRANSIENT_SETUP_COMMANDS else None)
        if command not in TRANSIENT_SETUP_COMMANDS and transient is not None:
            raise LoadError("argument_error", "Transient control takes no setup arguments.")
        if mode == "live":
            raise LoadError("unsupported_live_scope", "Transient control is offline only.")
        if mode == "dry-run":
            result = {"command": command, "hardware_access": False}
            if configuration is not None:
                preview = ControlSimulator()
                preview.transient_configuration = configuration
                preview.transient_function = TRANSIENT_SETUP_COMMANDS[command]
                result["function"] = preview.transient_function
                result.update(preview.transient_settings())
            return result
        return _transient_simulation(simulator or ControlSimulator(), command, configuration, stop)
    if transient is not None:
        raise LoadError("argument_error", "Transient parameters require a Transient setup command.")
    if command == "set-cc" and current is None:
        raise LoadError("argument_error", "CC requires current and auto/low/high range.")
    if command == "set-cv" and voltage is None:
        raise LoadError("argument_error", "CV requires voltage and auto/low/high range.")
    if command in CR_COMMANDS and resistance is None:
        raise LoadError("argument_error", "CR requires resistance and auto/low/high range.")
    if command == "set-cw" and (power is None or range_name is not None):
        raise LoadError("argument_error", "CW requires power and does not accept a range.")
    if mode == "live" and command == "set-cv":
        raise LoadError("unsupported_live_scope",
                        "CV LIVE range programming requires hardware confirmation.")
    if mode == "live" and command in CR_COMMANDS:
        raise LoadError("unsupported_live_scope",
                        "CR LIVE programming requires hardware confirmation.")
    if mode == "live" and command == "set-cw":
        raise LoadError("unsupported_live_scope",
                        "CW LIVE programming requires hardware confirmation.")
    if mode == "live" and command not in PRODUCT_LIVE_COMMANDS and not validation:
        raise LoadError("unsupported_live_scope", "Control is pending LIVE validation.")
    if command in FIXED_RANGE_COMMANDS:
        value = {"set-cc": current, "set-cv": voltage,
                 **dict.fromkeys(CR_COMMANDS, resistance)}[command]
        range_name = resolve_fixed_range(command, value, range_name)
    slew = validate_fixed_cc_slew(range_name, rise_slew, fall_slew) if has_slew else {}
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
                "input_on_requires_confirmation": command == "input-on", **slew}
        if command in CR_COMMANDS:
            result.update(resistance_ohm=float(validate_resistance(resistance, range_name)),
                          led_enabled=vdrop is not None)
            if vdrop is not None:
                result["vdrop_v"] = str(vdrop)
        return result
    if mode == "simulate":
        simulated = simulator or ControlSimulator()
        fixed_setup = command in (*FIXED_RANGE_COMMANDS, "set-cw")
        if fixed_setup and simulated.mode == "LIST":
            raise LoadError("list_selected", "Exit LIST explicitly before Fixed setup.")
        if fixed_setup and simulated.transient_enabled:
            raise LoadError("transient_enabled", "Disable Transient before Fixed setup.")
        result = _perform(simulated, command, current=current,
                        voltage=voltage, resistance=resistance, power=power, range_name=range_name,
                        confirmed=confirmed, stop=stop, allow_cv=True, allow_cr=True, allow_cw=True,
                        vdrop=vdrop)
        if slew:
            from .readback import fixed_slew_status

            simulated.fixed_cc_slew = slew
            observed = fixed_slew_status(simulated)
            if (not observed["configured"]
                    or any(Decimal(observed[key]) != Decimal(value) for key, value in slew.items())):
                raise LoadError("readback_mismatch", "Fixed CC slew did not match readback.", 3)
            result.update({key: observed[key] for key in slew})
        if fixed_setup:
            simulated.transient_configuration = None
            simulated.last_trigger = None
        return result
    with open_session(resource) as (session, runtime):
        result = _perform(session, command, current=current, voltage=voltage,
                          resistance=resistance, power=power,
                          range_name=range_name, confirmed=confirmed,
                          stop=stop, allow_cv=False, allow_cr=False, allow_cw=False)
        result["visa"] = runtime
    return result
