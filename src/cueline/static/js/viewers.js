// Studio item viewers: one large modal per item, with a renderer per format.
import { api, appState, el, icon, kindIcon, modal, renderMarkdown, toast, copyText, download, confirmDialog, badCites, citeWarning, coverageNote } from "./ui.js";

export async function openStudioItem(itemId, ctx) {
  let item = await api(`/api/studio/${itemId}`);
  const type = appState.studioTypes.find(t => t.kind === item.kind) || {};
  const render = RENDERERS[item.kind] || renderReport;
  let shown = null;  // index of an older version on screen; null = the current one
  const view = () => {
    const v = shown === null ? null : (item.versions || [])[shown];
    return v ? { ...item, title: v.title, content: v.content, version: shown } : item;
  };
  const m = modal({ title: item.title, iconNode: kindIcon(item.kind), wide: true, body: el("div"), bodyClass: "viewer-body" });
  const cite = (idx, loc) => { m.close(); ctx.onCite(idx, loc, item.source_ids); };
  const bar = type.refine ? el("div", { class: "refine-bar" }) : null;
  if (bar) m.box.append(bar);
  const paint = () => {
    const shownItem = view();
    m.box.querySelector(".modal-head h2").textContent = shownItem.title;
    const checks = shownItem.version == null ? item.checks : null;  // checks cover the current version only
    cite.bad = badCites(checks);
    const cover = shownItem.version == null ? coverageNote(item.context, "output") : "";
    if (cover) cover.classList.add("banner");
    m.body.replaceChildren(cover, citeWarning(checks, "cite-warn banner"), ...[].concat(render(shownItem, { cite, ctx, close: m.close })));
    if (bar) paintBar();
  };

  // ---- refine by chat + versions (types with refine: true) ----
  const poll = async () => {
    while (m.box.isConnected) {
      await new Promise(r => setTimeout(r, 2500));
      item = await api(`/api/studio/${item.id}`);
      if (item.status !== "running") { shown = null; paint(); ctx.refresh(); if (!item.error) toast("Change applied"); return; }
    }
  };
  const send = async change => {
    change = change.trim();
    if (!change) return;
    try { item = await api(`/api/studio/${item.id}/refine`, { method: "POST", body: { change } }); } catch (e) { toast(e.message); return; }
    shown = null;
    paintBar();
    ctx.refresh();
    poll();
  };
  function paintBar() {
    const busy = item.status === "running";
    const versions = item.versions || [];
    const input = el("input", { type: "text", placeholder: busy ? "Applying your change…" : `Ask for a change, e.g. ${type.format === "html" ? "use orange as the main colour" : type.format === "audio" ? "spend more time on the second idea" : "make the opening punchier"}`, disabled: busy, "aria-label": "Change request" });
    input.addEventListener("keydown", e => { if (e.key === "Enter") send(input.value); });
    const chips = el("div", { class: "refine-chips" }, (QUICK[type.format] || QUICK.markdown).map(c =>
      el("button", { class: "chip", disabled: busy, onclick: () => send(c) }, c)));
    const picker = versions.length > 1 ? el("select", { class: "version-select", "aria-label": "Version", disabled: busy,
      onchange: e => { const i = Number(e.target.value); shown = i === versions.length - 1 ? null : i; paint(); } },
    versions.map((v, i) => el("option", { value: String(i), selected: (shown ?? versions.length - 1) === i },
      `v${i + 1} · ${i === versions.length - 1 ? "current" : v.change}`)).reverse()) : "";
    const restore = shown !== null ? el("button", { class: "btn soft", onclick: async () => {
      try { item = await api(`/api/studio/${item.id}/restore`, { method: "POST", body: { index: shown } }); } catch (e) { toast(e.message); return; }
      shown = null; paint(); ctx.refresh(); toast("Version restored");
    } }, icon("refresh"), "Restore this version") : "";
    bar.replaceChildren(
      item.error ? el("div", { class: "refine-error" }, item.error) : "",
      shown === null ? chips : el("div", { class: "refine-note" }, `Viewing v${shown + 1}: ${(item.versions || [])[shown].change}`),
      el("div", { class: "refine-row" }, picker, restore || input,
        restore ? "" : el("button", { class: "icon-btn send", "aria-label": "Apply change", disabled: busy, onclick: () => send(input.value) },
          busy ? el("span", { class: "spinner" }) : icon("send"))));
  }

  const actions = [];
  if (ARTIFACTS.includes(item.kind)) {
    actions.push(el("a", { class: "btn soft", href: `/api/studio/${item.id}/view`, target: "_blank", rel: "noopener" }, icon("maximize"), "Open full screen"));
  }
  if (!ARTIFACTS.includes(item.kind) && !["mindmap", "flashcards", "quiz"].includes(item.kind)) {
    // Content creation: copy the publishable piece only (title and Fact check list stay in Cueline).
    const copy = () => item.kind === "podcast" ? podcastScript(view())
      : item.kind !== "content" ? view().content
      : view().content.split(/\n-{3,}\s*\n+## Fact check/)[0].replace(/^# .*\n+/, "").trim();
    const copyLabel = { content: "Copy post", podcast: "Copy script" }[item.kind] || "Copy";
    actions.push(el("button", { class: "icon-btn", title: copyLabel, onclick: () => copyText(copy()) }, icon("copy")));
  }
  actions.push(
    el("button", { class: "icon-btn", title: "Download", onclick: () => download(`/api/studio/${item.id}/download`) }, icon("download")),
    el("button", {
      class: "icon-btn", title: "Delete", onclick: async () => {
        if (!(await confirmDialog("Delete this output?", `"${item.title}" will be removed from the notebook.`))) return;
        await api(`/api/studio/${item.id}`, { method: "DELETE" });
        m.close();
        ctx.refresh();
      },
    }, icon("trash")),
  );
  m.headActions.append(...actions);
  paint();
  if (item.status === "running") poll();
}

const ARTIFACTS = ["landing_page", "tool", "slides", "infographic"];
// One-click changes offered under refinable outputs, by format.
const QUICK = {
  markdown: ["Shorter", "Punchier opening", "More concrete examples", "Simpler words"],
  html: ["Less text", "Bigger text", "Bolder design", "Different colours"],
  audio: ["Shorter", "More concrete examples", "More back-and-forth", "Simpler words"],
};

const RENDERERS = {
  mindmap: renderMindmap, flashcards: renderFlashcards, quiz: renderQuiz,
  landing_page: renderArtifact, tool: renderArtifact, slides: renderArtifact, infographic: renderArtifact,
  skill: renderSkill, brand_kit: renderSkill, note: renderNote, podcast: renderPodcast,
};

// Podcast: player + script (who says what) + fact check. Audio comes from /audio (?v= for older versions).
function renderPodcast(item, { cite }) {
  let p;
  try { p = JSON.parse(item.content); } catch { return renderReport(item, { cite }); }
  const q = item.version != null ? `v=${item.version}` : `t=${encodeURIComponent(item.updated_at)}`;
  const mins = Math.max(1, Math.round((p.duration_s || 0) / 60));
  const head = el("div", { class: "podcast-head", style: { padding: "20px 40px 8px" } },
    p.summary ? el("p", { style: { marginTop: "0" } }, p.summary) : null,
    el("audio", { controls: true, preload: "metadata", src: `/api/studio/${item.id}/audio?${q}`, style: { width: "100%" } }),
    el("div", { class: "muted small", style: { marginTop: "6px" } }, `About ${mins} min · ${p.words || "?"} words · voices: ${p.engine || ""}`));
  const script = el("div", { class: "podcast-script", style: { padding: "8px 40px" } },
    el("h3", {}, "Script"),
    ...(p.turns || []).map(t => el("p", {}, el("strong", {}, `${(p.hosts || {})[t.speaker] || t.speaker}: `), t.text)));
  const facts = (p.fact_check || []).length ? renderMarkdown("## Fact check\n" + p.fact_check.map(f => `- ${f.claim} ${f.cite}`).join("\n"), cite) : null;
  if (facts) facts.style.padding = "0 40px 24px";
  return [head, script, facts];
}

function podcastScript(item) {
  try {
    const p = JSON.parse(item.content);
    return `${p.title}\n\n` + p.turns.map(t => `${(p.hosts || {})[t.speaker] || t.speaker}: ${t.text}`).join("\n\n");
  } catch { return item.content; }
}

function renderReport(item, { cite }) {
  return renderMarkdown(item.content, cite);
}

function renderArtifact(item) {
  // Served with a CSP sandbox header; the iframe sandbox repeats it (no same-origin: can't reach Cueline's API).
  // ?v= shows an older version; ?t= busts the cache after a change.
  const q = item.version != null ? `v=${item.version}` : `t=${encodeURIComponent(item.updated_at)}`;
  return el("iframe", { class: "artifact-frame", src: `/api/studio/${item.id}/view?${q}`, sandbox: "allow-scripts", title: item.title });
}

function renderSkill(item, { close, cite }) {
  const bar = el("div", { class: "callout", style: { margin: "0 40px 0", display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" } },
    el("span", { style: { flex: "1", minWidth: "200px" } }, item.kind === "brand_kit"
      ? "This brand kit was added to your library and linked to the project. Its notebooks now write in this voice by default; edit it any time."
      : "This skill was added to your library. Edit it, apply it in chat, or install it into your AI agents (Claude Code, Codex)."),
    el("button", { class: "btn primary", onclick: () => { close(); location.hash = `#/skills/${item.result_skill_id || ""}`; } }, icon("library"), "Open in library"));
  const body = item.content.replace(/^---\n[\s\S]*?\n---\n/, "");
  return [bar, renderMarkdown(body, cite)];
}

function renderNote(item, { ctx }) {
  const title = el("input", { type: "text", value: item.title, "aria-label": "Note title" });
  const text = el("textarea", { placeholder: "Write your note… (Markdown supported)" });
  text.value = item.content || "";
  const state = el("span", { class: "muted small" });
  let timer;
  const save = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      await api(`/api/studio/${item.id}`, { method: "PATCH", body: { title: title.value || "Note", content: text.value } });
      state.textContent = "Saved";
      ctx.refresh();
    }, 500);
    state.textContent = "Saving…";
  };
  title.addEventListener("input", save);
  text.addEventListener("input", save);
  return el("div", { class: "note-editor" }, title, text, state);
}

