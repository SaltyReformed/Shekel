"""What a reviewed statement pass looks like on the wire -- plan step X-f6a-3c-2.

**This is where this arc's most expensive defect lived.**  At plan step
X-f6a-3b the create form's destination arm was spelled as an ABSENT
``transaction_id``, and the always-rendered, always-prefilled name box therefore
read as a destination of its own -- so the existing-envelope arm was
unreachable from a browser and the door refused all 66 of the developer's lines
that had one.  Three independent adversarial reviews found it; the route test
that should have caught it posted a payload no browser sends.

So this module grades the FORM SHAPE itself, against the two facts that made
that defect possible:

* a browser submits every control it renders, whether or not the owner touched
  it -- so an untouched control must be recognisable as untouched;
* which arm was chosen is a fact the form has to STATE, never one a reader
  infers from an absence.

:func:`~app.schemas.validation.statements.batch_payload` and
:class:`~app.schemas.validation.statements.StatementBatchSchema` are the two
halves: the first regroups a flat ``MultiDict`` into acts, and the second is
the only thing that validates any of it.
"""

from decimal import Decimal

import pytest
from marshmallow import ValidationError
from werkzeug.datastructures import MultiDict

from app.services.statement_match import ReviewedRow, RowKind

from app.schemas.validation.statements import (  # pylint: disable=protected-access
    _MAX_POLICY_ITEMS,
    LEAVE_ALONE,
    NEVER,
    NEW_ENVELOPE,
    NOT_SAID,
    MerchantPolicyBatchSchema,
    StatementBatchSchema,
    batch_payload,
    policy_payload,
)


def _form(pairs):
    """Return a ``MultiDict`` the way a browser would submit one."""
    return MultiDict(pairs)


def _load(form):
    """Regroup and validate one submitted form.

    Args:
        form: The request's ``MultiDict``.

    Returns:
        The loaded payload.

    Raises:
        ValidationError: With marshmallow's own error structure.
    """
    return StatementBatchSchema().load(batch_payload(form))


