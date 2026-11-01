// Shared UI toolkit: API client, DOM helper, icons, markdown + citations, modal, menu, toast.

// App-wide state filled by main.js from /api/status.
export const appState = { studioTypes: [], ai: null, hermes: false };

export async function api(path, opts = {}) {
  const headers = { "X-Cueline": "1", ...(opts.headers || {}) };
  if (opts.body && !(opts.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    opts = { ...opts, body: JSON.stringify(opts.body) };
  }
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 204) return null;
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const data = isJson ? await res.json() : await res.text();
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

// Streams a POST that answers with Server-Sent Events; calls onEvent(name, data) per event.
export async function streamSSE(path, body, onEvent) {
  const res = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Cueline": "1" }, body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const ev = /^event: (.*)$/m.exec(chunk), data = /^data: (.*)$/m.exec(chunk);
      if (ev && data) onEvent(ev[1], JSON.parse(data[1]));
    }
  }
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export const $ = (sel, root = document) => root.querySelector(sel);

// ---------- icons (Lucide-style, 24x24, stroke) ----------
const P = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  back: '<path d="m15 18-6-6 6-6"/>',
  more: '<circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>',
  panel: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/>',
  panelR: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18"/>',
  youtube: '<path d="M2.5 17a24.1 24.1 0 0 1 0-10 2 2 0 0 1 1.4-1.4 49.6 49.6 0 0 1 16.2 0A2 2 0 0 1 21.5 7a24.1 24.1 0 0 1 0 10 2 2 0 0 1-1.4 1.4 49.6 49.6 0 0 1-16.2 0A2 2 0 0 1 2.5 17"/><path d="m10 15 5-3-5-3z"/>',
  pdf: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M9 13h1.5a1.5 1.5 0 0 1 0 3H9v-3zM9 16v2"/>',
  doc: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4M10 9H8M16 13H8M16 17H8"/>',
  text: '<path d="M17 6.1H3M21 12.1H3M15.1 18H3"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  trash: '<path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
  external: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  send: '<path d="m5 12 7-7 7 7M12 19V5"/>',
  sparkles: '<path d="M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.13-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.13a.5.5 0 0 1 .96 0L14.06 8.5A2 2 0 0 0 15.5 9.94l6.13 1.58a.5.5 0 0 1 0 .96L15.5 14.06a2 2 0 0 0-1.44 1.44l-1.58 6.13a.5.5 0 0 1-.96 0z"/><path d="M20 3v4M22 5h-4"/>',
  copy: '<rect x="8" y="8" width="14" height="14" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  note: '<path d="M15.5 3H5a2 2 0 0 0-2 2v14c0 1.1.9 2 2 2h14a2 2 0 0 0 2-2V8.5L15.5 3Z"/><path d="M15 3v6h6"/>',
  pencil: '<path d="M21.17 6.81a1 1 0 0 0-3.99-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.62l4.35-1.32a2 2 0 0 0 .83-.5zM15 5l4 4"/>',
  mindmap: '<rect x="16" y="16" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="9" y="2" width="6" height="6" rx="1"/><path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3M12 12V8"/>',
  cards: '<rect x="2" y="6" width="16" height="14" rx="2"/><path d="M6 2h14a2 2 0 0 1 2 2v12"/>',
  quiz: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3M12 17h.01"/>',
  book: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2zM22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
  briefing: '<path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2M18 14h-8M15 18h-5M10 6h8v4h-8z"/>',
  checklist: '<path d="m3 17 2 2 4-4M3 7l2 2 4-4M13 6h8M13 12h8M13 18h8"/>',
  layout: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/>',
  infographic: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 17v-4M12 17V8M17 17v-6M7 7h3"/>',
  calculator: '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 6h8M16 14v4M16 10h.01M12 10h.01M8 10h.01M12 14h.01M8 14h.01M12 18h.01M8 18h.01"/>',
  slides: '<path d="M2 3h20M21 3v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V3M7 21l5-5 5 5"/>',
  wand: '<path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72M14 7l3 3M5 6v4M19 14v4M10 2v2M7 8H3M21 16h-4M11 3H9"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8M21 3v5h-5M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16M8 16H3v5"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  library: '<path d="m16 6 4 14M12 6v14M8 8v12M4 4v16"/>',
  maximize: '<path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"/>',
  chat: '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
  shuffle: '<path d="M2 18h1.4c1.3 0 2.5-.6 3.3-1.7l6.1-8.6c.7-1.1 2-1.7 3.3-1.7H22M18 2l4 4-4 4M2 6h1.9c1.5 0 2.9.9 3.6 2.2M22 18h-5.9c-1.3 0-2.6-.7-3.3-1.8l-.5-.8M18 14l4 4-4 4"/>',
  install: '<path d="M12 2v13M7 10l5 5 5-5M5 21h14"/>',
  hermes: '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
  play: '<path d="m6 3 14 9-14 9z"/>',
  left: '<path d="m15 18-6-6 6-6"/>',
  right: '<path d="m9 18 6-6-6-6"/>',
  fit: '<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>',
  palette: '<circle cx="13.5" cy="6.5" r=".5" fill="currentColor"/><circle cx="17.5" cy="10.5" r=".5" fill="currentColor"/><circle cx="8.5" cy="7.5" r=".5" fill="currentColor"/><circle cx="6.5" cy="12.5" r=".5" fill="currentColor"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.93 0 1.65-.75 1.65-1.69 0-.44-.18-.84-.44-1.13-.29-.29-.44-.65-.44-1.13a1.64 1.64 0 0 1 1.67-1.67h2c3.05 0 5.56-2.5 5.56-5.55C21.97 6.01 17.46 2 12 2z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  monitor: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
  folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  settings: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  rss: '<path d="M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
  inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  mic: '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v3"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20M2 12h20"/>',
  megaphone: '<path d="m3 11 18-5v12L3 14v-3z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/>',
  bookmark: '<path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/>',
  eyeOff: '<path d="M9.88 9.88a3 3 0 1 0 4.24 4.24M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68M6.61 6.61A13.53 13.53 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61M2 2l20 20"/>',
  video: '<path d="m16 13 5.22 3.48a.5.5 0 0 0 .78-.42V7.94a.5.5 0 0 0-.76-.42L16 11"/><rect x="2" y="6" width="14" height="12" rx="2"/>',
  phone: '<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/>',
  tag: '<path d="M12.59 2.59A2 2 0 0 0 11.17 2H4a2 2 0 0 0-2 2v7.17a2 2 0 0 0 .59 1.41l8.71 8.71a2.43 2.43 0 0 0 3.42 0l6.58-6.58a2.43 2.43 0 0 0 0-3.42z"/><circle cx="7.5" cy="7.5" r=".5" fill="currentColor"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
};

