"""Validate source shape before any keyed lookup can discard a row."""

from __future__ import annotations

from collections import Counter

from fccsv3.domain.model import Diagnostic, TableSnapshot


def validate_snapshot(
    snapshot: TableSnapshot,
    *,
    max_rows: int = 20_000,
    max_columns: int = 100,
) -> tuple[Diagnostic, ...]:
    issues: list[Diagnostic] = []
    headers = snapshot.headers
    if not headers:
        return (Diagnostic("NO_HEADER", f"{snapshot.name}: missing header row"),)
    if len(headers) > max_columns:
        issues.append(
            Diagnostic("TOO_MANY_COLUMNS", f"{len(headers)} columns exceed {max_columns}")
        )
    if len(snapshot.rows) > max_rows:
        issues.append(Diagnostic("TOO_MANY_ROWS", f"{len(snapshot.rows)} rows exceed {max_rows}"))
    for header, count in Counter(headers).items():
        if not header.strip():
            issues.append(Diagnostic("BLANK_HEADER", "blank column header"))
        elif count > 1:
            issues.append(Diagnostic("DUPLICATE_HEADER", f"duplicate header {header!r}"))
        elif header != header.strip():
            issues.append(Diagnostic("HEADER_WHITESPACE", f"header has whitespace: {header!r}"))
    if snapshot.source != "sheet" and len(snapshot.types) != len(headers):
        issues.append(
            Diagnostic(
                "TYPE_ROW_WIDTH",
                f"type row has {len(snapshot.types)} cells; header has {len(headers)}",
                line=2,
            )
        )
    for index, row in enumerate(snapshot.rows):
        width = snapshot.raw_widths[index] if index < len(snapshot.raw_widths) else len(row)
        line = (
            snapshot.line_numbers[index]
            if index < len(snapshot.line_numbers)
            else index + (3 if snapshot.source != "sheet" else 2)
        )
        if (snapshot.source != "sheet" and width != len(headers)) or width > len(headers):
            issues.append(
                Diagnostic(
                    "ROW_WIDTH",
                    f"row has {width} cells; header has {len(headers)}",
                    line=line,
                )
            )
    return tuple(issues)
