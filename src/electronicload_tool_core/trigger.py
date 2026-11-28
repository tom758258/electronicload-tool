"""Shared IT8812C Trigger Setup validation and software trigger routing."""

from decimal import Decimal, InvalidOperation

from .errors import LoadError

TRIGGER_COMMANDS = ("trigger-configure", "trigger-status")
TRIGGER_SOURCES = ("MANUAL", "BUS", "HOLD", "TIMER", "EXTERNAL")
TRIGGER_SOURCE_QUERY = "TRIG:SOUR?"
TRIGGER_INTERVAL_QUERY = "TRIG:TIM?"
DEFAULT_INTERVAL = Decimal("0.01")
# IT8800 Programming Guide V2.1 pp.40–41 gives bounds and no programming increment.
MAX_INTERVAL = Decimal("999.99")
SOURCE_TOKENS = dict(zip(TRIGGER_SOURCES, ("MANU", "BUS", "HOLD", "TIM", "EXT")))
SOFTWARE_TRIGGERS = {"HOLD": "TRIG:IMM", "BUS": "*TRG"}


def validate_interval(value: object) -> Decimal:
    if type(value) in (str, int, float, Decimal):
        try:
            interval = Decimal(str(value))
            if interval.is_finite() and DEFAULT_INTERVAL <= interval <= MAX_INTERVAL:
                return interval
        except InvalidOperation:
            pass
    raise LoadError("invalid_trigger_interval", "Trigger Timer interval must be 0.01 through 999.99 s.")


def validate_setup(source: object, interval_s: object = None, *, restoring: bool = False) -> dict:
    if source not in TRIGGER_SOURCES:
        raise LoadError("invalid_trigger_source", "Select MANUAL, BUS, HOLD, TIMER or EXTERNAL.")
    if source == "TIMER" or restoring:
        return {"source": source, "interval_s": validate_interval(interval_s)}
    if interval_s is not None:
        raise LoadError("argument_error", "Interval applies only to TIMER.")
    return {"source": source}


def read_source(value: str) -> str:
    token = value.upper()
    for source, short in SOURCE_TOKENS.items():
        if token in (source, short):
            return source
    raise LoadError("invalid_response", "Unrecognized Trigger Source readback.", 3)


def software_trigger(source: str) -> str:
    if source not in SOFTWARE_TRIGGERS:
        raise LoadError("unsafe_trigger_source", "Software trigger requires HOLD or BUS.")
    return SOFTWARE_TRIGGERS[source]


