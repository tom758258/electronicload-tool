"""Explicit, CLI-only IT8812C validation; never promotes Product LIVE support.

IT8800 Programming Guide V2.1 (printed pp.40,46,48,50-58):
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
from .readback import TRANSIENT_LEVEL_QUERIES, fixed_slew_status, transient_mode, transient_values
from .simulator import ControlSimulator
from .trigger import (SOURCE_TOKENS, TRIGGER_COMMANDS, TRIGGER_INTERVAL_QUERY,
                      TRIGGER_SOURCE_QUERY, read_source, software_trigger, validate_interval,
                      validate_setup)


FUNCTIONS = {"cv": ("VOLT", "VOLTAGE"), "cr": ("RES", "RESISTANCE"),
             "cw": ("POW", "POWER"), "transient-cc": ("CURR", "CURRENT"),
             "transient-cv": ("VOLT", "VOLTAGE"), "transient-cr": ("RES", "RESISTANCE"),
             "transient-cw": ("POW", "POWER")}
MODES = {"continuous": "CONT", "pulse": "PULS", "toggle": "TOGG"}


def _input_timer_snapshot(session):
    # IT8800 Programming Guide V2.1 printed pp.45–46, including IT8812C.
    # DELay's command-syntax line omits :DELay; its example and query include it.
    value = _number(session, "INP:TIM:DEL?")
    if value != value.to_integral_value() or not 1 <= value <= 60000:
        raise LoadError("invalid_response", "Invalid Input Timer seconds readback.", 3)
    return {"seconds": int(value), "enabled": c._flag(session, "INP:TIM?") == "ON",
            "input_state": c._flag(session, "INP:STAT?"),
            "state_source": "instrument_readback"}


def execute_input_timer(command, *, mode, resource, seconds, current, range_name,
                        expected, confirmed, stop):
    """CLI-only bounded CC validation. Normal Product/Worker Timer LIVE stays closed."""
    if mode not in {"live", "dry-run"} or command not in {*c.INPUT_TIMER_COMMANDS,
                                                           "input-on", "input-off"}:
        raise LoadError("argument_error", "Command/mode does not match Input Timer validation.")
    if current is not None or range_name is not None:
        raise LoadError("argument_error", "Use expected validation settings for Timer Input ON.")
    if command == "set-input-timer":
        seconds = c.validate_input_timer(seconds)
    if command == "input-on":
        if not confirmed:
            raise LoadError("confirmation_required", "Input ON requires explicit confirmation.")
        if not isinstance(expected, dict) or set(expected) != {"current", "range", "seconds"}:
            raise LoadError("argument_error", "Timer Input ON requires current, range and seconds.")
        selected = c.resolve_fixed_range("set-cc", expected["current"], expected["range"])
        expected = {"current": c.validate_current(expected["current"], selected),
                    "range": selected, "seconds": c.validate_input_timer(expected["seconds"])}
    elif expected is not None or confirmed:
        raise LoadError("argument_error", "Expected settings/confirmation apply only to Input ON.")
    writes = {"set-input-timer": f"INP:TIM:DEL {seconds}",
              "input-timer-enable": "INP:TIM 1", "input-timer-disable": "INP:TIM 0",
              "input-on": "INP ON", "input-off": "INP OFF"}
    marker = {"validation_only": True, "validation_scope": "input-timer"}
    if mode == "dry-run":
        return {**marker, "command": command, "hardware_access": False,
                "planned_writes": ["SYST:REM", writes[command]] if command in writes else [],
                "expected_configuration": _serial(expected) if expected else None}

    def guard(session):
        _stopping(stop)
        c._input_off(session)
        if command != "input-timer-disable":
            settings = c._settings(session)
            c._check_settings(session, settings)
            if (c._flag(session, "TRAN?") != "OFF"
                    or c._flag(session, "INP:SHOR:STAT?") != "OFF"):
                raise LoadError("unsafe_mode", "Timer validation requires Fixed CC, Short/Transient OFF.")
            if command == "input-on":
                timer = _input_timer_snapshot(session)
                if (timer["input_state"] != "OFF" or not timer["enabled"]
                        or timer["seconds"] != expected["seconds"]
                        or settings["range"] != expected["range"]
                        or Decimal(str(settings["current_a"])) != expected["current"]):
                    raise LoadError("readback_mismatch", "Timer/CC differs from authorized settings.", 3)
        _stopping(stop)

    with c.open_session(resource) as (session, runtime):
        parse_identity(c._query(session, "*IDN?"))
        if command == "input-off":
            result = c._perform(session, command, current=None, voltage=None, resistance=None,
                                power=None, range_name=None, confirmed=False, stop=stop,
                                allow_cv=False, allow_cr=False, allow_cw=False)
        elif command == "input-timer-status":
            result = _input_timer_snapshot(session)
        else:
            guard(session)
            c._write(session, "SYST:REM")
            guard(session)
            _stopping(stop)
            if command == "input-on":
                try:
                    c._write(session, writes[command])
                    if not c._confirm_input_state(session, "ON"):
                        raise LoadError("input_on_not_confirmed", "Input ON was not confirmed.", 3)
                except LoadError as exc:
                    exc.details.update(input_state="UNKNOWN", input_on_attempted=True)
                    raise
                result = {"input_state": "ON"}
            else:
                c._write(session, writes[command])
                result = _input_timer_snapshot(session)
                if ((command == "set-input-timer" and result["seconds"] != seconds)
                        or (command != "set-input-timer"
                            and result["enabled"] != (command == "input-timer-enable"))):
                    raise LoadError("readback_mismatch", "Timer readback did not match.", 3)
                c._input_off(session)
        return {**result, **marker, "visa": runtime}


def _configuration(scope, settings):
    if not isinstance(settings, dict):
        raise LoadError("argument_error", "Supply the complete expected validation settings.")
    if scope.startswith("transient-"):
        return c.validate_transient("set-" + scope, settings)
    field, validator = {"cv": ("voltage", c.validate_voltage),
                        "cr": ("resistance", c.validate_resistance),
                        "cw": ("power", c.validate_power)}[scope]
    required = {field} if scope == "cw" else {field, "range"}
    if set(settings) != required:
        raise LoadError("argument_error", "Supply exactly the selected scope's setup fields.")
    if scope != "cw" and not isinstance(settings["range"], str):
        raise LoadError("invalid_range", "Range must be auto, low or high.")
    if scope != "cw":
        settings = {**settings, "range": c.resolve_fixed_range(
            "set-" + scope, settings[field], settings["range"])}
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


def _snapshot(session, scope, *, require_hold=True):
    fixed = c._query(session, "FUNC:MODE?").upper()
    function = c._query(session, "FUNC?").upper()
    if fixed not in {"FIX", "FIXED"} or function not in FUNCTIONS[scope]:
        raise LoadError("unsafe_mode", "Physical mode does not match validation scope.")
    enabled = c._flag(session, "TRAN?") == "ON"
    if c._flag(session, "INP:SHOR:STAT?") != "OFF":
        raise LoadError("unsafe_mode", "Short must be OFF.")
    if not scope.startswith("transient-") and enabled:
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
        if c._flag(session, c.LED_QUERY) != "OFF":
            raise LoadError("unsafe_mode", "Plain CR validation requires confirmed LED OFF.")
        raw_range = _number(session, "RES:RANG?")
        if raw_range not in (Decimal(10), Decimal(7500)):
            raise LoadError("invalid_range_readback", "Unrecognized physical CR range.", 3,
                            range_readback_ohm=str(raw_range))
        settings = {"range": "low" if raw_range == 10 else "high",
                    "resistance": _number(session, "RES?")}
    elif scope == "cw":
        settings = {"power": _number(session, "POW?")}
    else:
        prefix = FUNCTIONS[scope][0]
        settings = {"transient_mode": transient_mode(session, prefix)}
        if scope == "transient-cc":
            settings["range"] = c._range_name(c._number(session, "CURR:RANG?"))
        elif scope in {"transient-cv", "transient-cr"}:
            low, high = (Decimal(18), Decimal(120)) if scope == "transient-cv" else (Decimal(10), Decimal(7500))
            raw_range = _number(session, f"{prefix}:RANG?")
            if raw_range not in (low, high):
                raise LoadError("invalid_range_readback", "Unrecognized physical Transient range.", 3)
            settings["range"] = "low" if raw_range == low else "high"
            if scope == "transient-cv" and c._flag(session, "VOLT:RANG:AUTO?") != "OFF":
                raise LoadError("readback_mismatch", "CV automatic range must be disabled.", 3)
        settings.update(transient_values(session, prefix, settings["transient_mode"]))
        if require_hold and c._query(session, TRIGGER_SOURCE_QUERY).upper() != "HOLD":
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


def _setup_guard(session, stop, *, on_input_off=None):
    _stopping(stop)
    c._input_off(session)
    if on_input_off is not None:
        on_input_off()
    if c._flag(session, "TRAN?") != "OFF" or c._flag(session, "INP:SHOR:STAT?") != "OFF":
        raise LoadError("unsafe_mode", "Setup requires Transient and Short OFF.")


def _sequence(scope, settings, *, set_hold=True):
    sequence = ["FUNC:MODE FIX", f"FUNC {FUNCTIONS[scope][0]}"]
    if scope == "cv":
        ceiling = c.CV_RANGE_LIMITS[settings["range"]]
        sequence += ["VOLT:RANG:AUTO OFF", f"VOLT:RANG {ceiling}",
                     f"VOLT {settings['voltage']:f}"]
    elif scope == "cr":
        return list(c._cr_sequence(settings["resistance"], settings["range"]))
    elif scope == "cw":
        sequence += [f"POW {settings['power']:f}"]
    else:
        prefix = FUNCTIONS[scope][0]
        if scope == "transient-cc":
            selector = f"{max(settings['level_a'], settings['level_b']):f}"
            sequence += [f"CURR:RANG {'MAX' if settings['range'] == 'high' else selector}"]
        elif scope == "transient-cv":
            sequence += ["VOLT:RANG:AUTO OFF", f"VOLT:RANG {c.CV_RANGE_LIMITS[settings['range']]}"]
        elif scope == "transient-cr":
            sequence += [f"RES:RANG {c.CR_RANGE_LIMITS[settings['range']][1]}"]
        sequence += [f"{prefix}:TRAN:MODE {MODES[settings['transient_mode']]}"]
        queries = (TRANSIENT_LEVEL_QUERIES if scope == "transient-cc" else
                   {"level_a": f"{prefix}:TRAN:ALEV", "level_b": f"{prefix}:TRAN:BLEV"})
        sequence += [f"{query} {settings[field]:f}" for field, query in queries.items()]
        if settings["transient_mode"] == "continuous":
            sequence += [f"{prefix}:TRAN:AWID {settings['width_a']:f}",
                         f"{prefix}:TRAN:BWID {settings['width_b']:f}"]
        elif settings["transient_mode"] == "pulse":
            sequence += [f"{prefix}:TRAN:AWID {settings['pulse_width']:f}"]
        if set_hold:
            sequence += ["TRIG:SOUR HOLD"]
    return sequence


def _perform(session, command, scope, expected, sequence, stop, *, trigger=None, trigger_scope=False):
    parse_identity(c._query(session, "*IDN?"))
    if command == "input-off":
        # Reuse the established explicit OFF and bounded confirmation path.
        return c._perform(session, command, current=None, voltage=None, resistance=None,
                          power=None, range_name=None, confirmed=False, stop=stop,
                          allow_cv=False, allow_cr=False, allow_cw=False)
    if command == "transient-status":
        return _snapshot(session, scope, require_hold=not trigger_scope)
    _stopping(stop)
    if sequence:
        completed = []
        try:
            _setup_guard(session, stop)
            c._check_mode_change(session, force_led_off=scope == "cr")
            c._mutation_off(session, stop)
            c._write(session, "SYST:REM")
            completed.append("SYST:REM")
            _setup_guard(session, stop)
            led_off = c._check_mode_change(session, force_led_off=scope == "cr")
            c._leave_cr_led(session, led_off, stop)
            if led_off:
                completed.append("led_off")
            for write in sequence:
                _setup_guard(session, stop)
                _stopping(stop)
                c._write(session, write)
                completed.append(write)
            snapshot = _snapshot(session, scope, require_hold=not trigger_scope)
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
        result = _snapshot(session, scope, require_hold=not trigger_scope)
        _match(result, expected)
        if trigger is not None:
            _check_trigger(session, trigger)
            if c._flag(session, "INP:TIM?") != "OFF":
                raise LoadError("input_timer_enabled", "Disable Input Timer before Trigger validation.")
        wanted_input = "ON" if command == "transient-trigger" else "OFF"
        if result["input_state"] != wanted_input:
            raise LoadError("unsafe_input_state", f"Validation requires Input {wanted_input}.")
        wanted_enabled = scope.startswith("transient-") and command != "transient-enable"
        if result["enabled"] != wanted_enabled:
            raise LoadError("unsafe_mode", "Transient enable state does not match the operation.")
        _stopping(stop)
        return result

    enabling_transient = command == "transient-enable"
    completed = []
    try:
        check()
        if enabling_transient:
            c._check_mode_change(session)
            c._mutation_off(session, stop)
        c._write(session, "SYST:REM")
        completed.append("SYST:REM")
        snapshot = check()
        if enabling_transient:
            led_off = c._check_mode_change(session)
            c._leave_cr_led(session, led_off, stop)
            if led_off:
                completed.append("led_off")
            c._mutation_off(session, stop)
        _stopping(stop)
    except LoadError as exc:
        if enabling_transient:
            exc.details["completed_steps"] = completed
        raise
    if command == "input-on":
        if trigger_scope:
            c._mutation_off(session, stop)
        try:
            c._write(session, "INP ON")
            if not c._confirm_input_state(session, "ON"):
                raise LoadError("input_on_not_confirmed", "Input ON was not confirmed.", 3)
        except LoadError as exc:
            exc.details.update(input_state="UNKNOWN", input_on_attempted=True)
            raise
        return {**snapshot, "input_state": "ON"}
    if command == "transient-trigger":
        if trigger_scope:
            _require_trigger_input_on(session, stop, "Transient")
        c._write(session, software_trigger(trigger["source"]) if trigger else "TRIG:IMM")
        return {**snapshot, "trigger_dispatched_at_utc": datetime.now(timezone.utc).isoformat(),
                "waveform_completion_known": False}
    c._write(session, "TRAN ON" if enabling_transient else "TRAN OFF")
    if (c._flag(session, "TRAN?") == "ON") != enabling_transient:
        raise LoadError("readback_mismatch", "Transient enable readback did not match.", 3)
    c._input_off(session)
    return {"input_state": "OFF", "enabled": enabling_transient,
            **({"configuration": snapshot["configuration"]} if snapshot else {})}


def _trigger_snapshot(session):
    source = read_source(c._query(session, TRIGGER_SOURCE_QUERY))
    return {"source": source, "interval_s": _trigger_interval(session)}


def _trigger_interval(session):
    interval = _number(session, TRIGGER_INTERVAL_QUERY)
    try:
        validate_interval(interval)
    except LoadError as exc:
        raise LoadError("invalid_response", "Trigger Timer readback is outside its documented bounds.", 3) from exc
    return interval


def _check_trigger(session, expected):
    actual = _trigger_snapshot(session)
    if any(actual[key] != value for key, value in expected.items()):
        raise LoadError("readback_mismatch", "Trigger Setup differs from the requested settings.", 3)
    return actual


def _configure_trigger(session, settings, writes, stop):
    readbacks = {"source": "UNCONFIRMED", "interval_s": "UNCONFIRMED"}
    try:
        c._mutation_off(session, stop)
        expected = {**_trigger_snapshot(session), **settings}
        c._mutation_off(session, stop)
        c._write(session, "SYST:REM")
        for write in writes:
            c._mutation_off(session, stop)
            writing_interval = write.startswith("TRIG:TIM ")
            if not writing_interval:
                # A Source write invalidates the earlier Timer readback.
                readbacks["interval_s"] = "UNCONFIRMED"
            c._write(session, write)
            if writing_interval:
                if _trigger_interval(session) != expected["interval_s"]:
                    readbacks["interval_s"] = "MISMATCH"
                    raise LoadError("readback_mismatch", "Trigger Timer did not match.", 3)
                readbacks["interval_s"] = "CONFIRMED"
        interval = _trigger_interval(session)
        readbacks["interval_s"] = "CONFIRMED" if interval == expected["interval_s"] else "MISMATCH"
        source = read_source(c._query(session, TRIGGER_SOURCE_QUERY))
        readbacks["source"] = "CONFIRMED" if source == expected["source"] else "MISMATCH"
        if "MISMATCH" in readbacks.values():
            raise LoadError("readback_mismatch", "Trigger Setup differs from the requested settings.", 3)
        c._input_off(session)
        return {"source": source, "interval_s": str(interval), "input_state": "OFF",
                "state_source": "instrument_readback"}
    except LoadError as exc:
        exc.details["trigger_readbacks"] = readbacks
        raise


def _require_trigger_input_on(session, stop, function):
    if c._flag(session, "INP:STAT?") != "ON":
        raise LoadError("input_not_on", f"{function} trigger requires Input ON.")
    _stopping(stop)


def _list_writes(configuration):
    ceiling = c.RANGE_LIMITS[configuration["range"]]
    steps = configuration["steps"]
    # Use a known zero fixed-current setting before selecting and arming LIST.
    writes = ["FUNC:MODE FIX", "FUNC CURR", "CURR 0", f"LIST:RANG {ceiling:g}",
              f"LIST:COUN {configuration['repeat_count']}", f"LIST:STEP {len(steps)}"]
    for index, step in enumerate(steps, 1):
        writes.extend([f"LIST:LEV {index},{step['current_a']:f}",
                       f"LIST:SLEW {index},{step['slew_a_per_us']:f}",
                       f"LIST:WID {index},{step['width_s']:f}"])
    return [*writes, "FUNC:MODE LIST"]


def _list_snapshot(session, configuration):
    mode, function = c._load_mode(session)
    if mode != "LIST" or function not in FUNCTIONS["transient-cc"]:
        raise LoadError("unsafe_mode", "Trigger validation requires selected CC LIST.")
    checks = {"CURR?": Decimal(0), "LIST:RANG?": Decimal(str(c.RANGE_LIMITS[configuration["range"]])),
              "LIST:COUN?": configuration["repeat_count"],
              "LIST:STEP?": len(configuration["steps"])}
    for index, step in enumerate(configuration["steps"], 1):
        checks.update({f"LIST:LEV? {index}": step["current_a"],
                       f"LIST:SLEW? {index}": step["slew_a_per_us"],
                       f"LIST:WID? {index}": step["width_s"]})
    for query, value in checks.items():
        if _number(session, query) != value:
            raise LoadError("readback_mismatch", "Physical LIST differs from the authorized settings.", 3,
                            query=query, list_selected=True)
    return {"function_mode": mode, "function": function,
            "configuration": {**configuration, "steps": [_serial(step) for step in configuration["steps"]]}}


def _validation_list(session, command, configuration, trigger, writes, stop):
    def guard():
        _stopping(stop)
        if command == "list-trigger":
            _require_trigger_input_on(session, stop, "LIST")
            if c._flag(session, "TRAN?") != "OFF" or c._flag(session, "INP:SHOR:STAT?") != "OFF":
                raise LoadError("unsafe_mode", "LIST trigger requires Transient and Short OFF.")
        else:
            _setup_guard(session, stop)
        if command != "list-exit" and c._flag(session, "INP:TIM?") != "OFF":
            raise LoadError("input_timer_enabled", "Disable Input Timer before LIST validation.")
        if trigger is not None:
            _check_trigger(session, trigger)
        if command == "list-exit":
            mode, function = c._load_mode(session)
            if mode != "LIST" or function not in FUNCTIONS["transient-cc"]:
                raise LoadError("unsafe_mode", "Only selected CC LIST can be exited.")
        return _list_snapshot(session, configuration) if trigger is not None else {}

    guard()
    if command == "set-list-cc":
        c._check_mode_change(session)
    if command == "list-trigger":
        _require_trigger_input_on(session, stop, "LIST")
    else:
        c._mutation_off(session, stop)
    c._write(session, "SYST:REM")
    result = guard()
    if command == "set-list-cc":
        c._leave_cr_led(session, c._check_mode_change(session), stop)
        for write in writes:
            _setup_guard(session, stop)
            c._mutation_off(session, stop)
            c._write(session, write)
        result = _list_snapshot(session, configuration)
    elif command == "input-on":
        c._mutation_off(session, stop)
        try:
            c._write(session, "INP ON")
            if not c._confirm_input_state(session, "ON"):
                raise LoadError("input_on_not_confirmed", "LIST Input ON was not confirmed.", 3)
        except LoadError as exc:
            exc.details.update(input_state="UNKNOWN", input_on_attempted=True)
            raise
        return {**result, "input_state": "ON"}
    elif command == "list-trigger":
        _require_trigger_input_on(session, stop, "LIST")
        c._write(session, software_trigger(trigger["source"]))
        return {**result, "input_state": "ON",
                "trigger_dispatched_at_utc": datetime.now(timezone.utc).isoformat(),
                "waveform_completion_known": False}
    else:
        c._mutation_off(session, stop)
        c._write(session, "FUNC:MODE FIX")
        if c._query(session, "FUNC:MODE?").upper() not in {"FIX", "FIXED"}:
            raise LoadError("readback_mismatch", "Fixed mode was not confirmed after LIST exit.", 3)
    c._input_off(session)
    return {**result, "input_state": "OFF"}


def _finite_list(configuration):
    settings = c.validate_list_cc(configuration)
    if settings["repeat_count"] == 65536:
        raise LoadError("argument_error", "Trigger validation requires finite LIST repeats.")
    return settings


def execute_trigger(command, *, mode, resource, source, interval, transient, list_configuration,
                    expected, confirmed, stop):
    """Use native SCPI only in the explicitly selected standalone CLI scope."""
    transient_setup = command in c.TRANSIENT_SETUP_COMMANDS
    list_setup = command == "set-list-cc"
    needs_expected = command in {"input-on", "transient-enable", "transient-trigger", "list-trigger"}
    if mode not in {"live", "dry-run"} or command not in {
        *TRIGGER_COMMANDS, *c.TRANSIENT_SETUP_COMMANDS, "transient-enable", "transient-disable",
        "transient-trigger", "set-list-cc", "list-trigger", "list-exit", "input-on", "input-off"
    }:
        raise LoadError("argument_error", "Command/mode does not match Trigger validation.")
    if (not transient_setup and transient is not None) or (not list_setup and list_configuration is not None):
        raise LoadError("argument_error", "Dynamic setup arguments apply only to their setup command.")
    if (not needs_expected and expected is not None) or (confirmed and command != "input-on"):
        raise LoadError("argument_error", "Unexpected Trigger validation arguments.")
    if command == "input-on" and not confirmed:
        raise LoadError("confirmation_required", "Input ON requires explicit confirmation.")
    configuration = trigger = scope = None
    writes = []
    if command == "trigger-configure":
        # The contributor script supplies both fields when restoring a full snapshot.
        trigger = validate_setup(source, interval, restoring=interval is not None)
        if interval is not None:
            writes.append(f"TRIG:TIM {trigger['interval_s']:f}")
        writes.append(f"TRIG:SOUR {SOURCE_TOKENS[source]}")
    elif transient_setup:
        scope = command.removeprefix("set-")
        configuration = _configuration(scope, transient)
        writes = _sequence(scope, configuration, set_hold=False)
    elif list_setup:
        configuration = _finite_list(list_configuration)
        writes = _list_writes(configuration)
    elif needs_expected:
        if (not isinstance(expected, dict) or set(expected) != {"function", "configuration", "trigger"}
                or not isinstance(expected["trigger"], dict)
                or set(expected["trigger"]) not in ({"source"}, {"source", "interval_s"})):
            raise LoadError("argument_error", "Supply function, complete configuration and expected Trigger Setup.")
        trigger = validate_setup(**expected["trigger"])
        function = expected["function"]
        if function == "LIST" and command in {"input-on", "list-trigger"}:
            configuration = _finite_list(expected["configuration"])
        elif function in ("CC", "CV", "CR", "CW") and command != "list-trigger":
            scope = f"transient-{function.lower()}"
            configuration = _configuration(scope, expected["configuration"])
        else:
            raise LoadError("argument_error", "Function does not match the Trigger validation command.")
        if command in {"transient-trigger", "list-trigger"}:
            writes = [software_trigger(trigger["source"])]
        else:
            writes = ["INP ON" if command == "input-on" else "TRAN ON"]
    elif command in {"input-off", "transient-disable", "list-exit"}:
        writes = [{"input-off": "INP OFF", "transient-disable": "TRAN OFF",
                   "list-exit": "FUNC:MODE FIX"}[command]]
    marker = {"validation_only": True, "validation_scope": "trigger"}
    if mode == "dry-run":
        return {**marker, "command": command, "hardware_access": False,
                "planned_writes": ["SYST:REM", *writes] if writes else []}
    if command == "input-on" and trigger["source"] == "MANUAL":
        raise LoadError("unsupported_live_scope",
                        "MANUAL Trigger powered contributor LIVE is not physically validated.")
    with c.open_session(resource) as (session, runtime):
        if scope is not None or command in {"input-off", "transient-disable"}:
            setup_writes = writes if transient_setup else []
            result = _perform(session, command, scope or "transient-cc", configuration, setup_writes,
                              stop, trigger=trigger, trigger_scope=True)
        else:
            parse_identity(c._query(session, "*IDN?"))
            if command == "trigger-status":
                result = {**_serial(_trigger_snapshot(session)), "input_state": c._flag(session, "INP:STAT?"),
                          "state_source": "instrument_readback"}
            elif command == "trigger-configure":
                result = _configure_trigger(session, trigger, writes, stop)
            else:
                result = _validation_list(session, command, configuration, trigger, writes, stop)
        return {**result, **marker, "visa": runtime}


def _execute_led_or_slow_rise(command, *, scope, mode, resource, current, resistance,
                              range_name, vdrop, stop):
    permitted = ({"set-cc", *c.CR_COMMANDS, "cr-led-status"} if scope == "cr-led"
                 else {"set-cc", *c.SLOW_RISE_COMMANDS})
    if command not in permitted:
        raise LoadError("argument_error", "Command does not match hardware-validation scope.")
    configuration = None
    if command in {"set-cc", *c.CR_COMMANDS}:
        field = "current" if command == "set-cc" else "resistance"
        value = current if command == "set-cc" else resistance
        if (value is None or (command == "set-cc" and resistance is not None)
                or (command in c.CR_COMMANDS and current is not None)):
            raise LoadError("argument_error", "Supply only the selected mode's setup fields.")
        range_name = c.resolve_fixed_range(command, value, range_name)
        configuration = {field: str(value), "range": range_name}
        if vdrop is not None:
            configuration["vdrop"] = str(vdrop)
    elif any(value is not None for value in (current, resistance, range_name, vdrop)):
        raise LoadError("argument_error", "This command does not accept setup fields.")
    marker = {"validation_only": True, "validation_scope": scope}
    if mode == "dry-run":
        return {**marker, "command": command, "hardware_access": False,
                "expected_configuration": configuration}
    with c.open_session(resource) as (session, runtime):
        result = c._perform(session, command, current=current, voltage=None, resistance=resistance,
                            power=None, range_name=range_name, confirmed=False, stop=stop,
                            allow_cv=False, allow_cr=False, allow_cw=False,
                            vdrop=vdrop, cc_mode_scope=scope)
        return {**result, **marker, "visa": runtime}


def execute(command, *, scope, mode, resource, current, voltage, resistance, power,
            range_name, transient, expected, confirmed, stop, vdrop=None):
    if scope in ("cr-led", "current-slow-rise"):
        if (mode not in {"live", "dry-run"} or confirmed
                or any(value is not None for value in (voltage, power, transient, expected))):
            raise LoadError("argument_error", "Use the explicit validation scope with LIVE or dry-run.")
        return _execute_led_or_slow_rise(command, scope=scope, mode=mode, resource=resource,
                                         current=current, resistance=resistance, range_name=range_name,
                                         vdrop=vdrop, stop=stop)
    if not isinstance(scope, str) or scope not in FUNCTIONS or mode not in {"live", "dry-run"}:
        raise LoadError("argument_error", "Hardware validation requires an explicit scope and LIVE/dry-run.")
    setup = "set-" + scope
    permitted = {setup, "input-on", "input-off"}
    if scope.startswith("transient-"):
        permitted.update(set(c.TRANSIENT_COMMANDS) - c.TRANSIENT_SETUP_COMMANDS.keys())
    if command not in permitted:
        raise LoadError("argument_error", "Command does not match hardware-validation scope.")
    supplied = {key: value for key, value in
                {"current": current, "voltage": voltage, "resistance": resistance,
                 "power": power, "range": range_name}.items() if value is not None}
    sequence = []
    if command == setup:
        if expected is not None or (scope.startswith("transient-") and supplied):
            raise LoadError("argument_error", "Setup takes only its complete mode parameters.")
        if not scope.startswith("transient-") and transient is not None:
            raise LoadError("argument_error", "Transient parameters do not apply to Fixed validation.")
        expected = _configuration(scope, transient if scope.startswith("transient-") else supplied)
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
        actions = (["RES:LED:STAT 0", *sequence] if sequence and scope == "cr" else sequence)
        actions = actions or ([writes[command]] if command in writes else [])
        return {**marker, "command": command, "hardware_access": False,
                "planned_writes": ["SYST:REM", *actions] if actions else [],
                "expected_configuration": _serial(expected) if expected else None}
    with c.open_session(resource) as (session, runtime):
        result = _perform(session, command, scope, expected, sequence, stop)
        return {**result, **marker, "visa": runtime}


def validate_sequence_trial(loops, steps):
    """Limit contributor validation to the four bounded script trials."""
    setups = (
        ("set-cc", {"current": Decimal("0.1"), "range": "low"}),
        ("set-cv", {"voltage": Decimal("3"), "range": "low"}),
        ("set-cr", {"resistance": Decimal("1000"), "range": "high"}),
        ("set-cw", {"power": Decimal("0.10")}),
    )
    trial_steps = [
        ("input-on", {}), ("input-state", {}), ("wait", {"seconds": Decimal("0.25")}),
        ("measure", {}), ("input-off", {}), ("input-state", {}),
    ]
    if loops != 1 or steps[0] not in setups or steps[1:] != trial_steps:
        raise LoadError("unsupported_live_scope",
                        "Sequence hardware validation permits only the four fixed trials from live-cli-check.ps1.")


def sequence_fixed_setup(session, action, settings, stop, *, on_remote_attempt, on_input_off):
    """Apply the authorized Fixed setup without changing other instrument settings."""
    scope = action.removeprefix("set-")
    simulated = isinstance(session, ControlSimulator)

    def guard(*, after_remote=False):
        c._fixed_setup_guard(session, stop, fixed_only=True,
                             on_input_off=on_input_off if after_remote else None)
        if (c._check_mode_change(session)
                or ((simulated or scope == "cr") and c._flag(session, c.LED_QUERY) != "OFF")):
            raise LoadError("cr_led_enabled", "Disable CR-LED before Sequence setup.")

    if scope == "cc":
        if simulated and c._flag(session, c.LED_QUERY) != "OFF":
            raise LoadError("cr_led_enabled", "Disable CR-LED before Sequence setup.")
        c._set_fixed_cc(session, settings["current"], settings["range"], stop, "sequence",
                        on_remote_attempt=on_remote_attempt, on_input_off=on_input_off)
        if simulated and "rise_slew" in settings:
            slew = c.validate_fixed_cc_slew(settings["range"], settings["rise_slew"], settings["fall_slew"])
            c._set_simulated_fixed_cc_slew(session, slew)
    else:
        guard()
        c._mutation_off(session, stop)
        on_remote_attempt()
        c._write(session, "SYST:REM")
        guard(after_remote=True)
        if simulated:
            c._mutation_off(session, stop)
            if scope == "cv":
                session.configure_cv(settings["voltage"], settings["range"])
            elif scope == "cr":
                session.configure_cr(settings["resistance"], settings["range"])
            else:
                session.configure_cw(settings["power"])
        else:
            for write in _sequence(scope, settings):
                c._fixed_setup_guard(session, stop, fixed_only=True)
                if write.startswith(("RES:RANG ", "RES ")) and c._flag(session, c.LED_QUERY) != "OFF":
                    raise LoadError("cr_led_enabled", "Disable CR-LED before Sequence setup.")
                c._mutation_off(session, stop)
                c._write(session, write)
    _sequence_fixed_readback(session, scope, settings, stop)


def _sequence_fixed_readback(session, scope, expected, stop):
    if scope == "cc":
        observed = c._settings(session)
        if isinstance(session, ControlSimulator):
            c._check_settings(session, observed)
        if (observed["function_mode"] not in {"FIX", "FIXED"}
                or observed["function"] not in {"CURR", "CURRENT"}
                or observed["range"] != expected["range"]
                or not c._current_within(observed["current_a"], expected["current"], expected["range"])):
            raise LoadError("readback_mismatch", "CC settings differ from the Sequence setup.", 3)
        if "rise_slew" in expected:
            slew = fixed_slew_status(session)
            if not slew["configured"] or any(
                Decimal(slew[f"{field}_a_per_us"]) != expected[field] for field in ("rise_slew", "fall_slew")
            ):
                raise LoadError("readback_mismatch", "CC slew differs from the Sequence setup.", 3)
    else:
        snapshot = _snapshot(session, scope)
        try:
            _match(snapshot, expected)
        except LoadError as exc:
            # A settings mismatch skips the final OFF check. Keep the Input reading.
            exc.details["input_state"] = snapshot["input_state"]
            raise
    c._mutation_off(session, stop)


def sequence_input_on(session, setup, stop, *, on_attempt, on_remote_attempt, on_input_off):
    """Recheck the exact last setup and interlocks before and after Remote."""
    action, settings = setup
    scope = action.removeprefix("set-")

    def check():
        _setup_guard(session, stop, on_input_off=on_input_off)
        _sequence_fixed_readback(session, scope, settings, stop)
        if c._flag(session, "INP:TIM?") != "OFF":
            raise LoadError("input_timer_enabled", "Disable Input Timer before Sequence Input ON.")
        if scope != "cc" and c._flag(session, c.SLOW_RISE_QUERY) != "OFF":
            raise LoadError("current_slow_rise_enabled", "Disable Current Slow Rise before Input ON.")
        c._mutation_off(session, stop)
        return {}

    c._turn_input_on(session, check, on_attempt=on_attempt, on_remote_attempt=on_remote_attempt)
