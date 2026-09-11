"""R7d-c-2: a GENERATE pass bounds a loan payment by asking the loan.

``recurrence_engine.resolve_generation_plan`` is the preamble both engines run
before they write a row: the ownership check, the rule-present gate, and the
occurrence walk narrowed to the pass's window.  Plan step **R7d-c-2** moves
that walk onto the composed door
(:func:`app.services.recurring_definition.read_definition`), so every
occurrence is narrowed by the rule's authored bound AND by the stop its
destination derives -- a transfer paying a loan stops where the loan's balance
folds to zero -- instead of by the rule's own columns alone.

**What the column is, and why asking beats reading it.**
``budget.recurrence_rules.end_date`` holds the loan's projected payoff as a
snapshot, written by ten chokepoints between them.  A payoff is a fold over the
loan's whole forward plan, so a value persisted at mutation time is a CACHE,
and any reader can arrive before the next writer runs -- plan ledger row
**D35**, measured on production 2026-08-25 as ``2029-01-22`` stored against
``2029-02-22`` derived: one ``$531.94`` installment never generated.

**For the loan's STANDING payment the column binds nothing here, in EITHER
direction** (ruling **R-R56**, applied by the door): the app writes that
column, so it is read as the cache it is and the derived stop is the whole
answer.  A cache EARLIER than the payoff no longer drops the last installment
(the create direction); a cache LATER than it no longer projects payments
against a debt that is gone (the retire direction).  A SECOND recurring
transfer into the same loan keeps its owner's authored bound, ANDed with the
loan's stop -- the pair :class:`TestTheRulesOwnBoundStillBinds` grades.

**Measured on a production clone, 2026-09-11** (harness
``tests/manual/verify_loan_bound_at_generation.py``, base ``584b30fa``): the
two LIVE doors -- extend past the payoff, regenerate the tail -- came back
byte-identical, because both loan-payment rules store exactly what the loan
derives today.  The three PLANTED doors moved: the column re-authored one
month early wrote 34 rows on the base tree against 35 on the branch (the
``2029-02-22`` installment); a second ``$50.00`` definition into the same loan
bound ``2035-12-22`` wrote 65 rows to ``2031-08-22`` on the base tree against
35 to ``2029-02-22`` on the branch; and a true-up moving the payoff to
``2027-08-22`` with the column left behind had the base tree's maintain pass
retire nothing while the branch's retired 18 rows.

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.models.transfer import Transfer
from app.services import (
    pay_period_admin,
    pay_period_gates,
    recurrence_engine,
    transfer_recurrence,
)
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.loan_recurrence_sync import bind_rule_to_loan
from app.services.period_population import (
    populate_periods_from_active_templates,
)
from app.services.recurrence import (
    EMPTY,
    INDEFINITE,
    NEVER_ENDS,
    ClosesOn,
    EndsOnDate,
    reauthor_rule,
    recurrence_spec,
    rule_occurrences,
)
from app.services.recurring_definition import resolved_definition
from tests._test_helpers import (
    capture_sql_statements,
    create_account_of_type,
    create_loan_account,
    freeze_today,
    insert_trueup_event,
    loan_params_for,
    make_expense_template,
    make_loan_payment_template,
    make_transfer_template,
    rhythm_of,
)
from tests.oracles.recurrence_baseline import MONTHLY

#: The read pass's now for every test here.  ``seed_periods`` runs ten biweekly
#: paychecks from 2026-01-02, so the saved schedule covers through 2026-05-21;
#: a today of 2026-02-01 leaves four monthly installments inside it and the
#: loan below closes on the THIRD, which is what makes the bound observable at
#: all.  A payoff past the horizon is invisible to generation whatever the
#: bound says -- the sibling harness's own reason for extending 80 periods.
TODAY = date(2026, 2, 1)

#: A loan that closes INSIDE the seeded schedule: originated 2026-01-15 on a
#: three-month term with a payment day of the 15th, so its installments are
#: 2026-02-15, 2026-03-15 and 2026-04-15 and its balance folds to zero on the
#: last of them.  The fourth occurrence the monthly rule names, 2026-05-15,
#: falls inside the schedule and past the loan's life -- it is the row this
#: step stops being written.
#:
#: **``$1,200.00`` at 3%, and the figures are chosen so the level payment
#: leaves NO RESIDUE**: ``$402.00`` three times clears it to the cent.  A
#: first draft used ``$3,000.00`` at 5%, whose contract prices its last
#: installment ``$1,008.35`` against a ``$1,008.34`` level payment, and the
#: seam's two tiers disagree on that cent: the ESTIMATED tier prices the
#: unwritten last slot at the contract's adjusted figure while the PLANNED
#: tier prices the written row at the definition's level one, so writing the
#: three rows left ``$0.01`` owing and moved the derived payoff a whole
#: installment, ``2026-04-15`` -> ``2026-05-15``.  That is a finding about
#: the fold's pricing of a loan's final installment, reported with this step
#: rather than pinned by it; on a residue-free loan the bound is a fixed
#: point, which is what :class:`TestOnePassIsAFixedPoint` grades.
ORIGINATION = date(2026, 1, 15)
PAYMENT_DAY = 15
PRINCIPAL = Decimal("1200.00")
RATE = Decimal("0.03000")
TERM_MONTHS = 3
PAYOFF = date(2026, 4, 15)
PAST_PAYOFF = date(2026, 5, 15)
INSTALLMENTS = (date(2026, 2, 15), date(2026, 3, 15), PAYOFF)

#: A stale cache one installment EARLIER than the payoff -- plan ledger row
#: D35's measured shape, scaled onto this loan.
STALE_EARLY = date(2026, 3, 15)

#: A stale cache LATER than the payoff, which a chokepoint leaves behind when a
#: true-up moves the payoff earlier and no later chokepoint has run.
STALE_LATE = date(2026, 12, 31)


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    """Freeze today so the loan fold and the derived payoff are deterministic."""
    freeze_today(monkeypatch, TODAY)


def _ctx(seed_user, as_of=TODAY):
    """The read pass every generate in this module runs inside."""
    return BalanceContext.build(seed_user["user"].id, as_of)


def _short_loan(seed_user, db_session):
    """The three-installment loan described at :data:`PAYOFF`."""
    return create_loan_account(
        seed_user, db_session, name="Closes In April",
        principal=PRINCIPAL, rate=RATE, term=TERM_MONTHS,
        origination_date=ORIGINATION, payment_day=PAYMENT_DAY,
    )


def _payment(db_session, seed_user, loan):
    """A MONTHLY payment into *loan*, bound to it the way the loan door binds one.

    ``make_loan_payment_template`` authors the cadence with its first firing on
    the first 15th the schedule reaches -- 2026-01-15, the origination day
    itself, which the engine refuses to pay.  ``bind_rule_to_loan`` is what
    the loan door runs for the OPENING bound, and it moves that firing onto
    the contract's first installment (2026-02-15), so ``starts_on`` here is
    the production producer's answer and not a fixture day.  Its ``end_date``
    column is left NULL, as the door leaves it before the first chokepoint.
    """
    template = make_loan_payment_template(
        db_session, seed_user, loan, cadence=MONTHLY, fires_on_day=PAYMENT_DAY,
    )
    bind_rule_to_loan(template.recurrence_rule, loan.id)
    return template


def _restate_bound(rule, bound, ctx):
    """Re-author *rule* with a different closing bound, through the write door.

    The package's partial-change idiom: read the spec, replace the one fact,
    write it back.  Reaching for the column directly would author a state the
    write door cannot produce, and the point is what a real stored bound does.
    """
    reauthor_rule(
        rule, replace(recurrence_spec(rule), end_bound=bound), ctx.calendar(),
    )


def _plan(template, ctx):
    """The occurrences a generate pass over the whole schedule would write."""
    plan = recurrence_engine.resolve_generation_plan(
        template, GenerationSchedule.for_pass(ctx), ctx.scenario_id, None,
        block_message="test",
    )
    return [placement.occurrence for placement in plan.placements]


def _generate(template, ctx):
    """Run the transfer engine's generate over the whole schedule."""
    return transfer_recurrence.generate_for_template(
        template, GenerationSchedule.for_pass(ctx), ctx.scenario_id,
    )


