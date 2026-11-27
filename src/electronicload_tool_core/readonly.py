"""Read-only requests, identity checks, and exact live support policy."""

import math
import re

from .control import MEASUREMENT_VALIDATION_SCOPE
from .discovery import verify_asrl_identity
from .errors import LoadError
from .identity import MODEL_ID, parse_identity
from .readback import COMMANDS as READBACK_COMMANDS, execute as execute_readback
from .simulator import Simulator
from .visa import (ASRL_IDN_TIMEOUT_MS, ASRL_OPEN_TIMEOUT_MS, TERMINATION, TIMEOUT_MS,
                   SerialOptions, open_asrl_session, open_request_session, open_session, query as _query,
                   require_trusted_session, validate_connection, validate_request_connection)

BASIC_MEASUREMENTS = {
    "voltage": ("voltage_v", "MEAS:VOLT?"),
    "current": ("current_a", "MEAS:CURR?"),
    # FETCh returns the latest available power result; it may not be a fresh sample.
    "power": ("power_w", "FETC:POW?"),
}
QUERIES = {
    "identify": ("*IDN?",),
    "input-state": ("*IDN?", "INP:STAT?"),
    "measure": ("*IDN?", *(query for _, query in BASIC_MEASUREMENTS.values())),
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
ERROR_QUEUE_COMMAND = "error"
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
# Standalone USB diagnostics are explicitly admitted; Worker permissions are unchanged.
CLI_DIAGNOSTIC_LIVE_COMMANDS = frozenset({"scpi-version", "display-mode", ERROR_QUEUE_COMMAND})
ASRL_READONLY_SCOPE = "asrl-readonly"
ASRL_READONLY_COMMANDS = frozenset({"identify", "input-state", "measure"})


def validate_metric(command: str, metric: object) -> None:
    if metric is not None and (not isinstance(metric, str)
                              or metric not in NATIVE_MEASUREMENTS.get(command, {})):
        raise LoadError("argument_error", "Unsupported measurement metric.")


def validate_error_queue_all(all_errors: object) -> None:
    if type(all_errors) is not bool:
        raise LoadError("argument_error", "Error Queue all must be a boolean.")


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


def read_measurement(session, command: str, metric: str | None = None, *,
                     quantities=tuple(BASIC_MEASUREMENTS)) -> dict:
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
        readings = (BASIC_MEASUREMENTS[quantity] for quantity in quantities)
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


def _execute_asrl_readonly(command, mode, model_id, resource, backend, serial_options) -> dict:
    if command not in ASRL_READONLY_COMMANDS or mode == "simulate":
        raise LoadError("unsupported_live_scope", "ASRL read-only validation requires an eligible live or dry-run command.")
    if model_id != MODEL_ID:
        raise LoadError("unsupported_model", "The requested model profile is not registered.")
    validate_connection(resource, backend, required=True, asrl=True)
    overrides = serial_options.overrides() if serial_options is not None else {}
    scope = {"transport": "asrl", "validation_only": True, "validation_scope": ASRL_READONLY_SCOPE}
    if mode == "dry-run":
        return {
            **scope, "queries": list(QUERIES[command]), "backend": backend,
            "open_timeout_ms": ASRL_OPEN_TIMEOUT_MS, "idn_timeout_ms": ASRL_IDN_TIMEOUT_MS,
            "timeout_ms": TIMEOUT_MS, "serial_options": overrides,
            "live_access": "blocked", "resource_required_for_live": True,
        }
    try:
        with open_asrl_session(resource, overrides) as (session, runtime):
            identity = verify_asrl_identity(session)
            if command == "identify":
                result = identity.public_result()
            else:
                session.set_io_timeout(TIMEOUT_MS)
                result = read_measurement(session, command)
            return {**result, "visa": runtime, **scope}
    except LoadError as exc:
        if command == "input-state":
            exc.details["input_state"] = "UNKNOWN"
        raise


def execute(
    command: str, *, mode: str = "live", model_id: str = MODEL_ID,
    resource: str | None = None, backend: str = "system", validation: bool = False,
    metric: str | None = None, simulator=None, all_errors: bool = False,
    hardware_validation: str | None = None, serial_options: SerialOptions | None = None,
    asrl=None, standalone_cli: bool = False,
) -> dict:
    """Run one request; only the standalone CLI may opt into basic USB diagnostics."""
    if command not in COMMANDS or mode not in ("live", "dry-run", "simulate"):
        raise LoadError("argument_error", "Unknown command or execution mode.")
    validate_metric(command, metric)
    validate_error_queue_all(all_errors)
    if all_errors and command != ERROR_QUEUE_COMMAND:
        raise LoadError("argument_error", "Only error accepts all.")
    if asrl is not None and hardware_validation is None and command in ASRL_READONLY_COMMANDS:
        return _execute_asrl_readonly(command, mode, model_id, resource, backend, asrl)
    if hardware_validation == ASRL_READONLY_SCOPE:
        if asrl is not None:
            raise LoadError("argument_error", "Select one ASRL contributor option.")
        return _execute_asrl_readonly(command, mode, model_id, resource, backend, serial_options)
    if serial_options is not None:
        raise LoadError("argument_error", "Serial options require the ASRL read-only validation scope.")
    if command in READBACK_COMMANDS:
        return execute_readback(command, mode=mode, resource=resource, backend=backend,
                                model_id=model_id, simulator=simulator,
                                hardware_validation=hardware_validation, asrl=asrl)
    validation_commands = {
        "system-query": SYSTEM_QUERIES,
        MEASUREMENT_VALIDATION_SCOPE: NATIVE_MEASUREMENTS,
    }
    if hardware_validation is not None and (not isinstance(hardware_validation, str)
            or command not in validation_commands.get(hardware_validation, ()) or mode == "simulate"):
        raise LoadError("argument_error", "Command does not match a live or dry-run contributor scope.")
    validation_result = ({"validation_only": True, "validation_scope": hardware_validation}
                         if hardware_validation else {})
    if model_id != MODEL_ID:
        raise LoadError("unsupported_model", "The requested model profile is not registered.")
    validate_request_connection(resource, backend, mode=mode, asrl=asrl,
                                allowed=hardware_validation is not None)
    product_live = command in PRODUCT_LIVE_COMMANDS or (
        standalone_cli and asrl is None and hardware_validation is None
        and command in CLI_DIAGNOSTIC_LIVE_COMMANDS
    )
    if mode == "dry-run":
        return {
            "queries": (["*IDN?", NATIVE_MEASUREMENTS[command][metric][1]]
                        if metric is not None else list(QUERIES[command])),
            "backend": "system", "transport": "asrl" if asrl is not None else "usb", "timeout_ms": TIMEOUT_MS,
            "read_termination": asrl.read_termination if asrl is not None else TERMINATION,
            "write_termination": asrl.write_termination if asrl is not None else TERMINATION,
            "live_access": "product" if product_live else "blocked",
            "resource_required_for_live": True,
            **({"max_reads": MAX_ERROR_READS} if all_errors else {}),
            **validation_result,
        }
    if mode == "live" and not product_live and hardware_validation is None:
        raise LoadError("unsupported_live_scope", "Product LIVE is not open for this command.")
    try:
        if mode == "simulate":
            require_trusted_session(simulator)
            return _read(simulator or Simulator(), command, metric, all_errors)
        with open_request_session(resource, asrl, open_session) as (session, runtime):
            require_trusted_session(session)
            result = {**_read(session, command, metric, all_errors), "visa": runtime, **validation_result}
        return result
    except LoadError as exc:
        if command == "input-state":
            exc.details["input_state"] = "UNKNOWN"
        raise
