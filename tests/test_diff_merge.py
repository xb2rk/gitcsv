from fccsv.domain.diff_merge import diff_rows, diff_schema
from fccsv.domain.models import TableSnapshot


def _snap(name="T", headers=("Id", "Name"), rows=()):
    return TableSnapshot(
        name=name,
        headers=list(headers),
        types=["int", "string"],
        rows=[dict(r) for r in rows],
        key_column="Id",
    )


def test_schema_diff_detects_add_remove_reorder():
    local = _snap(headers=["Id", "A", "B"])
    remote = _snap(headers=["Id", "B", "C"])
    d = diff_schema(local, remote)
    assert d.added == ["C"]
    assert d.removed == ["A"]
    assert d.reordered is False  # common order [Id, B] preserved


def test_schema_reorder_flag():
    local = _snap(headers=["Id", "A", "B"])
    remote = _snap(headers=["Id", "B", "A"])
    assert diff_schema(local, remote).reordered is True


def test_row_diff_by_key():
    local = _snap(rows=[{"Id": "1", "Name": "a"}, {"Id": "2", "Name": "b"}])
    remote = _snap(rows=[{"Id": "1", "Name": "a2"}, {"Id": "3", "Name": "c"}])
    d = diff_rows(local, remote)
    assert d.added_keys == ["3"]
    assert d.removed_keys == ["2"]
    assert d.changed_keys == ["1"]
