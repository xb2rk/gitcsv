"""Cell-level 3-way merge: the core v2 upgrade over v1 table-level ConflictError."""

from fccsv.domain.merge3 import both_changed_since_base, merge3
from fccsv.domain.snapshot import TableSnapshot


def _snap(rows: list[dict[str, str]]) -> TableSnapshot:
    return TableSnapshot(
        name="Shop", headers=["ShopId", "Name"], rows=[dict(r) for r in rows], key_column="ShopId"
    )


def test_merge_takes_single_side_change() -> None:
    base = _snap([{"ShopId": "1", "Name": "a"}])
    local = _snap([{"ShopId": "1", "Name": "a"}])
    remote = _snap([{"ShopId": "1", "Name": "b"}])
    result = merge3(base, local, remote)
    assert not result.has_conflict
    assert result.merged.rows == [{"ShopId": "1", "Name": "b"}]


def test_merge_conflict_same_cell_both_changed() -> None:
    base = _snap([{"ShopId": "1", "Name": "a"}])
    local = _snap([{"ShopId": "1", "Name": "x"}])
    remote = _snap([{"ShopId": "1", "Name": "y"}])
    result = merge3(base, local, remote)
    assert result.has_conflict
    assert result.conflicts[0].column == "Name"
    assert result.merged.rows[0]["Name"] == "x"  # local wins in merged view


def test_merge_adds_both_sides() -> None:
    base = _snap([{"ShopId": "1", "Name": "a"}])
    local = _snap([{"ShopId": "1", "Name": "a"}, {"ShopId": "2", "Name": "b"}])
    remote = _snap([{"ShopId": "1", "Name": "a"}, {"ShopId": "3", "Name": "c"}])
    result = merge3(base, local, remote)
    assert not result.has_conflict
    assert sorted(r["ShopId"] for r in result.merged.rows) == ["1", "2", "3"]


def test_merge_positional_keyless() -> None:
    base = TableSnapshot(name="T", headers=["Name"], rows=[{"Name": "a"}])
    local = TableSnapshot(name="T", headers=["Name"], rows=[{"Name": "a"}])
    remote = TableSnapshot(name="T", headers=["Name"], rows=[{"Name": "b"}])
    result = merge3(base, local, remote)
    assert result.merged.rows == [{"Name": "b"}]


def test_both_changed_falls_back_to_hashes_without_base() -> None:
    local = _snap([{"ShopId": "1", "Name": "x"}])
    remote = _snap([{"ShopId": "1", "Name": "y"}])
    assert (
        both_changed_since_base(
            None, local, remote, "hL0", "hR0", local_hash="hL1", remote_hash="hR1"
        )
        is True
    )
    assert (
        both_changed_since_base(
            None, local, remote, "hL0", "hR0", local_hash="hL0", remote_hash="hR1"
        )
        is False
    )
