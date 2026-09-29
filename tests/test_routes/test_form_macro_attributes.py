"""The shared form macros emit attributes the browser can PARSE, and nothing else.

Plan step ``pay_calendar:C20`` (ledger row **PC-515**).  ``_form_macros.html``
wrote its optional attributes as quoted STRINGS inside ``{{ }}`` --
``{{ ('placeholder="' ~ placeholder ~ '"') if placeholder }}``,
``{{ input_attrs }}`` -- and Flask autoescapes every expression in an
``.html`` template, so every caller received ``min=&#34;1&#34;``,
``step=&#34;0.01&#34;``, ``aria-invalid=&#34;true&#34;``: an unquoted
attribute whose value carries literal quote marks, which the browser reads
and discards.  Measured 2026-09-14 before the fix: no number box rendered
through the macros had a working ``min`` / ``max`` / ``step`` (a rate of
3.25 and a YTD figure with cents both failed the browser's step check), the
companion password fields carried no ``autocomplete="new-password"``, and no
ARIA reached a screen reader.

Two halves, because the remedy has two edges.  The macros are rendered
DIRECTLY so each attribute's syntax is graded on its own, including the
injection case the "mark it safe" remedy would have opened; then the pages
that RENDER them are fetched so the callers' converted ``input_attrs``
mappings are graded where an owner meets them, and a sweep asserts no
escaped attribute syntax survives inside any ``<input`` or ``<select`` tag.
The pages are the census of who renders a field macro (an adversarial review
of this step took it): ``settings/_general.html``, ``_companion.html``,
``_pay_periods_form.html``, ``_pay_periods_manage.html``,
``analytics/_tax_checkpoint_card.html`` and ``auth/register.html`` (its
select only); every other importer takes ``form_card`` alone, and a page
without a macro on it would pass the sweep while grading nothing.
"""
import re

import pytest

from app.services.pay_period_batch import PERIOD_BATCH_MAX, PERIOD_BATCH_MIN
from tests._test_helpers import make_salary_profile

#: The tell of an attribute whose SYNTAX went through autoescape: an equals
#: sign followed by the entity for a double quote.  A quote a user typed into
#: a value is escaped the same way but sits INSIDE the quotes (``value="a
#: &#34;b&#34;"``), so the tell is the ``=`` immediately before the entity.
ESCAPED_ATTRIBUTE = b"=&#34;"


@pytest.fixture(name="macros")
def _macros(app):
    """Return the macro module, rendered under an app context."""
    with app.app_context():
        yield app.jinja_env.get_template("_form_macros.html").module


def _tags(page: bytes, element: bytes) -> list[bytes]:
    """Return every ``<element ...>`` opening tag on *page*, multi-line included."""
    return re.findall(rb"<" + element + rb"\b[\s\S]*?>", page)


class TestEachAttributeIsParseable:
    """Rendered directly: the attribute syntax is the template's, the value escaped."""

    def test_render_field_writes_extra_attributes_placeholder_and_aria(self, macros):
        """min / max, placeholder, aria-describedby and aria-invalid, all quoted once."""
        html = str(macros.render_field(
            "age", "Age", type="number", placeholder="years",
            help_text="Whole years.", errors={"age": ["Too young."]},
            input_attrs={"min": 1, "max": 52},
        ))
        assert ' min="1"' in html and ' max="52"' in html
        assert ' placeholder="years"' in html
        assert ' aria-describedby="age-error age-help"' in html
        assert ' aria-invalid="true"' in html
        assert "&#34;" not in html

    def test_render_field_omits_what_is_not_given(self, macros):
        """No stray quotes, entities or attributes when the optional half is absent."""
        html = str(macros.render_field("name", "Name"))
        assert "placeholder" not in html
        assert "aria-" not in html
        assert "required" not in html
        assert "&#34;" not in html
        # ``extra_attrs(none)`` renders nothing, not the word None.
        assert "None" not in html

    def test_render_input_group_writes_step(self, macros):
        """A money box's ``step="0.01"`` reaches the browser as a step."""
        html = str(macros.render_input_group(
            "ytd_gross", "YTD Gross", prefix="$", step="0.01", required=True,
        ))
        assert ' step="0.01"' in html
        assert " required" in html
        assert "&#34;" not in html

    def test_render_select_writes_aria(self, macros):
        """The select carries the same accessibility pair on a refusal."""
        html = str(macros.render_select(
            "shift", "Shift", [(1, "None")], help_text="h",
            errors={"shift": ["Invalid payday adjustment."]},
        ))
        assert ' aria-describedby="shift-error shift-help"' in html
        assert ' aria-invalid="true"' in html
        assert "&#34;" not in html

    def test_a_hostile_value_is_escaped_inside_its_quotes(self, macros):
        """The XSS the "mark it safe" remedy would have opened, closed by construction.

        An ``input_attrs`` value or a placeholder that tries to close the
        attribute and open a script lands as text inside the attribute's
        quotes: the syntax is the template's, the value is autoescaped.
        """
        hostile = '"><script>alert(1)</script>'
        html = str(macros.render_field(
            "n", "N", placeholder=hostile, input_attrs={"data-x": hostile},
        ))
        assert "<script>" not in html
        assert html.count("&#34;&gt;&lt;script&gt;alert(1)&lt;/script&gt;") == 2


