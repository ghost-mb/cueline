// Notebook workspace: Sources (left) · Chat (middle) · Studio (right).
import {
  api, streamSSE, el, $, icon, kindIcon, KINDS, SOURCE_ICONS, renderMarkdown, modal, confirmDialog, openMenu,
  toast, timeAgo, copyText, download, appState, switchRow, badCites, citeWarning, reviewState, coverageNote,
} from "./ui.js";
import { openStudioItem } from "./viewers.js";
import { updateCard, FOLLOW_KINDS, openUpdatesSettings, checkNow, checkButton, mediaFacts } from "./updates.js";

const GROUPS = [["learn", "Learn"], ["report", "Reports"], ["artifact", "Artifacts"], ["skill", "Skills"]];
const BUSY = new Set(["queued", "running"]);
const ETA = { html: "about a minute", markdown: "20-40 seconds", json: "10-20 seconds", audio: "a few minutes" };

let nb = null;          // current notebook payload
let skills = [];        // skill list for pickers
let chatSkill = "";     // selected skill id for chat
let sourceView = null;  // null = list, a source id, or "follow:<id>" for a live source's inbox
const inboxCache = {};  // follow id -> last fetched inbox items (renders instantly, then refreshes)
let pendingFocus = null; // {loc} to highlight after opening a source
let streaming = false;
let poller = null;
let mobilePanel = "chat";
let studioMore = false;
let newSourceIds = new Set();  // auto-added sources that were unseen when this notebook was opened  // "More outputs" open in Studio (kept across the 2.5 s re-renders)
let refs = {};

export async function renderNotebook(root, id) {
  stopPolling();
  sourceView = null;
  try {
    [nb, skills] = await Promise.all([api(`/api/notebooks/${id}`), api("/api/skills")]);
  } catch {
    root.replaceChildren(el("div", { class: "home" }, el("p", {}, "Notebook not found. ", el("a", { href: "#/" }, "Back home"))));
    return;
  }
  // A project's notebooks write in the project's voice (brand kit) by default; other notebooks start with no skill.
  const kit = nb.project?.brand_skill_id;
  chatSkill = kit && skills.some(s => s.id === kit) ? kit : "";
  // Sources auto-added by live sources since the last visit: tag them "New" during this visit, then mark them seen.
  newSourceIds = new Set(nb.sources.filter(s => s.is_new).map(s => s.id));
  if (newSourceIds.size) api(`/api/notebooks/${nb.id}/seen`, { method: "POST" }).catch(() => {});
  renderTitle();
  const left = el("section", { class: "panel sources-panel" });
  const mid = el("section", { class: "panel chat-panel" });
  const right = el("section", { class: "panel studio-panel" });
  const ws = el("div", { class: "workspace" }, left, mid, right);
  const tabs = el("nav", { class: "mobile-tabs" },
    ...[["sources", "doc", "Sources"], ["chat", "chat", "Chat"], ["studio", "sparkles", "Studio"]].map(([k, ic, label]) =>
      el("button", { "data-k": k, onclick: () => setMobile(k) }, icon(ic), label)));
  refs = { ws, left, mid, right, tabs };
  root.replaceChildren(ws, tabs);
  renderSources();
  renderChat();
  renderStudio();
  setMobile(nb.sources.length ? mobilePanel : "sources");
  startPolling();
  const pending = appState.pendingPrompt;
  if (pending && pending.notebookId === id && refs.ta) {
    appState.pendingPrompt = null;
    setMobile("chat");
    refs.ta.value = pending.text;
    refs.sync();
    refs.ta.focus();
    toast("Request drafted from the angle. Edit it or press Enter.", 4000);
  }
}

export function leaveNotebook() {
  stopPolling();
  nb = null;
}

function setMobile(k) {
  mobilePanel = k;
  const map = { sources: refs.left, chat: refs.mid, studio: refs.right };
  Object.entries(map).forEach(([key, p]) => p.classList.toggle("mobile-active", key === k));
  refs.tabs.querySelectorAll("button").forEach(b => b.classList.toggle("active", b.dataset.k === k));
}

function renderTitle() {
  const input = el("input", { class: "title-input", value: nb.title, "aria-label": "Notebook title", maxlength: "120" });
  const save = async () => {
    const t = input.value.trim();
    if (!t || t === nb.title) { input.value = nb.title; return; }
    nb.title = t;
    await api(`/api/notebooks/${nb.id}`, { method: "PATCH", body: { title: t } });
    document.title = `${t} · Cueline`;
  };
  input.addEventListener("change", save);
  input.addEventListener("keydown", e => { if (e.key === "Enter") input.blur(); });
  const exportBtn = el("button", { class: "icon-btn sm", title: "Export notebook (zip: sources, chat, Studio outputs)",
    "aria-label": "Export notebook", onclick: () => download(`/api/notebooks/${nb.id}/export`) }, icon("download"));
  $("#topbar-title").replaceChildren(projectChip(), el("span", { style: { fontSize: "1.4rem" } }, nb.emoji || "📓"), input, exportBtn);
  document.title = `${nb.title} · Cueline`;
}

function projectChip() {
  const p = nb.project;
  const btn = el("button", { class: `tag-chip btn-chip${p ? "" : " empty"}`, title: p ? "Project" : "Add to a project" },
    p ? `${p.emoji || "📁"} ${p.name}` : [icon("folder"), "Project"]);
  btn.onclick = async () => {
    const projects = await api("/api/projects");
    const assign = async projectId => {
      await api(`/api/notebooks/${nb.id}`, { method: "PATCH", body: { project_id: projectId } });
      await renderNotebook($("#app"), nb.id);
      toast(projectId ? "Moved to project" : "Removed from project");
    };
    openMenu(btn, [
      p ? { label: `Open ${p.name}`, icon: "external", action: () => { location.hash = `#/p/${p.id}`; } } : null,
      ...projects.filter(x => !p || x.id !== p.id).map(x => ({ label: `${p ? "Move to" : "Add to"} ${x.emoji || "📁"} ${x.name}`, action: () => assign(x.id) })),
      p ? { label: "Remove from project", icon: "x", action: () => assign(null) } : null,
      { label: "New project…", icon: "plus", action: async () => {
        const { newProject } = await import("./projects.js");
        newProject(created => assign(created.id));
      } },
    ]);
  };
  return btn;
}

// ---------- polling: guides + studio jobs ----------
function needsPoll() {
  if (!nb) return false;
  if (nb.studio.some(i => BUSY.has(i.status))) return true;
  if (nb.follows.some(f => f.busy_count || (!f.last_checked_at && f.enabled))) return true;
  const fresh = s => Date.now() - new Date(s.created_at).getTime() < 4 * 60 * 1000;
  return nb.sources.some(s => !s.guide && fresh(s)) || (nb.sources.length && !nb.summary && nb.sources.some(fresh));
}

function startPolling() {
  stopPolling();
  poller = setInterval(async () => {
    if (!nb || !needsPoll()) return;
    try {
      const fresh = await api(`/api/notebooks/${nb.id}`);
      const studioChanged = JSON.stringify(fresh.studio.map(i => [i.id, i.status, i.title])) !== JSON.stringify(nb.studio.map(i => [i.id, i.status, i.title]));
      const followsChanged = JSON.stringify(fresh.follows.map(f => [f.id, f.inbox_count, f.busy_count, f.last_checked_at])) !== JSON.stringify(nb.follows.map(f => [f.id, f.inbox_count, f.busy_count, f.last_checked_at]));
      const guideChanged = followsChanged || fresh.summary !== nb.summary || fresh.title !== nb.title || fresh.sources.some((s, k) => !!s.guide !== !!(nb.sources[k] || {}).guide);
      const finished = nb.studio.filter(i => BUSY.has(i.status)).map(i => fresh.studio.find(f => f.id === i.id)).filter(f => f && !BUSY.has(f.status));
      nb = { ...fresh, messages: nb.messages };
      if (studioChanged) renderStudio();
      if (guideChanged) { renderTitle(); renderSources(); if (!nb.messages.length && !streaming) renderChat(); }
      finished.forEach(f => toast(f.status === "done" ? `${KIND_LABEL(f.kind)} ready: ${f.title}` : `${KIND_LABEL(f.kind)} failed`));
    } catch { /* transient */ }
  }, 2500);
}

function stopPolling() {
  if (poller) clearInterval(poller);
  poller = null;
}

const KIND_LABEL = k => (appState.studioTypes.find(t => t.kind === k) || { label: k === "note" ? "Note" : k }).label;

