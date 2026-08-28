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

from app.schemas.validation.merchant_rules import (  # pylint: disable=protected-access
    _MAX_RULE_ITEMS,
    ALWAYS_ASK,
    NEVER,
    NOT_SAID,
    MerchantRuleBatchSchema,
    rule_payload,
)
from app.schemas.validation.statements import (
    StatementMatchSchema,
    hand_match_payload,
    LEAVE_ALONE,
    NEW_ENVELOPE,
    StatementBatchSchema,
    batch_payload,
)


def _form(pairs):
    """Return a ``MultiDict`` the way a browser would submit one."""
    return MultiDict(pairs)


def _load_hand(form):
    """Regroup and validate the WORKBENCH's one-group submission.

    Plan step ``bank_import:X-gf-3b``: the consent box moved to a surface with
    a door of its own, so the body it rides in is read by
    :func:`~app.schemas.validation.statements.hand_match_payload` and graded by
    :class:`~app.schemas.validation.statements.StatementMatchSchema` directly --
    there is no ordering index, so there is no list of items to index into.

    Args:
        form: The request's ``MultiDict``.

    Returns:
        The loaded match.

    Raises:
        ValidationError: With marshmallow's own error structure.
    """
    return StatementMatchSchema().load(hand_match_payload(form))


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
                # A proposal carries no accepted difference: the app's own
                # tiers propose only where the two sides agree exactly (plan
                # step bank_import:X-f6d-4).
                "residual": None,
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
            {"line_ids": [], "rows": [], "residual": None},
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
            {"line_ids": [5], "rows": [], "residual": None},
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