class TestOnlyWhatWasTickedIsAnAct:
    """Ruling **R-FP** as a property of the PAYLOAD rather than of a click.

    Every proposal on the page renders its ids, ticked or not, because a
    browser has no way to render them conditionally.  What separates the two is
    the checkbox, which a browser submits ONLY when it is ticked.
    """

    def test_an_unticked_proposal_contributes_no_act(self):
        """Its ids are on the wire and it is still not applied."""
        loaded = _load(_form([
            ("csrf_token", "x"),
            ("apply", "0"),
            ("match-0-line_ids", "11"),
            ("match-0-rows", "transaction:42:-180.00:1"),
            # Rendered, submitted, NOT ticked.
            ("match-1-line_ids", "12"),
            ("match-1-rows", "transaction:43:-180.00:1"),
        ]))

        assert loaded["matches"] == [
            {
                "line_ids": [11],
                "rows": [ReviewedRow(
                    kind=RowKind.TRANSACTION, row_id=42,
                    cash_amount=Decimal("-180.00"), version_id=1,
                )],
            },
        ]

    def test_a_GROUP_keeps_every_id_it_submitted_twice(self):
        """``request.form["k"]`` returns the FIRST value; a group has several.

        A route handed the raw ``MultiDict`` refuses a two-row group as "not a
        valid list", which is total in a browser and invisible to any test that
        passes a real list.

        **Both KINDS ride ONE repeated field since plan step
        ``bank_import:X-f6d-3``**, which is what makes that the only place the
        multi-value bug can hide: the two id lists it replaced could not
        desynchronise from each other because neither carried the row's
        reviewed state, and a state carried in a THIRD parallel list could.
        """
        loaded = _load(_form([
            ("apply", "0"),
            ("match-0-line_ids", "11"),
            ("match-0-rows", "transaction:42:-180.00:1"),
            ("match-0-rows", "transaction:43:-20.00:4"),
            ("match-0-rows", "purchase:7:-11.50:2"),
        ]))

        assert loaded["matches"][0]["rows"] == [
            ReviewedRow(
                kind=RowKind.TRANSACTION, row_id=42,
                cash_amount=Decimal("-180.00"), version_id=1,
            ),
            ReviewedRow(
                kind=RowKind.TRANSACTION, row_id=43,
                cash_amount=Decimal("-20.00"), version_id=4,
            ),
            ReviewedRow(
                kind=RowKind.PURCHASE, row_id=7,
                cash_amount=Decimal("-11.50"), version_id=2,
            ),
        ]

    def test_items_arrive_in_the_order_the_screen_rendered_them(self):
        """The receipt reads down the page, so the pass has to run down it.

        The tick values are submitted in DOM order, but a crafted request need
        not be -- and a sort that assumed numbers would raise on a token that
        is not one.
        """
        loaded = _load(_form([
            ("apply", "10"), ("apply", "2"), ("apply", "1"),
            ("match-1-line_ids", "101"),
            ("match-2-line_ids", "102"),
            ("match-10-line_ids", "110"),
        ]))

        assert [item["line_ids"] for item in loaded["matches"]] == [
            [101], [102], [110],
        ]

    def test_a_non_numeric_tick_does_not_raise(self):
        """A crafted ``apply`` value is data, not a crash.

        It names no rendered item, so it contributes an act naming nothing --
        which the accept door's own ``_reject_empty_side`` refuses with a
        sentence, where an exception inside a sort would be a 500 on a door an
        ordinary crafted POST reaches.
        """
        loaded = _load(_form([("apply", "not-an-index")]))

        assert loaded["matches"] == [
            {"line_ids": [], "rows": []},
        ]

    @pytest.mark.parametrize("token, why", [
        ("\N{SUPERSCRIPT TWO}", "isdigit() is True and int() raises"),
        ("\N{ARABIC-INDIC DIGIT ONE}\N{ARABIC-INDIC DIGIT TWO}",
         "a non-ASCII digit script int() DOES convert, to another id"),
        ("9" * 4301, "past CPython's 4,300-digit conversion limit"),
    ])
    def test_a_tick_that_str_isdigit_ACCEPTS_does_not_raise(self, token, why):
        """The branch the case above cannot reach, and it was a live 500.

        ``"not-an-index"`` takes the LEXICAL branch, so it grades nothing about
        the numeric one -- and the numeric one is where the defect was:
        ``str.isdigit()`` is true for 888 characters, 128 of which make
        ``int()`` raise, and true for a digit run longer than CPython will
        convert.  ``app/error_handlers.py`` registers no ``ValueError`` arm, so
        ``apply=%C2%B2`` was an unhandled 500 on the door that applies a whole
        reviewed pass.  This project owns that fact in
        :mod:`app.utils.digit_strings` and this function was not using it.
        Found by adversarial security review 2026-08-19.

        Args:
            token: A spelling ``str.isdigit`` accepts.
            why: What is wrong with it, for the failure message.
        """
        loaded = _load(_form([
            ("apply", token), (f"match-{token}-line_ids", "5"),
        ]))

        assert loaded["matches"] == [
            {"line_ids": [5], "rows": []},
        ], why

    def test_a_destination_KEY_that_isdigit_accepts_does_not_raise(self):
        """The SECOND caller of the same sort key, asked its own question.

        The creation half sorts the ``destination-`` suffixes, so a crafted
        one reaches the identical branch -- and a fix applied to one caller
        proves nothing about the other.  The key is refused as an id by the
        schema, which is the designed answer; what must not happen is a raise
        before any grading runs.
        """
        with pytest.raises(ValidationError) as raised:
            _load(_form([
                ("destination-\N{SUPERSCRIPT TWO}", NEW_ENVELOPE),
                ("envelope_name-\N{SUPERSCRIPT TWO}", "X"),
                ("category_id-\N{SUPERSCRIPT TWO}", "3"),
            ]))

        assert "line_id" in str(raised.value)


