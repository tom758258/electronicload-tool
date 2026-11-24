"""Isolated IT8812C settings trials. No trial sends Input ON or a trigger."""

from decimal import Decimal

from . import control as c
from . import readback
from .errors import LoadError
from .hardware_validation import _stopping, _trigger_snapshot, list_program_writes
from .trigger import SOURCE_TOKENS
from .identity import MODEL_ID, parse_identity
from .visa import open_request_session, validate_request_connection


COMMAND = "contributor-settings"
READBACK_TRIALS = ("fixed-cc", "fixed-cv", "fixed-cr", "fixed-cw", "transient", "list", "average")
SHARED_READBACK_TRIALS = ("von", "slew", "short")
TRIALS = ("von", "slew", "short", "list-cc", *READBACK_TRIALS)
_FIXED = {"FIX", "FIXED"}
_CURRENT = {"CURR", "CURRENT"}


def _guard(session, *, modes=_FIXED, short_trial=False):
    try:
        c._input_off(session)
    except LoadError as exc:
        exc.details["manual_action_required"] = True
        raise
    mode, function = c._load_mode(session)
    if mode not in modes or function not in _CURRENT or c._flag(session, "TRAN?") != "OFF":
        raise LoadError("unsafe_mode", "The isolated settings trial requires CC and Transient OFF.")
    if c._flag(session, "INP:TIM?") != "OFF":
        raise LoadError("input_timer_enabled", "Disable Input Timer before the isolated settings trial.")
    if not short_trial and c._flag(session, readback.SHORT_QUERY) != "OFF":
        raise LoadError("unsafe_mode", "Short must be OFF for the isolated settings trial.")


def _mismatch(message):
    return LoadError("readback_mismatch", message, 3)


def _exercise_and_restore(session, stop, exercise, restore):
    changed = False
    failure = None

    def write(command):
        nonlocal changed
        _stopping(stop)
        c._input_off(session)
        changed = True
        c._write(session, command)

    try:
        result = exercise(write)
    except (LoadError, KeyboardInterrupt) as exc:
        failure = exc if isinstance(exc, LoadError) else LoadError("interrupted", "The settings trial was interrupted.", 3)
    if changed:
        # A semantic mismatch or cooperative Stop leaves the exchange trusted.
        # A failed/ambiguous exchange must never cause blind cleanup writes.
        if failure is not None and failure.code not in {"readback_mismatch", "stopping", "input_not_off"}:
            raise _restore_error(failure) from failure
        try:
            restore()
            c._input_off(session)
        except (LoadError, KeyboardInterrupt) as exc:
            raise _restore_error(failure, exc) from exc
    if failure is not None:
        failure.details.update(restored=changed, input_state="OFF" if changed else "UNKNOWN")
        raise failure
    return {**result, "restored": True, "input_state": "OFF"}


def _restore_error(failure, cleanup=None):
    return LoadError("settings_restore_unconfirmed",
                     "Keep the source and DUT isolated and verify Input OFF and the original settings at the instrument.",
                     3, manual_action_required=True, input_state="UNKNOWN",
                     original_error=failure.as_dict() if failure else None,
                     restoration_error=cleanup.as_dict() if isinstance(cleanup, LoadError) else None)


def _von(session):
    values = readback.von_status(session)
    return c.validate_von(values["threshold_v"], values["latch"]), values["latch"]


def _von_writes(settings):
    voltage, latch = settings
    return (f"VOLT:ON {voltage:f}", f"VOLT:LATC {int(latch == 'on')}")


def _slew(session, range_name):
    if readback._range(session, "CC")[0] != range_name:
        raise LoadError("unsafe_mode", "The CC Range changed during the same-range Slew trial.", 3)
    values = readback.fixed_slew_status(session)
    rise, fall = (Decimal(values[field + "_a_per_us"]) for field in readback.SLEW_QUERIES)
    c.validate_fixed_cc_slew(range_name, rise, fall)
    return rise, fall


def _slew_writes(settings):
    return tuple(f"{stem} {value:f}" for stem, value in zip(readback.SLEW_QUERIES.values(), settings))


