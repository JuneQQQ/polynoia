#!/usr/bin/env python3
"""Generate Polynoia's static brand-icon SVG assets.

Source of truth for the *art* is the design handoff `icon-art-v2.jsx`
(platform-aware PNIcon). This script ports that art to standalone, self-
contained SVG files for the consumers that can't run React:

  - the web favicon (served by Vite from apps/web/public)
  - the desktop (macOS) + mobile (iOS) app-icon masters, for the future
    Tauri / React-Native builds (apps/desktop, apps/mobile — P1+)
  - the in-app brand logo master

Platform → concept mapping (a product decision — see assets/brand/README.md):

    web favicon        → mono   (字标 P)        flat orange tile + cream P
    desktop app icon   → triad  (三色交叠)       macOS squircle, cream + sheen
    mobile app icon    → triad  (三色交叠)       iOS squircle, cream + sheen
    in-app brand logo  → triad  (三色交叠)       flat cream tile (sidebar)

The live React component `apps/web/src/components/BrandIcon.tsx` renders the
SAME art at runtime; this script only emits the static files. Re-run after
changing the palette or geometry:  python3 scripts/gen_brand_icons.py
"""
from __future__ import annotations

import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Palette — kept in lockstep with icon-art-v2.jsx `PC`.
PC = {
    "cream": "#f3ede1", "cream2": "#fbf6ec",
    "dark2": "#25201c", "darkInk": "#15110e",
    "orange": "#e96a3c", "orangeLt": "#f0a07e", "orangeDk": "#d4552c",
    "teal": "#3aab8d", "violet": "#8a64d8",
}


def squircle(cx: float, cy: float, r: float, n: float, steps: int = 88) -> str:
    """Superellipse path, identical sampling to icon-art-v2.jsx."""
    pts: list[str] = []
    for i in range(steps + 1):
        t = (i / steps) * 2 * math.pi
        ct, st = math.cos(t), math.sin(t)
        x = cx + r * math.copysign(abs(ct) ** (2 / n), ct)
        y = cy + r * math.copysign(abs(st) ** (2 / n), st)
        pts.append(f"{x:.2f} {y:.2f}")
    return "M" + "L".join(pts) + "Z"


SQ_MAC = squircle(50, 50, 49.4, 4.2)   # macOS Big Sur
SQ_IOS = squircle(50, 50, 49.6, 5.2)   # iOS continuous corner, squarer

# Shared gradient + sheen defs (only the squircle masters need them).
_DEFS = (
    '<defs>'
    f'<linearGradient id="c" x1="0" y1="0" x2="0" y2="1">'
    f'<stop offset="0" stop-color="#fffdf9"/><stop offset="1" stop-color="{PC["cream2"]}"/></linearGradient>'
    '<linearGradient id="s" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#fff" stop-opacity="0.26"/>'
    '<stop offset="0.55" stop-color="#fff" stop-opacity="0"/></linearGradient>'
    '</defs>'
)


def _triad(scale: float = 1.0) -> str:
    """The 三色交叠 glyph: three multiply-blended agent-color discs."""
    cs = [(50, 39, PC["orange"]), (38, 60, PC["teal"]), (62, 60, PC["violet"])]
    discs = "".join(
        f'<circle cx="{x}" cy="{y}" r="21" fill="{c}" '
        f'fill-opacity="0.86" style="mix-blend-mode:multiply"/>'
        for x, y, c in cs
    )
    g = f'<g style="isolation:isolate">{discs}</g>'
    if scale != 1.0:
        g = f'<g transform="translate(50 50) scale({scale}) translate(-50 -50)">{g}</g>'
    return g


def _svg(body: str, *, title: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        f'width="100" height="100" role="img" aria-label="{title}">'
        f"<title>{title}</title>{body}</svg>\n"
    )


def favicon_web_mono() -> str:
    """Web favicon — flat orange tile + cream P (legible at 16px)."""
    body = (
        f'<rect x="0.6" y="0.6" width="98.8" height="98.8" rx="22" ry="22" fill="{PC["orange"]}"/>'
        # x=49.4 / y=48 (not 50/53.5) optically centers the "P" — the glyph's
        # ink sits low-left otherwise (stem-heavy, empty lower-right). No
        # letter-spacing on a single glyph (it shifted the anchor right).
        '<text x="49.4" y="48" text-anchor="middle" dominant-baseline="central" '
        f'font-family="Inter, system-ui, sans-serif" font-weight="800" font-size="62" '
        f'fill="{PC["cream"]}">P</text>'
    )
    return _svg(body, title="Polynoia")


def icon_squircle_triad(path: str, *, ring: bool, glyph_scale: float, title: str) -> str:
    body = _DEFS
    body += f'<path d="{path}" fill="url(#c)"/>'         # cream tile
    body += _triad(glyph_scale)                          # three discs
    body += f'<path d="{path}" fill="url(#s)"/>'         # top sheen
    if ring:                                             # macOS hairline ring
        body += f'<path d="{path}" fill="none" stroke="rgba(255,255,255,0.10)" stroke-width="1"/>'
    return _svg(body, title=title)


def logo_web_triad() -> str:
    """In-app brand logo — flat cream rounded tile + triad, faint border."""
    rect = '<rect x="0.6" y="0.6" width="98.8" height="98.8" rx="22" ry="22"'
    body = (
        f'{rect} fill="{PC["cream2"]}"/>'
        + _triad()
        + f'{rect} fill="none" stroke="#e3dac6" stroke-width="1.2"/>'
    )
    return _svg(body, title="Polynoia")


def main() -> None:
    targets = {
        ROOT / "apps/web/public/favicon.svg": favicon_web_mono(),
        ROOT / "assets/brand/favicon-web-mono.svg": favicon_web_mono(),
        ROOT / "assets/brand/icon-desktop.svg": icon_squircle_triad(
            SQ_MAC, ring=True, glyph_scale=1.0, title="Polynoia"),
        ROOT / "assets/brand/icon-mobile.svg": icon_squircle_triad(
            SQ_IOS, ring=False, glyph_scale=1.08, title="Polynoia"),
        ROOT / "assets/brand/logo.svg": logo_web_triad(),
    }
    for path, content in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"  wrote {path.relative_to(ROOT)}  ({len(content)} bytes)")


if __name__ == "__main__":
    main()