// ---------- mind map ----------
function renderMindmap(item) {
  const data = JSON.parse(item.content);
  const tree = {
    label: data.central, root: true, open: true,
    children: data.branches.map(b => ({
      label: b.label, open: true,
      children: b.children.map(c => ({ label: c.label, open: false, children: (c.details || []).map(d => ({ label: d, leaf: true })) })),
    })),
  };
  const wrap = el("div", { class: "mm-wrap" });
  const canvas = el("div", { class: "mm-canvas" });
  wrap.append(canvas);
  const ROW = 46, PAD = 40, GAP = 72;

  // Two passes: create + measure nodes, then place columns by the widest node in the previous column.
  function layout() {
    canvas.replaceChildren();
    const nodes = [], links = [];
    let row = 0;
    (function place(n, depth, parent) {
      const kids = n.open && n.children ? n.children : [];
      const start = row;
      kids.forEach(k => place(k, depth + 1, n));
      if (!kids.length) row += 1;
      n.y = kids.length ? (kids[0].y + kids[kids.length - 1].y) / 2 : (start + 0.5) * ROW;
      n.depth = depth;
      nodes.push(n);
      if (parent) links.push([parent, n]);
    })(tree, 0, null);
    const nodeEls = new Map();
    for (const n of nodes) {
      const cls = n.root ? "root" : n.leaf ? "l3" : `l${Math.min(n.depth, 2)}`;
      const hasKids = n.children && n.children.length;
      const node = el("div", { class: `mm-node ${cls}`, style: { left: "0px", top: PAD + n.y + "px" } },
        el("span", {}, n.label), hasKids ? el("span", { class: "toggle" }, n.open ? "‹" : n.children.length) : null);
      if (hasKids) node.addEventListener("click", () => { n.open = !n.open; layout(); });
      canvas.append(node);
      nodeEls.set(n, node);
    }
    const maxW = [];
    for (const n of nodes) maxW[n.depth] = Math.max(maxW[n.depth] || 0, nodeEls.get(n).offsetWidth);
    const colX = [0];
    for (let d = 1; d < maxW.length; d++) colX[d] = colX[d - 1] + maxW[d - 1] + GAP;
    for (const n of nodes) { n.x = colX[n.depth]; nodeEls.get(n).style.left = PAD + n.x + "px"; }
    const width = PAD * 2 + colX[colX.length - 1] + maxW[maxW.length - 1], height = row * ROW + PAD * 2;
    canvas.style.width = width + "px";
    canvas.style.height = height + "px";
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", width);
    svg.setAttribute("height", height);
    for (const [a, b] of links) {
      const ax = PAD + a.x + nodeEls.get(a).offsetWidth, ay = PAD + a.y, bx = PAD + b.x, by = PAD + b.y, mx = (ax + bx) / 2;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", `M${ax},${ay} C${mx},${ay} ${mx},${by} ${bx},${by}`);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", b.depth === 1 ? "var(--accent)" : "var(--line-2)");
      path.setAttribute("stroke-width", b.depth === 1 ? "2" : "1.5");
      svg.append(path);
    }
    canvas.prepend(svg);
  }

  const setAll = open => {
    (function walk(n) { if (n.children && !n.leaf) { n.open = open || n.root || n.depth === 0; n.children.forEach(walk); } })(tree);
    tree.open = true;
    layout();
  };
  const tools = el("div", { class: "mm-tools" },
    el("button", { class: "btn soft", onclick: () => setAll(true) }, "Expand all"),
    el("button", { class: "btn soft", onclick: () => setAll(false) }, "Collapse"));
  // drag to pan
  let drag = null;
  wrap.addEventListener("pointerdown", e => {
    if (e.target.closest(".mm-node, .mm-tools") || e.pointerType !== "mouse") return;
    drag = { x: e.clientX, y: e.clientY, l: wrap.scrollLeft, t: wrap.scrollTop };
    wrap.setPointerCapture(e.pointerId);
    wrap.classList.add("dragging");
  });
  wrap.addEventListener("pointermove", e => { if (drag) { wrap.scrollLeft = drag.l - (e.clientX - drag.x); wrap.scrollTop = drag.t - (e.clientY - drag.y); } });
  wrap.addEventListener("pointerup", () => { drag = null; wrap.classList.remove("dragging"); });
  requestAnimationFrame(layout);
  const holder = el("div", { style: { position: "relative", flex: "1", display: "flex", minHeight: "0" } }, wrap, tools);
  return holder;
}

