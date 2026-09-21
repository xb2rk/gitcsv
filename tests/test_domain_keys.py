"""Key inference + mapping oracle (ported intent from v1 test_mapping)."""

from fccsv.domain.keys import (
    auto_map_tabs_to_csvs,
    escape_tab,
    infer_key_column,
    suggest_rename,
)


def test_infer_single_id_like() -> None:
    rows = [{"ShopId": "1"}, {"ShopId": "2"}]
    assert infer_key_column(["ShopId", "Name"], rows) == "ShopId"


def test_infer_composite_prefers_secondary_rank() -> None:
    headers = ["ShopId", "SlotIndex", "Name"]
    rows = [
        {"ShopId": "1", "SlotIndex": "0", "Name": "a"},
        {"ShopId": "1", "SlotIndex": "1", "Name": "b"},
        {"ShopId": "2", "SlotIndex": "0", "Name": "c"},
    ]
    assert infer_key_column(headers, rows) == "ShopId,SlotIndex"


def test_infer_keyless_returns_empty() -> None:
    rows = [{"Name": "a"}, {"Name": "a"}]
    assert infer_key_column(["Name", "Desc"], rows) == ""


def test_infer_no_rows_first_id_like() -> None:
    assert infer_key_column(["ShopId", "Name"]) == "ShopId"


def test_auto_map_normalized() -> None:
    matched, unmatched_tabs, unmatched_csvs = auto_map_tabs_to_csvs(
        ["Shop Data", "Other"], ["ShopData.csv", "Extra.csv"]
    )
    assert matched == [("Shop Data", "ShopData.csv")]
    assert unmatched_tabs == ["Other"]
    assert unmatched_csvs == ["Extra.csv"]


def test_suggest_rename_pairs() -> None:
    pairs = suggest_rename(["ItemData V1.5"], ["ItemData.csv", "Shop.csv"])
    assert pairs == []


def test_escape_tab_doubles_quote() -> None:
    assert escape_tab("Designer's") == "'Designer''s'"
    assert escape_tab("Shop") == "'Shop'"
