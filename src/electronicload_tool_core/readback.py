"""Independent, query-only IT8812C configuration snapshots."""

from decimal import Decimal

from . import control as c
from .errors import LoadError
from .identity import MODEL_ID, parse_identity
from .simulator import ControlSimulator, transient_settings
from .visa import open_request_session, require_trusted_session, validate_request_connection

SCOPE = "readback"
COMMANDS = ("readback", "fixed-status", "fixed-slew-status")
CONTROL_STATUS_COMMANDS = (
    "von-status", "short-status", "average-status", "transient-status", "list-status",
)
FUNCTIONS = {alias: function for function, prefix in c.TRANSIENT_FUNCTIONS.items()
             for alias in (prefix, {"CC": "CURRENT", "CV": "VOLTAGE",
                                    "CR": "RESISTANCE", "CW": "POWER"}[function])}
TRANSIENT_MODES = {"CONT": "continuous", "CONTINUOUS": "continuous",
                   "PULS": "pulse", "PULSE": "pulse", "TOGG": "toggle", "TOGGLE": "toggle"}
SLEW_QUERIES = {"rise_slew": "CURR:SLEW:POS", "fall_slew": "CURR:SLEW:NEG"}
TRANSIENT_LEVEL_QUERIES = {"level_a": "CURR:TRAN:ALEV", "level_b": "CURR:TRAN:BLEV", **SLEW_QUERIES}
SETPOINT_LIMITS = {
    "CC": {name: (0, maximum) for name, maximum in c.RANGE_LIMITS.items()},
    "CV": {name: (c.CV_MIN_VOLTAGE, maximum) for name, maximum in c.CV_RANGE_LIMITS.items()},
    "CR": c.CR_RANGE_LIMITS,
    "CW": {None: (0, c.CW_MAX_POWER)},
}
FIXED_FIELDS = {"CC": "current_a", "CV": "voltage_v", "CR": "resistance_ohm", "CW": "power_w"}
SHORT_QUERY = "INP:SHOR:STAT?"
VON_THRESHOLD_QUERY = "VOLT:ON?"
VON_LATCH_QUERY = "VOLT:LATC?"
LIST_HEADER_QUERIES = ("LIST:RANG?", "LIST:STEP?", "LIST:COUN?")
LIST_STEP_QUERIES = {
    "current_a": "LIST:LEV? {}",
    "slew_a_per_us": "LIST:SLEW? {}",
    "width_s": "LIST:WID? {}",
}
CONTROL_STATUS_QUERIES = {
    "von-status": (VON_THRESHOLD_QUERY, VON_LATCH_QUERY),
    "short-status": (SHORT_QUERY,),
    "list-status": ("FUNC:MODE?", "FUNC?", "TRAN?", *LIST_HEADER_QUERIES,
                    *(query.format("{step}") for query in LIST_STEP_QUERIES.values()), "INP:STAT?"),
}


def _selection(session):
    mode, token = c._load_mode(session)
    simulated_mode = isinstance(session, ControlSimulator) and mode == "TRAN"
    if mode not in {"FIX", "FIXED", "LIST"} and not simulated_mode:
        raise LoadError("invalid_response", "Unknown function mode.", 3, query="FUNC:MODE?")
    if token not in FUNCTIONS:
        raise LoadError("invalid_response", "Unknown load function.", 3, query="FUNC?")
    enabled = c._flag(session, "TRAN?") == "ON"
    selected = "list" if mode == "LIST" else "transient" if enabled else "fixed"
    if simulated_mode and not enabled:
        mode = "FIX"
    return {"active_mode": selected, "function_mode": mode, "function": FUNCTIONS[token],
            "transient_enabled": enabled}


