"""Version 1 Software Sequence preflight and serial execution."""

from decimal import Decimal
from threading import Event

from . import control
from .errors import LoadError
from .hardware_validation import (
    sequence_fixed_setup,
    sequence_input_on,
    validate_sequence_trial,
)
from .identity import MODEL_ID, parse_identity
from .readonly import read_measurement
from .simulator import ControlSimulator
from .visa import open_session, require_trusted_session, validate_connection

COMMAND = "sequence"
SETPOINT_FIELDS = {"set-cc": "current", "set-cv": "voltage", "set-cr": "resistance",
                   "set-cw": "power"}
READ_ACTIONS = {"measure", "input-state"}
SLEW_FIELDS = {"rise_slew", "fall_slew"}
UNTRUSTED_ERRORS = {"query_failed", "write_failed", "invalid_response", "invalid_identity",
                    "identity_mismatch", "trace_session_untrusted"}


def _number(value):
    if type(value) in (int, float, Decimal):
        number = Decimal(str(value))
        if number.is_finite():
            return number
    raise LoadError("argument_error", "Expected a finite JSON number.")


def _step_arguments(action, arguments):
    if action in SETPOINT_FIELDS:
        field = SETPOINT_FIELDS[action]
        required = {field} if action == "set-cw" else {field, "range"}
        optional = SLEW_FIELDS if action == "set-cc" else set()
        if not required <= arguments.keys() or arguments.keys() - required - optional:
            raise LoadError("argument_error", "Supply the Fixed setup fields for this action.")
        settings = {key: _number(value) for key, value in arguments.items() if key != "range"}
        if action == "set-cw":
            control.validate_power(settings[field])
        else:
            settings["range"] = control.resolve_fixed_range(action, settings[field], arguments["range"])
        if action == "set-cc":
            control.validate_fixed_cc_slew(settings["range"], settings.get("rise_slew"),
                                           settings.get("fall_slew"))
        # Formatting a zero setpoint for SCPI must not expand a JSON exponent.
        if settings[field].is_zero():
            settings[field] = Decimal(0)
        return settings
    if action == "wait":
        if arguments.keys() != {"seconds"}:
            raise LoadError("argument_error", "Wait requires seconds.")
        seconds = _number(arguments["seconds"])
        if not 0 <= seconds <= 3600:
            raise LoadError("wait_out_of_range", "Wait must be between 0 and 3600 seconds.")
        return {"seconds": seconds}
    if arguments:
        raise LoadError("argument_error", "This action takes no arguments.")
    return {}


def validate_document(document):
    if not isinstance(document, dict) or document.keys() != {"version", "loop_count", "steps"}:
        raise LoadError("argument_error", "Sequence requires version, loop_count and steps.")
    version, loops, steps = (document[key] for key in ("version", "loop_count", "steps"))
    if type(version) is not int or version != 1:
        raise LoadError("argument_error", "Sequence version must be integer 1.")
    if type(loops) is not int or not 1 <= loops <= 255:
        raise LoadError("argument_error", "Sequence loop_count must be an integer from 1 to 255.")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 255 or loops * len(steps) > 10000:
        raise LoadError("argument_error", "Sequence requires 1 to 255 steps and at most 10000 expanded steps.")

    # The same steps repeat and every loop starts OFF without a fresh setup.
    actions = {*SETPOINT_FIELDS, *READ_ACTIONS, "wait", "input-on", "input-off"}
    prepared = []
    input_on = fresh_setup = contains_on = False
    for index, step in enumerate(steps, 1):
        action = step.get("action") if isinstance(step, dict) else None
        location = {"loop_index": 1, "step_index": index,
                    "action": action if isinstance(action, str) else None}
        try:
            if (not isinstance(step, dict) or step.keys() != {"action", "arguments"}
                    or not isinstance(action, str) or action not in actions
                    or not isinstance(step["arguments"], dict)):
                raise LoadError("invalid_step", "Each step requires a supported action and arguments object.")
            settings = _step_arguments(action, step["arguments"])
            if action in SETPOINT_FIELDS:
                if input_on:
                    raise LoadError("set_requires_input_off", "Fixed setup requires Input OFF.")
                fresh_setup = True
            elif action == "input-on":
                if input_on or not fresh_setup:
                    raise LoadError("fresh_fixed_setup_required",
                                    "Each Input ON requires a fresh Fixed setup with Input OFF.")
                input_on = contains_on = True
                fresh_setup = False
            elif action == "input-off":
                input_on = False
            if input_on and index == len(steps):
                raise LoadError("loop_requires_input_off", "Each loop must finish with Input OFF.")
        except LoadError as exc:
            raise LoadError("sequence_precheck_failed", str(exc), **location,
                            reason=exc.code, **exc.details) from exc
        prepared.append((action, settings))
    return loops, prepared, contains_on


