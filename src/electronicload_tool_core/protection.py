"""IT8812C protection settings and explicitly scoped contributor validation."""

import logging
import re
from decimal import Decimal
from time import sleep

from . import control as c
from .errors import LoadError
from .identity import MODEL_ID, parse_identity
from .simulator import ControlSimulator
from .visa import (open_request_session, open_session, require_trusted_session,
                   validate_request_connection)

COMMANDS = ("protection-status", "protection-set", "protection-clear")
LOGGER = logging.getLogger(__name__)
# IT8800 Programming Guide V2.1, Questionable group and Source Subsystem.
CONDITION_BITS = {
    0: "VF", 1: "OC", 2: "RS", 3: "OP", 4: "OT", 7: "RUN", 8: "EPU", 9: "RRV",
    10: "UNR", 11: "LRV", 12: "OV", 13: "PS", 14: "VON", 15: "TBF",
}
FAULT_MASK = sum(1 << bit for bit in (0, 1, 3, 4, 9, 11, 12, 13))
ENABLE = "software_current_enabled"
REGISTERS = {
    "software_current_limit": "CURR:PROT:LEV",
    "software_current_delay": "CURR:PROT:DEL",
    ENABLE: "CURR:PROT:STAT",
    "software_power_limit": "POW:PROT",
    "software_power_delay": "POW:PROT:DEL",
    "hardware_power_limit": "POW:CONF",
}
LIMITS = {
    # The profile conservatively admits software OCP through the rated 60 A.
    # The programming guide does not specify a model-specific OCP maximum.
    "software_current_limit": Decimal("60"),
    "software_current_delay": Decimal("60"),
    "software_power_limit": Decimal("260"),
    "software_power_delay": Decimal("60"),
    "hardware_power_limit": Decimal("250"),
}
DEFAULTS = {
    "software-power": {"software_power_limit": "MAX", "software_power_delay": Decimal("0")},
    "hardware-power": {"hardware_power_limit": "MAX"},
}
SIMULATED_DEFAULTS = {
    **LIMITS, ENABLE: False, "software_current_delay": Decimal("3"),
    "software_power_delay": Decimal("0"),
}
ARGUMENTS = {
    "protection-status": {"events"},
    "protection-set": {*REGISTERS, "restore_defaults", "confirm"},
    "protection-clear": {"confirm"},
}
TRIALS = {
    "software-current": {ENABLE: True, "software_current_limit": Decimal("0.10"),
                         "software_current_delay": Decimal("1")},
    "software-power": {"software_power_limit": Decimal("0.50"),
                       "software_power_delay": Decimal("1")},
    "hardware-power-baseline": {},
    "hardware-power": {"hardware_power_limit": Decimal("0.50")},
}
HARDWARE_POWER_TRIALS = ("hardware-power-baseline", "hardware-power")
TRIAL_CURRENT = Decimal("0.20")


def validate_arguments(command: str, arguments: dict, *, json_numbers: bool = False) -> dict:
    if not isinstance(arguments, dict) or arguments.keys() - ARGUMENTS[command]:
        raise LoadError("argument_error", "Unsupported Protection arguments.")
    for field in ("events", "confirm", ENABLE):
        if field in arguments and type(arguments[field]) is not bool:
            raise LoadError("argument_error", f"{field} must be a boolean.")
    if command == "protection-status":
        return {}
    if not arguments.get("confirm"):
        raise LoadError("confirmation_required", "Protection changes require explicit confirmation.")
    if command == "protection-clear":
        return {}
    settings = {field: arguments[field] for field in REGISTERS if field in arguments}
    if "restore_defaults" in arguments:
        defaults = arguments["restore_defaults"]
        if type(defaults) is not str or defaults not in DEFAULTS or settings:
            raise LoadError("argument_error", "Select one power default without individual settings.")
        return DEFAULTS[defaults].copy()
    if not settings:
        raise LoadError("argument_error", "Specify a Protection setting or restore_defaults.")
    for field, value in settings.items():
        if field == ENABLE:
            continue
        types = (int, float, Decimal) if json_numbers else (int, float, Decimal, str)
        if type(value) not in types or not c.NUMBER.fullmatch(str(value)):
            raise LoadError("invalid_protection_value", f"{field} must be a finite number.")
        try:
            number = c._decimal(value)
        except LoadError as exc:
            raise LoadError("invalid_protection_value", f"{field} must be a finite number.") from exc
        if not 0 <= number <= LIMITS[field]:
            raise LoadError("protection_out_of_range", f"{field} must be between 0 and {LIMITS[field]}.")
        settings[field] = number
    # Apply disable before editing its thresholds, and enable after verification.
    if settings.get(ENABLE) is True:
        settings[ENABLE] = settings.pop(ENABLE)
    elif settings.get(ENABLE) is False:
        settings = {ENABLE: False, **settings}
    return settings


