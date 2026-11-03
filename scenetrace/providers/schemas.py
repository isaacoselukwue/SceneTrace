from __future__ import annotations

import copy
import functools
import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

M = TypeVar("M", bound=BaseModel)

KEPT_KEYWORDS = frozenset({
    "type", "properties", "required", "additionalProperties", "items", "enum", "anyOf", "description",
    "pattern", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minItems", "maxItems",
})
DROPPED_KEYWORDS = frozenset({
    "title", "default", "examples", "minLength", "maxLength", "uniqueItems", "format", "readOnly", "writeOnly",
    "deprecated", "$schema", "$id", "$comment", "contentEncoding", "contentMediaType", "discriminator",
})
UNSUPPORTED_KEYWORDS = frozenset({
    "not", "if", "then", "else", "prefixItems", "patternProperties", "propertyNames", "dependentRequired",
    "dependentSchemas", "unevaluatedProperties", "unevaluatedItems", "contains", "minContains", "maxContains",
    "minProperties", "maxProperties", "additionalItems", "$dynamicRef", "$anchor",
})
SCALAR_TYPES = frozenset({"string", "integer", "number", "boolean", "null"})
SCALAR_KEYWORDS = frozenset({"type", "enum", "description", "pattern", "minimum", "maximum", "exclusiveMinimum",
                             "exclusiveMaximum", "multipleOf"})
FORMAT_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SchemaSubsetError(ValueError):
    pass


