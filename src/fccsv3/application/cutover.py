"""Read-only cutover audit and a local archive of both sides' before images."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from fccsv3.adapters.legacy_base import legacy_base_path, load_legacy_base
from fccsv3.adapters.project_lock import ProjectWriteLock
from fccsv3.adapters.state_db import read_bases, read_pending_summaries
from fccsv3.application.legacy_config import LegacyProject
from fccsv3.application.scan import SheetValuesReader, scan_tables
from fccsv3.domain.model import TableSnapshot


@dataclass(frozen=True, slots=True)
class CutoverTable:
    name: str
    status: str
    csv_rows: int | None
    sheet_rows: int | None
    identity: str
    errors: tuple[str, ...]
    v2_base_present: bool
    v2_base_schema_matches: bool | None
    v2_local_hash_matches: bool | None
    v2_sheet_hash_matches: bool | None
    metadata_hash_matches_v2: bool | None


@dataclass(frozen=True, slots=True)
class CutoverReport:
    tables: tuple[CutoverTable, ...]
    pending_operations: tuple[str, ...]
    warnings: tuple[str, ...]
    blockers: tuple[str, ...]

    @property
    def ready_for_review(self) -> bool:
        return not self.blockers


class _CachedReader:
    def __init__(self, values: dict[str, list[list[str]]]) -> None:
        self.values = values

    def read_values(self, tabs: tuple[str, ...]) -> dict[str, list[list[str]]]:
        return {tab: self.values[tab] for tab in tabs if tab in self.values}


def _v2_hash(snapshot: TableSnapshot | None) -> str | None:
    if snapshot is None:
        return None
    payload = json.dumps(
        {
            "h": list(snapshot.headers),
            "r": [snapshot.row_as_dict(index) for index in range(len(snapshot.rows))],
        },
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _legacy_hashes(path: Path) -> tuple[dict[str, dict[str, str]], str | None]:
    if not path.is_file():
        return {}, f"v2 state not found: {path}"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        tables = raw["tables"]
        if not isinstance(tables, dict):
            raise ValueError("tables must be an object")
        return {
            str(name): {"local": str(value.get("local", "")),
                        "remote": str(value.get("remote", ""))}
            for name, value in tables.items() if isinstance(value, dict)
        }, None
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {}, f"cannot read v2 state {path}: {error}"


def _metadata_hashes(values: list[list[str]] | None) -> dict[str, str]:
    if not values or len(values[0]) < 2:
        return {}
    if [cell.strip().lower() for cell in values[0][:2]] != ["tab", "hash"]:
        return {}
    return {row[0]: row[1] for row in values[1:] if len(row) >= 2 and row[0]}


def audit_cutover(
    root: Path,
    project: LegacyProject,
    reader: SheetValuesReader,
) -> CutoverReport:
    """Compare all mappings and legacy hints without trusting stale hashes."""

    tabs = tuple(mapping.tab for mapping in project.mappings)
    if not tabs:
        raise ValueError("no mapped tables in .fccsv.json")
    if len(set(tabs)) != len(tabs):
        raise ValueError("duplicate table names in .fccsv.json")
    values = dict(reader.read_values((*tabs, "_fccsv")))
    bases = read_bases(root)
    plans = scan_tables(project.csv_dir, project.mappings, _CachedReader(values), bases=bases)
    legacy_hashes, legacy_error = _legacy_hashes(project.legacy_state_file)
    metadata = _metadata_hashes(values.get("_fccsv"))
    warnings = [legacy_error] if legacy_error else []
    if not metadata:
        warnings.append("legacy _fccsv metadata is absent or invalid; it is advisory only")
    metadata_rows = (values.get("_fccsv") or [])[1:]
    metadata_names = [row[0] for row in metadata_rows if row and row[0]]
    if len(metadata_names) != len(set(metadata_names)):
        warnings.append("legacy _fccsv metadata contains duplicate tab names")
    pending = tuple(item[0] for item in read_pending_summaries(root))
    blockers = [f"pending v3 operation {item}" for item in pending]
    tables: list[CutoverTable] = []
    for mapping, plan in zip(project.mappings, plans, strict=True):
        old_base = load_legacy_base(root, mapping.tab)
        old_hash = legacy_hashes.get(mapping.tab, {})
        local_hash = _v2_hash(plan.local)
        remote_hash = _v2_hash(plan.remote)
        local_match = (
            local_hash == old_hash["local"]
            if local_hash is not None and old_hash.get("local") else None
        )
        remote_match = (
            remote_hash == old_hash["remote"]
            if remote_hash is not None and old_hash.get("remote") else None
        )
        meta_match = (
            metadata[mapping.tab] == old_hash["remote"]
            if mapping.tab in metadata and old_hash.get("remote") else None
        )
        schema_match = (
            old_base.headers == plan.local.headers
            and old_base.types == plan.local.types
            if old_base is not None and plan.local is not None else None
        )
        errors = tuple(
            issue.message for issue in plan.diagnostics if issue.severity == "error"
        )
        if plan.has_errors or plan.local is None or plan.remote is None:
            blockers.append(f"{mapping.tab}: invalid or missing CSV/Sheet")
        elif plan.has_diff:
            warnings.append(f"{mapping.tab}: CSV and Sheet differ; review direction")
        warnings.extend(
            f"{mapping.tab}: {issue.message}"
            for issue in plan.diagnostics if issue.severity == "warning"
        )
        if schema_match is False:
            warnings.append(f"{mapping.tab}: v2 BASE header or type row differs from CSV")
        if local_match is False:
            warnings.append(f"{mapping.tab}: CSV differs from the last v2 state hash")
        if remote_match is False:
            warnings.append(f"{mapping.tab}: Sheet differs from the last v2 state hash")
        if meta_match is False:
            warnings.append(f"{mapping.tab}: legacy _fccsv hash differs from v2 state")
        tables.append(CutoverTable(
            mapping.tab, plan.status,
            len(plan.local.rows) if plan.local else None,
            len(plan.remote.rows) if plan.remote else None,
            (
                ",".join(plan.identity.columns)
                if plan.identity and plan.identity.mode == "columns"
                else "row position" if plan.identity else ""
            ),
            errors, old_base is not None, schema_match,
            local_match, remote_match, meta_match,
        ))
    return CutoverReport(tuple(tables), pending, tuple(warnings), tuple(blockers))


def _csv_path(csv_dir: Path, csv_file: str) -> Path:
    root = csv_dir.resolve()
    path = (root / csv_file).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"CSV path escapes configured directory: {csv_file}")
    return path


def _optional_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def create_cutover_backup(
    root: Path,
    project: LegacyProject,
    reader: SheetValuesReader,
    output: Path,
) -> Path:
    """Archive CSV bytes and Sheet values without changing either source."""

    root = root.resolve()
    output = output.resolve()
    if output.exists():
        raise ValueError(f"backup already exists: {output}")
    if not project.mappings:
        raise ValueError("no mapped tables in .fccsv.json")
    if len(set(mapping.tab for mapping in project.mappings)) != len(project.mappings):
        raise ValueError("duplicate table names in .fccsv.json")
    with ProjectWriteLock(root):
        tabs = tuple(mapping.tab for mapping in project.mappings)
        values = dict(reader.read_values((*tabs, "_fccsv")))
        missing = tuple(tab for tab in tabs if tab not in values)
        if missing:
            raise ValueError("Sheet tab(s) missing from backup: " + ", ".join(missing))
        entries: dict[str, bytes] = {}
        file_before: dict[Path, bytes | None] = {}
        config_path = root / ".fccsv.json"
        file_before[config_path] = config_path.read_bytes()
        entries["config/.fccsv.json"] = file_before[config_path]
        file_before[project.legacy_state_file] = _optional_bytes(project.legacy_state_file)
        if file_before[project.legacy_state_file] is not None:
            entries["config/.fccsv-state.json"] = file_before[project.legacy_state_file]
        csv_manifest: list[dict[str, str]] = []
        for index, mapping in enumerate(project.mappings):
            path = _csv_path(project.csv_dir, mapping.csv_file)
            arcname = f"csv/{index:03d}.csv"
            file_before[path] = path.read_bytes()
            entries[arcname] = file_before[path]
            csv_manifest.append({"tab": mapping.tab, "path": str(path), "entry": arcname})
            base_path = legacy_base_path(root, mapping.tab)
            file_before[base_path] = _optional_bytes(base_path)
            if file_before[base_path] is not None:
                entries[f"legacy-base/{index:03d}.json"] = file_before[base_path]
        database = root / ".fccsv3" / "state.sqlite3"
        if database.is_file():
            with tempfile.TemporaryDirectory() as temporary:
                copy = Path(temporary) / "state.sqlite3"
                source = sqlite3.connect(database)
                target = sqlite3.connect(copy)
                try:
                    source.backup(target)
                finally:
                    target.close()
                    source.close()
                entries["v3/state.sqlite3"] = copy.read_bytes()
        entries["sheet/values.json"] = json.dumps(
            {tab: values[tab] for tab in (*tabs, "_fccsv") if tab in values},
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        manifest = {
            "version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "spreadsheet_id": project.spreadsheet_id,
            "legacy_state_path": str(project.legacy_state_file),
            "csv": csv_manifest,
            "entries": {
                name: hashlib.sha256(data).hexdigest()
                for name, data in entries.items()
            },
            "note": "Sheet values were read twice and compared; Google provides no "
                    "cross-tab atomic snapshot. Formatting and formulas are not "
                    "captured by the default values API.",
        }
        entries["manifest.json"] = json.dumps(
            manifest, ensure_ascii=False, indent=2
        ).encode("utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_output: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=output.parent, prefix=f".{output.name}.", suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_output = Path(handle.name)
            with zipfile.ZipFile(temporary_output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in entries.items():
                    archive.writestr(name, data)
            with zipfile.ZipFile(temporary_output) as archive:
                for name, digest in manifest["entries"].items():
                    if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                        raise RuntimeError(f"backup verification failed: {name}")
            current = dict(reader.read_values((*tabs, "_fccsv")))
            if current != values:
                raise RuntimeError("Sheet values changed during backup; retry after stopping writers")
            for path, before in file_before.items():
                if _optional_bytes(path) != before:
                    raise RuntimeError(f"local file changed during backup: {path}")
            os.link(temporary_output, output)
        finally:
            if temporary_output is not None:
                temporary_output.unlink(missing_ok=True)
    return output
