"""Schema guard: any header add/remove/reorder blocks without explicit approval."""

import pytest

from fccsv.domain.errors import SchemaChangeBlocked
from fccsv.domain.schema import ensure_schema_compatible
from fccsv.domain.snapshot import TableSnapshot


def test_compatible_passes() -> None:
    local = TableSnapshot(name="T", headers=["A", "B"])
    remote = TableSnapshot(name="T", headers=["A", "B"])
    ensure_schema_compatible(local, remote)


def test_added_blocked() -> None:
    local = TableSnapshot(name="T", headers=["A"])
    remote = TableSnapshot(name="T", headers=["A", "B"])
    with pytest.raises(SchemaChangeBlocked):
        ensure_schema_compatible(local, remote)


def test_reorder_blocked() -> None:
    local = TableSnapshot(name="T", headers=["A", "B"])
    remote = TableSnapshot(name="T", headers=["B", "A"])
    with pytest.raises(SchemaChangeBlocked):
        ensure_schema_compatible(local, remote)


def test_allow_flag_passes() -> None:
    local = TableSnapshot(name="T", headers=["A"])
    remote = TableSnapshot(name="T", headers=["A", "B"])
    ensure_schema_compatible(local, remote, allow_schema_change=True)
