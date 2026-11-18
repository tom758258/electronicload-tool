"""Bounded IT8812C native TRACE acquisition with offline command entry points."""

import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import math
from pathlib import Path
import re
from threading import Event
from time import monotonic, sleep

from .control import NUMBER, _query, _write
from .errors import LoadError, communication_error
from .identity import MODEL_ID, parse_identity
from .simulator import ControlSimulator, TRACE_BUFFER_FULL
from .visa import validate_connection

TRACE_COMMANDS = ("trace-configure", "trace-start")
CONFIG_FIELDS = frozenset({"source", "points", "interval_s"})
CSV_FIELDS = frozenset({"csv", "csv_format", "csv_time_columns"})
SAMPLE_FIELDS = {"VOLT": ("voltage_v",), "CURR": ("current_a",), "TWO": ("voltage_v", "current_a")}
SOURCE_READBACK = {"VOLT": "VOLT", "VOLTAGE": "VOLT", "CURR": "CURR", "CURRENT": "CURR", "TWO": "TWO"}
FEED_QUERY = "TRAC:FEED:CONT?"
STATUS_QUERY = "STAT:QUES:COND?"
DATA_QUERY = "TRAC:DATA?"
DATA_TIMEOUT_MS = 30000
READ_TIMESTAMP = "read_timestamp_utc_plus_8"
METADATA = ("source", "points", "interval_s", "sample_count")
TIME_COLUMNS = {"elapsed": "estimated_elapsed_s", "delta": "estimated_delta_t_s"}
STOPPED_FEED = "TRAC:FEED:CONT NEVER"


class TraceSessionLost(LoadError):
    """The command/reply stream is no longer trustworthy."""

    def __init__(self, operation, **details):
        super().__init__("trace_session_untrusted",
                         "TRACE lost reliable communication. Feed and Input states are unknown.",
                         3, operation=operation, feed="UNKNOWN", input_state="UNKNOWN", **details)


def _decimal(token):
    if not NUMBER.fullmatch(token):
        raise InvalidOperation
    return Decimal(token)


def _scaled(interval, count):
    with localcontext() as context:
        context.prec = max(context.prec, len(interval.as_tuple().digits) + 4)
        return interval * count


def validate_configuration(source, points, interval_s):
    if not isinstance(source, str) or source not in SAMPLE_FIELDS:
        raise LoadError("argument_error", "TRACE source must be VOLT, CURR or TWO.")
    if type(points) is not int or not 2 <= points <= 1000 or (source == "TWO" and points % 2):
        raise LoadError("argument_error", "TRACE requires 2–1000 points and an even count for TWO.")
    try:
        interval = _decimal(str(interval_s))
    except InvalidOperation as exc:
        raise LoadError("argument_error", "TRACE interval_s must be a finite number.") from exc
    if not Decimal("0.001") <= interval <= 3600:
        raise LoadError("argument_error", "TRACE interval_s must be between 0.001 and 3600 seconds.")
    sample_count = points // len(SAMPLE_FIELDS[source])
    expected = _scaled(interval, sample_count - 1)
    if expected > 86400:
        raise LoadError("trace_duration_limit", "Expected TRACE capture exceeds 86400 seconds.")
    return {"source": source, "points": points, "interval_s": str(interval),
            "sample_count": sample_count}, float(expected)


def validate_csv(arguments):
    if not arguments:
        return None
    path = arguments.get("csv")
    if not isinstance(path, str) or not path.strip() or "\0" in path:
        raise LoadError("argument_error", "CSV options require a non-empty CSV path.")
    csv_format = arguments.get("csv_format", "data")
    if csv_format not in ("data", "full"):
        raise LoadError("argument_error", "CSV format must be data or full.")
    selection = arguments.get("csv_time_columns", "")
    if not isinstance(selection, str):
        raise LoadError("argument_error", "CSV time columns must be a comma-separated string.")
    tokens = selection.split(",") if selection else []
    if any(token not in TIME_COLUMNS for token in tokens) or len(tokens) != len(set(tokens)):
        raise LoadError("argument_error", "Select elapsed, delta, or both CSV time columns once.")
    return Path(path), csv_format, tokens


def _ask(session, query):
    try:
        return _query(session, query)
    except LoadError as exc:
        raise TraceSessionLost(query, **exc.details) from exc


def _send(session, command):
    try:
        _write(session, command)
    except LoadError as exc:
        raise TraceSessionLost(command, **exc.details) from exc


