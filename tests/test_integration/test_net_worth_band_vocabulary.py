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
definition, a class selector -- never a bare mention, so a band named in a
comment cannot satisfy it (finding N-63's lesson: a static guard that greps for
a name cannot tell code from prose).
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.savings_dashboard_service._net_worth import (
    _ASSET_BANDS,
    _COMPOSITION_BANDS,
    _LIABILITY_BAND,
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


def _read(path: Path) -> str:
    """Return a repository file's text, failing loudly if it moved."""
    assert path.exists(), f"{path} not found -- did the file move?"
    return path.read_text(encoding="utf-8")


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
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._display import (
            _CATEGORY_ORDER,
        )
        assert set(_COMPOSITION_BANDS) == set(_CATEGORY_ORDER)
        assert _ASSET_BANDS == ("asset", "retirement", "investment", "other")
        assert _LIABILITY_BAND == "liability"
        # No band appears twice, and the split is total: the liability band is
        # exactly the categories minus the asset side.
        assert len(set(_COMPOSITION_BANDS)) == len(_COMPOSITION_BANDS)
        assert set(_ASSET_BANDS) | {_LIABILITY_BAND} == set(_COMPOSITION_BANDS)

    def test_the_chart_script_stacks_every_band_the_producer_sums(self):
        """``net_worth_cockpit.js`` knows exactly the producer's bands.

        The client stacks ``ASSET_BANDS`` and plots ``LIABILITY_BAND``; a band
        the producer sums into ``net`` but this array omits is drawn nowhere,
        so the stacked areas stop adding up to the net line the same chart
        draws.
        """
        source = _read(JS_PATH)
        assert _js_array(source, "ASSET_BANDS") == set(_ASSET_BANDS)
        assert f'var LIABILITY_BAND = "{_LIABILITY_BAND}"' in source

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
