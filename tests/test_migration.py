"""迁移计划：ALTER 顺序、回填清单、分区阻断。"""
from app.compat import judge
from app.matching import match_fields
from app.migration import plan


def f(fid, name, type_="string", nullable=True, default=None, partition=False):
    return {"id": fid, "name": name, "type": type_, "nullable": nullable, "default": default, "partition": partition}


def plan_for(old_fields, new_fields, old_parts=None, new_parts=None):
    match = match_fields(old_fields, new_fields)
    compat = judge(match["pairs"], old_parts, new_parts)
    return plan("dwd_t", match["pairs"], compat)


def test_no_change_produces_zero_steps():
    migration = plan_for([f(1, "a")], [f(1, "a")])
    assert migration["steps"] == []
    assert migration["sql_steps"] == []
    assert migration["sql_text"].count("ALTER TABLE") == 0
    assert migration["can_execute"] is True


def test_rename_then_add_then_backfill_then_type():
    old = [f(4, "channel"), f(3, "amount", "decimal(10,2)", nullable=False)]
    new = [
        f(4, "pay_channel"),
        f(3, "amount", "decimal(18,2)", nullable=False),
        f(7, "fee", "decimal(10,2)", nullable=False, default="0.00"),
        f(8, "settle_status", "string", nullable=True),
    ]
    migration = plan_for(old, new)
    actions = [s["action"] for s in migration["steps"]]
    assert actions[0] == "rename_column"
    assert actions.index("add_column") < actions.index("backfill") < actions.index("alter_type")
    assert [b["field"] for b in migration["backfill"]] == ["fee"]
    sql = [s["sql"] for s in migration["sql_steps"]]
    assert sql[0].startswith("ALTER TABLE dwd_t RENAME COLUMN channel TO pay_channel;")
    assert any("ADD COLUMN fee decimal(10,2) DEFAULT '0.00' NOT NULL" in s for s in sql)
    assert any("ALTER COLUMN amount TYPE decimal(18,2)" in s for s in sql)
    assert migration["can_execute"] is True


def test_partition_type_change_is_blocked_and_emits_no_alter():
    old = [f(4, "weight", "int64", nullable=False), f(5, "dt", "date", nullable=False, partition=True)]
    new = [f(4, "weight", "int32", nullable=False), f(5, "dt", "timestamp", nullable=False, partition=True)]
    migration = plan_for(old, new, ["dt"], ["dt"])
    codes = {b["code"] for b in migration["blocking"]}
    assert "partition_type_changed" in codes
    assert "type_blocked" in codes
    assert migration["can_execute"] is False
    assert not any("ALTER COLUMN dt" in (s["sql"] or "") for s in migration["steps"])
    assert not any("ALTER COLUMN weight" in (s["sql"] or "") for s in migration["steps"])


def test_required_added_column_blocks_without_alter():
    old = [f(1, "a")]
    new = [f(1, "a"), f(2, "b", nullable=False, default=None)]
    migration = plan_for(old, new)
    assert any(b["code"] == "field_added_required" for b in migration["blocking"])
    assert not any("ADD COLUMN b" in (s["sql"] or "") for s in migration["steps"])


def test_drop_column_is_last():
    old = [f(1, "a"), f(2, "gone")]
    new = [f(1, "a"), f(3, "added", nullable=True)]
    migration = plan_for(old, new)
    actions = [s["action"] for s in migration["steps"]]
    assert actions[-1] == "drop_column"
    assert migration["sql_steps"][-1]["sql"].startswith("ALTER TABLE dwd_t DROP COLUMN gone;")
    assert "DROP COLUMN" in migration["sql_text"]


def test_backfill_sql_mentions_default():
    old = [f(1, "a")]
    new = [f(1, "a"), f(2, "fee", "decimal(10,2)", nullable=False, default=0)]
    migration = plan_for(old, new)
    assert migration["backfill"][0]["suggested_sql"] == "UPDATE dwd_t SET fee = 0 WHERE fee IS NULL;"
    assert "回填" in migration["steps"][-1]["note"] or migration["steps"][-1]["action"] == "backfill"


def test_upgrade_order_differs_by_level():
    full = plan_for([f(1, "a")], [f(1, "a")])
    assert "任意顺序" in full["upgrade_order"][0]
    # none：删列 + 收窄 → 双向阻断
    none_case = plan_for(
        [f(1, "a"), f(2, "gone"), f(3, "x", "int64", nullable=False)],
        [f(1, "a"), f(3, "x", "int32", nullable=False)],
    )
    assert none_case["upgrade_order"][0].startswith("阻断")
    assert none_case["can_execute"] is False
    # forward：只收窄（旧读新没问题，新读旧被阻断）
    forward = plan_for([f(1, "x", "int64", nullable=False)], [f(1, "x", "int32", nullable=False)])
    assert forward["upgrade_order"][0].startswith("第 1 步：读取方")
