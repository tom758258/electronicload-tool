"""A single operator-supplied current ceiling for IT8812C load operations."""

from dataclasses import dataclass
import math
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from .errors import LoadError


@dataclass(frozen=True)
class SafetyLimits:
    max_current: float


def load_safety_config(path: str | None) -> SafetyLimits:
    if not path:
        raise LoadError("missing_safety_config", "LIVE load control requires --safety-config.")
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise LoadError("invalid_safety_config", "Cannot read or parse the safety config.",
                        exception_type=type(exc).__name__) from exc
    if not isinstance(data, dict) or set(data) != {"safety"}:
        raise LoadError("invalid_safety_config", "Safety config requires only [safety].")
    values = data["safety"]
    if not isinstance(values, dict) or set(values) != {"max_current"}:
        raise LoadError("invalid_safety_config", "Safety config requires only max_current.")
    value = values["max_current"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LoadError("invalid_safety_config", "max_current must be numeric.")
    if not math.isfinite(value) or not (0 < value <= 60):
        raise LoadError("invalid_safety_config", "max_current must be above 0 and at most 60 A.")
    return SafetyLimits(float(value))
