"""Explicit, CLI-only IT8812C validation; never promotes Product LIVE support.

IT8800 Programming Guide V2.1 (printed pp.40,46,48,50-52,54,56-57):
https://assets.testequity.com/te1/Documents/pdf/itech/ITECH_IT8800-Programming-Guide-EN.pdf
Physical Transient uses FIX + TRAN, not the simulator's internal TRAN mode.
Range responses must match these candidate semantics before any Input ON.
No automatic retries, rollback, Input OFF, local switch or error-queue reads.
"""

from datetime import datetime, timezone
from decimal import Decimal, localcontext

from . import control as c
from .errors import LoadError
from .identity import parse_identity


FUNCTIONS = {"cv": ("VOLT", "VOLTAGE"), "cr": ("RES", "RESISTANCE"),
             "cw": ("POW", "POWER"), "transient-cc": ("CURR", "CURRENT")}
MODES = {"continuous": "CONT", "pulse": "PULS", "toggle": "TOGG"}
TRANSIENT_QUERIES = {"level_a": "CURR:TRAN:ALEV", "level_b": "CURR:TRAN:BLEV",
                     "rise_slew": "CURR:SLEW:POS", "fall_slew": "CURR:SLEW:NEG"}


def _configuration(scope, settings):
    if not isinstance(settings, dict):
        raise LoadError("argument_error", "Supply the complete expected validation settings.")
    if scope == "transient-cc":
        return c.validate_transient_cc(settings)
    field, validator = {"cv": ("voltage", c.validate_voltage),
                        "cr": ("resistance", c.validate_resistance),
                        "cw": ("power", c.validate_power)}[scope]
    required = {field} if scope == "cw" else {field, "range"}
    if set(settings) != required:
        raise LoadError("argument_error", "Supply exactly the selected scope's setup fields.")
    if scope != "cw" and not isinstance(settings["range"], str):
        raise LoadError("invalid_range", "Range must be low or high.")
    with localcontext() as context:
        context.prec = max(28, len(str(settings[field])) + 8)
        value = (validator(settings[field]) if scope == "cw"
                 else validator(settings[field], settings["range"]))
    return {**settings, field: value}


def _number(session, query):
    raw = c._query(session, query)
    if not c.NUMBER.fullmatch(raw):
        raise LoadError("invalid_response", "Invalid validation numeric readback.", 3, query=query)
    value = Decimal(raw)
    if not value.is_finite():
        raise LoadError("invalid_response", "Nonfinite validation readback.", 3, query=query)
    return value


def _serial(settings):
    return {key: str(value) if isinstance(value, Decimal) else value
            for key, value in settings.items()}


def _snapshot(session, scope):
    fixed = c._query(session, "FUNC:MODE?").upper()
    function = c._query(session, "FUNC?").upper()
    if fixed not in {"FIX", "FIXED"} or function not in FUNCTIONS[scope]:
        raise LoadError("unsafe_mode", "Physical mode does not match validation scope.")
    enabled = c._flag(session, "TRAN?") == "ON"
    if c._flag(session, "INP:SHOR:STAT?") != "OFF":
        raise LoadError("unsafe_mode", "Short must be OFF.")
    if scope != "transient-cc" and enabled:
        raise LoadError("unsafe_mode", "Fixed validation requires Transient OFF.")
    if scope == "cv":
        raw_range = _number(session, "VOLT:RANG?")
        if raw_range not in (Decimal(18), Decimal(120)):
            raise LoadError("invalid_range_readback", "Unrecognized physical CV range.", 3,
                            range_readback_v=str(raw_range))
        if c._flag(session, "VOLT:RANG:AUTO?") != "OFF":
            raise LoadError("readback_mismatch", "CV automatic range must be disabled.", 3)
        settings = {"range": "low" if raw_range == 18 else "high",
                    "voltage": _number(session, "VOLT?")}
    elif scope == "cr":
        raw_range = _number(session, "RES:RANG?")
        if raw_range not in (Decimal(10), Decimal(7500)):
            raise LoadError("invalid_range_readback", "Unrecognized physical CR range.", 3,
                            range_readback_ohm=str(raw_range))
        settings = {"range": "low" if raw_range == 10 else "high",
                    "resistance": _number(session, "RES?")}
    elif scope == "cw":
        settings = {"power": _number(session, "POW?")}
    else:
        raw_mode = c._query(session, "CURR:TRAN:MODE?").upper()
        names = {"CONT": "continuous", "CONTINUOUS": "continuous",
                 "PULS": "pulse", "PULSE": "pulse", "TOGG": "toggle", "TOGGLE": "toggle"}
        if raw_mode not in names:
            raise LoadError("invalid_response", "Unknown physical Transient mode.", 3)
        settings = {"transient_mode": names[raw_mode],
                    "range": c._range_name(c._number(session, "CURR:RANG?"))}
        settings.update({field: _number(session, query + "?")
                         for field, query in TRANSIENT_QUERIES.items()})
        if settings["transient_mode"] == "continuous":
            settings.update(width_a=_number(session, "CURR:TRAN:AWID?"),
                            width_b=_number(session, "CURR:TRAN:BWID?"))
        elif settings["transient_mode"] == "pulse":
            settings["pulse_width"] = _number(session, "CURR:TRAN:AWID?")
        if c._query(session, "TRIG:SOUR?").upper() != "HOLD":
            raise LoadError("unsafe_trigger_source", "Validation requires software-only HOLD source.")
    validated = _configuration(scope, settings)
    return {"input_state": c._flag(session, "INP:STAT?"), "enabled": enabled,
            "function_mode": fixed, "function": function,
            "configuration": _serial(validated), "configuration_source": "instrument_readback"}


