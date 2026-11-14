"""字段匹配：id 优先、名称兜底、改名/ID 变化/增删。"""
from app.matching import match_fields


def f(fid, name, type_="string", nullable=True, default=None, partition=False):
    return {"id": fid, "name": name, "type": type_, "nullable": nullable, "default": default, "partition": partition}


def pairs_of(res):
    return {p["name"]: p for p in res["pairs"]}


def test_same_id_same_name_is_unchanged():
    res = match_fields([f(1, "order_id")], [f(1, "order_id")])
    assert res["counts"]["unchanged"] == 1
    assert res["pairs"][0]["basis"] == "id"


def test_same_id_different_name_is_renamed():
    res = match_fields([f(4, "channel")], [f(4, "pay_channel")])
    p = res["pairs"][0]
    assert p["kind"] == "renamed"
    assert p["old_name"] == "channel" and p["new_name"] == "pay_channel"
    assert res["counts"]["renamed"] == 1


def test_same_name_different_id_is_id_changed_and_needs_confirmation():
    res = match_fields([f(6, "carrier")], [f(9, "carrier")])
    p = res["pairs"][0]
    assert p["kind"] == "id_changed"
    assert p["basis"] == "name"
    assert p["field_id"] == 9
    assert "field_id 6→9" in p["note"]
    assert res["counts"]["id_changed"] == 1


def test_id_match_wins_over_name_match():
    # 旧: id=1 name=amount, id=2 name=fee；新: id=1 name=fee
    # id=1 先命中（amount→fee 改名），旧 id=2 按名称抢不到已被占用的 fee → removed
    res = match_fields([f(1, "amount"), f(2, "fee")], [f(1, "fee")])
    renamed = [p for p in res["pairs"] if p["kind"] == "renamed"]
    removed = [p for p in res["pairs"] if p["kind"] == "removed"]
    assert len(renamed) == 1
    assert renamed[0]["old_name"] == "amount"
    assert renamed[0]["new_name"] == "fee"
    assert renamed[0]["basis"] == "id"
    assert len(removed) == 1 and removed[0]["old_name"] == "fee"
    assert res["counts"]["renamed"] == 1
    assert res["counts"]["removed"] == 1


def test_no_id_falls_back_to_name_with_basis():
    res = match_fields(
        [{"id": None, "name": "dt", "type": "date", "nullable": False, "default": None, "partition": True}],
        [{"id": None, "name": "dt", "type": "date", "nullable": False, "default": None, "partition": True}],
    )
    p = res["pairs"][0]
    assert p["kind"] == "unchanged"
    assert p["basis"] == "name"
    assert "按名称匹配" in p["note"]


def test_added_and_removed_fields():
    res = match_fields([f(1, "a"), f(3, "status")], [f(1, "a"), f(7, "fee")])
    kinds = {p["name"]: p["kind"] for p in res["pairs"]}
    assert kinds == {"a": "unchanged", "status": "removed", "fee": "added"}
    assert res["counts"]["added"] == 1
    assert res["counts"]["removed"] == 1


def test_type_change_carries_matrix_verdict():
    res = match_fields([f(4, "weight_gram", "int64")], [f(4, "weight_gram", "int32")])
    p = res["pairs"][0]
    assert p["kind"] == "type_changed"
    assert p["type_result"]["verdict"] == "blocked"
    assert res["counts"]["type_changed"] == 1


def test_renamed_and_type_changed_combine():
    res = match_fields([f(4, "amount", "decimal(10,2)")], [f(4, "amt", "decimal(18,2)")])
    p = res["pairs"][0]
    assert p["kind"] == "renamed"
    assert p["type_changed"] is True
    assert p["type_result"]["verdict"] == "compatible"
    assert res["counts"]["type_changed"] == 1


def test_counts_are_consistent_and_inputs_not_mutated():
    old = [f(1, "a"), f(2, "b")]
    new = [f(1, "a2"), f(5, "c")]
    res = match_fields(old, new)
    assert old[1]["name"] == "b" and new[1]["name"] == "c"
    paired = sum(1 for p in res["pairs"] if p["kind"] != "added" and p["kind"] != "removed")
    assert paired + res["counts"]["added"] + res["counts"]["removed"] == len(res["pairs"])