def _maintain(template, ctx):
    """Run the transfer engine's maintain pass over the whole schedule."""
    return transfer_recurrence.regenerate_for_template(
        template, GenerationSchedule.for_pass(ctx), ctx.scenario_id,
    )


#: The tables a loan fold reads, which a plan for a definition that pays into
#: no loan -- or into one the pass has already folded -- must not touch.
_SEAM_TABLES = (
    "budget.loan_params", "budget.accounts", "budget.transactions",
    "budget.transfer_templates", "budget.loan_anchor_events",
)


def _seam_reads(statements):
    """The seam tables *statements* read, in first-read order, each once."""
    seen = []
    for statement, _ in statements:
        for table in _SEAM_TABLES:
            if f"FROM {table}" in statement and table not in seen:
                seen.append(table)
    return seen


def _occurrences_stored(template):
    """Every occurrence *template* holds a live transfer for, ascending."""
    rows = (
        Transfer.query
        .filter_by(transfer_template_id=template.id, is_deleted=False)
        .all()
    )
    return sorted(row.occurs_on for row in rows)


class TestGenerationStopsWhenTheLoanDoes:
    """The loan's own stop reaches the plan, and the engine writes exactly that."""

    def test_the_loan_closes_when_this_module_says_it_does(
        self, app, db, seed_user, seed_periods,
    ):
        """Precondition for everything below: the door derives ``PAYOFF``.

        Stated as its own case so a fixture drift -- a term, a rate, a
        payment day -- fails HERE with the loan's own answer in the message,
        rather than as a mystery count two classes down.
        """
        with app.app_context():
            loan = _short_loan(seed_user, db.session)
            template = _payment(db.session, seed_user, loan)
            db.session.commit()

            resolved = resolved_definition(template, _ctx(seed_user))

            assert resolved.closing.derived == ClosesOn(on=PAYOFF)
            assert resolved.closing.authored == NEVER_ENDS

    def test_the_rule_alone_names_an_installment_past_the_payoff(
        self, app, db, seed_user, seed_periods,
    ):
        """The control: the pure walk still emits ``PAST_PAYOFF``.

        Without this the assertions below could pass because the schedule
        simply did not reach a fourth occurrence, which would grade nothing.
        """
        with app.app_context():
            loan = _short_loan(seed_user, db.session)
            template = _payment(db.session, seed_user, loan)
            db.session.commit()

            walked = [
                placement.occurrence for placement in rule_occurrences(
                    template.recurrence_rule, _ctx(seed_user).calendar(),
                )
            ]

            assert walked == [*INSTALLMENTS, PAST_PAYOFF]

    def test_the_plan_names_nothing_past_the_payoff(
        self, app, db, seed_user, seed_periods,
    ):
        """An unbounded rule's plan stops where the loan does."""
        with app.app_context():
            loan = _short_loan(seed_user, db.session)
            template = _payment(db.session, seed_user, loan)
            db.session.commit()

            assert _plan(template, _ctx(seed_user)) == list(INSTALLMENTS)

    def test_the_ENGINE_writes_exactly_those_rows(
        self, app, db, seed_user, seed_periods,
    ):
        """The transfer engine, not just the plan: three rows, none in May."""
        with app.app_context():
            loan = _short_loan(seed_user, db.session)
            template = _payment(db.session, seed_user, loan)
            db.session.commit()

            created = _generate(template, _ctx(seed_user))
            db.session.commit()

            assert sorted(row.occurs_on for row in created) == list(INSTALLMENTS)
            assert _occurrences_stored(template) == list(INSTALLMENTS)


