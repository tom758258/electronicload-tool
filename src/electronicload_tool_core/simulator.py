"""Deterministic responses for the single registered profile; no VISA access."""

from collections import deque
from decimal import Decimal

from .trigger import DEFAULT_INTERVAL
from .visa import TIMEOUT_MS

# Bit 15 of the Questionable Condition register indicates TRACE Buffer Full.
TRACE_BUFFER_FULL = 1 << 15
NATIVE_MEASUREMENTS = {
    "MEAS:VOLT:MIN?": "11.9", "MEAS:VOLT:MAX?": "12.1",
    "MEAS:CURR:MIN?": "0.4", "MEAS:CURR:MAX?": "0.6",
    "MEAS:CAP?": "0.25", "MEAS:TIME?": "1800",
}


class Simulator:
    def __init__(self):
        self.error_queue: deque[tuple[int, str]] = deque()

    def query(self, command: str) -> str:
        if command == "SYST:ERR?":
            code, message = self.error_queue.popleft() if self.error_queue else (0, "No error")
            quoted = message.replace('"', '""')
            return f'{code},"{quoted}"'
        return {
            "*IDN?": "ITECH Ltd,IT8812C,SIMULATED,1.0",
            "INP:STAT?": "1",
            "MEAS:VOLT?": "12.0",
            "MEAS:CURR?": "0.5",
            "FETC:POW?": "6.0",
            "SYST:VERS?": "1999.0",
            "DISP:MODE?": "NORMAL",
            **NATIVE_MEASUREMENTS,
        }[command]


