"""Validate saved project JSON before display, scoring or an external action."""
import json
import math


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Project numbers must be finite')
    return number


def _invalid_constant(value):
    raise ValueError('Project JSON contains an invalid number')


def load_project_json(value):
    data = json.loads(value, parse_float=_finite_float, parse_constant=_invalid_constant)
    if not isinstance(data, dict):
        raise ValueError('Project must be an object')
    # Decoders and response encoders can have different recursion limits.
    # Bound depth explicitly before scoring or returning any nested metadata.
    pending = [(data, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 64:
            raise ValueError('Project data is too deeply nested')
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
            pending.extend((key, depth + 1) for key in item)
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            item.encode('utf-8')
    return data
