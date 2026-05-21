"""Tests for the centralized balance-contributing status predicate (E-15, MED-02).

MED-02 / D6-09 (08_findings.md:1456) identified that the "is this
transaction contributing to a balance" rule is hand-reproduced across
20+ sites in three forms (Python skip, SQLAlchemy filter, Jinja
conditional) plus two identically-bodied helpers under different names.
``app/utils/balance_predicates.py`` collapses every form onto one
``ref_cache``-backed source. This file pins the predicate's behavior at
the boundaries that matter:

- Every status's contribution decision under the seeded
  ``excludes_from_balance`` matrix (``Projected``, ``Paid``,
  ``Received``, ``Credit``, ``Cancelled``, ``Settled``).
- Soft-deleted rows always exclude regardless of status.
- The SQLAlchemy clause and the Python predicate classify a mixed-status
  seeded set identically -- the load-bearing parity test that prevents
  the SQL filter and the in-Python loop from drifting apart.
- The cached excluded-status ID set is exactly ``{Credit.id,
  Cancelled.id}`` (the only two seeded rows with
  ``excludes_from_balance=True`` per ``app/ref_seeds.py``).
- Mechanical source-level guard that the predicate never compares
  against the status display string, only against IDs and the
  ``excludes_from_balance`` boolean (E-15 / CLAUDE.md rule 4).

Test IDs C2-1..C2-8 trace to ``remediation_plan.md`` Section 9
"Commit 2" subsection E. ``test_is_projected_*`` is a small refinement
beyond the plan's E table that locks the contract of the
``is_projected`` helper (the function is part of the module's public
API per subsection C; the parity tests cover ``is_balance_contributing``
and the clause but not the pure-status-equality predicate).
"""
import ast
from decimal import Decimal
from pathlib import Path

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.ref import Status
from app.models.transaction import Transaction
from app.utils.balance_predicates import (
    balance_contributing_clause,
    balance_excluded_status_ids,
    is_balance_contributing,
    is_cancelled,
    is_credit,
    is_done,
    is_projected,
    is_projected_clause,
)


def _make_txn(db, seed_user, seed_periods, status_member, *, is_deleted=False):
    """Build, persist, and return a Transaction with the given status.

    Centralizes the boilerplate so each test pins its own status and
    optional ``is_deleted`` flag without re-stating account/period/type
    plumbing. Uses cached IDs (``ref_cache.status_id``) per E-15; the
    transaction type comes from the seeded ``txn_type_id`` cache rather
    than a name-string query so the helper itself never violates the
    rule the module under test enforces.
    """
    txn = Transaction(
        pay_period_id=seed_periods[0].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=ref_cache.status_id(status_member),
        name=f"Test-{status_member.name}",
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        estimated_amount=Decimal("100.00"),
        is_deleted=is_deleted,
    )
    db.session.add(txn)
    db.session.flush()
    return txn