class TestTheDifferenceTheOwnerAccepted:
    """Plan step ``bank_import:X-f6d-4``: the one field that is not per-row.

    The consent box is rendered DISABLED with ``value=""`` in lockstep, so an
    unticked group, and a browser with no JavaScript, both submit nothing at
    all.  What this grades is that the three states are distinguishable on the
    wire and that a hostile spelling cannot reach the door as a figure.

    **It grades ``hand_match_payload``, not ``batch_payload``, since plan step
    ``bank_import:X-gf-3b``.**  Every case here used to send ``apply=hand`` and
    ``match-hand-*`` -- a shape no rendered control emits any more, because the
    consent box moved to the workbench and its door reads flat field names.
    The assertions are unchanged; the reader beneath them is the one that now
    receives this body.  A class grading a payload nothing submits is a class
    that has stopped testing what it names, which is what an adversarial
    test-quality review found on 2026-08-28.
    """

    def test_an_unticked_group_carries_NONE(self):
        """Absence is a state the SCHEMA names, not one the reader invents.

        ``batch_payload`` omits the key rather than sending ``None``, so the
        default lives in exactly one place.
        """
        loaded = _load_hand(_form([
            ("line_ids", "11"),
            ("rows", "transaction:42:-180.00:1"),
        ]))

        assert loaded["residual"] is None

    def test_a_ticked_group_carries_the_figure_it_showed(self):
        """A signed decimal, read into a ``Decimal`` for the door to compare."""
        loaded = _load_hand(_form([
            ("line_ids", "11"),
            ("rows", "transaction:42:2473.38:1"),
            ("rows", "transaction:43:100.00:1"),
            ("residual", "0.05"),
        ]))

        assert loaded["residual"] == Decimal("0.05")

    def test_a_NEGATIVE_difference_is_read_as_one(self):
        """The bank took more than the rows say, which is the expense arm."""
        loaded = _load_hand(_form([
            ("line_ids", "11"),
            ("rows", "transaction:42:-180.00:1"),
            ("residual", "-0.06"),
        ]))

        assert loaded["residual"] == Decimal("-0.06")

    @pytest.mark.parametrize("spelling, why", [
        ("NaN", "compares unequal to every figure, so a guard becomes a no-op"),
        ("Infinity", "not a figure any row can hold"),
        ("1e1000000000", "an exponent Decimal cannot quantize"),
        ("0.05; DROP", "not a number at all"),
        ("1_0", "a spelling the row token on this same form refuses"),
        ("+0.05", "a leading plus the row token refuses"),
        (" 0.05 ", "surrounding whitespace the row token refuses"),
    ])
    def test_a_hostile_spelling_is_REFUSED(self, spelling, why):
        """Each of these reaches the field from a crafted POST.

        A ``NaN`` is the one that matters most and it is why this class exists:
        the door compares the accepted figure with its own using ``!=``, and
        ``Decimal("NaN") != x`` is TRUE for every x -- so a ``NaN`` slipping
        through would not open the door, it would jam it shut on every group,
        which is a denial dressed as a safety.  The other spellings are the
        ones this project has already paid to learn about
        (``_submission._FIGURE``).

        Args:
            spelling: What a crafted body sends.
            why: What is wrong with it, for the failure message.
        """
        with pytest.raises(ValidationError) as caught:
            _load_hand(_form([
                    ("line_ids", "11"),
                ("rows", "transaction:42:-180.00:1"),
                ("residual", spelling),
            ]))

        assert "residual" in caught.value.messages, why

    def test_a_SUB_CENT_figure_is_read_verbatim_and_left_to_the_door(self):
        """The reader is about the FORMAT; the VALUE is the door's question.

        This is where a quantizer went wrong.  ``fields.Decimal(places=2)``
        REPAIRED ``0.054`` into ``0.05`` and it then passed as consent for a
        true difference of ``0.05`` -- a half-cent tolerance on the one field
        the design says is exact, using the rounding mode
        :mod:`app.utils.money` forbids.  Read verbatim, it simply is not the
        door's own figure, and the door says so
        (``test_residual.TestTheFigureTheOwnerAcceptedIsReconciled``).
        """
        loaded = _load_hand(_form([
            ("line_ids", "11"),
            ("rows", "transaction:42:-180.00:1"),
            ("residual", "0.054"),
        ]))

        assert loaded["residual"] == Decimal("0.054")

    def test_an_EMPTY_consent_box_is_UNTOUCHED_rather_than_malformed(self):
        """This module's founding principle, on the newest control.

        A browser submits every control it renders, so an untouched one has to
        be recognisable as untouched.  The panel keeps ``value=""`` and
        ``disabled`` in lockstep so no browser sends this -- and a body that
        does would otherwise 400 the WHOLE pass over a field nobody filled in.
        """
        loaded = _load_hand(_form([
            ("line_ids", "11"),
            ("rows", "transaction:42:-180.00:1"),
            ("residual", ""),
        ]))

        assert loaded["residual"] is None

    def test_a_REPEATED_consent_cannot_desynchronise_the_item(self):
        """One consent per item, so a repeated key keeps the first.

        Whichever it keeps still has to EQUAL the difference the door derives
        from the same submission's rows, so no repeated key can choose what
        gets written -- which is why this reads with ``get`` rather than
        growing a list the schema would then have to reconcile.
        """
        loaded = _load_hand(_form([
            ("line_ids", "11"),
            ("rows", "transaction:42:-180.00:1"),
            ("residual", "0.05"),
            ("residual", "-999.00"),
        ]))

        assert loaded["residual"] == Decimal("0.05")



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

        # SPELLED OUT rather than derived from the schema's own field list:
        # deriving it would make this a tautology, and the whole point is that
        # a THIRD kind of act (ruling **bank_import:R-GW**'s incomes) added on one side
        # and not the other is caught here.
        assert loaded == {"matches": [], "creations": [], "incomes": []}


