"""What the RECONCILE page's form submits, and what the schemas make of it.

Plan step **bank_import:X-gj-1b**.  The readers under test move a browser's
body into the payloads :class:`~app.schemas.validation.statements
.StatementBatchSchema` and :class:`~app.schemas.validation.statements
.StatementMatchSchema` already grade -- so what is asserted here is the FORM's
own contract, and never a second set of money rules.

**The load-bearing case is the one that posts a whole rendered page with
nothing pressed.**  Ruling **R-HS** pre-fills a justified suggestion and then
says *an untouched card is not submitted*, and those two can only both hold
because the OK checkbox is what the reader keys on: a browser submits a
checkbox only when it is ticked.
"""

import pytest
from werkzeug.datastructures import MultiDict

from app.schemas.validation.statement_reconcile import (
    ReconcileRuleBatchSchema,
    reconcile_match_payload,
    reconcile_payload,
    reconcile_rules_payload,
)
from app.schemas.validation.statements import StatementBatchSchema


def _form(pairs):
    """Return *pairs* as the ``MultiDict`` a request carries."""
    return MultiDict(pairs)


class TestNothingIsAnActWithoutItsOwnOK:
    """Rulings **R-FP** and **R-HS**, at the grain of the reader."""

    def test_a_pre_filled_destination_alone_is_not_an_act(self):
        """The whole reason this reader exists beside ``batch_payload``.

        On the review queue the destination select IS the tick, so a rule's
        remembered destination may not arrive selected.  Here it does, and
        the OK checkbox is the tick -- so a card nobody pressed contributes
        nothing.
        """
        payload, silent = reconcile_payload(_form([
            ("verb-7", "add"),
            ("destination-7", "42"),
            ("envelope_name-7", "Lowe's"),
            ("category_id-7", "3"),
        ]))

        assert payload == {"matches": [], "creations": [], "incomes": []}
        assert silent == ()

    def test_an_OK_D_add_card_becomes_a_creation(self):
        """The same body with the box ticked."""
        payload, silent = reconcile_payload(_form([
            ("ok", "7"),
            ("verb-7", "add"),
            ("destination-7", "42"),
            ("envelope_name-7", "Lowe's"),
            ("category_id-7", "3"),
        ]))

        assert payload["creations"] == [{
            "line_id": "7", "destination": "42",
            "envelope_name": "Lowe's", "category_id": "3",
        }]
        assert silent == ()

    def test_an_OK_D_match_card_becomes_a_match_with_its_rows(self):
        """One card, one line id, and the rows its MATCH tab ticked."""
        payload, _ = reconcile_payload(_form([
            ("ok", "7"),
            ("verb-7", "match"),
            ("rows-7", "transaction:1:100.00:2"),
            ("rows-7", "transaction:2:2473.38:2"),
            ("residual-7", "0.04"),
        ]))

        assert payload["matches"] == [{
            "line_ids": ["7"],
            "rows": ["transaction:1:100.00:2", "transaction:2:2473.38:2"],
            "residual": "0.04",
        }]

    def test_an_OK_D_income_card_becomes_an_income(self):
        """Ruling **bank_import:R-GW**: one id and nothing to unpack."""
        payload, _ = reconcile_payload(_form([
            ("ok", "7"), ("verb-7", "add"), ("destination-7", "income"),
        ]))

        assert payload["incomes"] == [{"line_id": "7"}]
        assert payload["creations"] == []


class TestAPressIsNeverLeftUnanswered:
    """A card OK'd on nothing is REPORTED rather than dropped.

    It is reachable from a browser, so it may not be a pass-level refusal
    (**R-FZ(a)**, which would cost every other OK on the page) and it may not
    be silent.
    """

    def test_an_OK_with_no_destination_chosen_is_named(self):
        """The ADD tab's own button, pressed before the select was moved."""
        payload, silent = reconcile_payload(_form([
            ("ok", "7"), ("verb-7", "add"), ("destination-7", ""),
        ]))

        assert payload == {"matches": [], "creations": [], "incomes": []}
        assert silent == ("7",)

    def test_an_OK_on_a_verb_with_no_door_is_named(self):
        """TRANSFER and SKIP have no door in this build (**R-HW**).

        Their tabs render an explanation and no submitting control, so a body
        naming one is crafted or stale -- and it still gets an answer rather
        than silence.
        """
        payload, silent = reconcile_payload(_form([
            ("ok", "7"), ("verb-7", "transfer"),
        ]))

        assert payload == {"matches": [], "creations": [], "incomes": []}
        assert silent == ("7",)

    def test_an_OK_naming_no_verb_at_all_is_named(self):
        """A radio group always submits one, so this is a crafted body."""
        _, silent = reconcile_payload(_form([("ok", "7")]))

        assert silent == ("7",)


