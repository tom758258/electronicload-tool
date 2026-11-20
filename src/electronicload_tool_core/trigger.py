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
