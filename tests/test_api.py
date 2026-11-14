"""API + 端到端：成功、4xx、A/B/C 三组真实样例会跑。"""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def post_json(payload):
    return client.post("/api/compare", json=payload)


def test_health_ok():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_index_and_workbench_render():
    home = client.get("/")
    assert home.status_code == 200
    assert "Parquet 字段 ID 与类型演进映射" in home.text
    bench = client.get("/workbench?sample=B")
    assert bench.status_code == 200
    assert "dwd_payment" in bench.text


def test_sample_a_is_full_with_zero_migration():
    res = post_json({"sample": "A"})
    assert res.status_code == 200
    body = res.json()
    assert body["summary"]["level"] == "full"
    assert body["summary"]["sql_steps"] == 0
    assert body["summary"]["broken_consumers"] == 0
    page = client.get(body["redirect"])
    assert page.status_code == 200
    assert "字段匹配" in page.text


def test_sample_b_rename_added_backfill_and_alter_order():
    body = post_json({"sample": "B"}).json()
    summary = body["summary"]
    assert summary["level"] == "backward"
    assert summary["renamed"] == 1
    assert summary["added"] == 2
    assert summary["backfill_count"] == 1
    assert summary["sql_steps"] == 4
    run = client.get(f"/api/runs/{body['run_id']}").json()
    sql = run["migration"]["sql_text"]
    assert "RENAME COLUMN channel TO pay_channel" in sql
    assert "ADD COLUMN fee" in sql
    assert "ALTER COLUMN amount TYPE decimal(18,2)" in sql
    assert sql.index("RENAME COLUMN") < sql.index("ADD COLUMN") < sql.index("ALTER COLUMN")
    assert run["consumers_check"]["needs_update_count"] == 2


def test_sample_c_is_none_with_three_blocks_and_broken_consumers():
    body = post_json({"sample": "C"}).json()
    summary = body["summary"]
    assert summary["level"] == "none"
    assert summary["blocking_count"] == 3
    assert summary["can_execute"] is False
    assert summary["broken_consumers"] > 0
    run = client.get(f"/api/runs/{body['run_id']}").json()
    codes = {b["code"] for b in run["block_reasons"]}
    assert codes == {"type_blocked", "partition_type_changed", "field_removed"}
    columns = {b["column"] for b in run["consumers_check"]["broken"]}
    assert "status" in columns or "weight_gram" in columns


def test_custom_schemas_work():
    old = {"table": "t", "fields": [{"id": 1, "name": "a", "type": "int32"}]}
    new = {"table": "t", "fields": [{"id": 1, "name": "a", "type": "int64"}]}
    consumers = {"table": "t", "files": [{"path": "s3://x/part-0.parquet", "columns": ["a"]}]}
    res = post_json({"old": old, "new": new, "consumers": consumers})
    assert res.status_code == 200
    # int32→int64：旧写新读兼容，新写旧读（旧读方拿到更宽的值）被阻断 → backward
    assert res.json()["summary"]["level"] == "backward"


@pytest.mark.parametrize("payload,needle", [
    ({"old": "{bad json", "new": {}, "consumers": {}}, "不是合法 JSON"),
    ({"old": {"table": "t", "fields": []}, "new": {"table": "t", "fields": []},
      "consumers": {"files": []}}, "fields 必须是非空数组"),
    ({"old": {"table": "t", "fields": [{"name": "a", "type": "money"}]},
      "new": {"table": "t", "fields": [{"name": "a", "type": "string"}]},
      "consumers": {"files": [{"path": "p", "columns": ["a"]}]}}, "类型不合法"),
    ({"old": {"table": "t", "fields": [{"id": 1, "name": "a", "type": "string"},
                                       {"id": 1, "name": "b", "type": "string"}]},
      "new": {"table": "t", "fields": [{"id": 1, "name": "a", "type": "string"}]},
      "consumers": {"files": [{"path": "p", "columns": ["a"]}]}}, "field_id 重复"),
    ({"old": {"table": "t1", "fields": [{"id": 1, "name": "a", "type": "string"}]},
      "new": {"table": "t2", "fields": [{"id": 1, "name": "a", "type": "string"}]},
      "consumers": {"files": [{"path": "p", "columns": ["a"]}]}}, "表名不一致"),
])
def test_bad_inputs_return_400(payload, needle):
    res = post_json(payload)
    assert res.status_code == 400
    assert needle in res.json()["detail"]


def test_non_json_body_returns_400():
    res = client.post("/api/compare", content="not-json", headers={"Content-Type": "application/json"})
    assert res.status_code == 400


def test_unknown_sample_returns_404():
    assert client.get("/api/samples/Z").status_code == 404


def test_missing_run_returns_404():
    assert client.get("/api/runs/run-20990101T000000-abcdef").status_code == 404
    assert client.get("/result/run-20990101T000000-abcdef").status_code == 404


def test_review_page_and_history():
    post_json({"sample": "A"})
    res = client.get("/review")
    assert res.status_code == 200
    assert "类型矩阵说明" in res.text
    runs = client.get("/api/runs").json()
    assert runs["count"] >= 1


def test_migration_sql_download():
    run_id = post_json({"sample": "B"}).json()["run_id"]
    res = client.get(f"/api/runs/{run_id}/migration.sql")
    assert res.status_code == 200
    assert "RENAME COLUMN" in res.text
    assert "attachment" in res.headers.get("content-disposition", "")


def test_matrix_endpoint():
    body = client.get("/api/matrix").json()
    assert body["version"]
    assert any(row["verdict"] == "compatible" for row in body["promotions"])
    assert len(body["decimal_rules"]) == 4