async function reload(parts = ["sources", "chat", "studio"]) {
  nb = await api(`/api/notebooks/${nb.id}`);
  renderTitle();
  if (parts.includes("sources")) renderSources();
  if (parts.includes("chat")) renderChat();
  if (parts.includes("studio")) renderStudio();
}

function collapseBtn(side) {
  const cls = side === "left" ? "left-collapsed" : "right-collapsed";
  return el("button", {
    class: "icon-btn collapse-btn", title: "Collapse panel",
    onclick: () => { refs.ws.classList.toggle(cls); renderSources(); renderStudio(); },
  }, icon(side === "left" ? "panel" : "panelR"));
}

// Panels are rebuilt on every poll; keep the reader's scroll position unless the panel switched to another view.
function swapPanel(panel, view, ...children) {
  const old = panel.querySelector(":scope > .panel-body");
  const top = old && panel.dataset.view === view ? old.scrollTop : 0;
  panel.replaceChildren(...children);
  panel.dataset.view = view;
  const body = panel.querySelector(":scope > .panel-body");
  if (body && top) body.scrollTop = top;
}

// ================= SOURCES =================
function renderSources() {
  const panel = refs.left;
  const collapsed = refs.ws.classList.contains("left-collapsed");
  panel.classList.toggle("collapsed", collapsed);
  const head = el("div", { class: "panel-head" }, el("h2", {}, "Sources"), collapseBtn("left"));
  const rail = el("div", { class: "rail" },
    el("button", { class: "icon-btn", title: "Add sources", onclick: openAddSource }, icon("plus")),
    ...nb.sources.slice(0, 12).map(s => el("button", {
      class: "icon-btn", title: s.title,
      onclick: () => { refs.ws.classList.remove("left-collapsed"); sourceView = s.id; renderSources(); },
    }, icon(SOURCE_ICONS[s.kind] || "doc"))),
    ...nb.follows.slice(0, 6).map(f => el("button", {
      class: "icon-btn live", title: `${f.title} (live)`,
      onclick: () => { refs.ws.classList.remove("left-collapsed"); sourceView = `follow:${f.id}`; renderSources(); },
    }, icon((FOLLOW_KINDS[f.kind] || {}).icon || "rss"))));
  head.append(rail);
  const body = el("div", { class: "panel-body" });
  if (sourceView && sourceView.startsWith("follow:")) renderFollowInbox(body, sourceView.slice(7));
  else if (sourceView) renderSourceDetail(body);
  else renderSourceList(body);
  swapPanel(panel, sourceView ? `source:${sourceView}` : "list", head, body);
  wireDrop(panel);
}

function renderSourceList(body) {
  const add = el("button", { class: "btn add-source-btn", onclick: openAddSource }, icon("plus"), "Add sources");
  const live = nb.follows.length ? el("div", { class: "live-section" },
    el("div", { class: "src-group" }, el("span", { class: "live-dot" }), "Live sources"),
    el("ul", { class: "src-list" }, nb.follows.map(followRow))) : null;
  if (!nb.sources.length) {
    body.append(add, live || "", el("div", { class: "empty-state" }, icon("doc"),
      el("strong", {}, nb.follows.length ? "Add items from your live sources" : "Saved sources will appear here"),
      nb.follows.length ? "Open a live source to see what it found, then add the items you want to use."
        : "Add YouTube videos, PDFs, docs or notes, or follow a channel or feed. Then ask questions or create things from them.",
      el("div", { style: { marginTop: "8px" } }, "Drop files here or ", el("button", { class: "link", onclick: openAddSource }, "add a source"))));
    return;
  }
  const allOn = nb.sources.every(s => s.enabled);
  const all = el("input", { type: "checkbox", "aria-label": "Select all sources" });
  all.checked = allOn;
  all.addEventListener("change", async () => {
    await Promise.all(nb.sources.map(s => api(`/api/sources/${s.id}`, { method: "PATCH", body: { enabled: all.checked } })));
    nb.sources.forEach(s => (s.enabled = all.checked ? 1 : 0));
    renderSources();
    updateComposerCount();
    refreshSize();
  });
  const list = el("ul", { class: "src-list" }, nb.sources.map(s => {
    const cb = el("input", { type: "checkbox", "aria-label": `Use ${s.title}` });
    cb.checked = !!s.enabled;
    cb.addEventListener("click", e => e.stopPropagation());
    cb.addEventListener("change", async () => {
      s.enabled = cb.checked ? 1 : 0;
      await api(`/api/sources/${s.id}`, { method: "PATCH", body: { enabled: cb.checked } });
      all.checked = nb.sources.every(x => x.enabled);
      updateComposerCount();
      refreshSize();
    });
    const menuBtn = el("button", { class: "icon-btn sm menu-btn", "aria-label": "Source options" }, icon("more"));
    menuBtn.addEventListener("click", e => {
      e.stopPropagation();
      openMenu(menuBtn, [
        { label: "Rename", icon: "pencil", action: () => renameSource(s) },
        s.url ? { label: "Open original", icon: "external", action: () => window.open(s.url, "_blank", "noopener") } : null,
        { label: "Remove source", icon: "trash", danger: true, action: () => removeSource(s) },
      ]);
    });
    const linkedInfo = s.kind === "notebook" ? (s.linked ? `Linked notebook · ${s.linked.source_count} source${s.linked.source_count === 1 ? "" : "s"}` : "Linked notebook (deleted)") : s.title;
    return el("li", { class: `src-item${s.guide ? "" : " processing"}`, onclick: () => { sourceView = s.id; renderSources(); } },
      el("span", { class: `src-icon kind-${s.kind}` }, icon(SOURCE_ICONS[s.kind] || "doc")),
      el("span", { class: "name", title: linkedInfo }, s.title, s.kind === "notebook" ? el("small", {}, linkedInfo) : null),
      newSourceIds.has(s.id) ? el("span", { class: "badge accent", title: "Added automatically by a live source" }, "New") : null, menuBtn, cb);
  }));
  // Native append() renders null as the text "null", so pass "" for missing parts.
  body.append(add, live || "", live ? el("div", { class: "src-group" }, "Sources") : "", el("div", { class: "select-all" }, "Select all sources", all), list, sizeLine());
}

// How much of the full-read limit the enabled sources use. Past it, chat and Studio search for passages instead.
function sizeLine() {
  const s = nb.size;
  if (!s || !s.chars) return "";
  const pct = Math.round(100 * s.chars / s.limit);
  const k = n => `${Math.round(n / 1000).toLocaleString()}k`;
  const text = pct <= 100 ? `Full read: ${pct}% of the limit`
    : `Large notebook (${k(s.chars)} characters): answers use passage search`;
  return el("div", { class: `nb-size${pct > 100 ? " large" : pct >= 80 ? " near" : ""}`,
    title: `Chat and Studio read every enabled source in full up to about ${k(s.limit)} characters. Past that, they search for the passages that best match each request. Turn off unrelated sources to stay under it.` },
    el("span", { class: "nb-size-bar" }, el("span", { style: { width: `${Math.min(100, pct)}%` } })), text);
}

async function refreshSize() {
  try { nb.size = (await api(`/api/notebooks/${nb.id}`)).size; } catch { return; }
  document.querySelector(".nb-size")?.replaceWith(sizeLine() || el("span", { class: "nb-size" }));
}

function followRow(f) {
  const k = FOLLOW_KINDS[f.kind] || FOLLOW_KINDS.rss;
  const state = f.last_error ? el("span", { class: "badge bad", title: f.last_error }, "error")
    : f.busy_count || !f.last_checked_at ? el("span", { class: "spinner", title: "Checking…" })
      : !f.enabled ? el("span", { class: "badge" }, "paused")
        : f.inbox_count ? el("span", { class: "badge accent" }, `${f.inbox_count} new`)
          : f.auto_add ? el("span", { class: "badge", title: "New items are added automatically" }, "auto") : null;
  const menuBtn = el("button", { class: "icon-btn sm menu-btn", "aria-label": "Live source options" }, icon("more"));
  menuBtn.addEventListener("click", e => { e.stopPropagation(); followMenu(menuBtn, f); });
  return el("li", { class: "src-item live", onclick: () => { sourceView = `follow:${f.id}`; renderSources(); } },
    el("span", { class: `src-icon kind-${f.kind.startsWith("youtube") ? "youtube" : f.kind === "rss" ? "web" : f.kind}` }, icon(k.icon)),
    el("span", { class: "name", title: f.title }, f.title, el("small", {}, k.label)), state, menuBtn);
}

