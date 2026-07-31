"""Static gate: the net-worth band vocabulary is ONE set in five languages.

Plan step X-t3, finding N-108.  A composition band is a cockpit CATEGORY, and
the same five keys are spelled out in five places that no compiler, linter or
import can hold together:

* **Python** -- ``_display._CATEGORY_ORDER``, from which
  ``_net_worth._COMPOSITION_BANDS`` is now derived (that half is a real
  deletion, not a gate);
* **JavaScript** -- ``net_worth_cockpit.js``: ``ASSET_BANDS`` +
  ``LIABILITY_BAND`` (which datasets get stacked), ``BAND_LABELS`` (the tooltip
  and dataset names), ``BAND_FILL_ALPHA`` (each band's fill opacity);
* **Jinja** -- ``savings/_cockpit.html``: ``category_labels`` and
  ``category_icons``, the microcopy for the legend and the group headers;
* **CSS** -- ``accounts.css``: the ``--nw-band-*`` color tokens the chart reads
  off ``:root`` and the ``.nw-legend__swatch--*`` classes the legend paints.

**Why a gate and not a refactor.**  The Python half was two lists and is now
one.  The other four cannot import it: the chart script is served to a browser,
the CSS token has to exist before a stylesheet is parsed, and the microcopy is
display text.  Nothing in the toolchain fails when they drift, and the drift is
NOT cosmetic -- the server sums ``net`` over EVERY band while the client stacks
only the bands it knows, so a band added to the Python vocabulary and nowhere
else ships a per-period float series the browser drops on the floor AND makes
the drawn stack stop reconciling to the drawn net line.  That is money missing
from a chart with nothing failing, which is finding N-100's class one language
over.

Reading a repository file by path and asserting on its text is an established
shape here (``test_template_no_money_arithmetic.py``,
``test_posting_ref_seed_parity.py``).  Each arm below is matched against a
DECLARATION -- an array literal, an object literal's keys, a custom-property
definition, a class selector -- never a bare mention, and every source is
COMMENT-STRIPPED before it is scanned, so a band named in a comment cannot
satisfy any arm (finding N-63's lesson: a static guard that greps for a name
cannot tell code from prose).

**Both of those properties were checked by exercising the helpers, not by
reading them** (plan step X-t5, out of X-t's adversarial review).  The first
draft matched the declaration but scanned its body RAW, so

.. code-block:: javascript

    var ASSET_BANDS = [
      "asset", "retirement", "investment"  // "other" dropped
    ];

satisfied the arm that exists to catch exactly that -- the client stacking three
bands' worth of a four-band sum, which is the "drawn stack stops reconciling to
the drawn net line" this file was written for.  Two of the five arms had the
hole; the comment strip below closes it for all five and
:class:`TestTheGateItself` plants that source and requires a failure.

**What these arms do NOT prove**, stated so it is not mistaken for more: they
check that each DECLARATION carries the right keys, never that the client
USES the declaration.  Rewiring ``readBandColors`` to iterate some other list
would leave every arm green.  That is N-63's lesson one level up, and the honest
answer to it is the behavioural render assertions in ``test_savings.py``, not a
static scan.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.savings_dashboard_service._display import LIABILITY_KEY
from app.services.savings_dashboard_service._net_worth import (
    _ASSET_BANDS,
    _COMPOSITION_BANDS,
)

JS_PATH = Path("app/static/js/net_worth_cockpit.js")
CSS_PATH = Path("app/static/css/accounts.css")
COCKPIT_PATH = Path("app/templates/savings/_cockpit.html")

# A quoted lower-case token: an array member, or a dict key in the Jinja
# literals (whose VALUES are display text, so they never match).
_QUOTED = re.compile(r"""['"]([a-z_]+)['"]""")
# ``key:`` at the start of a line or after a comma -- a JS object literal key,
# or a quoted Jinja one.
_LITERAL_KEY = re.compile(r"""(?:^|[,{])\s*['"]?([a-z_]+)['"]?\s*:""")