class TestTheDestinationSelectIsTheTick:
    """The developer's ruling of 2026-08-19, and the X-f6a-3b defect's fix.

    One control says which of three things the owner meant.  Its default is the
    do-nothing arm, so a pass carrying forty untouched selects records nothing.
    """

    @staticmethod
    def _line_fields(line_id, destination, name="Walmart", category="3"):
        """Return exactly what one creatable row submits."""
        return [
            (f"destination-{line_id}", destination),
            (f"envelope_name-{line_id}", name),
            (f"category_id-{line_id}", category),
        ]

    def test_the_DEFAULT_records_nothing_even_though_it_submits(self):
        """The whole point: an untouched line is not an act.

        Its name box and its category select are both submitted -- the browser
        renders them -- and neither may be read as a destination.  That reading
        is exactly what made the existing-envelope arm unreachable.
        """
        loaded = _load(_form(self._line_fields(88, LEAVE_ALONE)))

        assert loaded["creations"] == []

    def test_an_ENVELOPE_id_names_the_existing_arm(self):
        """The arm that was dead in a browser for one leaf."""
        loaded = _load(_form(self._line_fields(88, "2225")))

        assert loaded["creations"] == [{
            "line_id": 88,
            "destination": 2225,
            "envelope_name": "Walmart",
            "category_id": 3,
        }]

    def test_the_NEW_arm_is_NAMED_rather_than_inferred(self):
        """``"new"`` is a value; "no id" was a guess."""
        loaded = _load(_form(self._line_fields(88, NEW_ENVELOPE)))

        assert loaded["creations"][0]["destination"] == NEW_ENVELOPE

    def test_a_new_envelope_with_no_category_still_LOADS(self):
        """Completeness is the DOOR's question, and that is the ruled shape.

        It was a ``@validates_schema`` rule here, and a nested schema error
        refuses the WHOLE payload -- so an owner who picked "a new envelope"
        and left the category on the form's own default lost every other act
        they had ticked.  The rule now lives in
        ``_create._reject_incomplete_new_envelope``, where it costs one item.
        This asserts the half that makes that possible: the payload survives
        loading, carrying the absence for the door to refuse.
        """
        loaded = _load(_form(self._line_fields(88, NEW_ENVELOPE, category="")))

        assert loaded["creations"] == [{
            "line_id": 88,
            "destination": NEW_ENVELOPE,
            "envelope_name": "Walmart",
            "category_id": None,
        }]

    def test_an_EXISTING_envelope_needs_no_category_at_all(self):
        """The control for the rule above, and the defect it replaced.

        Asking for a category unconditionally told an owner who picked an
        envelope they already had that their NEW envelope was incomplete --
        about a new envelope they had not asked for.
        """
        loaded = _load(_form(self._line_fields(88, "2225", category="")))

        assert loaded["creations"][0]["category_id"] is None

    def test_each_line_carries_its_OWN_fields_by_id(self):
        """Not paired arrays, which depend on the document's own ordering.

        ``reconcile.py``'s ``settled_amount-<id>`` boxes are keyed this way for
        the reason its comment gives: two lists arriving in the same order is a
        property of the page rather than of the form.
        """
        loaded = _load(_form(
            self._line_fields(88, NEW_ENVELOPE, name="Lowes", category="3")
            + self._line_fields(99, "2225", name="Target", category="4")
        ))

        by_line = {item["line_id"]: item for item in loaded["creations"]}
        assert by_line[88]["envelope_name"] == "Lowes"
        assert by_line[88]["destination"] == NEW_ENVELOPE
        assert by_line[99]["envelope_name"] == "Target"
        assert by_line[99]["destination"] == 2225

    def test_the_lines_arrive_in_LINE_order_and_not_field_order(self):
        """The receipt reads down the page, so the pass has to run down it.

        Sorting the raw field names put line 100 between 10 and 2, because
        ``destination-100`` sorts lexically -- and the screen renders these in
        bank-line order.  Nothing about the money depends on it (two creations
        never interact), which is exactly why a wrong order would have gone
        unnoticed while the receipt claimed to read down the page.
        """
        pairs = []
        for line_id in (2, 10, 9, 100):
            pairs += self._line_fields(line_id, NEW_ENVELOPE, category="3")

        loaded = _load(_form(pairs))

        assert [item["line_id"] for item in loaded["creations"]] == [
            2, 9, 10, 100,
        ]

    def test_a_destination_that_names_no_row_is_refused(self):
        """The id half is as strict as :class:`RowId` (finding **N-141**)."""
        with pytest.raises(ValidationError):
            _load(_form(self._line_fields(88, "007")))


