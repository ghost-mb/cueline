// Updates: what live sources found, across all notebooks. The same cards show in each notebook's inbox.
// Nothing becomes a source until the user adds it. How much AI reading happens is a setting (full / summary / off).
import { api, el, $, icon, renderMarkdown, modal, toast, timeAgo, appState } from "./ui.js";

export const FORMATS = {
  short_video: { label: "Short video", icon: "phone" },
  youtube_video: { label: "YouTube video", icon: "video" },
  offer: { label: "Offer", icon: "tag" },
  post: { label: "Post", icon: "text" },
};
export const FOLLOW_KINDS = {
  youtube: { icon: "youtube", label: "YouTube channel" },
  youtube_playlist: { icon: "youtube", label: "YouTube playlist" },
  youtube_search: { icon: "search", label: "YouTube search" },
  rss: { icon: "rss", label: "Blog / newsletter" },
  podcast: { icon: "mic", label: "Podcast (show notes)" },
};
const ITEM_ICON = { youtube: "youtube", article: "globe", podcast: "mic" };
const BUSY = new Set(["queued", "processing"]);

// What "Use" on an idea asks for in chat. The project's brand kit is preselected, so the voice comes from there.
const IDEA_PROMPTS = {
  short_video: "Draft a 30-45 second short-form video script (Reels/TikTok/Shorts){for}.",
  youtube_video: "Outline a YouTube video{for}: 3 title options, thumbnail text, a scripted first 30 seconds, chapters with key points, and the CTA.",
  offer: "Draft an offer{for}: name, one-line promise, who it's for, what's included, a placeholder price anchor, guarantee and CTA. Then a landing-page headline and subhead.",
  post: "Write a social post{for} (LinkedIn style): a scroll-stopping first line, short paragraphs, one clear takeaway and a CTA.",
};

let view = { project: "", state: "inbox", sort: "new" };
let timer = null;

// ---------- page ----------
export async function renderUpdates(root, projectId = "") {
  stopUpdatesPolling();
  view.project = projectId || "";
  $("#topbar-title").replaceChildren(el("span", { class: "page-title" }, "Updates"));
  document.title = "Updates · Cueline";
  const projects = await api("/api/projects");
  const page = el("div", { class: "home today" });
  root.replaceChildren(page);
  const status = el("p", { class: "today-status" });
  const failing = el("div");
  const checkBtn = checkButton("btn soft", async btn => { await checkNow(btn, { project_id: view.project || null }); load(); });
  const list = el("div", { class: "feed-list" });
  const chips = projects.length ? el("div", { class: "chip-row", role: "tablist", "aria-label": "Filter by project" },
    [["", "All projects"], ...projects.map(p => [p.id, `${p.emoji || "📁"} ${p.name}`])].map(([id, label]) => el("button", {
      class: `chip${view.project === id ? " active" : ""}`, role: "tab", "aria-selected": String(view.project === id),
      onclick: () => { location.hash = id ? `#/updates/${id}` : "#/updates"; },
    }, label))) : null;
  const stateTabs = el("div", { class: "segmented compact", role: "radiogroup", "aria-label": "Show" },
    [["inbox", "Inbox"], ["added", "Added"], ["dismissed", "Dismissed"], ["filtered", "Filtered out"]].map(([k, label]) => el("button", {
      class: view.state === k ? "active" : "", role: "radio", "aria-checked": String(view.state === k),
      onclick: () => { view.state = k; renderUpdates(root, view.project); },
    }, label)));
  const sort = el("select", { class: "sort", "aria-label": "Sort", onchange: e => { view.sort = e.target.value; load(); } },
    el("option", { value: "new", selected: view.sort === "new" }, "Newest first"),
    el("option", { value: "best", selected: view.sort === "best" }, "Best fit first"));
  page.append(
    el("div", { class: "today-head" },
      el("div", {}, el("h1", {}, "Updates"), status),
      el("div", { class: "today-actions" }, checkBtn,
        el("button", { class: "btn ghost", onclick: () => openUpdatesSettings(() => renderUpdates(root, view.project)) }, icon("settings"), "AI reading"))),
    failing, chips || "", el("div", { class: "today-toolbar" }, stateTabs, sort), list);

  async function load() {
    const q = new URLSearchParams({ state: view.state });
    if (view.project) q.set("project", view.project);
    let data;
    try { data = await api(`/api/updates?${q}`); } catch (e) { toast(e.message); return; }
    if (!document.body.contains(list)) return;
    paintStatus(status, data.status);
    failing.replaceChildren(failingBox(view.project ? [] : data.status.failing, load));
    if (!checking) checkBtn.disabled = data.status.checking;
    const items = data.items.slice();
    if (view.sort === "best") items.sort((a, b) => fit(b) - fit(a));
    list.replaceChildren(...(items.length ? items.map(i => updateCard(i, { refresh: load, showNotebook: true })) : [emptyUpdates()]));
    stopUpdatesPolling();
    if (data.status.checking || items.some(i => BUSY.has(i.status))) timer = setTimeout(load, 4000);
  }
  await load();
}

