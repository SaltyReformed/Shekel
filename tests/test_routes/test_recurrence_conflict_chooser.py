"""
Unit tests for ``app.routes._recurrence_conflict_chooser`` (Loop B, P3).

The chooser's own contracts -- parsing the per-instance keep/use decisions a
submit carries, and dispatching them without ever touching a row outside the
raised conflict set.  Split out of ``test_recurrence_form_helpers`` alongside
the module they cover (plan step R2e-1).

The route-level behaviour of the regeneration these helpers sit under lives in
``test_recurrence_clear.py`` (a cleared recurrence) and in the template /
transfer CRUD suites (an amount change).
"""
from decimal import Decimal

from werkzeug.datastructures import MultiDict

from app.exceptions import RecurrenceConflict
from app.routes._recurrence_conflict_chooser import (
    RecurrenceConflictKind,
    apply_conflict_decisions,
    parse_conflict_decisions,
)


class TestParseConflictDecisions:
    """The chooser's per-instance keep/use parser (Loop B, P3)."""

    def test_no_marker_returns_none(self):
        """A first-time edit submit (no chooser marker) parses to None, so the
        route knows to render the chooser rather than resolve."""
        form = MultiDict({"default_amount": "10.00"})
        assert parse_conflict_decisions(form) is None

    def test_marker_parses_valid_decisions_and_drops_malformed(self):
        """With the marker, valid ``conflict_decision_<id>`` pairs parse; a
        non-integer id or an unrecognized value is dropped (the surviving ids
        are re-checked against the real conflict set at apply time)."""
        form = MultiDict({
            "conflict_apply": "1",
            "conflict_decision_5": "keep",
            "conflict_decision_9": "use",
            "conflict_decision_x": "use",     # non-integer id -> dropped
            "conflict_decision_7": "bogus",   # invalid value -> dropped
            "default_amount": "10.00",
        })
        assert parse_conflict_decisions(form) == {5: "keep", 9: "use"}

    def test_marker_without_decisions_is_empty_map(self):
        """The marker with no decision fields parses to an empty map (an Apply
        that resolves nothing), distinct from None (render the chooser)."""
        assert parse_conflict_decisions(MultiDict({"conflict_apply": "1"})) == {}


class TestApplyConflictDecisions:
    """The chooser's resolution dispatch (Loop B, P3)."""

    def test_ignores_ids_outside_conflict_set(self):
        """An id absent from the raised conflict set never reaches
        ``resolve_fn``, so the chooser cannot be used to mutate an arbitrary
        owned row.  ``resolve_fn`` is a spy here; the partition into use/keep
        must exclude the out-of-set id (999)."""
        calls = []

        def fake_resolve(ids, action, user_id, new_amount=None):
            calls.append({
                "ids": list(ids), "action": action,
                "user_id": user_id, "new_amount": new_amount,
            })

        kind = RecurrenceConflictKind(
            model=None, amount_attr="amount", regenerate_fn=None,
            resolve_fn=fake_resolve, update_endpoint="x",
        )
        conflict = RecurrenceConflict(overridden=[10], deleted=[20])
        decisions = {10: "keep", 20: "use", 999: "use"}  # 999 not in the set

        apply_conflict_decisions(
            kind=kind, conflict=conflict, decisions=decisions,
            new_amount=Decimal("5.00"), user_id=1,
        )

        update = next(c for c in calls if c["action"] == "update")
        keep = next(c for c in calls if c["action"] == "keep")
        assert update["ids"] == [20]            # only the in-set "use"
        assert update["new_amount"] == Decimal("5.00")
        assert keep["ids"] == [10]              # only the in-set "keep"
        assert 999 not in update["ids"]
        assert 999 not in keep["ids"]