function followMenu(anchor, f) {
  openMenu(anchor, [
    { label: "Check now", icon: "refresh", action: async () => { await api(`/api/follows/${f.id}/check`, { method: "POST" }); toast("Checked"); reload(["sources"]); } },
    { label: f.enabled ? "Pause" : "Resume", icon: f.enabled ? "eyeOff" : "play", action: async () => {
      await api(`/api/follows/${f.id}`, { method: "PATCH", body: { enabled: !f.enabled } }); reload(["sources"]);
    } },
    { label: "Open original", icon: "external", action: () => window.open(f.url, "_blank", "noopener") },
    { label: "Unfollow", icon: "trash", danger: true, action: async () => {
      if (!(await confirmDialog("Unfollow?", `"${f.title}" stops updating and its inbox is cleared. Items you already added stay as sources.`, "Unfollow"))) return;
      await api(`/api/follows/${f.id}`, { method: "DELETE" });
      if (sourceView === `follow:${f.id}`) sourceView = null;
      reload(["sources"]);
    } },
  ]);
}

// A live source's inbox: what it found, waiting for the user to add or dismiss.
function renderFollowInbox(body, id) {
  const f = nb.follows.find(x => x.id === id);
  if (!f) { sourceView = null; renderSourceList(body); return; }
  const k = FOLLOW_KINDS[f.kind] || FOLLOW_KINDS.rss;
  body.classList.add("src-detail");
  const menuBtn = el("button", { class: "icon-btn sm", "aria-label": "Live source options" }, icon("more"));
  menuBtn.onclick = () => followMenu(menuBtn, f);
  const checkBtn = checkButton("btn soft sm", async btn => { await checkNow(btn, { notebook_id: nb.id }); reload(["sources"]); });
  body.append(
    el("div", { class: "back-row" },
      el("button", { class: "icon-btn sm", "aria-label": "Back to sources", onclick: () => { sourceView = null; renderSources(); } }, icon("back")),
      el("span", { class: "muted small" }, "Sources"), el("span", { class: "grow" }), menuBtn),
    el("div", { class: "follow-head" },
      el("span", { class: "live-pill" }, el("i"), "Live"), el("span", { class: "muted small" }, k.label)),
    el("h3", {}, f.title),
    el("div", { class: "muted small" }, f.last_error ? el("span", { class: "error-text" }, `Last check failed: ${f.last_error}`)
      : f.last_checked_at ? `Checked ${timeAgo(f.last_checked_at)} · every few hours` : "Checking for the first time…"),
    el("div", { class: "follow-actions" }, checkBtn,
      el("button", { class: "btn ghost sm", onclick: () => openUpdatesSettings(() => reload(["sources"])) }, icon("settings"), "AI reading")),
    switchRow("Add new items automatically", "Each new item becomes a source of this notebook, ready to use. Up to 10 a day.", f.auto_add, async on => {
      await api(`/api/follows/${f.id}`, { method: "PATCH", body: { auto_add: on } });
      toast(on ? "New items will be added as sources automatically." : "New items will wait in the inbox for you.");
      reload(["sources"]);
    }));
  const list = el("div", { class: "inbox-list" });
  body.append(list);
  // The cached list is painted right away (before swapPanel attaches the panel), so the scroll position can be kept.
  // A server answer is only used while this list is still on screen: an older render's answer must not repaint
  // or overwrite the cache.
  const paint = items => {
    list.replaceChildren(...(items.length ? items.map(i => updateCard({ ...i }, { compact: true, refresh: () => {
      inboxCache[id] = (inboxCache[id] || []).filter(x => x.id !== i.id);  // added or dismissed: it leaves the inbox now
      reload(["sources", "chat"]);
    } }))
      : [el("div", { class: "empty-state" }, icon("inbox"), el("strong", {}, "Inbox is empty"),
        f.last_checked_at ? "New items appear here when this source publishes something." : "Looking for the newest items…")]));
  };
  if (inboxCache[id]) paint(inboxCache[id]);
  else list.append(el("div", { class: "empty-state" }, el("span", { class: "spinner" })));
  api(`/api/updates?follow=${id}&state=inbox`).then(d => {
    if (!list.isConnected) return;
    const changed = JSON.stringify(d.items) !== JSON.stringify(inboxCache[id]);
    inboxCache[id] = d.items;
    if (changed) paint(d.items);
  }).catch(e => toast(e.message));
}

async function renameSource(s) {
  const input = el("input", { type: "text", value: s.title, autofocus: true });
  const m = modal({
    title: "Rename source", body: input,
    foot: [el("button", { class: "btn ghost", onclick: () => m.close() }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => {
        await api(`/api/sources/${s.id}`, { method: "PATCH", body: { title: input.value } });
        m.close();
        reload(["sources"]);
      } }, "Save")],
  });
}

async function removeSource(s) {
  const what = s.kind === "notebook" ? ["Unlink notebook?", `"${s.title}" stays as it is; this notebook just stops using its sources.`, "Unlink"]
    : ["Remove source?", `"${s.title}" will be removed from this notebook.`, "Remove"];
  if (!(await confirmDialog(...what))) return;
  await api(`/api/sources/${s.id}`, { method: "DELETE" });
  if (sourceView === s.id) sourceView = null;
  reload();
}

async function renderSourceDetail(body) {
  body.classList.add("src-detail");
  body.append(el("div", { class: "back-row" },
    el("button", { class: "icon-btn sm", "aria-label": "Back to sources", onclick: () => { sourceView = null; renderSources(); } }, icon("back")),
    el("span", { class: "muted small" }, "Sources")));
  let src;
  try { src = await api(`/api/sources/${sourceView}`); }
  catch { sourceView = null; toast("That source no longer exists."); renderSources(); return; }
  if (src.kind === "notebook") { renderLinkedNotebook(body, src); return; }
  if (src.notebook_id !== nb.id) {
    body.append(el("div", { class: "callout small-callout" }, icon("book"), "From a linked notebook. ",
      el("a", { href: `#/n/${src.notebook_id}` }, "Open it")));
  }
  const m = src.meta || {};
  const sub = [m.channel, m.pages ? `${m.pages} pages` : null, src.word_count ? `${src.word_count.toLocaleString()} words` : null].filter(Boolean).join(" · ");
  body.append(el("h3", {}, src.title), el("div", { class: "muted small" }, sub));
  if (src.url) body.append(el("a", { class: "btn soft", href: src.url, target: "_blank", rel: "noopener", style: { marginTop: "10px", padding: "6px 12px", fontSize: ".82rem" } }, icon(src.kind === "youtube" ? "play" : "external"), src.kind === "youtube" ? "Watch on YouTube" : "Open original"));
  if (m.audio || m.audio_file || m.stt) body.append(transcribeRow(src));
  const g = src.guide;
  body.append(el("div", { class: "guide-card" },
    el("div", { class: "label" }, icon("sparkles"), "Source guide"),
    g ? renderMarkdown(g.summary) : el("div", { class: "thinking" }, el("span", { class: "spinner" }), "Reading this source…"),
    g ? el("div", { class: "topics" }, g.key_topics.map(t => el("span", { class: "topic" }, t))) : null,
    g?.notes?.length ? keyPoints(g.notes, body, src) : null));
  body.append(sourceText(src));
  if (pendingFocus) {
    const target = findMarker(body, pendingFocus.loc, src);
    pendingFocus = null;
    if (target) setTimeout(() => { target.scrollIntoView({ block: "center", behavior: "smooth" }); }, 60);
  }
}

// Podcast episodes and uploaded audio: local speech-to-text (stt.py). Polls while queued/running.
function transcribeRow(src) {
  const m = src.meta || {};
  const row = el("div", { class: "callout small-callout", style: { marginTop: "10px" } });
  const busy = m.stt === "queued" || m.stt === "running";
  const start = async () => {
    try { await api(`/api/sources/${src.id}/transcribe`, { method: "POST" }); toast("Transcribing on this computer…"); renderSources(); }
    catch (e) { toast(e.message); }
  };
  if (busy) {
    row.append(el("span", { class: "spinner" }), m.stt === "queued" ? " Waiting to transcribe…" : " Transcribing on this computer (a 1-hour episode takes about 5–10 min)…");
    setTimeout(() => { if (sourceView === src.id) renderSources(); }, 8000);
  } else if (m.stt === "done") {
    row.append(icon("mic"), ` Transcript made on this computer (${m.stt_model || "whisper"}). `,
      m.audio ? el("button", { class: "btn ghost sm", onclick: start }, "Transcribe again") : "");
  } else {
    row.append(icon("mic"), m.stt === "error" ? ` Transcription failed: ${m.stt_error || "unknown error"} ` : " Only the show notes so far. ",
      el("button", { class: "btn soft sm", onclick: start }, m.stt === "error" ? "Try again" : "Transcribe audio"));
  }
  return row;
}

