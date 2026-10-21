"""Three-way merge preserves independent edits and refuses ambiguous changes."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fccsv3.domain.merge import merge3  # noqa: E402
from fccsv3.domain.model import TableSnapshot  # noqa: E402
from fccsv3.domain.plan import plan_table  # noqa: E402


def snapshot(
    rows: tuple[tuple[str, ...], ...],
    *,
    source: str = "csv",
    headers: tuple[str, ...] = ("Id", "Name", "Price"),
    types: tuple[str, ...] = ("number", "text", "number"),
) -> TableSnapshot:
    return TableSnapshot(
        "Items", headers, rows, types=() if source == "sheet" else types,
        source=source,  # type: ignore[arg-type]
    )


def merge(
    base_rows: tuple[tuple[str, ...], ...],
    local_rows: tuple[tuple[str, ...], ...],
    remote_rows: tuple[tuple[str, ...], ...],
    **options: object,
):
    base = snapshot(base_rows, source="base")
    local = snapshot(local_rows, **options)
    remote = snapshot(remote_rows, source="sheet")
    return merge3(plan_table(local, remote, base=base))


class MergeTests(unittest.TestCase):
    def test_independent_cell_edits_merge(self) -> None:
        result = merge(
            (("1", "Apple", "100"),),
            (("1", "Apricot", "100"),),
            (("1", "Apple", "120"),),
        )
        self.assertTrue(result.clean)
        self.assertEqual(result.snapshot.rows, (("1", "Apricot", "120"),))

    def test_same_cell_edit_is_conflict(self) -> None:
        result = merge(
            (("1", "Apple", "100"),),
            (("1", "Apricot", "100"),),
            (("1", "Pear", "100"),),
        )
        self.assertFalse(result.clean)
        self.assertEqual((result.conflicts[0].kind, result.conflicts[0].column), ("cell", "Name"))

    def test_delete_versus_edit_is_conflict(self) -> None:
        result = merge(
            (("1", "Apple", "100"),),
            (),
            (("1", "Apple", "120"),),
        )
        self.assertFalse(result.clean)
        self.assertEqual(result.conflicts[0].kind, "row")

    def test_one_sided_addition_keeps_position_and_other_side_cell_edit(self) -> None:
        result = merge(
            (("1", "Apple", "100"),),
            (("2", "Pear", "80"), ("1", "Apple", "100")),
            (("1", "Apple", "120"),),
        )
        self.assertTrue(result.clean)
        self.assertEqual(
            result.snapshot.rows,
            (("2", "Pear", "80"), ("1", "Apple", "120")),
        )

    def test_concurrent_distinct_additions_need_order_review(self) -> None:
        result = merge(
            (("1", "Apple", "100"),),
            (("1", "Apple", "100"), ("2", "Pear", "80")),
            (("1", "Apple", "100"), ("3", "Plum", "90")),
        )
        self.assertFalse(result.clean)
        self.assertEqual(result.conflicts[0].kind, "structure")

    def test_positional_both_changed_refuses_unsafe_merge(self) -> None:
        headers = ("Name", "Price")
        base = snapshot((("Apple", "100"),), source="base", headers=headers, types=("text", "number"))
        local = snapshot((("Apricot", "100"),), headers=headers, types=("text", "number"))
        remote = snapshot((("Apple", "120"),), source="sheet", headers=headers)
        result = merge3(plan_table(local, remote, base=base))
        self.assertFalse(result.clean)
        self.assertEqual(result.conflicts[0].kind, "identity")

    def test_local_type_change_and_remote_cell_edit_keep_both(self) -> None:
        base = snapshot((("1", "Apple", "100"),), source="base")
        local = snapshot((("1", "Apple", "100"),), types=("text", "text", "number"))
        remote = snapshot((("1", "Apple", "120"),), source="sheet")
        result = merge3(plan_table(local, remote, base=base))
        self.assertTrue(result.clean)
        self.assertEqual(result.snapshot.rows, (("1", "Apple", "120"),))
        self.assertEqual(result.snapshot.types, ("text", "text", "number"))

    def test_schema_change_requires_review(self) -> None:
        base = snapshot((("1", "Apple", "100"),), source="base")
        local = snapshot((('1', 'Apple', '100', 'A'),), headers=("Id", "Name", "Price", "Tag"),
                         types=("number", "text", "number", "text"))
        remote = snapshot((("1", "Apple", "100"),), source="sheet")
        result = merge3(plan_table(local, remote, base=base))
        self.assertFalse(result.clean)
        self.assertEqual(result.conflicts[0].kind, "schema")

    def test_no_base_cannot_merge_divergence(self) -> None:
        local = snapshot((("1", "Apricot", "100"),))
        remote = snapshot((("1", "Apple", "120"),), source="sheet")
        result = merge3(plan_table(local, remote))
        self.assertFalse(result.clean)
        self.assertEqual(result.conflicts[0].kind, "base")


if __name__ == "__main__":
    unittest.main()