def sampled_waveform_evidence(samples, *, source, level_a, level_b, width_a,
                              width_b=None, repeats=1, interval=None, baseline_current=None):
    """Recognize time-bracketed sampled plateaus, not continuous calibration."""
    if len(samples) < 3:
        return None
    widths = [float(width_a)] + ([float(width_b)] if width_b is not None else [])
    gap_limit = min(0.5, min(widths) / 4)
    gap = max(max(item["completed_s"] - item["started_s"] for item in samples),
              max(right["completed_s"] - left["started_s"]
                  for left, right in zip(samples, samples[1:])))
    if gap <= 0 or gap > gap_limit:
        return None
    is_list = width_b is not None
    # HOLD starts at the configured Fixed CC level until its first software
    # Trigger. Keep the historical B baseline for TIMER and older callers.
    observed_hold_origin = source == "HOLD" and baseline_current is not None
    levels = ({"S": Decimal(str(baseline_current)), "A": level_a, "B": level_b}
              if observed_hold_origin else {"A": level_a, "B": level_b})
    if is_list:
        levels["Z"] = Decimal(0)
    distances = [abs(left - right) for index, left in enumerate(levels.values())
                 for right in list(levels.values())[index + 1:]]
    tolerance = min(Decimal("0.01"), min(distances) / 4)
    if tolerance <= 0:
        return None
    terminal = None
    if is_list:
        # A final current level may remain after LIST completion. Only
        # the observed RUN transition establishes the final dwell boundary.
        states = [item.get("list_running") for item in samples]
        if states[0] is not False or states[-1] is not False or True not in states:
            return None
        first_running = states.index(True)
        if any(state is not False for state in states[:first_running]) or not all(
            state is True for state in states[first_running:-1]
        ):
            return None
        terminal = samples[-1].get("list_status_completed_s")
        terminal_start = samples[-1].get("list_status_started_s")
        last_running = samples[-2].get("list_status_started_s")
        last_running_end = samples[-2].get("list_status_completed_s")
        if (None in (terminal, terminal_start, last_running, last_running_end)
                or not samples[-2]["completed_s"] <= last_running <= last_running_end
                <= samples[-1]["started_s"] <= samples[-1]["completed_s"] <= terminal_start <= terminal
                or terminal - last_running > gap_limit):
            return None
        # The final current sample may already be the terminal level;
        # do not count it as a verified running plateau.
        waveform_samples = samples[:-1]
    else:
        waveform_samples = samples
    runs = []
    for sample in waveform_samples:
        value = Decimal(str(sample["current_a"]))
        label = next((key for key, wanted in levels.items() if abs(value - wanted) <= tolerance), None)
        if label is None:
            # Keep unknown samples as separate runs rather than joining
            # disjoint plateaus across an unobserved or out-of-band interval.
            label = "?"
        if runs and runs[-1][0] == label:
            runs[-1][1].append(sample)
        else:
            runs.append((label, [sample]))

    # Only bounded, in-range transitions between different known plateaus
    # may be skipped for Transient pulses and finite LIST steps. Retain their
    # measured duration in the boundary uncertainty; never merge disjoint
    # same-level plateaus.
    transition_spans = {}
    bounded_edges = is_list or observed_hold_origin or source == "TIMER"
    if bounded_edges:
        stable = []
        allowed = {("S", "A"), ("A", "B")} if observed_hold_origin else {("B", "A"), ("A", "B")}
        if is_list:
            allowed = {("Z", "A"), ("A", "B"), ("B", "A")}
        for index, (label, values) in enumerate(runs):
            if label == "?" and 0 < index < len(runs) - 1:
                left, right = runs[index - 1][0], runs[index + 1][0]
                if (left, right) in allowed:
                    lo, hi = sorted((levels[left], levels[right]))
                    span = values[-1]["completed_s"] - values[0]["started_s"]
                    if (0 <= span <= gap_limit and all(
                            lo <= Decimal(str(item["current_a"])) <= hi for item in values)):
                        transition_spans[len(stable)] = span
                        continue
            stable.append((label, values))
        runs = stable

    plateau_brackets = []

    def plateau(index, duration, *, terminal_s=None, run_lag=0):
        values = runs[index][1]
        if len(values) < 2:
            return False
        first, last = values[0], values[-1]
        before = runs[index - 1][1][-1]
        if first["completed_s"] - before["started_s"] > 2 * gap + transition_spans.get(index, 0):
            return False
        lower = last["started_s"] - first["completed_s"]
        if not bounded_edges and lower + 2 * gap < duration:
            return False
        if index + 1 == len(runs):
            if terminal_s is None:
                return False
            after_completed = terminal_s
        else:
            after_completed = runs[index + 1][1][0]["completed_s"]
        if after_completed - last["started_s"] > 2 * gap + transition_spans.get(index + 1, 0):
            return False
        upper = after_completed - before["started_s"]
        if bounded_edges:
            # Use the observed local edge bounds, not an extra global margin.
            # RUN bounds the end of the final dwell; the terminal current is
            # still excluded from the running plateau evidence.
            end_lower = last_running if terminal_s is not None else last["started_s"]
            entry_lower = max(before["started_s"],
                              first["completed_s"] - gap - transition_spans.get(index, 0))
            exit_upper = min(after_completed, end_lower + gap + transition_spans.get(index + 1, 0))
            if terminal_s is not None and run_lag:
                # RUN and current are separate exchanges. In the narrowly
                # bounded delayed-RUN case below, a final current sample can
                # precede the observed RUN clear but follow physical completion.
                # Require two confirmed B samples before the inferred end.
                end_lower = max(end_lower - run_lag, values[1]["completed_s"])
            lower = end_lower - first["completed_s"]
            upper = exit_upper - entry_lower
            plateau_brackets.append((entry_lower, first["completed_s"], end_lower, exit_upper))
        return lower <= duration <= upper

    labels = [run[0] for run in runs]
    if is_list:
        pattern = ["Z", *(["A", "B"] * repeats)]
        if labels != pattern:
            return None
        # Retain the normal RUN contract. At fast query cadence the first
        # A current may be visible one exchange before RUN is reported. Admit
        # only that single adjacent edge, with two subsequent RUN-confirmed A
        # samples and a bounded, common offset for the RUN start/end timeline.
        # A late RUN during B or a long unconfirmed A dwell still fails.
        unconfirmed = [item for _, values in runs[1:] for item in values
                       if item.get("list_running") is not True]
        run_lag = 0
        if unconfirmed:
            first_a = runs[1][1]
            if (len(unconfirmed) != 1 or len(first_a) < 3 or unconfirmed[0] is not first_a[0]
                    or samples[first_running] is not first_a[1]
                    or samples[first_running - 1] is not first_a[0]):
                return None
            prior, following = first_a[:2]
            run_start_lower = prior.get("list_status_started_s")
            prior_end = prior.get("list_status_completed_s")
            next_start = following.get("list_status_started_s")
            run_start_upper = following.get("list_status_completed_s")
            if (None in (run_start_lower, prior_end, next_start, run_start_upper)
                    or not prior["completed_s"] <= run_start_lower <= prior_end
                    <= following["started_s"] <= following["completed_s"]
                    <= next_start <= run_start_upper
                    or run_start_upper - run_start_lower > gap_limit):
                return None
            run_lag = min(gap, run_start_upper - run_start_lower)
            if run_lag <= 0:
                return None
        if not all(plateau(index, widths[(index - 1) % 2],
                           terminal_s=terminal if index == len(pattern) - 1 else None,
                           run_lag=run_lag if index == len(pattern) - 1 else 0)
                   for index in range(1, len(pattern))):
            return None
        # All dwell boundaries must agree on one finite LIST timeline. A
        # shared transition cannot be spent independently on both steps.
        earliest, latest = float("-inf"), float("inf")
        elapsed = 0.0
        for index, (entry_lower, entry_upper, exit_lower, exit_upper) in enumerate(plateau_brackets):
            earliest = max(earliest, entry_lower - elapsed)
            latest = min(latest, entry_upper - elapsed)
            elapsed += widths[index % 2]
            earliest = max(earliest, exit_lower - elapsed)
            latest = min(latest, exit_upper - elapsed)
        if earliest > latest:
            return None
        if run_lag:
            # The same nonnegative reporting offset must explain BOTH RUN
            # edges relative to the single current timeline. Do not spend a
            # separate extra margin on each dwell or accept a late RUN with
            # the wrong duration. The offset is bounded by the measured
            # adjacent exchanges, never the 0.5 s gap ceiling.
            run_earliest = max(run_start_lower, last_running - elapsed)
            run_latest = min(run_start_upper, terminal - elapsed)
            if (run_earliest > run_latest
                    or max(0, run_earliest - latest) > min(run_lag, run_latest - earliest)):
                return None
        events = repeats
    else:
        pattern = (["B", "A", "B", "A", "B"] if source == "TIMER" else
                   ["S", "A", "B"] if observed_hold_origin else ["B", "A", "B"])
        if observed_hold_origin and (labels != pattern or len(runs[-1][1]) < 2):
            return None
        events = 2 if source == "TIMER" else 1
        found = False
        for start in range(len(labels) - len(pattern) + 1):
            if labels[start:start + len(pattern)] != pattern or (source == "HOLD" and start != 0):
                continue
            plateau_brackets.clear()
            if not all(plateau(index, widths[0]) for index in range(start + 1, start + len(pattern), 2)):
                continue
            if source == "TIMER":
                # Both pulse starts and ends must fit one Width/Interval timeline.
                earliest, latest = float("-inf"), float("inf")
                for i, (entry_lower, entry_upper, exit_lower, exit_upper) in enumerate(plateau_brackets):
                    offset = i * float(interval)
                    earliest = max(earliest, entry_lower - offset, exit_lower - offset - widths[0])
                    latest = min(latest, entry_upper - offset, exit_upper - offset - widths[0])
                if earliest > latest:
                    continue
            found = True
            break
        if not found:
            return None
    return {"source": "instrument_current_samples", "sample_count": len(samples),
            "max_sample_gap_s": gap, "events_confirmed": events,
            **({"list_run_completed": True,
                "list_run_bracket": {"last_running_started_s": last_running,
                                     "first_stopped_completed_s": terminal,
                                     "uncertainty_s": terminal - last_running}}
               if is_list else {}),
            **({"list_run_start_bracket": {"last_idle_started_s": run_start_lower,
                                          "first_running_completed_s": run_start_upper,
                                          "max_reporting_offset_s": run_lag}}
               if is_list and run_lag else {})}
