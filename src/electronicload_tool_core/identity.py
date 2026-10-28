"""Exact identity matching for the registered electronic load profile."""

from dataclasses import dataclass, field

from .errors import LoadError

MODEL_ID = "itech-it8812c"
MODEL_TOKEN = "IT8812C"


@dataclass(frozen=True)
class Identity:
    manufacturer: str
    model: str
    serial: str = field(repr=False)
    firmware: str
    raw: str = field(repr=False)
    model_id: str = MODEL_ID
    canonical_model: str = MODEL_TOKEN

    def public_result(self) -> dict:
        return {
            "manufacturer": self.manufacturer,
            "model": self.model,
            "canonical_model": self.canonical_model,
            "firmware": self.firmware,
        }


def parse_identity(raw: str) -> Identity:
    fields = [value.strip() for value in raw.strip().split(",")]
    if len(fields) != 4 or not all(fields):
        raise LoadError("invalid_identity", "Identification requires four nonempty fields.")
    manufacturer, model, serial, firmware = fields
    # Manufacturer spelling comes from the IT8800 Programming Guide IDN example.
    if manufacturer.casefold() != "itech ltd" or model.upper() != MODEL_TOKEN:
        raise LoadError("identity_mismatch", "Detected identity does not match the IT8812C profile.")
    return Identity(manufacturer, model, serial, firmware, raw)
