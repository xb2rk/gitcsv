"""Last-sync hashes so `status` can report both-sides-changed conflicts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def snapshot_hash(headers: list[str], rows: list[dict[str, str]]) -> str:
    payload = json.dumps({"h": headers, "r": rows}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {"tables": {}}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"tables": {}}


def save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def record_table(state: dict, name: str, local_hash: str, remote_hash: str) -> dict:
    state.setdefault("tables", {})[name] = {
        "local": local_hash,
        "remote": remote_hash,
    }
    return state