def _match(snapshot, expected):
    # Decimal comparison tolerates only representation differences, never rounding.
    observed = snapshot["configuration"]
    if any((Decimal(observed[key]) != value if isinstance(value, Decimal)
            else observed[key] != value) for key, value in expected.items()):
        raise LoadError("readback_mismatch", "Settings differ from the authorized validation setup.",
                        3, expected=_serial(expected), observed=observed)


def _stopping(stop):
    if stop is not None and stop.is_set():
        raise LoadError("stopping", "Validation mutation blocked by Stop.")


def _setup_guard(session, stop):
    _stopping(stop)
    c._input_off(session)
    if c._flag(session, "TRAN?") != "OFF" or c._flag(session, "INP:SHOR:STAT?") != "OFF":
        raise LoadError("unsafe_mode", "Setup requires Transient and Short OFF.")


def _sequence(scope, settings):
    sequence = ["FUNC:MODE FIX", f"FUNC {FUNCTIONS[scope][0]}"]
    if scope == "cv":
        ceiling = c.CV_RANGE_LIMITS[settings["range"]]
        sequence += ["VOLT:RANG:AUTO OFF", f"VOLT:RANG {ceiling}",
                     f"VOLT {settings['voltage']:f}"]
    elif scope == "cr":
        ceiling = c.CR_RANGE_LIMITS[settings["range"]][1]
        sequence += [f"RES:RANG {ceiling}", f"RES {settings['resistance']:f}"]
    elif scope == "cw":
        sequence += [f"POW {settings['power']:f}"]
    else:
        selector = f"{max(settings['level_a'], settings['level_b']):f}"
        sequence += [f"CURR:RANG {'MAX' if settings['range'] == 'high' else selector}",
                     f"CURR:TRAN:MODE {MODES[settings['transient_mode']]}"]
        sequence += [f"{query} {settings[field]:f}"
                     for field, query in TRANSIENT_QUERIES.items()]
        if settings["transient_mode"] == "continuous":
            sequence += [f"CURR:TRAN:AWID {settings['width_a']:f}",
                         f"CURR:TRAN:BWID {settings['width_b']:f}"]
        elif settings["transient_mode"] == "pulse":
            sequence += [f"CURR:TRAN:AWID {settings['pulse_width']:f}"]
        sequence += ["TRIG:SOUR HOLD"]
    return sequence