def _round_trip(session, trial):
    if trial == "von":
        read, commands = lambda: _von(session), _von_writes
        baseline = read()
        candidate = (c.validate_von(1 if baseline[0] != 1 else 2, "off"), "off")
    else:
        range_name = readback._range(session, "CC")[0]
        read, commands = lambda: _slew(session, range_name), _slew_writes
        baseline = read()
        candidate = tuple(Decimal(value) for value in ("0.1", "0.2"))
        if baseline == candidate:
            candidate = candidate[::-1]
        c.validate_fixed_cc_slew(range_name, *candidate)

    def exercise(write):
        if read() != baseline:
            raise _mismatch("The original settings changed before the trial.")
        for command in commands(candidate):
            write(command)
        if read() != candidate:
            raise _mismatch("The test settings did not match the instrument readback.")
        return {"original": [str(value) for value in baseline], "observed": [str(value) for value in candidate]}

    def restore():
        _guard(session)
        if trial == "slew" and readback._range(session, "CC")[0] != range_name:
            raise _mismatch("Cannot restore Slew because the CC Range changed.")
        for command in commands(baseline):
            c._input_off(session)
            c._write(session, command)
        if read() != baseline:
            raise _mismatch("The restored settings did not match the original readback.")

    if trial == "von":
        original = {"threshold_v": baseline[0], "latch": baseline[1]}
    else:
        rise, fall = baseline
        original = {"range": range_name, "rise_slew_a_per_us": rise, "fall_slew_a_per_us": fall}
    return exercise, restore, original


def _short_trial(session):
    def exercise(write):
        if c._flag(session, readback.SHORT_QUERY) != "OFF":
            raise LoadError("unsafe_mode", "Short must start OFF for the isolated trial.")
        write("INP:SHOR 1")
        c._input_off(session)
        if c._flag(session, readback.SHORT_QUERY) != "ON":
            raise _mismatch("Short ON with Input OFF was not confirmed.")
        return {"short_on_confirmed": True, "short_state": "OFF"}

    def restore():
        if c._flag(session, "INP:STAT?") != "OFF":
            # An unexpected Input ON is a failure even with the terminals isolated.
            c._turn_input_off(session)
        _guard(session, short_trial=True)
        c._write(session, "INP:SHOR 0")
        if c._flag(session, readback.SHORT_QUERY) != "OFF":
            raise _mismatch("Short OFF was not confirmed.")

    return exercise, restore, {"input_state": "OFF", "short_state": "OFF"}


def _list_settings(session):
    raw = readback._list_configuration(session)
    if raw["range"] is None:
        raise LoadError("list_baseline_unrestorable", "The original LIST current range cannot be restored.", 3)
    return c.validate_list_cc({key: raw[key] for key in c.LIST_FIELDS})


def _list_trial(session, slot, previous_slot_width):
    fixed = readback._fixed(session, "CC")
    baseline = _list_settings(session)
    width = baseline["steps"][0]["width_s"]
    first = next(Decimal(value) for value in (1, 2, 3) if value not in (width, previous_slot_width))
    second = Decimal(2) if first == 1 else Decimal(1)

    def with_width(value):
        step, *rest = baseline["steps"]
        return c.validate_list_cc({**baseline, "steps": [{**step, "width_s": value}, *rest]})

    a, b = with_width(first), with_width(second)

    def require_list(expected, label):
        mode, function = c._load_mode(session)
        if mode != "LIST" or function not in _CURRENT or _list_settings(session) != expected:
            raise _mismatch(f"LIST {label} did not match the expected configuration.")

    def exercise(write):
        if c._check_mode_change(session) or _list_settings(session) != baseline:
            raise _mismatch("The original LIST or its mode interlocks changed before the trial.")
        # Establish a known slot baseline before testing a different Save.
        write(f"LIST:WID 1,{second:f}")
        write("FUNC:MODE LIST")
        require_list(b, "B baseline")
        write(f"LIST:SAV {slot}")
        write(f"LIST:WID 1,{first:f}")
        require_list(a, "A before baseline Recall")
        write(f"LIST:RCL {slot}")
        require_list(b, "Baseline Recall")

        write(f"LIST:WID 1,{first:f}")
        require_list(a, "A")
        write(f"LIST:SAV {slot}")
        write(f"LIST:WID 1,{second:f}")
        require_list(b, "B before final Recall")
        write(f"LIST:RCL {slot}")
        require_list(a, "Final Recall")
        return {"a_width_s": str(first), "b_width_s": str(second),
                "save_confirmed": True, "recall_confirmed": True,
                "exited_list": True, "saved_slot": slot}

    def restore():
        _guard(session, modes=_FIXED | {"LIST"})
        if c._load_mode(session)[0] == "LIST":
            c._write(session, "FUNC:MODE FIX")
        _guard(session)
        observed = _list_settings(session)
        if observed != baseline:
            if observed in (a, b):
                writes = (f"LIST:WID 1,{width:f}",)
            else:
                writes = list_program_writes(baseline)
            for command in writes:
                c._input_off(session)
                c._write(session, command)
        if _list_settings(session) != baseline or readback._fixed(session, "CC") != fixed:
            raise _mismatch("The original LIST buffer and Fixed CC settings were not restored.")

    return exercise, restore, {"mode": "fixed", "function": "CC", "fixed_cc": fixed, "working_list": baseline}