class TestThePassIsBounded:
    """A crafted submission is bounded by nothing the screen is bounded by.

    An import may carry 20,000 lines (``_secu_csv.MAX_LINES``), so an account
    can in principle offer more acts than one request has time for: measured,
    an item costs about 43 ms against a 120 s gunicorn timeout.
    """

    def test_a_pass_over_the_ceiling_is_REFUSED_and_says_so(self):
        """Never silently truncated -- half a pass applied without a word is
        worse than a refusal the owner can act on."""
        pairs = [("apply", str(index)) for index in range(501)]
        pairs += [
            (f"match-{index}-line_ids", str(index + 1))
            for index in range(501)
        ]

        with pytest.raises(ValidationError) as raised:
            _load(_form(pairs))

        assert "at most 500" in str(raised.value)

    def test_a_pass_at_the_ceiling_still_loads(self):
        """The control: the bound is a ceiling, not an off-by-one."""
        pairs = [("apply", str(index)) for index in range(500)]
        pairs += [
            (f"match-{index}-line_ids", str(index + 1))
            for index in range(500)
        ]

        loaded = _load(_form(pairs))

        assert len(loaded["matches"]) == 500

    def test_the_two_KINDS_are_bounded_together(self):
        """What a request's time budget cares about is the SUM.

        Two lists with their own ceilings would admit twice the work either
        one allows.
        """
        pairs = [("apply", str(index)) for index in range(300)]
        pairs += [
            (f"match-{index}-line_ids", str(index + 1))
            for index in range(300)
        ]
        for line_id in range(1000, 1300):
            pairs += [
                (f"destination-{line_id}", NEW_ENVELOPE),
                (f"envelope_name-{line_id}", "X"),
                (f"category_id-{line_id}", "3"),
            ]

        with pytest.raises(ValidationError) as raised:
            _load(_form(pairs))

        assert "600 things to apply" in str(raised.value)


class TestTheBatchSchemaRefusesWhatItDoesNotDeclare:
    """``unknown = RAISE``, deliberately, where its siblings EXCLUDE.

    :class:`~app.schemas.validation._helpers.BaseSchema` drops unknown keys so
    a form's ``csrf_token`` does not have to be declared -- correct for a
    payload that comes straight off a form.  This one never sees a form:
    :func:`batch_payload` has already turned one into two lists, so a key this
    schema does not declare is a regrouper and a schema that disagree, on the
    payload carrying every act in a pass.
    """

    def test_an_undeclared_key_is_refused(self):
        with pytest.raises(ValidationError):
            StatementBatchSchema().load({"matches": [], "smuggled": [1]})

    def test_the_regroupers_own_output_is_accepted(self):
        """The control: the two halves agree about the shape."""
        loaded = StatementBatchSchema().load(batch_payload(_form([
            ("csrf_token", "x"),
        ])))

        assert loaded == {"matches": [], "creations": []}


