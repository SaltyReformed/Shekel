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

import pytest

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

    @staticmethod
    def _spy_kind(calls, *, use_states_a_figure):
        """A :class:`RecurrenceConflictKind` whose resolver only records.

        ``**kwargs`` rather than a ``new_amount=None`` default, because what is
        under test in the parametrised case below is whether the argument was
        PASSED at all -- a default would make "omitted" and "passed as None"
        the same observation, which is the shape that lets a dispatch drift
        without any test noticing.
        """
        def fake_resolve(ids, action, user_id, **kwargs):
            calls.append({
                "ids": list(ids), "action": action,
                "user_id": user_id, "kwargs": dict(kwargs),
            })

        return RecurrenceConflictKind(
            # The kind's amount RULE, where this was the amount column's NAME
            # until plan step X-au-c2b: the chooser renders the figure as money
            # and a derived row carries no column to read.  Unused by the
            # dispatch under test, so it is the identity here.
            model=None, resolve_amount=lambda row: row, regenerate_fn=None,
            resolve_fn=fake_resolve, update_endpoint="x",
            use_states_a_figure=use_states_a_figure,
        )

    @pytest.mark.parametrize("use_states_a_figure", [True, False])
    def test_ignores_ids_outside_conflict_set(self, use_states_a_figure):
        """An id absent from the raised conflict set never reaches
        ``resolve_fn``, so the chooser cannot be used to mutate an arbitrary
        owned row.  ``resolve_fn`` is a spy here; the partition into use/keep
        must exclude the out-of-set id (999).

        **Both kinds are driven** (plan step balance:X-au-e, ruling **R-JD**):
        the allow-list is what this case is about and it is the same for both,
        but running only the transfer shape would leave the transaction
        shape's dispatch -- which calls ``resolve_fn`` with a DIFFERENT
        signature -- graded by nothing here.
        """
        calls = []
        kind = self._spy_kind(calls, use_states_a_figure=use_states_a_figure)
        conflict = RecurrenceConflict(overridden=[10], deleted=[20])
        decisions = {10: "keep", 20: "use", 999: "use"}  # 999 not in the set

        apply_conflict_decisions(
            kind=kind, conflict=conflict, decisions=decisions,
            new_amount=Decimal("5.00"), user_id=1,
        )

        update = next(c for c in calls if c["action"] == "update")
        keep = next(c for c in calls if c["action"] == "keep")
        assert update["ids"] == [20]            # only the in-set "use"
        assert keep["ids"] == [10]              # only the in-set "keep"
        assert 999 not in update["ids"]
        assert 999 not in keep["ids"]

    @pytest.mark.parametrize(
        "use_states_a_figure, expected",
        [(True, {"new_amount": Decimal("5.00")}), (False, {})],
    )
    def test_the_kind_decides_whether_use_carries_a_figure(
        self, use_states_a_figure, expected,
    ):
        """"Use" hands a TRANSFER a figure and a TRANSACTION nothing.

        Plan step balance:X-au-e, ruling **R-JD**.  A generated transaction
        stores no amount, so ``recurrence_engine.resolve_conflicts`` has no
        ``new_amount`` parameter at all and passing one would be a
        ``TypeError``; a generated transfer still stores one until plan step
        X-au-f, so its resolver still takes it.  This asserts the KWARGS, not
        just the value: the transaction arm's claim is that the argument is
        ABSENT, which an assertion on a value could not tell from ``None``.

        Both rows go through "use" here so the assertion is about the arm the
        branch selects rather than about which ids reach it.
        """
        calls = []
        kind = self._spy_kind(calls, use_states_a_figure=use_states_a_figure)
        conflict = RecurrenceConflict(overridden=[10], deleted=[20])

        apply_conflict_decisions(
            kind=kind, conflict=conflict, decisions={10: "use", 20: "use"},
            new_amount=Decimal("5.00"), user_id=1,
        )

        update = next(c for c in calls if c["action"] == "update")
        assert update["ids"] == [10, 20]
        assert update["kwargs"] == expected
        # "Keep" never carries a figure for either kind.
        assert next(c for c in calls if c["action"] == "keep")["kwargs"] == {}