class TestTheRuleSectionOnTheWire:
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
    def _row(index, merchant_id, answer, name="", category=""):
        """Return the fields ONE merchant row submits, as a browser sends them.

        *merchant_id* is the merchant ROW the hidden input carries (plan step
        ``bank_import:X-gd-1``).  It was the bank's own STRING, which is what
        made the schema unable to say anything about it at all.
        """
        return [
            (f"rule-{index}", answer),
            (f"rule_merchant-{index}", str(merchant_id)),
            (f"rule_name-{index}", name),
            (f"rule_category-{index}", category),
        ]

    def test_every_rendered_row_submits_including_the_untouched_ones(self):
        """The premise the door's "unchanged" arm exists for.

        There is no way to tell an untouched control from a deliberately
        repeated answer on the wire, and inventing one -- a hidden "what it
        was" field -- would be a value the submitter could forge into a write
        nobody asked for.  So every row arrives and the SERVICE compares.
        """
        payload = rule_payload(_form(
            self._row(0, 11, "t:38")
            + self._row(1, 12, NOT_SAID)
            + self._row(2, 13, NEVER),
        ))

        assert [item["merchant_id"] for item in payload["rules"]] == [
            "11", "12", "13",
        ]

    def test_NOT_SAID_is_carried_rather_than_dropped(self):
        """An arm is STATED, never inferred from an absence.

        The destination select one card down drops its do-nothing value,
        because there the default means "do not record this line" and there is
        nothing to undo.  Here it means "state nothing about this merchant",
        which the ROUTE drops -- but it has to ARRIVE for the route to drop it,
        and ``answer`` is ``required=True`` while ``BaseSchema``'s ``@pre_load``
        normalizer removes every ``""`` a form submits.  So an arm spelled as
        an absence is an arm that never reaches the door at all, which is what
        this case pins.

        **Its original reason was that this value WITHDREW a rule**, and ruling
        R-GS deleted the withdrawal in the same step that kept this case
        (plan step ``bank_import:X-gd-2``).  A stated rule is now permanent by
        design; the surviving reason is the one above.  Found by adversarial
        review 2026-08-26.
        """
        payload = rule_payload(_form(self._row(0, 11, NOT_SAID)))

        assert payload["rules"] == [{
            "merchant_id": "11", "answer": NOT_SAID,
            "envelope_name": "", "category_id": "",
        }]

    def test_a_TEMPLATE_answer_carries_its_arm_with_its_id(self):
        """The arm is STATED, never inferred from the value's shape.

        A bare number would have to be read as "a template" by convention,
        which is the inference that made the existing-envelope destination
        unreachable from a browser one leaf earlier.
        """
        loaded = MerchantRuleBatchSchema().load(
            rule_payload(_form(self._row(0, 11, "t:38"))),
        )

        assert loaded["rules"][0]["answer"] == 38

    def test_the_FIVE_wire_values_all_load(self):
        """The closed set, so none of them is refused by the grader.

        **It was four and the set became five at ruling R-GS** (plan step
        ``bank_import:X-gd-2``), which added *ask me every time*.  This class
        is where a value the grader refuses is caught, so a member missing
        from it is a member with no grading at THIS tier -- delete
        ``ALWAYS_ASK`` from ``RuleAnswerField``'s accepted tuple and the class
        whose whole job is the wire stayed green.  Found by adversarial review
        2026-08-26.
        """
        loaded = MerchantRuleBatchSchema().load(rule_payload(_form(
            self._row(0, 11, "t:38")
            + self._row(1, 12, NOT_SAID)
            + self._row(2, 13, NEVER)
            + self._row(3, 14, NEW_ENVELOPE, name="Lowe's", category="4")
            + self._row(4, 15, ALWAYS_ASK),
        )))

        assert [item["answer"] for item in loaded["rules"]] == [
            38, NOT_SAID, NEVER, NEW_ENVELOPE, ALWAYS_ASK,
        ]
        assert loaded["rules"][3]["envelope_name"] == "Lowe's"
        assert loaded["rules"][3]["category_id"] == 4

    def test_an_answer_with_no_merchant_beside_it_names_nothing(self):
        """Unreachable from this screen -- the two fields render together.

        Dropped rather than refused, because a crafted body naming an answer
        for nobody has asked for nothing, and refusing would give a caller a
        way to fail a whole legitimate pass by appending one key.
        """
        payload = rule_payload(_form([("rule-9", NEVER)]))

        assert payload["rules"] == []

    def test_a_merchant_key_that_str_isdigit_ACCEPTS_does_not_raise(self):
        """The same trap the tick keys carry, on this section's keys.

        ``apply=%C2%B2`` was a 500 on the money door until plan step
        X-f6a-3c-2, because ``str.isdigit`` is true for 888 characters and
        ``int()`` refuses 128 of them.  These keys are sorted through the same
        ``order_token_key``, so the fix covers them -- and this is what says so.
        """
        payload = rule_payload(_form(
            [("rule-\N{SUPERSCRIPT TWO}", NEVER),
             ("rule_merchant-\N{SUPERSCRIPT TWO}", "11")],
        ))

        assert [item["merchant_id"] for item in payload["rules"]] == ["11"]

    def test_a_respelled_template_id_is_refused(self):
        """``t:007`` names no template, exactly as ``007`` names no envelope.

        A second, laxer reading of a row id on a screen that decides where
        money is filed is what plan step X-ae removed.
        """
        with pytest.raises(ValidationError):
            MerchantRuleBatchSchema().load(
                rule_payload(_form(self._row(0, 11, "t:007"))),
            )

    def test_an_undeclared_key_is_refused(self):
        """``unknown = RAISE``: nothing is swallowed on this payload either."""
        with pytest.raises(ValidationError):
            MerchantRuleBatchSchema().load(
                {"rules": [], "sneaky": 1},
            )

    def test_a_submission_over_the_CEILING_is_refused_and_says_so(self):
        """The rule ceiling had no test at either tier.

        Its sibling ``_MAX_BATCH_ITEMS`` is graded twice; this one could be
        raised to a billion with the suite green.  It is a SEPARATE bound on
        purpose -- that one paces money acts, each running a settle door, and
        this one paces small writes over a set the account's own lines bound --
        so it needs its own control rather than inheriting that one's.
        """
        rows = []
        for index in range(_MAX_RULE_ITEMS + 1):
            rows.extend(self._row(index, 1000 + index, NEVER))

        with pytest.raises(ValidationError) as caught:
            MerchantRuleBatchSchema().load(rule_payload(_form(rows)))

        assert "at most" in str(caught.value)

    def test_a_submission_AT_the_ceiling_still_loads(self):
        """The bound pinned from the other side, so it is not off by one."""
        rows = []
        for index in range(_MAX_RULE_ITEMS):
            rows.extend(self._row(index, 1000 + index, NEVER))

        loaded = MerchantRuleBatchSchema().load(rule_payload(_form(rows)))

        assert len(loaded["rules"]) == _MAX_RULE_ITEMS

    def test_a_merchant_that_is_NOT_A_ROW_ID_is_refused(self):
        """The key is an id now, and it is exactly as strict as every other.

        It was free text from a BANK, so this schema could refuse only a string
        longer than the column -- everything else was the service's to check.
        A merchant is a row as of plan step ``bank_import:X-gd-1``, so
        :class:`~app.schemas.validation._fields.RowId` refuses the whole family
        it refuses everywhere else, and a well-formed id that is not this
        account's is refused by
        ``fk_merchant_rules_merchant_account`` rather than by anyone
        remembering to look.
        """
        for spelled in ("\N{ARABIC-INDIC DIGIT SEVEN}", " 7 ", "+7", "007",
                        "-7", "0", "Amazon"):
            with pytest.raises(ValidationError):
                MerchantRuleBatchSchema().load(
                    rule_payload(_form(self._row(0, spelled, NEVER))),
                )
