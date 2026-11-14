"""schema / 数据文件清单的解析、校验与样例加载。"""
from __future__ import annotations

import json
from pathlib import Path

from . import settings
from .types import TypeParseError, normalize_type


class SchemaError(ValueError):
    """输入不合法；路由层统一转 400。"""


def _load_json(value, label: str):
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise SchemaError(f"{label} 为空，请粘贴 JSON 或选择样例")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise SchemaError(f"{label} 不是合法 JSON：第 {exc.lineno} 行第 {exc.colno} 列 {exc.msg}")
    return value


def parse_schema(value, label: str = "schema") -> dict:
    doc = _load_json(value, label)
    if not isinstance(doc, dict):
        raise SchemaError(f"{label} 必须是 JSON 对象")
    table = str(doc.get("table") or "").strip()
    if not table:
        raise SchemaError(f"{label} 缺少 table 名称")
    raw_fields = doc.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise SchemaError(f"{label} 的 fields 必须是非空数组")
    fields: list[dict] = []
    seen_names: set[str] = set()
    seen_ids: set[int] = set()
    for index, raw in enumerate(raw_fields, start=1):
        if not isinstance(raw, dict):
            raise SchemaError(f"{label} 第 {index} 个字段必须是 JSON 对象")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise SchemaError(f"{label} 第 {index} 个字段缺少 name")
        if name in seen_names:
            raise SchemaError(f"{label} 字段名重复：{name}")
        seen_names.add(name)
        try:
            ftype = normalize_type(str(raw.get("type") or ""))
        except TypeParseError as exc:
            raise SchemaError(f"{label} 字段 {name} 类型不合法：{exc}")
        fid = raw.get("id")
        if fid is not None:
            try:
                fid = int(fid)
            except (TypeError, ValueError):
                raise SchemaError(f"{label} 字段 {name} 的 id 必须是整数")
            if fid in seen_ids:
                raise SchemaError(f"{label} field_id 重复：{fid}")
            seen_ids.add(fid)
        fields.append(
            {
                "id": fid,
                "name": name,
                "type": ftype,
                "nullable": bool(raw.get("nullable", True)),
                "default": raw.get("default"),
                "partition": bool(raw.get("partition", False)),
            }
        )
    partitions = [f["name"] for f in fields if f["partition"]]
    return {"table": table, "fields": fields, "partitions": partitions}


def parse_consumers(value, label: str = "consumers") -> dict:
    doc = _load_json(value, label)
    if not isinstance(doc, dict):
        raise SchemaError(f"{label} 必须是 JSON 对象")
    raw_files = doc.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise SchemaError(f"{label} 的 files 必须是非空数组")
    files: list[dict] = []
    for index, raw in enumerate(raw_files, start=1):
        if not isinstance(raw, dict):
            raise SchemaError(f"{label} 第 {index} 个文件必须是 JSON 对象")
        path = str(raw.get("path") or "").strip()
        if not path:
            raise SchemaError(f"{label} 第 {index} 个文件缺少 path")
        columns = raw.get("columns")
        if not isinstance(columns, list) or not columns:
            raise SchemaError(f"{label} 文件 {path} 的 columns 必须是非空数组")
        cleaned = [str(c).strip() for c in columns if str(c).strip()]
        if not cleaned:
            raise SchemaError(f"{label} 文件 {path} 的 columns 不能全为空")
        files.append({"path": path, "columns": cleaned})
    return {"table": (doc.get("table") or "").strip() or None, "files": files}


def load_sample_index() -> list[dict]:
    index_path = settings.SAMPLE_DIR / "index.json"
    if not index_path.exists():
        raise SchemaError(f"样例索引缺失：{index_path}")
    doc = json.loads(index_path.read_text(encoding="utf-8"))
    return doc.get("samples", [])


def load_sample(key: str) -> dict:
    key = (key or "").strip().upper()
    entry = None
    for item in load_sample_index():
        if item.get("key") == key:
            entry = item
            break
    if entry is None:
        raise SchemaError(f"未知样例 {key!r}，可选：A / B / C")

    def read(rel: str) -> str:
        path = settings.SAMPLE_DIR / rel
        if not path.exists():
            raise SchemaError(f"样例文件缺失：{rel}")
        return path.read_text(encoding="utf-8")

    return {
        "key": key,
        "label": entry.get("label", key),
        "description": entry.get("description", ""),
        "expected": entry.get("expected", ""),
        "old": parse_schema(read(entry["old"]), f"样例 {key} 旧 schema"),
        "new": parse_schema(read(entry["new"]), f"样例 {key} 新 schema"),
        "consumers": parse_consumers(read(entry["consumers"]), f"样例 {key} 数据文件清单"),
        "raw_old": read(entry["old"]),
        "raw_new": read(entry["new"]),
        "raw_consumers": read(entry["consumers"]),
    }
