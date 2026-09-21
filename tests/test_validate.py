import pytest

from fccsv.domain.exceptions import DataValidationError
from fccsv.domain.models import TableSnapshot
from fccsv.domain.validate import ensure_valid, find_duplicate_keys, validate_snapshot


def _snap(headers=("Id", "Name"), rows=(), key="Id"):
    return TableSnapshot(
        name="T",
        headers=list(headers),
        types=["int"] * len(headers),
        rows=[dict(r) for r in rows],
        key_column=key,
    )


def test_valid_table_has_no_issues():
    snap = _snap(rows=[{"Id": "1", "Name": "a"}])
    assert validate_snapshot(snap) == []


def test_duplicate_keys_reported():
    snap = _snap(rows=[{"Id": "1", "Name": "a"}, {"Id": "1", "Name": "b"}])
    assert find_duplicate_keys(snap) == ["1"]
    assert any("duplicate keys" in i for i in validate_snapshot(snap))


def test_duplicate_headers_reported():
    snap = _snap(headers=["Id", "Id"], rows=[])
    assert any("duplicate headers" in i for i in validate_snapshot(snap))


def test_blank_keys_reported():
    snap = _snap(rows=[{"Id": "", "Name": "a"}])
    assert any("blank key" in i for i in validate_snapshot(snap))


def test_ensure_valid_raises():
    snap = _snap(rows=[{"Id": "1", "Name": "a"}, {"Id": "1", "Name": "b"}])
    with pytest.raises(DataValidationError):
        ensure_valid(snap)