def _choice(session, query, choices):
    reply = _ask(session, query).upper()
    if reply not in choices:
        raise TraceSessionLost(query)
    return reply


def _read_decimal(session, query):
    try:
        return _decimal(_ask(session, query))
    except InvalidOperation as exc:
        raise TraceSessionLost(query) from exc


def _integer(session, query, *, maximum):
    value = _read_decimal(session, query)
    if not 0 <= value <= maximum or value != value.to_integral_value():
        raise TraceSessionLost(query)
    return int(value)


def _feed(session):
    return _choice(session, FEED_QUERY, ("NEXT", "NEVER"))


def _buffer_full(session):
    return bool(_integer(session, STATUS_QUERY, maximum=65535) & TRACE_BUFFER_FULL)


def _check_error(session):
    response = _ask(session, "SYST:ERR?")
    match = re.fullmatch(r'([+-]?[0-9]{1,6})(?:\s*,\s*(?:"[^"]*"|[A-Za-z][^\r\n]*))?', response)
    if match is None:
        raise TraceSessionLost("SYST:ERR?")
    if int(match[1]):
        raise LoadError("trace_instrument_error", "Instrument reported a TRACE error.", 3,
                        scpi_error=int(match[1]))


def _identity(session):
    try:
        parse_identity(_ask(session, "*IDN?"))
    except LoadError as exc:
        if exc.code == "invalid_identity":
            raise TraceSessionLost("*IDN?") from exc
        raise


def _check_off(session):
    if _choice(session, "INP:STAT?", ("0", "1")) != "0":
        raise LoadError("input_not_off", "TRACE configuration requires confirmed Input OFF.")


def _read_configuration(session):
    source = SOURCE_READBACK[_choice(session, "TRAC:FEED?", SOURCE_READBACK)]
    points = _integer(session, "TRAC:POIN?", maximum=65535)
    return source, points, _read_decimal(session, "TRAC:TIM?")


def _check_zero(session, query):
    if _read_decimal(session, query) != 0:
        raise LoadError("trace_readback_mismatch", "TRACE setting must read back as zero.", 3,
                        query=query)


def _configure(session, config, stop):
    _identity(session)
    _check_off(session)
    if stop is not None and stop.is_set():
        raise LoadError("stopping", "Worker stopped before TRACE configuration.", 3)
    _send(session, "SYST:REM")
    _check_off(session)
    _send(session, STOPPED_FEED)
    if _feed(session) != "NEVER":
        raise LoadError("trace_feed_active", "TRACE Feed did not stop.", 3)
    for command in (f"TRAC:FEED {config['source']}", f"TRAC:POIN {config['points']}",
                    f"TRAC:TIM {config['interval_s']}", "TRAC:DEL 0", "TRAC:FILT 0", "TRAC:CLE"):
        _send(session, command)
    source, points, interval = _read_configuration(session)
    if interval != Decimal(config["interval_s"]):
        raise LoadError("trace_interval_mismatch", "TRACE interval differs from the requested value.", 3,
                        requested_interval_s=config["interval_s"], actual_interval_s=str(interval))
    if (source, points) != (config["source"], config["points"]):
        raise LoadError("trace_readback_mismatch", "TRACE source or points differs from the request.", 3,
                        actual_source=source, actual_points=points)
    _check_zero(session, "TRAC:DEL?")
    _check_zero(session, "TRAC:FILT?")
    if _feed(session) != "NEVER":
        raise LoadError("trace_feed_active", "TRACE Feed must be idle after configuration.", 3)
    deadline = monotonic() + 5
    while _buffer_full(session):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise LoadError("trace_buffer_not_clear", "TRACE buffer did not clear.", 3)
        sleep(min(1, remaining))
    _check_error(session)
    return config