class StructuredOutputError(ValueError):
    def __init__(self, code: str, *, provider_call_id: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.provider_call_id = provider_call_id


def strict_subset(schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(schema, dict):
        raise SchemaSubsetError("a schema must be a JSON object")
    defs = {**(schema.get("definitions") or {}), **(schema.get("$defs") or {})}
    root = _convert(schema, defs, (), at_root=True)
    if root.get("type") != "object":
        raise SchemaSubsetError("the root must be a plain object")
    return root


def _convert(node: Any, defs: dict[str, Any], stack: tuple[str, ...], *, at_root: bool = False) -> dict[str, Any]:
    if not isinstance(node, dict):
        raise SchemaSubsetError("schema nodes must be JSON objects")
    node = {k: v for k, v in node.items() if k not in ("$defs", "definitions")}
    if "$ref" in node:
        return _inline_ref(node, defs, stack, at_root=at_root)
    if "allOf" in node:
        members = node.pop("allOf")
        if not isinstance(members, list) or len(members) != 1:
            raise SchemaSubsetError("allOf with more than one member is not supported")
        return _convert({**members[0], **node}, defs, stack, at_root=at_root)
    if "oneOf" in node:
        if "anyOf" in node:
            raise SchemaSubsetError("oneOf beside anyOf is not supported")
        node["anyOf"] = node.pop("oneOf")
    unsupported = UNSUPPORTED_KEYWORDS & node.keys()
    if unsupported:
        raise SchemaSubsetError(f"unsupported keywords: {sorted(unsupported)}")
    if "const" in node:
        value = node.pop("const")
        if "enum" in node and node["enum"] != [value]:
            raise SchemaSubsetError("const conflicts with enum")
        node["enum"] = [value]
    if "anyOf" in node:
        return _convert_union(node, defs, stack, at_root=at_root)
    kind = node.get("type")
    if isinstance(kind, list):
        return _convert_type_list(node, defs, stack, at_root=at_root)
    if kind == "object" or "properties" in node:
        return _convert_object(node, defs, stack)
    if at_root:
        raise SchemaSubsetError("the root must be a plain object")
    if kind == "array":
        return _convert_array(node, defs, stack)
    if kind in SCALAR_TYPES or "enum" in node:
        return _convert_scalar(node)
    raise SchemaSubsetError("an unconstrained schema node is not supported")


def _inline_ref(node: dict[str, Any], defs: dict[str, Any], stack: tuple[str, ...], *, at_root: bool) -> dict[str, Any]:
    ref = node.pop("$ref")
    if not isinstance(ref, str) or not ref.startswith(("#/$defs/", "#/definitions/")):
        raise SchemaSubsetError(f"only local definition references are supported: {ref!r}")
    name = ref.split("/", 2)[2]
    if name in stack:
        raise SchemaSubsetError(f"recursive schema through {name}")
    if name not in defs:
        raise SchemaSubsetError(f"unresolved reference {ref}")
    siblings = {k: v for k, v in node.items() if k not in DROPPED_KEYWORDS}
    if set(siblings) - {"description"}:
        raise SchemaSubsetError(f"keywords beside $ref are not supported: {sorted(set(siblings) - {'description'})}")
    resolved = _convert(defs[name], defs, (*stack, name), at_root=at_root)
    if "description" in siblings:
        resolved["description"] = siblings["description"]
    return resolved


def _convert_union(node: dict[str, Any], defs: dict[str, Any], stack: tuple[str, ...], *, at_root: bool) -> dict[str, Any]:
    if at_root:
        raise SchemaSubsetError("the root must not be a union")
    members = node.pop("anyOf")
    if not isinstance(members, list) or not members:
        raise SchemaSubsetError("anyOf needs at least one member")
    leftover = node.keys() - DROPPED_KEYWORDS - {"description"}
    if leftover:
        raise SchemaSubsetError(f"keywords beside anyOf are not supported: {sorted(leftover)}")
    out: dict[str, Any] = {"anyOf": [_convert(m, defs, stack) for m in members]}
    if "description" in node:
        out["description"] = node["description"]
    return out


def _convert_type_list(node: dict[str, Any], defs: dict[str, Any], stack: tuple[str, ...], *, at_root: bool) -> dict[str, Any]:
    kinds = list(dict.fromkeys(node["type"]))
    others = [k for k in kinds if k != "null"]
    if at_root or len(others) != 1 or "enum" in node:
        raise SchemaSubsetError("only a single type or a single type with null is supported")
    base = _convert({**node, "type": others[0]}, defs, stack)
    return _nullable(base) if "null" in kinds else base


def _convert_object(node: dict[str, Any], defs: dict[str, Any], stack: tuple[str, ...]) -> dict[str, Any]:
    properties = node.get("properties") or {}
    if not isinstance(properties, dict):
        raise SchemaSubsetError("properties must be a JSON object")
    if isinstance(node.get("additionalProperties"), dict):
        raise SchemaSubsetError("open maps (additionalProperties with a schema) are not supported")
    required = set(node.get("required") or [])
    if required - properties.keys():
        raise SchemaSubsetError(f"required names without properties: {sorted(required - properties.keys())}")
    leftover = node.keys() - DROPPED_KEYWORDS - {"type", "properties", "required", "additionalProperties", "description"}
    if leftover:
        raise SchemaSubsetError(f"unsupported object keywords: {sorted(leftover)}")
    converted: dict[str, Any] = {}
    for name, sub in properties.items():
        value = _convert(sub, defs, stack)
        converted[name] = value if name in required else _nullable(value)
    out: dict[str, Any] = {"type": "object", "properties": converted, "required": list(converted),
                           "additionalProperties": False}
    if "description" in node:
        out["description"] = node["description"]
    return out


def _convert_array(node: dict[str, Any], defs: dict[str, Any], stack: tuple[str, ...]) -> dict[str, Any]:
    items = node.get("items")
    if not isinstance(items, dict):
        raise SchemaSubsetError("arrays need one items schema")
    leftover = node.keys() - DROPPED_KEYWORDS - {"type", "items", "minItems", "maxItems", "description"}
    if leftover:
        raise SchemaSubsetError(f"unsupported array keywords: {sorted(leftover)}")
    out: dict[str, Any] = {"type": "array", "items": _convert(items, defs, stack)}
    for key in ("minItems", "maxItems", "description"):
        if key in node:
            out[key] = node[key]
    return out


def _convert_scalar(node: dict[str, Any]) -> dict[str, Any]:
    leftover = node.keys() - DROPPED_KEYWORDS - SCALAR_KEYWORDS
    if leftover:
        raise SchemaSubsetError(f"unsupported scalar keywords: {sorted(leftover)}")
    out = {k: v for k, v in node.items() if k in SCALAR_KEYWORDS}
    if "type" not in out and out.get("enum") and all(isinstance(v, str) for v in out["enum"]):
        out["type"] = "string"
    return out


def _accepts_null(node: dict[str, Any]) -> bool:
    if node.get("type") == "null" or None in (node.get("enum") or []):
        return True
    return any(isinstance(m, dict) and _accepts_null(m) for m in node.get("anyOf") or [])


def _nullable(node: dict[str, Any]) -> dict[str, Any]:
    if _accepts_null(node):
        return node
    if "anyOf" in node:
        return {**node, "anyOf": [*node["anyOf"], {"type": "null"}]}
    return {"anyOf": [node, {"type": "null"}]}


def strict_violations(schema: Any) -> list[str]:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return ["$: the root is not a plain object"]
    problems: list[str] = []
    for key in ("anyOf", "oneOf", "allOf", "enum", "not"):
        if key in schema:
            problems.append(f"$: root {key}")
    _walk(schema, "$", problems)
    return problems


def _walk(node: Any, path: str, problems: list[str]) -> None:
    if not isinstance(node, dict):
        problems.append(f"{path}: not a schema object")
        return
    for key in node:
        if key not in KEPT_KEYWORDS:
            problems.append(f"{path}: keyword {key}")
    if node.get("type") == "object" or "properties" in node:
        properties = node.get("properties")
        if not isinstance(properties, dict):
            problems.append(f"{path}: object without properties")
            return
        if node.get("additionalProperties") is not False:
            problems.append(f"{path}: object is not closed")
        if sorted(node.get("required") or []) != sorted(properties):
            problems.append(f"{path}: not every property is required")
        for name, sub in properties.items():
            _walk(sub, f"{path}.{name}", problems)
    if node.get("type") == "array":
        _walk(node.get("items"), f"{path}[]", problems)
    for index, member in enumerate(node.get("anyOf") or []):
        _walk(member, f"{path}|{index}", problems)


@functools.cache
def _model_schema(model: type[BaseModel]) -> dict[str, Any]:
    return strict_subset(model.model_json_schema())


def model_schema(model: type[BaseModel]) -> dict[str, Any]:
    return copy.deepcopy(_model_schema(model))


def text_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    if not FORMAT_NAME.match(name):
        raise ValueError("format names use letters, digits, underscores and hyphens, up to 64 characters")
    problems = strict_violations(schema)
    if problems:
        raise SchemaSubsetError("; ".join(problems[:5]))
    return {"type": "json_schema", "name": name, "strict": True, "schema": schema}


class _DuplicateKey(ValueError):
    pass


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _DuplicateKey(key)
        out[key] = value
    return out


def _no_constants(value: str) -> Any:
    raise ValueError(f"non-finite number {value}")


def parse_strict(text: str, model: type[M]) -> M:
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constants)
    except _DuplicateKey as exc:
        raise StructuredOutputError("OUTPUT_DUPLICATE_KEY") from exc
    except (ValueError, RecursionError) as exc:
        raise StructuredOutputError("OUTPUT_NOT_JSON") from exc
    if not isinstance(data, dict):
        raise StructuredOutputError("OUTPUT_NOT_OBJECT")
    try:
        return model.model_validate_json(text, strict=True)
    except ValidationError as exc:
        raise StructuredOutputError("OUTPUT_SCHEMA_INVALID") from exc