class TestIsBalanceContributing:
    """Hand-verified status-by-status pins for ``is_balance_contributing``.

    The seeded matrix (``app/ref_seeds.py``) sets
    ``excludes_from_balance=True`` for ``Credit`` and ``Cancelled``
    only; every other status contributes. ``is_deleted`` overrides
    status: any soft-deleted row contributes nothing, mirroring
    ``Transaction.effective_amount``'s own gate exactly.
    """

    def test_projected_contributes(self, app, db, seed_user, seed_periods):
        """C2-1: a Projected txn contributes to the balance.

        ``Projected`` has ``excludes_from_balance=False`` in the seed
        matrix and ``is_deleted`` defaults to ``False``, so the
        predicate must return ``True``. This is the dominant case: the
        whole reason the grid renders a projected balance series.
        """
        with app.app_context():
            txn = _make_txn(db, seed_user, seed_periods, StatusEnum.PROJECTED)
            assert is_balance_contributing(txn) is True

    def test_credit_excluded(self, app, db, seed_user, seed_periods):
        """C2-2: a Credit txn does not contribute to the balance.

        ``Credit`` is one of the two seed rows with
        ``excludes_from_balance=True`` (settled elsewhere via credit
        card, so the projected checking balance must not subtract it).
        """
        with app.app_context():
            txn = _make_txn(db, seed_user, seed_periods, StatusEnum.CREDIT)
            assert is_balance_contributing(txn) is False

    def test_cancelled_excluded(self, app, db, seed_user, seed_periods):
        """C2-3: a Cancelled txn does not contribute to the balance.

        ``Cancelled`` is the second seed row with
        ``excludes_from_balance=True``. Pinning ``Credit`` and
        ``Cancelled`` separately documents that the predicate honors
        the whole exclusion set, not just one row.
        """
        with app.app_context():
            txn = _make_txn(db, seed_user, seed_periods, StatusEnum.CANCELLED)
            assert is_balance_contributing(txn) is False

    def test_settled_contributes(self, app, db, seed_user, seed_periods):
        """C2-4: a Settled txn contributes to the balance.

        ``Settled`` has ``is_settled=True`` but
        ``excludes_from_balance=False`` -- the row is reconciled, not
        excluded -- so it must contribute. Pins the audit's observation
        that ``is_settled`` and ``excludes_from_balance`` are
        orthogonal flags; the predicate consults the latter.
        """
        with app.app_context():
            txn = _make_txn(db, seed_user, seed_periods, StatusEnum.SETTLED)
            assert is_balance_contributing(txn) is True

    def test_soft_deleted_excluded(self, app, db, seed_user, seed_periods):
        """C2-5: a soft-deleted txn never contributes, even with a
        contributing status.

        ``is_deleted=True`` is the first short-circuit in
        ``is_balance_contributing`` (mirroring
        ``Transaction.effective_amount``'s own first guard). The status
        here is ``Projected`` -- which would normally contribute -- so
        the test specifically isolates the soft-delete gate rather than
        a coincidental status exclusion.
        """
        with app.app_context():
            txn = _make_txn(
                db, seed_user, seed_periods,
                StatusEnum.PROJECTED, is_deleted=True,
            )
            assert is_balance_contributing(txn) is False


class TestPredicateClauseParity:
    """The load-bearing parity test (C2-6).

    The Python predicate and the SQLAlchemy clause are generated from
    the same ``balance_excluded_status_ids()`` accessor, but nothing
    other than this test enforces that they classify any given mixed
    set identically. The predicate could be edited to add a new gate
    without the clause picking it up (or vice versa), and the
    individual per-status pins above would still pass. This test seeds
    every distinct case the predicate has -- one per excluded status,
    one per contributing status, plus a soft-deleted row -- and
    asserts the ID set returned by an ORM filter equals the ID set
    returned by Python iteration. If they ever disagree, the SQL filter
    and the in-Python loop have drifted, and a balance somewhere is
    wrong.
    """

    def test_clause_matches_predicate(self, app, db, seed_user, seed_periods):
        """C2-6: clause-filtered IDs == Python-filtered IDs on a realistic mix.

        Seeded mix: one txn per StatusEnum member (6 rows) plus a
        second Projected txn marked ``is_deleted=True`` (7 rows total).
        Expected classification:

        - Projected (live): contributing
        - Paid:             contributing
        - Received:         contributing
        - Credit:           excluded (excludes_from_balance)
        - Cancelled:        excluded (excludes_from_balance)
        - Settled:          contributing
        - Projected (soft-deleted): excluded (is_deleted)

        Both paths must return exactly the four contributing IDs.
        """
        with app.app_context():
            txns_by_status = {
                member: _make_txn(db, seed_user, seed_periods, member)
                for member in StatusEnum
            }
            deleted_projected = _make_txn(
                db, seed_user, seed_periods,
                StatusEnum.PROJECTED, is_deleted=True,
            )

            all_txns = list(txns_by_status.values()) + [deleted_projected]
            all_ids = {t.id for t in all_txns}

            python_contributing = {
                t.id for t in all_txns if is_balance_contributing(t)
            }
            sql_contributing = {
                row.id for row in (
                    db.session.query(Transaction.id)
                    .filter(Transaction.id.in_(all_ids))
                    .filter(balance_contributing_clause())
                    .all()
                )
            }

            expected = {
                txns_by_status[StatusEnum.PROJECTED].id,
                txns_by_status[StatusEnum.DONE].id,
                txns_by_status[StatusEnum.RECEIVED].id,
                txns_by_status[StatusEnum.SETTLED].id,
            }

            assert python_contributing == expected, (
                "Python predicate classified a different set than expected; "
                "check is_balance_contributing against the seed matrix."
            )
            assert sql_contributing == expected, (
                "SQLAlchemy clause classified a different set than expected; "
                "check balance_contributing_clause against the seed matrix."
            )
            assert python_contributing == sql_contributing, (
                "Predicate and clause disagree -- "
                "balance_predicates.py has drifted; SQL filters and "
                "Python loops will silently produce different balances."
            )


