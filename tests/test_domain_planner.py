"""Tests for domain planning, identity determination, and operation decisions."""

from __future__ import annotations

import unittest

from gitcsv.core.identity import choose_identity
from gitcsv.core.models import TableSnapshot
from gitcsv.core.planner import decide_operation, plan_table
from gitcsv.core.validator import validate_snapshot


class DomainPlannerTests(unittest.TestCase):
    def test_identity_chooses_unique_id_column(self) -> None:
        local = TableSnapshot(
            name="Items",
            headers=("Id", "Name", "Cost"),
            types=("int", "string", "int"),
            rows=(("1", "Sword", "100"), ("2", "Shield", "50")),
        )
        remote = TableSnapshot(
            name="Items",
            headers=("Id", "Name", "Cost"),
            types=(),
            rows=(("1", "Sword", "120"), ("2", "Shield", "50")),
        )

        identity, diagnostics = choose_identity(local, remote)
        self.assertEqual(identity.mode, "columns")
        self.assertEqual(identity.columns, ("Id",))

    def test_identity_falls_back_to_position_when_no_unique_column(self) -> None:
        local = TableSnapshot(
            name="Dialogs",
            headers=("Speaker", "Text"),
            types=("string", "string"),
            rows=(("Dave", "Hello"), ("Dave", "Hello")),
        )
        remote = TableSnapshot(
            name="Dialogs",
            headers=("Speaker", "Text"),
            types=(),
            rows=(("Dave", "Hello"), ("Dave", "Hello")),
        )

        identity, diagnostics = choose_identity(local, remote)
        self.assertEqual(identity.mode, "position")

    def test_validation_detects_row_width_mismatch(self) -> None:
        bad_snap = TableSnapshot(
            name="Bad",
            headers=("A", "B"),
            types=("string", "string"),
            rows=(("1", "2"), ("1", "2", "3")),  # row 2 has 3 columns instead of 2
        )
        diagnostics = validate_snapshot(bad_snap)
        errors = [d for d in diagnostics if d.severity == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "ROW_WIDTH_MISMATCH")

    def test_pure_plan_detects_in_sync_and_cell_changes(self) -> None:
        local = TableSnapshot(
            name="Items",
            headers=("Id", "Cost"),
            types=("int", "int"),
            rows=(("1", "100"),),
        )
        remote = TableSnapshot(
            name="Items",
            headers=("Id", "Cost"),
            types=(),
            rows=(("1", "100"),),
        )

        # In-sync without base
        plan = plan_table(local, remote)
        self.assertEqual(plan.status, "in_sync")
        self.assertFalse(plan.has_diff)

        # Remote changed compared to local
        remote_changed = TableSnapshot(
            name="Items",
            headers=("Id", "Cost"),
            types=(),
            rows=(("1", "150"),),
        )
        plan_diff = plan_table(local, remote_changed)
        self.assertEqual(plan_diff.status, "unbased_divergence")
        self.assertTrue(plan_diff.has_diff)
        self.assertEqual(len(plan_diff.rows.cells), 1)
        self.assertEqual(plan_diff.rows.cells[0].local, "100")
        self.assertEqual(plan_diff.rows.cells[0].remote, "150")

    def test_decide_push_requires_force_on_unbased_divergence(self) -> None:
        local = TableSnapshot(
            name="Items",
            headers=("Id", "Cost"),
            types=("int", "int"),
            rows=(("1", "100"),),
        )
        remote = TableSnapshot(
            name="Items",
            headers=("Id", "Cost"),
            types=(),
            rows=(("1", "150"),),
        )
        plan = plan_table(local, remote)

        # Without force -> blocked
        decision_blocked = decide_operation(plan, "push", force=False)
        self.assertFalse(decision_blocked.allowed)
        self.assertTrue(any("force" in err.lower() for err in decision_blocked.errors))

        # With force -> allowed
        decision_forced = decide_operation(plan, "push", force=True)
        self.assertTrue(decision_forced.allowed)
        self.assertTrue(decision_forced.write_remote)
        self.assertFalse(decision_forced.write_local)


if __name__ == "__main__":
    unittest.main()