def _range(session, function):
    prefix = c.TRANSIENT_FUNCTIONS[function]
    query = f"{prefix}:RANG?"
    raw = c._decimal_readback(session, query)
    if function == "CC":
        if not 0 <= raw <= c.RANGE_LIMITS["high"]:
            raise LoadError("invalid_range_readback", "Current range readback is outside model limits.",
                            3, query=query)
        return c._range_name(raw), raw
    limits = {name: maximum for name, (_, maximum) in SETPOINT_LIMITS[function].items()}
    if not 0 <= raw <= limits["high"]:
        raise LoadError("invalid_range_readback", "Range readback is outside model limits.", 3, query=query)
    # The guide describes numeric selectors. Only label the known range endpoints.
    return {value: name for name, value in limits.items()}.get(raw), raw


def _bounds(limits, range_name):
    if range_name in limits:
        return limits[range_name]
    return min(lower for lower, _ in limits.values()), max(upper for _, upper in limits.values())


def _fixed(session, function):
    settings = {"range": None}
    if function != "CW":
        unit = FIXED_FIELDS[function].split("_", 1)[1]
        name, raw = _range(session, function)
        settings.update(range=name, **{f"range_readback_{unit}": float(raw)})
    if function == "CV":
        settings["range_auto"] = c._flag(session, "VOLT:RANG:AUTO?") == "ON"
    field = FIXED_FIELDS[function]
    query = f"{c.TRANSIENT_FUNCTIONS[function]}?"
    value = c._decimal_readback(session, query)
    minimum, maximum = _bounds(SETPOINT_LIMITS[function], settings["range"])
    if not minimum <= value <= maximum:
        raise LoadError("invalid_response", "Fixed setpoint is outside model limits.", 3, query=query)
    return {**settings, field: float(value)}


def fixed_slew_status(session):
    range_name = None
    if isinstance(session, ControlSimulator):
        # The simulator retains the last configured pair across CC range changes.
        if not session.fixed_cc_slew:
            return {"configured": False, "rise_slew_a_per_us": None, "fall_slew_a_per_us": None}
    else:
        range_name, _ = _range(session, "CC")
    minimum, maximum = _bounds(c.SLEW_LIMITS, range_name)
    readings = {"configured": True}
    for field, stem in SLEW_QUERIES.items():
        query = stem + "?"
        value = c._decimal_readback(session, query)
        if not minimum <= value <= maximum:
            raise LoadError("invalid_response", "Current slew is outside the bounds for the reported range.",
                            3, query=query, range=range_name, observed=str(value),
                            minimum=str(minimum), maximum=str(maximum))
        readings[f"{field}_a_per_us"] = str(value)
    return readings


def transient_mode(session, prefix):
    query = f"{prefix}:TRAN:MODE?"
    token = c._query(session, query).upper()
    if token not in TRANSIENT_MODES:
        raise LoadError("invalid_response", "Unknown physical Transient mode.", 3, query=query)
    return TRANSIENT_MODES[token]


def transient_values(session, prefix, mode, *, summary=False):
    queries = (TRANSIENT_LEVEL_QUERIES if prefix == "CURR" and not summary else
               {"level_a": f"{prefix}:TRAN:ALEV", "level_b": f"{prefix}:TRAN:BLEV"})
    if not summary and mode == "continuous":
        queries = {**queries, "width_a": f"{prefix}:TRAN:AWID", "width_b": f"{prefix}:TRAN:BWID"}
    elif not summary and mode == "pulse":
        queries = {**queries, "pulse_width": f"{prefix}:TRAN:AWID"}
    return {field: c._decimal_readback(session, query + "?") for field, query in queries.items()}


