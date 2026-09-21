"""Missing-tab handling: nonexistent tabs read as remote-missing, not crashes."""

from fccsv.adapters.sheets_gateway import InMemorySheetsGateway
from fccsv.app import sync_service
from fccsv.core.config import FccsvConfig, TableMapping
from fccsv.domain.exceptions import TabMissingError


class _BoomGateway(InMemorySheetsGateway):
    """Simulates the Google API 400 on an unparseable (missing) tab range."""

    def read_tab(self, tab: str):
        raise TabMissingError(tab)


def _config(tmp_path):
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    (csv_dir / "ShopData.csv").write_text("ShopId,Name\nint,string\n1,Basic\n", encoding="utf-8")
    return FccsvConfig(
        spreadsheet_id="x",
        csv_dir=str(csv_dir),
        tables=[TableMapping(tab="ShopData", csv="ShopData.csv", key_column="ShopId")],
        state_file=".state.json",
    )


def test_missing_tab_reports_local_only(tmp_path):
    cfg = _config(tmp_path)
    (status,) = sync_service.collect_status(cfg, tmp_path, _BoomGateway())
    assert status.local_only is True
    assert status.name == "ShopData"
