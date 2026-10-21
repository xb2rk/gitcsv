"""SQLite storage for BASE state, transaction journal, and remote cache."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from gitcsv.core.models import TableSnapshot


@dataclass(frozen=True, slots=True)
class OperationRecord:
    """A recorded write operation in the state journal."""

    id: str
    kind: str
    table: str
    status: str  # "pending", "committed", "failed", "cancelled"
    started_at: str
    error: str | None = None
    before_local: TableSnapshot | None = None
    before_remote: TableSnapshot | None = None
    after_local: TableSnapshot | None = None
    after_remote: TableSnapshot | None = None


def _serialize_snapshot(snap: TableSnapshot | None) -> str | None:
    if snap is None:
        return None
    return json.dumps({
        "name": snap.name,
        "headers": snap.headers,
        "types": snap.types,
        "rows": snap.rows,
        "source": snap.source,
    })


def _deserialize_snapshot(raw: str | None) -> TableSnapshot | None:
    if not raw:
        return None
    data = json.loads(raw)
    return TableSnapshot(
        name=data["name"],
        headers=tuple(data["headers"]),
        types=tuple(data.get("types", ())),
        rows=tuple(tuple(r) for r in data["rows"]),
        source=data.get("source", "base"),
    )


class StateDB:
    """Manages the SQLite database stored in .gitcsv/state.db."""

    def __init__(self, project_root: Path, *, read_only: bool = False) -> None:
        self.db_dir = project_root / ".gitcsv"
        self.db_path = self.db_dir / "state.db"
        self.read_only = read_only
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> StateDB:
        self.open()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()

    def open(self) -> None:
        """Open database connection and initialize schema if needed."""
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")

        self._init_schema()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database connection is not open.")
        return self._conn

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS bases (
                    table_name TEXT PRIMARY KEY,
                    headers_json TEXT NOT NULL,
                    types_json TEXT NOT NULL,
                    rows_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    before_local_json TEXT,
                    before_remote_json TEXT,
                    after_local_json TEXT,
                    after_remote_json TEXT,
                    started_at TEXT NOT NULL,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS remote_cache (
                    table_name TEXT PRIMARY KEY,
                    values_json TEXT NOT NULL,
                    fetched_at REAL NOT NULL
                );
            """)

    # --- BASE State Methods ---

    def get_base(self, table_name: str) -> TableSnapshot | None:
        """Retrieve the last verified synchronized snapshot for a table."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT headers_json, types_json, rows_json FROM bases WHERE table_name = ?;",
            (table_name,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        headers = tuple(json.loads(row[0]))
        types = tuple(json.loads(row[1]))
        rows = tuple(tuple(r) for r in json.loads(row[2]))

        return TableSnapshot(
            name=table_name,
            headers=headers,
            types=types,
            rows=rows,
            source="base",
        )

    def get_all_bases(self) -> dict[str, TableSnapshot]:
        """Retrieve all recorded base snapshots as a mapping."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT table_name, headers_json, types_json, rows_json FROM bases;")
        results: dict[str, TableSnapshot] = {}

        for row in cursor.fetchall():
            name = row[0]
            headers = tuple(json.loads(row[1]))
            types = tuple(json.loads(row[2]))
            rows = tuple(tuple(r) for r in json.loads(row[3]))
            results[name] = TableSnapshot(name=name, headers=headers, types=types, rows=rows, source="base")

        return results

    def save_base(self, table_name: str, snapshot: TableSnapshot) -> None:
        """Store or update the verified base snapshot."""
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO bases (table_name, headers_json, types_json, rows_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(table_name) DO UPDATE SET
                    headers_json=excluded.headers_json,
                    types_json=excluded.types_json,
                    rows_json=excluded.rows_json,
                    updated_at=excluded.updated_at;
                """,
                (
                    table_name,
                    json.dumps(snapshot.headers),
                    json.dumps(snapshot.types),
                    json.dumps(snapshot.rows),
                    now,
                ),
            )

    def delete_base(self, table_name: str) -> None:
        """Remove base snapshot for a table (e.g. during untrack or undo)."""
        with self.conn:
            self.conn.execute("DELETE FROM bases WHERE table_name = ?;", (table_name,))

    # --- Transaction Journal Methods ---

    def reserve_operation(
        self,
        kind: str,
        table_name: str,
        before_local: TableSnapshot | None,
        before_remote: TableSnapshot | None,
        after_local: TableSnapshot | None,
        after_remote: TableSnapshot | None,
    ) -> str:
        """Reserve a pending operation in the WAL before executing external writes."""
        op_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO operations (
                    id, kind, table_name, status,
                    before_local_json, before_remote_json,
                    after_local_json, after_remote_json,
                    started_at, error
                ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, NULL);
                """,
                (
                    op_id,
                    kind,
                    table_name,
                    _serialize_snapshot(before_local),
                    _serialize_snapshot(before_remote),
                    _serialize_snapshot(after_local),
                    _serialize_snapshot(after_remote),
                    now,
                ),
            )

        return op_id

    def commit_operation(self, op_id: str, new_base: TableSnapshot) -> None:
        """Mark an operation as committed and advance the BASE state atomically."""
        with self.conn:
            self.conn.execute(
                "UPDATE operations SET status = 'committed' WHERE id = ?;",
                (op_id,),
            )
            self.save_base(new_base.name, new_base)

    def fail_operation(self, op_id: str, error: str) -> None:
        """Record a failure message on a pending operation."""
        with self.conn:
            self.conn.execute(
                "UPDATE operations SET status = 'failed', error = ? WHERE id = ?;",
                (error, op_id),
            )

    def cancel_operation(self, op_id: str, reason: str) -> None:
        """Cancel a pending operation that aborted before any write took place."""
        with self.conn:
            self.conn.execute(
                "UPDATE operations SET status = 'cancelled', error = ? WHERE id = ?;",
                (reason, op_id),
            )

    def get_pending_operations(self) -> list[OperationRecord]:
        """Find any unfinished pending operations that need recovery."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, kind, table_name, status, started_at, error,
                   before_local_json, before_remote_json,
                   after_local_json, after_remote_json
            FROM operations WHERE status = 'pending' ORDER BY started_at ASC;
            """
        )
        return [self._row_to_record(row) for row in cursor.fetchall()]

    def get_latest_operation(self, table_name: str) -> OperationRecord | None:
        """Find the latest committed operation for a specific table."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, kind, table_name, status, started_at, error,
                   before_local_json, before_remote_json,
                   after_local_json, after_remote_json
            FROM operations WHERE table_name = ? AND status = 'committed'
            ORDER BY started_at DESC LIMIT 1;
            """,
            (table_name,),
        )
        row = cursor.fetchone()
        return self._row_to_record(row) if row else None

    def get_history(self, limit: int = 20) -> list[OperationRecord]:
        """Get history of recent operations."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, kind, table_name, status, started_at, error,
                   before_local_json, before_remote_json,
                   after_local_json, after_remote_json
            FROM operations ORDER BY started_at DESC LIMIT ?;
            """,
            (limit,),
        )
        return [self._row_to_record(row) for row in cursor.fetchall()]

    def _row_to_record(self, row: tuple) -> OperationRecord:
        return OperationRecord(
            id=row[0],
            kind=row[1],
            table=row[2],
            status=row[3],
            started_at=row[4],
            error=row[5],
            before_local=_deserialize_snapshot(row[6]),
            before_remote=_deserialize_snapshot(row[7]),
            after_local=_deserialize_snapshot(row[8]),
            after_remote=_deserialize_snapshot(row[9]),
        )