class TestBalanceExcludedStatusIds:
    """Pins the cached exclusion ID set."""

    def test_excluded_ids_are_credit_cancelled(self, app, db):
        """C2-7: the set is exactly {Credit.id, Cancelled.id}.

        Derives the expected IDs by querying the seeded ``Status``
        rows whose ``excludes_from_balance=True`` (independent of the
        cache) and asserts the cache returns the same set. The query
        is the canonical definition of "excluded from balance"; the
        cache exists only to avoid the per-call DB round trip. Any
        future change to the seed flags is caught here without
        re-pinning the test.
        """
        with app.app_context():
            seeded_excluded_ids = {
                row.id for row in (
                    db.session.query(Status)
                    .filter(Status.excludes_from_balance.is_(True))
                    .all()
                )
            }
            assert seeded_excluded_ids == {
                ref_cache.status_id(StatusEnum.CREDIT),
                ref_cache.status_id(StatusEnum.CANCELLED),
            }, (
                "Seed matrix in app/ref_seeds.py has changed -- only "
                "Credit and Cancelled should carry excludes_from_balance=True"
            )
            assert balance_excluded_status_ids() == frozenset(seeded_excluded_ids)

    def test_excluded_ids_is_frozenset(self, app, db):
        """``balance_excluded_status_ids`` returns an immutable frozenset.

        Mutation of the returned set could corrupt the cached lookup
        for every subsequent caller. ``frozenset`` makes the contract
        unforgivable: ``.add`` raises ``AttributeError``.
        """
        with app.app_context():
            assert isinstance(balance_excluded_status_ids(), frozenset)


class TestPredicateUsesIdsNotNames:
    """Mechanical source-level guard (C2-8).

    The whole point of MED-02 is to keep the predicate ID-based; the
    grep gate in the commit prompt asserts the same property at the
    text level, but a future edit could legally route through a
    ``.name`` comparison via an alias or attribute chain that the grep
    misses. This test parses the module with ``ast`` and asserts no
    ``Compare`` node consults a ``.name`` attribute on either side --
    the structural form of "comparing against the display string." If
    a future change needs the status's display name, that's a separate
    code path (logging, error messages) and belongs in a different
    helper, not in the predicate module.
    """

    def test_predicate_uses_ids_not_names(self):
        """C2-8: no ``Compare`` node in the predicate module touches a ``.name``.

        Walks the module AST and inspects every ``Compare`` node's
        left side and every comparator for an ``Attribute(attr="name")``
        access. A match would mean a status-or-other ``.name`` value
        is participating in a comparison, which is the exact violation
        E-15 forbids in business logic.
        """
        source_path = Path(__file__).resolve().parents[2] / "app" / "utils" / "balance_predicates.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))

        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            for operand in [node.left, *node.comparators]:
                if isinstance(operand, ast.Attribute) and operand.attr == "name":
                    violations.append(
                        f"line {node.lineno}: Compare touches `.name` "
                        f"({ast.unparse(operand)})"
                    )

        assert not violations, (
            "balance_predicates.py compares against a `.name` attribute; "
            "E-15 / CLAUDE.md rule 4 requires ID-based logic. "
            "Violations: " + "; ".join(violations)
        )


