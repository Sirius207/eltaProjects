"""openapi.yaml 用到的 schema 規則的簡易驗證器（只給測試用，不新增依賴）。

支援：$ref、oneOf、allOf、nullable、type、enum、required、properties、items、
minimum／maximum（含 3.0 的布林 exclusiveMinimum）、multipleOf、pattern、minLength、minItems。
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Union

try:
    import yaml
except ImportError:  # pragma: no cover - 沒有 PyYAML 時測試會 skip
    yaml = None

OPENAPI_PATH = Path(__file__).resolve().parents[1] / "openapi.yaml"
REF_PREFIX = "#/components/schemas/"


def load_document() -> Dict[str, Any]:
    with OPENAPI_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    raise ValueError(f"不支援的 type：{expected}")


class SchemaChecker:
    def __init__(self, document: Dict[str, Any]):
        self.document = document
        self.schemas = document["components"]["schemas"]

    def resolve(self, ref: str) -> Dict[str, Any]:
        if not ref.startswith(REF_PREFIX) or ref[len(REF_PREFIX):] not in self.schemas:
            raise KeyError(ref)
        return self.schemas[ref[len(REF_PREFIX):]]

    def unresolved_refs(self) -> List[str]:
        problems: List[str] = []

        def walk(node: Any, path: str) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    targets = [value] if key == "$ref" else list(value.values()) if key == "mapping" else []
                    for target in targets:
                        try:
                            self.resolve(target)
                        except KeyError:
                            problems.append(f"{path}.{key}: {target}")
                    if key not in ("$ref", "mapping"):
                        walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{path}[{index}]")

        walk(self.document, "$")
        return problems

    def undeclared_required(self) -> List[str]:
        return [
            f"{name}.{field}"
            for name, schema in self.schemas.items()
            for field in schema.get("required", [])
            if field not in schema.get("properties", {})
        ]

    def check(self, value: Any, schema: Union[str, Dict[str, Any]], path: str = "$") -> List[str]:
        if isinstance(schema, str):
            schema = self.schemas[schema]
        if "$ref" in schema:
            return self.check(value, self.resolve(schema["$ref"]), path)
        if value is None:
            return [] if schema.get("nullable") else [f"{path}: 不可為 null"]
        errors: List[str] = []
        if "oneOf" in schema:
            matches = sum(1 for option in schema["oneOf"] if not self.check(value, option, path))
            if matches != 1:
                errors.append(f"{path}: 符合 {matches} 個 oneOf 分支（應為 1）")
        for option in schema.get("allOf", []):
            errors += self.check(value, option, path)
        expected = schema.get("type")
        if expected and not _type_ok(value, expected):
            return errors + [f"{path}: 應為 {expected}，實際為 {type(value).__name__}"]
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: {value!r} 不在 enum 中")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema:
                if schema.get("exclusiveMinimum") is True and not value > schema["minimum"]:
                    errors.append(f"{path}: {value} 必須 > {schema['minimum']}")
                elif value < schema["minimum"]:
                    errors.append(f"{path}: {value} 必須 ≥ {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                errors.append(f"{path}: {value} 必須 ≤ {schema['maximum']}")
            if "multipleOf" in schema and value % schema["multipleOf"]:
                errors.append(f"{path}: {value} 不是 {schema['multipleOf']} 的倍數")
        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                errors.append(f"{path}: 長度小於 {schema['minLength']}")
            if "pattern" in schema and not re.search(schema["pattern"], value):
                errors.append(f"{path}: {value!r} 不符合 {schema['pattern']}")
        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                errors.append(f"{path}: 項目少於 {schema['minItems']}")
            for index, item in enumerate(value):
                errors += self.check(item, schema.get("items", {}), f"{path}[{index}]")
        if isinstance(value, dict):
            for field in schema.get("required", []):
                if field not in value:
                    errors.append(f"{path}: 缺少必填欄位 {field}")
            for field, field_schema in schema.get("properties", {}).items():
                if field in value:
                    errors += self.check(value[field], field_schema, f"{path}.{field}")
        return errors