def _transient(session, selection, *, summary=False):
    if isinstance(session, ControlSimulator):
        status = session.transient_status()
        if not summary:
            return status
        configuration = status["configuration"]
        if configuration is None:
            raise LoadError("invalid_response", "Enabled Transient has no simulated configuration.", 3)
        return {key: value for key, value in configuration.items()
                if key in {"transient_mode", "range"} or key.startswith(("level_a_", "level_b_"))}
    function = selection["function"]
    prefix = c.TRANSIENT_FUNCTIONS[function]
    mode = transient_mode(session, prefix)
    range_name, raw = _range(session, function) if function != "CW" else (None, None)
    values = transient_values(session, prefix, mode, summary=summary)
    level_bounds = _bounds(SETPOINT_LIMITS[function], range_name)
    for field, value in values.items():
        if field in {"level_a", "level_b"}:
            minimum, maximum = level_bounds
        elif field in SLEW_QUERIES:
            minimum, maximum = _bounds(c.SLEW_LIMITS, range_name)
        else:
            minimum, maximum = Decimal("0.00002"), Decimal("3600")
        if not minimum <= value <= maximum:
            raise LoadError("invalid_response", "Transient readback is outside model limits.", 3,
                            field=field)
    configuration = transient_settings(function, {"transient_mode": mode, "range": range_name, **values})
    range_readback = float(raw) if raw is not None else None
    if summary:
        return {**configuration, "range_readback": range_readback}
    return {"configured": True, "function": function, "mode": mode, "range": range_name,
            "range_readback": range_readback,
            "enabled": selection["transient_enabled"], "last_trigger": None,
            "configuration": configuration}


def _list_integer(session, query, minimum, maximum):
    value = c._decimal_readback(session, query)
    if not minimum <= value <= maximum or value != value.to_integral_value():
        raise LoadError("invalid_response", "Invalid LIST count.", 3, query=query, observed=str(value),
                        minimum=str(minimum), maximum=str(maximum))
    return int(value)


def _list_configuration(session, *, summary=False, expected_count=None):
    range_query, count_query, repeat_query = LIST_HEADER_QUERIES
    raw = c._decimal_readback(session, range_query)
    if not 0 <= raw <= c.RANGE_LIMITS["high"]:
        raise LoadError("invalid_range_readback", "LIST range is outside model limits.", 3,
                        query=range_query, observed=str(raw), minimum="0",
                        maximum=str(c.RANGE_LIMITS["high"]))
    count = _list_integer(session, count_query, 2, 84)
    if expected_count is not None and count != expected_count:
        raise LoadError("readback_mismatch", "LIST step count changed during the settings trial.", 3,
                        query=count_query, expected=str(expected_count), observed=str(count),
                        minimum="2", maximum="84")
    repeat = _list_integer(session, repeat_query, 1, 65536)
    ranges = {value: name for name, value in c.RANGE_LIMITS.items()}
    metadata = {"range": ranges.get(raw), "range_readback_a": float(raw),
                "repeat_count": repeat, "step_count": count}
    if summary:
        return metadata
    range_name = metadata["range"]
    limits = {
        "current_a": _bounds(SETPOINT_LIMITS["CC"], range_name),
        "slew_a_per_us": _bounds(c.SLEW_LIMITS, range_name),
        "width_s": c.LIST_WIDTH_LIMITS,
    }
    steps = []
    for index in range(1, count + 1):
        step = {}
        for field, template in LIST_STEP_QUERIES.items():
            query = template.format(index)
            minimum, maximum = limits[field]
            value = c._decimal_readback(session, query)
            if not minimum <= value <= maximum:
                raise LoadError("invalid_response", "LIST step readback is outside model limits.", 3,
                                query=query, step=index, observed=str(value), minimum=str(minimum), maximum=str(maximum))
            step[field] = value
        steps.append(step)
    return {**metadata, "steps": steps}


def _list_status(session, selection, *, summary=False, expected_count=None):
    if isinstance(session, ControlSimulator):
        status = session.list_status()
        if not summary:
            return status
        configuration = status["configuration"]
        if configuration is None:
            raise LoadError("invalid_response", "Selected LIST has no simulated configuration.", 3)
        return {"range": configuration["range"], "repeat_count": configuration["repeat_count"],
                "step_count": len(configuration["steps"])}
    configuration = _list_configuration(session, summary=summary, expected_count=expected_count)
    if summary:
        return configuration
    observed = {**configuration, "steps": [
        {field: float(value) if field == "current_a" else str(value) for field, value in step.items()}
        for step in configuration["steps"]]}
    return {"configured": True, "selected": selection["active_mode"] == "list",
            "function_mode": selection["function_mode"], "range": configuration["range"],
            "slot": None, "last_trigger": None, "configuration": observed}