class TestIsProjected:
    """Pins ``is_projected`` (refinement beyond the plan's E table).

    The plan's subsection C lists ``is_projected`` as part of the
    module's public API but the E test table only specifies tests for
    ``is_balance_contributing`` and the clause/IDs. Without a test the
    function's contract -- "pure status equality, does not consider
    is_deleted" -- is unenforced; a future refactor could quietly
    fold in an ``is_deleted`` short-circuit and the inline call sites
    that this helper replaces in Commit 29 would silently change
    semantics. These tests lock the documented contract.
    """

    def test_is_projected_true_for_projected_txn(
        self, app, db, seed_user, seed_periods,
    ):
        """A txn with ``status_id == Projected.id`` returns ``True``."""
        with app.app_context():
            txn = _make_txn(db, seed_user, seed_periods, StatusEnum.PROJECTED)
            assert is_projected(txn) is True

    def test_is_projected_false_for_non_projected_txn(
        self, app, db, seed_user, seed_periods,
    ):
        """A txn with any other status returns ``False``.

        Iterates every non-Projected ``StatusEnum`` member so a future
        new status added to the enum cannot silently be classified as
        Projected by omission.
        """
        with app.app_context():
            for member in StatusEnum:
                if member is StatusEnum.PROJECTED:
                    continue
                txn = _make_txn(db, seed_user, seed_periods, member)
                assert is_projected(txn) is False, (
                    f"is_projected returned True for {member.name}"
                )

    def test_is_projected_ignores_is_deleted(
        self, app, db, seed_user, seed_periods,
    ):
        """``is_projected`` is pure status equality; ``is_deleted`` is
        irrelevant.

        Pins the documented contract: callers needing the combined
        "live and projected" gate compose ``is_projected`` with
        ``is_balance_contributing`` (or with the ``is_deleted`` check
        directly). A soft-deleted Projected row is still "projected"
        for the purposes of this predicate.
        """
        with app.app_context():
            txn = _make_txn(
                db, seed_user, seed_periods,
                StatusEnum.PROJECTED, is_deleted=True,
            )
            assert is_projected(txn) is True


class TestIsCredit:
    """Pins ``is_credit`` (Commit 29 / MED-02 residual).

    Centralizes the inline ``status_id == credit_id`` /
    ``status_id != credit_id`` comparisons in ``credit_workflow.py``
    (mark-as-credit idempotency check; unmark-credit precondition
    guard) and ``entry_service.create_entry`` (block entries on
    credit-status transactions). The contract mirrors
    ``is_projected``: pure status equality, ``is_deleted`` irrelevant.
    """

    def test_is_credit_true_for_credit_txn(
        self, app, db, seed_user, seed_periods,
    ):
        """A txn with ``status_id == Credit.id`` returns ``True``."""
        with app.app_context():
            txn = _make_txn(db, seed_user, seed_periods, StatusEnum.CREDIT)
            assert is_credit(txn) is True

    def test_is_credit_false_for_non_credit_txn(
        self, app, db, seed_user, seed_periods,
    ):
        """A txn with any other status returns ``False``.

        Iterates every non-Credit ``StatusEnum`` member so a future
        new status added to the enum cannot silently be classified as
        Credit by omission.
        """
        with app.app_context():
            for member in StatusEnum:
                if member is StatusEnum.CREDIT:
                    continue
                txn = _make_txn(db, seed_user, seed_periods, member)
                assert is_credit(txn) is False, (
                    f"is_credit returned True for {member.name}"
                )

    def test_is_credit_ignores_is_deleted(
        self, app, db, seed_user, seed_periods,
    ):
        """``is_credit`` is pure status equality; ``is_deleted`` is
        irrelevant.

        The credit_workflow idempotency guards call this on the
        post-lock locked row whose ``is_deleted`` state is not
        consulted; pinning that the predicate honors that contract.
        """
        with app.app_context():
            txn = _make_txn(
                db, seed_user, seed_periods,
                StatusEnum.CREDIT, is_deleted=True,
            )
            assert is_credit(txn) is True