// ---------- flashcards ----------
function renderFlashcards(item, { cite }) {
  const data = JSON.parse(item.content);
  let cards = data.cards.slice(), i = 0;
  const front = el("div", { class: "fc-face fc-front" }), back = el("div", { class: "fc-face fc-back" });
  const card = el("div", { class: "fc-card", tabindex: "0", role: "button", "aria-label": "Flip card" }, el("div", { class: "fc-inner" }, front, back));
  const counter = el("span", { class: "muted" });
  const bar = el("div");
  const show = () => {
    const c = cards[i];
    card.classList.remove("flipped");
    front.replaceChildren(el("div", {}, c.front), el("span", { class: "hint" }, "Click or press space to flip"));
    back.replaceChildren(el("div", {}, c.back), c.cite ? el("div", { style: { marginTop: "14px" } }, renderMarkdown(c.cite, cite)) : "");
    counter.textContent = `${i + 1} / ${cards.length}`;
    bar.style.width = `${((i + 1) / cards.length) * 100}%`;
  };
  const flip = () => card.classList.toggle("flipped");
  const go = d => { i = (i + d + cards.length) % cards.length; show(); };
  card.addEventListener("click", e => { if (!e.target.closest(".cite")) flip(); });
  const stage = el("div", { class: "fc-stage", tabindex: "-1" },
    el("div", { class: "fc-progress" }, bar), card,
    el("div", { class: "fc-controls" },
      el("button", { class: "icon-btn", "aria-label": "Previous", onclick: () => go(-1) }, icon("left")),
      counter,
      el("button", { class: "icon-btn", "aria-label": "Next", onclick: () => go(1) }, icon("right")),
      el("button", { class: "btn soft", onclick: () => { cards.sort(() => Math.random() - 0.5); i = 0; show(); toast("Shuffled"); } }, icon("shuffle"), "Shuffle")));
  stage.addEventListener("keydown", e => {
    if (e.key === "ArrowRight") go(1);
    else if (e.key === "ArrowLeft") go(-1);
    else if (e.key === " " || e.key === "Enter") { e.preventDefault(); flip(); }
  });
  show();
  setTimeout(() => card.focus(), 50);
  return stage;
}

