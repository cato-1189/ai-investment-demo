"""Validación mínima de contratos JSON para la DEMO.

Implementa solo el subconjunto de JSON Schema usado por `schemas/*.json` para evitar
agregar dependencias en Fase 2.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SchemaValidationError(ValueError):
    """Error claro cuando un output no respeta su contrato."""


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def validate_schema(value: Any, schema: dict[str, Any], path: str = "$", errors: list[str] | None = None) -> list[str]:
    """Devuelve una lista de errores para el subconjunto de schema soportado."""
    if errors is None:
        errors = []

    expected_type = schema.get("type")
    if expected_type is not None:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_type_matches(value, item) for item in types):
            errors.append(f"{path}: tipo inválido; esperado {types}, recibido {type(value).__name__}")
            return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: valor {value!r} no está en enum {schema['enum']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} menor que mínimo {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} mayor que máximo {schema['maximum']}")

    if isinstance(value, str) and "minLength" in schema and len(value) < schema["minLength"]:
        errors.append(f"{path}: string más corto que minLength {schema['minLength']}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key}: campo requerido ausente")
        properties = schema.get("properties", {})
        for key, child in properties.items():
            if key in value:
                validate_schema(value[key], child, f"{path}.{key}", errors)
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                errors.append(f"{path}: campos no declarados {extra!r}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: lista con menos items que minItems {schema['minItems']}")
        item_schema = schema.get("items")
        if item_schema:
            for idx, item in enumerate(value):
                validate_schema(item, item_schema, f"{path}[{idx}]", errors)

    return errors


def load_schema(schema_path: Path) -> dict[str, Any]:
    return json.loads(schema_path.read_text(encoding="utf-8"))


def assert_valid(value: Any, schema: dict[str, Any], name: str) -> None:
    errors = validate_schema(value, schema)
    if errors:
        raise SchemaValidationError(f"Output inválido para {name}: " + "; ".join(errors))