class TestIsCancelled:
    """Pins ``is_cancelled`` (Commit 29 / MED-02 residual).

    Centralizes the inline ``status_id == cancelled_id`` comparisons
    in ``app/routes/grid.py`` (skip-cancelled row-key collection,
    mirroring the templates' ``!= STATUS_CANCELLED`` guards) and
    ``entry_service.create_entry`` (block entries on
    cancelled-status transactions). Narrower than
    ``is_balance_contributing``: ``Credit`` is excluded from balance
    but is NOT cancelled, and the grid still renders the Credit row.
    """

    def test_is_cancelled_true_for_cancelled_txn(
        self, app, db, seed_user, seed_periods,
    ):
        """A txn with ``status_id == Cancelled.id`` returns ``True``."""
        with app.app_context():
            txn = _make_txn(
                db, seed_user, seed_periods, StatusEnum.CANCELLED,
            )
            assert is_cancelled(txn) is True

    def test_is_cancelled_false_for_non_cancelled_txn(
        self, app, db, seed_user, seed_periods,
    ):
        """A txn with any other status returns ``False`` -- including
        ``Credit`` (which shares ``excludes_from_balance=True`` with
        Cancelled but is rendered with strike-through, not omitted).
        """
        with app.app_context():
            for member in StatusEnum:
                if member is StatusEnum.CANCELLED:
                    continue
                txn = _make_txn(db, seed_user, seed_periods, member)
                assert is_cancelled(txn) is False, (
                    f"is_cancelled returned True for {member.name}"
                )

    def test_is_cancelled_narrower_than_balance_contributing(
        self, app, db, seed_user, seed_periods,
    ):
        """A ``Credit`` txn is NOT cancelled, but IS excluded from
        balance.

        Pins the documented contract: ``is_cancelled`` and
        ``is_balance_contributing`` are independent predicates with
        different membership sets. The grid uses ``is_cancelled`` to
        omit only Cancelled rows; ``is_balance_contributing`` excludes
        both Cancelled AND Credit from numeric balance contribution.
        """
        with app.app_context():
            credit_txn = _make_txn(
                db, seed_user, seed_periods, StatusEnum.CREDIT,
            )
            assert is_cancelled(credit_txn) is False
            assert is_balance_contributing(credit_txn) is False


class TestIsDone:
    """Pins ``is_done`` (Commit 29 / MED-02 residual).

    Centralizes the inline ``status_id == done_id`` comparison in
    ``entry_service._update_actual_if_paid`` (recompute
    ``actual_amount`` from entries when the parent transaction is
    already Paid). ``StatusEnum.DONE`` is the Python enum identifier;
    the ref-table row carries display name "Paid".
    """

    def test_is_done_true_for_paid_txn(
        self, app, db, seed_user, seed_periods,
    ):
        """A txn with ``status_id == DONE.id`` (display "Paid")
        returns ``True``."""
        with app.app_context():
            txn = _make_txn(db, seed_user, seed_periods, StatusEnum.DONE)
            assert is_done(txn) is True

    def test_is_done_false_for_non_paid_txn(
        self, app, db, seed_user, seed_periods,
    ):
        """A txn with any other status returns ``False``.

        Includes ``Settled``, which is also ``is_settled=True`` but
        a distinct ref-table row; ``is_done`` is narrow status
        equality and must not collapse the two.
        """
        with app.app_context():
            for member in StatusEnum:
                if member is StatusEnum.DONE:
                    continue
                txn = _make_txn(db, seed_user, seed_periods, member)
                assert is_done(txn) is False, (
                    f"is_done returned True for {member.name}"
                )


