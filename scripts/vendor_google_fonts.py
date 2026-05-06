"""
Shekel Budget App -- Vendor Google Fonts CSS + Variable-Font woff2 Files

Refreshes the locally vendored Inter and JetBrains Mono assets under
``app/static/vendor/fonts/``.  Idempotent: every run downloads the
upstream Google Fonts CSS, extracts the latin and latin-ext
``@font-face`` blocks, downloads each unique woff2 file, and emits a
self-hosted CSS file that references the local files.

Why this exists: the production CSP forbids ``https://fonts.googleapis.com``
and ``https://fonts.gstatic.com`` (audit finding F-037).  The web app
must serve every font asset from its own origin.  Re-running this script
is the supported way to bump the vendored font version; see
``app/static/vendor/VERSIONS.txt`` and ``docs/runbook.md`` "CDN vendor
refresh procedure".

Subsets dropped (not needed for an English-language US-focused app):
``cyrillic``, ``cyrillic-ext``, ``greek``, ``greek-ext``, ``vietnamese``.

Usage:
    python scripts/vendor_google_fonts.py

After running, recompute the SHA-384 hashes in
``app/static/vendor/VERSIONS.txt`` and commit the changed files.
"""

import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


# Google Fonts CSS endpoint, fully pinned to the Inter (3 weights) and
# JetBrains Mono (2 weights) shapes the app actually uses.  Bumping a
# weight or family means editing this URL.
FONTS_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Inter:wght@400;600;700"
    "&family=JetBrains+Mono:wght@400;700"
    "&display=swap"
)

# Modern UA so Google Fonts returns woff2 src= URLs.  An empty/older UA
# triggers the legacy woff fallback path which we do not want.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Subsets to keep.  Anything else is intentionally dropped.
WANTED_SUBSETS = frozenset({"latin", "latin-ext"})


def fetch_url(url: str) -> bytes:
    """Fetch ``url`` over HTTPS with a modern User-Agent.

    Args:
        url: Absolute HTTPS URL.

    Returns:
        Raw response body bytes.

    Raises:
        urllib.error.URLError: Network failure.
        urllib.error.HTTPError: Non-2xx response.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def main() -> int:
    """Refresh the vendored fonts. Returns a process exit code."""
    out_dir = (
        Path(__file__).resolve().parent.parent
        / "app" / "static" / "vendor" / "fonts"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {FONTS_URL}")
    try:
        css_bytes = fetch_url(FONTS_URL)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"ERROR: failed to fetch CSS: {exc}", file=sys.stderr)
        return 1
    src = css_bytes.decode("utf-8")

    # Each block is: comment label naming the subset, then @font-face { ... }.
    block_re = re.compile(
        r"/\*\s*([a-zA-Z\-]+)\s*\*/\s*(@font-face\s*\{[^}]*\})", re.DOTALL
    )
    font_url_re = re.compile(r"url\(([^)]+)\)")
    family_re = re.compile(r"font-family:\s*'([^']+)'")
    weight_re = re.compile(r"font-weight:\s*(\d+)")

    # Map remote URL -> deterministic local filename.  Google Fonts ships
    # variable fonts; the same woff2 file backs every weight for a given
    # subset, so we key by URL and emit one filename per (family, subset).
    url_to_local: dict[str, str] = {}
    rewritten_blocks: list[tuple[str, str, str, str]] = []

    for match in block_re.finditer(src):
        subset = match.group(1).strip().lower()
        block = match.group(2)
        if subset not in WANTED_SUBSETS:
            continue

        family_match = family_re.search(block)
        weight_match = weight_re.search(block)
        url_match = font_url_re.search(block)
        if not (family_match and weight_match and url_match):
            print(
                f"ERROR: could not parse @font-face block: {block!r}",
                file=sys.stderr,
            )
            return 1

        family = family_match.group(1).replace(" ", "").lower()
        weight = weight_match.group(1)
        remote_url = url_match.group(1).strip()
        if not remote_url.startswith("http"):
            print(
                f"ERROR: unexpected non-http url: {remote_url!r}",
                file=sys.stderr,
            )
            return 1

        if remote_url not in url_to_local:
            url_to_local[remote_url] = f"{family}-{subset}.woff2"
        local_name = url_to_local[remote_url]
        rewritten = block.replace(remote_url, local_name)
        rewritten_blocks.append((subset, family, weight, rewritten))

    # Download each unique woff2 once.
    for remote_url, local_name in url_to_local.items():
        target = out_dir / local_name
        print(f"Downloading {local_name}")
        try:
            data = fetch_url(remote_url)
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            print(
                f"ERROR: failed to download {remote_url}: {exc}",
                file=sys.stderr,
            )
            return 1
        target.write_bytes(data)

    # Emit the consolidated CSS.  Header is a frozen template so the
    # output is deterministic and reviewable in PRs.
    header = [
        "/* Self-hosted Google Fonts -- Inter, JetBrains Mono.",
        " * Subsets: latin, latin-ext only (English-language app).",
        " * Source: https://fonts.googleapis.com/css2",
        " * Subsets dropped: cyrillic, cyrillic-ext, greek, greek-ext,",
        " *                  vietnamese.",
        " * Each woff2 is a variable font covering all weights for its subset;",
        " * multiple @font-face declarations reference the same file with",
        " * different font-weight values so the browser picks the right",
        " * weight axis.",
        " * Generated by scripts/vendor_google_fonts.py -- do not hand-edit.",
        " * See app/static/vendor/VERSIONS.txt for the upstream pin.",
        " */",
        "",
    ]
    body: list[str] = []
    for subset, family, weight, block in rewritten_blocks:
        body.append(f"/* {family} {weight} {subset} */")
        body.append(block)
        body.append("")

    (out_dir / "fonts.css").write_text(
        "\n".join(header + body), encoding="utf-8"
    )
    print(
        f"\nWrote {out_dir / 'fonts.css'} "
        f"({len(rewritten_blocks)} blocks, {len(url_to_local)} woff2 files)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