function renderLinkedNotebook(body, src) {
  const s = nb.sources.find(x => x.id === src.id) || {};
  const linked = s.linked;
  body.append(el("div", { class: "follow-head" }, el("span", { class: "badge accent" }, icon("book"), "Linked notebook")),
    el("h3", {}, src.title),
    el("p", { class: "muted small" }, linked
      ? `Answers and Studio here can use its ${linked.source_count} switched-on source${linked.source_count === 1 ? "" : "s"}, live: when that notebook changes, this one sees it. Citations open the original source.`
      : "The linked notebook was deleted."),
    src.guide?.summary ? el("div", { class: "guide-card" }, el("div", { class: "label" }, icon("sparkles"), "About it"), renderMarkdown(src.guide.summary)) : null,
    el("div", { class: "follow-actions" },
      linked ? el("a", { class: "btn soft sm", href: `#/n/${linked.id}` }, icon("external"), "Open notebook") : null,
      el("button", { class: "btn ghost sm", onclick: () => removeSource(src) }, icon("x"), "Unlink")));
}

// Render source text; timestamps and page markers become anchors citations can scroll to.
function sourceText(src) {
  const box = el("div", { class: "src-text" });
  const timed = /^### \[\d{1,2}:\d{2}/m.test(src.content);  // videos, transcribed podcasts and audio
  const re = timed ? /^### \[(\d{1,2}:\d{2}(?::\d{2})?)\][ \t]*(.*)$/gm : /^\[p\.(\d+)\]$/gm;
  let last = 0, m;
  const text = src.content;
  while ((m = re.exec(text))) {
    box.append(text.slice(last, m.index));
    const label = timed ? `${m[1]}${m[2] ? " · " + m[2] : ""}` : `Page ${m[1]}`;
    const key = timed ? m[1] : `p.${m[1]}`;
    const href = src.kind === "youtube" && src.url ? `${src.url}&t=${toSeconds(m[1])}s` : null;
    box.append(href ? el("a", { class: "ts", "data-loc": key, href, target: "_blank", rel: "noopener" }, label) : el("span", { class: "ts", "data-loc": key }, label));
    last = m.index + m[0].length;
  }
  box.append(text.slice(last));
  return box;
}

function toSeconds(ts) {
  return ts.split(":").map(Number).reduce((a, b) => a * 60 + b, 0);
}

// The source's key-point notes (used in large notebooks); a marker jumps to that place in the text below.
function keyPoints(notes, body, src) {
  return el("details", { class: "key-points" },
    el("summary", { title: "Written by AI from the whole source. In large notebooks every request also gets these notes." }, `Key points (${notes.length})`),
    el("ul", {}, notes.map(n => el("li", {},
      n.at ? el("button", { class: "kp-at", onclick: () => findMarker(body, n.at, src)?.scrollIntoView({ block: "center", behavior: "smooth" }) }, n.at) : null,
      n.point))));
}

function findMarker(body, loc, src) {
  if (!loc) return null;
  const markers = [...body.querySelectorAll(".ts")];
  let best = null;
  if (loc.startsWith("p.")) best = markers.find(x => x.dataset.loc === loc);
  else {
    const want = toSeconds(loc);
    for (const x of markers) if (toSeconds(x.dataset.loc) <= want) best = x;
  }
  if (!best) return null;
  // highlight the passage after the marker
  const next = best.nextSibling;
  if (next && next.nodeType === Node.TEXT_NODE && next.nodeValue.trim()) {
    const mark = el("mark", {}, next.nodeValue);
    next.replaceWith(mark);
    setTimeout(() => mark.replaceWith(document.createTextNode(mark.textContent)), 4000);
  }
  return best;
}

function onCite(idx, loc, sourceIds) {
  const ids = sourceIds && sourceIds.length ? sourceIds : nb.sources.filter(s => s.enabled).map(s => s.id);
  const id = ids[idx - 1];
  if (!id) { toast("That source is no longer available."); return; }  // may live in a linked notebook
  refs.ws.classList.remove("left-collapsed");
  sourceView = id;
  pendingFocus = { loc };
  setMobile("sources");
  renderSources();
}