class TestIsProjectedClause:
    """Pins ``is_projected_clause`` (Commit 29 / MED-02 residual).

    The eleven SQLAlchemy filter sites that previously each wrote
    ``Model.status_id == projected_id`` inline now consume this
    parameterised helper. The parity test below seeds Projected and
    non-Projected rows of both ``Transaction`` and ``Transfer`` and
    asserts the clause returns exactly the Projected ID set in each
    case -- the load-bearing guarantee that the SQL form classifies
    rows identically to the Python ``is_projected`` predicate.
    """

    def test_clause_returns_only_projected_transactions(
        self, app, db, seed_user, seed_periods,
    ):
        """Seeded mix of Transaction rows: filter via
        ``is_projected_clause(Transaction)`` returns only the
        Projected IDs.

        Seeded matrix: one transaction per StatusEnum member (6
        rows). Expected: the clause selects exactly the Projected
        row.
        """
        with app.app_context():
            txns_by_status = {
                member: _make_txn(db, seed_user, seed_periods, member)
                for member in StatusEnum
            }
            seeded_ids = {t.id for t in txns_by_status.values()}

            projected_ids = {
                row.id for row in (
                    db.session.query(Transaction.id)
                    .filter(Transaction.id.in_(seeded_ids))
                    .filter(is_projected_clause(Transaction))
                    .all()
                )
            }

            assert projected_ids == {
                txns_by_status[StatusEnum.PROJECTED].id,
            }, (
                "is_projected_clause(Transaction) selected a "
                "different set than {Projected}"
            )

    def test_clause_matches_python_is_projected(
        self, app, db, seed_user, seed_periods,
    ):
        """The SQL clause classifies the same rows as the Python
        predicate.

        Seeded mix as above; asserts the ID set returned by the
        ORM filter equals the ID set produced by iterating in
        Python with ``is_projected``. Mirrors the C2-6 parity test
        for ``balance_contributing_clause`` against
        ``is_balance_contributing``; required because nothing else
        enforces that the SQL form and the Python form classify
        identically.
        """
        with app.app_context():
            txns = [
                _make_txn(db, seed_user, seed_periods, member)
                for member in StatusEnum
            ]
            seeded_ids = {t.id for t in txns}

            python_projected = {t.id for t in txns if is_projected(t)}
            sql_projected = {
                row.id for row in (
                    db.session.query(Transaction.id)
                    .filter(Transaction.id.in_(seeded_ids))
                    .filter(is_projected_clause(Transaction))
                    .all()
                )
            }

            assert python_projected == sql_projected, (
                "is_projected_clause and is_projected disagree -- "
                "balance_predicates.py has drifted; the SQL filter "
                "sites and the Python predicate sites will silently "
                "classify Projected rows differently."
            )


