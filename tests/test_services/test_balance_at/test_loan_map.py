"""Balance-at seam tests: the loan balance map, interest from the latest assertion, the loan gate,
feed independence, and multi-loan isolation.

Split from ``tests/test_services/test_balance_at.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import CalcMethodEnum, PaycheckLineKindEnum
from app.models.interest_params import InterestParams
from app.models.paycheck_line import PaycheckLine
from app.services import anchor_service, balance_at, liability_sign
from app.services.account_projection import AccountProjectionKind, classify_account
from app.services.investment_projection import AccountPayrollFeed
from app.services.scenario_resolver import get_baseline_scenario
from app.services.balance_at import BalanceContext
from app.services.balance_at._inputs import (
    _contribution_inputs_for_account,
    _contribution_inputs_for_accounts,
)
from app.services.balance_at._resolution import configured_loan, resolved_loan
from tests._test_helpers import (
    all_periods,
    create_hysa_account,
    insert_trueup_event,
    last_covered_day,
    make_investment_account,
    make_salary_profile,
    reassert_balance_on,
    settle_instant_on,
)

from ._helpers import _owed_today, _make_mortgage


class TestInterestBeginsAtTheLatestAssertion:
    """Ruling R-L / plan step X-c2a, through the seam rather than the engine.

    The engine-level arithmetic is pinned in
    ``test_balance_calculator_hysa.py``; what these pin is the INPUT the
    kernel derives -- that the accrual window opens at the account's latest
    balance ASSERTION (its ``AccountAnchorHistory`` row's UTC civil day, read
    through the dated source of truth) and not at the anchor PERIOD's start,
    which is the date the code used before and which precedes it by up to a
    full period.  Both readers of that one walk are covered, because the
    balance map and the account-detail "Interest, next 12 mo" chip share it
    (plan finding N-47).

    Every APY here is 5% daily on a 14-day period, so a full period on
    $10,000 earns ``Q(10000 * ((1 + 0.05/365) ** 14 - 1))`` = ``$19.20`` and
    the halves below are visibly less than that.
    """

    @staticmethod
    def _hysa_asserted_on(db, seed_user, anchor_period, balance, day):
        """Build an HYSA whose OPENING assertion instant is pinned to *day*."""
        account = create_hysa_account(
            seed_user, db.session, anchor_period, balance,
        )
        reassert_balance_on(
            db.session, account, settle_instant_on(day),
        )
        db.session.commit()
        return account

    def test_the_anchor_period_accrues_only_from_the_assertion_day(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An account opened mid-period earns only that period's remainder.

        The anchor period runs 14 days; the balance is asserted on its 8th
        day, so 7 days of it are already inside the asserted figure and only
        the last 7 accrue:

            Q(10000 * ((1 + 0.05/365) ** 7 - 1)) = Q(9.5915..) = 9.59

        against ``$19.20`` for the whole period -- which is what this same
        fixture produced before ruling R-L, and what the firing control
        (reverting the ``max`` in ``_layer_interest``) restores.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            anchor = periods[0]
            hysa = self._hysa_asserted_on(
                db, seed_user, anchor, Decimal("10000.00"),
                anchor.start_date + timedelta(days=7),
            )

            balances = balance_at.balance_map(hysa, bctx)

            assert balances[anchor.id] == Decimal("10009.59")

    def test_a_later_true_up_moves_the_window_forward(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The LATEST assertion opens the window, not the opening one.

        The account opens in period 0 at $6,000.00 and the user trues it up in
        period 2 (through the production ``stage_anchor_true_up`` path, so the
        ``current_anchor_*`` cache and the history row agree exactly as they
        do in production).  Periods 0 and 1 then precede the newest assertion
        entirely and accrue nothing; period 2 accrues from the true-up day.

        Trued up on period 2's 8th day at $10,000:

            Q(10000 * ((1 + 0.05/365) ** 7 - 1)) = 9.59
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            hysa = create_hysa_account(
                seed_user, db.session, periods[0], Decimal("6000.00"),
            )
            # **The true-up names its own day at the DOOR** (plan step
            # X-f3c-2c).  It used to be staged for today and then re-stamped
            # onto the day this case turns on; the table is append-only, so
            # the day an assertion is true for is decided when it is written --
            # and ``stage_anchor_true_up`` already takes it.
            #
            # The stager takes a BOUNDED day (plan step X-f1e2): only
            # ``resolve_observation_day`` mints one, so the fixture asks for it
            # the same way both production doors do.
            anchor_service.stage_anchor_true_up(
                account=hysa,
                new_balance=Decimal("10000.00"),
                observed_on=anchor_service.resolve_observation_day(
                    user_id, periods[2].start_date + timedelta(days=7),
                ),
            )
            db.session.commit()

            balances = balance_at.balance_map(hysa, bctx)

            # Pre-assertion periods are PRESENT and accrue nothing: the fold
            # back-projects the opening over the records it contains (ruling
            # R-I), so they carry the $6,000.00 the account demonstrably held,
            # and the R-L window earns zero across days the true-up covers.
            assert balances[periods[0].id] == Decimal("6000.00")
            assert balances[periods[1].id] == Decimal("6000.00")
            assert balances[periods[2].id] == Decimal("10009.59")

    def test_the_interest_chip_reads_the_same_window(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The account-detail chip follows the assertion clock too (N-47).

        ``interest_by_period_for_account`` and the balance map are ONE walk,
        so the "Interest, next 12 mo" figure cannot keep accruing over days
        the balance map has stopped accruing over.  Same fixture as the first
        test: the anchor period earns the 7-day figure, not the 14-day one.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            anchor = periods[0]
            hysa = self._hysa_asserted_on(
                db, seed_user, anchor, Decimal("10000.00"),
                anchor.start_date + timedelta(days=7),
            )
            params = (
                db.session.query(InterestParams)
                .filter_by(account_id=hysa.id)
                .one()
            )

            chip = balance_at.interest_by_period_for_account(
                hysa, bctx,
            )
            balances = balance_at.balance_map(hysa, bctx)

            assert chip[anchor.id] == Decimal("9.59")
            # And the two agree: the chip's accrual IS the balance's premium
            # over the flat asserted figure in that first period.
            assert (
                balances[anchor.id] - Decimal("10000.00") == chip[anchor.id]
            )


class TestBalanceMapLoan:
    """``balance_map`` folds an amortizing loan's per-period balances (C3b3)."""

    def test_pre_first_payment_uses_current_balance(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The map folds: post-anchor holds C flat, pre-anchor reads the ledger.

        The seam folds the loan's SOURCE events
        (:func:`app.services.balance_at.positions`), which step B2 proves equals
        the sum-of-postings reader on every day.  A balance true-up dated today
        re-anchors the resolver, so its schedule is today-forward, and the map
        splits on the date the balance was ASSERTED:

        * A period that had not ended when the true-up landed, and that still
          precedes the first scheduled payment, owes the trued-up
          current_balance ($200,000) flat -- NEVER the $240,000 original
          principal.
        * A period that ENDED before the true-up owes what the fold knew then:
          the $240,000 opening, undisturbed, because not one payment was ever
          recorded.  The past belongs to the fold, and the trued-up balance is
          not back-projected across it.

        The map reports what the loan HOLDS, negative when owed (ruling R-CC47,
        plan step credit_card:CC-5-5c), so every figure here is read through
        ``liability_sign.owed``: the dollars above are what the loan owes.

        Both halves are verified against the real dev clone, where the Mortgage's
        past periods likewise step down at each recorded event rather than
        carrying today's balance backward.

        This does NOT fence PR #44 / aba0242 (a forward projection seeded with
        ``original_principal``): both periods above have BEGUN, so the fold answers
        them from source events and no forward projection is consulted at all.  That
        fence is STRUCTURAL -- C2b deleted the schedule-only map, C3b3 retired the
        per-period forward map, and C6b deleted the schedule-forward primitives
        entirely; and since plan step recurrence:R16-c-1 there is no forward seed
        at all -- the loan's timeline replays from its opening ASSERTION (never
        ``original_principal``) -- so there is no seed-argument call site left to
        police (the W9905 checker that once did retired with those primitives at
        C6b).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            mortgage, params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            # A true-up to a balance distinct from the $240,000 origination
            # principal, anchored today, makes the schedule today-forward so
            # the early periods are genuinely pre-first-payment AND the
            # current_balance ($200,000) is observably not the principal.
            insert_trueup_event(
                params, Decimal("200000.00"), anchor_date=date.today(),
            )
            db.session.commit()

            state = resolved_loan(mortgage, bctx).state

            seam = balance_at.balance_map(mortgage, bctx)

            assert seam is not None

            anchor_date = date.today()
            first_payment = min(
                row.payment_date for row in state.schedule
            )

            # A period still open when the balance was asserted, and before the
            # first scheduled payment: the trued-up balance, held flat.
            post_anchor = [
                p for p in periods
                if anchor_date <= last_covered_day(p) < first_payment
            ]
            assert post_anchor, "expected a post-anchor pre-first-payment period"
            assert liability_sign.owed(
                seam[post_anchor[0].id],
            ) == _owed_today(mortgage, bctx)
            # ...which is the trued-up current balance, never the original
            # principal (the PR #44 / aba0242 boundary bug).
            assert _owed_today(mortgage, bctx) == Decimal("200000.00")
            assert _owed_today(mortgage, bctx) != Decimal("240000.00")

            # A period that ENDED before the true-up: the ledger's answer.  The
            # loan opened at $240,000 and no payment was ever recorded, so that is
            # what it owed then.  The $200,000 assertion is dated today and is NOT
            # back-projected over the past.
            pre_anchor = [p for p in periods if last_covered_day(p) < anchor_date]
            assert pre_anchor, "expected a pre-anchor period"
            assert liability_sign.owed(
                seam[pre_anchor[-1].id],
            ) == Decimal("240000.00")

    def test_paid_off_empty_schedule_uses_current_balance(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A paid-off loan (empty schedule) holds its current_balance flat.

        A balance trueup to $0 leaves the resolver with an empty schedule and a
        $0 current balance.  The seam's map dispatch routes the loan to the fold
        on ``_resolution.configured_loan`` -- an empty schedule is still a
        CONFIGURED loan, not a fall-through to cash -- and it reports $0 at
        every period.

        This is one half of the pair that makes plan step X-g3b-0's gate change
        safe (the other is
        ``TestBrokenLoanFailsLoud.test_an_amortizing_account_with_no_loan_params_does_NOT_raise``):
        the retired gate tested MEMBERSHIP in a ``debt_schedules`` map, and a
        loan whose schedule is EMPTY is exactly the shape a careless
        reimplementation drops to cash.  ``TestTheLoanGateIsOneQuestion`` below
        asserts the two spellings agree on this shape directly.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            loan, params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            insert_trueup_event(params, Decimal("0.00"))
            db.session.commit()

            state = resolved_loan(loan, bctx).state

            seam = balance_at.balance_map(loan, bctx)

            assert seam is not None
            # Paid off -> empty schedule -> $0 current balance everywhere.
            assert state.schedule == []
            assert _owed_today(loan, bctx) == Decimal("0.00")
            assert seam[periods[0].id] == Decimal("0.00")
            assert seam[periods[-1].id] == Decimal("0.00")

    def test_future_period_reads_the_amortized_schedule_row(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A future period past the first payment reads the amortized schedule row.

        The map's FORWARD region, pinned by VALUE.  For a period after today whose
        end is past the loan's first scheduled payment, the seam reads the forward
        projection: the loan owes the schedule's ``remaining_balance`` for the LAST
        installment due by that period's end -- a specific reduced balance,
        strictly below the seed.  The map reports what the loan HOLDS, negative
        when owed (ruling R-CC47, plan step credit_card:CC-5-5c), so it is read
        through ``liability_sign.owed`` before it meets that owed row.  Independently recomputes the expected row here (last unconfirmed row
        on-or-before the period end) rather than trusting the producer, so a
        regression in the map's forward SAMPLING (wrong period date) or the walk's
        row SELECTION would fire.  Restores at the seam the after-payment forward
        pin the retired savings dispatcher unit tests carried on synthetic
        schedules.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            loan, params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            # Trued up today -> schedule is today-forward, seed $200,000, so every
            # future period genuinely amortizes DOWN from a known balance.
            insert_trueup_event(
                params, Decimal("200000.00"), anchor_date=date.today(),
            )
            db.session.commit()

            state = resolved_loan(loan, bctx).state
            seam = balance_at.balance_map(loan, bctx)

            first_payment = min(
                row.payment_date for row in state.schedule
                if not row.is_confirmed
            )
            future = [
                p for p in periods
                if p.start_date > bctx.as_of and last_covered_day(p) > first_payment
            ]
            assert future, "expected a future period past the first payment"
            fp = future[0]
            # The last unconfirmed installment due by the period's end -- what the
            # forward walk reduces the balance to -- recomputed independently.
            due_by_end = [
                row for row in state.schedule
                if not row.is_confirmed and row.payment_date <= last_covered_day(fp)
            ]
            assert due_by_end, "expected an installment due by the future period"
            expected = max(
                due_by_end, key=lambda row: row.payment_date,
            ).remaining_balance
            assert liability_sign.owed(seam[fp.id]) == expected
            # A real reduction: strictly below the $200,000 seed, still owing.
            assert Decimal("0.00") < expected < _owed_today(loan, bctx)
            assert _owed_today(loan, bctx) == Decimal("200000.00")


class TestTheLoanGateIsOneQuestion:
    """The seam asks "is this a configured loan?" ONE way (plan step X-g3b-0).

    The scalar (``_kind_correct.balance_at``) has always asked
    ``classify_account(...) is AMORTIZING and resolved_loan(account, ctx) is not
    None``.  The per-period map asked a DIFFERENT expression -- membership in an
    ``_AssembledInputs.debt_schedules`` map the seam built and then discarded the
    values of -- and the forward liability band decomposed the same rule into two
    guard clauses.  The equivalence between the three was recorded in a docstring
    rather than enforced.  X-g3b-0 deleted the bundle and pointed all three at
    one named predicate, ``_resolution.configured_loan``.

    **What these cases lock, stated so the class is not over-trusted.**  The
    SHIPPED side calls the production predicate, so a mutation of it fires here.
    The RETIRED side is rebuilt as the expression it stood for: membership in
    ``generate_debt_schedules``' map, which held exactly the loans
    ``resolved_loan`` resolves.  It was rebuilt FROM that producer, which also
    made the pair fire if the producer gained a filter the resolver lacks, until
    plan step recurrence:R16-c-2 deleted the producer with its last caller
    (ruling R-R112); the retired side reads the resolution directly since, and
    that second purpose went with the producer.  What they do NOT lock is that the map and the band
    still CALL the predicate: that is
    ``TestBalanceMapLoan`` / ``TestBrokenLoanFailsLoud``'s job, by value.
    """

    @staticmethod
    def _both_spellings(account, ctx):
        """Return ``(retired_gate, shipped_gate)`` for *account*.

        The retired spelling is rebuilt exactly as it stood -- the kind test AND
        membership in the schedule map, over the AMORTIZING-filtered subset the
        assembly passed; the map held a loan iff ``resolved_loan`` resolved it,
        which is how it reads since that map's producer was deleted (plan step
        recurrence:R16-c-2) -- because dropping the kind conjunct would compare a
        LOOSER rule and report a false divergence for a ``LoanParams`` row on a
        non-amortizing account, which is a data defect both surfaces are
        supposed to degrade identically.

        The shipped spelling is the PRODUCTION function, not a copy of it, so a
        mutation of the predicate fires these cases rather than passing a
        reimplementation.
        """
        retired = (
            classify_account(account) is AccountProjectionKind.AMORTIZING
            and resolved_loan(account, ctx) is not None
        )
        return retired, configured_loan(account, ctx) is not None

    def test_a_configured_loan_is_a_loan_under_both(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The ordinary case: a Mortgage with LoanParams takes the loan arm."""
        with app.app_context():
            bctx = BalanceContext.build(seed_user["user"].id)
            periods = all_periods(seed_user["user"].id)
            loan, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            db.session.commit()

            retired, shipped = self._both_spellings(loan, bctx)

            assert retired is True
            assert shipped is True
            assert retired == shipped

    def test_a_paid_off_loan_with_an_empty_schedule_is_still_a_loan(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The discriminating shape: configured, but its schedule is EMPTY.

        A trueup to $0 leaves the resolver with no amortization rows at all.
        Both spellings must still say "loan" -- a gate that tested the SCHEDULE
        rather than the RESOLUTION would drop this account onto the cash
        producer, where its balance would become the sum of its payment
        transfers read as income (finding B-3's shape).
        """
        with app.app_context():
            bctx = BalanceContext.build(seed_user["user"].id)
            periods = all_periods(seed_user["user"].id)
            loan, params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            insert_trueup_event(params, Decimal("0.00"))
            db.session.commit()

            resolved = resolved_loan(loan, bctx)
            retired, shipped = self._both_spellings(loan, bctx)

            # The shape really is the discriminating one.
            assert resolved is not None
            assert resolved.state.schedule == []
            assert retired is True
            assert shipped is True
            assert retired == shipped

    def test_a_mortgage_with_no_loan_params_is_not_a_loan_under_either(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The other side: Mortgage-typed, terms never entered -> NOT a loan."""
        # Pylint: ``import-outside-toplevel`` -- the shared factory is imported
        # lazily here exactly as the sibling fall-through test does it.
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_account_of_type

        with app.app_context():
            acct = create_account_of_type(
                seed_user, db.session, "Mortgage", "Terms Never Entered",
                anchor_balance=Decimal("150000.00"),
            )
            db.session.commit()
            bctx = BalanceContext.build(seed_user["user"].id)

            retired, shipped = self._both_spellings(acct, bctx)

            assert classify_account(acct) is AccountProjectionKind.AMORTIZING
            assert retired is False
            assert shipped is False
            assert retired == shipped

    def test_a_non_loan_account_is_not_a_loan_under_either(
        self, app, seed_user, seed_periods_today,
    ):
        """A plain checking account: neither spelling admits it."""
        with app.app_context():
            bctx = BalanceContext.build(seed_user["user"].id)

            retired, shipped = self._both_spellings(seed_user["account"], bctx)

            assert retired is False
            assert shipped is False
            assert retired == shipped

    def test_loan_terms_on_a_non_amortizing_account_are_not_a_loan(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The CLASSIFIER half: ``LoanParams`` alone must not make a loan.

        Without this case the kind conjunct inside ``configured_loan`` is
        unpinned -- deleting it leaves every other case in this class green,
        because none of them carries loan terms on an account the classifier
        refuses.  The resolver half is pinned by the params-less Mortgage above;
        this is its opposite number.

        **The shape is production-reachable, not hypothetical.**  A user can
        edit an existing account type and flip ``has_amortization`` off
        (``routes/accounts/types.py:151-157``); the boundary guard refuses only
        while a linked ledger carries postings, and nothing deletes the
        ``LoanParams`` rows of accounts already on that type.  What is left is
        exactly this: loan terms on an account the classifier no longer calls
        amortizing.  Both spellings must refuse it, or ``positions`` would
        amortize an account whose balance is its transaction rows.
        """
        with app.app_context():
            bctx = BalanceContext.build(seed_user["user"].id)
            periods = all_periods(seed_user["user"].id)
            loan, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            db.session.commit()
            # It IS a configured loan until the type flag moves.
            assert configured_loan(loan, bctx) is not None

            loan.account_type.has_amortization = False
            db.session.commit()
            db.session.refresh(loan)
            bctx = BalanceContext.build(seed_user["user"].id)

            retired, shipped = self._both_spellings(loan, bctx)

            # The loan terms are still there -- only the classifier moved.
            assert resolved_loan(loan, bctx) is not None
            assert classify_account(loan) is not AccountProjectionKind.AMORTIZING
            assert retired is False
            assert shipped is False
            assert retired == shipped

    def test_the_control_the_equivalence_would_fail(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """THE CONTROL: the comparison can distinguish the two spellings.

        Every case above asserts the pair AGREES, and a pair of expressions that
        could never disagree would pass all four vacuously.  This one PATCHES
        the retired spelling's resolution to drop a loan whose schedule is empty
        -- the exact filter a careless reimplementation would add -- and asserts
        ``_both_spellings`` then reports a DISAGREEMENT on the same fixture the
        case above found agreement on.  So the helper can return an unequal
        pair, and ``retired == shipped`` is a real assertion rather than a
        tautology.
        """
        with app.app_context():
            bctx = BalanceContext.build(seed_user["user"].id)
            periods = all_periods(seed_user["user"].id)
            loan, params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            insert_trueup_event(params, Decimal("0.00"))
            db.session.commit()

            # Unpatched, the pair agrees (the case above, restated so the
            # patched result below is a change and not a fresh observation).
            assert self._both_spellings(loan, bctx) == (True, True)

            real = resolved_loan

            def _schedule_rows_only(account, ctx):
                """The careless filter: a loan with no rows is dropped."""
                resolved = real(account, ctx)
                return resolved if resolved and resolved.state.schedule else None

            # The retired spelling reads this MODULE's ``resolved_loan`` name;
            # ``configured_loan`` resolves through its own module's, so only
            # the retired side sees the filter.  It patched the retired side's
            # producer, ``generate_debt_schedules``, until plan step
            # recurrence:R16-c-2 deleted it.
            monkeypatch.setitem(globals(), "resolved_loan", _schedule_rows_only)

            # The retired spelling now says "not a loan" while the shipped one
            # still says "loan" -- the divergence the agreement cases exist to
            # catch, shown to be reachable.
            assert self._both_spellings(loan, bctx) == (False, True)


class TestAFeedIsTheSameWhoeverItIsLoadedBeside:
    """One account's contribution feed does not depend on its batch mates.

    The property :func:`._inputs._contribution_inputs_for_accounts` promises in
    its Returns clause, and it needed a test rather than a comment (plan step
    X-g3b-0's second adversarial review).  The gross FETCH is scoped to the SET
    -- it is skipped entirely when no account in it has investment params -- so
    handing the set's gross to every member would make a checking account's feed
    read ``$0`` alone and the user's real gross beside a 401(k).

    Nothing consumes the field on such an account today (the contribution tier
    short-circuits on kind and params first), which is exactly why the batch
    shape is the only place the difference is observable and exactly why the
    single-account case below cannot stand in for it: on a one-element set the
    set-level gate returns ``ZERO`` on its own, so that assertion passes with
    the per-account arm deleted.
    """

    def test_a_checking_accounts_feed_is_identical_alone_and_in_a_batch(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The contract, asserted as the identity it is.

        The 401(k) beside it is what makes the case non-vacuous: it is the
        account that HAS a feed, so the checking account's empty one is the
        per-account arm's doing and not the whole set answering nothing.

        **The leak this guards became unrepresentable at plan step
        salary:R14-b**, and the case is kept as the pin on that rather than
        deleted.  The bundle carried one ``salary_gross_biweekly`` fetched for
        the SET, so handing it to every member would have made a checking
        account's feed depend on which other accounts shared its read --
        ``$0`` alone and the owner's real gross in a batch.  A feed is priced
        per ACCOUNT now, off the deductions naming it and the profile its own
        params name, so there is no set-wide figure left to leak.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            periods = all_periods(user_id)
            checking = seed_user["account"]
            roth = make_investment_account(
                seed_user, db.session, periods[2], Decimal("10000.00"),
            )
            # A real salary AND a deduction naming the Roth, or NEITHER
            # account has a feed and the case cannot tell the per-account arm
            # from a set that answered nothing.  The non-vacuity assertion
            # below is what caught its absence.
            profile = make_salary_profile(seed_user, db.session)
            db.session.flush()
            db.session.add(PaycheckLine(
                salary_profile_id=profile.id, target_account_id=roth.id,
                name="Roth deferral", amount=Decimal("100.00"),
                calc_method_id=ref_cache.calc_method_id(CalcMethodEnum.FLAT),
                paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                    PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
                ),
                is_active=True,
            ))
            db.session.commit()

            ctx = BalanceContext.build(user_id)
            alone = _contribution_inputs_for_account(checking, ctx)
            batched = _contribution_inputs_for_accounts([checking, roth], ctx)

            # Non-vacuity: the batch really did hand out a feed that prices.
            # Asked of the feed over the pass's saved periods since plan step
            # salary:S3-e-2 (it was ``any(amount > 0)`` over a priced MAP,
            # which S3-e-1 put here when it deleted ``models_employee``).  A
            # stronger claim than the surviving ``is_payroll_linked`` would
            # make, which is why the prices and not the flag are asserted.
            assert any(
                batched[roth.id].feed.employee_at(period) > 0
                for period in ctx.reported_periods()
            )
            # The arm: the account that cannot consume one carries none, in
            # BOTH shapes -- so the two reads are the same object's worth of
            # facts, which is the Returns clause stated as an assertion.
            assert batched[checking.id].feed == AccountPayrollFeed.absent()
            assert alone == batched[checking.id]


class TestMultiLoanIsolation:
    """build_maps keeps each loan's schedule separate (no shared/positional bug)."""

    def test_two_loans_keep_distinct_current_balances(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two trued-up loans in one build_maps keep DISTINCT balances, past AND future.

        A shared or positional debt-schedule forward would collapse both loans onto
        one balance.  Loan A is trued up to $200,000 today and loan B to $50,000, so
        the seam must report each loan's OWN balance -- every producer beneath the
        batch loop is asked per account, never positionally.

        The seam folds each loan from its OWN memoized walk
        (:meth:`~app.services.balance_at.BalanceContext.loan_walk`, keyed
        by account) for begun periods and projects its OWN per-account re-derived
        schedule for the future, so two loans in one ``build_maps`` cannot
        cross-contaminate.  Pinning all three regions catches a regression that
        shared or positionally mis-keyed a loan's balance -- it would surface as
        one loan drifting toward the other's, and the FUTURE tail (where the
        balances diverge most) is where it would show first.

        So all three regions are pinned: each loan's own opening at a pre-anchor
        period ($240,000 / $180,000), its own trued-up balance at the anchor period
        ($200,000 / $50,000), and its own forward projection -- which must still
        straddle the two loans' wildly different balances after today.

        Every one of those figures is what the loan OWES.  The maps report what
        each loan HOLDS, negative when owed (ruling R-CC47, plan step
        credit_card:CC-5-5c), so each is read through ``liability_sign.owed``.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            loan_a, params_a = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1), name="Mortgage A",
            )
            loan_b, params_b = _make_mortgage(
                db, seed_user, periods[0], Decimal("180000.00"),
                date(2024, 1, 1), name="Mortgage B",
            )
            insert_trueup_event(
                params_a, Decimal("200000.00"), anchor_date=date.today(),
            )
            insert_trueup_event(
                params_b, Decimal("50000.00"), anchor_date=date.today(),
            )
            db.session.commit()

            seam_maps = balance_at.build_maps([loan_a, loan_b], bctx)

            anchor_date = date.today()
            anchor_period = next(
                p for p in periods
                if p.start_date <= anchor_date <= last_covered_day(p)
            )
            # The period the true-ups landed in -> each loan's OWN trued-up
            # balance (both are still pre-first-payment there).
            assert liability_sign.owed(
                seam_maps[loan_a.id][anchor_period.id],
            ) == Decimal("200000.00")
            assert liability_sign.owed(
                seam_maps[loan_b.id][anchor_period.id],
            ) == Decimal("50000.00")

            # A period that ended before the true-ups -> each loan's OWN ledger
            # opening (no payment was recorded against either).
            pre_anchor = [p for p in periods if last_covered_day(p) < anchor_date]
            assert pre_anchor, "expected a pre-anchor period"
            earlier = pre_anchor[-1].id
            assert liability_sign.owed(
                seam_maps[loan_a.id][earlier],
            ) == Decimal("240000.00")
            assert liability_sign.owed(
                seam_maps[loan_b.id][earlier],
            ) == Decimal("180000.00")

            # The FUTURE tail -- the region that read the per-loan DebtSchedule
            # bundle's seed until plan step recurrence:R16-c-1 (the bundle went
            # at R16-c-2), and so the one where a positional/shared mix-up
            # could surface.  Each loan must still amortize down from its OWN
            # trued-up balance, so A stays far above B and neither drifts toward the
            # other's schedule.
            future = [p for p in periods if p.start_date > anchor_date]
            assert future, "expected a future period"
            later = future[-1].id
            a_future = liability_sign.owed(seam_maps[loan_a.id][later])
            b_future = liability_sign.owed(seam_maps[loan_b.id][later])
            # A was trued up to 200k and B to 50k; a few biweekly periods of
            # amortization cannot move either near the other.
            assert Decimal("190000.00") < a_future <= Decimal("200000.00"), (
                f"loan A's forward projection {a_future!r} did not amortize from "
                f"its own $200,000 balance; the debt schedules are crossed"
            )
            assert Decimal("45000.00") < b_future <= Decimal("50000.00"), (
                f"loan B's forward projection {b_future!r} did not amortize from "
                f"its own $50,000 balance; the debt schedules are crossed"
            )
