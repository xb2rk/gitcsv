"""Comparison planning between local CSV, remote Sheets, and BASE snapshots."""

from __future__ import annotations

from gitcsv.core.identity import choose_identity
from gitcsv.core.models import (
    CellDelta,
    Diagnostic,
    Identity,
    OperationDecision,
    OperationKind,
    RowDiff,
    SchemaDiff,
    TablePlan,
    TableSnapshot,
    TableStatus,
)
from gitcsv.core.validator import validate_snapshot


def _schema_diff(
    local: TableSnapshot, remote: TableSnapshot, base: TableSnapshot | None
) -> SchemaDiff:
    """Compare header columns and types between local, remote, and base."""
    common_local = tuple(h for h in local.headers if h in remote.headers)
    common_remote = tuple(h for h in remote.headers if h in local.headers)

    return SchemaDiff(
        only_local=tuple(h for h in local.headers if h not in remote.headers),
        only_remote=tuple(h for h in remote.headers if h not in local.headers),
        reordered=common_local != common_remote,
        local_types_changed=base is not None and local.types != base.types,
    )


def _keyed_rows(
    snapshot: TableSnapshot, columns: tuple[str, ...]
) -> tuple[tuple[tuple[str, ...], ...], dict[tuple[str, ...], tuple[str, ...]]]:
    """Index rows by identity column values."""
    col_indices = tuple(snapshot.headers.index(c) for c in columns)
    sequence = tuple(tuple(row[idx] for idx in col_indices) for row in snapshot.rows)
    lookup = dict(zip(sequence, snapshot.rows, strict=True))

    return sequence, lookup


def _row_diff(local: TableSnapshot, remote: TableSnapshot, identity: Identity) -> RowDiff:
    """Compute cell deltas, row additions/deletions, and ordering changes."""
    common_headers = tuple(h for h in local.headers if h in remote.headers)
    local_indices = tuple(local.headers.index(h) for h in common_headers)
    remote_indices = tuple(remote.headers.index(h) for h in common_headers)

    # 1. Positional comparison mode
    if identity.mode == "position":
        cells: list[CellDelta] = []
        min_rows = min(len(local.rows), len(remote.rows))

        for idx in range(min_rows):
            l_row, r_row = local.rows[idx], remote.rows[idx]
            for col, l_idx, r_idx in zip(common_headers, local_indices, remote_indices, strict=True):
                if l_row[l_idx] != r_row[r_idx]:
                    cells.append(CellDelta(idx + 1, col, l_row[l_idx], r_row[r_idx]))

        return RowDiff(
            only_local=tuple(range(len(remote.rows) + 1, len(local.rows) + 1)),
            only_remote=tuple(range(len(local.rows) + 1, len(remote.rows) + 1)),
            cells=tuple(cells),
            order_changed=False,
        )

    # 2. Keyed column comparison mode
    local_seq, local_dict = _keyed_rows(local, identity.columns)
    remote_seq, remote_dict = _keyed_rows(remote, identity.columns)

    local_keys, remote_keys = set(local_dict), set(remote_dict)
    key_cells: list[CellDelta] = []

    for key in local_seq:
        if key not in remote_dict:
            continue

        l_row, r_row = local_dict[key], remote_dict[key]
        for col, l_idx, r_idx in zip(common_headers, local_indices, remote_indices, strict=True):
            if l_row[l_idx] != r_row[r_idx]:
                key_cells.append(CellDelta(key, col, l_row[l_idx], r_row[r_idx]))

    # Check if relative order of common rows differs
    common_local_keys = tuple(k for k in local_seq if k in remote_keys)
    common_remote_keys = tuple(k for k in remote_seq if k in local_keys)

    return RowDiff(
        only_local=tuple(k for k in local_seq if k not in remote_keys),
        only_remote=tuple(k for k in remote_seq if k not in local_keys),
        cells=tuple(key_cells),
        order_changed=common_local_keys != common_remote_keys,
    )