def _serialize(values: dict) -> dict:
    return {field: str(value) if isinstance(value, Decimal) else value
            for field, value in values.items()}


def _register(raw: str, query: str) -> dict:
    if not re.fullmatch(r"\+?[0-9]{1,5}", raw) or int(raw) > 65535:
        raise LoadError("invalid_response", "Invalid 16-bit Protection register.", 3, query=query)
    bits = int(raw)
    return {"raw": bits, "flags": [name for bit, name in CONDITION_BITS.items() if bits & (1 << bit)]}


def _condition(session) -> dict:
    return _register(c._query(session, "STAT:QUES:COND?"), "STAT:QUES:COND?")


def _read_setting(session, field: str, suffix: str = ""):
    query = REGISTERS[field] + "?" + suffix
    if field == ENABLE:
        # The programming guide specifies NR3 for the boolean readback.
        number = c._decimal_readback(session, query)
        if number not in (0, 1):
            raise LoadError("invalid_response", "Invalid current protection enable state.", 3, query=query)
        return number == 1
    try:
        number = c._decimal_readback(session, query)
    except LoadError as exc:
        if suffix:
            LOGGER.debug("Protection %s failed: %s", query, exc.code)
        raise
    if suffix or not 0 <= number <= LIMITS[field]:
        LOGGER.debug("Protection %s returned %s (accepted range 0 to %s)",
                     query, number, LIMITS[field])
    if not 0 <= number <= LIMITS[field]:
        raise LoadError("invalid_response", "Protection readback is outside the model bounds.", 3, query=query)
    return number


def _status(session, events: bool) -> dict:
    result = {"input_state": c._flag(session, "INP:STAT?"), "condition": _condition(session),
              "settings": _serialize({field: _read_setting(session, field) for field in REGISTERS})}
    if events:
        query = "STAT:QUES:EVEN?"
        result["events"] = _register(c._query(session, query), query)
    return result


def _write_token(value) -> str:
    if isinstance(value, bool):
        return str(int(value))
    return str(value)


def _set(session, settings: dict, stop) -> dict:
    confirmed = {}
    pending = None
    try:
        c._mutation_off(session, stop)
        expected = {field: _read_setting(session, field, " MAX") if value == "MAX" else value
                    for field, value in settings.items()}
        c._mutation_off(session, stop)
        c._write(session, "SYST:REM")
        for field, value in settings.items():
            c._mutation_off(session, stop)
            pending = field
            c._write(session, f"{REGISTERS[field]} {_write_token(value)}")
            observed = _read_setting(session, field)
            if observed != expected[field]:
                raise LoadError("readback_mismatch", "Protection setting did not match readback.", 3,
                                field=field, expected=_write_token(expected[field]),
                                observed=_write_token(observed))
            confirmed[field] = observed
            pending = None
        c._mutation_off(session, stop)
    except LoadError as exc:
        exc.details.update(confirmed=_serialize(confirmed), unconfirmed=[pending] if pending else [],
                           not_attempted=[field for field in settings
                                          if field not in confirmed and field != pending])
        raise
    return {"input_state": "OFF", "applied": _serialize(confirmed)}


