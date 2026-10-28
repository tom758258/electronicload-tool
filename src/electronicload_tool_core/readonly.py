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
    "measure": ("*IDN?", "MEAS:VOLT?", "MEAS:CURR?", "MEAS:POW?"),
}
COMMANDS = tuple(QUERIES)


def _query(session, query: str) -> str:
    try:
        return session.query(query)
    except Exception as exc:
        error = communication_error("query_failed", f"Query {query} failed.", exc)
        error.details["query"] = query
        raise error from exc


def _read(session, command: str) -> dict:
    identity = parse_identity(_query(session, "*IDN?"))
    if command == "identify":
        return identity.public_result()
    if command == "input-state":
        response = _query(session, "INP:STAT?").strip()
        if response not in ("0", "1"):
            raise LoadError("invalid_response", "Input state reply must be 0 or 1.", 3)
        return {"input_state": "OFF" if response == "0" else "ON"}
    result = {}
    for key, query in zip(("voltage_v", "current_a", "power_w"), QUERIES["measure"][1:]):
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
) -> dict:
    """Run one request. Validation relaxes only the Product-open requirement."""
    if command not in COMMANDS or mode not in ("live", "dry-run", "simulate"):
        raise LoadError("argument_error", "Unknown command or execution mode.")
    if model_id != MODEL_ID:
        raise LoadError("unsupported_model", "The requested model profile is not registered.")
    validate_connection(resource, backend, required=mode == "live")
    if mode == "dry-run":
        return {
            "queries": list(QUERIES[command]),
            "backend": "system", "transport": "usb", "timeout_ms": TIMEOUT_MS,
            "read_termination": TERMINATION, "write_termination": TERMINATION,
            "live_access": "diagnostic" if command == "identify" else "validation-only",
            "resource_required_for_live": True,
        }
    if mode == "live" and command != "identify" and not validation:
        raise LoadError("unsupported_live_scope", "Product LIVE is not open for this command.")
    try:
        if mode == "simulate":
            return _read(Simulator(), command)
        with open_session(resource) as (session, runtime):
            result = _read(session, command)
            result["visa"] = runtime
        return result
    except LoadError as exc:
        if command == "input-state":
            exc.details["input_state"] = "UNKNOWN"
        raise