def plan_table(
    local: TableSnapshot | None,
    remote: TableSnapshot | None,
    *,
    name: str | None = None,
    base: TableSnapshot | None = None,
    preferred_key: tuple[str, ...] = (),
) -> TablePlan:
    """Perform a pure, lossless comparison between local, remote, and base snapshots."""
    table_name = name or (local.name if local else remote.name if remote else "")
    diagnostics: list[Diagnostic] = []

    # 1. Validate snapshot shapes
    if local is not None:
        diagnostics.extend(validate_snapshot(local))
    if remote is not None:
        diagnostics.extend(validate_snapshot(remote))
    if base is not None:
        base_issues = validate_snapshot(base)
        if base_issues:
            diagnostics.append(
                Diagnostic("BASE_UNUSABLE", "Saved BASE snapshot has formatting issues", "warning")
            )
            base = None

    # 2. Determine initial status if missing or invalid
    if any(d.severity == "error" for d in diagnostics):
        status: TableStatus = "invalid"
    elif local is None:
        status = "missing_local"
        diagnostics.append(Diagnostic("LOCAL_MISSING", "Local CSV file does not exist", "warning"))
    elif remote is None:
        status = "missing_remote"
        diagnostics.append(Diagnostic("REMOTE_MISSING", "Remote Google Sheet tab does not exist", "warning"))
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

    # 3. Match rows and compute diffs
    identity, identity_issues = choose_identity(
        local, remote, base=base, preferred=preferred_key
    )
    diagnostics.extend(identity_issues)

    schema = _schema_diff(local, remote, base)
    rows = _row_diff(local, remote, identity)

    # 4. Classify divergence based on BASE
    if base is None:
        status = "unbased_divergence" if (schema.has_change or rows.has_change) else "in_sync"
    elif schema.has_change or rows.has_change:
        local_changed = (
            local.headers != base.headers
            or local.types != base.types
            or local.rows != base.rows
        )
        remote_changed = (
            remote.headers != base.headers
            or remote.rows != base.rows
        )

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


def decide_operation(
    plan: TablePlan,
    kind: OperationKind,
    *,
    force: bool = False,
    accept_schema: bool = False,
    new_types: dict[str, str] | None = None,
) -> OperationDecision:
    """Evaluate whether a push, pull, or sync is safe to execute."""
    errors: list[str] = []

    # 1. Guard against invalid or missing tables
    if plan.has_errors:
        errors.append(f"Table {plan.name} has validation errors; fix data before syncing")
        return OperationDecision(kind, plan.name, None, None, errors=tuple(errors))

    if plan.status == "missing_local" and kind == "push":
        errors.append(f"Cannot push {plan.name}: local CSV does not exist")
        return OperationDecision(kind, plan.name, None, None, errors=tuple(errors))

    if plan.status == "missing_remote" and kind == "pull":
        errors.append(f"Cannot pull {plan.name}: remote Google Sheet tab does not exist")
        return OperationDecision(kind, plan.name, None, None, errors=tuple(errors))

    # 2. Check schema alterations
    if plan.remote is not None and plan.schema.has_change and not accept_schema:
        errors.append(
            f"Schema changed in {plan.name} (columns added/removed/reordered). "
            "Inspect diff and re-run with --accept-schema"
        )

    # 3. Decide directional push / pull
    if kind == "push":
        assert plan.local is not None
        if plan.remote is not None and plan.status in ("remote_changed", "both_changed", "unbased_divergence") and not force:
            errors.append(
                f"Remote Google Sheet has changes that would be overwritten. "
                f"Review 'gitcsv diff {plan.name}' or use 'gitcsv push {plan.name} --force'"
            )

        # Result of push: remote becomes identical to local
        remote_target = TableSnapshot(
            name=plan.name,
            headers=plan.local.headers,
            types=(),  # Sheets do not store type row
            rows=plan.local.rows,
            source="sheet",
        )
        return OperationDecision(
            kind=kind,
            table=plan.name,
            local_after=None,  # local unchanged
            remote_after=remote_target,
            errors=tuple(errors),
        )

    if kind == "pull":
        assert plan.remote is not None
        if plan.local is not None and plan.status in ("local_changed", "both_changed", "unbased_divergence") and not force:
            errors.append(
                f"Local CSV has changes that would be overwritten. "
                f"Review 'gitcsv diff {plan.name}' or use 'gitcsv pull {plan.name} --force'"
            )

        # Infer or preserve types for local CSV
        resolved_types: list[str] = []
        custom_types = new_types or {}

        for header in plan.remote.headers:
            if plan.local and header in plan.local.headers:
                col_idx = plan.local.headers.index(header)
                resolved_types.append(
                    plan.local.types[col_idx] if col_idx < len(plan.local.types) else "string"
                )
            elif header in custom_types:
                resolved_types.append(custom_types[header])
            else:
                resolved_types.append("string")

        local_target = TableSnapshot(
            name=plan.name,
            headers=plan.remote.headers,
            types=tuple(resolved_types),
            rows=plan.remote.rows,
            source="csv",
        )
        return OperationDecision(
            kind=kind,
            table=plan.name,
            local_after=local_target,
            remote_after=None,  # remote unchanged
            errors=tuple(errors),
        )

    return OperationDecision(kind, plan.name, None, None, errors=tuple(errors))
