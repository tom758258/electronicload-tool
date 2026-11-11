"""Read-only requests, identity checks, and exact live support policy."""

import math
import re

from .errors import LoadError, communication_error
from .identity import MODEL_ID, parse_identity
from .simulator import Simulator
from .visa import TERMINATION, TIMEOUT_MS, open_session, validate_connection

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
COMMANDS = tuple(QUERIES)
# Exact Product LIVE command allowlist; model, USB and backend checks remain mandatory.
PRODUCT_LIVE_COMMANDS = frozenset({"identify", "input-state", "measure"})


def validate_metric(command: str, metric: object) -> None:
    if metric is not None and (not isinstance(metric, str)
                              or metric not in NATIVE_MEASUREMENTS.get(command, {})):
        raise LoadError("argument_error", "Unsupported measurement metric.")


def _query(session, query: str) -> str:
    try:
        return session.query(query)
    except Exception as exc:
        error = communication_error("query_failed", f"Query {query} failed.", exc)
        error.details["query"] = query
        raise error from exc


def _read(session, command: str, metric: str | None = None) -> dict:
    validate_metric(command, metric)
    identity = parse_identity(_query(session, "*IDN?"))
    if command == "identify":
        return identity.public_result()
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
    metric: str | None = None, simulator=None,
) -> dict:
    """Run one request; the legacy validation flag never opens offline-only commands."""
    if command not in COMMANDS or mode not in ("live", "dry-run", "simulate"):
        raise LoadError("argument_error", "Unknown command or execution mode.")
    validate_metric(command, metric)
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
        }
    if mode == "live" and command not in PRODUCT_LIVE_COMMANDS:
        raise LoadError("unsupported_live_scope", "Product LIVE is not open for this command.")
    try:
        if mode == "simulate":
            return _read(simulator or Simulator(), command, metric)
        with open_session(resource) as (session, runtime):
            result = _read(session, command, metric)
            result["visa"] = runtime
        return result
    except LoadError as exc:
        if command == "input-state":
            exc.details["input_state"] = "UNKNOWN"
        raise