export function icon(name, cls = "") {
  const span = document.createElement("span");
  span.innerHTML = `<svg class="${cls}" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[name] || P.sparkles}</svg>`;
  return span.firstChild;
}

// Visual identity per Studio type (icon + color), shared by tiles, list rows and viewers.
export const KINDS = {
  mindmap: { icon: "mindmap", color: "var(--c-violet)" },
  flashcards: { icon: "cards", color: "var(--c-orange)" },
  quiz: { icon: "quiz", color: "var(--c-blue)" },
  study_guide: { icon: "book", color: "var(--c-teal)" },
  briefing: { icon: "briefing", color: "var(--c-indigo)" },
  blog_post: { icon: "text", color: "var(--c-blue)" },
  report: { icon: "briefing", color: "var(--c-indigo)" },
  action_plan: { icon: "checklist", color: "var(--c-green)" },
  landing_page: { icon: "layout", color: "var(--c-pink)" },
  content: { icon: "pencil", color: "var(--c-orange)" },
  infographic: { icon: "infographic", color: "var(--c-teal)" },
  tool: { icon: "calculator", color: "var(--c-amber)" },
  slides: { icon: "slides", color: "var(--c-red)" },
  podcast: { icon: "mic", color: "var(--c-indigo)" },
  skill: { icon: "wand", color: "var(--c-gold)" },
  brand_kit: { icon: "megaphone", color: "var(--c-pink)" },
  note: { icon: "note", color: "var(--muted)" },
};
export function kindIcon(kind) {
  const k = KINDS[kind] || KINDS.note;
  const svg = icon(k.icon);
  svg.style.color = k.color;
  return svg;
}
export const SOURCE_ICONS = { youtube: "youtube", pdf: "pdf", doc: "doc", text: "text", web: "globe", podcast: "mic", audio: "mic", notebook: "book" };

