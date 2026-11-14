"""迁移计划：ALTER TABLE 序列（改名 → 加列/默认值 → 类型 → 删列最后）+ 回填对象 + 读写方升级顺序。

分区字段被删除或改类型 → 直接阻断，不产出任何会破坏分区的 ALTER。
"""
from __future__ import annotations

from .types import VERDICT_LABELS

ACTION_LABELS = {
    "rename_column": "列改名",
    "add_column": "新增列",
    "alter_type": "类型变更",
    "drop_column": "删除列",
    "backfill": "回填历史分区",
}


def _sql_default(value) -> str:
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if value is None:
        return "NULL"
    return str(value)


def plan(table: str, pairs: list[dict], compat: dict) -> dict:
    steps: list[dict] = []
    backfill: list[dict] = []
    blocking: list[dict] = []
    seq = 0

    def push(action: str, sql: str | None, object_name: str, note: str,
             requires_rewrite: bool = False, executable: bool = True) -> None:
        nonlocal seq
        seq += 1
        steps.append({
            "seq": seq,
            "action": action,
            "action_label": ACTION_LABELS[action],
            "sql": sql,
            "object": object_name,
            "note": note,
            "requires_rewrite": requires_rewrite,
            "executable": executable,
        })

    def block(code: str, field: str, text: str) -> None:
        blocking.append({"code": code, "field": field, "text": text})

    # 1) 列改名（同 field_id）
    for p in pairs:
        if p["kind"] == "renamed":
            push(
                "rename_column",
                f"ALTER TABLE {table} RENAME COLUMN {p['old_name']} TO {p['new_name']};",
                p["new_name"],
                f"field_id {p['field_id']} 不变，仅列名变化；按列名的下游 SQL 需同步改名",
            )

    # 2) 新增列（带默认值则同时登记回填）
    for p in pairs:
        if p["kind"] != "added":
            continue
        new = p["new"]
        if new.get("partition"):
            block("partition_added", new["name"], f"新增分区字段 {new['name']} 会使历史分区路径失配，需单独治理")
            continue
        if not new.get("nullable") and new.get("default") is None:
            block("field_added_required", new["name"],
                  f"新增列 {new['name']} 非空且无默认值：历史分区无法满足，需先补默认值")
            continue
        default_sql = ""
        if new.get("default") is not None:
            default_sql = f" DEFAULT {_sql_default(new['default'])}"
        null_sql = "" if new.get("nullable") else " NOT NULL"
        push(
            "add_column",
            f"ALTER TABLE {table} ADD COLUMN {new['name']} {new['type']}{default_sql}{null_sql};",
            new["name"],
            "新增列" + ("，带默认值（历史分区需回填）" if new.get("default") is not None else "，可空（历史分区保持 null）"),
        )
        if new.get("default") is not None:
            backfill.append({
                "field": new["name"],
                "kind": "add_column_with_default",
                "kind_label": "新增列带默认值",
                "reason": f"新增列 {new['name']} 带默认值 {_sql_default(new['default'])}：历史分区需物化默认值后才与读口径一致",
                "suggested_sql": f"UPDATE {table} SET {new['name']} = {_sql_default(new['default'])} WHERE {new['name']} IS NULL;",
            })

    # 2.5) 加列（含默认值）之后统一登记回填步骤
    for item in backfill:
        push(
            "backfill",
            None,
            item["field"],
            item["reason"] + "；执行方式为重写历史分区，不产出单条 ALTER",
            requires_rewrite=True,
        )

    # 3) 类型变更
    for p in pairs:
        if not p.get("type_changed"):
            continue
        old, new = p["old"], p["new"]
        if old.get("partition"):
            block("partition_type_changed", p["name"],
                  f"分区字段 {p['name']} 类型 {old['type']}→{new['type']}：直接阻断，分区取值与路径都需重写")
            continue
        verdict = p["type_result"]["verdict"]
        if verdict == "blocked":
            block("type_blocked", p["name"],
                  f"{p['name']} 类型 {old['type']}→{new['type']} 被矩阵阻断：{p['type_result']['reason']}")
            continue
        if verdict == "unknown":
            block("type_unknown", p["name"],
                  f"{p['name']} 类型 {old['type']}→{new['type']} 未在矩阵内：需人工确认后再生成 ALTER")
            continue
        push(
            "alter_type",
            f"ALTER TABLE {table} ALTER COLUMN {p['name']} TYPE {new['type']};",
            p["name"],
            f"{old['type']}→{new['type']}：{p['type_result']['reason']}",
            requires_rewrite=True,
        )

    # 4) 删除列放最后（先让下游改造）
    for p in pairs:
        if p["kind"] != "removed":
            continue
        if p["old"].get("partition"):
            block("partition_removed", p["name"],
                  f"分区字段 {p['name']} 被删除：直接阻断，新 schema 无法定位历史分区")
            continue
        push(
            "drop_column",
            f"ALTER TABLE {table} DROP COLUMN {p['name']};",
            p["name"],
            "删除列必须最后执行：先确认 broken_consumers 已全部改造完成",
        )

    total = len(steps)
    sql_lines = [f"-- schemaevo 迁移脚本 · 表 {table} · 兼容级别 {compat['level']}（{compat['level_label']}）"]
    sql_lines.append(f"-- 计划共 {total} 个步骤，其中 {len([s for s in steps if s['sql']])} 条 ALTER；回填等无 SQL 的步骤见结果页步骤表")
    if blocking:
        sql_lines.append(f"-- 注意：存在 {len(blocking)} 项阻断，脚本不可在阻断消除前执行")
    for step in steps:
        if step["sql"]:
            sql_lines.append(f"-- 步骤 {step['seq']}/{total} · {step['action_label']} {step['object']}：{step['note']}")
            sql_lines.append(step["sql"])
    if blocking:
        sql_lines.append("-- 阻断项（不产出 ALTER）：")
        for item in blocking:
            sql_lines.append(f"-- - {item['field']}：{item['text']}")
    sql_text = "\n".join(sql_lines) + "\n"

    return {
        "steps": steps,
        "sql_steps": [s for s in steps if s["sql"]],
        "backfill": backfill,
        "blocking": blocking,
        "can_execute": not blocking and not compat["manual_confirm"] and compat["level"] != "none",
        "upgrade_order": upgrade_order(compat),
        "sql_text": sql_text,
    }


def upgrade_order(compat: dict) -> list[str]:
    level = compat["level"]
    if level == "full":
        return [
            "双向兼容：读写方可任意顺序滚动升级，无需停机窗口",
            "可选：全部升级完成后清理历史默认值注释与旧分区标记",
        ]
    if level == "backward":
        return [
            "第 1 步：写入方（上游产出任务）先升级并发布新 schema（含默认值）",
            "第 2 步：回填历史分区（按回填对象清单逐分区重写）",
            "第 3 步：读取方（下游查询/看板）按新列名与新类型升级",
            "第 4 步：确认无旧读者后，再执行删除列/清理旧字段",
        ]
    if level == "forward":
        return [
            "第 1 步：读取方先升级以忽略新增列",
            "第 2 步：写入方再升级 schema",
            "第 3 步：保留回滚窗口，确认旧读方无报错后再收口",
        ]
    return [
        "阻断：当前不允许直接发布新 schema",
        "改两阶段迁移：先加新列并双写 → 回填历史分区 → 切换读写 → 最后删除旧列",
        "破坏性变更（删列/收窄/分区变更）必须经表治理评审后单独排期",
    ]