def _clear(session, stop) -> dict:
    c._mutation_off(session, stop)
    c._write(session, "SYST:REM")
    c._mutation_off(session, stop)
    try:
        c._write(session, "PROT:CLE")
        state = c._flag(session, "INP:STAT?")
    except LoadError as exc:
        exc.details.update(clear_attempted=True, input_state="UNKNOWN", operator_action_required=True)
        raise
    if state == "ON":
        try:
            c._write(session, "INP OFF")
            final = c._flag(session, "INP:STAT?")
        except LoadError as exc:
            exc.details.update(clear_attempted=True, input_state="UNKNOWN", off_attempted=True,
                               operator_action_required=True)
            raise
        try:
            condition = _condition(session)
        except LoadError as exc:
            exc.details.update(clear_attempted=True, input_state=final, off_attempted=True,
                               operator_action_required=True)
            raise
        raise LoadError("protection_clear_unexpected_on", "Input turned ON after Protection Clear.", 3,
                        clear_attempted=True, off_attempted=True, input_state=final,
                        condition=condition, operator_action_required=final != "OFF")
    try:
        condition = _condition(session)
    except LoadError as exc:
        exc.details.update(clear_attempted=True, input_state="OFF", operator_action_required=True)
        raise
    if condition["raw"] & FAULT_MASK:
        raise LoadError("protection_still_active", "Protection remains active after Clear.", 3,
                        clear_attempted=True, input_state="OFF", condition=condition)
    return {"input_state": "OFF", "condition": condition}


def _simulate(command: str, settings: dict, events: bool, session, stop) -> dict:
    if command == "protection-status":
        result = {"input_state": c._flag(session, "INP:STAT?"), "condition": _condition(session),
                  "settings": _serialize({**SIMULATED_DEFAULTS, **session.protection_settings})}
        if events:
            result["events"] = _register(str(session.protection_events), "STAT:QUES:EVEN?")
            session.protection_events = 0
        return result
    c._mutation_off(session, stop)
    if command == "protection-clear":
        session.protection_condition &= ~FAULT_MASK
        return {"input_state": "OFF", "condition": _condition(session)}
    changes = {field: LIMITS[field] if value == "MAX" else value for field, value in settings.items()}
    session.protection_settings.update(changes)
    return {"input_state": "OFF", "applied": _serialize(changes)}


def _trial_snapshot(session) -> dict:
    cc = c._check_input_on_conditions(session, None, allow_cv=False, allow_cr=False, allow_cw=False)
    if (c._flag(session, "INP:TIM?") != "OFF"
            or c._flag(session, c.SLOW_RISE_QUERY) != "OFF"
            or c._decimal_readback(session, "VOLT:ON?") >= Decimal("4.75")):
        raise LoadError("protection_trial_interlock", "Timer, slow rise or VON could affect the trial.",
                        input_state="OFF")
    current = c.validate_current(c._decimal_readback(session, "CURR?"), cc["range"])
    return {"range": cc["range"], "current_a": str(current)}


def _trial_guard(session, trial: str, stop) -> None:
    cc = _trial_snapshot(session)
    if Decimal(cc["current_a"]) != TRIAL_CURRENT:
        raise LoadError("protection_trial_interlock", "Protection trials require Fixed CC at 0.20 A.",
                        input_state="OFF")
    settings = {field: _read_setting(session, field) for field in REGISTERS}
    if any(settings[field] != value for field, value in TRIALS[trial].items()):
        raise LoadError("readback_mismatch", "Protection trial settings do not match the requested case.", 3,
                        input_state="OFF")
    if ((trial != "software-current" and settings[ENABLE]
         and settings["software_current_limit"] <= TRIAL_CURRENT)
            or (trial != "software-power" and settings["software_power_limit"] <= Decimal("1.05"))
            or (trial != "hardware-power" and settings["hardware_power_limit"] <= Decimal("1.05"))
            or _condition(session)["raw"] & FAULT_MASK):
        raise LoadError("protection_trial_interlock", "An existing protection could affect the trial.",
                        input_state="OFF")
    c._mutation_off(session, stop)