export function stopUpdatesPolling() {
  if (timer) clearTimeout(timer);
  timer = null;
}

function fit(i) { return i.triage?.relevance ?? -1; }

export function paintStatus(node, s) {
  const parts = [];
  if (s.checking) parts.push("Checking your live sources…");
  else if (s.last_checked_at) parts.push(`Checked ${timeAgo(s.last_checked_at)}`);
  else parts.push("Not checked yet");
  if (s.next_check_at && !s.checking) {
    const h = Math.max(0, (new Date(s.next_check_at) - Date.now()) / 3600000);
    parts.push(h < 1 ? "next check within the hour" : `next check in ~${Math.round(h)}h`);
  }
  if (s.queued + s.processing && s.ai_mode !== "off") parts.push(`${s.queued + s.processing} being read`);
  parts.push({ full: "AI: summary, fit & ideas", summary: "AI: summaries only", off: "AI reading off" }[s.ai_mode]);
  if (s.ai_mode !== "off" && s.triaged_today >= s.daily_cap) parts.push(`daily AI limit reached (${s.daily_cap}); the rest wait until tomorrow`);
  if (s.ai_paused_until && s.ai_mode !== "off") parts.push(`AI usage limit reached; reading resumes around ${new Date(s.ai_paused_until).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
  node.textContent = parts.join(" · ");
}

// Live sources whose last check failed (paused ones first): where they are, what went wrong, one-click resume.
export function failingBox(failing, refresh) {
  if (!failing?.length) return "";
  return el("div", { class: "failing-box", role: "status" },
    el("strong", {}, failing.length === 1 ? "1 live source needs attention" : `${failing.length} live sources need attention`),
    el("ul", {}, failing.map(f => el("li", {},
      el("a", { href: `#/n/${f.notebook_id}` }, `${f.notebook_emoji || "📓"} ${f.title}`),
      el("span", { class: "muted small" }, ` in ${f.notebook_title}: ${f.last_error}`),
      !f.enabled ? el("button", { class: "btn ghost sm", onclick: async e => {
        e.currentTarget.disabled = true;
        await api(`/api/follows/${f.id}`, { method: "PATCH", body: { enabled: true } });
        toast(`Resumed "${f.title}". It will be checked again shortly.`); refresh();
      } }, icon("play"), "Resume") : null))));
}

// Check now: runs the check, waits for it to finish (it runs in the background on the server), then says what it found.
// Panels re-render while it runs, so the busy state lives here: build check buttons with checkButton() to show it.
let checking = false;

export function checkButton(cls, onclick) {
  const btn = el("button", { class: cls, onclick: () => onclick(btn) });
  if (checking) { btn.disabled = true; btn.append(el("span", { class: "spinner" }), "Checking…"); }
  else btn.append(icon("refresh"), "Check now");
  return btn;
}

export async function checkNow(btn, body) {
  if (checking) return;
  checking = true;
  btn.disabled = true;
  btn.replaceChildren(el("span", { class: "spinner" }), "Checking…");
  const q = new URLSearchParams({ state: "inbox" });
  if (body.notebook_id) q.set("notebook", body.notebook_id);
  if (body.project_id) q.set("project", body.project_id);
  try {
    const before = new Set((await api(`/api/updates?${q}`)).items.map(i => i.id));
    await api("/api/updates/check", { method: "POST", body });
    const started = Date.now();
    let d;
    do {
      await new Promise(r => setTimeout(r, 1500));
      d = await api(`/api/updates?${q}`);
    } while (d.status.checking && Date.now() - started < 180000);
    const n = d.items.filter(i => !before.has(i.id)).length;
    toast(n ? `Found ${n} new item${n === 1 ? "" : "s"}.` : "Nothing new yet. Cueline checks again every few hours.", 3500);
  } catch (e) { toast(e.message); }
  finally {
    checking = false;
    if (btn.isConnected) { btn.disabled = false; btn.replaceChildren(icon("refresh"), "Check now"); }
  }
}