class TestTheStoredColumnIsNotWhatGenerationReads:
    """Ruling R-R56 at the one reader that MOVES MONEY, in both directions."""

    def test_a_cache_EARLIER_than_the_payoff_no_longer_drops_the_last_installment(
        self, app, db, seed_user, seed_periods,
    ):
        """Plan ledger row D35's shape: the ``PAYOFF`` installment is written.

        Until this step generation read the column as the rule's own bound,
        so a cache one month early stopped the walk one installment short --
        the ``$531.94`` due ``2029-02-22`` that production's stale
        ``2029-01-22`` never created.  The row this asserts on is that
        installment, scaled onto this loan.
        """
        with app.app_context():
            loan = _short_loan(seed_user, db.session)
            template = _payment(db.session, seed_user, loan)
            db.session.commit()
            _restate_bound(
                template.recurrence_rule, EndsOnDate(on=STALE_EARLY),
                _ctx(seed_user),
            )
            db.session.commit()
            assert template.recurrence_rule.end_date == STALE_EARLY, (
                "precondition: the column is stale, one installment early"
            )

            created = _generate(template, _ctx(seed_user))
            db.session.commit()

            assert PAYOFF in [row.occurs_on for row in created], (
                "the stale column bound generation; the last installment "
                "the loan owes was never written"
            )
            assert _occurrences_stored(template) == list(INSTALLMENTS)

    def test_a_cache_LATER_than_the_payoff_no_longer_projects_past_the_loan(
        self, app, db, seed_user, seed_periods,
    ):
        """The other direction: a late cache writes no row the loan will refund.

        On the tree before this step a late column was the only bound the walk
        applied, so the ``PAST_PAYOFF`` occurrence was written and the cash
        side debited a payment the loan fold routes whole to Refund.
        """
        with app.app_context():
            loan = _short_loan(seed_user, db.session)
            template = _payment(db.session, seed_user, loan)
            db.session.commit()
            _restate_bound(
                template.recurrence_rule, EndsOnDate(on=STALE_LATE),
                _ctx(seed_user),
            )
            db.session.commit()

            created = _generate(template, _ctx(seed_user))
            db.session.commit()

            assert PAST_PAYOFF not in [row.occurs_on for row in created], (
                "the stale column bound generation; a payment was projected "
                "against a debt that is gone"
            )
            assert _occurrences_stored(template) == list(INSTALLMENTS)