class TestCreditCancelledSingleDefinition:
    """Pin C29-4: the ``[Credit, Cancelled]`` exclusion set is defined
    in exactly one place.

    Pre-Commit-29 the set was independently re-derived in
    ``budget_variance_service`` (inline ``excluded_status_ids =
    [CREDIT, CANCELLED]``) and in ``year_end_summary_service``
    (``_get_excluded_status_ids()`` helper of identical body), plus
    reproduced as ``Status.excludes_from_balance.is_(False)`` filters
    in five SQL sites and ``not t.status.excludes_from_balance``
    Python checks in three sites. Commit 29 collapses all of them
    onto ``balance_excluded_status_ids()`` from this module. This
    test mechanically asserts that property by greppingthe app/
    tree.
    """

    def test_no_competing_credit_cancelled_helper(self):
        """No other module defines a helper that returns the
        ``[Credit, Cancelled]`` ID list.

        Greps for the ``_get_excluded_status_ids`` name (the
        pre-Commit-29 duplicated helper in
        ``year_end_summary_service``) plus the inline list-literal
        pattern ``[ref_cache.status_id(StatusEnum.CREDIT)``. Either
        match outside ``balance_predicates.py`` would be a
        regression: a future contributor would have re-introduced
        the duplication MED-02 fixes.
        """
        app_root = Path(__file__).resolve().parents[2] / "app"
        offenders: list[str] = []
        for py_path in app_root.rglob("*.py"):
            if py_path.name == "balance_predicates.py":
                continue
            source = py_path.read_text(encoding="utf-8")
            if "_get_excluded_status_ids" in source:
                offenders.append(
                    f"{py_path.relative_to(app_root)}: defines "
                    "_get_excluded_status_ids (pre-Commit-29 dup)"
                )
            # The inline list literal whose first element binds the
            # CREDIT status id is the structural form of the
            # pre-Commit-29 ``excluded_status_ids = [CREDIT,
            # CANCELLED]`` reproduction.  Detected by the substring
            # "StatusEnum.CREDIT)," appearing in a list-literal
            # context (the trailing comma differentiates it from a
            # bare ``ref_cache.status_id(StatusEnum.CREDIT)`` access).
            if (
                "ref_cache.status_id(StatusEnum.CREDIT),"
                in source.replace(" ", "").replace("\n", "")
            ):
                offenders.append(
                    f"{py_path.relative_to(app_root)}: inline "
                    "[CREDIT, CANCELLED] list literal "
                    "(pre-Commit-29 dup)"
                )
        assert not offenders, (
            "The [Credit, Cancelled] exclusion set is re-derived "
            "outside balance_predicates.py. Route through "
            "balance_excluded_status_ids() instead. Offenders: "
            + "; ".join(offenders)
        )


class TestNoInlineStatusBusinessLogic:
    """Pin C29-1: business-logic per-status equality lives only in
    ``balance_predicates.py``.

    The audit's D6-09 (i) and D6-09 (ii) registers covered three
    inline forms of the per-status equality comparison: Python
    ``status_id != projected_id`` skips, Python ``status_id ==
    credit_id`` / ``cancelled_id`` / ``done_id`` state-machine
    gates, and SQLAlchemy ``Model.status_id == projected_id`` filter
    sites. Commit 29 routes every one of them through the helpers
    in this module. This test mechanically asserts that no
    business-logic file rebuilds the same comparison inline.

    The grep accepts a small whitelist of file:pattern pairs that
    are NOT D6-09 instances (relational invariant checks, JOIN
    conditions, two-field display selections). Any new match
    outside that whitelist fails the test.
    """

    # File-level allowlist of `status_id == ` / `status_id != ` sites
    # that are NOT the D6-09 pattern: two-field invariant checks,
    # SQL JOIN ON clauses, and form-render ``selected if`` two-field
    # comparisons.  Each entry is (relative-path, substring-of-line)
    # so a future change that adds a NEW inline comparison in any
    # listed file is still caught.
    _ALLOWED_NON_D609_SITES = (
        # transfer_service: relational integrity (shadow status must
        # match parent), comparing two model fields not a constant.
        ("services/transfer_service.py",
         "shadow.status_id != xfer.status_id"),
        # archive_helpers: SQL JOIN ON clauses on the status FK.
        ("utils/archive_helpers.py",
         "Transaction.status_id == Status.id"),
        ("utils/archive_helpers.py",
         "Transfer.status_id == Status.id"),
    )

    def test_no_inline_status_equality_outside_predicate_module(self):
        """Every business-logic ``status_id == X`` /
        ``status_id != X`` in ``app/`` is either (a) inside
        ``balance_predicates.py``, (b) on the documented allowlist
        of non-D6-09 patterns, or (c) a Jinja template using the
        ID-based ``STATUS_*`` globals.

        Walks every ``.py`` and ``.html`` file under ``app/`` and
        classifies each ``status_id ==`` / ``status_id !=`` line.
        Any unclassified hit is a regression (a new D6-09 inline
        comparison snuck in past the helper).
        """
        app_root = Path(__file__).resolve().parents[2] / "app"
        violations: list[str] = []

        for path in app_root.rglob("*.py"):
            rel = str(path.relative_to(app_root))
            if rel == "utils/balance_predicates.py":
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1,
            ):
                if (
                    "status_id ==" not in line
                    and "status_id !=" not in line
                ):
                    continue
                # Ignore comment lines -- pure documentation.
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                # Ignore the allowlist of non-D6-09 patterns.
                if any(
                    rel == allowed_rel and allowed_substr in line
                    for allowed_rel, allowed_substr in (
                        self._ALLOWED_NON_D609_SITES
                    )
                ):
                    continue
                violations.append(f"{rel}:{lineno}: {stripped}")

        # Templates: a comparison against ``status.name`` (the
        # audit's name-string violation) is the structural form
        # ``status.name ==`` or ``status.name !=``.  Output
        # expressions like ``{{ t.status.name }}`` in an aria-label
        # are display uses (the comparison would be inside an
        # adjacent ``{% if %}`` block on the same line, which the
        # substring check would still catch).
        for path in app_root.rglob("*.html"):
            rel = str(path.relative_to(app_root))
            source = path.read_text(encoding="utf-8")
            collapsed = source.replace(" ", "")
            if (
                "status.name==" in collapsed
                or "status.name!=" in collapsed
            ):
                violations.append(
                    f"{rel}: Jinja compares status.name "
                    "(E-15 violation)"
                )

        assert not violations, (
            "Inline business-logic status comparisons found outside "
            "the centralized predicate module. Route through "
            "is_projected / is_credit / is_cancelled / is_done / "
            "is_projected_clause from app/utils/balance_predicates.py. "
            "Offenders: " + "; ".join(violations)
        )