function wireDrop(panel) {
  let hint = null;
  panel.ondragover = e => {
    if (![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    if (!hint) { hint = el("div", { class: "drop-hint" }, "Drop to add as sources"); panel.style.position = "relative"; panel.append(hint); }
  };
  panel.ondragleave = e => { if (!panel.contains(e.relatedTarget) && hint) { hint.remove(); hint = null; } };
  panel.ondrop = async e => {
    e.preventDefault();
    if (hint) { hint.remove(); hint = null; }
    for (const f of e.dataTransfer.files) await uploadFile(f);
  };
}

async function uploadFile(file, statusEl = null) {
  const fd = new FormData();
  fd.append("file", file);
  const say = (t, err) => { if (statusEl) { statusEl.className = `status-line${err ? " error" : ""}`; statusEl.replaceChildren(err ? "" : el("span", { class: "spinner" }), t); } else if (err) toast(t); };
  say(`Reading ${file.name}…`);
  try {
    await api(`/api/notebooks/${nb.id}/upload`, { method: "POST", body: fd });
    toast(`Added ${file.name}`);
    await reload();
    return true;
  } catch (e) {
    say(`${file.name}: ${e.message}`, true);
    return false;
  }
}

// Progress of a several-links job (POST /sources/batch). Keeps running on the server if the dialog is closed.
function watchBatch(job, pane, close) {
  const LABEL = { waiting: "Waiting", working: "Reading…", added: "Added", skipped: "Skipped", failed: "Not added" };
  const list = el("ul", { class: "batch-list", style: { listStyle: "none", padding: "0", margin: "0 0 12px" } });
  const note = el("p", { class: "muted small" }, `Links are read one by one, about ${Math.round(job.delay_s || 8)} seconds apart. You can close this window; it keeps going. A link that fails is not added.`);
  pane.replaceChildren(note, list, el("div", { style: { display: "flex", justifyContent: "flex-end" } },
    el("button", { class: "btn", onclick: close }, "Close")));
  let lastAdded = 0;
  const here = () => nb && nb.id === job.notebook_id;  // the user may have opened another page meanwhile
  const paint = j => {
    list.replaceChildren(...j.items.map(it => el("li", { style: { display: "flex", gap: "10px", alignItems: "baseline", padding: "6px 0", borderBottom: "1px solid var(--line)" } },
      it.state === "working" ? el("span", { class: "spinner" }) : el("span", { class: `badge${it.state === "failed" ? " bad" : it.state === "added" ? " ok" : ""}` }, LABEL[it.state]),
      el("span", { style: { minWidth: "0", overflowWrap: "anywhere" } }, it.title || it.url,
        it.error ? el("div", { class: "muted small" }, it.error) : null))));
    const added = j.items.filter(it => it.state === "added").length;
    if (added !== lastAdded && here()) { lastAdded = added; reload(["sources"]); }
  };
  paint(job);
  const tick = async () => {
    let j;
    try { j = await api(`/api/batches/${job.id}`); } catch { return; }
    paint(j);
    if (j.done) {
      const ok = j.items.filter(it => it.state === "added").length;
      const failed = j.items.filter(it => it.state === "failed").length;
      toast(`Added ${ok} of ${j.items.length}` + (failed ? ` · ${failed} couldn't be read and weren't added` : ""), failed ? 6000 : 3000);
      if (here()) reload();
      return;
    }
    setTimeout(tick, 2000);
  };
  setTimeout(tick, 1500);
}

// ---------- add-source modal ----------
function openAddSource() {
  const tabs = [["youtube", "youtube", "YouTube"], ["web", "globe", "Website"], ["upload", "upload", "Upload files"], ["text", "text", "Paste text"],
    ["follow", "rss", "Follow (live)"], ["notebook", "book", "Notebook"], ...(appState.hermes ? [["hermes", "hermes", "From Hermes"]] : [])];
  let active = "youtube";
  const tabBar = el("div", { class: "tabs", role: "tablist" });
  const pane = el("div");
  const status = el("div", { class: "status-line", role: "status" });
  const m = modal({ title: "Add sources", body: [tabBar, pane, status] });
  const busy = t => status.replaceChildren(el("span", { class: "spinner" }), t);
  const fail = t => { status.className = "status-line error"; status.textContent = t; };
  const done = async label => { m.close(); toast(`Added ${label}`); await reload(); setMobile("sources"); };

  const panes = {
    web() {
      // Blog posts and web pages (sources.from_web). One link: added right away; several: the same paced job as YouTube.
      const url = el("textarea", { rows: "3", placeholder: "https://example.com/blog/post\nPaste up to 10 links, one per line", autofocus: true });
      const go = el("button", { class: "btn primary" }, "Add page");
      const links = () => [...new Set(url.value.match(/https?:\/\/[^\s<>"']+/g) || [])];
      url.addEventListener("input", () => { const n = links().length; go.textContent = n > 1 ? `Add ${n} pages` : "Add page"; });
      const submit = async () => {
        const found = links();
        if (!found.length) { if (url.value.trim()) fail("No links found. Paste full links starting with https://"); return; }
        go.disabled = true; status.className = "status-line";
        if (found.length === 1) {
          busy("Reading the page…");
          try {
            const s = await api(`/api/notebooks/${nb.id}/sources`, { method: "POST", body: { type: "web", url: found[0] } });
            await done(s.title);
          } catch (e) { fail(`Couldn't add this page: ${e.message} Nothing was added to the notebook.`); go.disabled = false; }
          return;
        }
        let job;
        try { job = await api(`/api/notebooks/${nb.id}/sources/batch`, { method: "POST", body: { text: url.value, kind: "web" } }); }
        catch (e) { fail(e.message); go.disabled = false; return; }
        watchBatch(job, pane, () => m.close());
      };
      go.onclick = submit;
      url.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && links().length <= 1) { e.preventDefault(); submit(); } });
      return [el("label", { class: "field" }, "Paste blog or website links", url),
        el("div", { class: "callout small-callout" }, icon("info"),
          " Some websites block automated reading, need JavaScript, or sit behind a login or paywall. If a page can't be read, Cueline tells you why and doesn't add it, so no empty source is left behind. You can always copy the text and use Paste text."),
        el("p", { class: "muted small" }, "Cueline keeps the main text of the page (not menus or ads). To get new posts automatically, use Follow (live) instead."),
        el("div", { style: { display: "flex", justifyContent: "flex-end" } }, go)];
    },
    youtube() {
      // One link: added right away. Several (up to 10): a background job reads them one by one with a pause between.
      const url = el("textarea", { rows: "3", placeholder: "https://www.youtube.com/watch?v=…\nPaste up to 10 links, one per line", autofocus: true });
      const go = el("button", { class: "btn primary" }, "Add video");
      const links = () => [...new Set(url.value.match(/https?:\/\/(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?\S*v=|shorts\/|live\/)|youtu\.be\/)[\w-]{6,}[^\s,]*/g) || [])];
      url.addEventListener("input", () => { const n = links().length; go.textContent = n > 1 ? `Add ${n} videos` : "Add video"; });
      const submit = async () => {
        const found = links();
        if (!found.length) { if (url.value.trim()) fail("No YouTube video links found."); return; }
        go.disabled = true; status.className = "status-line";
        if (found.length === 1) {
          busy("Fetching transcript… (10-30 seconds)");
          try {
            const s = await api(`/api/notebooks/${nb.id}/sources`, { method: "POST", body: { type: "youtube", url: found[0] } });
            await done(s.title);
          } catch (e) { fail(e.message); go.disabled = false; }
          return;
        }
        let job;
        try { job = await api(`/api/notebooks/${nb.id}/sources/batch`, { method: "POST", body: { text: url.value, kind: "youtube" } }); }
        catch (e) { fail(e.message); go.disabled = false; return; }
        watchBatch(job, pane, () => m.close());
      };
      go.onclick = submit;
      url.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && links().length <= 1) { e.preventDefault(); submit(); } });
      return [el("label", { class: "field" }, "Paste YouTube links", url),
        el("p", { class: "muted small" }, "Cueline reads the captions (auto-generated ones work). Nothing is downloaded. Several links are added one by one, a few seconds apart, so YouTube and your AI plan aren't flooded."),
        el("div", { style: { display: "flex", justifyContent: "flex-end" } }, go)];
    },
    upload() {
      const input = el("input", { type: "file", multiple: true, accept: ".pdf,.docx,.txt,.md,.markdown,.csv,.html,.mp3,.m4a,.wav,.ogg,.opus,.flac,.webm,.aac", style: { display: "none" } });
      const zone = el("div", { class: "dropzone", tabindex: "0", role: "button", onclick: () => input.click() },
        icon("upload"), el("strong", {}, "Choose files or drop them here"), "PDF, DOCX, TXT, Markdown (up to 30 MB) · audio, transcribed on this computer (up to 400 MB)");
      const handle = async files => {
        let ok = 0;
        for (const f of files) if (await uploadFile(f, status)) ok++;
        if (ok === files.length && ok) m.close();
      };
      input.onchange = () => handle([...input.files]);
      zone.ondragover = e => { e.preventDefault(); zone.classList.add("over"); };
      zone.ondragleave = () => zone.classList.remove("over");
      zone.ondrop = e => { e.preventDefault(); zone.classList.remove("over"); handle([...e.dataTransfer.files]); };
      zone.onkeydown = e => { if (e.key === "Enter" || e.key === " ") input.click(); };
      return [zone, input];
    },
    text() {
      const title = el("input", { type: "text", placeholder: "Title (optional)" });
      const text = el("textarea", { rows: "10", placeholder: "Paste notes, an email thread, a transcript, your own ideas…", autofocus: true });
      const go = el("button", { class: "btn primary" }, "Add text");
      go.onclick = async () => {
        go.disabled = true; busy("Saving…");
        try {
          const s = await api(`/api/notebooks/${nb.id}/sources`, { method: "POST", body: { type: "text", title: title.value, text: text.value } });
          await done(s.title);
        } catch (e) { fail(e.message); go.disabled = false; }
      };
      return [el("label", { class: "field" }, "Title", title), el("label", { class: "field" }, "Text", text),
        el("div", { style: { display: "flex", justifyContent: "flex-end" } }, go)];
    },
    follow() {
      let mode = "link";
      const input = el("input", { type: "text", autofocus: true });
      const find = el("button", { class: "btn primary" }, icon("search"), "Find");
      const preview = el("div");
      const seg = el("div", { class: "segmented compact", role: "radiogroup", "aria-label": "What to follow" });
      const paintSeg = () => {
        seg.replaceChildren(...[["link", "Link"], ["search", "YouTube search"]].map(([k, label]) =>
          el("button", { class: mode === k ? "active" : "", role: "radio", "aria-checked": String(mode === k), onclick: () => { mode = k; paintSeg(); preview.replaceChildren(); input.focus(); } }, label)));
        input.type = mode === "link" ? "url" : "text";
        input.placeholder = mode === "link" ? "YouTube channel or playlist, blog, newsletter or podcast link" : "Keywords, e.g. AI video editing";
      };
      paintSeg();
      const body = () => (mode === "link" ? { url: input.value } : { query: input.value });
      const submit = async () => {
        if (!input.value.trim()) return;
        find.disabled = true; status.className = "status-line"; busy("Finding it…");
        try {
          const info = await api("/api/follows/preview", { method: "POST", body: body() });
          status.textContent = "";
          const k = FOLLOW_KINDS[info.kind] || FOLLOW_KINDS.rss;
          const go = el("button", { class: "btn primary" }, el("span", { class: "live-dot" }), "Follow");
          let autoAdd = false;
          go.onclick = async () => {
            go.disabled = true; busy("Following…");
            try {
              const f = await api(`/api/notebooks/${nb.id}/follows`, { method: "POST", body: { ...body(), auto_add: autoAdd } });
              m.close(); toast(autoAdd ? `Following ${f.title}. New items will be added as sources automatically.`
                : `Following ${f.title}. Its newest items will appear in this notebook's inbox.`, 4500);
              sourceView = `follow:${f.id}`;
              await reload(["sources"]); setMobile("sources");
            } catch (e) { fail(e.message); go.disabled = false; }
          };
          preview.replaceChildren(el("div", { class: "follow-preview" },
            el("div", { class: "fp-head" }, el("span", { class: `src-icon kind-${info.kind.startsWith("youtube") ? "youtube" : info.kind === "rss" ? "web" : info.kind}` }, icon(k.icon)),
              el("div", {}, el("strong", {}, info.title), el("div", { class: "muted small" }, k.label))),
            info.latest.length ? el("ul", { class: "fp-list" }, info.latest.map(x => el("li", {},
              x.thumbnail ? el("img", { src: x.thumbnail, alt: "", loading: "lazy" }) : el("span", { class: "fp-ph" }, icon(k.icon)),
              el("span", {}, x.title, el("small", {}, [x.author, x.published_at ? timeAgo(x.published_at) : null, ...mediaFacts(x)].filter(Boolean).join(" · ")))))) : el("p", { class: "muted small" }, "Nothing published yet."),
            switchRow("Add new items automatically", "Off: new items wait in the inbox until you add them. Cueline checks every few hours.", false, on => (autoAdd = on)),
            el("div", { style: { display: "flex", justifyContent: "flex-end" } }, go)));
        } catch (e) { fail(e.message); }
        find.disabled = false;
      };
      find.onclick = submit;
      input.addEventListener("keydown", e => { if (e.key === "Enter") submit(); });
      return [seg, el("div", { class: "add-row", style: { marginTop: "12px" } }, input, find), preview];
    },
    notebook() {
      const linked = new Set(nb.sources.filter(s => s.kind === "notebook").map(s => s.meta?.notebook_id));
      const search = el("input", { type: "search", placeholder: "Search notebooks", autofocus: true });
      const list = el("ul", { class: "pick-list" }, el("li", {}, el("div", { class: "empty-state" }, el("span", { class: "spinner" }))));
      let all = [];
      const paint = () => {
        const q = search.value.toLowerCase();
        const rows = all.filter(n => n.id !== nb.id && (!q || n.title.toLowerCase().includes(q)));
        if (!rows.length) { list.replaceChildren(el("li", {}, el("div", { class: "empty-state" }, all.length > 1 ? "No matches." : "You have no other notebooks yet."))); return; }
        list.replaceChildren(...rows.map(n => {
          const done = linked.has(n.id);
          const btn = el("button", { class: "btn soft", disabled: done }, done ? "Linked" : "Link");
          btn.onclick = async () => {
            btn.disabled = true; busy(`Linking ${n.title}…`);
            try { await api(`/api/notebooks/${nb.id}/sources`, { method: "POST", body: { type: "notebook", notebook_id: n.id } }); await done_(n.title); }
            catch (e) { fail(e.message); btn.disabled = false; }
          };
          return el("li", {}, el("div", { class: "pick-row" }, el("span", { class: "e" }, n.emoji || "📓"),
            el("span", { style: { flex: "1", minWidth: "0" } }, el("div", { class: "t" }, n.title), el("div", { class: "d" }, `${n.source_count} source${n.source_count === 1 ? "" : "s"}`)), btn));
        }));
      };
      const done_ = done;
      search.oninput = paint;
      api("/api/notebooks").then(r => { all = r; paint(); }).catch(e => fail(e.message));
      return [el("p", { class: "muted small", style: { marginTop: "0" } }, "Use another notebook's sources here, live: when that notebook gets new sources, this one sees them too. Citations open the original."),
        search, el("div", { style: { height: "10px" } }), list];
    },
    hermes() {
      const list = el("ul", { class: "pick-list" }, el("li", {}, el("div", { class: "empty-state" }, el("span", { class: "spinner" }))));
      api("/api/hermes-transcripts").then(items => {
        const have = new Set(nb.sources.map(s => s.title));
        if (!items.length) { list.replaceChildren(el("li", {}, el("div", { class: "empty-state" }, "No transcripts in Hermes's YouTube library yet."))); return; }
        list.replaceChildren(...items.map(t => {
          const btn = el("button", { class: "btn soft", disabled: have.has(t.title) }, have.has(t.title) ? "Added" : "Add");
          btn.onclick = async () => {
            btn.disabled = true; busy(`Importing ${t.title}…`);
            try { await api(`/api/notebooks/${nb.id}/sources`, { method: "POST", body: { type: "hermes", file: t.file } }); btn.textContent = "Added"; status.textContent = ""; await reload(); }
            catch (e) { fail(e.message); btn.disabled = false; }
          };
          return el("li", {}, el("label", {}, el("span", { class: "src-icon kind-youtube", style: { width: "30px", height: "30px", borderRadius: "8px", display: "grid", placeItems: "center", flexShrink: "0" } }, icon("youtube")),
            el("span", { style: { flex: "1" } }, el("div", { class: "t" }, t.title), el("div", { class: "d" }, [t.channel, t.duration].filter(Boolean).join(" · "))), btn));
        }));
      }).catch(e => fail(e.message));
      return [el("p", { class: "muted small", style: { marginTop: "0" } }, "Transcripts Hermes already extracted with its youtube_learner skill. Cueline copies them; Hermes's files are never changed."), list];
    },
  };
  const show = k => {
    active = k;
    status.className = "status-line"; status.textContent = "";
    tabBar.replaceChildren(...tabs.map(([key, ic, label]) => el("button", { class: `tab${key === active ? " active" : ""}`, role: "tab", onclick: () => show(key) }, icon(ic), label)));
    pane.replaceChildren(...panes[k]());
    setTimeout(() => pane.querySelector("[autofocus]")?.focus(), 20);
  };
  show(active);
}