def _run_trial(session, trial: str, stop) -> dict:
    _trial_guard(session, trial, stop)
    c._write(session, "SYST:REM")
    _trial_guard(session, trial, stop)
    try:
        c._write(session, "INP ON")
        # Capture the software trial's available stimulus before the delay
        # expires. A late sample cannot disprove an observed trip.
        sample = {}
        if trial not in HARDWARE_POWER_TRIALS:
            sample = {"voltage_v": c._number(session, "MEAS:VOLT?"),
                      "current_a": c._number(session, "MEAS:CURR?")}
            sample["power_w"] = sample["voltage_v"] * sample["current_a"]
        # Give the one-second software delay time to expire. The caller bounds
        # the complete process, including queries and explicit OFF.
        sleep(2)
        result = {"input_state": c._flag(session, "INP:STAT?"), "condition": _condition(session), **sample}
        if trial in HARDWARE_POWER_TRIALS:
            result.update(voltage_v=c._number(session, "MEAS:VOLT?"),
                          current_a=c._number(session, "MEAS:CURR?"),
                          power_w=c._number(session, "FETC:POW?"))
            result["calculated_power_w"] = result["voltage_v"] * result["current_a"]
    except LoadError as exc:
        exc.details.update(input_on_attempted=True, input_state="UNKNOWN", operator_action_required=True)
        raise
    return result


def execute(command: str, *, mode: str = "live", resource=None, backend: str = "system",
            model_id: str = MODEL_ID, arguments=None, simulator=None, stop=None,
            hardware_validation: bool = False, snapshot: bool = False, trial: str | None = None,
            asrl=None) -> dict:
    if mode not in {"live", "dry-run", "simulate"} or command not in (*COMMANDS, "input-on"):
        raise LoadError("argument_error", "Unknown Protection command or mode.")
    if model_id != MODEL_ID:
        raise LoadError("unsupported_model", "The requested profile is not registered.")
    if hardware_validation and mode == "simulate":
        raise LoadError("argument_error", "Contributor Protection validation requires LIVE or dry-run.")
    if (snapshot and (not hardware_validation or command != "protection-status")) or (
            command != "input-on" and trial is not None):
        raise LoadError("argument_error", "Unexpected contributor Protection arguments.")
    args = {} if arguments is None else arguments
    if command == "input-on":
        if not hardware_validation or type(trial) is not str or trial not in TRIALS:
            raise LoadError("argument_error", "Select a contributor Protection trial.")
        if args != {"confirm": True} or args["confirm"] is not True:
            raise LoadError("confirmation_required", "Input ON requires explicit confirmation.")
        settings = {}
    else:
        settings = validate_arguments(command, args)
    if mode == "live" and not hardware_validation:
        raise LoadError("unsupported_live_scope", "Protection is offline only.")
    validate_request_connection(resource, backend, mode=mode, asrl=asrl,
                                allowed=hardware_validation)
    marker = {"validation_only": True, "validation_scope": "protection"} if hardware_validation else {}
    if mode == "dry-run":
        writes = [f"{REGISTERS[field]} {_write_token(value)}" for field, value in settings.items()]
        if command == "protection-clear":
            writes = ["PROT:CLE"]
        elif trial is not None:
            writes = ["INP ON"]
        return {**marker, "command": command, "hardware_access": False, "events": args.get("events", False),
                "planned_writes": ["SYST:REM", *writes] if writes else [],
                **({"trial": trial, "current_a": str(TRIAL_CURRENT)} if trial else {})}
    if mode == "simulate":
        require_trusted_session(simulator)
        result = _simulate(command, settings, args.get("events", False),
                           simulator if simulator is not None else ControlSimulator(), stop)
        return {**result, "state_source": "simulate"}
    with open_request_session(resource, asrl, open_session) as (session, runtime):
        parse_identity(c._query(session, "*IDN?"))
        if command == "protection-status":
            # Finish the read-only trial checks before consuming an event.
            cc = _trial_snapshot(session) if snapshot else None
            result = _status(session, args.get("events", False))
            if cc is not None:
                result["fixed_cc"] = cc
        elif command == "protection-set":
            result = _set(session, settings, stop)
        elif command == "protection-clear":
            result = _clear(session, stop)
        else:
            result = _run_trial(session, trial, stop)
        return {**result, **marker, "visa": runtime}
