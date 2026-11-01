// Appearance: colour palette and light/dark mode, plus the logo. Preferences live in localStorage (per browser).
// index.html applies palette + mode before first paint; this module owns the menu, the logo and the favicon.
import { el, icon } from "./ui.js";

export const PALETTES = [
  { id: "amber", name: "Amber", dots: ["#f7b733", "#d4780a", "#1d1b17"] },
  { id: "coral", name: "Coral", dots: ["#f09682", "#b2422c", "#1e1916"] },
  { id: "sage", name: "Sage", dots: ["#86a586", "#345035", "#171c15"] },
  { id: "tide", name: "Tide", dots: ["#6997c3", "#1e4b72", "#151b22"] },
];
// Palettes removed on 2026-10-27 → closest current one (same map in index.html's pre-paint script).
const RENAMED = { indigo: "tide", ocean: "tide", midnight: "tide", emerald: "sage" };
const DEFAULTS = { palette: "amber", mode: "system" };

function load(key) {
  let v;
  try { v = localStorage.getItem(`cueline.${key}`) || DEFAULTS[key]; } catch { return DEFAULTS[key]; }
  if (key === "palette") v = RENAMED[v] || (PALETTES.some(p => p.id === v) ? v : DEFAULTS.palette);
  return v;
}
function save(key, value) {
  try { localStorage.setItem(`cueline.${key}`, value); } catch { /* private mode: preference lasts this page only */ }
}

const prefs = { palette: load("palette"), mode: load("mode") };
const darkQuery = matchMedia("(prefers-color-scheme: dark)");

export function applyTheme() {
  const root = document.documentElement;
  root.dataset.palette = prefs.palette;
  const dark = prefs.mode === "dark" || (prefs.mode === "system" && darkQuery.matches);
  root.toggleAttribute("data-dark", dark);
  document.querySelectorAll(".brand-mark[data-live]").forEach(m => m.replaceChildren(logoNode()));
  updateFavicon();
}
darkQuery.addEventListener("change", () => { if (prefs.mode === "system") applyTheme(); });

// ---------- logo: a source in brackets with a point of light. Colours are theme tokens so it fits every palette. ----------
const LOGO_SVG = `
  <circle cx="20" cy="20" r="7.6" style="fill:var(--accent)" opacity=".2"/>
  <circle cx="20" cy="20" r="4.6" style="fill:var(--accent)"/>
  <path d="M14.5 7H9.5V33H14.5M25.5 7H30.5V33H25.5" style="fill:none;stroke:var(--primary)" stroke-width="4.2" stroke-linecap="round" stroke-linejoin="round"/>`;

export function logoNode() {
  const wrap = document.createElement("span");
  wrap.innerHTML = `<svg viewBox="0 0 40 40" fill="none" aria-hidden="true">${LOGO_SVG}</svg>`;
  return wrap.firstChild;
}

// Favicon = current logo with the CSS variables resolved to real colours (a data: URL can't read page CSS).
function updateFavicon() {
  const css = getComputedStyle(document.documentElement);
  let svg = logoNode().outerHTML.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ');
  svg = svg.replace(/var\(--([\w-]+)\)/g, (_, name) => css.getPropertyValue(`--${name}`).trim() || "#888");
  let link = document.querySelector('link[rel="icon"]');
  if (!link) { link = el("link", { rel: "icon" }); document.head.append(link); }
  link.href = "data:image/svg+xml," + encodeURIComponent(svg);
}

// ---------- appearance menu ----------
export function openAppearance(anchor) {
  document.querySelectorAll(".appearance").forEach(n => n.remove());
  const panel = el("div", { class: "appearance", role: "dialog", "aria-label": "Appearance" });
  const render = () => {
    panel.replaceChildren(
      el("section", {}, el("h4", {}, "Theme"),
        el("div", { class: "swatches" }, PALETTES.map(p => el("button", {
          class: `swatch${p.id === prefs.palette ? " active" : ""}`, "aria-pressed": String(p.id === prefs.palette),
          onclick: () => set("palette", p.id),
        }, el("span", { class: "dots" }, p.dots.map(c => el("i", { style: { background: c } }))), p.name)))),
      el("section", {}, el("h4", {}, "Mode"),
        el("div", { class: "segmented", role: "radiogroup" }, [["system", "monitor", "Auto"], ["light", "sun", "Light"], ["dark", "moon", "Dark"]].map(([m, ic, label]) =>
          el("button", { class: m === prefs.mode ? "active" : "", role: "radio", "aria-checked": String(m === prefs.mode), onclick: () => set("mode", m) }, icon(ic), label)))),
    );
  };
  const set = (key, value) => { prefs[key] = value; save(key, value); applyTheme(); render(); };
  render();
  document.body.append(panel);
  const r = anchor.getBoundingClientRect();
  panel.style.top = r.bottom + 8 + "px";
  panel.style.left = Math.max(8, Math.min(r.right - panel.offsetWidth, innerWidth - panel.offsetWidth - 8)) + "px";
  const away = e => {
    if (!panel.contains(e.target) && !anchor.contains(e.target)) { panel.remove(); document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); }
  };
  const esc = e => { if (e.key === "Escape") { panel.remove(); document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); anchor.focus(); } };
  setTimeout(() => { document.addEventListener("mousedown", away); document.addEventListener("keydown", esc); }, 0);
}