class TestThePolicySectionOnTheWire:
    """Where your merchants go, as a form submits it -- plan step X-f6a-3d.

    The same two facts this module was written for, on the section this leaf
    adds: a browser submits every control it renders, and which answer was
    chosen is a fact the form STATES rather than one a reader infers.  The
    consequence is sharper here than one card down, because the section
    submits every merchant on the account on every pass -- so *untouched* is
    the ordinary case rather than the edge, and a reader that could not
    recognise it would rewrite twenty answers to say what they already said.
    """

    @staticmethod
    def _row(index, merchant, answer, name="", category=""):
        """Return the fields ONE merchant row submits, as a browser sends them."""
        return [
            (f"policy-{index}", answer),
            (f"policy_merchant-{index}", merchant),
            (f"policy_name-{index}", name),
            (f"policy_category-{index}", category),
        ]

    def test_every_rendered_row_submits_including_the_untouched_ones(self):
        """The premise the door's "unchanged" arm exists for.

        There is no way to tell an untouched control from a deliberately
        repeated answer on the wire, and inventing one -- a hidden "what it
        was" field -- would be a value the submitter could forge into a write
        nobody asked for.  So every row arrives and the SERVICE compares.
        """
        payload = policy_payload(_form(
            self._row(0, "Amazon", "t:38")
            + self._row(1, "Walmart", NOT_SAID)
            + self._row(2, "Capital One", NEVER),
        ))

        assert [item["merchant"] for item in payload["policies"]] == [
            "Amazon", "Walmart", "Capital One",
        ]

    def test_NOT_SAID_is_carried_rather_than_dropped(self):
        """It is how a policy is WITHDRAWN, so it cannot be an absence.

        The destination select one card down drops its do-nothing value,
        because there the default means "do not record this line" and there is
        nothing to undo.  Here it means "forget what I said", which is an act
        -- and dropping it would make a stated policy permanent.
        """
        payload = policy_payload(_form(self._row(0, "Amazon", NOT_SAID)))

        assert payload["policies"] == [{
            "merchant": "Amazon", "answer": NOT_SAID,
            "envelope_name": "", "category_id": "",
        }]

    def test_a_TEMPLATE_answer_carries_its_arm_with_its_id(self):
        """The arm is STATED, never inferred from the value's shape.

        A bare number would have to be read as "a template" by convention,
        which is the inference that made the existing-envelope destination
        unreachable from a browser one leaf earlier.
        """
        loaded = MerchantPolicyBatchSchema().load(
            policy_payload(_form(self._row(0, "Amazon", "t:38"))),
        )

        assert loaded["policies"][0]["answer"] == 38

    def test_the_four_answers_all_load(self):
        """The closed set, so none of them is refused by the grader."""
        loaded = MerchantPolicyBatchSchema().load(policy_payload(_form(
            self._row(0, "Amazon", "t:38")
            + self._row(1, "Walmart", NOT_SAID)
            + self._row(2, "Capital One", NEVER)
            + self._row(3, "Lowe's", NEW_ENVELOPE, name="Lowe's", category="4"),
        )))

        assert [item["answer"] for item in loaded["policies"]] == [
            38, NOT_SAID, NEVER, NEW_ENVELOPE,
        ]
        assert loaded["policies"][3]["envelope_name"] == "Lowe's"
        assert loaded["policies"][3]["category_id"] == 4

    def test_an_answer_with_no_merchant_beside_it_names_nothing(self):
        """Unreachable from this screen -- the two fields render together.

        Dropped rather than refused, because a crafted body naming an answer
        for nobody has asked for nothing, and refusing would give a caller a
        way to fail a whole legitimate pass by appending one key.
        """
        payload = policy_payload(_form([("policy-9", NEVER)]))

        assert payload["policies"] == []

    def test_a_merchant_key_that_str_isdigit_ACCEPTS_does_not_raise(self):
        """The same trap the tick keys carry, on this section's keys.

        ``apply=%C2%B2`` was a 500 on the money door until plan step
        X-f6a-3c-2, because ``str.isdigit`` is true for 888 characters and
        ``int()`` refuses 128 of them.  These keys are sorted through the same
        ``_sort_key``, so the fix covers them -- and this is what says so.
        """
        payload = policy_payload(_form(
            [("policy-\N{SUPERSCRIPT TWO}", NEVER),
             ("policy_merchant-\N{SUPERSCRIPT TWO}", "Amazon")],
        ))

        assert [item["merchant"] for item in payload["policies"]] == ["Amazon"]

    def test_a_respelled_template_id_is_refused(self):
        """``t:007`` names no template, exactly as ``007`` names no envelope.

        A second, laxer reading of a row id on a screen that decides where
        money is filed is what plan step X-ae removed.
        """
        with pytest.raises(ValidationError):
            MerchantPolicyBatchSchema().load(
                policy_payload(_form(self._row(0, "Amazon", "t:007"))),
            )

    def test_an_undeclared_key_is_refused(self):
        """``unknown = RAISE``: nothing is swallowed on this payload either."""
        with pytest.raises(ValidationError):
            MerchantPolicyBatchSchema().load(
                {"policies": [], "sneaky": 1},
            )

    def test_a_submission_over_the_CEILING_is_refused_and_says_so(self):
        """The policy ceiling had no test at either tier.

        Its sibling ``_MAX_BATCH_ITEMS`` is graded twice; this one could be
        raised to a billion with the suite green.  It is a SEPARATE bound on
        purpose -- that one paces money acts, each running a settle door, and
        this one paces small writes over a set the account's own lines bound --
        so it needs its own control rather than inheriting that one's.
        """
        rows = []
        for index in range(_MAX_POLICY_ITEMS + 1):
            rows.extend(self._row(index, f"Merchant {index}", NEVER))

        with pytest.raises(ValidationError) as caught:
            MerchantPolicyBatchSchema().load(policy_payload(_form(rows)))

        assert "at most" in str(caught.value)

    def test_a_submission_AT_the_ceiling_still_loads(self):
        """The bound pinned from the other side, so it is not off by one."""
        rows = []
        for index in range(_MAX_POLICY_ITEMS):
            rows.extend(self._row(index, f"Merchant {index}", NEVER))

        loaded = MerchantPolicyBatchSchema().load(policy_payload(_form(rows)))

        assert len(loaded["policies"]) == _MAX_POLICY_ITEMS

    def test_a_merchant_longer_than_the_COLUMN_is_refused(self):
        """The key may not be stored truncated, so it may not be submitted long.

        ``bank_statement_lines.merchant`` is 100 characters and the adapter
        reads no longer token; a policy keyed on a truncated string would be a
        DIFFERENT key from the one the bank named, silently.
        """
        with pytest.raises(ValidationError):
            MerchantPolicyBatchSchema().load(
                policy_payload(_form(self._row(0, "X" * 101, NEVER))),
            )