def _start_precheck(session):
    _identity(session)
    config, expected = validate_configuration(*_read_configuration(session))
    _check_zero(session, "TRAC:DEL?")
    _check_zero(session, "TRAC:FILT?")
    if _feed(session) != "NEVER":
        raise LoadError("trace_feed_active", "TRACE Feed must be idle before starting.", 3)
    if _buffer_full(session):
        raise LoadError("trace_buffer_not_clear", "Configure TRACE to clear the buffer before starting.", 3)
    _choice(session, "INP:STAT?", ("0", "1"))
    # LIST uses the function mode selector. The simulator also models TRAN as
    # a distinct mode, while the physical Transient guard is based on TRAN?.
    mode = _choice(session, "FUNC:MODE?", ("FIX", "FIXED", "LIST", "TRAN", "TRANSIENT"))
    function = _choice(session, "FUNC?", ("CURR", "CURRENT", "VOLT", "VOLTAGE",
                                         "RES", "RESISTANCE", "POW", "POWER"))
    if mode not in {"FIX", "FIXED"}:
        raise LoadError("trace_unsafe_mode", "TRACE requires Fixed CC, CV, CR or CW.", 3,
                        function=function, function_mode=mode)
    for query in ("TRAN?", "INP:SHOR:STAT?", "INP:TIM?"):
        if _choice(session, query, ("0", "1")) != "0":
            raise LoadError("trace_unsafe_mode", "TRACE requires Transient, Short and Input Timer OFF.",
                            3, query=query)
    return config, expected


def _checkpoint(stop, cancel):
    if stop is not None and stop.is_set():
        raise LoadError("trace_stopped", "Worker stopped before TRACE capture completed.", 3)
    if cancel is not None and cancel.is_set():
        raise LoadError("trace_cancelled", "TRACE capture cancelled.", 3)


def _poll_interval(elapsed, expected):
    if expected <= 60 or expected - 5 <= elapsed < expected + 5:
        return 1
    if elapsed < expected - 5:
        return min(60, expected - 5 - elapsed)
    return 60