class TestTheRulesOwnBoundStillBinds:
    """A SECOND definition into the loan: authored AND derived, never substituted.

    The first transfer into a loan is the one the app bounds -- its column is
    the chokepoints' cache and the door reads it as such -- so a case about an
    AUTHORED bound needs a second definition, whose column nothing writes.
    """

    def test_an_EARLIER_authored_bound_is_not_widened_by_the_loan(
        self, app, db, seed_user, seed_periods,
    ):
        """An owner who says "stop in March" is not overruled by a loan open to April."""
        with app.app_context():
            loan = _short_loan(seed_user, db.session)
            standing = _payment(db.session, seed_user, loan)
            standing.name = "The app-bounded payment"
            db.session.flush()
            second = _payment(db.session, seed_user, loan)
            db.session.commit()
            _restate_bound(
                second.recurrence_rule, EndsOnDate(on=STALE_EARLY),
                _ctx(seed_user),
            )
            db.session.commit()

            resolved = resolved_definition(second, _ctx(seed_user))
            assert resolved.closing.authored == EndsOnDate(on=STALE_EARLY), (
                "precondition: the second definition's bound is its owner's"
            )
            assert _plan(second, _ctx(seed_user)) == list(INSTALLMENTS[:2])

    def test_a_LATER_authored_bound_is_narrowed_to_the_loans_life(
        self, app, db, seed_user, seed_periods,
    ):
        """An owner who says "run to December" gets rows only while the loan owes.

        The harness's fourth door: on the base tree a second definition bound
        ``2035-12-22`` wrote 65 rows to ``2031-08-22`` into a loan that closes
        ``2029-02-22`` -- thirty payments the loan would refund.
        """
        with app.app_context():
            loan = _short_loan(seed_user, db.session)
            standing = _payment(db.session, seed_user, loan)
            standing.name = "The app-bounded payment"
            db.session.flush()
            second = _payment(db.session, seed_user, loan)
            db.session.commit()
            _restate_bound(
                second.recurrence_rule, EndsOnDate(on=STALE_LATE),
                _ctx(seed_user),
            )
            db.session.commit()

            created = _generate(second, _ctx(seed_user))
            db.session.commit()

            assert sorted(row.occurs_on for row in created) == list(INSTALLMENTS)


