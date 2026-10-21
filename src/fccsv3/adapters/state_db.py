"""Single-file v3 BASE and recovery journal, separate from all v2 state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fccsv3.domain.model import TableSnapshot
from fccsv3.domain.operation import OperationDecision

STATE_VERSION = 2


class StateConflict(RuntimeError):
    """A pending operation or changed BASE makes a write unsafe."""


@dataclass(frozen=True, slots=True)
class OperationRecord:
    id: str
    table: str
    kind: str
    status: str
    before_local: TableSnapshot
    before_remote: TableSnapshot
    after_local: TableSnapshot
    after_remote: TableSnapshot
    started_at: str
    finished_at: str | None
    error: str | None
    source_operation_id: str | None


def _snapshot_json(snapshot: TableSnapshot) -> str:
    return json.dumps(
        {
            "name": snapshot.name,
            "headers": snapshot.headers,
            "types": snapshot.types,
            "rows": snapshot.rows,
            "source": snapshot.source,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _snapshot_from_json(raw: str) -> TableSnapshot:
    data = json.loads(raw)
    return TableSnapshot(
        data["name"], data["headers"], data["rows"],
        types=data["types"], source=data["source"],
    )


def snapshot_fingerprint(snapshot: TableSnapshot) -> str:
    """Hash values and CSV types, excluding source labels and raw parser metadata."""

    payload = json.dumps(
        [snapshot.headers, snapshot.types, snapshot.rows],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _check_version(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in (1, STATE_VERSION):
        raise ValueError(
            f"unsupported fccsv3 state version {version}; expected {STATE_VERSION}"
        )


def read_bases(root: Path) -> dict[str, TableSnapshot]:
    """Read v3 BASE without creating or modifying the database."""

    path = root / ".fccsv3" / "state.sqlite3"
    if not path.is_file():
        return {}
    with StateDB(root, read_only=True) as state:
        return {
            row[0]: _snapshot_from_json(row[1])
            for row in state.connection.execute("SELECT tab, snapshot FROM bases")
        }


def read_pending_summaries(root: Path) -> tuple[tuple[str, str], ...]:
    """Inspect pending writes without initializing or migrating v3 state."""

    path = root / ".fccsv3" / "state.sqlite3"
    if not path.is_file():
        return ()
    with StateDB(root, read_only=True) as state:
        return tuple(state.connection.execute(
            "SELECT id, tab FROM operations WHERE status='pending' ORDER BY started_at"
        ))


class StateDB:
    """Persist pending intent before writes and BASE only after verification."""

    def __init__(self, root: Path, *, read_only: bool = False) -> None:
        directory = root / ".fccsv3"
        self.path = directory / "state.sqlite3"
        if read_only:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
            self.connection = sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5
            )
            try:
                self.connection.execute("PRAGMA query_only=ON")
                _check_version(self.connection)
                self.version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            except Exception:
                self.connection.close()
                raise
            return
        directory.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA journal_mode=DELETE")
        self.connection.execute("PRAGMA synchronous=FULL")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, STATE_VERSION):
            self.connection.close()
            raise ValueError(f"unsupported fccsv3 state version {version}")
        if version == 0:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.connection.execute(
                    "CREATE TABLE bases (tab TEXT PRIMARY KEY, snapshot TEXT NOT NULL, "
                    "fingerprint TEXT NOT NULL, operation_id TEXT NOT NULL)"
                )
                self.connection.execute(
                    "CREATE TABLE operations ("
                    "id TEXT PRIMARY KEY, tab TEXT NOT NULL, kind TEXT NOT NULL, "
                    "status TEXT NOT NULL, before_local TEXT NOT NULL, "
                    "before_remote TEXT NOT NULL, after_local TEXT NOT NULL, "
                    "after_remote TEXT NOT NULL, started_at TEXT NOT NULL, "
                    "finished_at TEXT, error TEXT, source_operation_id TEXT)"
                )
                self.connection.execute(
                    "CREATE INDEX operations_tab_time ON operations(tab, started_at)"
                )
                self.connection.execute(f"PRAGMA user_version={STATE_VERSION}")
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                self.connection.close()
                raise
        elif version == 1:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.connection.execute(
                    "ALTER TABLE operations ADD COLUMN source_operation_id TEXT"
                )
                self.connection.execute(f"PRAGMA user_version={STATE_VERSION}")
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                self.connection.close()
                raise
        self.version = STATE_VERSION

    def __enter__(self) -> StateDB:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.connection.close()

    def reserve(
        self,
        decision: OperationDecision,
        before_local: TableSnapshot,
        before_remote: TableSnapshot,
        *,
        expected_base: str | None,
        source_operation_id: str | None = None,
    ) -> str:
        if not decision.allowed:
            raise ValueError("cannot reserve a blocked operation")
        assert decision.local_after is not None and decision.remote_after is not None
        operation_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            pending = self.connection.execute(
                "SELECT id FROM operations WHERE status='pending' LIMIT 1"
            ).fetchone()
            if pending is not None:
                raise StateConflict(
                    f"operation {pending[0]} is pending; inspect or recover it first"
                )
            current = self.connection.execute(
                "SELECT fingerprint FROM bases WHERE tab=?", (decision.table,)
            ).fetchone()
            current_hash = current[0] if current is not None else None
            if current_hash != expected_base:
                raise StateConflict("BASE changed since planning; scan again")
            if source_operation_id is not None:
                latest = self.connection.execute(
                    "SELECT id FROM operations WHERE tab=? AND status='committed' "
                    "ORDER BY rowid DESC LIMIT 1", (decision.table,)
                ).fetchone()
                if latest is None or latest[0] != source_operation_id:
                    raise StateConflict("undo target is no longer the latest committed operation")
            self.connection.execute(
                "INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    operation_id, decision.table, decision.kind, "pending",
                    _snapshot_json(before_local), _snapshot_json(before_remote),
                    _snapshot_json(decision.local_after),
                    _snapshot_json(decision.remote_after), now, None, None,
                    source_operation_id,
                ),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return operation_id

    def commit(self, operation_id: str, base: TableSnapshot) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT tab, status, after_local FROM operations WHERE id=?", (operation_id,)
            ).fetchone()
            if row is None or row[1] != "pending":
                raise StateConflict(f"operation {operation_id} is not pending")
            if snapshot_fingerprint(base) != snapshot_fingerprint(
                _snapshot_from_json(row[2])
            ):
                raise StateConflict("BASE must equal the verified CSV result")
            self.connection.execute(
                "INSERT INTO bases(tab,snapshot,fingerprint,operation_id) VALUES (?,?,?,?) "
                "ON CONFLICT(tab) DO UPDATE SET snapshot=excluded.snapshot, "
                "fingerprint=excluded.fingerprint, operation_id=excluded.operation_id",
                (row[0], _snapshot_json(base), snapshot_fingerprint(base), operation_id),
            )
            self.connection.execute(
                "UPDATE operations SET status='committed', finished_at=?, error=NULL WHERE id=?",
                (now, operation_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def commit_unbased(self, operation_id: str) -> None:
        """Finish an undo to divergent before images without inventing a BASE."""

        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT tab,kind,status,after_local,after_remote FROM operations WHERE id=?",
                (operation_id,),
            ).fetchone()
            if row is None or row[2] != "pending" or row[1] != "undo":
                raise StateConflict("only a pending undo can clear BASE")
            local = _snapshot_from_json(row[3])
            remote = _snapshot_from_json(row[4])
            if local.headers == remote.headers and local.rows == remote.rows:
                raise StateConflict("equal undo targets should commit a BASE")
            self.connection.execute("DELETE FROM bases WHERE tab=?", (row[0],))
            self.connection.execute(
                "UPDATE operations SET status='committed', finished_at=?, error=NULL WHERE id=?",
                (now, operation_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def mark_restored(self, operation_id: str) -> None:
        """Finish a pending operation whose two sides match its before images."""

        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT status FROM operations WHERE id=?", (operation_id,)
            ).fetchone()
            if row is None or row[0] != "pending":
                raise StateConflict(f"operation {operation_id} is not pending")
            self.connection.execute(
                "UPDATE operations SET status='restored', finished_at=?, "
                "error=NULL WHERE id=?", (now, operation_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def annotate_failure(self, operation_id: str, message: str) -> None:
        self.connection.execute(
            "UPDATE operations SET error=? WHERE id=? AND status='pending'",
            (message, operation_id),
        )

    def cancel(self, operation_id: str, reason: str) -> None:
        """Close a reservation when a pre-write check found changed input."""

        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT status FROM operations WHERE id=?", (operation_id,)
            ).fetchone()
            if row is None or row[0] != "pending":
                raise StateConflict(f"operation {operation_id} is not pending")
            self.connection.execute(
                "UPDATE operations SET status='cancelled', finished_at=?, error=? "
                "WHERE id=?", (now, reason, operation_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def get(self, operation_id: str) -> OperationRecord | None:
        source_column = "source_operation_id" if self.version == STATE_VERSION else "NULL"
        row = self.connection.execute(
            "SELECT id,tab,kind,status,before_local,before_remote,after_local,"
            f"after_remote,started_at,finished_at,error,{source_column} "
            "FROM operations WHERE id=?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        return OperationRecord(
            row[0], row[1], row[2], row[3],
            _snapshot_from_json(row[4]), _snapshot_from_json(row[5]),
            _snapshot_from_json(row[6]), _snapshot_from_json(row[7]),
            row[8], row[9], row[10], row[11],
        )

    def pending(self) -> tuple[OperationRecord, ...]:
        ids = self.connection.execute(
            "SELECT id FROM operations WHERE status='pending' ORDER BY started_at"
        ).fetchall()
        return tuple(record for (id_,) in ids if (record := self.get(id_)) is not None)

    def history(self, tab: str | None = None, limit: int = 20) -> tuple[OperationRecord, ...]:
        if limit < 1:
            raise ValueError("history limit must be positive")
        if tab is None:
            rows = self.connection.execute(
                "SELECT id FROM operations ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT id FROM operations WHERE tab=? ORDER BY started_at DESC LIMIT ?",
                (tab, limit),
            ).fetchall()
        return tuple(record for (id_,) in rows if (record := self.get(id_)) is not None)

    def latest_committed(self, tab: str) -> OperationRecord | None:
        row = self.connection.execute(
            "SELECT id FROM operations WHERE tab=? AND status='committed' "
            "ORDER BY rowid DESC LIMIT 1", (tab,)
        ).fetchone()
        return self.get(row[0]) if row is not None else None

    def base_fingerprint(self, tab: str) -> str | None:
        row = self.connection.execute(
            "SELECT fingerprint FROM bases WHERE tab=?", (tab,)
        ).fetchone()
        return row[0] if row is not None else None
