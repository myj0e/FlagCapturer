"""Validate the small JSON-schema subset used by controller-owned tools."""
import math
import re


def validate(value, schema, depth=0):
    if depth > 12:
        raise ValueError("tool arguments exceed nesting limit")
    kind = schema.get("type")
    valid = {"string": isinstance(value, str), "integer": isinstance(value, int) and not isinstance(value, bool),
             "number": isinstance(value, (int, float)) and not isinstance(value, bool),
             "array": isinstance(value, list), "object": isinstance(value, dict), "boolean": isinstance(value, bool)}
    if kind in valid and not valid[kind]:
        raise ValueError(f"expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("value is outside the tool's supported choices")
    if kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 16384):
            raise ValueError("string exceeds tool bounds")
        if "\x00" in value:
            raise ValueError("NUL bytes are not accepted in tool strings")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ValueError("string does not match the required tool format")
    elif kind in {"number", "integer"}:
        if (isinstance(value, float) and not math.isfinite(value)) or value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise ValueError("number exceeds tool bounds")
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 1000):
            raise ValueError("array exceeds tool bounds")
        for item in value:
            validate(item, schema.get("items", {}), depth+1)
    elif kind == "object":
        properties = schema.get("properties", {})
        if len(value) > schema.get("maxProperties", 128) or any(key not in value for key in schema.get("required", [])):
            raise ValueError("object is missing required fields or exceeds tool bounds")
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError("object has unsupported fields")
        for key, item in value.items():
            validate(item, properties.get(key, {}), depth+1)
