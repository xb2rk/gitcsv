"""Tests for three-way merge logic and conflict resolution."""

from __future__ import annotations

import unittest

from gitcsv.core.merger import decide_sync, three_way_merge
from gitcsv.core.models import TableSnapshot
from gitcsv.core.planner import plan_table


class DomainMergerTests(unittest.TestCase):
    def test_three_way_merge_independent_edits_succeed(self) -> None:
        # Base: Item 1 has Cost=100, Stock=5
        base = TableSnapshot(
            name="Items",
            headers=("Id", "Cost", "Stock"),
            types=("int", "int", "int"),
            rows=(("1", "100", "5"),),
        )
        # Local edited Cost to 120
        local = TableSnapshot(
            name="Items",
            headers=("Id", "Cost", "Stock"),
            types=("int", "int", "int"),
            rows=(("1", "120", "5"),),
        )
        # Remote edited Stock to 10
        remote = TableSnapshot(
            name="Items",
            headers=("Id", "Cost", "Stock"),
            types=(),
            rows=(("1", "100", "10"),),
        )

        plan = plan_table(local, remote, base=base)
        result = three_way_merge(plan)

        self.assertFalse(result.has_conflicts)
        self.assertIsNotNone(result.merged_local)
        # Merged result should have both Cost=120 and Stock=10
        self.assertEqual(result.merged_local.rows, (("1", "120", "10"),))

    def test_three_way_merge_conflicting_cell_edits_detected(self) -> None:
        base = TableSnapshot(
            name="Items",
            headers=("Id", "Cost"),
            types=("int", "int"),
            rows=(("1", "100"),),
        )
        local = TableSnapshot(
            name="Items",
            headers=("Id", "Cost"),
            types=("int", "int"),
            rows=(("1", "120"),),
        )
        remote = TableSnapshot(
            name="Items",
            headers=("Id", "Cost"),
            types=(),
            rows=(("1", "150"),),
        )

        plan = plan_table(local, remote, base=base)
        result = three_way_merge(plan, strategy="manual")

        self.assertTrue(result.has_conflicts)
        self.assertEqual(len(result.conflicts), 1)
        self.assertEqual(result.conflicts[0].local_val, "120")
        self.assertEqual(result.conflicts[0].remote_val, "150")

    def test_three_way_merge_strategy_ours_and_theirs(self) -> None:
        base = TableSnapshot(
            name="Items",
            headers=("Id", "Cost"),
            types=("int", "int"),
            rows=(("1", "100"),),
        )
        local = TableSnapshot(
            name="Items",
            headers=("Id", "Cost"),
            types=("int", "int"),
            rows=(("1", "120"),),
        )
        remote = TableSnapshot(
            name="Items",
            headers=("Id", "Cost"),
            types=(),
            rows=(("1", "150"),),
        )

        plan = plan_table(local, remote, base=base)

        # Strategy ours: pick local 120
        res_ours = three_way_merge(plan, strategy="ours")
        self.assertFalse(res_ours.has_conflicts)
        self.assertEqual(res_ours.merged_local.rows, (("1", "120"),))

        # Strategy theirs: pick remote 150
        res_theirs = three_way_merge(plan, strategy="theirs")
        self.assertFalse(res_theirs.has_conflicts)
        self.assertEqual(res_theirs.merged_local.rows, (("1", "150"),))


if __name__ == "__main__":
    unittest.main()