def _check_stop(stop):
    if stop.is_set():
        raise LoadError("stopping", "Sequence was stopped.", 3)


class _Execution:
    def __init__(self, loops, live, stop):
        self.live = live
        self.stop = stop
        self.result = {"loop_count": loops, "completed_loops": 0, "completed_steps": 0,
                       "measurements": [], "input_states": [], "input_state": "UNKNOWN",
                       "cleanup": {"required": False, "attempted": False, "off_confirmed": False}}
        self.step = None
        self.owns_input = False
        self.remote_attempted = False
        self.explicit_off_attempted = False
        self.explicit_off_needs_loop_check = False

    def _remote_attempt(self):
        self.remote_attempted = True
        self.result["input_state"] = "UNKNOWN"

    def _input_off_observed(self):
        self.result["input_state"] = "OFF"

    def _input_on_attempt(self):
        self.remote_attempted = False
        self.owns_input = True
        self.result["input_state"] = "UNKNOWN"
        self.result["cleanup"]["off_confirmed"] = False

    def _run(self, session, steps):
        _check_stop(self.stop)
        require_trusted_session(session)
        parse_identity(control._query(session, "*IDN?"))
        self.result["input_state"] = read_measurement(session, "input-state")["input_state"]
        if self.result["input_state"] != "OFF":
            raise LoadError("input_not_off", "Sequence requires confirmed Input OFF before the first step.")

        for loop in range(1, self.result["loop_count"] + 1):
            setup = None
            for index, (action, arguments) in enumerate(steps, 1):
                self.step = {"loop_index": loop, "step_index": index, "action": action}
                _check_stop(self.stop)
                if action in SETPOINT_FIELDS:
                    sequence_fixed_setup(session, action, arguments, self.stop,
                                         on_remote_attempt=self._remote_attempt,
                                         on_input_off=self._input_off_observed)
                    self.remote_attempted = False
                    setup = action, arguments
                elif action == "input-on":
                    sequence_input_on(session, setup, self.stop, on_attempt=self._input_on_attempt,
                                      on_remote_attempt=self._remote_attempt,
                                      on_input_off=self._input_off_observed)
                    self.result["input_state"] = "ON"
                elif action == "input-off":
                    self.explicit_off_attempted = True
                    control._turn_input_off(session)
                    if self.owns_input:
                        self.explicit_off_needs_loop_check = True
                    self.explicit_off_attempted = self.owns_input = False
                    self.result["input_state"] = "OFF"
                elif action == "wait":
                    if self.stop.wait(float(arguments["seconds"])):
                        _check_stop(self.stop)
                else:
                    reading = read_measurement(session, action)
                    if action == "input-state":
                        self.result["input_state"] = reading["input_state"]
                    key = "measurements" if action == "measure" else "input_states"
                    self.result[key].append({**self.step, **reading})
                self.result["completed_steps"] += 1
            self.step = None
            _check_stop(self.stop)
            self.result["input_state"] = read_measurement(session, "input-state")["input_state"]
            if self.result["input_state"] != "OFF":
                raise LoadError("input_not_off", "Sequence loop did not finish with Input OFF.", 3,
                                loop_index=loop)
            self.result["cleanup"]["off_confirmed"] = True
            self.explicit_off_needs_loop_check = False
            self.result["completed_loops"] += 1
        _check_stop(self.stop)

    def _cleanup(self, session, error):
        cleanup = self.result["cleanup"]
        cleanup["required"] = self.owns_input
        interrupted_io = (self.live and error.code == "interrupted"
                          and (self.step is None or self.step["action"] != "wait"))
        untrusted = error.code in UNTRUSTED_ERRORS or interrupted_io
        if untrusted or self.explicit_off_attempted:
            self.result["input_state"] = "UNKNOWN"
            if (self.owns_input or self.explicit_off_attempted or self.remote_attempted
                    or (self.live and self.explicit_off_needs_loop_check)):
                cleanup["reason"] = ("session_untrusted" if untrusted
                                     else "explicit_off_unconfirmed")
        elif self.owns_input:
            cleanup["attempted"] = True
            try:
                control._turn_input_off(session)
            except (LoadError, KeyboardInterrupt) as exc:
                self.result["input_state"] = "UNKNOWN"
                cleanup["error"] = (exc.as_dict() if isinstance(exc, LoadError)
                                    else {"code": "interrupted", "message": "OFF cleanup was interrupted."})
            else:
                self.result["input_state"] = "OFF"
        elif error.code in {"input_not_off", "already_on"}:
            self.result["input_state"] = "ON"
        if (self.result["input_state"] == "ON"
                and (self.remote_attempted or self.explicit_off_needs_loop_check)):
            cleanup["reason"] = "input_not_off"
        elif self.remote_attempted and self.result["input_state"] == "UNKNOWN":
            cleanup.setdefault("reason", "remote_input_unconfirmed")
        cleanup["off_confirmed"] = self.result["input_state"] == "OFF"

    def failure(self, error):
        cleanup = self.result["cleanup"]
        risk = not cleanup["off_confirmed"] and (
            self.owns_input or self.explicit_off_attempted or "reason" in cleanup)
        progress = {**self.result, "failed_step": self.step, "original_error": error.as_dict()}
        if risk:
            state = "Input is ON." if self.result["input_state"] == "ON" else "Input OFF is unconfirmed."
            return LoadError("sequence_cleanup_unconfirmed",
                             f"{state} Independently switch off the load now.",
                             3, **progress)
        return LoadError(error.code, str(error), error.exit_code, **progress)

    def run(self, session, steps):
        try:
            self._run(session, steps)
        except (Exception, KeyboardInterrupt) as exc:
            if isinstance(exc, LoadError):
                error = exc
            elif isinstance(exc, KeyboardInterrupt):
                error = LoadError("interrupted", "Sequence was interrupted.", 3)
            else:
                error = LoadError("execution_failed", "Sequence execution failed.", 3,
                                  exception_type=type(exc).__name__)
            self._cleanup(session, error)
            raise self.failure(error) from exc
        return self.result