// ---------- quiz ----------
function renderQuiz(item, { cite }) {
  const data = JSON.parse(item.content);
  const qs = data.questions;
  let i = 0, score = 0;
  const box = el("div", { class: "quiz" });
  const show = () => {
    if (i >= qs.length) {
      const pct = Math.round((score / qs.length) * 100);
      box.replaceChildren(el("div", { class: "score" },
        el("div", { class: "big" }, `${score}/${qs.length}`),
        el("p", { class: "muted" }, pct >= 80 ? "Excellent. You've got this material down." : pct >= 50 ? "Solid start. Review the explanations and try again." : "Worth another pass through the sources."),
        el("button", { class: "btn primary", onclick: () => { i = 0; score = 0; show(); } }, icon("refresh"), "Retake quiz")));
      return;
    }
    const q = qs[i];
    let answered = false;
    const opts = q.options.map((o, k) => el("button", { class: "opt" }, el("span", { class: "letter" }, "ABCD"[k] || k + 1), el("span", {}, o)));
    const after = el("div");
    opts.forEach((b, k) => b.addEventListener("click", () => {
      if (answered) return;
      answered = true;
      if (k === q.answer) score++;
      opts.forEach((x, j) => { x.disabled = true; if (j === q.answer) x.classList.add("correct"); else if (j === k) x.classList.add("wrong"); });
      after.replaceChildren(
        el("div", { class: "explain" }, el("strong", {}, k === q.answer ? "Correct. " : "Not quite. "), q.explanation, " ",
          q.cite ? renderMarkdown(q.cite, cite).firstChild || "" : ""),
        el("button", { class: "btn primary", onclick: () => { i++; show(); } }, i + 1 < qs.length ? "Next question" : "See score"));
    }));
    box.replaceChildren(
      el("div", { class: "qnum" }, el("span", {}, `Question ${i + 1} of ${qs.length}`), el("span", {}, `Score ${score}`)),
      el("h3", {}, q.question), ...opts, after);
  };
  show();
  return box;
}
