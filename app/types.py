"""类型提升矩阵（纯函数，无 IO）。

判定口径：promotion(old, new) 回答「旧数据里的值能否被新类型无损读取」。
返回值 verdict ∈ compatible / blocked / unknown；显式表以外的类型对一律 unknown，不做猜测。
"""
from __future__ import annotations

import re

TYPE_ALIASES = {
    "int8": "int8", "tinyint": "int8", "byte": "int8",
    "int16": "int16", "smallint": "int16", "short": "int16",
    "int32": "int32", "int": "int32", "integer": "int32",
    "int64": "int64", "long": "int64", "bigint": "int64",
    "float": "float", "float32": "float", "real": "float",
    "double": "double", "float64": "double",
    "string": "string", "varchar": "string", "text": "string", "utf8": "string",
    "boolean": "boolean", "bool": "boolean",
    "date": "date", "timestamp": "timestamp", "datetime": "timestamp",
    "decimal": "decimal", "numeric": "decimal",
}

_DECIMAL_RE = re.compile(r"^(?:decimal|numeric)\s*\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$")

# 显式兼容：位宽提升 / 精度提升 / 同类型
PROMOTIONS = {
    ("int8", "int16"): "整型位宽提升 int8→int16，可无损读取",
    ("int8", "int32"): "整型位宽提升 int8→int32，可无损读取",
    ("int8", "int64"): "整型位宽提升 int8→int64，可无损读取",
    ("int16", "int32"): "整型位宽提升 int16→int32，可无损读取",
    ("int16", "int64"): "整型位宽提升 int16→int64，可无损读取",
    ("int32", "int64"): "整型位宽提升 int32→int64，可无损读取",
    ("float", "double"): "浮点位宽提升 float→double，可无损读取",
}

# 显式阻断：位宽收窄 / 浮点收窄 / 字符串转数值 / date→timestamp
BLOCKED = {
    ("int64", "int32"): "整型位宽收窄 int64→int32 可能溢出，阻断",
    ("int64", "int16"): "整型位宽收窄 int64→int16 可能溢出，阻断",
    ("int64", "int8"): "整型位宽收窄 int64→int8 可能溢出，阻断",
    ("int32", "int16"): "整型位宽收窄 int32→int16 可能溢出，阻断",
    ("int32", "int8"): "整型位宽收窄 int32→int8 可能溢出，阻断",
    ("int16", "int8"): "整型位宽收窄 int16→int8 可能溢出，阻断",
    ("double", "float"): "浮点收窄 double→float 丢失有效位，阻断",
    ("string", "int8"): "字符串→整数需解析，非无损读取，阻断",
    ("string", "int16"): "字符串→整数需解析，非无损读取，阻断",
    ("string", "int32"): "字符串→整数需解析，非无损读取，阻断",
    ("string", "int64"): "字符串→整数需解析，非无损读取，阻断",
    ("date", "timestamp"): "date→timestamp 语义变更（缺时间与时区），阻断",
}


class TypeParseError(ValueError):
    """类型名不合法。"""


def normalize_type(raw: str) -> str:
    """把书写形式归一化：大写/别名/decimal(p,s)。非法返回 TypeParseError。"""
    text = (raw or "").strip().lower().replace(" ", "")
    if not text:
        raise TypeParseError("类型为空")
    m = _DECIMAL_RE.match(text)
    if m:
        precision, scale = int(m.group(1)), int(m.group(2))
        if not 1 <= precision <= 38:
            raise TypeParseError(f"decimal 精度需在 1..38，收到 {precision}")
        if not 0 <= scale <= precision:
            raise TypeParseError(f"decimal 标度需在 0..{precision}，收到 {scale}")
        return f"decimal({precision},{scale})"
    if text in ("decimal", "numeric"):
        raise TypeParseError("decimal 缺少精度与标度，请写成 decimal(p,s)")
    base = TYPE_ALIASES.get(text)
    if base is None:
        raise TypeParseError(f"不支持的类型名 {raw!r}（可用 int8/int16/int32/int64/float/double/string/boolean/date/timestamp/decimal(p,s)）")
    return base


def base_type(type_name: str) -> str:
    if type_name.startswith("decimal("):
        return "decimal"
    return type_name


def decimal_of(type_name: str) -> tuple[int, int] | None:
    m = _DECIMAL_RE.match(type_name.replace(" ", "").lower())
    return (int(m.group(1)), int(m.group(2))) if m else None


def promotion(old_type: str, new_type: str) -> dict:
    """旧类型值能否被新类型无损读取。"""
    old_b, new_b = base_type(old_type), base_type(new_type)
    result = {
        "from": old_type,
        "to": new_type,
        "from_base": old_b,
        "to_base": new_b,
        "rule": "explicit",
    }
    if old_type == new_type:
        result.update(verdict="compatible", reason="同类型，无需变更")
        return result
    if old_b == "decimal" or new_b == "decimal":
        if old_b != new_b:
            result.update(
                verdict="unknown",
                rule="unknown",
                reason=f"decimal 与 {new_b if old_b == 'decimal' else old_b} 之间的转换未在矩阵内，不猜测",
            )
            return result
        rule = decimal_of(old_type)
        target = decimal_of(new_type)
        if rule is None or target is None:
            result.update(verdict="unknown", rule="unknown", reason="decimal 精度未声明，无法判定")
            return result
        (old_p, old_s), (new_p, new_s) = rule, target
        if old_s != new_s:
            result.update(
                verdict="unknown",
                reason=f"标度变更 decimal({old_p},{old_s})→decimal({new_p},{new_s}) 需业务确认换算口径，不猜测",
            )
            return result
        if new_p >= old_p:
            result.update(
                verdict="compatible",
                reason=f"decimal 精度提升 {old_p}→{new_p}（标度 {old_s} 不变），可无损读取",
            )
        else:
            result.update(
                verdict="blocked",
                reason=f"decimal 精度收窄 {old_p}→{new_p} 可能溢出，阻断",
            )
        return result
    if (old_b, new_b) in PROMOTIONS:
        result.update(verdict="compatible", reason=PROMOTIONS[(old_b, new_b)])
        return result
    if (old_b, new_b) in BLOCKED:
        result.update(verdict="blocked", reason=BLOCKED[(old_b, new_b)])
        return result
    result.update(
        verdict="unknown",
        rule="unknown",
        reason=f"{old_b}→{new_b} 未在显式矩阵内（不猜测，需人工确认）",
    )
    return result


VERDICT_LABELS = {"compatible": "兼容", "blocked": "阻断", "unknown": "未知"}


def matrix_table() -> list[dict]:
    """复核页展示用的完整显式矩阵。"""
    rows = []
    for (src, dst), reason in sorted(PROMOTIONS.items()):
        rows.append({"from": src, "to": dst, "verdict": "compatible", "rule": "显式表", "reason": reason})
    for (src, dst), reason in sorted(BLOCKED.items()):
        rows.append({"from": src, "to": dst, "verdict": "blocked", "rule": "显式表", "reason": reason})
    return rows


DECIMAL_RULES = [
    "decimal(p,s) → decimal(p2,s)：p2 ≥ p 且标度不变 → compatible（精度提升）",
    "decimal(p,s) → decimal(p2,s)：p2 < p → blocked（精度收窄）",
    "标度变化（s ≠ s2）→ unknown，需业务确认换算口径",
    "decimal 与其他基础类型互转 → unknown，不在显式矩阵内",
]
