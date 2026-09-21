from pathlib import Path

from fccsv.adapters import local_store
from fccsv.adapters.sheets_gateway import InMemorySheetsGateway
from fccsv.app import sync_service
from fccsv.core.config import FccsvConfig, TableMapping
from fccsv.domain.models import TableSnapshot


def test_local_roundtrip_preserves_types_and_lf(tmp_path: Path):
    csv_path = tmp_path / "PlantData.csv"
    snap = TableSnapshot(
        name="PlantData",
        headers=["Id", "BaseATK"],
        types=["int", "int"],
        rows=[{"Id": "20000", "BaseATK": "2"}],
        key_column="Id",
    )
    local_store.write_csv_table(csv_path, snap)
    raw = csv_path.read_bytes()
    assert b"\r\n" not in raw
    back = local_store.read_csv_table(csv_path)
    assert back.headers == ["Id", "BaseATK"]
    assert back.types == ["int", "int"]
    assert back.rows == [{"Id": "20000", "BaseATK": "2"}]


def test_push_then_pull_roundtrip(tmp_path: Path):
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
    gw = InMemorySheetsGateway()
    assert "pushed" in sync_service.push_table(cfg, tmp_path, gw, "ShopData")
    # Remote edit then pull overwrites local (with backup).
    gw.write_tab(
        TableSnapshot(
            name="ShopData",
            headers=["ShopId", "Name"],
            rows=[{"ShopId": "1", "Name": "Edited"}],
            key_column="ShopId",
        )
    )
    assert "pulled" in sync_service.pull_table(cfg, tmp_path, gw, "ShopData")
    assert local_store.read_csv_table(csv_dir / "ShopData.csv").rows == [
        {"ShopId": "1", "Name": "Edited"}
    ]
    assert (tmp_path / ".state.json").exists()