class TestTheOrderIsTheBankLinesAndNotTheFieldNames:
    """The receipt this order becomes is meant to read down the page."""

    def test_line_100_does_not_sort_between_10_and_2(self):
        """Lexical order put line 100 between 10 and 2 once already."""
        payload, _ = reconcile_payload(_form([
            ("ok", "100"), ("verb-100", "add"), ("destination-100", "1"),
            ("ok", "2"), ("verb-2", "add"), ("destination-2", "1"),
            ("ok", "10"), ("verb-10", "add"), ("destination-10", "1"),
        ]))

        assert [item["line_id"] for item in payload["creations"]] == [
            "2", "10", "100",
        ]

    def test_a_repeated_OK_names_its_line_once(self):
        """A crafted body cannot make one card into two acts."""
        payload, _ = reconcile_payload(_form([
            ("ok", "7"), ("ok", "7"),
            ("verb-7", "add"), ("destination-7", "1"),
        ]))

        assert len(payload["creations"]) == 1

    def test_a_non_numeric_OK_does_not_raise_inside_the_sort(self):
        """The same tolerance ``order_token_key`` gives every other reader."""
        payload, silent = reconcile_payload(_form([("ok", "²")]))

        assert payload == {"matches": [], "creations": [], "incomes": []}
        assert silent == ("²",)


class TestWhatTheReadersHandTheSchemasIsWhatTheSchemasGrade:
    """The readers validate nothing; the schemas refuse everything."""

    def test_a_forged_destination_is_the_schema_s_refusal(self):
        """A destination that names no row is graded where ids are."""
        payload, _ = reconcile_payload(_form([
            ("ok", "7"), ("verb-7", "add"), ("destination-7", "007"),
        ]))

        assert StatementBatchSchema().validate(payload)

    def test_a_well_formed_pass_loads(self):
        """The ordinary body, through the door's own grader."""
        payload, _ = reconcile_payload(_form([
            ("ok", "7"), ("verb-7", "add"), ("destination-7", "42"),
            ("envelope_name-7", "Lowe's"), ("category_id-7", "3"),
        ]))

        loaded = StatementBatchSchema().load(payload)

        assert loaded["creations"][0]["line_id"] == 7
        assert loaded["creations"][0]["destination"] == 42


class TestTheMatchReaderIsSharedByThePassAndThePanel:
    """Two callers, one reading of one body.

    The panel prices what is ticked and the pass applies it, so the figure on
    screen and the figure the door compares against are one derivation.
    """

    def test_an_untouched_consent_is_omitted_rather_than_sent_as_empty(self):
        """The schema's own ``load_default`` is the one statement of absence.

        The panel renders the box ``value=""`` and ``disabled`` in lockstep,
        so a browser cannot send one -- but a body that does must not 400 the
        whole pass over a field nobody filled in.
        """
        item = reconcile_match_payload(
            _form([("rows-7", "transaction:1:100.00:2"), ("residual-7", "")]),
            "7",
        )

        assert "residual" not in item

    def test_it_reads_the_SAME_fields_the_pass_does(self):
        """One reader, so the two cannot disagree about a card's rows."""
        body = _form([
            ("ok", "7"), ("verb-7", "match"),
            ("rows-7", "transaction:1:100.00:2"),
            ("residual-7", "0.04"),
        ])

        assert reconcile_payload(body)[0]["matches"] == [
            reconcile_match_payload(body, "7"),
        ]


class TestTheAlwaysTicksAreTheirOwnSubmission:
    """A second grader beside the money one, which is the shipped separation.

    Stating where a merchant's spending goes moves no money, so one schema
    carrying both would have to refuse a whole money pass over a preference.
    """

    def test_presence_is_the_tick_and_the_value_is_the_merchant(self):
        """The merchant is an ID and never the bank's own string."""
        assert reconcile_rules_payload(_form([
            ("always-7", "3"), ("always-2", "9"),
        ])) == {"rules": [
            {"line_id": "2", "merchant_id": "9"},
            {"line_id": "7", "merchant_id": "3"},
        ]}

    def test_an_untouched_page_asks_for_no_rule(self):
        """A checkbox nobody ticked is not in the body at all."""
        assert reconcile_rules_payload(_form([
            ("ok", "7"), ("verb-7", "add"), ("destination-7", "42"),
        ])) == {"rules": []}

    def test_a_forged_merchant_id_is_refused_by_the_schema(self):
        """Both ids are ``RowId``, so a respelling names no row."""
        payload = reconcile_rules_payload(_form([("always-7", "007")]))

        assert ReconcileRuleBatchSchema().validate(payload)

    def test_a_well_formed_tick_loads(self):
        """The ordinary body."""
        loaded = ReconcileRuleBatchSchema().load(
            reconcile_rules_payload(_form([("always-7", "3")])),
        )

        assert loaded["rules"] == [{"line_id": 7, "merchant_id": 3}]