def _read_batch(session, config):
    try:
        previous_timeout = session.timeout
        session.timeout = DATA_TIMEOUT_MS
    except Exception as exc:
        error = communication_error("visa_configure_failed", "Cannot set TRACE data timeout.", exc)
        raise TraceSessionLost(DATA_QUERY, **error.details) from exc
    payload = _ask(session, DATA_QUERY)
    timestamp = datetime.now(timezone(timedelta(hours=8))).isoformat()
    try:
        session.timeout = previous_timeout
    except Exception as exc:
        error = communication_error("visa_configure_failed", "Cannot restore VISA timeout.", exc)
        raise TraceSessionLost(DATA_QUERY, **error.details) from exc
    tokens = payload.removesuffix(",").split(",") if payload else []
    if config["source"] == "TWO":
        # TWO can return a comma-separated series of V/I pairs, with a space
        # within each pair. Accept a flat comma-separated numeric reply too.
        groups = [token.split() for token in tokens]
        if groups and len({len(group) for group in groups}) != 1:
            raise TraceSessionLost(DATA_QUERY)
        if any(len(group) not in (1, 2) for group in groups):
            raise TraceSessionLost(DATA_QUERY)
        tokens = [token for group in groups for token in group]
    values = []
    for token in tokens:
        token = token.strip()
        if not NUMBER.fullmatch(token) or not math.isfinite(value := float(token)):
            raise TraceSessionLost(DATA_QUERY)
        values.append(value)
    if len(values) != config["points"]:
        raise LoadError("trace_incomplete", "TRACE returned an unexpected number of values.", 3,
                        expected_points=config["points"], actual_points=len(values))
    fields = SAMPLE_FIELDS[config["source"]]
    width = len(fields)
    samples = [{"sample_index": offset // width + 1, **dict(zip(fields, values[offset:offset + width]))}
               for offset in range(0, len(values), width)]
    return {**config, READ_TIMESTAMP: timestamp, "samples": samples}


def _capture(session, config, expected, stop, cancel):
    _checkpoint(stop, cancel)
    feed = "NEXT"
    try:
        _send(session, "TRAC:FEED:CONT NEXT")
        _checkpoint(stop, cancel)
        started = monotonic()
        deadline = started + expected + max(expected * 0.20, 5)
        _send(session, "TRIG")
        while True:
            _checkpoint(stop, cancel)
            now = monotonic()
            if now >= deadline:
                raise LoadError("trace_timeout", "TRACE did not complete before its deadline.", 3)
            delay = min(_poll_interval(now - started, expected), deadline - now)
            if cancel is None:
                sleep(delay)
            else:
                cancel.wait(delay)
            _checkpoint(stop, cancel)
            if monotonic() >= deadline:
                raise LoadError("trace_timeout", "TRACE did not complete before its deadline.", 3)
            feed = _feed(session)
            if monotonic() >= deadline:
                raise LoadError("trace_timeout", "TRACE did not complete before its deadline.", 3)
            if feed == "NEVER":
                break
    except LoadError as exc:
        if not isinstance(exc, TraceSessionLost) and feed == "NEXT":
            _send(session, STOPPED_FEED)
            observed = _feed(session)
            if observed != "NEVER" and exc.code == "trace_cancelled":
                raise LoadError("trace_cancel_feed_unconfirmed",
                                "Cancellation could not confirm TRACE Feed stopped.", 3,
                                feed=observed) from exc
            exc.details["feed"] = observed
        raise
    _checkpoint(stop, cancel)
    _buffer_full(session)  # Query diagnostic status; either TBF value is valid here.
    _checkpoint(stop, cancel)
    result = _read_batch(session, config)
    _checkpoint(stop, cancel)
    _check_error(session)
    return result


def _capture_with_csv(session, config, expected, stop, cancel, csv_options):
    path, csv_format, columns = csv_options
    fields = [READ_TIMESTAMP, "sample_index", *SAMPLE_FIELDS[config["source"]]]
    if csv_format == "full":
        fields.extend(METADATA)
    fields.extend(TIME_COLUMNS[column] for column in columns)
    created = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            file = path.open("x", encoding="utf-8", newline="")
        except FileExistsError as exc:
            raise LoadError("trace_csv_exists", "TRACE CSV already exists.", 3) from exc
        with file:
            created = True
            writer = csv.DictWriter(file, fields)
            writer.writeheader()
            file.flush()
            result = _capture(session, config, expected, stop, cancel)
            interval = Decimal(config["interval_s"])
            for sample in result["samples"]:
                row = {READ_TIMESTAMP: result[READ_TIMESTAMP], **sample}
                if csv_format == "full":
                    row.update(config)
                index = sample["sample_index"]
                if "elapsed" in columns:
                    row[TIME_COLUMNS["elapsed"]] = str(_scaled(interval, index - 1))
                if "delta" in columns and index != 1:
                    row[TIME_COLUMNS["delta"]] = str(interval)
                writer.writerow(row)
            file.flush()
    except LoadError as exc:
        if created:
            exc.details["csv_incomplete"] = True
        raise
    except OSError as exc:
        if isinstance(exc.__context__, LoadError):
            # A close error must never hide an acquisition fault or cancel.
            primary = exc.__context__
            primary.details.update(csv_incomplete=True, csv_close_error=type(exc).__name__)
            raise primary
        message = ("TRACE CSV is incomplete and was retained." if created
                   else "Could not create the requested TRACE CSV.")
        raise LoadError("trace_csv_failed", message, 3, csv_incomplete=created,
                        exception_type=type(exc).__name__) from exc
    return result


def execute(command, *, mode="live", resource=None, backend="system", model_id=MODEL_ID,
            arguments=None, simulator=None, stop: Event | None = None,
            cancel: Event | None = None):
    """Run a TRACE command. Native sequences are exercised only offline."""
    if command not in TRACE_COMMANDS or mode not in ("live", "simulate", "dry-run"):
        raise LoadError("argument_error", "Unknown TRACE command or execution mode.")
    if model_id != MODEL_ID:
        raise LoadError("unsupported_model", "The requested model profile is not registered.")
    validate_connection(resource, backend, required=mode == "live")
    arguments = arguments if arguments is not None else {}
    if not isinstance(arguments, dict):
        raise LoadError("argument_error", "TRACE arguments must be an object.")
    config = {}
    csv_options = None
    if command == "trace-configure":
        if set(arguments) != CONFIG_FIELDS:
            raise LoadError("argument_error", "TRACE configuration requires source, points and interval_s.")
        config, _ = validate_configuration(**arguments)
    else:
        if set(arguments) - CSV_FIELDS:
            raise LoadError("argument_error", "TRACE start accepts only CSV options.")
        csv_options = validate_csv(arguments)
    if mode == "live":
        raise LoadError("unsupported_live_scope", "TRACE is offline only.")
    if mode == "dry-run":
        return {"command": command, "hardware_access": False, **config}

    session = simulator if simulator is not None else ControlSimulator()
    if command == "trace-configure":
        return _configure(session, config, stop)
    _checkpoint(stop, cancel)
    config, expected = _start_precheck(session)
    _checkpoint(stop, cancel)
    # The built-in simulator makes its batch visible on the first FEED poll.
    # The full configured duration is still validated before scheduling.
    if type(session) is ControlSimulator:
        expected = 0
    if csv_options is None:
        return _capture(session, config, expected, stop, cancel)
    return _capture_with_csv(session, config, expected, stop, cancel, csv_options)