# Line comments (``//``), block comments (``/* */``) and Jinja comments
# (``{# #}``).  A CSS block comment is the same ``/* */``.
_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/|\{#.*?#\}", re.DOTALL)


def _strip_comments(source: str) -> str:
    """Blank every comment in *source*, keeping the rest byte-for-byte.

    A band name mentioned in a comment must not satisfy any arm of this gate
    (see the module docstring for the planted source that proved it could).
    Comments are replaced rather than removed so nothing else shifts.
    """
    return _COMMENTS.sub("", source)


def _read(path: Path) -> str:
    """Return a repository file's COMMENT-STRIPPED text, failing if it moved."""
    assert path.exists(), f"{path} not found -- did the file move?"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _literal_body(source: str, declaration: str, opener: str) -> str:
    """Return the text INSIDE the bracket literal that *declaration* opens.

    Scans from the first *opener* after *declaration* to its matching closer,
    counting depth, so a nested literal cannot truncate the body the way a
    ``[^}]*`` regex does.  Declarations are matched as literal text (never a
    formatted pattern), so a brace in the pattern cannot be misread as a format
    field -- the bug this helper replaced.

    Args:
        source: The file's text.
        declaration: The literal text that introduces the value (e.g.
            ``var ASSET_BANDS =``).
        opener: ``"["`` or ``"{"``.

    Returns:
        The characters between the brackets.
    """
    closer = {"[": "]", "{": "}"}[opener]
    start = source.find(declaration)
    assert start >= 0, f"declaration not found: {declaration!r}"
    start = source.index(opener, start + len(declaration))
    depth = 0
    for index in range(start, len(source)):
        if source[index] == opener:
            depth += 1
        elif source[index] == closer:
            depth -= 1
            if depth == 0:
                return source[start + 1:index]
    raise AssertionError(f"unclosed {opener} after {declaration!r}")


def _js_array(source: str, name: str) -> set[str]:
    """Return the quoted string members of a JS array literal declaration."""
    return set(_QUOTED.findall(_literal_body(source, f"var {name} =", "[")))


def _js_object_keys(source: str, name: str) -> set[str]:
    """Return the keys of a JS object literal declaration."""
    return set(
        _LITERAL_KEY.findall(_literal_body(source, f"var {name} =", "{"))
    )


def _jinja_dict_keys(source: str, name: str) -> set[str]:
    """Return the keys of a ``{% set name = {...} %}`` dict literal."""
    return set(
        _LITERAL_KEY.findall(_literal_body(source, f"{{% set {name} =", "{"))
    )


class TestBandVocabulary:
    """Every home of the band vocabulary carries the same five keys."""

    def test_the_python_bands_are_the_display_categories(self):
        """The composition bands ARE the cockpit categories, derived not copied.

        The one arm that is not about another language: ``_ASSET_BANDS`` is the
        category order minus the liability key, so the producer cannot sum a
        band the grid has no group for.  Pinned as a literal because the
        derivation is what a future edit would undo.

        Since plan step X-z (ruling R-CP) the liability key has ONE home --
        ``_display.LIABILITY_KEY``, which is ``_CATEGORY_KEYS[LIABILITY]`` --
        and ``_net_worth._LIABILITY_BAND`` is deleted rather than kept in step
        with it.  The literal below is now asserted against the mapping that
        assigns it, so a renamed key moves both together or fails here.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctCategoryEnum
        from app.services.savings_dashboard_service._display import (
            _CATEGORY_KEYS,
            _CATEGORY_ORDER,
        )
        assert set(_COMPOSITION_BANDS) == set(_CATEGORY_ORDER)
        assert _ASSET_BANDS == ("asset", "retirement", "investment", "other")
        assert LIABILITY_KEY == "liability"
        assert LIABILITY_KEY == _CATEGORY_KEYS[AcctCategoryEnum.LIABILITY]
        # No band appears twice, and the split is total: the liability band is
        # exactly the categories minus the asset side.
        assert len(set(_COMPOSITION_BANDS)) == len(_COMPOSITION_BANDS)
        assert set(_ASSET_BANDS) | {LIABILITY_KEY} == set(_COMPOSITION_BANDS)

    def test_the_chart_script_stacks_every_band_the_producer_sums(self):
        """``net_worth_cockpit.js`` knows exactly the producer's bands.

        The client stacks ``ASSET_BANDS`` and plots ``LIABILITY_BAND``; a band
        the producer sums into ``net`` but this array omits is drawn nowhere,
        so the stacked areas stop adding up to the net line the same chart
        draws.
        """
        source = _read(JS_PATH)
        assert _js_array(source, "ASSET_BANDS") == set(_ASSET_BANDS)
        assert f'var LIABILITY_BAND = "{LIABILITY_KEY}"' in source

    def test_the_chart_script_labels_and_shades_every_band(self):
        """Every band has a tooltip label and a fill opacity in the script.

        ``BAND_LABELS`` names the dataset (the tooltip row and the legend
        entry) and ``BAND_FILL_ALPHA`` gives it an opacity; a band missing from
        either renders as ``undefined`` text or an invisible area rather than
        failing.
        """
        source = _read(JS_PATH)
        assert _js_object_keys(source, "BAND_LABELS") == set(_COMPOSITION_BANDS)
        assert (
            _js_object_keys(source, "BAND_FILL_ALPHA")
            == set(_COMPOSITION_BANDS)
        )

    def test_the_cockpit_template_names_and_glyphs_every_band(self):
        """The cockpit's microcopy dicts cover every band.

        ``category_labels`` titles the legend entry AND the account-group card
        header; ``category_icons`` gives that header its glyph.  Both fall back
        rather than fail (``.get(band, band|title)``), so a missing band shows
        the raw key and a wallet icon on a live page.
        """
        source = _read(COCKPIT_PATH)
        assert (
            _jinja_dict_keys(source, "category_labels")
            == set(_COMPOSITION_BANDS)
        )
        assert (
            _jinja_dict_keys(source, "category_icons")
            == set(_COMPOSITION_BANDS)
        )

    def test_the_horizon_projects_every_band_it_publishes(self):
        """The Horizon's three band producers EXHAUST the composition.

        The gate's fifth home, and the one in Python (plan step X-t5, out of
        X-t's adversarial design review).  ``_horizon`` assembles the annual
        composition from three producers -- the /retirement engine's bands, the
        param-growth bands, and the liability band -- and a band belonging to
        none of them is published as a permanent ZERO series while the
        ``2 years`` range beside it (which keys off the category map, not a
        literal) reports the real money.  Nothing raises; the Horizon simply
        stops equalling the hero it documents itself as starting from.

        ``_PARAM_GROWTH_BANDS`` is derived for that reason, so this pins the
        partition rather than a list.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._horizon import (
            _ENGINE_BANDS,
            _PARAM_GROWTH_BANDS,
        )
        covered = set(_ENGINE_BANDS) | set(_PARAM_GROWTH_BANDS) | {
            LIABILITY_KEY,
        }
        assert covered == set(_COMPOSITION_BANDS)
        # And the three producers are disjoint: a band summed twice would
        # double-count into ``net``.
        assert not set(_ENGINE_BANDS) & set(_PARAM_GROWTH_BANDS)
        assert LIABILITY_KEY not in set(_ENGINE_BANDS) | set(
            _PARAM_GROWTH_BANDS,
        )

    def test_the_stylesheet_defines_a_token_and_a_swatch_per_band(self):
        """Every band has a ``--nw-band-*`` token and a legend swatch class.

        ``readBandColors`` resolves ``--nw-band-<band>`` off ``:root`` for each
        band it stacks; an undefined token resolves to an empty string, which
        the canvas paints BLACK rather than raising.  The legend's swatch class
        is the same color one selector away.
        """
        source = _read(CSS_PATH)
        tokens = set(re.findall(r"--nw-band-([a-z_]+)\s*:", source))
        swatches = set(
            re.findall(r"\.nw-legend__swatch--([a-z_]+)\s*\{", source)
        )
        assert tokens == set(_COMPOSITION_BANDS)
        assert swatches == set(_COMPOSITION_BANDS)


class TestTheGateItself:
    """The gate's own controls: a comment cannot satisfy it, a gap fails it.

    Section 7.3 of the balance plan -- "every guard gets a negative control
    that is shown to fire" -- applied to a guard whose subject is TEXT.  These
    exercise the extractors on planted sources rather than on the repository,
    so they fire on demand instead of only when someone breaks the real files.

    The first case is not hypothetical: it is the source X-t's adversarial
    review planted to prove the first draft of this file reported agreement it
    had not checked.
    """

    _DROPPED_BAND_IN_A_COMMENT = '''
      var ASSET_BANDS = [
        "asset", "retirement", "investment"  // "other" dropped
      ];
    '''

    def test_a_band_named_only_in_a_comment_does_not_count(self):
        """The array arm sees three bands, not four, in the planted source."""
        assert _js_array(_strip_comments(self._DROPPED_BAND_IN_A_COMMENT),
                         "ASSET_BANDS") == {
            "asset", "retirement", "investment",
        }
        # And without the strip it DOES count -- the hole, reproduced, so this
        # control cannot quietly stop discriminating.
        assert _js_array(self._DROPPED_BAND_IN_A_COMMENT, "ASSET_BANDS") == {
            "asset", "retirement", "investment", "other",
        }

    def test_a_jinja_comment_cannot_supply_a_key(self):
        """A band mentioned in a ``{# #}`` comment is not a dict key."""
        planted = (
            "{% set category_labels = {\n"
            "  'asset': 'Assets',\n"
            "  {# 'liability': 'Liabilities', #}\n"
            "} %}"
        )
        assert _jinja_dict_keys(_strip_comments(planted), "category_labels") == {
            "asset",
        }

    def test_a_partial_literal_cannot_pass_as_a_whole_one(self):
        """The body scan spans the WHOLE literal, nested braces included.

        The first draft used a ``[^}]*`` regex, which stopped at the first
        inner brace and graded a FRAGMENT -- reporting agreement over half a
        declaration.  The depth scan reaches the last key.

        A nested key is collected too (``a`` below), which is deliberate rather
        than tolerated: the real literals are flat, so if one ever gains a
        nested object this arm reports an EXTRA band and fails, where dropping
        the nesting silently would have reported agreement it had not checked.
        The failure direction is the point.
        """
        planted = 'var BAND_LABELS = {\n  asset: {a: 1},\n  liability: "L"\n};'
        assert _js_object_keys(planted, "BAND_LABELS") == {
            "asset", "a", "liability",
        }

    def test_a_moved_file_fails_loudly(self):
        """A missing source is an assertion, never a silently empty scan."""
        import pytest  # pylint: disable=import-outside-toplevel
        with pytest.raises(AssertionError, match="did the file move"):
            _read(Path("app/static/js/no_such_file.js"))
