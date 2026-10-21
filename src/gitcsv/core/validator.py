"""CSV and tabular snapshot validation rules."""

from __future__ import annotations

from gitcsv.core.models import Diagnostic, TableSnapshot

MAX_COLUMNS = 300
MAX_ROWS = 100_000
MAX_CELL_BYTES = 50_000


def validate_snapshot(snapshot: TableSnapshot) -> tuple[Diagnostic, ...]:
    """Validate header format, type row, width consistency, and size limits."""
    diagnostics: list[Diagnostic] = []

    # 1. Header validation
    if not snapshot.headers:
        diagnostics.append(Diagnostic("EMPTY_TABLE", "Table has no columns/headers", "error"))
        return tuple(diagnostics)

    if len(snapshot.headers) > MAX_COLUMNS:
        diagnostics.append(
            Diagnostic(
                "TOO_MANY_COLUMNS",
                f"Column count {len(snapshot.headers)} exceeds maximum {MAX_COLUMNS}",
                "error",
            )
        )

    # Check for empty or duplicate headers
    seen_headers: set[str] = set()
    for col_idx, header in enumerate(snapshot.headers):
        clean = header.strip()

        if not clean:
            diagnostics.append(
                Diagnostic("EMPTY_HEADER", f"Header at column index {col_idx} is empty", "error")
            )
            continue

        if clean in seen_headers:
            diagnostics.append(
                Diagnostic("DUPLICATE_HEADER", f"Duplicate header name: {clean!r}", "error")
            )
        seen_headers.add(clean)

    # 2. Type row validation (for CSV files)
    if snapshot.types:
        if len(snapshot.types) != len(snapshot.headers):
            diagnostics.append(
                Diagnostic(
                    "TYPE_WIDTH_MISMATCH",
                    f"Type row width ({len(snapshot.types)}) does not match headers ({len(snapshot.headers)})",
                    "error",
                    line=2,
                )
            )

    # 3. Row size and width validation
    if len(snapshot.rows) > MAX_ROWS:
        diagnostics.append(
            Diagnostic(
                "TOO_MANY_ROWS",
                f"Row count {len(snapshot.rows)} exceeds maximum {MAX_ROWS}",
                "error",
            )
        )

    expected_width = len(snapshot.headers)
    line_offset = 3 if snapshot.types else 2

    for row_idx, row in enumerate(snapshot.rows):
        line_num = row_idx + line_offset

        if len(row) != expected_width:
            diagnostics.append(
                Diagnostic(
                    "ROW_WIDTH_MISMATCH",
                    f"Row {row_idx + 1} has {len(row)} cells, expected {expected_width}",
                    "error",
                    line=line_num,
                )
            )

        for cell in row:
            if len(cell.encode("utf-8")) > MAX_CELL_BYTES:
                diagnostics.append(
                    Diagnostic(
                        "CELL_TOO_LARGE",
                        f"Cell in row {row_idx + 1} exceeds {MAX_CELL_BYTES} bytes limit",
                        "error",
                        line=line_num,
                    )
                )
                break

    return tuple(diagnostics)
