"""Check every theme in static/css/themes.css against WCAG AA. Run: python3 tools/check_contrast.py

Text pairs need 4.5:1; UI parts (focus ring, icons, placeholder hints) need 3:1. Exits 1 on any failure.
"""
import re
import sys
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "static" / "css" / "themes.css").read_text()

# (foreground, background, minimum ratio, what it is)
PAIRS = [
    ("ink", "bg", 4.5, "body text on page"), ("ink", "panel", 4.5, "body text on panel"),
    ("ink", "chip", 4.5, "text on chips/bubbles"), ("ink-2", "panel", 4.5, "secondary text"),
    ("muted", "panel", 4.5, "muted text on panel"), ("muted", "bg", 4.5, "muted text on page"),
    ("muted", "chip", 4.5, "muted text on chip"), ("faint", "panel", 3.0, "icons / placeholders"),
    ("on-primary", "primary", 4.5, "primary button label"), ("accent-ink", "accent-soft", 4.5, "callout text"),
    ("accent", "panel", 3.0, "focus ring / accent UI"),
]
SEMANTIC = [("ok", "ok-soft"), ("bad", "bad-soft"), ("info", "info-soft")]


def lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a: str, b: str) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def blocks():
    base = {}
    for selector, body in re.findall(r"([^{}]+)\{([^}]*)\}", CSS):
        tokens = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", body))
        sel = selector.strip().split("\n")[-1]
        if sel == ":root":
            base["light"] = tokens
        elif sel == ":root[data-dark]":
            base["dark"] = tokens
        elif "data-palette=" in sel:
            name = re.search(r'data-palette="(\w+)"', sel).group(1)
            mode = "dark" if "[data-dark]" in sel.split(",")[0] else "light"
            yield f"{name}/{mode}", {**base.get(mode, {}), **tokens}


def main() -> int:
    failures = 0
    for name, t in blocks():
        worst = []
        for fg, bg, need, what in PAIRS + [(f, b, 4.5, "status text") for f, b in SEMANTIC]:
            r = ratio(t[fg], t[bg])
            if r < need:
                failures += 1
                worst.append(f"FAIL {fg} on {bg} = {r:.2f} (< {need}) [{what}]")
        print(f"{name:16} {'ok' if not worst else ''}")
        for line in worst:
            print("   ", line)
    print("\nAll themes pass WCAG AA." if not failures else f"\n{failures} failing pair(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
