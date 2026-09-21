"""Oracle ports: v1 sync behavior re-proven against the v2 plan/apply API.

Each flow wires plan -> apply -> commit explicitly (the CLI will do the
same in Phase 3). Covers: seed roundtrip, one-sided changes, conflicts +
force, missing tabs, batch planning, schema gate, init auto-map.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fccsv.adapters import base as base_mod
from fccsv.adapters import state as state_mod
from fccsv.adapters.sheets import InMemorySheetsGateway
from fccsv.domain.snapshot import TableSnapshot
from fccsv.app import init as init_mod
from fccsv.app.finalize import commit_pull, commit_push, write_metadata_tab
from fccsv.app.sync import (
    apply_pull,
    apply_push,
    plan_conflicted,
    plan_in_sync,
    plan_many,
    plan_one,
)
from fccsv.core.config import TableMapping
from fccsv.domain.errors import (
    ConflictError,
    DataValidationError,
    SchemaChangeBlocked,
    TabMissingError,
)

HEADERS = ["Id", "Name", "Price"]
TYPES = ["number", "text", "number"]
ROWS = [["1", "Apple", "100"], ["2", "Banana", "50"]]


def _write_csv(path: Path, headers: list[str], types: list[str], rows: list[list[str]]) -> str:
    text = "\n".join([",".join(headers), ",".join(types), *(",".join(r) for r in rows)]) + "\n"
    path.write_text(text, encoding="utf-8")
    return text


def _sheet_tab(
    tab: str, headers: list[str], rows: list[list[str]], key: str = "Id"
) -> TableSnapshot:
    return TableSnapshot(
        name=tab,
        headers=list(headers),
        types=[],
        rows=[dict(zip(headers, r, strict=True)) for r in rows],
        key_column=key,
    )


def _make_project(
    tmp_path: Path, tables: dict[str, tuple[list[str], list[str], list[list[str]]]]
) -> Any:
    csv_dir = tmp_path / "Assets" / "CSV"
    csv_dir.mkdir(parents=True)
    mappings = []
    for tab, (headers, types, rows) in tables.items():
        _write_csv(csv_dir / f"{tab}.csv", headers, types, rows)
        mappings.append(TableMapping(tab=tab, csv=f"{tab}.csv", key_column=headers[0]))
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


def _mapping(proj: Any, tab: str) -> Any:
    for mapping in proj.mappings:
        if mapping.tab == tab:
            return mapping
    raise AssertionError(f"no mapping for {tab}")


def _do_push(proj: Any, tab: str, **kwargs: Any) -> Any:
    mapping = _mapping(proj, tab)
    plan = plan_one(
        mapping,
        csv_dir=proj.csv_dir,
        gateway=proj.gateway,
        state=proj.state,
        base_dir=proj.base_dir,
    )
    outcome, new_remote = apply_push(plan, proj.gateway, **kwargs)
    if new_remote is not None and plan.local is not None:
        commit_push(
            proj.state, proj.base_dir, name=tab, local_snapshot=plan.local, new_remote=new_remote
        )
        state_mod.save_state(proj.state_file, proj.state)
        write_metadata_tab(proj.gateway, proj.state, [m.tab for m in proj.mappings])
    return outcome


def _do_pull(proj: Any, tab: str, **kwargs: Any) -> Any:
    mapping = _mapping(proj, tab)
    plan = plan_one(
        mapping,
        csv_dir=proj.csv_dir,
        gateway=proj.gateway,
        state=proj.state,
        base_dir=proj.base_dir,
    )
    outcome, merged = apply_pull(
        plan, proj.gateway, csv_dir=proj.csv_dir, backup_root=proj.backup_root, **kwargs
    )
    if merged is not None and plan.remote is not None:
        commit_pull(proj.state, proj.base_dir, name=tab, merged=merged, remote_snapshot=plan.remote)
        state_mod.save_state(proj.state_file, proj.state)
    return outcome


def _sheet_rows(gateway: InMemorySheetsGateway, tab: str) -> list[dict[str, str]]:
    return gateway.read_tab(tab).rows


def test_push_seeds_sheet_state_and_base(tmp_path: Path) -> None:
    proj = _make_project(tmp_path, {"Items": (HEADERS, TYPES, ROWS)})
    outcome = _do_push(proj, "Items")
    assert outcome.applied
    assert _sheet_rows(proj.gateway, "Items") == [
        {"Id": "1", "Name": "Apple", "Price": "100"},
        {"Id": "2", "Name": "Banana", "Price": "50"},
    ]
    assert proj.state.last("Items").local and proj.state.last("Items").remote
    assert base_mod.load_base(proj.base_dir, "Items") is not None
    assert proj.state_file.exists()


def test_pull_designer_edit_updates_csv_and_backs_up(tmp_path: Path) -> None:
    proj = _make_project(tmp_path, {"Items": (HEADERS, TYPES, ROWS)})
    _do_push(proj, "Items")
    proj.gateway.write_tab(
        _sheet_tab("Items", HEADERS, [["1", "Apple", "120"], ["2", "Banana", "50"]])
    )
    outcome = _do_pull(proj, "Items")
    assert outcome.applied
    text = (proj.csv_dir / "Items.csv").read_text(encoding="utf-8")
    assert "120" in text
    backups = list(proj.backup_root.iterdir())
    assert len(backups) == 1
    assert "100" in backups[0].read_text(encoding="utf-8")


def test_in_sync_push_and_pull_are_noops(tmp_path: Path) -> None:
    proj = _make_project(tmp_path, {"Items": (HEADERS, TYPES, ROWS)})
    _do_push(proj, "Items")
    plan = plan_one(
        _mapping(proj, "Items"),
        csv_dir=proj.csv_dir,
        gateway=proj.gateway,
        state=proj.state,
        base_dir=proj.base_dir,
    )
    assert plan_in_sync(plan)
    assert not plan_conflicted(plan)
    assert _do_push(proj, "Items").applied is False
    assert _do_pull(proj, "Items").applied is False


def test_conflict_blocks_push_and_pull(tmp_path: Path) -> None:
    proj = _make_project(tmp_path, {"Items": (HEADERS, TYPES, ROWS)})
    _do_push(proj, "Items")
    _write_csv(
        proj.csv_dir / "Items.csv", HEADERS, TYPES, [["1", "Apple", "150"], ["2", "Banana", "50"]]
    )
    proj.gateway.write_tab(
        _sheet_tab("Items", HEADERS, [["1", "Apple", "120"], ["2", "Banana", "50"]])
    )
    plan = plan_one(
        _mapping(proj, "Items"),
        csv_dir=proj.csv_dir,
        gateway=proj.gateway,
        state=proj.state,
        base_dir=proj.base_dir,
    )
    assert plan_conflicted(plan)
    with pytest.raises(ConflictError):
        apply_push(plan, proj.gateway)
    with pytest.raises(ConflictError):
        apply_pull(plan, proj.gateway, csv_dir=proj.csv_dir, backup_root=proj.backup_root)


def test_force_push_and_force_pull_win(tmp_path: Path) -> None:
    proj = _make_project(tmp_path, {"Items": (HEADERS, TYPES, ROWS)})
    _do_push(proj, "Items")
    _write_csv(
        proj.csv_dir / "Items.csv", HEADERS, TYPES, [["1", "Apple", "150"], ["2", "Banana", "50"]]
    )
    proj.gateway.write_tab(
        _sheet_tab("Items", HEADERS, [["1", "Apple", "120"], ["2", "Banana", "50"]])
    )
    assert _do_push(proj, "Items", force=True).applied is True
    assert _sheet_rows(proj.gateway, "Items")[0]["Price"] == "150"

    proj.gateway.write_tab(
        _sheet_tab("Items", HEADERS, [["1", "Apple", "999"], ["2", "Banana", "50"]])
    )
    assert _do_pull(proj, "Items", force=True).applied is True
    assert "999" in (proj.csv_dir / "Items.csv").read_text(encoding="utf-8")


def test_one_sided_changes_do_not_conflict(tmp_path: Path) -> None:
    proj = _make_project(tmp_path, {"Items": (HEADERS, TYPES, ROWS)})
    _do_push(proj, "Items")
    _write_csv(
        proj.csv_dir / "Items.csv", HEADERS, TYPES, [["1", "Apple", "150"], ["2", "Banana", "50"]]
    )
    assert _do_push(proj, "Items").applied is True

    proj.gateway.write_tab(
        _sheet_tab("Items", HEADERS, [["1", "Apple", "150"], ["2", "Cherry", "50"]])
    )
    assert _do_pull(proj, "Items").applied is True
    assert "Cherry" in (proj.csv_dir / "Items.csv").read_text(encoding="utf-8")


def test_push_creates_missing_tab(tmp_path: Path) -> None:
    proj = _make_project(tmp_path, {"Items": (HEADERS, TYPES, ROWS)})
    plan = plan_one(
        _mapping(proj, "Items"),
        csv_dir=proj.csv_dir,
        gateway=proj.gateway,
        state=proj.state,
        base_dir=proj.base_dir,
    )
    assert plan.remote is None
    assert _do_push(proj, "Items").applied is True
    assert len(_sheet_rows(proj.gateway, "Items")) == 2


def test_pull_missing_tab_raises(tmp_path: Path) -> None:
    proj = _make_project(tmp_path, {"Items": (HEADERS, TYPES, ROWS)})
    plan = plan_one(
        _mapping(proj, "Items"),
        csv_dir=proj.csv_dir,
        gateway=proj.gateway,
        state=proj.state,
        base_dir=proj.base_dir,
    )
    assert plan.remote is None
    with pytest.raises(TabMissingError):
        apply_pull(plan, proj.gateway, csv_dir=proj.csv_dir, backup_root=proj.backup_root)


def test_batch_plan_many_reads_sheet_once(tmp_path: Path) -> None:
    class CountingGateway(InMemorySheetsGateway):
        def __init__(self) -> None:
            super().__init__()
            self.read_tabs_calls = 0

        def read_tabs(self, tabs: list[str]) -> dict[str, TableSnapshot]:
            self.read_tabs_calls += 1
            return super().read_tabs(tabs)

    proj = _make_project(
        tmp_path,
        {
            "Items": (HEADERS, TYPES, ROWS),
            "Shop": (["ShopId", "Stock"], ["number", "number"], [["1", "9"]]),
        },
    )
    proj.gateway = CountingGateway()
    plans = plan_many(
        proj.mappings,
        csv_dir=proj.csv_dir,
        gateway=proj.gateway,
        state=proj.state,
        base_dir=proj.base_dir,
    )
    assert len(plans) == 2
    assert proj.gateway.read_tabs_calls == 1
    for plan in plans:
        outcome, new_remote = apply_push(plan, proj.gateway)
        assert outcome.applied
        assert new_remote is not None and plan.local is not None
        commit_push(
            proj.state,
            proj.base_dir,
            name=plan.tab,
            local_snapshot=plan.local,
            new_remote=new_remote,
        )
    assert len(_sheet_rows(proj.gateway, "Shop")) == 1


def test_schema_change_blocked_then_allowed(tmp_path: Path) -> None:
    proj = _make_project(tmp_path, {"Items": (HEADERS, TYPES, ROWS)})
    _do_push(proj, "Items")
    renamed = ["Id", "Title", "Price"]
    _write_csv(
        proj.csv_dir / "Items.csv", renamed, TYPES, [["1", "Apple", "100"], ["2", "Banana", "50"]]
    )
    with pytest.raises(SchemaChangeBlocked):
        _do_push(proj, "Items")
    assert _do_push(proj, "Items", allow_schema_change=True).applied is True
    assert proj.gateway.read_tab("Items").headers == renamed


def test_ragged_csv_push_rejected(tmp_path: Path) -> None:
    proj = _make_project(tmp_path, {"Items": (HEADERS, TYPES, ROWS)})
    _do_push(proj, "Items")
    (proj.csv_dir / "Items.csv").write_text(
        "Id,Name,Price\nnumber,text,number\n1,Apple\n2,Banana,50\n", encoding="utf-8"
    )
    with pytest.raises(DataValidationError):
        _do_push(proj, "Items")


def test_init_builds_config_from_tabs(tmp_path: Path) -> None:
    csv_dir = tmp_path / "Assets" / "CSV"
    csv_dir.mkdir(parents=True)
    _write_csv(csv_dir / "Items.csv", ["Id", "Name"], ["number", "text"], [["1", "Apple"]])
    _write_csv(csv_dir / "Shop.csv", ["ShopId", "Stock"], ["number", "number"], [["1", "9"]])
    config = init_mod.build_config_from_tabs(["Items", "ExtraTab"], csv_dir, spreadsheet_id="abc")
    by_tab = {t.tab: t.csv for t in config.active.tables}
    assert by_tab["Items"] == "Items.csv"
    assert by_tab["Shop"] == "Shop.csv"
    assert "ExtraTab" not in by_tab
    assert config.active.spreadsheet_id == "abc"
    items = next(t for t in config.active.tables if t.tab == "Items")
    assert items.key_column == "Id"

    config2 = init_mod.init_from_url_or_id(
        "https://docs.google.com/spreadsheets/d/XYZ/edit",
        None,
        csv_dir=csv_dir,
        list_tabs_fn=lambda: ["Items"],
    )
    assert config2.active.spreadsheet_id == "XYZ"