def _selection_guard(session, *, allow_transient=False):
    """Only switch a known, unloaded mode with all relevant interlocks OFF."""
    c._input_off(session)
    original = readback._selection(session)
    modes = {"fixed", "list", "transient"} if allow_transient else {"fixed", "list"}
    if (original["active_mode"] not in modes or (original["transient_enabled"] and not allow_transient)
            or c._flag(session, readback.SHORT_QUERY) != "OFF"
            or c._flag(session, "INP:TIM?") != "OFF"
            or c._flag(session, c.SLOW_RISE_QUERY) != "OFF"
            or c._check_mode_change(session)):
        raise LoadError("unsafe_mode", "Settings mode changes require Input, Short, Timer, Transient and Slow Rise OFF.")
    if original["active_mode"] == "list" and (original["function"] != "CC" or original["transient_enabled"]):
        raise LoadError("unsafe_mode", "Only CC LIST with Transient OFF can be restored.")
    return original


def _select_fixed(session, function, write, *, allow_transient=False):
    state = _selection_guard(session, allow_transient=allow_transient)
    if function == "CR" and c._flag(session, c.LED_QUERY) != "OFF":
        raise LoadError("unsafe_mode", "Plain CR settings require LED OFF.")
    if state["transient_enabled"]:
        write("TRAN OFF")
        state = _selection_guard(session)
    if state["active_mode"] == "list":
        write("FUNC:MODE FIX")
        state = _selection_guard(session)
    if state["function"] != function:
        write(f"FUNC {c.TRANSIENT_FUNCTIONS[function]}")
    state = _selection_guard(session)
    if state["active_mode"] != "fixed" or state["function"] != function:
        raise _mismatch("Fixed mode selection did not match the settings trial.")


def _with_selection(session, original, function, exercise, restore, baseline):
    target_changed = False
    original_transient = None
    original_trigger = None
    if original["transient_enabled"]:
        original_transient = readback._transient(session, original)["configuration"]
        original_trigger = _trigger_snapshot(session)
        if (original["function"] != "CW" and original_transient["range"] not in {"low", "high"}):
            raise LoadError("unsafe_mode", "The original Transient range cannot be restored.")
        if original_trigger["source"] != "HOLD":
            raise LoadError("unsafe_mode", "An enabled Transient baseline requires HOLD for safe isolated restoration.")

    def run(write):
        if original_transient is not None:
            if (readback._transient(session, original)["configuration"] != original_transient
                    or _trigger_snapshot(session) != original_trigger):
                raise _mismatch("Original Transient or Trigger changed before testing.")
        _select_fixed(session, function, write, allow_transient=original_transient is not None)

        def exercise_write(command):
            nonlocal target_changed
            target_changed = True
            write(command)

        return exercise(exercise_write)

    def cleanup():
        def write(command):
            c._input_off(session)
            c._write(session, command)

        if target_changed:
            restore()
        _select_fixed(session, original["function"], write)
        if original["active_mode"] == "list":
            write("FUNC:MODE LIST")
        if original_transient is not None:
            if (readback._transient(session, original)["configuration"] != original_transient
                    or _trigger_snapshot(session) != original_trigger):
                raise _mismatch("Original Transient or Trigger was not restored before enabling.")
            _selection_guard(session)
            write("TRAN ON")
        if readback._selection(session) != original:
            raise _mismatch("Original mode selection was not restored.")

    return run, cleanup, {"selection": original, **baseline,
                          **({"original_transient": original_transient, "original_trigger": original_trigger}
                             if original_transient is not None else {})}