class TestTheOtherTwoDerivedShapes:
    """``Indefinite`` narrows nothing; ``Empty`` names nothing."""

    def test_a_loan_that_never_pays_off_narrows_NOTHING(
        self, app, db, seed_user, seed_periods,
    ):
        """Negative amortization: the rule's own bound is the whole answer.

        The shape ``test_loan_recurrence_sync`` pins: a $240,000 / 30-year
        contract at 6% trued up to $900,000, whose ~$1,439 level payment cannot
        cover $4,500 of monthly interest, so the balance GROWS.  A small stated
        payment on a healthy loan is NOT a substitute: the ESTIMATED tier's
        post-contractual extension lets a tiny payment clear a loan at its
        contractual last installment, and the window comes back a date.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Never Clears",
                principal=Decimal("240000.00"), rate=Decimal("0.06000"),
                term=360, origination_date=ORIGINATION,
                payment_day=PAYMENT_DAY,
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("900000.00"),
            )
            template = _payment(db.session, seed_user, loan)
            db.session.commit()

            resolved = resolved_definition(template, _ctx(seed_user))
            assert resolved.closing.derived == INDEFINITE, (
                "precondition: the loan never pays off"
            )
            assert _plan(template, _ctx(seed_user)) == [
                *INSTALLMENTS, PAST_PAYOFF,
            ]

    def test_an_EMPTY_window_names_no_occurrence_at_all(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan cleared before its first installment generates nothing.

        Trued to zero on 2026-01-16, the day after origination and a month
        before the first installment: the derived window closes before the
        definition ever fires, which is CORRECT at nought occurrences (the
        state that held ``ck_recurrence_rules_valid_window`` back, plan ledger
        row D35).
        """
        with app.app_context():
            loan = _short_loan(seed_user, db.session)
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
                anchor_date=date(2026, 1, 16),
            )
            template = _payment(db.session, seed_user, loan)
            db.session.commit()

            resolved = resolved_definition(template, _ctx(seed_user))
            assert resolved.closing.derived == EMPTY, (
                "precondition: the loan closed before its first firing"
            )
            assert _plan(template, _ctx(seed_user)) == []
            assert _generate(template, _ctx(seed_user)) == []


