"""兼容级别：backward / forward / full / none 四例 + 分区阻断。"""
from app.compat import judge
from app.matching import match_fields


def f(fid, name, type_="string", nullable=True, default=None, partition=False):
    return {"id": fid, "name": name, "type": type_, "nullable": nullable, "default": default, "partition": partition}


def levels_for(old_fields, new_fields, old_parts=None, new_parts=None):
    match = match_fields(old_fields, new_fields)
    result = judge(match["pairs"], old_parts, new_parts)
    return match, result


def test_full_when_identical():
    _, res = levels_for([f(1, "a"), f(2, "b")], [f(1, "a"), f(2, "b")])
    assert res["level"] == "full"
    assert res["directions"]["backward"]["state"] == "compatible"
    assert res["directions"]["forward"]["state"] == "compatible"


def test_backward_when_decimal_widened():
    old = [f(1, "amount", "decimal(10,2)", nullable=False)]
    new = [f(1, "amount", "decimal(18,2)", nullable=False)]
    _, res = levels_for(old, new)
    assert res["level"] == "backward"
    assert res["directions"]["backward"]["state"] == "compatible"
    assert res["directions"]["forward"]["state"] == "blocked"
    assert any(r["code"] == "type_blocked" for r in res["reasons"])


def test_forward_when_required_column_added():
    _, res = levels_for([f(1, "a")], [f(1, "a"), f(2, "b", nullable=False, default=None)])
    assert res["level"] == "forward"
    assert res["directions"]["backward"]["state"] == "blocked"
    assert any(r["code"] == "field_added_required" for r in res["reasons"])


def test_none_when_removed_and_narrowed():
    old = [f(1, "a"), f(2, "status"), f(3, "weight", "int64", nullable=False)]
    new = [f(1, "a"), f(3, "weight", "int32", nullable=False)]
    _, res = levels_for(old, new)
    assert res["level"] == "none"
    codes = {r["code"] for r in res["reasons"] if r["severity"] == "block"}
    assert "field_removed" in codes and "type_blocked" in codes


def test_partition_type_change_blocks_both_directions():
    old = [f(1, "a"), f(5, "dt", "date", nullable=False, partition=True)]
    new = [f(1, "a"), f(5, "dt", "timestamp", nullable=False, partition=True)]
    _, res = levels_for(old, new, ["dt"], ["dt"])
    assert res["level"] == "none"
    assert res["directions"]["backward"]["state"] == "blocked"
    assert res["directions"]["forward"]["state"] == "blocked"
    assert any(r["code"] == "partition_type_changed" for r in res["reasons"])


def test_partition_removed_blocks_both_directions():
    old = [f(1, "a"), f(5, "dt", "date", nullable=False, partition=True)]
    new = [f(1, "a")]
    _, res = levels_for(old, new, ["dt"], [])
    assert res["level"] == "none"
    assert any(r["code"] == "partition_removed" for r in res["reasons"])


def test_id_changed_marks_unknown_and_manual_confirmation():
    old = [f(6, "carrier")]
    new = [f(9, "carrier")]
    _, res = levels_for(old, new)
    assert res["level"] == "none"
    assert res["directions"]["backward"]["state"] == "unknown"
    assert len(res["manual_confirm"]) == 2  # 两个方向各一条


def test_rename_only_stays_full():
    old = [f(4, "channel")]
    new = [f(4, "pay_channel")]
    _, res = levels_for(old, new)
    assert res["level"] == "full"
    assert any(r["code"] == "renamed" for r in res["reasons"])


def test_added_nullable_column_keeps_backward():
    old = [f(1, "a")]
    new = [f(1, "a"), f(2, "b", nullable=True)]
    _, res = levels_for(old, new)
    assert res["level"] == "full"
