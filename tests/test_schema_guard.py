import pytest

from fccsv.domain.exceptions import SchemaChangeBlocked
from fccsv.domain.models import TableSnapshot
from fccsv.domain.schema_guard import ensure_schema_compatible, validate_row_width


def _snap(headers):
    return TableSnapshot(name="T", headers=list(headers), key_column=headers[0])


def test_blocks_add_column_by_default():
    with pytest.raises(SchemaChangeBlocked):
        ensure_schema_compatible(_snap(["Id", "A"]), _snap(["Id", "A", "B"]))


def test_allows_with_flag():
    ensure_schema_compatible(_snap(["Id", "A"]), _snap(["Id", "A", "B"]), allow_schema_change=True)


def test_row_width_reports_bad_rows():
    snap = TableSnapshot(
        name="T",
        headers=["Id", "A"],
        rows=[{"Id": "1", "A": "x"}, {"Id": "2"}],
        key_column="Id",
    )
    assert validate_row_width(snap) == [1]