def von_status(session):
    voltage = c._decimal_readback(session, VON_THRESHOLD_QUERY)
    if not 0 <= voltage <= c.VON_MAX_VOLTAGE:
        raise LoadError("invalid_response", "VON threshold is outside model limits.", 3,
                        query=VON_THRESHOLD_QUERY)
    return {"threshold_v": str(voltage), "latch": c._flag(session, VON_LATCH_QUERY).lower()}


def snapshot(session, command, *, expected_list_count=None):
    if command == "von-status":
        return von_status(session)
    if command == "short-status":
        return {"short_state": c._flag(session, SHORT_QUERY)}
    if command == "average-status":
        return {"average_count": c._average_count(session)}

    selection = _selection(session)
    active, function = selection["active_mode"], selection["function"]
    if command in {"fixed-status", "fixed-slew-status"} and active != "fixed":
        raise LoadError("mode_not_fixed", "The load is not in a Fixed mode.", **selection)
    if command == "fixed-slew-status" and function != "CC":
        raise LoadError("mode_not_fixed", "Fixed CC is required to read its slew rates.", **selection)
    if command in ("list-status", "transient-status"):
        settings = (_list_status(session, selection, expected_count=expected_list_count) if command == "list-status"
                    else _transient(session, selection))
        return {**settings, "input_state": c._flag(session, "INP:STAT?")}

    common = {**selection, "input_state": c._flag(session, "INP:STAT?")}
    if command == "fixed-slew-status":
        return {**common, **fixed_slew_status(session)}
    if command == "fixed-status":
        return {**common, **_fixed(session, function)}
    if active == "list":
        settings = _list_status(session, selection, summary=True)
    elif active == "transient":
        settings = _transient(session, selection, summary=True)
    else:
        settings = _fixed(session, function)
        if function == "CR":
            settings["led_enabled"] = c._flag(session, c.LED_QUERY) == "ON"
            if settings["led_enabled"]:
                settings["vdrop_v"] = c._cr_vdrop(session)
    return {**common, "short_state": c._flag(session, SHORT_QUERY), "configuration": settings}


def execute(command, *, mode, resource, backend="system", model_id=MODEL_ID,
            simulator=None, hardware_validation=None,
            asrl=None):
    if command not in (*COMMANDS, *CONTROL_STATUS_COMMANDS) or mode not in ("live", "simulate", "dry-run"):
        raise LoadError("argument_error", "Unknown readback command or execution mode.")
    if model_id != MODEL_ID:
        raise LoadError("unsupported_model", "The requested model profile is not registered.")
    validate_request_connection(resource, backend, mode=mode, asrl=asrl,
                                allowed=hardware_validation == SCOPE)
    if hardware_validation not in (None, SCOPE) or (hardware_validation and mode == "simulate"):
        raise LoadError("argument_error", "Readback contributor validation requires live or dry-run.")
    if mode == "live" and hardware_validation != SCOPE:
        raise LoadError("unsupported_live_scope", "Product LIVE is not open for this readback.")
    marker = ({"validation_only": True, "validation_scope": SCOPE} if hardware_validation else {})
    if mode == "dry-run":
        queries = CONTROL_STATUS_QUERIES.get(command)
        return {"command": command, "hardware_access": False, "planned_writes": [], **marker,
                **({"queries": ["*IDN?", *queries]} if queries else {})}
    if mode == "simulate":
        require_trusted_session(simulator)
        return {**snapshot(simulator or ControlSimulator(), command), "state_source": "simulate"}
    with open_request_session(resource, asrl, c.open_session) as (session, runtime):
        require_trusted_session(session)
        parse_identity(c._query(session, "*IDN?"))
        result = snapshot(session, command)
    return {**result, "state_source": "instrument_readback", **marker, "visa": runtime}
