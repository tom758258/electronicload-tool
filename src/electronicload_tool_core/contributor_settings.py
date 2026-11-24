"""Isolated IT8812C settings trials. No trial sends Input ON or a trigger."""

from decimal import Decimal

from . import control as c
from . import readback
from .errors import LoadError
from .hardware_validation import _stopping, list_program_writes
from .identity import MODEL_ID, parse_identity
from .visa import open_request_session, validate_request_connection


COMMAND = "contributor-settings"
TRIALS = ("von", "slew", "short", "list-cc")
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


def execute(trial, *, mode, resource, backend, model_id, confirmed, slot=None, previous_slot_width=None,
            asrl=None, stop=None, on_baseline=None):
    if model_id != MODEL_ID:
        raise LoadError("unsupported_model", "The requested model profile is not registered.")
    if trial not in TRIALS or mode not in {"live", "dry-run"}:
        raise LoadError("argument_error", "Choose a Contributor settings trial in live or dry-run mode.")
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
        return {**marker, "hardware_access": False, "planned_writes": ["SYST:REM", *writes[trial]],
                **({"conditional_writes": conditional[trial]} if trial in conditional else {}),
                "queries": ["*IDN?", "INP:STAT?", "FUNC:MODE?", "FUNC?", "TRAN?", "INP:TIM?",
                            readback.SHORT_QUERY, *queries[trial]]}

    with open_request_session(resource, asrl, c.open_session) as (session, runtime):
        _stopping(stop)
        parse_identity(c._query(session, "*IDN?"))
        _guard(session)
        try:
            if trial == "short":
                exercise, restore, original = _short_trial(session)
            elif trial == "list-cc":
                exercise, restore, original = _list_trial(session, slot, previous_slot_width)
            else:
                exercise, restore, original = _round_trip(session, trial)
        except LoadError as exc:
            exc.details["no_settings_written"] = True
            raise
        if on_baseline is not None:
            on_baseline({"trial": trial, **original})
        _stopping(stop)
        c._input_off(session)
        c._write(session, "SYST:REM")
        _guard(session)
        return {**_exercise_and_restore(session, stop, exercise, restore), **marker, "visa": runtime}
