"""Isolated IT8812C settings trials. No trial sends Input ON or a trigger."""

from decimal import Decimal
from time import monotonic_ns

from . import control as c
from . import readback
from .errors import LoadError
from .hardware_validation import _stopping, _trigger_snapshot, list_program_writes
from .trigger import SOURCE_TOKENS
from .identity import MODEL_ID, parse_identity
from .visa import QUERY_DELAY_S, open_request_session, validate_request_connection


COMMAND = "contributor-settings"
READBACK_TRIALS = ("fixed-cc", "fixed-cv", "fixed-cr", "fixed-cw", "transient", "list", "average")
SHARED_READBACK_TRIALS = ("von", "slew", "short")
HANDOFF_TRIAL = "fixed-cc-handoff"
TRIALS = ("von", "slew", "short", "list-cc", *READBACK_TRIALS, HANDOFF_TRIAL)
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


def _exercise_and_restore(session, stop, exercise, restore, *, before_write=None):
    changed = False
    failure = None

    def write(command):
        nonlocal changed
        _stopping(stop)
        if before_write is not None:
            before_write()
        c._input_off(session)
        if before_write is not None:
            before_write()
        changed = True
        c._write(session, command)

    try:
        result = exercise(write)
    except (LoadError, KeyboardInterrupt) as exc:
        failure = exc if isinstance(exc, LoadError) else LoadError("interrupted", "The settings trial was interrupted.", 3)
    if changed:
        # A semantic mismatch or cooperative Stop leaves the exchange trusted.
        # A failed/ambiguous exchange must never cause blind cleanup writes.
        if failure is not None and failure.code not in {"readback_mismatch", "stopping", "input_not_off", "settings_budget_insufficient"}:
            raise _restore_error(failure) from failure
        try:
            restore()
            c._input_off(session)
        except (LoadError, KeyboardInterrupt) as exc:
            raise _restore_error(failure, exc) from exc
    if failure is not None:
        failure.details.update(restored=changed, input_state="OFF" if changed else "UNKNOWN")
        if failure.code == "settings_budget_insufficient" and not changed:
            failure.details["no_settings_written"] = True
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


def _list_settings(session, *, expected_count=None):
    raw = readback._list_configuration(session, expected_count=expected_count)
    if raw["range"] is None:
        raise LoadError("list_baseline_unrestorable", "The original LIST current range cannot be restored.", 3,
                        query="LIST:RANG?", observed=str(raw["range_readback_a"]))
    return c.validate_list_cc({key: raw[key] for key in c.LIST_FIELDS})


def _list_trial(session, slot, previous_slot_width, budget):
    fixed = readback._fixed(session, "CC")
    baseline = budget.snapshot(lambda: _list_settings(session, expected_count=budget.count), preparing=True)
    width = baseline["steps"][0]["width_s"]
    first = next(Decimal(value) for value in (1, 2, 3) if value not in (width, previous_slot_width))
    second = Decimal(2) if first == 1 else Decimal(1)

    def with_width(value):
        step, *rest = baseline["steps"]
        return c.validate_list_cc({**baseline, "steps": [{**step, "width_s": value}, *rest]})

    a, b = with_width(first), with_width(second)

    def require_list(expected, label):
        mode, function = c._load_mode(session)
        if mode != "LIST" or function not in _CURRENT or budget.snapshot(lambda: _list_settings(session, expected_count=budget.count)) != expected:
            raise _mismatch(f"LIST {label} did not match the expected configuration.")

    def exercise(write):
        if c._check_mode_change(session) or budget.snapshot(lambda: _list_settings(session, expected_count=budget.count)) != baseline:
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
        if _list_settings(session, expected_count=len(baseline["steps"])) != baseline or readback._fixed(session, "CC") != fixed:
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
    cc_selector = Decimal(str(baseline["range_readback_a"])) if function == "CC" else None
    change_selector = function == "CC" and range_name == "low"
    if change_selector and cc_selector < original:
        raise LoadError("readback_mismatch", "Original CC selector cannot safely restore the setpoint.", 3,
                        field=field, query="CURR:RANG?", range=range_name)
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

    def observe(value, *, testing=False):
        snapshot = readback.snapshot(session, "fixed-status")
        actual = snapshot.get(field)
        matches = actual is not None and (
            c._current_within(actual, value, range_name) if function == "CC" and testing
            else Decimal(str(actual)) == value)
        if (snapshot["input_state"] != "OFF" or snapshot["function"] != function
                or snapshot["range"] != range_name or not matches
                or snapshot.get("range_auto") != baseline.get("range_auto")
                or (function == "CC" and not (testing and change_selector)
                    and Decimal(str(snapshot["range_readback_a"])) != cc_selector)):
            details = {"field": field, "range": snapshot["range"],
                       "query": f"{prefix}?"}
            if testing and actual is not None:
                details.update(expected=str(value), observed=str(actual))
            raise LoadError("readback_mismatch", "Fixed readback differs from the expected setting.", 3, **details)
        return snapshot

    def exercise(write):
        observe(original)
        if change_selector:
            # IT8812C accepts a numeric selector before a nonzero Low CC level.
            write(f"CURR:RANG {candidate:f}")
        write(f"{prefix} {candidate:f}")
        return {"observed": observe(candidate, testing=True), "expected_setpoint": str(candidate)}

    def restore():
        selected = _selection_guard(session)
        if selected["active_mode"] != "fixed" or selected["function"] != function:
            raise _mismatch("Fixed selection changed; original setpoint cannot be safely restored.")
        if function != "CW" and readback._range(session, function)[0] != range_name:
            raise _mismatch("Fixed Range changed; original setpoint cannot be safely restored.")
        if change_selector:
            c._input_off(session)
            c._write(session, f"CURR:RANG {cc_selector:f}")
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


