"""列引用检查：数据文件清单里引用被删/被改类型列的 → broken_consumers。"""
from __future__ import annotations

from .matching import find_by_old_name

STATUS_LABELS = {"broken": "破损", "needs_update": "需同步改名", "unknown": "待确认", "ok": "正常"}


def check_consumers(files: list[dict], pairs: list[dict]) -> dict:
    broken: list[dict] = []
    needs_update: list[dict] = []
    unknown: list[dict] = []
    ok_refs = 0
    total_refs = 0
    referenced: set[str] = set()

    for file in files:
        for column in file["columns"]:
            total_refs += 1
            referenced.add(column)
            pair = find_by_old_name(pairs, column)
            if pair is None:
                unknown.append({
                    "path": file["path"], "column": column,
                    "reason": "旧 schema 中找不到该列，无法判定影响", "status": "unknown",
                })
                continue
            kind = pair["kind"]
            if kind == "removed":
                broken.append({
                    "path": file["path"], "column": column,
                    "reason": f"列 {column} 在新 schema 中被删除，读取该列的任务会失败",
                    "status": "broken", "severity": "high",
                })
            elif kind == "id_changed":
                unknown.append({
                    "path": file["path"], "column": column,
                    "reason": f"列 {column} 的 field_id 变化，需人工确认后重跑",
                    "status": "unknown",
                })
            elif pair.get("type_changed"):
                verdict = pair["type_result"]["verdict"]
                if verdict == "compatible":
                    ok_refs += 1
                elif verdict == "blocked":
                    broken.append({
                        "path": file["path"], "column": column,
                        "reason": f"列 {column} 类型 {pair['old_type']}→{pair['new_type']} 被阻断，引用方需改造",
                        "status": "broken", "severity": "high",
                    })
                else:
                    unknown.append({
                        "path": file["path"], "column": column,
                        "reason": f"列 {column} 类型 {pair['old_type']}→{pair['new_type']} 未知，需人工确认",
                        "status": "unknown",
                    })
            elif pair["kind"] == "renamed":
                needs_update.append({
                    "path": file["path"], "column": column,
                    "new_column": pair["new_name"],
                    "reason": f"列 {column} 改名为 {pair['new_name']}（同 field_id），按列名的 SQL/视图需同步",
                    "status": "needs_update",
                })
            else:
                ok_refs += 1

    return {
        "files": len(files),
        "total_refs": total_refs,
        "ok_refs": ok_refs,
        "broken": broken,
        "needs_update": needs_update,
        "unknown": unknown,
        "broken_count": len(broken),
        "needs_update_count": len(needs_update),
        "unknown_count": len(unknown),
        "referenced_columns": sorted(referenced),
    }
