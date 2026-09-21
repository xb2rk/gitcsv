"""Validation: ragged via raw_widths, duplicates, blanks, caps."""

from typing import Any

import pytest

from fccsv.domain.errors import DataValidationError
from fccsv.domain.snapshot import TableSnapshot
from fccsv.domain.validate import ensure_valid, validate_snapshot


def _snap(**kwargs: Any) -> TableSnapshot:
    base = TableSnapshot(
        name="Shop",
        headers=["ShopId", "Name"],
        rows=[{"ShopId": "1", "Name": "a"}],
        key_column="ShopId",
        raw_widths=[2],
        line_no=[3],
    )
    for key, value in kwargs.items():
        setattr(base, key, value)
    return base


def test_valid_snapshot_clean() -> None:
    assert validate_snapshot(_snap()) == []


def test_ragged_detected_from_raw_widths_not_padded_dict() -> None:
    snap = _snap(raw_widths=[1])  # short row padded to dict of 2 in loader
    issues = validate_snapshot(snap)
    assert any("ragged" in issue for issue in issues)


def test_duplicate_headers() -> None:
    snap = _snap(headers=["ShopId", "ShopId"])
    assert any("duplicate headers" in i for i in validate_snapshot(snap))


def test_duplicate_keys() -> None:
    snap = _snap(
        rows=[{"ShopId": "1", "Name": "a"}, {"ShopId": "1", "Name": "b"}],
        raw_widths=[2, 2],
        line_no=[3, 4],
    )
    assert any("duplicate keys" in i for i in validate_snapshot(snap))


def test_blank_key() -> None:
    snap = _snap(rows=[{"ShopId": "", "Name": "a"}])
    assert any("blank key" in i for i in validate_snapshot(snap))


def test_caps_enforced() -> None:
    snap = _snap(rows=[{"ShopId": str(i), "Name": "a"} for i in range(5)])
    assert any("too many rows" in i for i in validate_snapshot(snap, max_rows=2))


def test_ensure_valid_raises() -> None:
    with pytest.raises(DataValidationError):
        ensure_valid(_snap(headers=[]))
