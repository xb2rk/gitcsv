"""Conflict detection: both-sides-changed blocks without --force."""

import pytest

from fccsv.adapters import local_store
from fccsv.adapters.sheets_gateway import InMemorySheetsGateway
from fccsv.app import sync_service
from fccsv.core.config import FccsvConfig, TableMapping
from fccsv.domain.exceptions import ConflictError
from fccsv.domain.models import TableSnapshot


def _setup(tmp_path):
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    local_store.write_csv_table(
        csv_dir / "ShopData.csv",
        TableSnapshot(
            name="ShopData",
            headers=["ShopId", "Name"],
            types=["int", "string"],
            rows=[{"ShopId": "1", "Name": "Basic"}],
            key_column="ShopId",
        ),
    )
    cfg = FccsvConfig(
        spreadsheet_id="x",
        csv_dir=str(csv_dir),
        tables=[TableMapping(tab="ShopData", csv="ShopData.csv", key_column="ShopId")],
        state_file=".state.json",
    )
    return cfg, InMemorySheetsGateway()


def test_push_then_diverge_blocks_without_force(tmp_path):
    cfg, gw = _setup(tmp_path)
    sync_service.push_table(cfg, tmp_path, gw, "ShopData")
    # Remote changes.
    gw.write_tab(
        TableSnapshot(
            name="ShopData",
            headers=["ShopId", "Name"],
            rows=[{"ShopId": "1", "Name": "Remote"}],
            key_column="ShopId",
        )
    )
    # Local changes too.
    csv_path = tmp_path / "csv" / "ShopData.csv"
    snap = local_store.read_csv_table(csv_path)
    snap.rows[0]["Name"] = "Local"
    local_store.write_csv_table(csv_path, snap)
    with pytest.raises(ConflictError):
        sync_service.push_table(cfg, tmp_path, gw, "ShopData")
    with pytest.raises(ConflictError):
        sync_service.pull_table(cfg, tmp_path, gw, "ShopData")


def test_force_push_keeps_local(tmp_path):
    cfg, gw = _setup(tmp_path)
    sync_service.push_table(cfg, tmp_path, gw, "ShopData")
    gw.write_tab(
        TableSnapshot(
            name="ShopData",
            headers=["ShopId", "Name"],
            rows=[{"ShopId": "1", "Name": "Remote"}],
            key_column="ShopId",
        )
    )
    csv_path = tmp_path / "csv" / "ShopData.csv"
    snap = local_store.read_csv_table(csv_path)
    snap.rows[0]["Name"] = "Local"
    local_store.write_csv_table(csv_path, snap)
    msg = sync_service.push_table(cfg, tmp_path, gw, "ShopData", force="push")
    assert "pushed" in msg
    assert gw.read_tab("ShopData").rows[0]["Name"] == "Local"


def test_one_sided_change_does_not_conflict(tmp_path):
    cfg, gw = _setup(tmp_path)
    sync_service.push_table(cfg, tmp_path, gw, "ShopData")
    gw.write_tab(
        TableSnapshot(
            name="ShopData",
            headers=["ShopId", "Name"],
            rows=[{"ShopId": "1", "Name": "Remote"}, {"ShopId": "2", "Name": "New"}],
            key_column="ShopId",
        )
    )
    msg = sync_service.pull_table(cfg, tmp_path, gw, "ShopData")
    assert "pulled" in msg


def test_status_reports_conflict(tmp_path):
    cfg, gw = _setup(tmp_path)
    sync_service.push_table(cfg, tmp_path, gw, "ShopData")
    gw.write_tab(
        TableSnapshot(
            name="ShopData",
            headers=["ShopId", "Name"],
            rows=[{"ShopId": "1", "Name": "Remote"}],
            key_column="ShopId",
        )
    )
    csv_path = tmp_path / "csv" / "ShopData.csv"
    snap = local_store.read_csv_table(csv_path)
    snap.rows[0]["Name"] = "Local"
    local_store.write_csv_table(csv_path, snap)
    (status,) = sync_service.collect_status(cfg, tmp_path, gw)
    assert status.conflict is True
