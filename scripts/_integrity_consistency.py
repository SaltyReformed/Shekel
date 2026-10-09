"""
Shekel Budget App -- Data Integrity Check: the consistency family (DC-*)

One of the four family modules ``scripts/integrity_check.py`` runs, split
along its ``--category`` values at plan step ``balance:X-bi-6-4c-4`` (ruling
**R-BAL161**); ``scripts/_integrity_core.py`` says why.
"""

from scripts._integrity_core import (
    TRANSFER_LEG_RECORDS_JOIN,
    CheckSpec,
    run_check,
)


# ── Category 4: Data Consistency ─────────────────────────────────


def check_data_consistency(session):
    """Run all DC-* data consistency checks.

    Cross-table logical consistency validations.

    Args:
        session: SQLAlchemy session.

    Returns:
        List of CheckResult for checks DC-02 through DC-12.

    Note:
        DC-01 ("done/received transactions without actual_amount") was
        removed 2026-06-11: settling without a manual actual is a
        designed, documented state (``MarkDoneSchema`` deliberately
        leaves the column untouched and ``Transaction.effective_amount``
        falls back to ``estimated_amount``), so the check flagged
        routine legal data on every prod run.  The remaining IDs keep
        their historical numbers so past run logs stay comparable.
    """
    results = []

    # DC-02: Transfers where from_account equals to_account (warning).
    results.append(run_check(session, CheckSpec(
        "DC-02", "consistency", "warning",
        "Transfers where from_account equals to_account",
        """
        SELECT tr.id, tr.name, tr.from_account_id, tr.to_account_id
        FROM budget.transfers tr
        WHERE tr.from_account_id = tr.to_account_id
        """,
    )))

    # DC-03: Account type-specific params mismatch (warning).
    # Checks: interest-bearing accounts without interest_params.
    results.append(run_check(session, CheckSpec(
        "DC-03", "consistency", "warning",
        "Typed accounts missing their type-specific params",
        """
        SELECT a.id, a.name, at.name AS type_name, atc.name AS category_name
        FROM budget.accounts a
        JOIN ref.account_types at ON a.account_type_id = at.id
        JOIN ref.account_type_categories atc ON at.category_id = atc.id
        LEFT JOIN budget.interest_params hp ON hp.account_id = a.id
        LEFT JOIN budget.loan_params lp ON lp.account_id = a.id
        WHERE (at.name = 'HYSA' AND hp.id IS NULL)
           OR (at.has_amortization = TRUE AND lp.id IS NULL)
        """,
    )))

    # DC-04: Self-referential credit payback cycles (warning).
    # A chain longer than 1: A.credit_payback_for_id -> B.credit_payback_for_id -> C.
    results.append(run_check(session, CheckSpec(
        "DC-04", "consistency", "warning",
        "Credit payback chains longer than 1 level",
        """
        SELECT t1.id AS txn_id, t1.credit_payback_for_id AS pays_back,
               t2.credit_payback_for_id AS chain_pays_back
        FROM budget.transactions t1
        JOIN budget.transactions t2 ON t1.credit_payback_for_id = t2.id
        WHERE t2.credit_payback_for_id IS NOT NULL
        """,
    )))

    # DC-05: Active templates for inactive accounts (warning).
    results.append(run_check(session, CheckSpec(
        "DC-05", "consistency", "warning",
        "Active templates referencing inactive accounts",
        """
        SELECT tt.id, tt.name, tt.account_id, a.name AS account_name
        FROM budget.transaction_templates tt
        JOIN budget.accounts a ON tt.account_id = a.id
        WHERE tt.is_active = TRUE
          AND a.is_active = FALSE
        """,
    )))

    # DC-06: Two generated rows answering ONE occurrence (critical).
    # The predicate mirrors the schema's own uniqueness contract, and plan
    # step **R17** re-keyed that contract off the paycheck and onto the
    # occurrence: a row answers one occurrence of its template's cadence, and
    # the pay period is only where that occurrence's money lands.  Asking the
    # old question here would report a CORRECT state as critical corruption --
    # a cadence that names one paycheck twice (a monthly bill at a pay cadence
    # of 30 days or more) legitimately stores two rows there, which is exactly
    # what the re-key made possible.
    #
    # FOUR arms: two contracts across two tables.  ``budget.transfers`` carries
    # the identical pair of indexes and the identical duplicate-money failure,
    # and it went ungraded here until plan step R17 -- which is the step that
    # makes "two rows in one paycheck" a LEGAL state, so the check that says
    # which pairs are legal now has to exist on both tables.  The ``source``
    # column names which one reported.
    #
    # Two contracts, because a row that answers an
    # occurrence is unique on it, and a row that answers NONE
    # (``occurs_on IS NULL`` -- a carry-forward roll-forward, a one-time
    # transfer) still holds its paycheck alone.  Both stay unique only WHERE
    # ``is_override = FALSE``: an override sibling legally coexists with the
    # rule-generated row for its target period.
    results.append(run_check(session, CheckSpec(
        "DC-06", "consistency", "critical",
        "Two generated rows answering one occurrence (or one paycheck, undated)",
        """
        SELECT 'transactions' AS source, template_id, scenario_id, occurs_on,
               NULL::integer AS pay_period_id, COUNT(*) AS cnt
        FROM budget.transactions
        WHERE template_id IS NOT NULL
          AND occurs_on IS NOT NULL
          AND is_deleted = FALSE
          AND is_override = FALSE
        GROUP BY template_id, scenario_id, occurs_on
        HAVING COUNT(*) > 1
        UNION ALL
        SELECT 'transactions', template_id, scenario_id, NULL::date,
               pay_period_id, COUNT(*)
        FROM budget.transactions
        WHERE template_id IS NOT NULL
          AND occurs_on IS NULL
          AND is_deleted = FALSE
          AND is_override = FALSE
        GROUP BY template_id, scenario_id, pay_period_id
        HAVING COUNT(*) > 1
        UNION ALL
        SELECT 'transfers', transfer_template_id, scenario_id, occurs_on,
               NULL::integer, COUNT(*)
        FROM budget.transfers
        WHERE transfer_template_id IS NOT NULL
          AND occurs_on IS NOT NULL
          AND is_deleted = FALSE
          AND is_override = FALSE
        GROUP BY transfer_template_id, scenario_id, occurs_on
        HAVING COUNT(*) > 1
        UNION ALL
        SELECT 'transfers', transfer_template_id, scenario_id, NULL::date,
               pay_period_id, COUNT(*)
        FROM budget.transfers
        WHERE transfer_template_id IS NOT NULL
          AND occurs_on IS NULL
          AND is_deleted = FALSE
          AND is_override = FALSE
        GROUP BY transfer_template_id, scenario_id, pay_period_id
        HAVING COUNT(*) > 1
        """,
    )))

    # DC-07: Users without user_settings (critical).
    results.append(run_check(session, CheckSpec(
        "DC-07", "consistency", "critical",
        "Users without a user_settings row",
        """
        SELECT u.id, u.email
        FROM auth.users u
        LEFT JOIN auth.user_settings s ON u.id = s.user_id
        WHERE s.id IS NULL
        """,
    )))

    # DC-08: Users without a baseline scenario (critical).
    # Companion-role users are excluded: a companion views the linked
    # owner's data and owns no budget rows of their own (no accounts,
    # no periods, no scenarios) by design, so "no baseline scenario"
    # is their correct steady state, not a defect.
    results.append(run_check(session, CheckSpec(
        "DC-08", "consistency", "critical",
        "Users without a baseline scenario",
        """
        SELECT u.id, u.email
        FROM auth.users u
        JOIN ref.user_roles r ON u.role_id = r.id
        LEFT JOIN budget.scenarios s
          ON u.id = s.user_id AND s.is_baseline = TRUE
        WHERE s.id IS NULL
          AND r.name != 'companion'
        """,
    )))

    # DC-09: Salary deduction target accounts belonging to a different user (warning).
    results.append(run_check(session, CheckSpec(
        "DC-09", "consistency", "warning",
        "Salary deductions targeting another user's account",
        """
        SELECT pd.id, pd.name AS deduction_name,
               sp.user_id AS profile_user, a.user_id AS account_user
        FROM salary.paycheck_lines pd
        JOIN salary.salary_profiles sp ON pd.salary_profile_id = sp.id
        JOIN budget.accounts a ON pd.target_account_id = a.id
        WHERE pd.target_account_id IS NOT NULL
          AND sp.user_id != a.user_id
        """,
    )))

    # DC-10: An UN-DATED movement holding a live journal leg (critical).
    #
    # ``_posting_purchases.purchase_posts`` is the write side's one statement
    # of "this movement is in the ledger": a contributing parent, a debit, a
    # RECORDED posting day, and for a transfer leg its record.  So a movement
    # with no ``settled_on`` owes the ledger nothing, and a non-zero net of
    # postings linked to it is money booked for a day nobody has stated.
    # Reachable since plan step ``balance:X-bi-3e-2``, when a revert began
    # KEEPING the status seam's covering movement un-dated (ruling **R-BAL61**):
    # the seam releases the day and the DOOR's family reconcile reverses the legs
    # (``transaction_service.apply_requested_status`` ->
    # ``posting_service.sync_transaction_postings``), so a caller that
    # reached the bare seam and never reconciled would leave exactly this
    # state -- and this arm grades it without depending on that door.  Over
    # EVERY un-dated entry, not the seam's alone: a purchase whose day was
    # cleared through ``entry_service.update_entry`` reconciles through the
    # same family walk (``_doors._resync_after_entry_change`` ->
    # ``sync_transaction_postings``) and owes the same zero.  Net per ledger
    # account: a reversed leg appears with its reversal and nets to zero, so
    # a fully-reversed movement does not report.  A finding names the
    # movement's PARENT, which is a row (``transaction_id``) or, since plan
    # step ``balance:X-bi-6-4d-2``, a transfer whose side the movement is filed
    # under (``transfer_id``, ruling **R-BAL88**) -- one of the two, never
    # both (``ck_transaction_entries_one_parent``); a transfer side's record
    # had named its shadow row until then, which the operator could not act on.
    results.append(run_check(session, CheckSpec(
        "DC-10", "consistency", "critical",
        "Un-dated movements (no settled_on) holding a live journal leg",
        """
        SELECT e.id AS entry_id, e.transaction_id,
               COALESCE(e.expense_transfer_id, e.income_transfer_id)
                 AS transfer_id,
               e.covers_settlement,
               p.ledger_account_id, SUM(p.amount) AS net
        FROM budget.transaction_entries e
        JOIN budget.journal_entries je ON je.transaction_entry_id = e.id
        JOIN budget.account_postings p ON p.journal_entry_id = je.id
        WHERE e.settled_on IS NULL
        GROUP BY e.id, e.transaction_id, e.expense_transfer_id,
                 e.income_transfer_id, e.covers_settlement,
                 p.ledger_account_id
        HAVING SUM(p.amount) <> 0
        ORDER BY e.id, p.ledger_account_id
        """,
    )))

    # DC-11: A settled row whose money no reader can see (critical).
    #
    # Since plan step ``balance:X-bi-4a`` the cash fold and the posting
    # writer read a settled row's money as its MOVEMENTS and nothing of the
    # row (ruling **R-BAL80**), and the settled stream admits a movement by
    # ITS OWN ``settled_on``.  So two states are money the balance silently
    # omits: a row in the settled band with NO settle day, and a covering
    # movement that exists but carries NO ``settled_on`` under a settled row
    # -- the fold's real input, which the row's own day does not stand in
    # for.  The first is the state the cash walk REFUSED loudly through
    # ``X-bi-3e`` (``balance_predicates.settled_day`` raised on a dateless
    # settled row the fold read) and can no longer meet.  Neither is a
    # door's: the seam writes the status, the day and the movement in one
    # act and dates the movement on the row's day, and the cutover migration
    # ``ad573b07bede`` refused a dateless row and covered every other.  (A
    # third arm -- a stored non-zero figure with no covering movement --
    # graded the row's own ``settled_amount`` against the movement through
    # plan step ``balance:X-bi-4b-1``; the column went at ``X-bi-4b-2``,
    # migration ``45f10b870c8b``, which refused any row where the two
    # disagreed, and a settled row with no movement is the ``$0.00`` record
    # since, ruling **R-BAL82**.)
    # A settled TRANSFER's money is graded as its LEGS since leaf ``X-bi-6-4a``
    # (ruling **R-BAL106**; the UNION's second arm, one row per undated leg).
    # Its leg join is ``_integrity_core.TRANSFER_LEG_RECORDS_JOIN`` since plan
    # step ``balance:X-bi-6-4c-4``, which BA-06 reads too, so plan step
    # ``balance:X-bi-6-4d-2`` moved the sweep's one raw spelling of it onto
    # the side links in one place.
    # **The row arm grades no SHADOW since that step** (``t.transfer_id IS
    # NULL`` over the whole arm; it graded a shadow's own missing day until
    # then).  A shadow holds no money from that step -- its side's record hangs
    # off the transfer, which the leg arm grades -- and no reader reads a
    # shadow's status or day: the status seam's Transfer arm writes the
    # transfer and each side's record and leaves the twins as they were.  So
    # a shadow's day is no fact the fold can miss, and grading it would be
    # wrong as well as idle: a transfer CREATED settled builds its twins in
    # its settled status with no day (``transfer_service._create._build_shadow``
    # states none, and the arm dates the side records instead), which this arm
    # read as CRITICAL money the fold cannot see.  The twins themselves go at
    # ``X-bi-6-4d-3`` (ruling **R-BAL166**), with DC-12.
    results.append(run_check(session, CheckSpec(
        "DC-11", "consistency", "critical",
        "Settled rows the fold cannot see: no settle day, or a covering "
        "movement with no day; settled transfers holding an undated leg",
        f"""
        SELECT t.id AS transaction_id, NULL::integer AS transfer_id,
               t.account_id, s.name AS status, t.settled_on,
               (SELECT COUNT(*) FROM budget.transaction_entries e
                 WHERE e.transaction_id = t.id AND e.covers_settlement)
                 AS covering_movements,
               (SELECT COUNT(*) FROM budget.transaction_entries e
                 WHERE e.transaction_id = t.id AND e.covers_settlement
                   AND e.settled_on IS NULL)
                 AS undated_covering_movements
        FROM budget.transactions t
        JOIN ref.statuses s ON s.id = t.status_id
        WHERE s.is_settled AND NOT t.is_deleted AND t.transfer_id IS NULL
          AND (
            t.settled_on IS NULL
            OR EXISTS (
              SELECT 1 FROM budget.transaction_entries e
              WHERE e.transaction_id = t.id AND e.covers_settlement
                AND e.settled_on IS NULL
            )
          )
        UNION ALL
        SELECT NULL::integer, x.id, e.account_id, s.name, NULL::date, 1, 1
        FROM budget.transfers x
        JOIN ref.statuses s ON s.id = x.status_id{TRANSFER_LEG_RECORDS_JOIN}
        WHERE s.is_settled AND NOT x.is_deleted AND e.settled_on IS NULL
        ORDER BY transaction_id NULLS LAST, transfer_id, account_id
        """,
    )))

    # DC-12: A live transfer whose live shadows number other than two
    # (critical).
    #
    # Transfer Invariant 1: every transfer has exactly two linked shadow
    # rows, one expense and one income.  Through plan step ``balance:X-bi-6-3``
    # the posting writer was the app's only DETECTOR of a broken pair: it
    # read the INCOME shadow's record to book the pair as one entry and
    # refused when that shadow was missing, and the deploy resync warned
    # about such pairs.  Under ruling **R-BAL45**'s shape C each side's
    # covering movement posts on its own against the owner's
    # Transfers-in-transit account (ruling **R-BAL101**), so a pair with one
    # side is the honest in-transit state to the writer -- money left one
    # account and has not arrived -- and no door polices the count.  Which is
    # right for the writer and wrong for the app as a whole: a transfer whose
    # income shadow vanished would leave its cash sitting in transit with
    # nothing to arrive, visible nowhere.  So the invariant lives here, where
    # the states that are nobody's door to police already live (DC-10, DC-11),
    # read by the operator's integrity run and never by the writer.  Developer
    # ruling 2026-09-21 (the leaf's adversarial review, finding 2).  Counts
    # LIVE shadows of LIVE transfers: a soft-deleted pair carries its flag on
    # all three rows, and a hard-deleted transfer takes its shadows with it
    # (CASCADE).  Dies with the shadow rows at ``X-bi-6-5``.
    results.append(run_check(session, CheckSpec(
        "DC-12", "consistency", "critical",
        "Live transfers whose live shadow rows number other than two",
        """
        SELECT t.id AS transfer_id, t.user_id, t.from_account_id,
               t.to_account_id,
               (SELECT COUNT(*) FROM budget.transactions sh
                 WHERE sh.transfer_id = t.id AND NOT sh.is_deleted)
                 AS live_shadows
        FROM budget.transfers t
        WHERE NOT t.is_deleted
          AND (SELECT COUNT(*) FROM budget.transactions sh
                WHERE sh.transfer_id = t.id AND NOT sh.is_deleted) <> 2
        ORDER BY t.id
        """,
    )))

    return results
