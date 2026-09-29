"""Encoding is used only for public inputs, source literals and explicit exports."""
import math

MAX_BYTES = 4 * 1024 * 1024
MAX_ITEMS = 100_000
MAX_DEPTH = 48


def encode(value, _depth=0, _seen=None):
    if _depth > MAX_DEPTH:
        raise ValueError("Public input nesting exceeds the supported limit")
    kind = type(value)
    if value is None:
        return ["null"]
    if kind is bool:
        return ["bool", value]
    if kind is int:
        if not -(2**63) <= value < 2**63:
            raise ValueError("Integers must fit in signed 64 bits")
        return ["int", str(value)]
    if kind is float:
        if not math.isfinite(value):
            raise ValueError("Only finite floating-point values are supported")
        return ["float", repr(value)]
    if kind is str:
        if len(value.encode("utf-8")) > MAX_BYTES:
            raise ValueError("Public input exceeds the byte limit")
        return ["str", value]
    if kind is bytes:
        if len(value) > MAX_BYTES:
            raise ValueError("Public input exceeds the byte limit")
        return ["bytes", value.hex()]
    if kind not in (list, dict):
        raise TypeError("Supported public inputs are scalars, bytes, lists and string-keyed dictionaries")
    if len(value) > MAX_ITEMS:
        raise ValueError("Public input exceeds the item limit")
    seen = set() if _seen is None else _seen
    if id(value) in seen:
        raise ValueError("Cyclic public inputs are unsupported")
    seen.add(id(value))
    try:
        if kind is list:
            return ["list", [encode(item, _depth + 1, seen) for item in value]]
        if any(type(key) is not str for key in value):
            raise TypeError("Dictionary keys must be strings")
        return ["dict", [[key, encode(item, _depth + 1, seen)] for key, item in value.items()]]
    finally:
        seen.remove(id(value))


def decode(value):
    kind = value[0]
    if kind == "null":
        return None
    if kind == "bool":
        return value[1]
    if kind == "int":
        return int(value[1])
    if kind == "float":
        return float(value[1])
    if kind == "str":
        return value[1]
    if kind == "bytes":
        return bytes.fromhex(value[1])
    if kind == "list":
        return [decode(item) for item in value[1]]
    if kind == "dict":
        return {key: decode(item) for key, item in value[1]}
    raise RuntimeError("Invalid native export encoding")