def _fixed_readback_trial(session, function):
    baseline = readback._fixed(session, function)
    field = readback.FIXED_FIELDS[function]
    range_name = baseline["range"]
    validators = {"CC": c.validate_current, "CV": c.validate_voltage,
                  "CR": c.validate_resistance, "CW": c.validate_power}

    def validate(value):
        validator = validators[function]
        return validator(value) if function == "CW" else validator(value, range_name)

    original = validate(baseline[field])
    candidates = {"CC": ("0.1", "0.2"), "CV": ("3", "4"),
                  "CR": ("5", "10") if range_name == "low" else ("1000", "2000"),
                  "CW": ("0.10", "0.20")}[function]
    if function == "CV" and baseline["range_auto"] and range_name == "high":
        # Stay close to the validated original in the automatically selected range.
        candidates = (str(original + Decimal("0.01")), str(original - Decimal("0.01")))
        candidate = validate(candidates[0] if original < Decimal("120") else candidates[1])
    else:
        candidate = next(validate(value) for value in candidates if Decimal(value) != original)
    prefix = c.TRANSIENT_FUNCTIONS[function]

    def observe(value):
        snapshot = readback.snapshot(session, "fixed-status")
        if (snapshot["input_state"] != "OFF" or snapshot["function"] != function
                or snapshot["range"] != range_name or Decimal(str(snapshot[field])) != value
                or snapshot.get("range_auto") != baseline.get("range_auto")):
            raise _mismatch("Fixed readback differs from the expected setting.")
        return snapshot

    def exercise(write):
        observe(original)
        write(f"{prefix} {candidate:f}")
        return {"observed": observe(candidate), "expected_setpoint": str(candidate)}

    def restore():
        _selection_guard(session)
        # Setpoint-only changes preserve the Range selector; do not attempt a
        # restore into a different or unrecognized range.
        if function != "CW" and readback._range(session, function)[0] != range_name:
            raise _mismatch("Fixed Range changed; original setpoint cannot be safely restored.")
        c._input_off(session)
        c._write(session, f"{prefix} {original:f}")
        observe(original)

    return exercise, restore, {"fixed": baseline}


def _transient_readback_trial(session):
    def settings():
        mode = readback.transient_mode(session, "CURR")
        return c.validate_transient_cc({
            "range": readback._range(session, "CC")[0], "transient_mode": mode,
            **readback.transient_values(session, "CURR", mode),
        })

    baseline = settings()
    trigger = _trigger_snapshot(session)
    candidate = c.validate_transient_cc({
        **baseline, "level_a": Decimal("0.2") if baseline["level_a"] == Decimal("0.1") else Decimal("0.1"),
    })
    source_changed = trigger["source"] != "HOLD"

    def exercise(write):
        _guard(session)
        if settings() != baseline or _trigger_snapshot(session) != trigger:
            raise _mismatch("Original Transient settings changed before testing.")
        write(f"CURR:TRAN:ALEV {candidate['level_a']:f}")
        if source_changed:
            write("TRIG:SOUR HOLD")
        if settings() != candidate or _trigger_snapshot(session)["source"] != "HOLD":
            raise _mismatch("Transient setup did not match.")
        _guard(session)
        write("TRAN ON")
        observed = readback.snapshot(session, "transient-status")
        wanted = readback.transient_settings("CC", candidate)
        actual = observed["configuration"]
        matches = set(actual) == set(wanted) and all(
            actual[key] == value if key in {"range", "transient_mode"}
            else Decimal(str(actual[key])) == Decimal(str(value))
            for key, value in wanted.items()
        )
        if (not observed["enabled"] or observed["input_state"] != "OFF"
                or observed["function"] != "CC" or not matches or settings() != candidate):
            raise _mismatch("Transient readback did not match the enabled test configuration.")
        return {"observed": observed, "expected_level_a": str(candidate["level_a"])}

    def restore():
        c._input_off(session)
        if c._flag(session, "TRAN?") != "OFF":
            c._write(session, "TRAN OFF")
        _guard(session)
        if readback._range(session, "CC")[0] != baseline["range"]:
            raise _mismatch("Transient Range changed; cannot restore level A.")
        c._write(session, f"CURR:TRAN:ALEV {baseline['level_a']:f}")
        if source_changed:
            c._input_off(session)
            c._write(session, f"TRIG:TIM {trigger['interval_s']:f}")
            c._input_off(session)
            c._write(session, f"TRIG:SOUR {SOURCE_TOKENS[trigger['source']]}")
        if settings() != baseline or _trigger_snapshot(session) != trigger:
            raise _mismatch("Transient or Trigger settings were not restored.")

    return exercise, restore, {"transient": baseline, "trigger": trigger}