// ---------- markdown with citation chips ----------
const LOC = String.raw`(?:\s+(?:p\.\s?\d+|\d{1,2}:\d{2}(?::\d{2})?))?`;
const CITE_RE = new RegExp(String.raw`\[(S\d+${LOC}(?:\s*[,;]\s*S\d+${LOC})*)\]`, "g");
const HAS_CITE = new RegExp(CITE_RE.source);  // non-global: .test() must not carry lastIndex state
const ONE_RE = new RegExp(String.raw`S(\d+)(?:\s+(p\.\s?\d+|\d{1,2}:\d{2}(?::\d{2})?))?`, "g");

export function renderMarkdown(text, onCite) {
  const html = window.DOMPurify.sanitize(window.marked.parse(text || "", { gfm: true, breaks: false }));
  const box = el("div", { class: "md", html });
  box.querySelectorAll("a").forEach(a => { a.target = "_blank"; a.rel = "noopener noreferrer"; });
  if (onCite) linkCitations(box, onCite);
  return box;
}

// Citation check results (server: citecheck.py). onCite.bad = Map "n|loc" -> reason marks those chips as unverified.
export function badCites(checks) {
  return new Map(((checks && checks.bad) || []).map(b => [b.key, b.reason]));
}

// Large notebooks (retrieval.py): say that the AI read selected passages, not everything ("" for full reads).
export function coverageNote(context, what = "answer") {
  if (!context || context.mode !== "retrieval") return "";
  const pct = Math.max(1, Math.round(100 * context.chars_used / Math.max(1, context.chars_total)));
  const text = context.passages_matched && context.weak
    ? `Large notebook, weak match: the most specific words of the request weren't found, so the ${context.passages_used} passages used may not answer it. Check the citations, or rephrase with words the sources use.`
    : context.passages_matched
    ? `Large notebook: this ${what} is based on ${context.passages_used} passages (about ${pct}% of the text) that best matched the request${context.notes_used ? `, plus key-point notes of ${context.notes_used} of ${context.sources_total} sources` : ` from ${context.sources_used} of ${context.sources_total} sources`}, not on every word of every source.`
    : `Large notebook: no passage matched the request, so the AI only saw the opening of ${context.sources_used} of ${context.sources_total} sources. Try more specific words, or turn off unrelated sources.`;
  return el("div", { class: `coverage-note${context.weak ? " weak" : ""}`, title: context.terms?.length ? `Searched for: ${context.terms.join(", ")}${context.alternates?.length ? `\nAlso searched: ${context.alternates.join(" · ")}` : ""}` : "" }, "🔎 " + text);
}

// Studio review label: what still needs the user before an output is used (null = not checked / nothing to say).
export function reviewState(checks) {
  if (!checks) return null;
  const why = [];
  if (checks.placeholders) why.push(`${checks.placeholders} placeholder${checks.placeholders > 1 ? "s" : ""} to fill`);
  if (checks.bad?.length) why.push(`${checks.bad.length} unverified citation${checks.bad.length > 1 ? "s" : ""}`);
  if (checks.quotes?.length) why.push(`${checks.quotes.length} unmatched quote${checks.quotes.length > 1 ? "s" : ""}`);
  return why.length ? { ready: false, label: "Needs review", why: why.join(", ") }
    : { ready: true, label: "Ready", why: "No placeholders, unverified citations or unmatched quotes found. Still read it before you publish." };
}

