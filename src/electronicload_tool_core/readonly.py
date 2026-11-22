"""Read-only requests, identity checks, and exact live support policy."""

import math
import re

from .errors import LoadError, communication_error
from .identity import MODEL_ID, parse_identity
from .readback import COMMANDS as READBACK_COMMANDS, execute as execute_readback
from .simulator import Simulator
from .visa import TERMINATION, TIMEOUT_MS, open_session, require_trusted_session, validate_connection

QUERIES = {
    "identify": ("*IDN?",),
    "input-state": ("*IDN?", "INP:STAT?"),
    # FETCh returns the latest available power result; it may not be a fresh sample.
    "measure": ("*IDN?", "MEAS:VOLT?", "MEAS:CURR?", "FETC:POW?"),
}
NATIVE_MEASUREMENTS = {
    "measure-min-max": {
        "voltage-min": ("voltage_min_v", "MEAS:VOLT:MIN?"),
        "voltage-max": ("voltage_max_v", "MEAS:VOLT:MAX?"),
        "current-min": ("current_min_a", "MEAS:CURR:MIN?"),
        "current-max": ("current_max_a", "MEAS:CURR:MAX?"),
    },
    "measure-discharge": {
        "capacity": ("capacity_ah", "MEAS:CAP?"),
        "time": ("discharge_time_s", "MEAS:TIME?"),
    },
}
QUERIES.update({command: ("*IDN?", *(query for _, query in metrics.values()))
                for command, metrics in NATIVE_MEASUREMENTS.items()})
ERROR_QUEUE_COMMAND = "scpi-error-next"
SYSTEM_QUERIES = {
    ERROR_QUEUE_COMMAND: "SYST:ERR?",
    "scpi-version": "SYST:VERS?",
    "display-mode": "DISP:MODE?",
}
MAX_ERROR_READS = 20
QUERIES.update({command: ("*IDN?", query) for command, query in SYSTEM_QUERIES.items()})
COMMANDS = (*QUERIES, *READBACK_COMMANDS)
# Exact Product LIVE command allowlist; model, USB and backend checks remain mandatory.
PRODUCT_LIVE_COMMANDS = frozenset({"identify", "input-state", "measure"})


def validate_metric(command: str, metric: object) -> None:
    if metric is not None and (not isinstance(metric, str)
                              or metric not in NATIVE_MEASUREMENTS.get(command, {})):
        raise LoadError("argument_error", "Unsupported measurement metric.")


def validate_error_queue_all(all_errors: object) -> None:
    if type(all_errors) is not bool:
        raise LoadError("argument_error", "Error Queue all must be a boolean.")


def _query(session, query: str) -> str:
    try:
        return session.query(query)
    except Exception as exc:
        error = communication_error("query_failed", f"Query {query} failed.", exc)
        error.details["query"] = query
        raise error from exc


def _error_queue_entry(session, query: str) -> dict:
    response = _query(session, query).strip()
    match = re.fullmatch(r'([+-]?[0-9]+)[ \t]*,[ \t]*("(?:[^"\r\n]|"")*"|[^"\r\n]+)', response)
    if match:
        code, message = match.groups()
        try:
            error_code = int(code)
        except ValueError:
            pass
        else:
            if message.startswith('"'):
                message = message[1:-1].replace('""', '"')
            return {"error_code": error_code, "message": message.strip()}
    raise LoadError("invalid_response", "Error Queue reply must contain an integer and a message.",
                    3, query=query)


def _read_system_query(session, command: str, all_errors: bool) -> dict:
    query = SYSTEM_QUERIES[command]
    if command == ERROR_QUEUE_COMMAND:
        if not all_errors:
            return _error_queue_entry(session, query)
        errors = []
        for read_count in range(1, MAX_ERROR_READS + 1):
            try:
                entry = _error_queue_entry(session, query)
            except LoadError as exc:
                # A failed reply may have consumed an entry. Report only parsed replies.
                exc.details.update(errors=errors, read_count=len(errors))
                raise
            if entry["error_code"] == 0:
                break
            errors.append(entry)
        empty_confirmed = entry["error_code"] == 0
        return {"errors": errors, "read_count": read_count, "max_reads": MAX_ERROR_READS,
                "empty_confirmed": empty_confirmed, "limit_reached": not empty_confirmed}
    response = _query(session, query).strip()
    if command == "scpi-version":
        if re.fullmatch(r"[0-9]{4}\.[0-9]+", response):
            return {"scpi_version": response}
        raise LoadError("invalid_response", "SCPI version reply must use YYYY.V format.", 3, query=query)
    mode = response.upper()
    if mode == "NORM":
        mode = "NORMAL"
    if mode not in ("NORMAL", "TEXT"):
        raise LoadError("invalid_response", "Display Mode reply must be NORMAL or TEXT.", 3, query=query)
    return {"display_mode": mode}


