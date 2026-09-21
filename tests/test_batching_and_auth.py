"""Quota-saving batch paths and OAuth client path resolution."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fccsv.adapters import local_store
from fccsv.adapters.sheets_gateway import GoogleSheetsGateway, InMemorySheetsGateway
from fccsv.app import sync_service
from fccsv.app.auth_service import load_credentials, resolve_auth_paths
from fccsv.core.config import FccsvConfig, TableMapping
from fccsv.domain.exceptions import QuotaExceededError
from fccsv.domain.models import TableSnapshot


class CountingGateway(InMemorySheetsGateway):
    def __init__(self, tabs: dict[str, TableSnapshot] | None = None) -> None:
        super().__init__(tabs)
        self.list_calls = 0
        self.read_batches: list[list[str]] = []
        self.write_batches: list[list[str]] = []
        self.drive_time: str | None = "initial"

    def list_tabs(self) -> list[str]:
        self.list_calls += 1
        return super().list_tabs()

    def read_tabs(self, tabs: list[str]) -> dict[str, TableSnapshot]:
        self.read_batches.append(list(tabs))
        return super().read_tabs(tabs)

    def write_tabs(self, snapshots: list[TableSnapshot]) -> None:
        self.write_batches.append([snapshot.name for snapshot in snapshots])
        super().write_tabs(snapshots)

    def drive_modified_time(self) -> str | None:
        return self.drive_time


def _config(tmp_path: Path) -> FccsvConfig:
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    for tab in ("Plants", "Zombies"):
        local_store.write_csv_table(
            csv_dir / f"{tab}.csv",
            TableSnapshot(
                name=tab,
                headers=["Id", "Name"],
                types=["int", "string"],
                rows=[{"Id": "1", "Name": tab}],
                key_column="Id",
            ),
        )
    return FccsvConfig(
        spreadsheet_id="sheet",
        csv_dir=str(csv_dir),
        tables=[
            TableMapping(tab="Plants", csv="Plants.csv", key_column="Id"),
            TableMapping(tab="Zombies", csv="Zombies.csv", key_column="Id"),
        ],
        state_file=".state.json",
    )


def test_push_all_writes_metadata_and_skips_clean_tables(tmp_path: Path) -> None:
    config = _config(tmp_path)
    gateway = CountingGateway()

    oks, errs = sync_service.push_all(config, tmp_path, gateway, ["Plants", "Zombies"])

    assert not errs
    assert len(oks) == 2
    assert gateway.write_batches == [["Plants", "Zombies", sync_service.METADATA_TAB]]
    assert set(gateway.read_tab(sync_service.METADATA_TAB).rows[0]) == {"tab", "hash"}

    gateway.read_batches.clear()
    gateway.write_batches.clear()
    oks, errs = sync_service.push_all(config, tmp_path, gateway, ["Plants", "Zombies"])

    assert not errs
    assert oks == ["in-sync Plants.csv -> Plants", "in-sync Zombies.csv -> Zombies"]
    assert gateway.write_batches == []
    assert gateway.read_batches == []


def test_push_all_repairs_missing_metadata_without_dropping_other_tables(tmp_path: Path) -> None:
    config = _config(tmp_path)
    gateway = CountingGateway()
    sync_service.push_all(config, tmp_path, gateway, ["Plants", "Zombies"])
    expected = {row["tab"]: row["hash"] for row in gateway.read_tab(sync_service.METADATA_TAB).rows}
    gateway.tabs.pop(sync_service.METADATA_TAB)
    local_store.write_csv_table(
        tmp_path / "csv" / "Plants.csv",
        TableSnapshot(
            name="Plants",
            headers=["Id", "Name"],
            types=["int", "string"],
            rows=[{"Id": "1", "Name": "Updated"}],
            key_column="Id",
        ),
    )

    _, errs = sync_service.push_all(config, tmp_path, gateway, ["Plants"])

    assert not errs
    repaired = {row["tab"]: row["hash"] for row in gateway.read_tab(sync_service.METADATA_TAB).rows}
    assert repaired["Zombies"] == expected["Zombies"]
    assert set(repaired) == {"Plants", "Zombies"}


def test_drive_tier_avoids_data_reads_and_remote_only_status_is_safe(tmp_path: Path) -> None:
    config = _config(tmp_path)
    gateway = CountingGateway()
    sync_service.push_all(config, tmp_path, gateway, ["Plants", "Zombies"])

    gateway.read_batches.clear()
    statuses = sync_service.collect_status(config, tmp_path, gateway)

    assert all(status.in_sync for status in statuses)
    assert gateway.read_batches == []

    # An old OAuth grant has no Drive scope. `_fccsv` cannot prove a normal
    # tab was not manually edited, so status safely makes one batch data read.
    gateway.drive_time = None
    gateway.read_batches.clear()
    statuses = sync_service.collect_status(config, tmp_path, gateway)
    assert all(status.in_sync for status in statuses)
    assert gateway.read_batches == [["Plants", "Zombies"]]

    (tmp_path / "csv" / "Zombies.csv").unlink()
    statuses = sync_service.collect_status(config, tmp_path, gateway)
    assert statuses[1].remote_only is True


def test_pull_all_refreshes_stale_metadata(tmp_path: Path) -> None:
    config = _config(tmp_path)
    gateway = CountingGateway()
    sync_service.push_all(config, tmp_path, gateway, ["Plants", "Zombies"])
    gateway.tabs.pop(sync_service.METADATA_TAB)
    gateway.write_tab(
        TableSnapshot(
            name="Plants",
            headers=["Id", "Name"],
            rows=[{"Id": "1", "Name": "Changed"}],
            key_column="Id",
        )
    )
    gateway.drive_time = "changed"

    oks, errs = sync_service.pull_all(config, tmp_path, gateway, ["Plants", "Zombies"])

    assert not errs
    assert "pulled Plants -> Plants.csv (1 rows)" in oks
    assert gateway.write_batches[-1] == [sync_service.METADATA_TAB]
    assert {row["tab"] for row in gateway.read_tab(sync_service.METADATA_TAB).rows} == {
        "Plants",
        "Zombies",
    }
    gateway.read_batches.clear()
    assert all(status.in_sync for status in sync_service.collect_status(config, tmp_path, gateway))
    assert gateway.read_batches == []


class _Request:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def execute(self):
        if self.error is not None:
            raise self.error
        return self.result


class _FakeSheetsService:
    def __init__(self) -> None:
        self.tabs = ["First"]
        self.get_calls = 0

    def spreadsheets(self):
        return self

    def get(self, **_kwargs):
        self.get_calls += 1
        return _Request({"sheets": [{"properties": {"title": tab}} for tab in self.tabs]})

    def batchUpdate(self, *, body, **_kwargs):
        for request in body["requests"]:
            self.tabs.append(request["addSheet"]["properties"]["title"])
        return _Request({})


def _google_gateway(service: _FakeSheetsService) -> GoogleSheetsGateway:
    gateway = object.__new__(GoogleSheetsGateway)
    gateway._service = service
    gateway._spreadsheet_id = "sheet"
    gateway._tabs_cache = None
    gateway._execute = lambda request, **_kwargs: request.execute()
    return gateway


def test_tab_cache_is_reused_and_invalidated_after_ensure() -> None:
    service = _FakeSheetsService()
    gateway = _google_gateway(service)

    assert gateway.list_tabs() == ["First"]
    assert gateway.list_tabs() == ["First"]
    assert service.get_calls == 1

    gateway.ensure_tab("Second")
    assert gateway.list_tabs() == ["First", "Second"]
    assert service.get_calls == 2


def test_rate_limit_reason_raises_without_retry() -> None:
    payload = {"error": {"details": [{"reason": "RATE_LIMIT_EXCEEDED"}]}}
    error = RuntimeError("quota")
    error.content = json.dumps(payload).encode()  # type: ignore[attr-defined]
    error.resp = type("Response", (), {"status": 403})()  # type: ignore[attr-defined]
    gateway = object.__new__(GoogleSheetsGateway)

    with pytest.raises(QuotaExceededError):
        gateway._execute(_Request(error=error), what="quota-test")


def test_missing_drive_timestamp_hook_skips_the_fast_path() -> None:
    assert sync_service._drive_stamp(object()) is None


def test_auth_paths_prefer_flag_then_env_then_root_then_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    cwd = tmp_path / "cwd"
    root.mkdir()
    cwd.mkdir()
    (root / "client.json").write_text("{}", encoding="utf-8")
    (cwd / "token.json").write_text("{}", encoding="utf-8")
    explicit = tmp_path / "explicit.json"
    monkeypatch.chdir(cwd)

    credentials, token = resolve_auth_paths(
        root, "client.json", "token.json", credentials=str(explicit)
    )
    assert credentials == explicit
    assert token == cwd / "token.json"

    env_client = tmp_path / "env-client.json"
    monkeypatch.setenv("FCCSV_CREDENTIALS", str(env_client))
    credentials, _ = resolve_auth_paths(root, "client.json", "token.json")
    assert credentials == env_client


def test_existing_token_refreshes_using_its_recorded_scopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adding Drive scope must not invalidate a teammate's existing grant."""
    from google.oauth2.credentials import Credentials

    token_path = tmp_path / "token.json"
    token_path.write_text(
        json.dumps(
            {
                "token": "expired",
                "expiry": "2000-01-01T00:00:00Z",
                "refresh_token": "refresh",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "client",
                "client_secret": "secret",
                "scopes": ["https://www.googleapis.com/auth/spreadsheets"],
            }
        ),
        encoding="utf-8",
    )

    def refresh(self, _request) -> None:
        self.token = "fresh"
        self.expiry = datetime(2099, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(Credentials, "refresh", refresh)
    creds = load_credentials(token_path, tmp_path / "unused.json", ["new-drive-scope"])

    assert creds.scopes == ["https://www.googleapis.com/auth/spreadsheets"]
    assert creds.token == "fresh"
