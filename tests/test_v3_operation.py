"""Direction, force, and schema decisions use complete copyable commands."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fccsv3.domain.model import TableSnapshot  # noqa: E402
from fccsv3.domain.operation import decide_operation  # noqa: E402
from fccsv3.domain.plan import plan_table  # noqa: E402


def table(
    rows: tuple[tuple[str, ...], ...],
    *,
    source: str = "csv",
    headers: tuple[str, ...] = ("Id", "Value"),
    types: tuple[str, ...] = ("number", "text"),
) -> TableSnapshot:
    return TableSnapshot(
        "Items", headers, rows, types=() if source == "sheet" else types,
        source=source,  # type: ignore[arg-type]
    )


class OperationTests(unittest.TestCase):
    def test_unbased_push_requires_explicit_direction(self) -> None:
        plan = plan_table(table((("1", "CSV"),)), table((("1", "Sheet"),), source="sheet"))
        blocked = decide_operation(plan, "push")
        self.assertFalse(blocked.allowed)
        self.assertIn("push TABLE --force", blocked.errors[0])
        forced = decide_operation(plan, "push", force=True)
        self.assertTrue(forced.allowed)
        self.assertTrue(forced.write_remote)
        self.assertEqual(forced.remote_after.rows, (("1", "CSV"),))

    def test_remote_change_blocks_push_without_force(self) -> None:
        base = table((("1", "Old"),), source="base")
        plan = plan_table(
            table((("1", "CSV"),)),
            table((("1", "Sheet"),), source="sheet"),
            base=base,
        )
        blocked = decide_operation(plan, "push")
        self.assertFalse(blocked.allowed)
        self.assertIn("sync TABLE", blocked.errors[0])

    def test_local_change_blocks_pull_without_force(self) -> None:
        base = table((("1", "Old"),), source="base")
        plan = plan_table(
            table((("1", "CSV"),)),
            table((("1", "Sheet"),), source="sheet"),
            base=base,
        )
        blocked = decide_operation(plan, "pull")
        self.assertFalse(blocked.allowed)
        self.assertIn("pull TABLE --force", blocked.errors[0])

    def test_sync_merges_independent_cells(self) -> None:
        headers = ("Id", "Name", "Price")
        types = ("number", "text", "number")
        base = table((("1", "Apple", "100"),), source="base", headers=headers, types=types)
        local = table((("1", "Apricot", "100"),), headers=headers, types=types)
        remote = table((("1", "Apple", "120"),), source="sheet", headers=headers)
        decision = decide_operation(plan_table(local, remote, base=base), "sync")
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.write_local)
        self.assertTrue(decision.write_remote)
        self.assertEqual(decision.local_after.rows, (("1", "Apricot", "120"),))

    def test_sync_rejects_force_without_direction(self) -> None:
        plan = plan_table(table((("1", "A"),)), table((("1", "A"),), source="sheet"))
        self.assertIn("no force direction", decide_operation(plan, "sync", force=True).errors[0])

    def test_sync_can_adopt_equal_unbased_table_without_writing_values(self) -> None:
        plan = plan_table(table((("1", "A"),)), table((("1", "A"),), source="sheet"))
        decision = decide_operation(plan, "sync")
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.write_local)
        self.assertFalse(decision.write_remote)

    def test_pull_new_sheet_column_needs_type_and_preserves_value(self) -> None:
        local = table((("1", "A"),))
        remote = table(
            (("1", "A", "new"),), source="sheet", headers=("Id", "Value", "Tag")
        )
        plan = plan_table(local, remote)
        self.assertIn("--accept-schema", decide_operation(plan, "pull", force=True).errors[0])
        missing = decide_operation(plan, "pull", force=True, accept_schema=True)
        self.assertIn("Tag", missing.errors[0])
        ready = decide_operation(
            plan, "pull", force=True, accept_schema=True, new_types={"Tag": "text"}
        )
        self.assertTrue(ready.allowed)
        self.assertEqual(ready.local_after.headers, ("Id", "Value", "Tag"))
        self.assertEqual(ready.local_after.rows[0][-1], "new")
        self.assertEqual(ready.local_after.types[-1], "text")

    def test_force_does_not_bypass_invalid_duplicate_header(self) -> None:
        local = table((("1", "A"),), headers=("Id", "Id"))
        remote = table((("1", "A"),), source="sheet", headers=("Id", "Value"))
        decision = decide_operation(plan_table(local, remote), "push", force=True)
        self.assertFalse(decision.allowed)
        self.assertIn("validate both sides", decision.errors[0])

    def test_pull_type_only_local_change_keeps_type(self) -> None:
        base = table((("1", "A"),), source="base")
        local = table((("1", "A"),), types=("text", "text"))
        remote = table((("1", "B"),), source="sheet")
        decision = decide_operation(plan_table(local, remote, base=base), "pull")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.local_after.types, ("text", "text"))


if __name__ == "__main__":
    unittest.main()