// ================= CHAT =================
function renderChat() {
  const panel = refs.mid;
  const menuBtn = el("button", { class: "icon-btn", "aria-label": "Chat options" }, icon("more"));
  menuBtn.onclick = () => openMenu(menuBtn, [
    { label: "Clear chat", icon: "trash", danger: true, action: async () => {
      if (!(await confirmDialog("Clear chat?", "All messages in this notebook's chat will be deleted. Sources and Studio outputs stay."))) return;
      await api(`/api/notebooks/${nb.id}/messages`, { method: "DELETE" });
      nb.messages = [];
      renderChat();
    } },
  ]);
  const head = el("div", { class: "panel-head" }, el("h2", {}, "Chat"), nb.messages.length ? menuBtn : el("span"));
  const scroll = el("div", { class: "chat-scroll" });
  const inner = el("div", { class: "chat-inner" });
  scroll.append(inner);
  if (!nb.sources.length) inner.append(welcome());
  else {
    inner.append(guideHeader());
    nb.messages.forEach(msg => inner.append(messageEl(msg)));
  }
  const composer = buildComposer();
  panel.replaceChildren(head, el("div", { class: "panel-body" }, scroll,
    el("div", { class: "composer-wrap" }, composer, el("div", { class: "disclaimer" }, "Cueline can make mistakes. Check important facts against the cited sources."))));
  refs.scroll = scroll;
  refs.inner = inner;
  requestAnimationFrame(() => (scroll.scrollTop = nb.messages.length ? scroll.scrollHeight : 0));
}

function welcome() {
  return el("div", { class: "welcome" },
    el("div", { class: "wave" }, "✨"),
    el("h1", {}, "Let's light up this notebook"),
    el("p", {}, "Add the videos, documents and notes you want to learn from. Cueline will read them, answer your questions with citations, and turn them into study tools, landing pages, slide decks and reusable skills."),
    el("h3", {}, "Start with a source"),
    el("div", { class: "pill-list" },
      el("button", { class: "pill", onclick: openAddSource }, "▶  Learn from a YouTube video"),
      el("button", { class: "pill", onclick: openAddSource }, "📄  Upload a PDF or document"),
      el("button", { class: "pill", onclick: openAddSource }, "📝  Paste notes or an email thread")));
}

function guideHeader() {
  const box = el("div", { class: "nb-guide" },
    el("div", { class: "emoji" }, nb.emoji || "📓"),
    el("h1", {}, nb.title),
    el("div", { class: "count" }, `${nb.sources.length} source${nb.sources.length === 1 ? "" : "s"}`));
  if (nb.summary) box.append(el("div", { class: "summary" }, renderMarkdown(nb.summary)));
  else box.append(el("div", { class: "summary loading" }, el("span", { class: "spinner" }), "Reading your sources and writing a notebook guide…"));
  if (nb.suggestions && nb.suggestions.length && !nb.messages.length) {
    box.append(el("div", { class: "suggestions" }, nb.suggestions.map(q => el("button", { class: "pill", onclick: () => send(q) }, q))));
  }
  return box;
}