class TestTheCallersRenderParseableAttributes:
    """Through HTTP: the converted callers, where an owner meets them."""

    def test_the_general_settings_rate_box_carries_its_step_and_bounds(
        self, auth_client,
    ):
        """``default_inflation_rate`` accepts 3.25 again; the counts keep their floors."""
        page = auth_client.get("/settings?section=general").data
        rate = next(t for t in _tags(page, b"input") if b'id="default_inflation_rate"' in t)
        assert b' step="0.01"' in rate
        periods = next(t for t in _tags(page, b"input") if b'id="grid_default_periods"' in t)
        assert b' min="1"' in periods and b' max="52"' in periods

    def test_the_companion_password_boxes_carry_their_hints(self, auth_client):
        """``autocomplete="new-password"`` and the length bounds reach the browser."""
        page = auth_client.get("/settings?section=companions").data
        password = next(t for t in _tags(page, b"input") if b'name="password"' in t)
        assert b' autocomplete="new-password"' in password
        assert b' minlength="12"' in password and b' maxlength="72"' in password

    def test_the_pay_period_count_boxes_read_the_batch_bounds(self, auth_client):
        """Extend, add earlier, regenerate and reset bound the count by the batch policy, not a literal.

        Four boxes share ``id="num_periods"`` on this page, one per form --
        pre-existing, and reported by this step's review rather than fixed
        here: duplicate ids break ``<label for>`` and ``aria-describedby``
        targeting for a screen reader.
        """
        page = auth_client.get("/settings?section=pay-periods").data
        counts = [t for t in _tags(page, b"input") if b'id="num_periods"' in t]
        assert len(counts) == 4
        for tag in counts:
            assert f' min="{PERIOD_BATCH_MIN}"'.encode() in tag
            assert f' max="{PERIOD_BATCH_MAX}"'.encode() in tag

    def test_the_tax_checkpoint_money_boxes_accept_cents(
        self, app, db, auth_client, seed_user,
    ):
        """The YTD boxes carry ``step="0.01"``, so a figure with cents submits.

        The consequence the defect statement names: ``render_input_group``
        wrote ``step=&#34;0.01&#34;``, the browser fell back to a step of 1,
        and a pay stub's ``$1,234.56`` failed its own validity check.  The
        card renders only for an owner with a salary profile.
        """
        with app.app_context():
            # The tab computes a tax report before it renders the card, so
            # the owner needs an active profile (the shipped tax law prices
            # it) -- the taxes-tab route tests' own recipe.  An HTMX request,
            # because a direct GET renders the analytics shell and loads the
            # tab later.
            make_salary_profile(seed_user, db.session)
            db.session.commit()
            page = auth_client.get(
                "/analytics/taxes", headers={"HX-Request": "true"},
            ).data
            boxes = [
                t for t in _tags(page, b"input")
                if b'name="ytd_' in t and b'type="number"' in t
            ]
            assert len(boxes) == 5, "the checkpoint card did not render"
            for tag in boxes:
                assert b' step="0.01"' in tag
                assert ESCAPED_ATTRIBUTE not in tag

    @pytest.mark.parametrize("path", [
        "/settings?section=general",
        "/settings?section=pay-periods",
        "/settings?section=companions",
    ])
    def test_no_escaped_attribute_survives_inside_a_form_control(
        self, auth_client, path,
    ):
        """The sweep, over the settings pages that render the field macros."""
        page = auth_client.get(path).data
        controls = [
            t for element in (b"input", b"select") for t in _tags(page, element)
        ]
        assert len(controls) > 3, "the page rendered no controls to grade"
        offenders = [t for t in controls if ESCAPED_ATTRIBUTE in t]
        assert offenders == [], offenders

    def test_a_422_re_render_keeps_its_aria_parseable(self, bare_auth_client):
        """The refusal path, where ``aria-invalid`` is actually emitted."""
        response = bare_auth_client.post("/pay-periods/generate", data={
            "start_date": "not-a-date", "num_periods": "3",
        })
        assert response.status_code == 422
        start = next(t for t in _tags(response.data, b"input") if b'id="start_date"' in t)
        assert b' aria-invalid="true"' in start
        assert b' aria-describedby="start_date-error start_date-help"' in start
        assert ESCAPED_ATTRIBUTE not in start

    def test_registrations_convention_select_is_clean(self, client):
        """The public form's one macro control -- ``render_select`` -- without a session."""
        page = client.get("/register").data
        shift = next(t for t in _tags(page, b"select") if b'name="shift"' in t)
        assert ESCAPED_ATTRIBUTE not in shift
        assert b' required' in shift