def _read(session, command: str, metric: str | None = None, all_errors: bool = False) -> dict:
    validate_metric(command, metric)
    identity = parse_identity(_query(session, "*IDN?"))
    if command == "identify":
        return identity.public_result()
    if command in SYSTEM_QUERIES:
        return _read_system_query(session, command, all_errors)
    return read_measurement(session, command, metric)


def read_measurement(session, command: str, metric: str | None = None) -> dict:
    """Read Input or measurements on a session whose identity was already checked."""
    if command == "input-state":
        response = _query(session, "INP:STAT?").strip()
        if response not in ("0", "1"):
            raise LoadError("invalid_response", "Input state reply must be 0 or 1.", 3)
        return {"input_state": "OFF" if response == "0" else "ON"}
    result = {}
    if command in NATIVE_MEASUREMENTS:
        metrics = NATIVE_MEASUREMENTS[command]
        readings = (metrics[metric],) if metric is not None else metrics.values()
    else:
        readings = zip(("voltage_v", "current_a", "power_w"), QUERIES["measure"][1:])
    for key, query in readings:
        response = _query(session, query).strip()
        numeric = re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?", response)
        value = float(response) if numeric else math.nan
        if not math.isfinite(value):
            raise LoadError(
                "invalid_response", "Measurement reply must be a finite number.", 3, query=query
            )
        result[key] = value
    return result


def execute(
    command: str, *, mode: str = "live", model_id: str = MODEL_ID,
    resource: str | None = None, backend: str = "system", validation: bool = False,
    metric: str | None = None, simulator=None, all_errors: bool = False,
    hardware_validation: str | None = None,
) -> dict:
    """Run one request; the legacy validation flag never opens offline-only commands."""
    if command not in COMMANDS or mode not in ("live", "dry-run", "simulate"):
        raise LoadError("argument_error", "Unknown command or execution mode.")
    validate_metric(command, metric)
    validate_error_queue_all(all_errors)
    if all_errors and command != ERROR_QUEUE_COMMAND:
        raise LoadError("argument_error", "Only scpi-error-next accepts all.")
    if command in READBACK_COMMANDS:
        return execute_readback(command, mode=mode, resource=resource, backend=backend,
                                model_id=model_id, simulator=simulator,
                                hardware_validation=hardware_validation)
    if hardware_validation is not None and (
            hardware_validation != "system-query" or command not in SYSTEM_QUERIES or mode == "simulate"):
        raise LoadError("argument_error", "System Query contributor validation requires live or dry-run.")
    validation_result = ({"validation_only": True, "validation_scope": hardware_validation}
                         if hardware_validation else {})
    if model_id != MODEL_ID:
        raise LoadError("unsupported_model", "The requested model profile is not registered.")
    validate_connection(resource, backend, required=mode == "live")
    if mode == "dry-run":
        return {
            "queries": (["*IDN?", NATIVE_MEASUREMENTS[command][metric][1]]
                        if metric is not None else list(QUERIES[command])),
            "backend": "system", "transport": "usb", "timeout_ms": TIMEOUT_MS,
            "read_termination": TERMINATION, "write_termination": TERMINATION,
            "live_access": "product" if command in PRODUCT_LIVE_COMMANDS else "blocked",
            "resource_required_for_live": True,
            **({"max_reads": MAX_ERROR_READS} if all_errors else {}),
            **validation_result,
        }
    if mode == "live" and command not in PRODUCT_LIVE_COMMANDS and hardware_validation is None:
        raise LoadError("unsupported_live_scope", "Product LIVE is not open for this command.")
    try:
        if mode == "simulate":
            require_trusted_session(simulator)
            return _read(simulator or Simulator(), command, metric, all_errors)
        with open_session(resource) as (session, runtime):
            require_trusted_session(session)
            result = {**_read(session, command, metric, all_errors), "visa": runtime, **validation_result}
        return result
    except LoadError as exc:
        if command == "input-state":
            exc.details["input_state"] = "UNKNOWN"
        raise
