"""类型提升矩阵：兼容 / 阻断 / unknown 全量覆盖。"""
import pytest

from app.types import BLOCKED, PROMOTIONS, TypeParseError, base_type, matrix_table, normalize_type, promotion

ALL_BASE = ["int8", "int16", "int32", "int64", "float", "double", "string", "boolean", "date", "timestamp"]


def test_identity_is_compatible_for_every_type():
    for name in ALL_BASE + ["decimal(10,2)"]:
        res = promotion(name, name)
        assert res["verdict"] == "compatible"
        assert "同类型" in res["reason"]


def test_all_explicit_promotions_are_compatible():
    assert len(PROMOTIONS) >= 7
    for (src, dst) in PROMOTIONS:
        res = promotion(src, dst)
        assert res["verdict"] == "compatible", (src, dst, res)
        assert res["rule"] == "explicit"


def test_all_explicit_blocks_are_blocked():
    for (src, dst) in BLOCKED:
        res = promotion(src, dst)
        assert res["verdict"] == "blocked", (src, dst, res)


@pytest.mark.parametrize("pair", [
    ("string", "double"), ("timestamp", "date"), ("boolean", "int32"),
    ("decimal(10,2)", "double"), ("int64", "decimal(10,2)"), ("float", "int32"),
])
def test_pairs_outside_matrix_are_unknown(pair):
    res = promotion(*pair)
    assert res["verdict"] == "unknown"
    assert res["rule"] == "unknown"
    assert "不猜测" in res["reason"]


def test_decimal_precision_up_is_compatible():
    assert promotion("decimal(10,2)", "decimal(18,2)")["verdict"] == "compatible"
    assert promotion("decimal(10,2)", "decimal(10,2)")["verdict"] == "compatible"


def test_decimal_precision_down_is_blocked():
    assert promotion("decimal(18,2)", "decimal(10,2)")["verdict"] == "blocked"


def test_decimal_scale_change_is_unknown():
    res = promotion("decimal(10,2)", "decimal(12,3)")
    assert res["verdict"] == "unknown"


def test_normalize_aliases_and_case():
    assert normalize_type("BIGINT") == "int64"
    assert normalize_type(" Long ") == "int64"
    assert normalize_type("DECIMAL(10,2)") == "decimal(10,2)"
    assert normalize_type("Varchar") == "string"
    assert base_type("decimal(10,2)") == "decimal"


@pytest.mark.parametrize("bad", ["money", "", "decimal", "decimal(0,2)", "decimal(10,11)"])
def test_invalid_type_names_raise(bad):
    with pytest.raises(TypeParseError):
        normalize_type(bad)


def test_matrix_table_lists_both_verdicts():
    rows = matrix_table()
    verdicts = {r["verdict"] for r in rows}
    assert verdicts == {"compatible", "blocked"}
    assert len(rows) == len(PROMOTIONS) + len(BLOCKED)