class TestJinjaUsesIdsNotNames:
    """Pin C29-2: Jinja templates use IDs in conditionals, never
    ``status.name``.

    The audit's D6-09 (iv) and CLAUDE.md rule 4 ("IDs for logic,
    strings for display only") both require that Jinja conditional
    branches consume the ID-based ``STATUS_*`` globals set in
    ``app/__init__.py``, with the display string reserved for
    aria-labels and badges.

    This test parses every ``.html`` template and asserts that no
    ``{% if %}`` / ``{% elif %}`` / ``{% set %}`` line contains
    ``status.name`` on the comparison side. Display uses
    (``{{ status.name }}`` in aria-label / title / badge body) are
    allowed -- those are output, not control flow.
    """

    def test_no_status_name_in_template_conditionals(self):
        """Every Jinja conditional that touches a status compares
        IDs, not display strings."""
        app_root = Path(__file__).resolve().parents[2] / "app"
        violations: list[str] = []
        for path in app_root.rglob("*.html"):
            rel = str(path.relative_to(app_root))
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1,
            ):
                # ``status.name`` in a control-flow tag (``{% if``
                # / ``{% elif`` / ``{% set ... = ... %}``) means the
                # template is comparing against the display string,
                # which E-15 forbids.  Bare ``{{ status.name }}``
                # output expressions are display labels and allowed.
                if "status.name" not in line:
                    continue
                if (
                    ("{% if" in line and "==" in line)
                    or ("{% elif" in line and "==" in line)
                    or ("{% set" in line and "==" in line)
                ):
                    violations.append(f"{rel}:{lineno}: {line.strip()}")
        assert not violations, (
            "Jinja template conditional compares against "
            "``status.name`` (E-15 / CLAUDE.md rule 4 violation). "
            "Use the ID-based ``STATUS_*`` Jinja globals instead. "
            "Offenders: " + "; ".join(violations)
        )
