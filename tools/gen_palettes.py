"""Generate Cueline palettes 2–4 for src/cueline/static/css/themes.css (paste the output over them, then run check_contrast.py).
Tokens from OKLCH (lightness, chroma, hue) → sRGB hex, clipping chroma into gamut."""
import math, sys

def oklch_hex(L, C, h):
    for _ in range(60):
        a, b = C*math.cos(math.radians(h)), C*math.sin(math.radians(h))
        l_ = L + 0.3963377774*a + 0.2158037573*b; m_ = L - 0.1055613458*a - 0.0638541728*b; s_ = L - 0.0894841775*a - 1.2914855480*b
        l, m, s = l_**3, m_**3, s_**3
        rgb = [4.0767416621*l - 3.3077115913*m + 0.2309699292*s, -1.2684380046*l + 2.6097574011*m - 0.3413193965*s,
               -0.0041960863*l - 0.7034186147*m + 1.7076147010*s]
        if all(-1e-4 <= c <= 1+1e-4 for c in rgb): break
        C *= 0.95
    enc = lambda c: 12.92*c if c <= 0.0031308 else 1.055*c**(1/2.4) - 0.055
    return "#" + "".join(f"{round(min(1, max(0, enc(min(1, max(0, c)))))*255):02x}" for c in rgb)

# nh/nc = neutral hue/chroma, ah/ac = accent hue/chroma, aL = light accent lightness, pL/pk = light primary lightness/chroma factor
THEMES = {
    "coral": dict(nh=55, nc=0.006, ah=33, ac=0.15, aL=0.62, pL=0.53, pk=1.0),
    "sage":  dict(nh=135, nc=0.010, ah=145, ac=0.075, aL=0.55, pL=0.40, pk=0.75),
    "tide":  dict(nh=250, nc=0.010, ah=248, ac=0.11, aL=0.52, pL=0.40, pk=0.75),
}

def tokens(nh, nc, ah, ac, aL, pL, pk):
    N = lambda L, k=1: oklch_hex(L, nc*k, nh)
    A = lambda L, k=1: oklch_hex(L, ac*k, ah)
    light = dict(bg=N(.968), panel="#ffffff", raised=N(.988, .5), ink=N(.22, 1.6), **{"ink-2": N(.37, 1.6)}, muted=N(.52, 1.4),
                 faint=N(.66, 1.2), line=N(.925), **{"line-2": N(.875)}, chip=N(.952), **{"chip-hover": N(.922)},
                 primary=A(pL, pk), **{"on-primary": "#ffffff"}, accent=A(aL), **{"accent-2": A(aL+.14, .75)},
                 **{"accent-soft": A(.955, .17), "accent-ink": A(.38, .8)})
    dark = dict(bg=N(.165, .8), panel=N(.2, .8), raised=N(.232, .8), ink=N(.955, .5), **{"ink-2": N(.87, .6)}, muted=N(.72, .8),
                faint=N(.58, .8), line=N(.275, .8), **{"line-2": N(.325, .8)}, chip=N(.255, .8), **{"chip-hover": N(.295, .8)},
                primary=A(.84, .55), **{"on-primary": N(.165, .8)}, accent=A(.76, .8), **{"accent-2": A(.84, .6)},
                **{"accent-soft": A(.3, .35), "accent-ink": A(.87, .45)})
    return light, dark

ORDER = ["bg","panel","raised","ink","ink-2","muted","faint","line","line-2","chip","chip-hover","primary","on-primary","accent","accent-2","accent-soft","accent-ink"]
def fmt(t):
    parts = [f"--{k}: {t[k]};" for k in ORDER]
    return "  " + " ".join(parts[:6]) + "\n  " + " ".join(parts[6:11]) + "\n  " + " ".join(parts[11:15]) + "\n  " + " ".join(parts[15:]) + "\n"

if __name__ == "__main__":
    for name, p in THEMES.items():
        l, d = tokens(**p)
        print(f'/* {name} */\n:root[data-palette="{name}"] {{\n{fmt(l)}}}\n:root[data-palette="{name}"][data-dark] {{\n{fmt(d)}}}\n')