def _list_readback_trial(session, budget):
    baseline = budget.snapshot(lambda: _list_settings(session, expected_count=budget.count), preparing=True)
    fixed = readback._fixed(session, "CC")
    original = baseline["steps"][0]["width_s"]
    candidate = Decimal(2) if original == 1 else Decimal(1)
    first, *rest = baseline["steps"]
    expected = c.validate_list_cc({**baseline, "steps": [{**first, "width_s": candidate}, *rest]})

    def exercise(write):
        _guard(session)
        if budget.snapshot(lambda: _list_settings(session, expected_count=budget.count)) != baseline:
            raise _mismatch("Original LIST buffer changed before testing.")
        write(f"LIST:WID 1,{candidate:f}")
        write("FUNC:MODE LIST")
        observed = budget.snapshot(
            lambda: readback.snapshot(session, "list-status", expected_list_count=budget.count), extra_queries=4)
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
        if _list_settings(session, expected_count=len(baseline["steps"])) != baseline or readback._fixed(session, "CC") != fixed:
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


def _prepare_trial(session, trial, slot, previous_slot_width, *, readback_trial=False, deadline_ns=None):
    target = trial[-2:].upper() if trial.startswith("fixed-") else "CC"
    selection = None
    needs_selection = False
    if readback_trial:
        selection = _selection_guard(session, allow_transient=True)
        needs_selection = selection["active_mode"] != "fixed" or selection["function"] != target
    else:
        # Preserve the entire original standalone guard, not just its
        # Transient check. Standalone settings never switch function/mode.
        _guard(session)
    if target == "CR" and c._flag(session, c.LED_QUERY) != "OFF":
        raise LoadError("unsafe_mode", "Plain CR settings require LED OFF.")
    budget = (_ListBudget(session, trial, deadline_ns, selection, needs_selection)
              if trial in {"list", "list-cc"} else None)
    if trial == "short":
        functions = _short_trial(session)
    elif trial == "list-cc":
        functions = _list_trial(session, slot, previous_slot_width, budget)
    elif trial.startswith("fixed-"):
        functions = _fixed_readback_trial(session, target)
    elif trial == "transient":
        functions = _transient_readback_trial(session)
    elif trial == "list":
        functions = _list_readback_trial(session, budget)
    elif trial == "average":
        functions = _average_readback_trial(session)
    else:
        functions = _round_trip(session, trial)
    if needs_selection:
        functions = _with_selection(session, selection, target, *functions)
    elif readback_trial:
        exercise, restore, original = functions
        functions = exercise, restore, {"selection": selection, **original}
    return (*functions, budget)


