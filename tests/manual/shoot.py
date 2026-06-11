"""Design screenshot helper for the Fable 5 visual loop.

Captures a target (a standalone mockup file, or a running-app URL) across one
or more viewports and themes, writing PNGs that Claude Code can ``Read`` and
compare. Uses only the repo's existing Python Playwright + Chromium -- no new
dependencies and no Node.

For each (viewport, direction, theme) combination it loads the target, sets
``data-theme`` / ``data-bs-theme`` (and ``data-direction`` when given) on the
root element, then screenshots. ``data-bs-theme`` drives the real app's theme;
``data-direction`` drives a mockup's direction variants; setting an attribute
the page does not use is harmless.

Examples::

    # Standalone mockup, all directions x both themes (no app/auth needed):
    .venv/bin/python tests/manual/shoot.py docs/design/grid_directions_mockup.html \\
        --directions a,b,c --themes dark,light

    # Real app grid with a saved login session (run save_dev_session.py first):
    .venv/bin/python tests/manual/shoot.py http://127.0.0.1:5000/grid \\
        --themes dark,light --storage-state tests/manual/.dev_session_state.json
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from playwright.sync_api import sync_playwright

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "tests" / "manual" / "screenshots"

# name -> (width, height). Heights are nominal; full-page capture extends them.
VIEWPORTS: dict[str, tuple[int, int]] = {
    "desktop": (1440, 900),
    "mobile": (390, 844),
}


def _resolve_url(target: str) -> str:
    """Return a navigable URL for a file path or pass an http(s) URL through."""
    if target.startswith(("http://", "https://", "file://")):
        return target
    path = pathlib.Path(target)
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Target file does not exist: {path}")
    return path.as_uri()


def _slug(target: str) -> str:
    """Short label for output filenames, derived from the target."""
    tail = target.rstrip("/").split("/")[-1] or "page"
    return tail.replace(".html", "").replace(".", "_") or "page"


def _csv(value: str) -> list[str]:
    """Split a comma list, dropping blanks."""
    return [piece.strip() for piece in value.split(",") if piece.strip()]


def shoot(args: argparse.Namespace) -> int:
    """Capture every requested viewport/direction/theme combination."""
    url = _resolve_url(args.target)
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    viewports = _csv(args.viewports)
    unknown = [name for name in viewports if name not in VIEWPORTS]
    if unknown:
        raise ValueError(
            f"Unknown viewport(s) {unknown}; choose from {sorted(VIEWPORTS)}"
        )
    themes = _csv(args.themes)
    directions = _csv(args.directions) or [""]
    name = args.name or _slug(args.target)

    storage_state = args.storage_state or None
    if storage_state and not pathlib.Path(storage_state).exists():
        raise FileNotFoundError(
            f"--storage-state file not found: {storage_state}. "
            "Run tests/manual/save_dev_session.py first."
        )

    written: list[pathlib.Path] = []
    with sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            for vp_name in viewports:
                width, height = VIEWPORTS[vp_name]
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=2,
                    storage_state=storage_state,
                )
                page = context.new_page()
                page.goto(url, wait_until="networkidle")
                for direction in directions:
                    for theme in themes:
                        page.evaluate(
                            """([theme, direction]) => {
                                const r = document.documentElement;
                                r.setAttribute('data-theme', theme);
                                r.setAttribute('data-bs-theme', theme);
                                if (direction) r.setAttribute('data-direction', direction);
                            }""",
                            [theme, direction],
                        )
                        page.wait_for_timeout(180)
                        parts = [name, vp_name]
                        if direction:
                            parts.append(direction)
                        parts.append(theme)
                        dest = out_dir / ("__".join(parts) + ".png")
                        page.screenshot(path=str(dest), full_page=True)
                        written.append(dest)
                context.close()
        finally:
            browser.close()

    print(f"Wrote {len(written)} screenshot(s) to {out_dir}:")
    for dest in written:
        shown = dest.relative_to(REPO_ROOT) if dest.is_relative_to(REPO_ROOT) else dest
        print(f"  {shown}")
    return 0


def main() -> int:
    """Parse arguments and run the capture."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", help="A repo-relative/absolute file path or an http(s) URL.")
    parser.add_argument("--themes", default="dark,light", help="Comma list, default 'dark,light'.")
    parser.add_argument(
        "--directions", default="",
        help="Comma list of mockup direction variants (sets data-direction); omit for the real app.",
    )
    parser.add_argument(
        "--viewports", default="desktop,mobile",
        help=f"Comma list from {sorted(VIEWPORTS)}; default 'desktop,mobile'.",
    )
    parser.add_argument("--storage-state", default="", help="Playwright storage_state JSON for an authed app target.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output directory for PNGs.")
    parser.add_argument("--name", default="", help="Filename label; defaults to the target's basename.")
    args = parser.parse_args()
    try:
        return shoot(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
