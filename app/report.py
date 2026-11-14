"""报表组装：把匹配/矩阵/兼容/迁移/引用检查拼成可重放的运行记录。"""
from __future__ import annotations

from datetime import datetime, timezone

from . import settings
from .compat import judge
from .consumers import check_consumers
from .matching import BASIS_LABELS, KIND_LABELS, match_fields
from .migration import ACTION_LABELS, plan
from .types import VERDICT_LABELS


def _matrix_usage(pairs: list[dict]) -> list[dict]:
    rows = []
    seen = set()
    for p in pairs:
        if not p.get("type_result"):
            continue
        tr = p["type_result"]
        key = (tr["from"], tr["to"])
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "from": tr["from"],
            "to": tr["to"],
            "verdict": tr["verdict"],
            "verdict_label": VERDICT_LABELS[tr["verdict"]],
            "rule": tr["rule"],
            "reason": tr["reason"],
        })
    return rows


def build_run(old: dict, new: dict, consumers: dict, source: str = "自定义输入") -> dict:
    """核心编排：纯计算，不读写文件。"""
    match = match_fields(old["fields"], new["fields"])
    pairs = match["pairs"]
    compat = judge(pairs, old.get("partitions"), new.get("partitions"))
    migration = plan(new["table"], pairs, compat)
    consumer_check = check_consumers(consumers["files"], pairs)

    match_rows = []
    for index, p in enumerate(pairs, start=1):
        match_rows.append({
            "index": index,
            "kind": p["kind"],
            "kind_label": KIND_LABELS[p["kind"]],
            "basis": p["basis"],
            "basis_label": BASIS_LABELS[p["basis"]],
            "field_id": p["field_id"],
            "old_name": p["old_name"],
            "new_name": p["new_name"],
            "old_type": p["old_type"],
            "new_type": p["new_type"],
            "type_verdict": p["type_result"]["verdict"] if p.get("type_result") else None,
            "type_reason": p["type_result"]["reason"] if p.get("type_result") else None,
            "partition": p["partition"],
            "note": p["note"],
        })

    type_changes = []
    for p in pairs:
        if not p.get("type_changed"):
            continue
        tr = p["type_result"]
        type_changes.append({
            "field": p["name"],
            "old_type": p["old_type"],
            "new_type": p["new_type"],
            "verdict": tr["verdict"],
            "verdict_label": VERDICT_LABELS[tr["verdict"]],
            "rule": tr["rule"],
            "reason": tr["reason"],
        })

    # 阻断原因按 (code, field) 去重：兼容层与迁移层各记来源，避免同一变更重复计数
    block_reasons: list[dict] = []
    seen_blocks: set[tuple[str, str]] = set()
    for item in compat["blocking"] + migration["blocking"]:
        key = (item.get("code"), item.get("field"))
        if key in seen_blocks:
            continue
        seen_blocks.add(key)
        block_reasons.append({
            "code": item["code"],
            "field": item.get("field"),
            "text": item["text"],
            "direction": item.get("direction"),
            "direction_label": item.get("direction_label"),
        })
    run = {
        "run_id": None,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": source,
        "table": new["table"],
        "matrix_version": settings.MATRIX_VERSION,
        "old_schema": old,
        "new_schema": new,
        "consumers": consumers,
        "match": {
            "counts": match["counts"],
            "rows": match_rows,
        },
        "type_changes": type_changes,
        "matrix_usage": _matrix_usage(pairs),
        "compat": compat,
        "migration": {
            "steps": migration["steps"],
            "sql_steps": migration["sql_steps"],
            "backfill": migration["backfill"],
            "blocking": migration["blocking"],
            "can_execute": migration["can_execute"],
            "upgrade_order": migration["upgrade_order"],
            "sql_text": migration["sql_text"],
        },
        "consumers_check": consumer_check,
        "block_reasons": block_reasons,
        "summary": {
            "level": compat["level"],
            "level_label": compat["level_label"],
            "renamed": match["counts"]["renamed"],
            "added": match["counts"]["added"],
            "removed": match["counts"]["removed"],
            "id_changed": match["counts"]["id_changed"],
            "type_changed": match["counts"]["type_changed"],
            "sql_steps": len(migration["sql_steps"]),
            "backfill_count": len(migration["backfill"]),
            "blocking_count": len(block_reasons),
            "migration_blocking": len(migration["blocking"]),
            "compat_blocking": len(compat["blocking"]),
            "broken_consumers": consumer_check["broken_count"],
            "needs_update": consumer_check["needs_update_count"],
            "can_execute": migration["can_execute"],
        },
    }
    return run


def overview(run: dict) -> dict:
    s = run["summary"]
    return {
        "run_id": run["run_id"],
        "table": run["table"],
        "created_at": run["created_at"],
        "source": run["source"],
        "level": s["level"],
        "level_label": s["level_label"],
        "sql_steps": s["sql_steps"],
        "backfill_count": s["backfill_count"],
        "blocking_count": s["blocking_count"],
        "broken_consumers": s["broken_consumers"],
        "can_execute": s["can_execute"],
        "changed_fields": s["renamed"] + s["added"] + s["removed"] + s["id_changed"] + s["type_changed"],
    }


def sql_download_name(run: dict) -> str:
    return f"{run['table']}-{run['run_id']}.sql"