def _list_readback_trial(session):
    baseline = _list_settings(session)
    fixed = readback._fixed(session, "CC")
    original = baseline["steps"][0]["width_s"]
    candidate = Decimal(2) if original == 1 else Decimal(1)
    first, *rest = baseline["steps"]
    expected = c.validate_list_cc({**baseline, "steps": [{**first, "width_s": candidate}, *rest]})

    def exercise(write):
        _guard(session)
        if _list_settings(session) != baseline:
            raise _mismatch("Original LIST buffer changed before testing.")
        write(f"LIST:WID 1,{candidate:f}")
        write("FUNC:MODE LIST")
        observed = readback.snapshot(session, "list-status")
        configuration = observed["configuration"]
        # Public readback preserves width and slew as decimal strings.
        # Normalize only this trusted snapshot, not caller-provided LIST JSON.
        actual = {key: configuration[key] for key in c.LIST_FIELDS}
        actual["steps"] = [
            {field: c._decimal(value) for field, value in step.items()}
            for step in configuration["steps"]
        ]
        if (not observed["selected"] or observed["input_state"] != "OFF"
                or c.validate_list_cc(actual) != expected):
            raise _mismatch("LIST readback did not match the test buffer.")
        return {"observed": observed, "expected_width_s": str(candidate)}

    def restore():
        _guard(session, modes=_FIXED | {"LIST"})
        if c._load_mode(session)[0] == "LIST":
            c._write(session, "FUNC:MODE FIX")
        _guard(session)
        c._write(session, f"LIST:WID 1,{original:f}")
        if _list_settings(session) != baseline or readback._fixed(session, "CC") != fixed:
            raise _mismatch("Original LIST buffer or Fixed CC setting was not restored.")

    return exercise, restore, {"working_list": baseline, "fixed_cc": fixed}


def _average_readback_trial(session):
    original = c._average_count(session)
    candidate = c.validate_average(4 if original == 8 else 8)

    def exercise(write):
        if c._average_count(session) != original:
            raise _mismatch("Original Average setting changed before testing.")
        write(f"SENS:AVER:COUN {candidate}")
        observed = readback.snapshot(session, "average-status")
        if observed["average_count"] != candidate:
            raise _mismatch("Average readback differs from the test count.")
        return {"observed": observed, "expected_average_count": candidate}

    def restore():
        c._input_off(session)
        c._write(session, f"SENS:AVER:COUN {original}")
        if c._average_count(session) != original:
            raise _mismatch("Original Average count was not restored.")

    return exercise, restore, {"average_count": original}