function emptyUpdates() {
  const msg = { inbox: "Nothing new right now.", added: "Nothing added yet.", dismissed: "Nothing dismissed.", filtered: "Nothing filtered out." }[view.state];
  return el("div", { class: "empty-state big" }, icon(view.state === "inbox" ? "inbox" : "check"), el("strong", {}, msg),
    view.state === "inbox"
      ? "Follow a YouTube channel, playlist, search, blog, newsletter or podcast from any notebook: Add sources → Follow. New items land here every few hours."
      : view.state === "filtered" ? "Give a live source a focus, and new items outside it land here instead of your inbox." : "");
}

// ---------- settings ----------
export async function openUpdatesSettings(onSaved) {
  const s = await api("/api/settings");
  let mode = s.updates_ai;
  const opts = [
    ["full", "Summary, fit score & ideas", "Cueline reads each new item and tells you what it says, how well it fits the notebook, and 3 content ideas."],
    ["summary", "Summary only", "A short summary of each item. Uses less of your ChatGPT plan."],
    ["off", "Off: titles and thumbnails", "No AI until you add an item. Uses none of your plan."],
  ];
  const group = el("div", { class: "radio-cards", role: "radiogroup", "aria-label": "AI reading of new items" });
  const paint = () => group.replaceChildren(...opts.map(([k, title, text]) => el("button", {
    class: `radio-card${mode === k ? " active" : ""}`, role: "radio", "aria-checked": String(mode === k), onclick: () => { mode = k; paint(); },
  }, el("span", { class: "dot" }), el("span", {}, el("strong", {}, title), el("span", {}, text)))));
  paint();
  const m = modal({
    title: "AI reading of new items", iconNode: icon("settings"),
    body: [el("p", { class: "muted", style: { marginTop: "0" } }, "When a live source finds something new, how much should Cueline read before you decide?"), group,
      el("p", { class: "muted small" }, `Cueline checks live sources every ${s.interval_hours} hours. AI reading is capped at ${s.daily_cap} items a day to protect your AI plan's usage limits.`)],
    foot: [el("button", { class: "btn ghost", onclick: () => m.close() }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => {
        await api("/api/settings", { method: "PUT", body: { updates_ai: mode } });
        m.close(); toast("Saved. Applies to new items."); onSaved && onSaved();
      } }, "Save")],
  });
}

// ---------- one item (Updates page and notebook inbox) ----------
export function updateCard(item, { refresh, showNotebook = false, compact = false }) {
  const t = item.triage || {};
  const meta = item.meta || {};
  const thumb = meta.thumbnail
    ? el("a", { class: "feed-thumb", href: item.url, target: "_blank", rel: "noopener noreferrer", "aria-label": "Open original", tabindex: "-1" },
      el("img", { src: meta.thumbnail, alt: "", loading: "lazy" }))
    : el("div", { class: `feed-thumb icon kind-${item.kind === "article" ? "web" : item.kind}` }, icon(ITEM_ICON[item.kind] || "globe"));
  const head = el("div", { class: "feed-meta" },
    showNotebook ? el("a", { class: "tag-chip", href: `#/n/${item.notebook_id}`, title: "Notebook" }, `${item.notebook_emoji || "📓"} ${item.notebook_title}`) : null,
    el("span", { class: "src" }, icon((FOLLOW_KINDS[item.follow_kind] || {}).icon || "rss"), item.follow_kind === "youtube_search" ? item.author || item.follow_title : item.follow_title),
    el("span", {}, "·"), el("span", {}, item.published_at ? timeAgo(item.published_at) : `found ${timeAgo(item.created_at)}`),
    mediaFacts(meta).length ? el("span", { class: "media-facts" }, `· ${mediaFacts(meta).join(" · ")}`) : null,
    item.status === "ready" && t.relevance !== undefined ? fitBadge(t.relevance) : null);
  const title = el("h3", {}, item.url ? el("a", { href: item.url, target: "_blank", rel: "noopener noreferrer" }, item.title) : item.title);
  const body = el("div", { class: "feed-body" }, head, title);
  if (BUSY.has(item.status)) {
    body.append(el("div", { class: "feed-busy" }, el("span", { class: "spinner" }), item.status === "queued" ? "Waiting to be read…" : "Reading…"));
  } else if (item.status === "error") {
    body.append(el("div", { class: "feed-error" }, item.error || "Couldn't read this item.",
      el("button", { class: "btn ghost sm", onclick: async () => { await api(`/api/updates/${item.id}/retry`, { method: "POST" }); refresh(); } }, icon("refresh"), "Try again")));
  } else if (item.triage) {
    if (item.state === "filtered") body.append(el("p", { class: "feed-reason" }, `Outside your focus: ${t.focus_reason || "no reason given"}`));
    if (t.relevance_reason && !compact) body.append(el("p", { class: "feed-reason" }, t.relevance_reason));
    body.append(el("div", { class: "feed-summary" }, renderMarkdown(t.summary || "")));
    if (t.key_points?.length && !compact) {
      body.append(el("details", { class: "feed-points" }, el("summary", {}, "Key points"), el("ul", {}, t.key_points.map(p => el("li", {}, p)))));
    }
    if (t.angles?.length) {
      body.append(el("details", { class: "angles", open: !compact && (t.relevance ?? 0) >= 60 },
        el("summary", { class: "angles-label" }, icon("sparkles"), `${t.angles.length} content ideas`),
        t.angles.map(a => ideaRow(item, a))));
    }
    if (t.flags?.length) body.append(el("p", { class: "feed-flags" }, "⚠ " + t.flags.join(" · ")));
  }
  body.append(actions(item, refresh));
  return el("article", { class: `feed-card${compact ? " compact" : ""}${item.status === "ready" ? "" : " " + item.status}` }, compact ? null : thumb, body);
}

