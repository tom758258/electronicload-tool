"""Deterministic responses for the single registered profile; no VISA access."""


class Simulator:
    def query(self, command: str) -> str:
        return {
            "*IDN?": "ITECH Ltd,IT8812C,SIMULATED,1.0",
            "INP:STAT?": "1",
            "MEAS:VOLT?": "12.0",
            "MEAS:CURR?": "0.5",
            "MEAS:POW?": "6.0",
        }[command]