def _prepare_trial(session, trial, slot, previous_slot_width, *, readback_trial=False):
    selection = readback._selection(session)
    target = trial[-2:].upper() if trial.startswith("fixed-") else "CC"
    needs_selection = selection["active_mode"] != "fixed" or selection["function"] != target
    if needs_selection:
        selection = _selection_guard(session, allow_transient=readback_trial)
    elif not readback_trial:
        _guard(session)
    else:
        _selection_guard(session)
    if target == "CR" and c._flag(session, c.LED_QUERY) != "OFF":
        raise LoadError("unsafe_mode", "Plain CR settings require LED OFF.")
    if trial == "short":
        functions = _short_trial(session)
    elif trial == "list-cc":
        functions = _list_trial(session, slot, previous_slot_width)
    elif trial.startswith("fixed-"):
        functions = _fixed_readback_trial(session, target)
    elif trial == "transient":
        functions = _transient_readback_trial(session)
    elif trial == "list":
        functions = _list_readback_trial(session)
    elif trial == "average":
        functions = _average_readback_trial(session)
    else:
        functions = _round_trip(session, trial)
    if needs_selection:
        return _with_selection(session, selection, target, *functions)
    if readback_trial:
        exercise, restore, original = functions
        return exercise, restore, {"selection": selection, **original}
    return functions