// One line under an answer / banner on a Studio output when something didn't check out ("" when all is fine).
export function citeWarning(checks, cls = "cite-warn") {
  const bad = (checks && checks.bad) || [], quotes = (checks && checks.quotes) || [];
  if (!bad.length && !quotes.length && !(checks?.placeholders && cls.includes("banner"))) return "";
  const parts = [];
  if (checks.placeholders && cls.includes("banner")) parts.push(`${checks.placeholders} placeholder${checks.placeholders > 1 ? "s" : ""} to fill`);
  if (bad.length) parts.push(`${bad.length} citation${bad.length > 1 ? "s" : ""} couldn't be verified`);
  if (quotes.length) parts.push(`${quotes.length} quote${quotes.length > 1 ? "s" : ""} not found word for word in the sources`);
  const details = [...bad.map(b => `${b.label}: ${b.reason}`), ...quotes.map(q => `“${q.length > 90 ? q.slice(0, 90) + "…" : q}”`)];
  return el("details", { class: cls },
    el("summary", {}, "⚠ " + parts.join(" · ") + ". Check these before you use them."),
    el("ul", {}, details.map(d => el("li", {}, d))));
}

function linkCitations(root, onCite) {
  const bad = onCite.bad || new Map();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: n => (n.parentElement.closest("code, pre") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  const nodes = [];
  while (walker.nextNode()) if (HAS_CITE.test(walker.currentNode.nodeValue)) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const frag = document.createDocumentFragment();
    let last = 0;
    const text = node.nodeValue;
    text.replace(CITE_RE, (match, inner, offset) => {
      frag.append(text.slice(last, offset));
      for (const m of inner.matchAll(ONE_RE)) {
        const idx = Number(m[1]), loc = m[2] ? m[2].replace(/\s/g, "") : "";
        const why = bad.get(`${idx}|${loc}`);
        frag.append(el("button", {
          class: why ? "cite bad" : "cite", title: why ? `Unverified: ${why}` : `Source ${idx}${loc ? " · " + loc : ""}`,
          onclick: e => { e.stopPropagation(); onCite(idx, loc); },
        }, String(idx)));
      }
      last = offset + match.length;
    });
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }
}

// ---------- toast / modal / menu ----------
export function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), ms);
}

export function modal({ title, iconNode = null, body, foot = null, wide = false, onClose = null, bodyClass = "" }) {
  const root = $("#modal-root");
  const close = () => {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
    if (onClose) onClose();
  };
  const onKey = e => { if (e.key === "Escape" && root.lastChild === overlay) close(); };
  const head = el("div", { class: "modal-head" },
    iconNode ? el("span", { class: "head-icon" }, iconNode) : null,
    el("h2", {}, title),
  );
  const headActions = el("div", { class: "row-actions", style: { display: "flex", gap: "4px" } });
  head.append(headActions, el("button", { class: "icon-btn", "aria-label": "Close", onclick: close }, icon("x")));
  const bodyEl = el("div", { class: `modal-body ${bodyClass}` }, body);
  const box = el("div", { class: `modal${wide ? " wide" : ""}`, role: "dialog", "aria-modal": "true", "aria-label": title },
    head, bodyEl, foot ? el("div", { class: "modal-foot" }, foot) : null);
  const overlay = el("div", { class: "overlay", onmousedown: e => { if (e.target === overlay) close(); } }, box);
  root.append(overlay);
  document.addEventListener("keydown", onKey);
  setTimeout(() => (box.querySelector("[autofocus]") || box.querySelector("input, textarea"))?.focus(), 30);
  return { close, box, body: bodyEl, headActions };
}

// On/off switch with a label and an optional hint line (a checkbox underneath, so it's keyboard + screen-reader friendly).
export function switchRow(label, hint, checked, onchange) {
  const input = el("input", { type: "checkbox", role: "switch" });
  input.checked = !!checked;
  input.addEventListener("change", () => onchange(input.checked));
  return el("label", { class: "switch-row" }, input, el("span", { class: "switch" }),
    el("span", { class: "txt" }, el("strong", {}, label), hint ? el("small", {}, hint) : ""));
}

