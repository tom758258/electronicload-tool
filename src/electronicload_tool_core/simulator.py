"""Deterministic responses for the single registered profile; no VISA access."""

from decimal import Decimal


class Simulator:
    def query(self, command: str) -> str:
        return {
            "*IDN?": "ITECH Ltd,IT8812C,SIMULATED,1.0",
            "INP:STAT?": "1",
            "MEAS:VOLT?": "12.0",
            "MEAS:CURR?": "0.5",
            "FETC:POW?": "6.0",
        }[command]


class ControlSimulator:
    """Minimal state for fixed CC and offline-only fixed CV/CR simulation."""

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
        self.remote = False

    def query(self, command: str) -> str:
        return {
            "*IDN?": "ITECH Ltd,IT8812C,SIMULATED,1.0",
            "INP:STAT?": "1" if self.input_on else "0",
            "FUNC?": self.function,
            "FUNC:MODE?": self.mode,
            "CURR:RANG?": str(self.range_a),
            "CURR?": str(self.current_a),
            "VOLT:RANG?": str(18.0 if self.cv_range == "low" else 120.0),
            "VOLT?": str(self.voltage_v),
            "RES:RANG?": str(10.0 if self.cr_range == "low" else 7500.0),
            "RES?": str(self.resistance_ohm),
            "TRAN?": "0",
            "INP:SHOR:STAT?": "0",
            "MEAS:VOLT?": "12.0",
            "MEAS:CURR?": str(self.current_a if self.input_on else 0),
            "FETC:POW?": str(12.0 * self.current_a if self.input_on else 0),
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

    def write(self, command: str) -> None:
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
        else:
            raise ValueError("Unknown simulator command")
