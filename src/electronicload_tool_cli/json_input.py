"""Strict decimal-preserving JSON decoding for complete LIST requests."""

from decimal import Decimal, InvalidOperation
import json


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field.")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("Nonfinite JSON number.")


def decode_list_json(text):
    try:
        return json.loads(text, parse_float=Decimal, parse_constant=_reject_constant,
                          object_pairs_hook=_unique_fields)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal JSON number.") from exc
