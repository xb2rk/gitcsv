"""New fault-matrix and guarantee tests (no v1 equivalent).

Dry-run purity, idempotency, corrupt-state quarantine, metadata fallback,
fetch fast path, partial --only/--exclude, remote-set lineage reset,
rename guard, BOM/CRLF, type-row preservation, backup rotation, doctor,
auth path resolution.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fccsv.adapters import backup as backup_mod
from fccsv.adapters import base as base_mod
from fccsv.adapters import local as local_mod
from fccsv.adapters import state as state_mod
from fccsv.adapters.sheets import InMemorySheetsGateway, spreadsheet_id_from_url
from fccsv.domain.snapshot import TableSnapshot
from fccsv.app import auth as auth_mod
from fccsv.app import doctor as doctor_mod
from fccsv.app import remote as remote_mod
from fccsv.app.fetch import fetch, read_metadata_hashes
from fccsv.app.finalize import commit_pull, commit_push, write_metadata_tab
from fccsv.app.sync import apply_pull, apply_push, plan_one
from fccsv.core.config import FccsvConfig, SourceConfig, TableMapping
from fccsv.core.constants import DEFAULT_CREDENTIALS_FILENAME
from fccsv.domain.errors import DataValidationError

HEADERS = ["Id", "Name", "Price"]
TYPES = ["number", "text", "number"]
ROWS = [["1", "Apple", "100"], ["2", "Banana", "50"]]


def _write_csv(path: Path, headers: list[str], types: list[str], rows: list[list[str]]) -> None:
    text = "\n".join([",".join(headers), ",".join(types), *(",".join(r) for r in rows)]) + "\n"
    path.write_text(text, encoding="utf-8")


def _sheet_tab(tab: str, headers: list[str], rows: list[list[str]]) -> TableSnapshot:
    return TableSnapshot(
        name=tab,
        headers=list(headers),
        types=[],
        rows=[dict(zip(headers, r, strict=True)) for r in rows],
        key_column="Id",
    )


def _make_project(tmp_path: Path) -> Any:
    csv_dir = tmp_path / "Assets" / "CSV"
    csv_dir.mkdir(parents=True)
    _write_csv(csv_dir / "Items.csv", HEADERS, TYPES, ROWS)
    mappings = [TableMapping(tab="Items", csv="Items.csv", key_column="Id")]
    base_dir = tmp_path / ".fccsv" / "base"
    base_dir.mkdir(parents=True)
    backup_root = tmp_path / ".fccsv" / "backups"
    backup_root.mkdir(parents=True)
    return SimpleNamespace(
        csv_dir=csv_dir,
        mappings=mappings,
        gateway=InMemorySheetsGateway(),
        state=state_mod.SyncState(),
        state_file=tmp_path / ".fccsv-state.json",
        base_dir=base_dir,
        backup_root=backup_root,
    )


def _plan(proj: Any) -> Any:
    return plan_one(
        proj.mappings[0],
        csv_dir=proj.csv_dir,
        gateway=proj.gateway,
        state=proj.state,
        base_dir=proj.base_dir,
    )


def _seed(proj: Any) -> None:
    plan = _plan(proj)
    outcome, new_remote = apply_push(plan, proj.gateway)
    assert outcome.applied
    assert new_remote is not None and plan.local is not None
    commit_push(
        proj.state, proj.base_dir, name="Items", local_snapshot=plan.local, new_remote=new_remote
    )
    state_mod.save_state(proj.state_file, proj.state)
    write_metadata_tab(proj.gateway, proj.state, ["Items"])


def test_push_dry_run_writes_nothing(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    _seed(proj)
    before = state_mod.snapshot_hash(proj.gateway.read_tab("Items"))
    _write_csv(
        proj.csv_dir / "Items.csv", HEADERS, TYPES, [["1", "Apple", "150"], ["2", "Banana", "50"]]
    )
    outcome, new_remote = apply_push(_plan(proj), proj.gateway, dry_run=True)
    assert outcome.applied is False
    assert new_remote is None
    assert "would push" in outcome.message
    assert state_mod.snapshot_hash(proj.gateway.read_tab("Items")) == before


def test_pull_dry_run_writes_nothing(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    _seed(proj)
    csv_before = (proj.csv_dir / "Items.csv").read_text(encoding="utf-8")
    proj.gateway.write_tab(
        _sheet_tab("Items", HEADERS, [["1", "Apple", "120"], ["2", "Banana", "50"]])
    )
    outcome, merged = apply_pull(
        _plan(proj), proj.gateway, csv_dir=proj.csv_dir, backup_root=proj.backup_root, dry_run=True
    )
    assert outcome.applied is False
    assert merged is None
    assert (proj.csv_dir / "Items.csv").read_text(encoding="utf-8") == csv_before
    assert list(proj.backup_root.iterdir()) == []


def test_corrupt_state_quarantined_not_swallowed(tmp_path: Path) -> None:
    state_file = tmp_path / ".fccsv-state.json"
    state_file.write_text("{not valid json", encoding="utf-8")
    state = state_mod.load_state(state_file)
    assert state.tables == {}
    quarantined = list(tmp_path.glob(".fccsv-state.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "{not valid json"


def test_missing_metadata_tab_falls_back_to_hashing(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    plan = _plan(proj)
    outcome, new_remote = apply_push(plan, proj.gateway)
    assert outcome.applied and new_remote is not None and plan.local is not None
    commit_push(
        proj.state, proj.base_dir, name="Items", local_snapshot=plan.local, new_remote=new_remote
    )
    # No write_metadata_tab: sheet carries no _fccsv tab.
    result = fetch(proj.mappings, proj.gateway, proj.state)
    assert result.tables[0].changed is False
    proj.gateway.write_tab(
        _sheet_tab("Items", HEADERS, [["1", "Apple", "120"], ["2", "Banana", "50"]])
    )
    result = fetch(proj.mappings, proj.gateway, proj.state)
    assert result.tables[0].changed is True
    assert result.any_changed is True


def test_foreign_metadata_shape_ignored(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    proj.gateway.write_tab(
        TableSnapshot(
            name="_fccsv", headers=["designer", "note"], rows=[{"designer": "x", "note": "y"}]
        )
    )
    assert read_metadata_hashes(proj.gateway) == {}


def test_fetch_stamp_fast_path_skips_reads(tmp_path: Path) -> None:
    class StampedGateway(InMemorySheetsGateway):
        def __init__(self, stamp: str | None) -> None:
            super().__init__()
            self.stamp = stamp

        def drive_modified_time(self) -> str | None:
            return self.stamp

    proj = _make_project(tmp_path)
    proj.gateway = StampedGateway("S1")
    proj.state.modified_time = "S1"
    result = fetch(proj.mappings, proj.gateway, proj.state)
    assert result.stamp_match is True
    assert result.tables == []

    proj.gateway.stamp = "S2"
    result = fetch(proj.mappings, proj.gateway, proj.state)
    assert result.stamp_match is False
    assert len(result.tables) == 1  # falls through to hashing; local-only table changed=True


def test_fetch_detects_remote_change_via_metadata(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    _seed(proj)
    result = fetch(proj.mappings, proj.gateway, proj.state)
    assert result.any_changed is False
    proj.gateway.write_tab(
        _sheet_tab("Items", HEADERS, [["1", "Apple", "120"], ["2", "Banana", "50"]])
    )
    result = fetch(proj.mappings, proj.gateway, proj.state)
    assert result.tables[0].changed is True


def test_partial_push_only_selected_keys(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    _seed(proj)
    _write_csv(
        proj.csv_dir / "Items.csv", HEADERS, TYPES, [["1", "Apple", "150"], ["2", "Banana", "60"]]
    )
    outcome, _ = apply_push(_plan(proj), proj.gateway, only_keys={"1"})
    assert outcome.applied
    assert "pushed" in outcome.message
    rows = {r["Id"]: r for r in proj.gateway.read_tab("Items").rows}
    assert rows["1"]["Price"] == "150"
    assert rows["2"]["Price"] == "50"


def test_partial_pull_exclude_columns(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    _seed(proj)
    proj.gateway.write_tab(
        _sheet_tab("Items", HEADERS, [["1", "Apricot", "120"], ["2", "Banana", "50"]])
    )
    plan = _plan(proj)
    outcome, merged = apply_pull(
        plan,
        proj.gateway,
        csv_dir=proj.csv_dir,
        backup_root=proj.backup_root,
        exclude_columns={"Price"},
    )
    assert outcome.applied
    assert merged is not None and plan.remote is not None
    commit_pull(proj.state, proj.base_dir, name="Items", merged=merged, remote_snapshot=plan.remote)
    snap = local_mod.read_csv_table(proj.csv_dir / "Items.csv")
    by_id = {r["Id"]: r for r in snap.rows}
    assert by_id["1"]["Name"] == "Apricot"
    assert by_id["1"]["Price"] == "100"


def test_remote_set_resets_lineage(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    _seed(proj)
    assert proj.state_file.exists()
    config = FccsvConfig(
        sources=[
            SourceConfig(spreadsheet_id="old", tables=[TableMapping(tab="Items", csv="Items.csv")])
        ]
    )
    message = remote_mod.set_remote(
        config,
        "https://docs.google.com/spreadsheets/d/NEWID/edit#gid=0",
        state_file=proj.state_file,
        base_dir=proj.base_dir,
    )
    assert config.active.spreadsheet_id == "NEWID"
    assert not proj.state_file.exists()
    assert (tmp_path / ".fccsv-state.json.pre-remote-set").exists()
    assert list(proj.base_dir.iterdir()) == []
    assert "keep-local" in message
    with pytest.raises(ValueError):
        remote_mod.set_remote(
            config,
            "OTHER",
            state_file=proj.state_file,
            base_dir=proj.base_dir,
            mode="bogus",
        )
    assert spreadsheet_id_from_url("bare-id-123") == "bare-id-123"


def test_rename_table_guardrails() -> None:
    config = FccsvConfig(
        sources=[
            SourceConfig(
                spreadsheet_id="x",
                tables=[
                    TableMapping(tab="Items", csv="Items.csv"),
                    TableMapping(tab="Shop", csv="Shop.csv"),
                ],
            )
        ]
    )
    assert "ItemData" in remote_mod.rename_table(config, old="Items", new="ItemData")
    assert config.table_by_tab("ItemData") is not None
    with pytest.raises(LookupError, match="Known tabs"):
        remote_mod.rename_table(config, old="Nope", new="Whatever")
    with pytest.raises(LookupError):
        remote_mod.rename_table(config, old="Shop", new="ItemData")
    assert "spreadsheet_id: x" in remote_mod.show_remote(config)


def test_bom_and_crlf_read_clean(tmp_path: Path) -> None:
    path = tmp_path / "Items.csv"
    path.write_bytes("﻿Id,Name\r\nnumber,text\r\n1,A\r\n".encode("utf-8"))
    snap = local_mod.read_csv_table(path)
    assert snap.headers == ["Id", "Name"]
    assert snap.rows == [{"Id": "1", "Name": "A"}]


def test_pull_preserves_local_type_row(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    _seed(proj)
    proj.gateway.write_tab(
        _sheet_tab("Items", HEADERS, [["1", "Apple", "120"], ["2", "Banana", "50"]])
    )
    plan = _plan(proj)
    outcome, merged = apply_pull(
        plan, proj.gateway, csv_dir=proj.csv_dir, backup_root=proj.backup_root
    )
    assert outcome.applied
    assert merged is not None and plan.remote is not None
    commit_pull(proj.state, proj.base_dir, name="Items", merged=merged, remote_snapshot=plan.remote)
    assert local_mod.read_csv_table(proj.csv_dir / "Items.csv").types == TYPES


def test_backup_prune_and_restore(tmp_path: Path) -> None:
    source = tmp_path / "Items.csv"
    backup_root = tmp_path / "backups"
    contents = []
    for version in ("v1", "v2", "v3"):
        source.write_text(version, encoding="utf-8")
        contents.append(backup_mod.backup_file(source, backup_root))
    assert all(contents)
    assert len(backup_mod.list_backups(backup_root, "Items")) == 3
    pruned = backup_mod.prune_backups(backup_root, "Items", keep=2)
    assert pruned == 1
    remaining = backup_mod.list_backups(backup_root, "Items")
    assert len(remaining) == 2
    dest = tmp_path / "restored.csv"
    backup_mod.restore_backup(remaining[0], dest)  # newest-first
    assert dest.read_text(encoding="utf-8") == "v3"


def test_base_roundtrip_and_clear(tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    snap = TableSnapshot(
        name="Items",
        headers=HEADERS,
        types=TYPES,
        rows=[{"Id": "1", "Name": "A", "Price": "1"}],
        key_column="Id",
    )
    assert base_mod.load_base(base_dir, "Items") is None
    base_mod.save_base(base_dir, snap)
    loaded = base_mod.load_base(base_dir, "Items")
    assert loaded is not None and loaded.rows == snap.rows
    base_mod.clear_base(base_dir, "Items")
    assert base_mod.load_base(base_dir, "Items") is None


def test_doctor_reports_problems(tmp_path: Path) -> None:
    no_id = FccsvConfig(sources=[SourceConfig(spreadsheet_id="", tables=[])])
    assert any("spreadsheet" in issue for issue in doctor_mod.check_config(no_id))
    dupes = FccsvConfig(
        sources=[
            SourceConfig(
                spreadsheet_id="x",
                tables=[
                    TableMapping(tab="A", csv="a.csv"),
                    TableMapping(tab="A", csv="b.csv"),
                ],
            )
        ]
    )
    assert any("duplicate tabs" in issue for issue in doctor_mod.check_config(dupes))

    assert doctor_mod.check_csv_dir(tmp_path / "missing") != []
    empty = tmp_path / "empty"
    empty.mkdir()
    assert doctor_mod.check_csv_dir(empty) != []
    good = tmp_path / "good"
    good.mkdir()
    _write_csv(good / "Items.csv", HEADERS, TYPES, ROWS)
    assert doctor_mod.check_csv_dir(good) == []

    assert len(doctor_mod.check_auth_files(None, tmp_path / "token.json")) == 2

    class Unreachable:
        def list_tabs(self) -> list[str]:
            raise RuntimeError("boom")

    assert doctor_mod.check_gateway(Unreachable()) == ["sheet unreachable: boom"]
    gateway = InMemorySheetsGateway({"Items": _sheet_tab("Items", HEADERS, ROWS)})
    assert doctor_mod.check_gateway(gateway) == []
    assert doctor_mod.check_gateway(InMemorySheetsGateway()) == ["sheet has no tabs"]


def test_auth_client_file_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "proj"
    tool = tmp_path / "tool"
    root.mkdir()
    tool.mkdir()
    assert (
        auth_mod.resolve_client_file(None, project_root=root, tool_dir=tool, cwd=tmp_path) is None
    )
    (tool / DEFAULT_CREDENTIALS_FILENAME).write_text("{}", encoding="utf-8")
    assert auth_mod.resolve_client_file(None, project_root=root, tool_dir=tool, cwd=tmp_path) == (
        tool / DEFAULT_CREDENTIALS_FILENAME
    )
    (root / DEFAULT_CREDENTIALS_FILENAME).write_text("{}", encoding="utf-8")
    assert auth_mod.resolve_client_file(None, project_root=root, tool_dir=tool, cwd=tmp_path) == (
        root / DEFAULT_CREDENTIALS_FILENAME
    )
    assert auth_mod.resolve_client_file(
        "custom.json", project_root=root, tool_dir=tool, cwd=tmp_path
    ) == Path("custom.json")
    monkeypatch.setenv("FCCSV_CREDENTIALS", "env.json")
    assert auth_mod.resolve_client_file(
        None, project_root=root, tool_dir=tool, cwd=tmp_path
    ) == Path("env.json")
    assert auth_mod.resolve_token_file(None, project_root=root) == root / "token.json"


def test_push_rejects_duplicate_keys(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    _write_csv(
        proj.csv_dir / "Items.csv", HEADERS, TYPES, [["1", "Apple", "100"], ["1", "Banana", "50"]]
    )
    with pytest.raises(DataValidationError):
        apply_push(_plan(proj), proj.gateway)
