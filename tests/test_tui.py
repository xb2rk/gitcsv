"""Phase 5a: TUI over the same `app/` orchestration as the CLI.

Pilot (headless) tests prove: dashboard renders with markers, push
updates the sheet, diverged push raises the conflict modal and force
resolves it, log/doctor/validate screens render, fetch updates the
status bar, `--only` input sticks. Backend unit tests pin markers,
diff wording, and the missing-textual hint path.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from fccsv.adapters.sheets import InMemorySheetsGateway
from fccsv.app import auth as auth_mod
from fccsv.app import sync as sync_mod
from fccsv.domain.snapshot import TableSnapshot
from fccsv.interfaces import cli
from fccsv.interfaces.cli import cmd_tui, main
from fccsv.interfaces.tui.app import FccsvApp
from fccsv.interfaces.tui.backend import Backend
from fccsv.interfaces.tui.modals import AuthGate, ConflictModal, KeysModal, ReportScreen

HEADERS = ["Id", "Name", "Price"]
TYPES = ["number", "text", "number"]
ROWS = [["1", "Apple", "100"], ["2", "Banana", "50"]]


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    text = "\n".join([",".join(HEADERS), ",".join(TYPES), *(",".join(r) for r in rows)])
    path.write_text(text + "\n", encoding="utf-8")


def _sheet_tab(tab: str, rows: list[list[str]]) -> TableSnapshot:
    return TableSnapshot(
        name=tab,
        headers=list(HEADERS),
        types=[],
        rows=[dict(zip(HEADERS, r, strict=True)) for r in rows],
        key_column="Id",
    )


def _project(
    tmp_path: Path,
    *,
    csv_rows: list[list[str]] = ROWS,
    sheet_rows: list[list[str]] = ROWS,
    spreadsheet: str | None = "test-id",
) -> InMemorySheetsGateway:
    csv_dir = tmp_path / "Assets" / "CSV"
    csv_dir.mkdir(parents=True)
    _write_csv(csv_dir / "Items.csv", csv_rows)
    config = {
        "sources": [
            {
                "spreadsheet_id": spreadsheet or "",
                "tables": [
                    {
                        "tab": "Items",
                        "csv": "Items.csv",
                        "key_column": "Id",
                        "column_map": {},
                        "skip_type_row_in_sheet": True,
                    }
                ],
            }
        ],
        "csv_dir": "Assets/CSV",
        "credentials_file": "credentials.json",
        "token_file": "token.json",
        "state_file": ".fccsv-state.json",
    }
    (tmp_path / ".fccsv.json").write_text(json.dumps(config), encoding="utf-8")
    # Logged-in user: the startup gate goes straight to the dashboard.
    # Gate tests delete/omit this file to simulate first run.
    (tmp_path / "token.json").write_text("{}", encoding="utf-8")
    return InMemorySheetsGateway({"Items": _sheet_tab("Items", sheet_rows)})


def _backend(root: Path, gateway: InMemorySheetsGateway) -> Backend:
    config = cli.load_config(root)
    return Backend(cli.build_context(root, config), gateway)


def _price_of(gateway: InMemorySheetsGateway, key: str = "1") -> str:
    snap = gateway.read_tab("Items")
    for row in snap.rows:
        if row.get("Id") == key:
            return row.get("Price", "")
    raise AssertionError(f"key {key} missing on sheet")


async def _wait_for(predicate: Callable[[], bool], timeout: float = 30.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("condition not met in time")
        await asyncio.sleep(0.05)


# ---------------------------------------------------------------- backend


def test_backend_markers(tmp_path: Path) -> None:
    gateway = _project(tmp_path)
    rows, _ = _backend(tmp_path, gateway).refresh()
    assert [(r.plan.tab, r.direction) for r in rows] == [("Items", "=")]
    _write_csv(tmp_path / "Assets" / "CSV" / "Items.csv", [["1", "Apple", "120"], ROWS[1]])
    rows, _ = _backend(tmp_path, gateway).refresh()
    # Fresh project: no lineage yet, so a one-sided edit reads as X
    # (both sides differ from empty history). Push still needs no force —
    # the real gate is plan_conflicted, which is False here.
    assert [(r.plan.tab, r.direction) for r in rows] == [("Items", "X")]
    assert not sync_mod.plan_conflicted(rows[0].plan)


def test_backend_diff_lines(tmp_path: Path) -> None:
    gateway = _project(tmp_path)
    backend = _backend(tmp_path, gateway)
    rows, _ = backend.refresh()
    assert backend.diff_lines(rows[0]) == ["= Items: +0 -0 ~0", "no differences"]
    _write_csv(tmp_path / "Assets" / "CSV" / "Items.csv", [["1", "Apple", "120"], ROWS[1]])
    rows, _ = backend.refresh()
    lines = backend.diff_lines(rows[0])
    assert lines[0].startswith("X Items:")
    assert "~ Id=1.Price: '100' -> '120'" in lines


def test_backend_doctor_and_validate(tmp_path: Path) -> None:
    gateway = _project(tmp_path)
    backend = _backend(tmp_path, gateway)
    rc, text = backend.doctor_text()
    assert "client:" in text
    assert rc in (0, 1)  # fake "{}" token is presence-only; content is never parsed here
    rc, text = backend.validate_text()
    assert rc == 0
    assert "[Items] ok (2 rows)" in text


def test_tui_subcommand_registered() -> None:
    args = cli.build_parser().parse_args(["tui"])
    assert args.handler is cmd_tui


def test_missing_textual_hint(tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = _project(tmp_path)
    for mod in [m for m in sys.modules if m == "textual" or m.startswith("textual.")]:
        monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.delitem(sys.modules, "fccsv.interfaces.tui.app", raising=False)
    monkeypatch.setitem(sys.modules, "textual", None)
    assert cli._load_tui_app() is None
    config = cli.load_config(tmp_path)
    ctx = cli.build_context(tmp_path, config)
    import argparse

    rc = cmd_tui(ctx, gateway, argparse.Namespace())
    assert rc == 2
    _, err = capsys.readouterr()
    assert "pip install fccsv[tui]" in err


# ---------------------------------------------------------------- pilot


async def test_dashboard_renders_in_sync(tmp_path: Path) -> None:
    gateway = _project(tmp_path)
    app = FccsvApp(root=tmp_path, gateway=gateway)
    async with app.run_test() as pilot:
        _ = pilot
        await _wait_for(lambda: len(app._rows) == 1)
        app.select_tab("Items")
        await _wait_for(
            lambda: "no differences" in "\n".join(app._backend.diff_lines(app._rows[0]))
        )
        status = app.query_one("#status")
        assert "sheet: test-id" in status.text  # type: ignore[attr-defined]


async def test_push_flow_updates_sheet(tmp_path: Path, capsys: Any) -> None:
    _ = capsys
    gateway = _project(tmp_path)
    _write_csv(tmp_path / "Assets" / "CSV" / "Items.csv", [["1", "Apple", "120"], ROWS[1]])
    app = FccsvApp(root=tmp_path, gateway=gateway)
    async with app.run_test() as pilot:
        await _wait_for(lambda: len(app._rows) == 1)
        app.select_tab("Items")
        assert app._rows[0].direction == "X"  # fresh lineage: unrelated histories
        await pilot.press("P")  # one-sided: no force needed despite the X marker
        await _wait_for(lambda: _price_of(gateway) == "120")
        await _wait_for(lambda: not app._busy)
        journal = (tmp_path / ".fccsv-journal.jsonl").read_text(encoding="utf-8")
        assert '"action": "push"' in journal and '"table": "Items"' in journal


async def test_conflict_modal_then_force(tmp_path: Path, capsys: Any) -> None:
    gateway = _project(tmp_path)
    assert main(["--root", str(tmp_path), "push"], gateway=gateway) == 0
    capsys.readouterr()
    _write_csv(tmp_path / "Assets" / "CSV" / "Items.csv", [["1", "Apple", "120"], ROWS[1]])
    gateway.write_tab(_sheet_tab("Items", [["1", "Apple", "130"], ROWS[1]]))
    app = FccsvApp(root=tmp_path, gateway=gateway)
    async with app.run_test() as pilot:
        await _wait_for(lambda: len(app._rows) == 1)
        app.select_tab("Items")
        await _wait_for(
            lambda: bool(app._rows) and app._rows[0].direction == "X",
        )
        await pilot.press("P")
        await _wait_for(lambda: isinstance(app.screen, ConflictModal))
        await pilot.press("f")
        await _wait_for(lambda: _price_of(gateway) == "120")


async def test_log_fetch_validate_doctor_screens(tmp_path: Path, capsys: Any) -> None:
    gateway = _project(tmp_path)
    # Diverge first: an in-sync push is a no-op and records no journal line.
    _write_csv(tmp_path / "Assets" / "CSV" / "Items.csv", [["1", "Apple", "120"], ROWS[1]])
    assert main(["--root", str(tmp_path), "push"], gateway=gateway) == 0
    capsys.readouterr()
    app = FccsvApp(root=tmp_path, gateway=gateway)
    async with app.run_test() as pilot:
        await _wait_for(lambda: len(app._rows) == 1)
        app.select_tab("Items")
        await pilot.press("l")
        await _wait_for(lambda: isinstance(app.screen, ReportScreen))
        assert any("push" in line for line in app.screen._lines)  # type: ignore[attr-defined]
        await pilot.press("escape")
        await _wait_for(lambda: not isinstance(app.screen, ReportScreen))
        await pilot.press("f")
        await _wait_for(lambda: app._last_fetch is not None)
        assert "fetch" in app.query_one("#status").text  # type: ignore[attr-defined]
        await pilot.press("D")
        await _wait_for(lambda: isinstance(app.screen, ReportScreen))
        await pilot.press("q")
        await _wait_for(lambda: not isinstance(app.screen, ReportScreen))
        await pilot.press("v")
        await _wait_for(lambda: isinstance(app.screen, ReportScreen))
        await pilot.press("q")
        await _wait_for(lambda: not isinstance(app.screen, ReportScreen))


async def test_only_keys_modal(tmp_path: Path) -> None:
    gateway = _project(tmp_path)
    app = FccsvApp(root=tmp_path, gateway=gateway)
    async with app.run_test() as pilot:
        await _wait_for(lambda: len(app._rows) == 1)
        await pilot.press("o")
        await _wait_for(lambda: isinstance(app.screen, KeysModal))
        await pilot.press("1", ",", "2")
        await pilot.press("enter")
        await _wait_for(lambda: app._only == {"1", "2"})
        assert "only: 1,2" in app.query_one("#status").text  # type: ignore[attr-defined]


# ---------------------------------------------------------------- startup gates


def _no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_mod, "resolve_client_file", lambda *a, **k: None)
    for var in ("FCCSV_CREDENTIALS", "FCCSV_CLIENT_JSON", "FCCSV_CLIENT_ID", "FCCSV_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)


def test_startup_state_ready(tmp_path: Path) -> None:
    gateway = _project(tmp_path)
    state = _backend(tmp_path, gateway).startup_state()
    assert state.has_client  # dev checkout: tool-dir credentials.json
    assert state.has_token and state.has_sheet


def test_startup_state_needs_login(tmp_path: Path) -> None:
    gateway = _project(tmp_path)
    (tmp_path / "token.json").unlink()
    state = _backend(tmp_path, gateway).startup_state()
    assert state.has_client and state.has_sheet
    assert not state.has_token


def test_startup_state_needs_sheet(tmp_path: Path) -> None:
    gateway = _project(tmp_path, spreadsheet=None)
    state = _backend(tmp_path, gateway).startup_state()
    assert state.has_client and state.has_token
    assert not state.has_sheet


def test_startup_state_no_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_client(monkeypatch)
    state = _backend(tmp_path, InMemorySheetsGateway()).startup_state()
    assert not state.has_client
    assert state.client_origin == "client: none found"


async def test_auth_gate_shown_when_no_token(tmp_path: Path) -> None:
    gateway = _project(tmp_path)
    (tmp_path / "token.json").unlink()
    app = FccsvApp(root=tmp_path, gateway=gateway)
    async with app.run_test() as pilot:
        _ = pilot
        await _wait_for(lambda: isinstance(app.screen, AuthGate))
        app.screen.query_one("#login")  # tool-dir client -> login offered
