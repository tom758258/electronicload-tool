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
                              width_b=None, repeats=1, interval=None):
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
    levels = {"A": level_a, "B": level_b}
    if is_list:
        levels["Z"] = Decimal(0)
    distances = [abs(left - right) for index, left in enumerate(levels.values())
                 for right in list(levels.values())[index + 1:]]
    tolerance = min(Decimal("0.01"), min(distances) / 4)
    if tolerance <= 0:
        return None
    runs = []
    for sample in samples:
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

    def plateau(index, duration, *, allow_open=False):
        values = runs[index][1]
        if len(values) < 2:
            return False
        first, last = values[0], values[-1]
        before = runs[index - 1][1][-1]
        if first["completed_s"] - before["started_s"] > 2 * gap:
            return False
        lower = last["started_s"] - first["completed_s"]
        if lower + 2 * gap < duration:
            return False
        if index + 1 == len(runs):
            return allow_open and lower >= duration
        after = runs[index + 1][1][0]
        if after["completed_s"] - last["started_s"] > 2 * gap:
            return False
        upper = after["completed_s"] - before["started_s"]
        return lower <= duration <= upper

    labels = [run[0] for run in runs]
    if is_list:
        pattern = ["Z", *(["A", "B"] * repeats)]
        if labels[:len(pattern)] != pattern:
            return None
        if not all(plateau(index, widths[(index - 1) % 2], allow_open=index == len(pattern) - 1)
                   for index in range(1, len(pattern))):
            return None
        events = repeats
    else:
        pattern = ["B", "A", "B", "A", "B"] if source == "TIMER" else ["B", "A", "B"]
        events = 2 if source == "TIMER" else 1
        found = False
        for start in range(len(labels) - len(pattern) + 1):
            if labels[start:start + len(pattern)] != pattern or (source == "HOLD" and start != 0):
                continue
            if not all(plateau(index, widths[0]) for index in range(start + 1, start + len(pattern), 2)):
                continue
            if source == "TIMER":
                first, second = start + 1, start + 3
                earliest = runs[second - 1][1][-1]["started_s"] - runs[first][1][0]["completed_s"]
                latest = runs[second][1][0]["completed_s"] - runs[first - 1][1][-1]["started_s"]
                if not earliest <= float(interval) <= latest:
                    continue
            found = True
            break
        if not found:
            return None
    return {"source": "instrument_current_samples", "sample_count": len(samples),
            "max_sample_gap_s": gap, "events_confirmed": events}