// Length and views as YouTube listed them when Cueline found the item (views are not refreshed later).
export function mediaFacts(meta) {
  const out = [];
  if (meta.duration_s) out.push(clock(meta.duration_s));
  else if (meta.duration) out.push(String(meta.duration));
  if (meta.views != null) out.push(`${compact(meta.views)} views`);
  return out;
}

function clock(sec) {
  const s = Math.round(sec), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${r}` : `${m}:${r}`;
}

function compact(n) {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

function fitBadge(n) {
  const v = Number(n) || 0;
  const level = v >= 70 ? "high" : v >= 40 ? "mid" : "low";
  return el("span", { class: `rel ${level}`, title: "How well this fits the notebook (0-100)" },
    { high: "Strong fit", mid: "Some fit", low: "Weak fit" }[level], el("b", {}, v));
}

function ideaRow(item, a) {
  const f = FORMATS[a.format] || FORMATS.post;
  return el("div", { class: "angle" },
    el("span", { class: "fmt" }, icon(f.icon), f.label),
    el("div", { class: "angle-txt" }, el("strong", {}, a.hook), el("span", {}, a.idea)),
    el("button", { class: "btn sm", title: "Add the item to its notebook and draft this in chat", onclick: e => useIdea(item, a, e.currentTarget) }, "Use", icon("right")));
}

async function useIdea(item, a, btn) {
  btn.disabled = true;
  try {
    const res = await api(`/api/updates/${item.id}/add`, { method: "POST" });
    const forWho = item.project_name ? ` for ${item.project_name}` : "";
    const ask = (IDEA_PROMPTS[a.format] || IDEA_PROMPTS.post).replace("{for}", forWho);
    appState.pendingPrompt = {
      notebookId: res.notebook_id,
      text: `${ask}\nHook: "${a.hook}"\nIdea: ${a.idea}\nBuild it on the insight from "${item.title}", cite it, and keep it original (no copying).`,
    };
    if (location.hash === `#/n/${res.notebook_id}`) window.dispatchEvent(new HashChangeEvent("hashchange"));
    else location.hash = `#/n/${res.notebook_id}`;
  } catch (e) { toast(e.message); btn.disabled = false; }
}

function actions(item, refresh) {
  const row = el("div", { class: "feed-actions" });
  const setState = async state => {
    await api(`/api/updates/${item.id}`, { method: "PATCH", body: { state } });
    toast(state === "dismissed" ? "Dismissed" : "Back in the inbox");
    refresh();
  };
  if (item.state === "added") {
    row.append(el("span", { class: "badge ok" }, icon("check"), "Added as a source"),
      el("a", { class: "btn ghost sm", href: `#/n/${item.notebook_id}` }, icon("external"), "Open notebook"));
    return row;
  }
  if (!BUSY.has(item.status)) {
    const add = el("button", { class: "btn soft sm" }, icon("plus"), "Add as source");
    add.onclick = async () => {
      add.disabled = true;
      add.replaceChildren(el("span", { class: "spinner" }), "Adding…");
      try {
        const res = await api(`/api/updates/${item.id}/add`, { method: "POST" });
        toast(res.existing ? "Already a source in that notebook" : `Added to ${item.notebook_title || "the notebook"}`);
        refresh(res);
      } catch (e) { toast(e.message); add.disabled = false; add.replaceChildren(icon("plus"), "Add as source"); }
    };
    row.append(add);
  }
  row.append(item.state === "inbox"
    ? el("button", { class: "btn ghost sm", onclick: () => setState("dismissed") }, icon("eyeOff"), "Dismiss")
    : el("button", { class: "btn ghost sm", onclick: () => setState("inbox") }, icon("inbox"), "Back to inbox"));
  return row;
}
