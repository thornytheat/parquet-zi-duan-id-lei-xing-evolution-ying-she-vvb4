"""运行记录持久化：/app/data/runs 下的 JSON 文件（可重跑、可回看历史）。"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from . import settings

RUN_ID_RE = re.compile(r"^run-\d{8}T\d{6}-[0-9a-f]{6}$")


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"run-{stamp}-{uuid.uuid4().hex[:6]}"


def _ensure_dir() -> None:
    settings.RUN_DIR.mkdir(parents=True, exist_ok=True)


def _path(run_id: str):
    if not RUN_ID_RE.match(run_id or ""):
        raise KeyError(f"运行记录 {run_id!r} 不存在")
    return settings.RUN_DIR / f"{run_id}.json"


def save_run(run: dict) -> dict:
    _ensure_dir()
    run = dict(run)
    run["run_id"] = run.get("run_id") or new_run_id()
    path = _path(run["run_id"])
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    _prune()
    return run


def load_run(run_id: str) -> dict:
    path = _path(run_id)
    if not path.exists():
        raise KeyError(f"运行记录 {run_id!r} 不存在")
    return json.loads(path.read_text(encoding="utf-8"))


def list_runs(limit: int = 50) -> list[dict]:
    _ensure_dir()
    files = sorted(settings.RUN_DIR.glob("run-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    rows = []
    for path in files[:limit]:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        rows.append({
            "run_id": run.get("run_id"),
            "table": run.get("table"),
            "created_at": run.get("created_at"),
            "source": run.get("source"),
            "summary": run.get("summary", {}),
        })
    return rows


def _prune() -> None:
    files = sorted(settings.RUN_DIR.glob("run-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[settings.MAX_RUNS_KEPT:]:
        try:
            path.unlink()
        except OSError:
            pass
