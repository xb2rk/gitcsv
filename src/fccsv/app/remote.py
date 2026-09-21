"""Remote management: show current source, relocate with state invalidation.

Tab names are immutable contracts. `set_remote` switches the spreadsheet,
backs up the old state file, and clears hashes + BASE so the next
push/pull starts a clean lineage instead of false conflicts (v1 left
stale state behind on re-init).
"""

from __future__ import annotations

from pathlib import Path

from fccsv.adapters import base as base_mod
from fccsv.adapters.sheets import spreadsheet_id_from_url
from fccsv.core.config import FccsvConfig


def show_remote(config: FccsvConfig) -> str:
    active = config.active
    lines = [f"spreadsheet_id: {active.spreadsheet_id or '(none)'}"]
    for table in active.tables:
        lines.append(f"  {table.tab} -> {table.csv}")
    return "\n".join(lines)


def set_remote(
    config: FccsvConfig,
    url_or_id: str,
    *,
    state_file: Path,
    base_dir: Path,
    mode: str = "keep-local",
) -> str:
    """Point at a new spreadsheet. `mode` is kept for CLI surface parity:
    keep-local (default) and keep-remote both reset lineage — the first
    sync after a move is always explicit (push or pull by the user)."""
    if mode not in ("keep-local", "keep-remote"):
        raise ValueError(f"unknown mode: {mode}")
    old_id = config.active.spreadsheet_id
    config.active.spreadsheet_id = spreadsheet_id_from_url(url_or_id)
    if state_file.exists():
        backup = state_file.with_name(state_file.name + ".pre-remote-set")
        try:
            backup.write_bytes(state_file.read_bytes())
        except OSError:
            pass
        try:
            state_file.unlink()
        except OSError:
            pass
    base_mod.clear_all_base(base_dir)
    return (
        f"remote moved: {old_id or '(none)'} -> {config.active.spreadsheet_id} "
        f"({mode}; lineage reset, old state backed up)"
    )


def rename_table(config: FccsvConfig, *, old: str, new: str) -> str:
    """Explicit tab rename migration: updates the mapping. State/BASE for
    the old name is dropped so the next sync seeds fresh lineage."""
    mapping = config.table_by_tab(old)
    if mapping is None:
        known = ", ".join(t.tab for t in config.active.tables) or "(none)"
        raise LookupError(f"unknown tab '{old}'. Known tabs: {known}")
    if config.table_by_tab(new) is not None:
        raise LookupError(f"tab '{new}' already mapped")
    mapping.tab = new
    return f"renamed mapping: '{old}' -> '{new}' (re-run push/pull to seed lineage)"
