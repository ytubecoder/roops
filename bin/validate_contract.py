#!/usr/bin/env python3
"""bin/validate_contract.py — §9.2 stdlib-only contract validator.

No `jsonschema` dependency. Covers exactly the JSON-Schema subset used by
contract/contract.schema.json: `type`, `required`, `properties`, `enum`,
`additionalProperties:false`, `minLength`, `maxLength`, `const`, and the
integer/number/string/object/array/boolean primitive types.

Plus the §9.1 rule: `metrics` must be a string that parses as a JSON
object (checked structurally here, not expressible in the strict-OpenAI
JSON-Schema subset itself).

    validate_contract.py --schema S --file F [--expect-run-id ID]

Exit 0 valid, 1 invalid (reasons to stderr, one per line), 2 usage error.
"""
import argparse
import json
import sys

_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


def _type_ok(value, type_name):
    if type_name == "integer":
        # bool is a subclass of int in Python — exclude it explicitly.
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    expected = _TYPE_MAP.get(type_name)
    if expected is None:
        return True
    return isinstance(value, expected)


def _validate(instance, schema, path, errors):
    if "const" in schema:
        if instance != schema["const"]:
            errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")
            return

    if "type" in schema:
        if not _type_ok(instance, schema["type"]):
            errors.append(f"{path}: expected type {schema['type']}, got {type(instance).__name__}")
            return

    if "enum" in schema:
        if instance not in schema["enum"]:
            errors.append(f"{path}: {instance!r} not in enum {schema['enum']}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: length {len(instance)} < minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: length {len(instance)} > maxLength {schema['maxLength']}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")

        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in properties:
                    errors.append(f"{path}: additional property {key!r} not allowed")

        for key, subschema in properties.items():
            if key in instance:
                _validate(instance[key], subschema, f"{path}.{key}", errors)

    if isinstance(instance, list):
        item_schema = schema.get("items")
        if item_schema is not None:
            for i, item in enumerate(instance):
                _validate(item, item_schema, f"{path}[{i}]", errors)


def validate_instance(instance, schema):
    errors = []
    _validate(instance, schema, "$", errors)
    return errors


def validate_metrics_field(instance) -> list:
    """§9.1: `metrics` must be a JSON string that parses as a JSON object."""
    errors = []
    if not isinstance(instance, dict) or "metrics" not in instance:
        return errors  # covered by schema-level required/type checks
    metrics = instance["metrics"]
    if not isinstance(metrics, str):
        return errors  # schema-level type check already flags this
    try:
        parsed = json.loads(metrics)
    except (ValueError, json.JSONDecodeError):
        errors.append("$.metrics: string does not parse as JSON")
        return errors
    if not isinstance(parsed, dict):
        errors.append("$.metrics: parsed JSON is not an object")
    return errors


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="validate_contract.py")
    p.add_argument("--schema", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--expect-run-id", default=None)
    args = p.parse_args(argv)

    try:
        with open(args.schema, "r") as f:
            schema = json.load(f)
    except OSError as e:
        print(f"cannot read schema {args.schema}: {e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"schema {args.schema} is not valid JSON: {e}", file=sys.stderr)
        return 2

    try:
        with open(args.file, "r") as f:
            raw = f.read()
    except OSError as e:
        print(f"cannot read file {args.file}: {e}", file=sys.stderr)
        return 2

    try:
        instance = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"$: file is not valid JSON: {e}", file=sys.stderr)
        return 1

    errors = validate_instance(instance, schema)
    errors += validate_metrics_field(instance)

    if args.expect_run_id is not None:
        run_id = instance.get("run_id") if isinstance(instance, dict) else None
        if run_id != args.expect_run_id:
            errors.append(
                f"$.run_id: expected {args.expect_run_id!r}, got {run_id!r}"
            )

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
