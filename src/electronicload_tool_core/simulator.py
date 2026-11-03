"""Deterministic responses for the single registered profile; no VISA access."""


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
    """Only the minimum mutable state required for fixed-CC command simulation."""

    def __init__(self):
        self.input_on = False
        self.function = "CURR"
        self.mode = "FIX"
        self.range_a = 60.0
        self.current_a = 0.0

    def query(self, command: str) -> str:
        return {
            "*IDN?": "ITECH Ltd,IT8812C,SIMULATED,1.0",
            "INP:STAT?": "1" if self.input_on else "0",
            "FUNC?": self.function,
            "FUNC:MODE?": self.mode,
            "CURR:RANG?": str(self.range_a),
            "CURR?": str(self.current_a),
            "TRAN?": "0",
            "INP:SHOR:STAT?": "0",
            "MEAS:VOLT?": "12.0",
            "MEAS:CURR?": str(self.current_a if self.input_on else 0),
            "FETC:POW?": str(12.0 * self.current_a if self.input_on else 0),
        }[command]

    def write(self, command: str) -> None:
        if command == "FUNC:MODE FIX":
            self.mode = "FIX"
        elif command == "FUNC CURR":
            self.function = "CURR"
        elif command.startswith("CURR:RANG "):
            self.range_a = 6.0 if command.endswith("MIN") else 60.0
            self.current_a = min(self.current_a, self.range_a)
        elif command.startswith("CURR "):
            self.current_a = float(command.split(" ", 1)[1])
        elif command in {"INP ON", "INP OFF"}:
            self.input_on = command.endswith("ON")
        else:
            raise ValueError("Unknown simulator command")