class _ListBudget:
    """Conservative admission and restoration reserve for the two LIST trials.

    A query-delay budget is not a guarantee against future transport stalls.
    Never extend the runner deadline or retry a failed exchange.
    """

    def __init__(self, session, trial, deadline_ns, selection, needs_selection):
        self.deadline_ns = deadline_ns
        self.count = readback._list_integer(session, readback.LIST_HEADER_QUERIES[1], 2, 84)
        self.snapshot_queries = 3 + 3 * self.count
        self.query_ns = int(max(QUERY_DELAY_S, getattr(session, "query_delay", 0) or 0) * 1e9)
        self.prepare_queries = 16
        if trial == "list-cc":
            # Seven exercise snapshots; cleanup can read a different buffer,
            # rewrite its headers/steps with OFF guards, then verify baseline.
            self.exercise_queries = 7 * self.snapshot_queries + 55
            self.restore_queries = (3 + 3 * 84) + 2 * self.snapshot_queries + 40
        else:
            # Two exercise snapshots, one cleanup snapshot, Fixed settings
            # and interlocks. Budget each selection leg for the actual origin.
            self.exercise_queries = 2 * self.snapshot_queries + 16
            self.restore_queries = self.snapshot_queries + 20
            if needs_selection:
                if selection["transient_enabled"]:
                    self.prepare_queries += 10
                    self.exercise_queries += 44
                    self.restore_queries += 49
                elif selection["active_mode"] == "list":
                    self.exercise_queries += 32
                    self.restore_queries += 26
                else:
                    self.exercise_queries += 23
                    self.restore_queries += 27
        # Admission includes the remaining baseline and pre-remote guards.
        self.require(self.snapshot_queries + self.exercise_queries + self.prepare_queries)

    def require(self, queries=0):
        # Reserve time for restoration even when a trial is interrupted.
        required_ns = (queries + self.restore_queries) * self.query_ns + 1_000_000_000
        if monotonic_ns() + required_ns >= self.deadline_ns:
            raise LoadError("settings_budget_insufficient",
                            "Insufficient time for LIST verification and restoration within the command deadline.",
                            3)

    def snapshot(self, read, *, preparing=False, extra_queries=0):
        queries = self.snapshot_queries + extra_queries
        self.require(queries)
        started = monotonic_ns()
        result = read()
        elapsed_ns = monotonic_ns() - started
        # Account for actual baseline/readback costs, not just USB's
        # configured delay; later samples may be slower.
        self.query_ns = max(self.query_ns, (elapsed_ns + queries - 1) // queries)
        configuration = result.get("configuration", result)
        if len(configuration["steps"]) != self.count:
            raise _mismatch("LIST step count changed during the settings trial.")
        self.require(self.exercise_queries + self.prepare_queries if preparing else 0)
        return result

    def before_write(self):
        self.require(2)


def _execute_handoff(session, *, origin_function, stop, on_baseline, marker, runtime):
    # This is a separate, explicitly isolated mixed-run transition, not
    # permission for standalone Auxiliary/LIST to change their origin.
    original = _selection_guard(session)
    if original["active_mode"] != "fixed" or original["function"] != origin_function:
        raise LoadError("unsafe_mode", "Mixed-run handoff requires the expected Fixed origin.",
                        no_settings_written=True)
    if on_baseline is not None:
        on_baseline({"trial": HANDOFF_TRIAL, "selection": original})
    _stopping(stop)
    if _selection_guard(session) != original:
        raise _mismatch("Fixed mode changed before the mixed-run handoff.")

    def write(command):
        _stopping(stop)
        c._input_off(session)
        c._write(session, command)

    if original["function"] != "CC":
        write("SYST:REM")
        if _selection_guard(session) != original:
            raise _mismatch("Fixed mode changed during remote transition.")
        try:
            write("FUNC CURR")
            _guard(session)
        except LoadError as exc:
            # A possibly applied selection is not proof of the final mode.
            # Do not start the standalone trial or blindly retry.
            exc.details["manual_action_required"] = True
            raise
    else:
        _guard(session)
    return {**marker, "visa": runtime, "input_state": "OFF", "function": "CC"}


def execute(trial, *, mode, resource, backend, model_id, confirmed, slot=None, previous_slot_width=None,
            asrl=None, stop=None, on_baseline=None, readback_trial=False, deadline_ns=None,
            origin_function=None):
    if model_id != MODEL_ID:
        raise LoadError("unsupported_model", "The requested model profile is not registered.")
    if trial not in TRIALS or mode not in {"live", "dry-run"}:
        raise LoadError("argument_error", "Choose a Contributor settings trial in live or dry-run mode.")
    if readback_trial and trial not in (*READBACK_TRIALS, *SHARED_READBACK_TRIALS):
        raise LoadError("argument_error", "Readback selection is not applicable to this settings trial.")
    readback_trial = readback_trial or trial in READBACK_TRIALS
    if ((trial == HANDOFF_TRIAL and origin_function not in {"CC", "CV", "CR", "CW"})
            or (trial != HANDOFF_TRIAL and origin_function is not None)):
        raise LoadError("argument_error", "An expected Fixed origin applies only to the mixed-run handoff.")
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
    if deadline_ns is not None and (trial not in {"list", "list-cc"} or mode != "live"
                                    or type(deadline_ns) is not int):
        raise LoadError("argument_error", "A live LIST settings deadline is required.")
    if trial in {"list", "list-cc"} and mode == "live":
        now = monotonic_ns()
        limit_ns = (300 if trial == "list-cc" else 30) * 10**9
        if deadline_ns is None:
            deadline_ns = now + limit_ns
        if not now < deadline_ns <= now + limit_ns:
            raise LoadError("argument_error", "Invalid LIST settings deadline.")
    if mode == "dry-run" and trial == HANDOFF_TRIAL:
        return {**marker, "hardware_access": False, "planned_writes": [],
                "conditional_writes": [{"condition": f"Expected Fixed {origin_function} origin, all interlocks OFF; switch only if non-CC",
                                        "writes": ["SYST:REM", "FUNC CURR"]}],
                "queries": ["*IDN?", "INP:STAT?", "FUNC:MODE?", "FUNC?", "TRAN?", "INP:TIM?",
                            readback.SHORT_QUERY, c.SLOW_RISE_QUERY, c.LED_QUERY]}
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
            if function == "CC":
                conditional[key] = [{"condition": "Original CC range is Low; selectors precede the respective test and restore setpoints",
                                     "writes": ["CURR:RANG <test>", "CURR:RANG <original selector>"]}]
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
        if readback_trial:
            conditional.setdefault(trial, []).append(mode_change)
            conditional[trial].append({"condition": "Original Transient enabled, HOLD and restorable settings confirmed with Input OFF",
                                       "writes": ["TRAN OFF", "TRAN ON"]})
        if trial == "transient":
            conditional[trial].append({"condition": "Original Trigger Source differs from HOLD",
                                       "writes": ["TRIG:SOUR HOLD", "TRIG:TIM <original>", "TRIG:SOUR <original>"]})
        if readback_trial:
            queries[trial] += [c.SLOW_RISE_QUERY, c.LED_QUERY, "TRIG:SOUR?", "TRIG:TIM?"]
        return {**marker, "hardware_access": False, "planned_writes": ["SYST:REM", *writes[trial]],
                **({"conditional_writes": conditional[trial]} if trial in conditional else {}),
                "queries": ["*IDN?", "INP:STAT?", "FUNC:MODE?", "FUNC?", "TRAN?", "INP:TIM?",
                            readback.SHORT_QUERY, *queries[trial]]}

    with open_request_session(resource, asrl, c.open_session) as (session, runtime):
        _stopping(stop)
        parse_identity(c._query(session, "*IDN?"))
        if trial == HANDOFF_TRIAL:
            return _execute_handoff(session, origin_function=origin_function, stop=stop,
                                    on_baseline=on_baseline, marker=marker, runtime=runtime)
        try:
            exercise, restore, original, budget = _prepare_trial(
                session, trial, slot, previous_slot_width, readback_trial=readback_trial, deadline_ns=deadline_ns)
        except LoadError as exc:
            exc.details["no_settings_written"] = True
            raise
        if on_baseline is not None:
            on_baseline({"trial": trial, **original})
        _stopping(stop)
        if budget is not None:
            try:
                budget.require(budget.exercise_queries + 14)
            except LoadError as exc:
                exc.details["no_settings_written"] = True
                raise
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
        return {**_exercise_and_restore(session, stop, exercise, restore,
                                         before_write=budget.before_write if budget is not None else None),
                **marker, "visa": runtime}