export function confirmDialog(title, message, okLabel = "Delete", danger = true) {
  return new Promise(resolve => {
    let done = false;
    const finish = v => { if (!done) { done = true; resolve(v); m.close(); } };
    const m = modal({
      title, body: el("p", { class: "muted" }, message),
      foot: [el("button", { class: "btn ghost", onclick: () => finish(false) }, "Cancel"),
        el("button", { class: `btn primary${danger ? " danger-fill" : ""}`, autofocus: true, onclick: () => finish(true) }, okLabel)],
      onClose: () => { if (!done) { done = true; resolve(false); } },
    });
  });
}

export function openMenu(anchor, items) {
  document.querySelectorAll(".menu").forEach(m => m.remove());
  const menu = el("div", { class: "menu", role: "menu" }, items.filter(Boolean).map(it =>
    el("button", { class: it.danger ? "danger" : "", role: "menuitem", onclick: e => { e.stopPropagation(); menu.remove(); it.action(); } },
      it.icon ? icon(it.icon) : null, it.label)));
  document.body.append(menu);
  const r = anchor.getBoundingClientRect();
  const w = menu.offsetWidth, h = menu.offsetHeight;
  menu.style.left = Math.max(8, Math.min(r.right - w, innerWidth - w - 8)) + "px";
  menu.style.top = (r.bottom + h + 8 > innerHeight ? r.top - h - 4 : r.bottom + 4) + "px";
  const away = e => { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener("mousedown", away); } };
  setTimeout(() => document.addEventListener("mousedown", away), 0);
}

// Emoji picker: a button showing the current emoji; click opens a small grid. onPick(emoji) after a choice.
const EMOJIS = "✨🧭🚀🎯💡🔥🌱🎨📈💼🧠💰🎬🎙️📚📸🏋️🧘🍳🛍️🏠✈️🌍⚡❤️🐾🎵💻🧪🏆🌟🦄🍀☕🎮🩺⚖️🏗️🚗👗".match(/\p{Extended_Pictographic}\uFE0F?/gu);

export function emojiPicker(value, onPick, label = "Choose an emoji") {
  const btn = el("button", { type: "button", class: "emoji-btn", "aria-label": label, title: label, "aria-haspopup": "true" }, value || "✨");
  btn.onclick = e => {
    e.stopPropagation();
    document.querySelectorAll(".emoji-grid").forEach(n => n.remove());
    const grid = el("div", { class: "emoji-grid", role: "listbox", "aria-label": label }, EMOJIS.map(em =>
      el("button", { type: "button", role: "option", "aria-selected": String(em === btn.textContent), class: em === btn.textContent ? "active" : "",
        onclick: ev => { ev.stopPropagation(); btn.textContent = em; grid.remove(); onPick(em); btn.focus(); } }, em)));
    document.body.append(grid);
    const r = btn.getBoundingClientRect();
    grid.style.left = Math.max(8, Math.min(r.left, innerWidth - grid.offsetWidth - 8)) + "px";
    grid.style.top = (r.bottom + grid.offsetHeight + 8 > innerHeight ? r.top - grid.offsetHeight - 6 : r.bottom + 6) + "px";
    grid.querySelector(".active, button").focus();
    const close = ev => {
      if (ev.type === "keydown" && ev.key !== "Escape") return;
      if (ev.type === "mousedown" && grid.contains(ev.target)) return;
      grid.remove(); document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close, true);
      if (ev.type === "keydown") { ev.stopPropagation(); btn.focus(); }
    };
    setTimeout(() => { document.addEventListener("mousedown", close); document.addEventListener("keydown", close, true); }, 0);
  };
  return btn;
}

export function timeAgo(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied"); }
  catch { toast("Couldn't copy. Select the text and copy it manually."); }
}

export function download(url) {
  const a = el("a", { href: url, download: "" });
  document.body.append(a);
  a.click();
  a.remove();
}
