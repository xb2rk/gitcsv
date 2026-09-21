"""Diff + translate purity (v1 diff_merge intent, plus mutation fix)."""

from fccsv.domain.diff import diff_rows, diff_schema, filter_snapshot, translate_snapshot
from fccsv.domain.snapshot import TableSnapshot


def _table(rows: list[dict[str, str]]) -> TableSnapshot:
    return TableSnapshot(
        name="Shop", headers=["ShopId", "Name"], rows=[dict(r) for r in rows], key_column="ShopId"
    )


def test_diff_schema_added_removed_reorder() -> None:
    local = TableSnapshot(name="T", headers=["A", "B", "C"])
    remote = TableSnapshot(name="T", headers=["B", "A", "D"])
    diff = diff_schema(local, remote)
    assert diff.added == ["D"]
    assert diff.removed == ["C"]
    assert diff.reordered is True
    assert diff.has_change is True


def test_diff_rows_keyed() -> None:
    local = _table([{"ShopId": "1", "Name": "a"}, {"ShopId": "2", "Name": "b"}])
    remote = _table([{"ShopId": "1", "Name": "a2"}, {"ShopId": "3", "Name": "c"}])
    diff = diff_rows(local, remote)
    assert diff.changed_keys == ["1"]
    assert diff.added_keys == ["3"]
    assert diff.removed_keys == ["2"]


def test_diff_rows_positional_fallback() -> None:
    local = TableSnapshot(name="T", headers=["Name"], rows=[{"Name": "a"}])
    remote = TableSnapshot(name="T", headers=["Name"], rows=[{"Name": "b"}, {"Name": "c"}])
    diff = diff_rows(local, remote)
    assert diff.changed_keys == ["0"]
    assert diff.added_keys == ["+1"]


def test_translate_empty_map_returns_same_object_no_mutation() -> None:
    snap = _table([{"ShopId": "1", "Name": "a"}])
    out = translate_snapshot(snap, {}, key_column="")
    assert out is snap
    assert snap.key_column == "ShopId"


def test_translate_renames_headers() -> None:
    snap = TableSnapshot(
        name="T", headers=["S_ID", "S_Name"], rows=[{"S_ID": "1", "S_Name": "a"}], key_column="S_ID"
    )
    out = translate_snapshot(snap, {"S_ID": "ShopId", "S_Name": "Name"}, key_column="ShopId")
    assert out.headers == ["ShopId", "Name"]
    assert out.rows == [{"ShopId": "1", "Name": "a"}]
    assert snap.headers == ["S_ID", "S_Name"]  # input untouched


def test_filter_only_and_exclude() -> None:
    snap = _table([{"ShopId": "1", "Name": "a"}, {"ShopId": "2", "Name": "b"}])
    only = filter_snapshot(snap, only_keys={"1"})
    assert [r["ShopId"] for r in only.rows] == ["1"]
    excl = filter_snapshot(snap, exclude_columns={"Name"})
    assert excl.headers == ["ShopId"]
    assert excl.rows == [{"ShopId": "1"}, {"ShopId": "2"}]
