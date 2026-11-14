"""字段匹配（纯函数）：field_id 优先，其次名称精确匹配。

匹配顺序：
1. 两侧都有 id 且相同 → 同一字段（名称变化 → renamed）
2. 剩余字段按 name 精确匹配；两侧 id 都存在但不同 → id_changed（需人工确认）
3. 仍无匹配 → added / removed
"""
from __future__ import annotations

from .types import promotion

KIND_LABELS = {
    "unchanged": "无变化",
    "renamed": "改名",
    "id_changed": "字段 ID 变更",
    "type_changed": "类型变更",
    "added": "新增",
    "removed": "删除",
}

BASIS_LABELS = {
    "id": "field_id 相同",
    "name": "名称精确匹配",
    None: "—",
}


def _pair(old: dict | None, new: dict | None, basis: str | None) -> dict:
    if old is None:
        return {
            "kind": "added",
            "basis": None,
            "old": None,
            "new": new,
            "field_id": new.get("id"),
            "name": new["name"],
            "old_name": None,
            "new_name": new["name"],
            "old_type": None,
            "new_type": new["type"],
            "name_changed": False,
            "type_changed": False,
            "type_result": None,
            "partition": bool(new.get("partition")),
            "note": "新 schema 独有字段；旧数据文件不含该列",
        }
    if new is None:
        return {
            "kind": "removed",
            "basis": None,
            "old": old,
            "new": None,
            "field_id": old.get("id"),
            "name": old["name"],
            "old_name": old["name"],
            "new_name": None,
            "old_type": old["type"],
            "new_type": None,
            "name_changed": False,
            "type_changed": False,
            "type_result": None,
            "partition": bool(old.get("partition")),
            "note": "旧 schema 独有字段；新 schema 不再声明该列",
        }

    name_changed = old["name"] != new["name"]
    type_changed = old["type"] != new["type"]
    old_id, new_id = old.get("id"), new.get("id")
    id_changed = (
        basis == "name"
        and old_id is not None
        and new_id is not None
        and int(old_id) != int(new_id)
    )
    if name_changed:
        kind = "renamed"
    elif id_changed:
        kind = "id_changed"
    elif type_changed:
        kind = "type_changed"
    else:
        kind = "unchanged"

    notes = []
    if basis == "id":
        notes.append("按 field_id 认定同一字段")
    else:
        notes.append("两侧缺 field_id 或 id 不同，按名称匹配")
    if id_changed:
        notes.append(f"field_id {old_id}→{new_id} 变化，需人工确认是否同一列")
    if name_changed:
        notes.append(f"名称 {old['name']}→{new['name']}，Parquet 按 id 读取不受影响，但按列名的 SQL 需同步")
    if bool(old.get("partition")) != bool(new.get("partition")):
        notes.append("分区标记发生变化")

    return {
        "kind": kind,
        "basis": basis,
        "old": old,
        "new": new,
        "field_id": new_id if new_id is not None else old_id,
        "name": new["name"],
        "old_name": old["name"],
        "new_name": new["name"],
        "old_type": old["type"],
        "new_type": new["type"],
        "name_changed": name_changed,
        "id_changed": id_changed,
        "type_changed": type_changed,
        "type_result": promotion(old["type"], new["type"]) if type_changed else None,
        "partition": bool(old.get("partition")) or bool(new.get("partition")),
        "note": "；".join(notes),
    }


def match_fields(old_fields: list[dict], new_fields: list[dict]) -> dict:
    """返回字段匹配结果与计数；不修改入参。"""
    old_rows = [dict(f) for f in old_fields]
    new_rows = [dict(f) for f in new_fields]
    old_used: set[int] = set()
    new_used: set[int] = set()
    pairs: list[dict] = []

    new_by_id: dict[int, int] = {}
    for ni, nf in enumerate(new_rows):
        if nf.get("id") is not None:
            new_by_id.setdefault(int(nf["id"]), ni)

    # 阶段 1：field_id 相同（列身份优先于名称）
    for oi, of in enumerate(old_rows):
        oid = of.get("id")
        if oid is None:
            continue
        ni = new_by_id.get(int(oid))
        if ni is None or ni in new_used:
            continue
        old_used.add(oi)
        new_used.add(ni)
        pairs.append(_pair(of, new_rows[ni], "id"))

    # 阶段 2：名称精确匹配
    new_by_name: dict[str, int] = {}
    for ni, nf in enumerate(new_rows):
        if ni not in new_used:
            new_by_name.setdefault(nf["name"], ni)
    for oi, of in enumerate(old_rows):
        if oi in old_used:
            continue
        ni = new_by_name.get(of["name"])
        if ni is None or ni in new_used:
            continue
        old_used.add(oi)
        new_used.add(ni)
        pairs.append(_pair(of, new_rows[ni], "name"))

    # 阶段 3：新增 / 删除（保持原顺序：先旧侧删除，再新侧新增）
    for oi, of in enumerate(old_rows):
        if oi not in old_used:
            pairs.append(_pair(of, None, None))
    for ni, nf in enumerate(new_rows):
        if ni not in new_used:
            pairs.append(_pair(None, nf, None))

    counts = {
        "old_fields": len(old_rows),
        "new_fields": len(new_rows),
        "unchanged": 0,
        "renamed": 0,
        "id_changed": 0,
        "type_changed": 0,
        "added": 0,
        "removed": 0,
    }
    for p in pairs:
        counts[p["kind"]] += 1
        if p["kind"] in ("renamed", "type_changed") and p.get("type_changed"):
            counts["type_changed"] += 0 if p["kind"] == "type_changed" else 0
    # type_changed 计数包含「改名同时改类型」的组合情况
    counts["type_changed"] = sum(1 for p in pairs if p.get("type_changed"))
    counts["changed"] = counts["renamed"] + counts["id_changed"] + counts["added"] + counts["removed"] + counts["type_changed"]

    return {"pairs": pairs, "counts": counts}


def find_by_old_name(pairs: list[dict], name: str) -> dict | None:
    for p in pairs:
        if p.get("old") and p["old"]["name"] == name:
            return p
    return None
