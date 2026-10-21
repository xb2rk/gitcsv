"""Pure, lossless comparison of local and remote table snapshots."""

from __future__ import annotations

from fccsv3.domain.identity import choose_identity
from fccsv3.domain.model import (
    CellDelta,
    Diagnostic,
    Identity,
    RowDiff,
    SchemaDiff,
    TablePlan,
    TableSnapshot,
)
from fccsv3.domain.validate import validate_snapshot


def _schema_diff(
    local: TableSnapshot, remote: TableSnapshot, base: TableSnapshot | None
) -> SchemaDiff:
    common_local = tuple(header for header in local.headers if header in remote.headers)
    common_remote = tuple(header for header in remote.headers if header in local.headers)
    return SchemaDiff(
        only_local=tuple(header for header in local.headers if header not in remote.headers),
        only_remote=tuple(header for header in remote.headers if header not in local.headers),
        reordered=common_local != common_remote,
        local_types_changed_since_base=base is not None and local.types != base.types,
    )


def _keyed_rows(
    snapshot: TableSnapshot, columns: tuple[str, ...]
) -> tuple[tuple[tuple[str, ...], ...], dict[tuple[str, ...], tuple[str, ...]]]:
    positions = tuple(snapshot.headers.index(column) for column in columns)
    sequence = tuple(tuple(row[index] for index in positions) for row in snapshot.rows)
    # choose_identity proved uniqueness before this dictionary is constructed.
    return sequence, dict(zip(sequence, snapshot.rows, strict=True))


def _row_diff(local: TableSnapshot, remote: TableSnapshot, identity: Identity) -> RowDiff:
    common = tuple(header for header in local.headers if header in remote.headers)
    local_columns = tuple(local.headers.index(header) for header in common)
    remote_columns = tuple(remote.headers.index(header) for header in common)
    cells: list[CellDelta] = []
    if identity.mode == "position":
        for index in range(min(len(local.rows), len(remote.rows))):
            local_row, remote_row = local.rows[index], remote.rows[index]
            for column, local_index, remote_index in zip(
                common, local_columns, remote_columns, strict=True
            ):
                if local_row[local_index] != remote_row[remote_index]:
                    cells.append(
                        CellDelta(
                            index + 1, column, local_row[local_index], remote_row[remote_index]
                        )
                    )
        return RowDiff(
            only_local=tuple(range(len(remote.rows) + 1, len(local.rows) + 1)),
            only_remote=tuple(range(len(local.rows) + 1, len(remote.rows) + 1)),
            cells=tuple(cells),
        )

    local_sequence, local_by_key = _keyed_rows(local, identity.columns)
    remote_sequence, remote_by_key = _keyed_rows(remote, identity.columns)
    local_keys, remote_keys = set(local_by_key), set(remote_by_key)
    for key in local_sequence:
        if key not in remote_by_key:
            continue
        local_row, remote_row = local_by_key[key], remote_by_key[key]
        for column, local_index, remote_index in zip(
            common, local_columns, remote_columns, strict=True
        ):
            if local_row[local_index] != remote_row[remote_index]:
                cells.append(
                    CellDelta(key, column, local_row[local_index], remote_row[remote_index])
                )
    return RowDiff(
        only_local=tuple(key for key in local_sequence if key not in remote_keys),
        only_remote=tuple(key for key in remote_sequence if key not in local_keys),
        cells=tuple(cells),
        order_changed=(
            tuple(key for key in local_sequence if key in remote_keys)
            != tuple(key for key in remote_sequence if key in local_keys)
        ),
    )


def plan_table(
    local: TableSnapshot | None,
    remote: TableSnapshot | None,
    *,
    name: str | None = None,
    base: TableSnapshot | None = None,
    preferred_key: tuple[str, ...] = (),
) -> TablePlan:
    """Validate both sides before matching rows or computing a keyed diff."""

    table_name = name or (local.name if local is not None else remote.name if remote else "")
    diagnostics: list[Diagnostic] = []
    if local is not None:
        diagnostics.extend(validate_snapshot(local))
    if remote is not None:
        diagnostics.extend(validate_snapshot(remote))
    if base is not None:
        base_issues = validate_snapshot(base)
        if base_issues:
            diagnostics.append(
                Diagnostic(
                    "BASE_UNUSABLE",
                    f"last-sync snapshot has {len(base_issues)} shape issue(s)",
                    "warning",
                )
            )
            base = None
    if any(issue.severity == "error" for issue in diagnostics):
        status = "invalid"
    elif local is None:
        status = "missing_local"
        diagnostics.append(Diagnostic("LOCAL_MISSING", "local CSV is missing", "warning"))
    elif remote is None:
        status = "missing_remote"
        diagnostics.append(Diagnostic("REMOTE_MISSING", "Sheet tab is missing", "warning"))
    else:
        status = "in_sync"
    if local is None or remote is None or status == "invalid":
        return TablePlan(
            name=table_name,
            status=status,
            local=local,
            remote=remote,
            base=base,
            identity=None,
            schema=SchemaDiff(),
            rows=RowDiff(),
            diagnostics=tuple(diagnostics),
        )

    identity, identity_issues = choose_identity(
        local, remote, base=base, preferred=preferred_key
    )
    diagnostics.extend(identity_issues)
    schema = _schema_diff(local, remote, base)
    rows = _row_diff(local, remote, identity)
    if base is None:
        status = "unbased_divergence" if schema.has_change or rows.has_change else "in_sync"
    elif schema.has_change or rows.has_change:
        local_changed = (
            local.headers != base.headers
            or local.types != base.types
            or local.rows != base.rows
        )
        remote_changed = remote.headers != base.headers or remote.rows != base.rows
        if local_changed and remote_changed:
            status = "both_changed"
        elif local_changed:
            status = "local_changed"
        elif remote_changed:
            status = "remote_changed"
        else:
            status = "unbased_divergence"
    return TablePlan(
        name=table_name,
        status=status,
        local=local,
        remote=remote,
        base=base,
        identity=identity,
        schema=schema,
        rows=rows,
        diagnostics=tuple(diagnostics),
    )
