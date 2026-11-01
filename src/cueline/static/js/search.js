// Search across every notebook: titles, summaries, source text and Studio outputs (GET /api/search).
import { api, el, $, icon, kindIcon, SOURCE_ICONS } from "./ui.js";

let timer = null;

export async function renderSearch(root, initial = "") {
  $("#topbar-title").replaceChildren(el("span", { class: "page-title" }, "Search"));
  document.title = "Search · Cueline";
  const input = el("input", { type: "search", class: "search-input", placeholder: "Search all notebooks, sources and Studio outputs",
    value: initial, "aria-label": "Search", autofocus: true, style: { width: "100%", fontSize: "1.05rem", padding: "12px 14px" } });
  const results = el("div", { class: "search-results", "aria-live": "polite" });
  root.replaceChildren(el("div", { class: "home" }, input, results));
  const run = async () => {
    const q = input.value.trim();
    history.replaceState(null, "", q ? `#/search/${encodeURIComponent(q)}` : "#/search");
    if (q.length < 2) { results.replaceChildren(el("p", { class: "muted" }, "Type at least 2 letters. All words must match.")); return; }
    let r;
    try { r = await api(`/api/search?q=${encodeURIComponent(q)}`); } catch (e) { results.replaceChildren(el("p", { class: "muted" }, e.message)); return; }
    if (input.value.trim() !== q) return;  // a newer search is on its way
    const total = r.notebooks.length + r.sources.length + r.studio.length;
    const row = (href, iconNode, title, sub, snippet) => el("a", { class: "card search-hit", href,
      style: { display: "flex", gap: "12px", padding: "12px 14px", margin: "8px 0", textDecoration: "none", color: "inherit" } },
      el("span", { style: { flexShrink: "0", marginTop: "2px" } }, iconNode),
      el("span", { style: { minWidth: "0" } }, el("strong", {}, title), el("div", { class: "muted small" }, sub),
        snippet ? el("div", { class: "small", style: { marginTop: "4px" } }, highlight(snippet, q)) : null));
    const section = (label, rows) => rows.length ? [el("h3", { style: { marginTop: "22px" } }, `${label} (${rows.length})`), ...rows] : [];
    results.replaceChildren(
      total ? "" : el("p", { class: "muted" }, `Nothing found for "${q}".`),
      ...section("Notebooks", r.notebooks.map(n => row(`#/n/${n.id}`, el("span", {}, n.emoji || "📓"), n.title, "Notebook", n.summary ? n.summary.slice(0, 200) : ""))),
      ...section("Sources", r.sources.map(s => row(`#/n/${s.notebook_id}`, icon(SOURCE_ICONS[s.kind] || "doc"), s.title,
        `in ${s.notebook_title}${s.loc ? " · at " + (s.loc.startsWith("p.") ? "page " + s.loc.slice(2) : s.loc) : ""}`, s.snippet))),
      ...section("Studio outputs", r.studio.map(i => row(`#/n/${i.notebook_id}`, kindIcon(i.kind), i.title, `in ${i.notebook_title}`, i.snippet))));
  };
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 250); });
  run();
  input.focus();
}

// Wrap matched words in <mark> (text nodes only, no HTML injection).
function highlight(text, q) {
  const words = q.toLowerCase().match(/\w+/g) || [];
  if (!words.length) return text;
  const re = new RegExp(`(${words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return text.split(re).map((part, i) => i % 2 ? el("mark", {}, part) : part);
}