class ControlSimulator:
    """Minimal control state for Fixed modes, offline Transient and LIST CC."""

    def __init__(self):
        self.input_on = False
        self.function = "CURR"
        self.mode = "FIX"
        self.range_a = 60.0
        self.current_a = 0.0
        self.cv_range = 'high'
        self.voltage_v = 0.1
        self.cr_range = "high"
        self.resistance_ohm = Decimal("10")
        self.cr_led_enabled = False
        self.cr_vdrop_v = Decimal("0")
        self.current_slow_rise_enabled = False
        self.power_w = Decimal("0")
        self.remote = False
        self.fixed_cc_slew = {}
        self.von_settings = (Decimal("0"), "on")
        self.short_on = False
        self.input_timer_seconds = 10
        self.input_timer_enabled = False
        self.average_count = 2
        self.transient_configuration = None
        self.transient_function = None
        self.transient_enabled = False
        self.last_trigger = None
        # Existing simulated Transient/LIST workflows use software triggering by default.
        self.trigger_source = "HOLD"
        self.trigger_interval = DEFAULT_INTERVAL
        self.list_configuration = None
        self.list_slot = None
        self.list_slots = {}
        self.list_last_trigger = None
        self.timeout = TIMEOUT_MS
        self.trace_source = "TWO"
        self.trace_points = 4
        self.trace_interval = "1"
        self.trace_delay = "0"
        self.trace_filter = "0"
        self.trace_feed = "NEVER"
        self.trace_capturing = False
        self.trace_data = ""
        self.protection_settings = {}
        self.protection_condition = 0
        self.protection_events = 0

    def list_settings(self) -> dict:
        return list_settings(self.list_configuration)

    def list_status(self) -> dict:
        configuration = self.list_configuration
        return {"state_source": "simulate", "configured": configuration is not None,
                "selected": self.mode == "LIST" and self.function == "CURR",
                "function_mode": self.mode,
                "range": configuration["range"] if configuration else None,
                "slot": self.list_slot,
                "configuration": self.list_settings() if configuration else None,
                "last_trigger": dict(self.list_last_trigger) if self.list_last_trigger else None}

    def transient_status(self) -> dict:
        configuration = self.transient_configuration
        return {"configured": configuration is not None,
                "function": self.transient_function if configuration else None,
                "mode": configuration["transient_mode"] if configuration else None,
                "range": configuration.get("range") if configuration else None,
                "enabled": self.transient_enabled,
                "last_trigger": dict(self.last_trigger) if self.last_trigger else None,
                "configuration": self.transient_settings() if configuration else None}

    def transient_settings(self) -> dict:
        return transient_settings(self.transient_function, self.transient_configuration)

    def query(self, command: str) -> str:
        if command == "TRAC:FEED:CONT?" and self.trace_capturing:
            voltage, current = "12.0", str(self.current_a if self.input_on else 0)
            sample = {"VOLT": (voltage,), "CURR": (current,), "TWO": (voltage, current)}
            values = sample[self.trace_source]
            self.trace_data = ",".join([" ".join(values)] * (self.trace_points // len(values)))
            self.trace_feed = "NEVER"
            self.trace_capturing = False
        return {
            "*IDN?": "ITECH Ltd,IT8812C,SIMULATED,1.0",
            "INP:STAT?": "1" if self.input_on else "0",
            "FUNC?": self.function,
            "FUNC:MODE?": self.mode,
            "CURR:RANG?": str(self.range_a),
            "CURR?": str(self.current_a),
            "VOLT:RANG?": str(18.0 if self.cv_range == "low" else 120.0),
            "VOLT?": str(self.voltage_v),
            "VOLT:RANG:AUTO?": "0",
            "VOLT:ON?": str(self.von_settings[0]),
            "VOLT:LATC?": "1" if self.von_settings[1] == "on" else "0",
            "CURR:SLEW:POS?": self.fixed_cc_slew.get("rise_slew_a_per_us"),
            "CURR:SLEW:NEG?": self.fixed_cc_slew.get("fall_slew_a_per_us"),
            "RES:RANG?": str(10.0 if self.cr_range == "low" else 7500.0),
            "RES?": str(self.resistance_ohm),
            "RES:LED:STAT?": str(int(self.cr_led_enabled)),
            "RES:VDR?": str(self.cr_vdrop_v),
            "CURR:SLEW:STAT?": str(int(self.current_slow_rise_enabled)),
            "POW?": str(self.power_w),
            "TRAN?": "1" if self.transient_enabled else "0",
            "INP:SHOR:STAT?": "1" if self.short_on else "0",
            "MEAS:VOLT?": "12.0",
            "MEAS:CURR?": str(self.current_a if self.input_on else 0),
            "FETC:POW?": str(12.0 * self.current_a if self.input_on else 0),
            "SENS:AVER:COUN?": str(self.average_count),
            "INP:TIM?": "1" if self.input_timer_enabled else "0",
            "TRAC:FEED?": self.trace_source,
            "TRAC:POIN?": str(self.trace_points),
            "TRAC:TIM?": self.trace_interval,
            "TRAC:DEL?": self.trace_delay,
            "TRAC:FILT?": self.trace_filter,
            "TRAC:FEED:CONT?": self.trace_feed,
            "TRAC:DATA?": self.trace_data,
            "STAT:QUES:COND?": str(self.protection_condition
                                   | (TRACE_BUFFER_FULL if self.trace_data else 0)),
            "SYST:ERR?": '0,"No error"',
            **NATIVE_MEASUREMENTS,
        }[command]

    def configure_cv(self, voltage, range_name: str) -> None:
        """Offline model only; do not infer real IT8812C VOLT:RANG syntax."""
        if self.input_on:
            raise ValueError("Input must be OFF")
        self.remote = True
        self.mode = "FIX"
        self.function = "VOLT"
        self.cv_range = range_name
        self.voltage_v = float(voltage)

    def configure_cr(self, resistance: Decimal, range_name: str) -> None:
        """Offline state only; no physical RES/range command semantics implied."""
        if self.input_on:
            raise ValueError("Input must be OFF")
        self.remote = True
        self.mode = "FIX"
        self.function = "RES"
        self.cr_range = range_name
        self.resistance_ohm = resistance

    def configure_cw(self, power: Decimal) -> None:
        """Offline state only; no physical POW/range command semantics implied."""
        if self.input_on:
            raise ValueError("Input must be OFF")
        self.remote = True
        self.mode = "FIX"
        self.function = "POW"
        self.power_w = power

    def write(self, command: str) -> None:
        if command == "TRIG" or command.startswith("TRAC:"):
            self._write_trace(command)
            return
        if command == "SYST:REM":
            self.remote = True
            return
        if not self.remote:
            return
        if command == "FUNC:MODE FIX":
            self.mode = "FIX"
        elif command == "FUNC CURR":
            self.function = "CURR"
        elif command.startswith("CURR:RANG "):
            selector = command.split(" ", 1)[1]
            if selector == "MIN":
                self.range_a = 0.0
            elif selector == "MAX":
                self.range_a = 60.0
            else:
                requested = float(selector)
                if not 0 <= requested <= 60:
                    raise ValueError("Invalid simulated range selector")
                self.range_a = requested
            self.current_a = min(self.current_a, self.range_a)
        elif command.startswith("CURR "):
            value = float(command.split(" ", 1)[1])
            if value > self.range_a:
                # The physical IT8812C returned error 120, rather than
                # applying CURR 0.1 after CURR:RANG MIN (readback=0).
                raise ValueError("Parameter overflowed")
            self.current_a = value
        elif command in {"INP ON", "INP OFF"}:
            self.input_on = command.endswith("ON")
        elif command.startswith("RES:LED:STAT "):
            self.cr_led_enabled = command.endswith(" 1")
        elif command.startswith("RES:VDR "):
            self.cr_vdrop_v = Decimal(command.split(" ", 1)[1])
        elif command.startswith("CURR:SLEW:STAT "):
            self.current_slow_rise_enabled = command.endswith(" 1")
        elif command.startswith("SENS:AVER:COUN "):
            self.average_count = int(command.split(" ", 1)[1])
        else:
            raise ValueError("Unknown simulator command")

    def _write_trace(self, command: str) -> None:
        name, _, value = command.partition(" ")
        if name == "TRIG" and self.trace_feed == "NEXT":
            self.trace_capturing = True
        elif name == "TRAC:FEED:CONT":
            self.trace_feed = value
            self.trace_capturing = False
        elif name == "TRAC:CLE":
            self.trace_data = ""
        elif name == "TRAC:FEED":
            self.trace_source = value
        elif name == "TRAC:POIN":
            self.trace_points = int(value)
        elif name == "TRAC:TIM":
            self.trace_interval = value
        elif name == "TRAC:DEL":
            self.trace_delay = value
        elif name == "TRAC:FILT":
            self.trace_filter = value
        else:
            raise ValueError("Unknown or unarmed simulator TRACE command")


def list_settings(configuration) -> dict:
    # LIST width/slew have no documented decimal step; retain all accepted digits.
    return {"range": configuration["range"], "repeat_count": configuration["repeat_count"],
            "steps": [{"current_a": float(step["current_a"]),
                       "slew_a_per_us": str(step["slew_a_per_us"]),
                       "width_s": str(step["width_s"])} for step in configuration["steps"]]}


def transient_settings(function, configuration) -> dict:
    if function != "CC":
        unit = {"CV": "v", "CR": "ohm", "CW": "w"}[function]
        units = {"level_a": f"level_a_{unit}", "level_b": f"level_b_{unit}",
                 "width_a": "width_a_s", "width_b": "width_b_s", "pulse_width": "pulse_width_s"}
        # Widths and CR levels have no documented decimal step. Keep every digit.
        return {"range": configuration.get("range"),
                **{units.get(key, key): (str(value) if "width" in key or unit == "ohm"
                                        else float(value)) if isinstance(value, Decimal) else value
                   for key, value in configuration.items()}}
    units = {"level_a": "level_a_a", "level_b": "level_b_a", "width_a": "width_a_s",
             "width_b": "width_b_s", "pulse_width": "pulse_width_s",
             "rise_slew": "rise_slew_a_per_us", "fall_slew": "fall_slew_a_per_us"}
    # Slew has no documented decimal step: preserve all accepted digits in JSON.
    return {units.get(key, key): (str(value) if "slew" in key else float(value))
            if isinstance(value, Decimal) else value
            for key, value in configuration.items()}