def execute(document, *, mode="live", model_id=MODEL_ID, backend="system", resource=None,
            confirmed=False, simulator=None, stop=None, hardware_validation=None):
    loops, steps, contains_on = validate_document(document)
    if mode not in {"dry-run", "simulate", "live"} or type(confirmed) is not bool:
        raise LoadError("argument_error", "Invalid Sequence mode or Input ON confirmation.")
    if model_id != MODEL_ID:
        raise LoadError("unsupported_model", "The requested model profile is not registered.")
    if hardware_validation is not None and (hardware_validation != COMMAND or mode == "simulate"):
        raise LoadError("argument_error", "Sequence hardware validation requires live or dry-run.")
    if mode == "live" and hardware_validation is None:
        raise LoadError("unsupported_live_scope", "Product LIVE is not open for Sequence.")
    validate_connection(resource, backend, required=mode == "live")
    marker = {"validation_only": True, "validation_scope": COMMAND} if hardware_validation else {}
    if hardware_validation:
        validate_sequence_trial(loops, steps)
    if mode == "dry-run":
        return {**marker, "version": 1, "loop_count": loops, "expanded_step_count": loops * len(steps),
                "hardware_access": False, "input_on_requires_confirmation": contains_on,
                "steps": [{"action": action, "arguments": {
                    key: str(value) if isinstance(value, Decimal) else value
                    for key, value in arguments.items()}} for action, arguments in steps]}
    if contains_on and not confirmed:
        raise LoadError("confirmation_required", "Sequence Input ON requires explicit run confirmation.")

    execution = _Execution(loops, mode == "live", stop if stop is not None else Event())
    if mode == "simulate":
        return execution.run(simulator if simulator is not None else ControlSimulator(), steps)
    _check_stop(execution.stop)
    result = None
    try:
        with open_session(resource) as (session, runtime):
            result = execution.run(session, steps)
    except LoadError as exc:
        if result is not None:
            raise execution.failure(exc) from exc
        raise
    return {**result, **marker, "visa": runtime}