function messageEl(msg, opts = {}) {
  if (msg.role === "user") {
    const skill = msg.skill_id && skills.find(s => s.id === msg.skill_id);
    return el("div", { class: "msg user" }, el("div", {},
      skill ? el("div", { style: { textAlign: "right" } }, el("span", { class: "skill-tag" }, icon("wand"), skill.name)) : null,
      el("div", { class: "bubble" }, msg.content)));
  }
  const cite = (i, loc) => onCite(i, loc, msg.source_ids);
  cite.bad = badCites(msg.checks);
  const body = renderMarkdown(msg.content, cite);
  const actions = el("div", { class: "msg-actions" },
    el("button", { class: "btn ghost", onclick: () => copyText(msg.content) }, icon("copy"), "Copy"),
    el("button", { class: "btn ghost", onclick: () => saveNote(msg.content) }, icon("note"), "Save to note"));
  return el("div", { class: "msg assistant" }, body, citeWarning(msg.checks), coverageNote(msg.context), opts.noActions ? null : actions);
}

async function saveNote(content) {
  const first = content.replace(/[#*_`>\[\]]/g, "").trim().split("\n")[0].slice(0, 60);
  await api(`/api/notebooks/${nb.id}/studio`, { method: "POST", body: { kind: "note", title: first || "Saved response", content } });
  toast("Saved to notes in Studio");
  reload(["studio"]);
}

function buildComposer() {
  const ta = el("textarea", { rows: "1", placeholder: nb.sources.length ? "Ask about your sources, or ask Cueline to create something…" : "Add a source to start chatting", "aria-label": "Message" });
  const sendBtn = el("button", { class: "send-btn", "aria-label": "Send", disabled: true }, icon("send"));
  const count = el("span", { class: "src-count" });
  const pick = skillPicker(chatSkill, v => { chatSkill = v; });
  const sync = () => {
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
    sendBtn.disabled = !ta.value.trim() || streaming || !nb.sources.some(s => s.enabled);
  };
  ta.addEventListener("input", sync);
  ta.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!sendBtn.disabled) send(ta.value); }
  });
  sendBtn.onclick = () => send(ta.value);
  refs.ta = ta; refs.sendBtn = sendBtn; refs.count = count; refs.sync = sync;
  const box = el("div", { class: "composer" }, ta, el("div", { class: "composer-row" }, pick, el("span", { class: "grow" }), count, sendBtn));
  updateComposerCount();
  return box;
}

function updateComposerCount() {
  if (!refs.count) return;
  const n = nb.sources.filter(s => s.enabled).length;
  refs.count.textContent = `${n} source${n === 1 ? "" : "s"}`;
  refs.sync && refs.sync();
}

function skillPicker(value, onChange) {
  const select = el("select", { "aria-label": "Apply a skill" },
    el("option", { value: "" }, "No skill"),
    ...skills.map(s => el("option", { value: s.id, selected: s.id === value }, s.name)));
  const wrap = el("label", { class: `skill-pick${value ? " active" : ""}`, title: "Apply a skill from your library to shape the answer" }, icon("wand"), select);
  select.onchange = () => { wrap.classList.toggle("active", !!select.value); onChange(select.value); };
  if (!skills.length) select.disabled = true;
  return wrap;
}

async function send(text) {
  text = (text || "").trim();
  if (!text || streaming) return;
  if (!nb.sources.some(s => s.enabled)) { toast("Turn on at least one source first."); return; }
  streaming = true;
  refs.ta.value = "";
  refs.sync();
  refs.inner.querySelector(".suggestions")?.remove();
  const userMsg = { role: "user", content: text, skill_id: chatSkill || null };
  refs.inner.append(messageEl(userMsg));
  const live = el("div", { class: "msg assistant" }, el("div", { class: "thinking" }, el("span", { class: "dots" }, el("span"), el("span"), el("span")), "Reading your sources…"));
  refs.inner.append(live);
  const stick = () => { const s = refs.scroll; if (s.scrollHeight - s.scrollTop - s.clientHeight < 160) s.scrollTop = s.scrollHeight; };
  refs.scroll.scrollTop = refs.scroll.scrollHeight;
  let acc = "", timer = null;
  const paint = () => { timer = null; live.replaceChildren(renderMarkdown(acc)); stick(); };
  try {
    await streamSSE(`/api/notebooks/${nb.id}/chat`, { message: text, skill_id: chatSkill || null }, (ev, data) => {
      if (ev === "user") nb.messages.push(data);
      else if (ev === "delta") { acc += data; if (!timer) timer = setTimeout(paint, 50); }
      else if (ev === "done") {
        clearTimeout(timer);
        nb.messages.push(data);
        live.replaceWith(messageEl(data));
        stick();
      } else if (ev === "error") {
        clearTimeout(timer);
        live.replaceChildren(el("div", { class: "callout", style: { color: "var(--bad)" } }, `Couldn't answer: ${data}`));
      }
    });
  } catch (e) {
    live.replaceChildren(el("div", { class: "callout", style: { color: "var(--bad)" } }, e.message));
  } finally {
    streaming = false;
    refs.sync();
    refs.ta.focus();
  }
}

// ================= STUDIO =================
function renderStudio() {
  const panel = refs.right;
  const collapsed = refs.ws.classList.contains("right-collapsed");
  panel.classList.toggle("collapsed", collapsed);
  const head = el("div", { class: "panel-head" }, el("h2", {}, "Studio"), collapseBtn("right"));
  const tiles = studioTiles();
  head.append(el("div", { class: "rail" }, ...tiles.map(t => el("button", {
    class: "icon-btn", title: t.label, onclick: () => tileAction(t),
  }, kindIcon(t.kind)))));
  const body = el("div", { class: "panel-body" });
  // studio_fit comes with the notebook overview: suggested tiles first, the rest behind "More outputs".
  // "Create" tiles (Content creation) are Cueline's main action: always pinned on top, never hidden under "More".
  const pinned = tiles.filter(t => t.group === "create");
  if (pinned.length) body.append(el("div", { class: "studio-group" }, "Create"), el("div", { class: "studio-grid wide" }, pinned.map(tile)));
  const others = tiles.filter(t => !pinned.includes(t));
  const fit = (nb.studio_fit || []).map(k => others.find(t => t.kind === k)).filter(Boolean);
  const rest = fit.length ? others.filter(t => !fit.includes(t)) : others;
  if (fit.length) {
    body.append(el("div", { class: "studio-group" }, "Suggested for these sources"), el("div", { class: "studio-grid" }, fit.map(tile)));
    if (rest.length) body.append(el("button", {
      class: `studio-more${studioMore ? " open" : ""}`, "aria-expanded": String(studioMore),
      onclick: () => { studioMore = !studioMore; renderStudio(); },
    }, studioMore ? "Fewer outputs" : `More outputs (${rest.length})`, icon("right")));
  }
  if (!fit.length || studioMore) {
    for (const [g, label] of GROUPS) {
      const group = rest.filter(t => t.group === g);
      if (group.length) body.append(el("div", { class: "studio-group" }, label), el("div", { class: "studio-grid" }, group.map(tile)));
    }
  }
  body.append(el("hr", { class: "studio-divider" }));
  if (!nb.studio.length) {
    body.append(el("div", { class: "studio-empty" }, icon("wand"),
      el("strong", {}, "Studio output will be saved here"),
      nb.sources.length ? "Click any tile to generate it from your sources. Use the pencil to customize or apply a skill." : "Add sources first, then create mind maps, decks, landing pages, skills and more."));
  } else {
    body.append(el("ul", { class: "out-list" }, nb.studio.map(outRow)));
  }
  const foot = el("div", { class: "panel-foot" }, el("button", { class: "btn", onclick: newNote }, icon("note"), "Add note"));
  swapPanel(panel, "studio", head, body, foot);
}

// Types that share a "menu" (e.g. the report styles) collapse into one tile that opens a picker.
const MENUS = { report: "Report" };
function studioTiles() {
  const out = [];
  const live = appState.studioTypes.filter(t => !t.retired);  // retired types: old items still open, no new ones
  for (const t of live) {
    if (!t.menu) out.push(t);
    else if (!out.some(o => o.kind === t.menu)) {
      out.push({ kind: t.menu, label: MENUS[t.menu] || t.menu, group: t.group, options: live.filter(o => o.menu === t.menu) });
    }
  }
  return out;
}
// Menu tiles and types with choices (e.g. Infographic style + shape) open the picker first.
const PICKER = { content: { title: "Create content", ask: "Anything specific? Platform, audience, length, angle (optional)" } };
const tileAction = t => (t.options || t.choices ? customize(t) : generate(t.kind));

