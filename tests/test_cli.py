"""Phase 3: CLI dispatch over `app/`. Injected InMemory gateway, tmp roots.

Covers: status formats + CI gate, cell diff, push/pull roundtrip with
state/journal/metadata, conflict + force, pull seeding, partial push,
fetch, remote set/show, rename, validate, doctor, init.
"""

from __future__ import annotations

import json
from pathlib import Path

from fccsv.adapters.sheets import InMemorySheetsGateway
from fccsv.domain.snapshot import TableSnapshot
from fccsv.interfaces.cli import main

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
    spreadsheet_id: str = "test-id",
) -> InMemorySheetsGateway:
    csv_dir = tmp_path / "Assets" / "CSV"
    csv_dir.mkdir(parents=True)
    _write_csv(csv_dir / "Items.csv", csv_rows)
    config = {
        "sources": [
            {
                "spreadsheet_id": spreadsheet_id,
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
    return InMemorySheetsGateway({"Items": _sheet_tab("Items", sheet_rows)})


def _run(
    root: Path, gateway: InMemorySheetsGateway, capsys: object, *argv: str
) -> tuple[int, str, str]:
    rc = main(["--root", str(root), *argv], gateway=gateway)
    out, err = capsys.readouterr()  # type: ignore[attr-defined]
    return rc, out, err


def _run_global(
    root: Path, gateway: InMemorySheetsGateway, capsys: object, *argv: str
) -> tuple[int, str, str]:
    """Global flags (--offline) must precede the subcommand."""
    rc = main([*argv], gateway=gateway)
    out, err = capsys.readouterr()  # type: ignore[attr-defined]
    return rc, out, err


def test_status_in_sync(tmp_path: Path, capsys: object) -> None:
    gateway = _project(tmp_path)
    rc, out, _ = _run(tmp_path, gateway, capsys, "status")
    assert rc == 0
    assert "[Items] in sync" in out


def test_status_short_stat_json_and_ci_gate(tmp_path: Path, capsys: object) -> None:
    gateway = _project(tmp_path)
    _write_csv(tmp_path / "Assets" / "CSV" / "Items.csv", [["1", "Apple", "120"], ROWS[1]])
    rc, out, _ = _run(tmp_path, gateway, capsys, "status", "--short")
    assert rc == 0
    assert "X Items" in out  # fresh lineage: both sides differ from empty
    rc, out, _ = _run(tmp_path, gateway, capsys, "status", "--stat")
    assert "Items: +0 -0 ~1" in out
    rc, out, _ = _run(tmp_path, gateway, capsys, "status", "--json")
    assert json.loads(out)[0]["direction"] == "X"
    rc, _, _ = _run(tmp_path, gateway, capsys, "status", "--fail-on-diff")
    assert rc == 1


def test_diff_shows_cell_change(tmp_path: Path, capsys: object) -> None:
    gateway = _project(tmp_path)
    _write_csv(tmp_path / "Assets" / "CSV" / "Items.csv", [["1", "Apple", "120"], ROWS[1]])
    rc, out, _ = _run(tmp_path, gateway, capsys, "diff")
    assert rc == 0
    assert "~ [Items] Id=1.Price: '100' -> '120'" in out
    rc, out, _ = _run(tmp_path, gateway, capsys, "diff", "--name-only")
    assert out.strip() == "Items"


def test_push_pull_roundtrip_with_journal_and_log(tmp_path: Path, capsys: object) -> None:
    gateway = _project(tmp_path)
    _write_csv(tmp_path / "Assets" / "CSV" / "Items.csv", [["1", "Apple", "120"], ROWS[1]])
    rc, out, _ = _run(tmp_path, gateway, capsys, "push")
    assert rc == 0
    assert "[Items] pushed" in out
    assert gateway.read_tab("Items").rows[0]["Price"] == "120"
    assert (tmp_path / ".fccsv-state.json").exists()
    rc, out, _ = _run(tmp_path, gateway, capsys, "log")
    assert rc == 0
    assert "push Items 2 rows" in out
    rc, out, _ = _run(tmp_path, gateway, capsys, "status")
    assert "[Items] in sync" in out


def test_pull_designer_edit_backs_up_csv(tmp_path: Path, capsys: object) -> None:
    gateway = _project(tmp_path)
    rc, _, _ = _run(tmp_path, gateway, capsys, "push")
    assert rc == 0
    snap = gateway.read_tab("Items")
    edited = TableSnapshot(
        name="Items",
        headers=list(snap.headers),
        rows=[dict(r) for r in snap.rows],
        key_column="Id",
    )
    edited.rows[0]["Price"] = "999"
    gateway.write_tab(edited)
    rc, out, _ = _run(tmp_path, gateway, capsys, "pull")
    assert rc == 0
    assert "[Items] pulled" in out
    text = (tmp_path / "Assets" / "CSV" / "Items.csv").read_text(encoding="utf-8")
    assert "999" in text
    assert list((tmp_path / "backups").glob("Items*")) != []


def test_conflict_blocks_push_until_forced(tmp_path: Path, capsys: object) -> None:
    gateway = _project(tmp_path)
    rc, _, _ = _run(tmp_path, gateway, capsys, "push")
    assert rc == 0
    _write_csv(tmp_path / "Assets" / "CSV" / "Items.csv", [["1", "Apple", "111"], ROWS[1]])
    snap = gateway.read_tab("Items")
    edited = TableSnapshot(
        name="Items",
        headers=list(snap.headers),
        rows=[dict(r) for r in snap.rows],
        key_column="Id",
    )
    edited.rows[0]["Price"] = "222"
    gateway.write_tab(edited)
    rc, _, err = _run(tmp_path, gateway, capsys, "push")
    assert rc == 1
    assert "both changed" in err
    rc, _, _ = _run(tmp_path, gateway, capsys, "push", "--force")
    assert rc == 0
    assert gateway.read_tab("Items").rows[0]["Price"] == "111"


def test_pull_seeds_missing_csv(tmp_path: Path, capsys: object) -> None:
    gateway = _project(tmp_path)
    (tmp_path / "Assets" / "CSV" / "Items.csv").unlink()
    rc, out, _ = _run(tmp_path, gateway, capsys, "pull")
    assert rc == 0
    assert "new file" in out
    text = (tmp_path / "Assets" / "CSV" / "Items.csv").read_text(encoding="utf-8")
    assert "Apple" in text and "Banana" in text


def test_partial_push_only_selected_keys(tmp_path: Path, capsys: object) -> None:
    gateway = _project(tmp_path)
    rc, _, _ = _run(tmp_path, gateway, capsys, "push")
    assert rc == 0
    _write_csv(
        tmp_path / "Assets" / "CSV" / "Items.csv",
        [["1", "Apple", "111"], ["2", "Banana", "222"]],
    )
    rc, out, _ = _run(tmp_path, gateway, capsys, "push", "--only", "1")
    assert rc == 0
    assert "partial" in out
    rows = gateway.read_tab("Items").rows
    assert rows[0]["Price"] == "111"
    assert rows[1]["Price"] == "50"


def test_fetch_reports_designer_edit(tmp_path: Path, capsys: object) -> None:
    gateway = _project(tmp_path)
    rc, _, _ = _run(tmp_path, gateway, capsys, "push")
    assert rc == 0
    snap = gateway.read_tab("Items")
    edited = TableSnapshot(
        name="Items",
        headers=list(snap.headers),
        rows=[dict(r) for r in snap.rows],
        key_column="Id",
    )
    edited.rows[1]["Price"] = "55"
    gateway.write_tab(edited)
    rc, out, _ = _run(tmp_path, gateway, capsys, "fetch")
    assert rc == 0
    assert "[Items] changed on sheet" in out
    rc, out, _ = _run(tmp_path, gateway, capsys, "fetch", "--json")
    payload = json.loads(out)
    assert payload["tables"][0]["changed"] is True


def test_remote_show_and_set(tmp_path: Path, capsys: object) -> None:
    gateway = _project(tmp_path)
    rc, out, _ = _run(tmp_path, gateway, capsys, "remote", "show")
    assert rc == 0
    assert "test-id" in out
    rc, out, _ = _run(tmp_path, gateway, capsys, "remote", "set", "new-id")
    assert rc == 0
    assert "lineage reset" in out
    saved = json.loads((tmp_path / ".fccsv.json").read_text(encoding="utf-8"))
    assert saved["sources"][0]["spreadsheet_id"] == "new-id"


def test_rename_updates_mapping_and_rejects_unknown(tmp_path: Path, capsys: object) -> None:
    gateway = _project(tmp_path)
    rc, out, _ = _run(tmp_path, gateway, capsys, "rename", "--old", "Items", "--new", "Products")
    assert rc == 0
    assert "renamed mapping" in out
    saved = json.loads((tmp_path / ".fccsv.json").read_text(encoding="utf-8"))
    assert saved["sources"][0]["tables"][0]["tab"] == "Products"
    rc, _, err = _run(tmp_path, gateway, capsys, "rename", "--old", "Nope", "--new", "X")
    assert rc == 2
    assert "unknown tab" in err


def test_validate_catches_ragged_csv(tmp_path: Path, capsys: object) -> None:
    gateway = _project(tmp_path)
    rc, out, _ = _run(tmp_path, gateway, capsys, "validate")
    assert rc == 0
    assert "ok (2 rows)" in out
    (tmp_path / "Assets" / "CSV" / "Items.csv").write_text(
        "Id,Name,Price\nnumber,text,number\n1,Apple\n", encoding="utf-8"
    )
    rc, out, _ = _run(tmp_path, gateway, capsys, "validate")
    assert rc == 1
    assert "invalid" in out


def test_doctor_offline(tmp_path: Path, capsys: object) -> None:
    gateway = _project(tmp_path)
    rc, out, _ = _run_global(
        tmp_path, gateway, capsys, "--root", str(tmp_path), "--offline", "doctor"
    )
    assert rc == 1
    assert "FAIL" in out  # no credentials/token files yet
    (tmp_path / "credentials.json").write_text("{}", encoding="utf-8")
    (tmp_path / "token.json").write_text("{}", encoding="utf-8")
    rc, out, _ = _run_global(
        tmp_path, gateway, capsys, "--root", str(tmp_path), "--offline", "doctor"
    )
    assert rc == 0
    assert "ok:" in out


def test_init_builds_config(tmp_path: Path, capsys: object) -> None:
    csv_dir = tmp_path / "Assets" / "CSV"
    csv_dir.mkdir(parents=True)
    _write_csv(csv_dir / "Items.csv", ROWS)
    gateway = InMemorySheetsGateway({"Items": _sheet_tab("Items", ROWS)})
    rc, out, _ = _run(tmp_path, gateway, capsys, "init", "sheet-id-123")
    assert rc == 0
    assert "Items -> Items.csv" in out
    saved = json.loads((tmp_path / ".fccsv.json").read_text(encoding="utf-8"))
    assert saved["sources"][0]["spreadsheet_id"] == "sheet-id-123"


def test_unknown_tab_is_usage_error(tmp_path: Path, capsys: object) -> None:
    gateway = _project(tmp_path)
    rc, _, err = _run(tmp_path, gateway, capsys, "status", "--tab", "Nope")
    assert rc == 2
    assert "unknown tab" in err


def test_dry_run_writes_nothing(tmp_path: Path, capsys: object) -> None:
    gateway = _project(tmp_path)
    _write_csv(tmp_path / "Assets" / "CSV" / "Items.csv", [["1", "Apple", "120"], ROWS[1]])
    rc, out, _ = _run(tmp_path, gateway, capsys, "push", "--dry-run")
    assert rc == 0
    assert "would push" in out
    assert gateway.read_tab("Items").rows[0]["Price"] == "100"
    assert not (tmp_path / ".fccsv-state.json").exists()