def _perform(session, command, scope, expected, sequence, stop):
    parse_identity(c._query(session, "*IDN?"))
    if command == "input-off":
        # Reuse the established explicit OFF and bounded confirmation path.
        return c._perform(session, command, current=None, voltage=None, resistance=None,
                          power=None, range_name=None, confirmed=False, stop=stop,
                          allow_cv=False, allow_cr=False, allow_cw=False)
    if command == "transient-status":
        return _snapshot(session, scope)
    _stopping(stop)
    if sequence:
        completed = []
        try:
            for write in ["SYST:REM", *sequence]:
                _setup_guard(session, stop)
                _stopping(stop)
                c._write(session, write)
                completed.append(write)
            snapshot = _snapshot(session, scope)
            _match(snapshot, expected)
            _setup_guard(session, stop)
            return snapshot
        except LoadError as exc:
            exc.details["completed_steps"] = completed
            raise

    def check():
        _stopping(stop)
        if command == "transient-disable":
            c._input_off(session)
            return None
        result = _snapshot(session, scope)
        _match(result, expected)
        wanted_input = "ON" if command == "transient-trigger" else "OFF"
        if result["input_state"] != wanted_input:
            raise LoadError("unsafe_input_state", f"Validation requires Input {wanted_input}.")
        wanted_enabled = scope == "transient-cc" and command != "transient-enable"
        if result["enabled"] != wanted_enabled:
            raise LoadError("unsafe_mode", "Transient enable state does not match the operation.")
        _stopping(stop)
        return result

    check()
    c._write(session, "SYST:REM")
    snapshot = check()
    _stopping(stop)
    if command == "input-on":
        try:
            c._write(session, "INP ON")
            if not c._confirm_input_state(session, "ON"):
                raise LoadError("input_on_not_confirmed", "Input ON was not confirmed.", 3)
        except LoadError as exc:
            exc.details.update(input_state="UNKNOWN", input_on_attempted=True)
            raise
        return {**snapshot, "input_state": "ON"}
    if command == "transient-trigger":
        c._write(session, "TRIG:IMM")
        return {**snapshot, "trigger_dispatched_at_utc": datetime.now(timezone.utc).isoformat(),
                "waveform_completion_known": False}
    enabled = command == "transient-enable"
    c._write(session, "TRAN ON" if enabled else "TRAN OFF")
    if (c._flag(session, "TRAN?") == "ON") != enabled:
        raise LoadError("readback_mismatch", "Transient enable readback did not match.", 3)
    c._input_off(session)
    return {"input_state": "OFF", "enabled": enabled,
            **({"configuration": snapshot["configuration"]} if snapshot else {})}


def execute(command, *, scope, mode, resource, current, voltage, resistance, power,
            range_name, transient, expected, confirmed, stop):
    if not isinstance(scope, str) or scope not in FUNCTIONS or mode not in {"live", "dry-run"}:
        raise LoadError("argument_error", "Hardware validation requires an explicit scope and LIVE/dry-run.")
    setup = "set-" + scope
    permitted = {setup, "input-on", "input-off"}
    if scope == "transient-cc":
        permitted.update(c.TRANSIENT_COMMANDS)
    if command not in permitted:
        raise LoadError("argument_error", "Command does not match hardware-validation scope.")
    supplied = {key: value for key, value in
                {"current": current, "voltage": voltage, "resistance": resistance,
                 "power": power, "range": range_name}.items() if value is not None}
    sequence = []
    if command == setup:
        if expected is not None or (scope == "transient-cc" and supplied):
            raise LoadError("argument_error", "Setup takes only its complete mode parameters.")
        if scope != "transient-cc" and transient is not None:
            raise LoadError("argument_error", "Transient parameters do not apply to Fixed validation.")
        expected = _configuration(scope, transient if scope == "transient-cc" else supplied)
        sequence = _sequence(scope, expected)
    else:
        if supplied or transient is not None:
            raise LoadError("argument_error", "Use validation settings for the expected setup.")
        if command in {"input-on", "transient-enable", "transient-trigger"}:
            expected = _configuration(scope, expected)
        elif expected is not None:
            raise LoadError("argument_error", "This command does not take expected settings.")
    if command == "input-on" and not confirmed:
        raise LoadError("confirmation_required", "Input ON requires explicit confirmation.")
    marker = {"validation_only": True, "validation_scope": scope}
    if mode == "dry-run":
        writes = {"input-on": "INP ON", "input-off": "INP OFF",
                  "transient-enable": "TRAN ON", "transient-disable": "TRAN OFF",
                  "transient-trigger": "TRIG:IMM"}
        actions = sequence or ([writes[command]] if command in writes else [])
        return {**marker, "command": command, "hardware_access": False,
                "planned_writes": ["SYST:REM", *actions] if actions else [],
                "expected_configuration": _serial(expected) if expected else None}
    with c.open_session(resource) as (session, runtime):
        result = _perform(session, command, scope, expected, sequence, stop)
        return {**result, **marker, "visa": runtime}