class TestDefinitionsNoLoanBounds:
    """A destination that is not a loan, and a definition with no destination."""

    def test_a_transfer_into_a_SAVINGS_account_is_untouched(
        self, app, db, seed_user, seed_periods,
    ):
        """No derived stop, so the plan is the rule's own answer."""
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Rainy Day",
            )
            template = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()
            ctx = _ctx(seed_user)

            resolved = resolved_definition(template, ctx)
            assert resolved.closing.derived is None

            walked = [
                placement.occurrence for placement in rule_occurrences(
                    template.recurrence_rule, ctx.calendar(),
                )
            ]
            assert _plan(template, ctx) == walked
            assert walked, "precondition: the every-paycheck rule fires at all"

    def test_a_TRANSACTION_template_costs_the_door_NO_QUERY(
        self, app, db, seed_user, seed_periods,
    ):
        """An expense pays into no account, so the door asks nothing of the seam.

        Measured, not argued: on a pass whose calendar is already memoised, the
        plan reads none of the five tables a loan fold reads
        (:data:`_SEAM_TABLES`, graded on ``FROM <table>``).  A loan lookup or
        a params load here would be a per-template cost on every generate of
        the schedule.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            db.session.commit()
            ctx = _ctx(seed_user)
            schedule = GenerationSchedule.for_pass(ctx)
            # Warm what is not under test: the pass's calendar memo, and the
            # committed template's expired attributes, which the ORM reloads
            # on first touch with its joined relationships.
            ctx.calendar()
            assert template.recurrence_rule is not None

            _, statements = capture_sql_statements(
                lambda: recurrence_engine.resolve_generation_plan(
                    template, schedule, ctx.scenario_id, None,
                    block_message="test",
                ),
            )

            assert _seam_reads(statements) == [], (
                "a transaction template's plan folded something for a "
                "definition that pays into nothing"
            )


class TestTheLoanIsFoldedOncePerPass:
    """Two definitions into one loan, one pass: the loan resolves once."""

    def test_the_second_definition_reads_the_first_ones_fold(
        self, app, db, seed_user, seed_periods,
    ):
        """The pass memoises the loan; the plan does not re-fold it per template.

        The first definition's plan folds the loan -- its params, its shadows,
        its standing payment; the second definition's plan, on the same pass,
        reads none of those tables again.  Counted on the statements rather
        than on a spy, because a spy on the memo's filler could be satisfied
        by a second memo nobody shares.
        """
        with app.app_context():
            loan = _short_loan(seed_user, db.session)
            first = _payment(db.session, seed_user, loan)
            first.name = "The app-bounded payment"
            db.session.flush()
            second = _payment(db.session, seed_user, loan)
            db.session.commit()
            ctx = _ctx(seed_user)
            schedule = GenerationSchedule.for_pass(ctx)
            ctx.calendar()
            assert second.recurrence_rule is not None

            def _plan_for(template):
                return lambda: recurrence_engine.resolve_generation_plan(
                    template, schedule, ctx.scenario_id, None,
                    block_message="test",
                )

            _, on_first = capture_sql_statements(_plan_for(first))
            _, on_second = capture_sql_statements(_plan_for(second))

            assert "budget.loan_params" in _seam_reads(on_first), (
                "precondition: the first definition's plan folded the loan"
            )
            assert _seam_reads(on_second) == [], (
                "the second definition into the same loan folded it again "
                "on the same pass"
            )


class TestTheMaintainPassRetiresWhatTheLoanNoLongerJustifies:
    """The retire direction, through the pass that can retire."""

    def test_a_true_up_that_moves_the_payoff_EARLIER_retires_the_rows_past_it(
        self, app, db, seed_user, seed_periods,
    ):
        """Rows generated to April; the loan cleared in February; March and April go.

        The true-up is recorded the way the balance true-up door records one
        -- an anchor event reconciled into the ledger -- WITHOUT the
        chokepoint's column sync, so the column still says the OLD payoff.
        On the tree before this step the maintain pass honoured that column
        and retired nothing.  ``regenerate_for_template`` removes an empty
        projected row the rule no longer names (ruling R-R19), which is what
        makes the retire branch reachable here.

        The maintain pass runs on a LATER day than the generate: a loan
        cleared on 2026-02-20 is retired only on a pass whose ``as_of`` has
        reached that day, and its closing date is then the day it last became
        closed (plan step ``recurrence:R7d-h``).
        """
        with app.app_context():
            loan = _short_loan(seed_user, db.session)
            template = _payment(db.session, seed_user, loan)
            db.session.commit()
            _generate(template, _ctx(seed_user))
            db.session.commit()
            assert _occurrences_stored(template) == list(INSTALLMENTS), (
                "precondition: three installments stand"
            )

            cleared_on = date(2026, 2, 20)
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
                anchor_date=cleared_on,
            )
            db.session.commit()
            later = date(2026, 3, 1)
            resolved = resolved_definition(template, _ctx(seed_user, later))
            assert resolved.closing.derived == ClosesOn(on=cleared_on), (
                "precondition: the loan now closes in February"
            )

            created = _maintain(template, _ctx(seed_user, later))
            db.session.commit()

            assert created == []
            assert _occurrences_stored(template) == [INSTALLMENTS[0]], (
                "the March and April installments were left projected "
                "against a loan cleared in February"
            )


class TestOnePassIsAFixedPoint:
    """The bound is folded over the rows the pass writes, and it does not move."""

    def test_generating_the_rows_does_not_move_the_bound_they_were_written_under(
        self, app, db, seed_user, seed_periods,
    ):
        """Generate, re-read, maintain, generate again: same stop, no new row.

        The PLANNED tier prices a written row from its definition and the
        ESTIMATED tier prices an unwritten slot from the same definition
        (plan steps R7d-a and balance:X-au-f), so covering a slot with a row
        changes no figure the fold reads -- for the loan's standing payment on
        the contract's own cadence.  Measured on the harness's first door:
        ``2029-02-22`` before and after 101 rows, 0 created on the second
        pass.  (A SECOND definition into the loan does move it, because the
        estimate prices from the standing payment alone -- plan ledger row
        **D47**, plan step R16-b-2's.)
        """
        with app.app_context():
            loan = _short_loan(seed_user, db.session)
            template = _payment(db.session, seed_user, loan)
            db.session.commit()
            before = resolved_definition(template, _ctx(seed_user)).closing

            first = _generate(template, _ctx(seed_user))
            db.session.commit()
            after = resolved_definition(template, _ctx(seed_user)).closing
            maintained = _maintain(template, _ctx(seed_user))
            db.session.commit()
            again = _generate(template, _ctx(seed_user))
            db.session.commit()

            assert len(first) == len(INSTALLMENTS)
            assert after == before
            assert maintained == []
            assert again == []
            assert _occurrences_stored(template) == list(INSTALLMENTS)


class TestThePopulationDoor:
    """The route-shaped paths: new periods filled on a pass opened after the write."""

    def test_the_regenerate_door_reads_the_same_stop_in_the_hole(
        self, app, db, seed_user, seed_periods,
    ):
        """Plan ledger row D46's question, asked of the loan door's own payment.

        The rows stand; the not-yet-started tail is retired and its rows
        cascade; a pass opened in that hole -- the one the route opens for
        the repopulation (ruling R-R38) -- reads the loan with every retired
        slot AHEAD of it, and the estimate re-synthesises each at the price
        the row carried.  So the stop read in the hole is the stop read
        before, and the rows come back exactly as they were.  A payment whose
        slot is already past sits in a period that has started, which the
        door keeps; the ``_period_population`` docstring carries the one
        authorable shape this does not hold for.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            loan = _short_loan(seed_user, db.session)
            template = _payment(db.session, seed_user, loan)
            db.session.commit()
            _generate(template, _ctx(seed_user))
            db.session.commit()
            before = resolved_definition(template, _ctx(seed_user)).closing

            # Today is 2026-02-01, inside the period opening 2026-01-30; the
            # tail from 2026-02-13 is rebuilt and every installment's row
            # goes with it.
            rebuilt = pay_period_admin.regenerate_pay_periods(
                user_id, date(2026, 2, 13), 8, rhythm_of(14),
                confirms=pay_period_gates.Confirmations(discard=True),
            )
            db.session.flush()
            assert _occurrences_stored(template) == [], (
                "precondition: the rebuild took every installment's row"
            )
            in_the_hole = _ctx(seed_user)
            assert resolved_definition(template, in_the_hole).closing == before

            created = populate_periods_from_active_templates(
                in_the_hole, {period.id for period in rebuilt},
            )
            db.session.commit()

            assert created == len(INSTALLMENTS)
            assert _occurrences_stored(template) == list(INSTALLMENTS)
            assert resolved_definition(template, _ctx(seed_user)).closing == before

    def test_new_periods_past_the_payoff_are_filled_only_while_the_loan_owes(
        self, app, db, seed_user, seed_periods,
    ):
        """``populate_periods_from_active_templates`` over the whole schedule.

        The extend door's shape (ruling R-R38): the periods exist, the pass is
        opened, the batch is filled.  Every period is offered here, so the
        May paycheck is in the window and the only thing keeping the
        ``PAST_PAYOFF`` row out of it is the loan's own stop.
        """
        with app.app_context():
            loan = _short_loan(seed_user, db.session)
            template = _payment(db.session, seed_user, loan)
            db.session.commit()
            ctx = _ctx(seed_user)

            created = populate_periods_from_active_templates(
                ctx, {period.id for period in seed_periods},
            )
            db.session.commit()

            assert created == len(INSTALLMENTS)
            assert _occurrences_stored(template) == list(INSTALLMENTS)