def execute(trial, *, mode, resource, backend, model_id, confirmed, slot=None, previous_slot_width=None,
            asrl=None, stop=None, on_baseline=None, readback_trial=False):
    if model_id != MODEL_ID:
        raise LoadError("unsupported_model", "The requested model profile is not registered.")
    if trial not in TRIALS or mode not in {"live", "dry-run"}:
        raise LoadError("argument_error", "Choose a Contributor settings trial in live or dry-run mode.")
    if readback_trial and trial not in (*READBACK_TRIALS, *SHARED_READBACK_TRIALS):
        raise LoadError("argument_error", "Readback selection is not applicable to this settings trial.")
    readback_trial = readback_trial or trial in READBACK_TRIALS
    if trial == "list-cc":
        slot = c.validate_list_slot(slot)
        if previous_slot_width is not None:
            previous_slot_width = c._decimal(previous_slot_width)
            if not c.LIST_WIDTH_LIMITS[0] <= previous_slot_width <= c.LIST_WIDTH_LIMITS[1]:
                raise LoadError("width_out_of_range", "The previous slot width must be within LIST width limits.")
    elif slot is not None or previous_slot_width is not None:
        raise LoadError("argument_error", "Scratch slot options apply only to the LIST trial.")
    if mode == "live" and not confirmed:
        raise LoadError("confirmation_required", "Confirm terminal isolation and any requested slot overwrite.")
    validate_request_connection(resource, backend, mode=mode, asrl=asrl, allowed=True)
    marker = {"validation_only": True, "validation_scope": COMMAND, "trial": trial}
    if mode == "dry-run":
        writes = {
            "von": ["VOLT:ON <test/original>", "VOLT:LATC <test/original>"],
            "slew": [f"{stem} <test/original>" for stem in readback.SLEW_QUERIES.values()],
            "short": ["INP:SHOR 1", "INP:SHOR 0"],
            "list-cc": ["LIST:WID 1,<B>", "FUNC:MODE LIST", f"LIST:SAV {slot}", "LIST:WID 1,<A>",
                        f"LIST:RCL {slot}", "LIST:WID 1,<A>", f"LIST:SAV {slot}",
                        "LIST:WID 1,<B>", f"LIST:RCL {slot}", "FUNC:MODE FIX"],
        }
        queries = {
            "von": [readback.VON_THRESHOLD_QUERY, readback.VON_LATCH_QUERY],
            "slew": ["CURR:RANG?", *(stem + "?" for stem in readback.SLEW_QUERIES.values())],
            "short": [],
            "list-cc": [c.SLOW_RISE_QUERY, "CURR:RANG?", "CURR?", *readback.LIST_HEADER_QUERIES,
                        *(query.format("{step}") for query in readback.LIST_STEP_QUERIES.values())],
        }
        conditional = {
            "short": [{"condition": "Trusted cleanup finds Input ON, before clearing Short",
                       "writes": ["SYST:REM", "INP OFF"]}],
            "list-cc": [
                {"condition": "Trusted cleanup finds A or B in the working buffer",
                 "writes": ["LIST:WID 1,<original>"]},
                {"condition": "Trusted cleanup finds a different valid working buffer",
                 "writes": ["LIST:RANG <original range>", "LIST:COUN <original count>", "LIST:STEP <original count>",
                            "LIST:LEV <step>,<original current>", "LIST:SLEW <step>,<original slew>",
                            "LIST:WID <step>,<original width>"]},
            ],
        }
        for function in ("CC", "CV", "CR", "CW"):
            key = "fixed-" + function.lower()
            prefix = c.TRANSIENT_FUNCTIONS[function]
            writes[key] = [f"{prefix} <test>", f"{prefix} <original>"]
            queries[key] = [f"{prefix}?", *([f"{prefix}:RANG?"] if function != "CW" else []),
                            *(["VOLT:RANG:AUTO?"] if function == "CV" else []),
                            *([c.LED_QUERY] if function == "CR" else [])]
        writes.update({
            "transient": ["CURR:TRAN:ALEV <test>", "TRAN ON", "TRAN OFF", "CURR:TRAN:ALEV <original>"],
            "list": ["LIST:WID 1,<test>", "FUNC:MODE LIST", "FUNC:MODE FIX", "LIST:WID 1,<original>"],
            "average": ["SENS:AVER:COUN <test>", "SENS:AVER:COUN <original>"],
        })
        queries.update({
            "transient": ["CURR:RANG?", "CURR:TRAN:MODE?", "TRIG:SOUR?", "TRIG:TIM?",
                          *(stem + "?" for stem in readback.TRANSIENT_LEVEL_QUERIES.values()),
                          "CURR:TRAN:AWID?", "CURR:TRAN:BWID?"],
            "list": queries["list-cc"],
            "average": ["SENS:AVER:COUN?"],
        })
        target = trial[-2:].upper() if trial.startswith("fixed-") else "CC"
        mode_change = {"condition": "Original selected function differs or original LIST is selected",
                       "writes": ["FUNC:MODE FIX", f"FUNC {c.TRANSIENT_FUNCTIONS[target]}",
                                  "FUNC <original>", "FUNC:MODE <original FIX/LIST>"]}
        conditional.setdefault(trial, []).append(mode_change)
        if readback_trial:
            conditional[trial].append({"condition": "Original Transient enabled, HOLD and restorable settings confirmed with Input OFF",
                                       "writes": ["TRAN OFF", "TRAN ON"]})
        if trial == "transient":
            conditional[trial].append({"condition": "Original Trigger Source differs from HOLD",
                                       "writes": ["TRIG:SOUR HOLD", "TRIG:TIM <original>", "TRIG:SOUR <original>"]})
        queries[trial] += [c.SLOW_RISE_QUERY, c.LED_QUERY]
        if readback_trial:
            queries[trial] += ["TRIG:SOUR?", "TRIG:TIM?"]
        return {**marker, "hardware_access": False, "planned_writes": ["SYST:REM", *writes[trial]],
                **({"conditional_writes": conditional[trial]} if trial in conditional else {}),
                "queries": ["*IDN?", "INP:STAT?", "FUNC:MODE?", "FUNC?", "TRAN?", "INP:TIM?",
                            readback.SHORT_QUERY, *queries[trial]]}

    with open_request_session(resource, asrl, c.open_session) as (session, runtime):
        _stopping(stop)
        parse_identity(c._query(session, "*IDN?"))
        try:
            exercise, restore, original = _prepare_trial(
                session, trial, slot, previous_slot_width, readback_trial=readback_trial)
        except LoadError as exc:
            exc.details["no_settings_written"] = True
            raise
        if on_baseline is not None:
            on_baseline({"trial": trial, **original})
        _stopping(stop)
        c._input_off(session)
        c._write(session, "SYST:REM")
        c._input_off(session)
        if "selection" in original:
            if _selection_guard(session, allow_transient=readback_trial) != original["selection"]:
                raise _mismatch("Original selection changed before the settings trial.")
        elif readback_trial:
            _selection_guard(session)
        else:
            _guard(session)
        return {**_exercise_and_restore(session, stop, exercise, restore), **marker, "visa": runtime}