function tile(t) {
  // Menu tiles always open the picker, so they don't need the separate pencil button.
  const edit = t.options || t.choices ? null : el("button", { class: "icon-btn sm edit", "aria-label": `Customize ${t.label}` }, icon("pencil"));
  if (edit) edit.onclick = e => { e.stopPropagation(); customize(t); };
  return el("div", { class: "tile", role: "button", tabindex: "0", title: `Create ${t.label}`,
    onclick: () => tileAction(t), onkeydown: e => { if (e.key === "Enter") tileAction(t); } },
  el("span", { class: "tile-icon" }, kindIcon(t.kind)), el("span", { class: "label" }, t.label), edit);
}

function outRow(item) {
  const busy = BUSY.has(item.status);
  const type = appState.studioTypes.find(t => t.kind === item.kind);
  const n = (item.source_ids || []).length;
  let sub;
  const refining = busy && item.has_content;  // a change requested in the viewer
  if (busy && item.progress) sub = item.progress;
  else if (refining) sub = "Applying your change…";
  else if (busy) sub = `Usually takes ${ETA[type?.format] || "under a minute"}`;
  else if (item.status === "error") sub = item.error || "Failed";
  else sub = [KIND_LABEL(item.kind), item.version_count > 1 ? `v${item.version_count}` : null, n ? `${n} source${n === 1 ? "" : "s"}` : null,
    item.context_mode === "retrieval" ? "selected passages" : null, timeAgo(item.updated_at)].filter(Boolean).join(" · ");
  const menuBtn = el("button", { class: "icon-btn sm menu-btn", "aria-label": "Output options" }, icon("more"));
  menuBtn.onclick = e => {
    e.stopPropagation();
    openMenu(menuBtn, [
      item.status === "error" ? { label: "Try again", icon: "refresh", action: () => generate(item.kind, item.instructions, item.skill_id) } : null,
      item.status === "done" ? { label: "Download", icon: "download", action: () => download(`/api/studio/${item.id}/download`) } : null,
      { label: "Delete", icon: "trash", danger: true, action: async () => { await api(`/api/studio/${item.id}`, { method: "DELETE" }); reload(["studio"]); } },
    ]);
  };
  const iconBox = el("span", { class: "out-icon" }, busy ? el("span", { class: "spinner" }) : kindIcon(item.kind));
  const review = !busy && item.status === "done" ? reviewState(item.checks) : null;
  const badge = review ? el("span", { class: `review-badge ${review.ready ? "ok" : "warn"}`, title: review.why },
    review.ready ? review.label : `${review.label} · ${review.why}`) : null;
  return el("li", {
    class: `out-item${busy ? " busy" : ""}${item.status === "error" ? " error" : ""}`,
    onclick: () => { if (item.status === "done" || refining) openStudioItem(item.id, { onCite, refresh: () => reload(["studio"]) }); },
  }, iconBox, el("div", { class: "txt" }, el("div", { class: "title" }, busy && !refining ? `Generating ${KIND_LABEL(item.kind)}…` : item.title), el("div", { class: "sub" }, sub), badge), busy ? null : menuBtn);
}

async function generate(kind, instructions = "", skillId = null, choices = null) {
  if (!nb.sources.some(s => s.enabled)) { toast("Add or turn on a source first."); setMobile("sources"); return; }
  try {
    await api(`/api/notebooks/${nb.id}/studio`, { method: "POST", body: { kind, instructions, skill_id: skillId, choices } });
    await reload(["studio"]);
    setMobile("studio");
  } catch (e) { toast(e.message); }
}

function customize(t) {
  const hints = {
    content: "e.g. For LinkedIn, aimed at agency owners. Lead with the Golden Circle story. End by asking a question.",
    infographic: "e.g. The 3 steps of the Golden Circle, for LinkedIn. Use orange as the accent.",
    report: "e.g. For my newsletter readers (solo founders). Focus on the Golden Circle. About 800 words.",
    landing_page: "Your offer: what it is, who it's for, price, dates, CTA. e.g. Inbox Zero Sprint, a 2-week email course. For agency owners. $49. CTA: Book a demo. (Anything you leave out becomes a placeholder.)",
    tool: "e.g. A pricing calculator for cold-email campaigns using the frameworks in the sources.",
    slides: "e.g. 10 slides for a 5-minute investor update. Dark theme.",
    podcast: "e.g. For busy agency owners. Spend most time on the pricing example. End with one thing to try this week.",
    skill: "e.g. Focus on the storytelling techniques; skip biography.",
    brand_kit: "e.g. They sell to B2B founders; keep it punchy; never promise income results.",
  };
  const instr = el("textarea", { rows: t.options || t.choices ? "3" : "5", placeholder: hints[t.kind] || "e.g. Focus on chapter 3, keep it beginner-friendly, use simple language.", autofocus: !(t.options || t.choices) });
  let skillId = "";
  // Picker for menu tiles (e.g. Report → Briefing / Study guide / Blog post).
  let kind = t.options ? (t.options.find(o => o.kind === "briefing") || t.options[0]).kind : t.kind;
  const styles = t.options ? el("div", { class: "radio-cards", role: "radiogroup", "aria-label": `${t.label} style` }) : null;
  const paint = () => styles && styles.replaceChildren(...t.options.map(o => el("button", {
    class: `radio-card${kind === o.kind ? " active" : ""}`, role: "radio", "aria-checked": String(kind === o.kind), onclick: () => { kind = o.kind; paint(); },
  }, el("span", { class: "dot" }), el("span", {}, el("strong", {}, o.label), el("span", {}, o.blurb || "")))));
  paint();
  // Per-type choices (e.g. Infographic: style cards + shape buttons); first option of each is the default.
  const picked = {};
  const choiceFields = (t.choices || []).map(c => {
    picked[c.key] = c.options[0].id;
    const chips = c.display === "chips";
    const box = el("div", { class: chips ? "chip-picker" : c.options.length > 3 ? "radio-cards grid2" : "segmented", role: "radiogroup", "aria-label": c.label });
    const hint = chips ? el("div", { class: "chip-hint" }) : "";
    const draw = () => box.replaceChildren(...c.options.map(o => {
      const on = picked[c.key] === o.id;
      const pick = () => { picked[c.key] = o.id; draw(); };
      if (on && chips) hint.textContent = o.blurb;
      if (chips) return el("button", { class: on ? "active" : "", role: "radio", "aria-checked": String(on), onclick: pick }, o.label);
      return c.options.length > 3
        ? el("button", { class: `radio-card${on ? " active" : ""}`, role: "radio", "aria-checked": String(on), onclick: pick },
          el("span", { class: "dot" }), el("span", {}, el("strong", {}, o.label), el("span", {}, o.blurb)))
        : el("button", { class: on ? "active" : "", role: "radio", "aria-checked": String(on), title: o.blurb, onclick: pick }, o.label);
    }));
    draw();
    return el("div", { class: "field" }, el("div", { style: { marginBottom: "6px" } }, c.label), box, hint);
  });
  const m = modal({
    title: PICKER[t.kind]?.title || (t.options || t.choices ? `Create ${/^[aeiou]/i.test(t.label) ? "an" : "a"} ${t.label.toLowerCase()}` : `Customize ${t.label}`), iconNode: kindIcon(t.kind),
    body: [
      styles ? el("div", { class: "field" }, el("div", { style: { marginBottom: "6px" } }, "Style"), styles) : "",
      ...choiceFields,
      el("label", { class: "field" }, PICKER[t.kind]?.ask || (t.options || t.choices ? "What should it focus on? (optional)" : "What should it focus on?"), instr),
      el("div", { class: "field" }, el("div", { style: { marginBottom: "6px" } }, "Apply a skill (optional)"), skillPicker("", v => (skillId = v))),
      el("p", { class: "muted small" }, `Uses the ${nb.sources.filter(s => s.enabled).length} selected source(s).`),
    ],
    foot: [el("button", { class: "btn ghost", onclick: () => m.close() }, "Cancel"),
      el("button", { class: "btn primary", onclick: () => { m.close(); generate(kind, instr.value, skillId || null, t.choices ? picked : null); } }, icon("sparkles"), "Generate")],
  });
}

async function newNote() {
  const item = await api(`/api/notebooks/${nb.id}/studio`, { method: "POST", body: { kind: "note", title: "New note", content: "" } });
  await reload(["studio"]);
  openStudioItem(item.id, { onCite, refresh: () => reload(["studio"]) });
}
