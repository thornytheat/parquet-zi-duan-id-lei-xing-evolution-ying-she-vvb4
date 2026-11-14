"""兼容级别判定：旧写新读（backward）/ 新写旧读（forward）双向，输出 full/backward/forward/none。"""
from __future__ import annotations

from .types import promotion

DIRECTION_LABELS = {
    "backward": "旧写新读（旧数据 → 新 schema，backward）",
    "forward": "新写旧读（新数据 → 旧 schema，forward）",
}

LEVEL_LABELS = {
    "full": "完全兼容（full）",
    "backward": "向后兼容（backward）：新 schema 可读旧数据，旧 schema 读不了新数据",
    "forward": "向前兼容（forward）：旧 schema 可读新数据，新 schema 读不了旧数据",
    "none": "不兼容（none）：双向均被阻断",
}

SEVERITY_LABELS = {"block": "阻断", "confirm": "需确认", "info": "提示"}


def _mark(state: dict, direction: str, verdict: str) -> None:
    """blocked > unknown > compatible，取最严重。"""
    rank = {"compatible": 0, "unknown": 1, "blocked": 2}
    if rank[verdict] > rank[state[direction]]:
        state[direction] = verdict


def judge(pairs: list[dict], old_partitions: list[str] | None = None,
          new_partitions: list[str] | None = None) -> dict:
    state = {"backward": "compatible", "forward": "compatible"}
    reasons: list[dict] = []
    blocking: list[dict] = []
    manual: list[dict] = []

    def add(direction: str, verdict: str, code: str, field: str, text: str, severity: str) -> None:
        _mark(state, direction, verdict)
        item = {
            "direction": direction,
            "direction_label": DIRECTION_LABELS.get(direction, "双向"),
            "verdict": verdict,
            "code": code,
            "field": field,
            "text": text,
            "severity": severity,
            "severity_label": SEVERITY_LABELS[severity],
        }
        reasons.append(item)
        if severity == "block" and verdict != "compatible":
            blocking.append(item)
        if severity == "confirm":
            manual.append(item)

    for p in pairs:
        kind = p["kind"]
        old, new = p.get("old"), p.get("new")
        partitions = [old_partitions or [], new_partitions or []]

        if kind == "removed":
            if old.get("partition"):
                text = f"分区字段 {old['name']} 被删除，历史分区无法被新 schema 定位"
                add("backward", "blocked", "partition_removed", old["name"], text, "block")
                add("forward", "blocked", "partition_removed", old["name"], text, "block")
                continue
            add("backward", "compatible", "field_removed", old["name"],
                f"旧数据含列 {old['name']}、新 schema 未声明：读取时忽略该列（backward 可用）", "info")
            add("forward", "blocked", "field_removed", old["name"],
                f"旧读方期望列 {old['name']}，新数据文件缺失该列（forward 阻断）", "block")
            continue

        if kind == "added":
            if new.get("partition"):
                add("backward", "blocked", "partition_added", new["name"],
                    f"新增分区字段 {new['name']}：历史分区缺少该分区值", "block")
                add("forward", "compatible", "partition_added", new["name"],
                    f"新增分区字段 {new['name']}，旧读方忽略", "info")
                continue
            if new.get("nullable") or new.get("default") is not None:
                add("backward", "compatible", "field_added", new["name"],
                    f"新增列 {new['name']}（{'可空' if new.get('nullable') else '带默认值'}）：旧数据按 null/默认值补齐",
                    "info")
            else:
                add("backward", "blocked", "field_added_required", new["name"],
                    f"新增列 {new['name']} 非空且无默认值：旧数据无法满足约束", "block")
            add("forward", "compatible", "field_added_extra", new["name"],
                f"新增列 {new['name']}：旧读方忽略多余列", "info")
            continue

        if kind == "id_changed":
            text = f"字段 {p['name']} 的 field_id 由 {old.get('id')} 变为 {new.get('id')}，需人工确认是否同一列"
            add("backward", "unknown", "id_changed", p["name"], text, "confirm")
            add("forward", "unknown", "id_changed", p["name"], text, "confirm")
            continue

        # unchanged / renamed / type_changed（可能组合）
        if old.get("partition") and p.get("type_changed"):
            text = f"分区字段 {p['name']} 类型由 {old['type']} 改为 {new['type']}，历史分区路径/取值失效"
            add("backward", "blocked", "partition_type_changed", p["name"], text, "block")
            add("forward", "blocked", "partition_type_changed", p["name"], text, "block")
            continue

        if p.get("type_changed"):
            back = p["type_result"]
            fwd = promotion(new["type"], old["type"])
            add("backward", back["verdict"], f"type_{back['verdict']}", p["name"],
                f"{old['type']}→{new['type']}（旧写新读）：{back['reason']}", 
                "block" if back["verdict"] == "blocked" else ("confirm" if back["verdict"] == "unknown" else "info"))
            add("forward", fwd["verdict"], f"type_{fwd['verdict']}", p["name"],
                f"{new['type']}→{old['type']}（新写旧读）：{fwd['reason']}",
                "block" if fwd["verdict"] == "blocked" else ("confirm" if fwd["verdict"] == "unknown" else "info"))
        if p.get("name_changed"):
            add("backward", "compatible", "renamed", p["name"],
                f"列名 {p['old_name']}→{p['new_name']}，field_id 未变：Parquet 按 id 读取不受影响（backward 可用）", "info")
            add("forward", "compatible", "renamed", p["name"],
                f"列名变更不影响旧读方按 id 读取；按列名的 SQL/视图需同步改名", "info")
        if kind == "unchanged":
            add("backward", "compatible", "unchanged", p["name"], f"{p['name']} 名称/类型均未变化", "info")

    if state["backward"] == "compatible" and state["forward"] == "compatible":
        level = "full"
    elif state["backward"] == "compatible":
        level = "backward"
    elif state["forward"] == "compatible":
        level = "forward"
    else:
        level = "none"

    return {
        "level": level,
        "level_label": LEVEL_LABELS[level],
        "directions": {
            direction: {
                "state": state[direction],
                "state_label": {"compatible": "兼容", "blocked": "阻断", "unknown": "未知"}[state[direction]],
                "label": DIRECTION_LABELS[direction],
            }
            for direction in ("backward", "forward")
        },
        "reasons": reasons,
        "blocking": blocking,
        "manual_confirm": manual,
    }
