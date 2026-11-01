// Projects: optional folders of notebooks. A project has a description and a brand kit (the default voice of its
// notebooks). Live sources live in notebooks, not here.
import { api, el, $, icon, modal, openMenu, confirmDialog, toast, timeAgo, emojiPicker } from "./ui.js";

export function newProject(onCreated) {
  const name = el("input", { type: "text", placeholder: "e.g. My agency, Spring launch, Personal brand", maxlength: "80", autofocus: true });
  let emoji = "📁";
  const pick = emojiPicker(emoji, e => { emoji = e; }, "Project icon");
  const description = el("textarea", { rows: "3", maxlength: "600", placeholder: "What it's about and who it's for. e.g. Productivity coach for solo founders; sells a 6-week focus program." });
  const status = el("div", { class: "status-line" });
  const create = async () => {
    if (!name.value.trim()) { status.className = "status-line error"; status.textContent = "Give the project a name."; return; }
    try {
      const p = await api("/api/projects", { method: "POST", body: { name: name.value, description: description.value, emoji } });
      m.close();
      if (onCreated) onCreated(p); else location.hash = `#/p/${p.id}`;
    } catch (e) { status.className = "status-line error"; status.textContent = e.message; }
  };
  name.addEventListener("keydown", e => { if (e.key === "Enter") create(); });
  const m = modal({
    title: "New project", iconNode: icon("folder"),
    body: [
      el("div", { class: "name-row" },
        el("div", { class: "field" }, el("span", { class: "field-label" }, "Icon"), pick),
        el("label", { class: "field grow" }, "Name", name)),
      el("label", { class: "field" }, "What it's about (optional)", description),
      el("p", { class: "muted small" }, "A project groups notebooks. Its description and brand kit help Cueline judge what's relevant and write in the right voice."),
      status],
    foot: [el("button", { class: "btn ghost", onclick: () => m.close() }, "Cancel"), el("button", { class: "btn primary", onclick: create }, "Create project")],
  });
}

let current = null;

export async function renderProject(root, id) {
  try { current = await api(`/api/projects/${id}`); }
  catch { root.replaceChildren(el("div", { class: "home" }, el("p", {}, "Project not found. ", el("a", { href: "#/" }, "Back home")))); return; }
  const p = current;
  $("#topbar-title").replaceChildren(el("a", { class: "crumb", href: "#/" }, "Home"), el("span", { class: "crumb-sep" }, "/"),
    el("span", { class: "page-title" }, `${p.emoji || "📁"} ${p.name}`));
  document.title = `${p.name} · Cueline`;

  const name = el("input", { class: "title-input big", value: p.name, maxlength: "80", "aria-label": "Project name" });
  name.addEventListener("change", () => save({ name: name.value }));
  name.addEventListener("keydown", e => { if (e.key === "Enter") name.blur(); });
  const description = el("textarea", { class: "niche-input", rows: "2", maxlength: "600", placeholder: "What it's about and who it's for.", "aria-label": "Project description" });
  description.value = p.description || "";
  description.addEventListener("change", () => save({ description: description.value }));
  const more = el("button", { class: "icon-btn", "aria-label": "Project options" }, icon("more"));
  more.onclick = () => openMenu(more, [{
    label: "Delete project", icon: "trash", danger: true, action: async () => {
      if (!(await confirmDialog("Delete project?", `The "${p.name}" folder is removed. Its notebooks and brand kit stay, just without a project.`))) return;
      await api(`/api/projects/${p.id}`, { method: "DELETE" });
      location.hash = "#/";
    },
  }]);
  const newNb = async () => {
    const nb = await api("/api/notebooks", { method: "POST", body: { project_id: p.id } });
    location.hash = `#/n/${nb.id}`;
  };
  const grid = el("div", { class: "nb-grid" },
    el("button", { class: "nb-card create", onclick: newNb }, el("span", { class: "plus" }, icon("plus")), "New notebook in this project"),
    ...p.notebooks.map(n => el("a", { class: "nb-card", href: `#/n/${n.id}` },
      el("div", { class: "emoji" }, n.emoji || "📓"),
      el("h3", {}, n.title),
      el("div", { class: "meta" }, `${timeAgo(n.updated_at)} · ${n.source_count} source${n.source_count === 1 ? "" : "s"}`,
        n.follow_count ? el("span", { class: "live-pill" }, el("i"), `${n.follow_count} live`) : null,
        n.inbox_count ? el("span", { class: "badge", title: "Waiting in the inbox for you to add" }, `${n.inbox_count} to review`) : null,
        n.new_count ? el("span", { class: "badge accent", title: "Sources added automatically since your last visit" }, `${n.new_count} new`) : null))));

  root.replaceChildren(el("div", { class: "home project-page" },
    el("div", { class: "project-head" },
      emojiPicker(p.emoji || "📁", e => save({ emoji: e }), "Change the project's icon"),
      el("div", { class: "grow" }, name, description),
      el("div", { class: "today-actions" }, el("a", { class: "btn", href: `#/updates/${p.id}` }, icon("inbox"), "Updates"), more)),
    brandCard(p),
    el("div", { class: "section-head" }, el("h2", {}, "Notebooks")),
    grid));
}

async function save(fields) {
  try { current = { ...current, ...(await api(`/api/projects/${current.id}`, { method: "PATCH", body: fields })) }; toast("Saved"); }
  catch (e) { toast(e.message); }
}

function brandCard(p) {
  const kit = p.brand_kit;
  const head = el("div", { class: "card-head" }, el("h2", {}, "Brand kit"),
    el("p", {}, kit ? "The default voice for every notebook in this project: tone, audience, offers, words to use and avoid."
      : `Teach Cueline (and your agents) how ${p.name} sounds and sells. It's a skill, so you can also install it into your AI agents (Claude Code, Codex).`));
  if (kit) {
    return el("section", { class: "card brand-card" }, head,
      el("div", { class: "kit" }, el("span", { class: "kit-icon" }, icon("megaphone")),
        el("div", { class: "txt" }, el("strong", {}, kit.name), el("p", {}, kit.description || ""),
          el("div", { class: "badges" }, el("span", { class: "badge" }, `v${kit.version}`), el("span", { class: "badge" }, `updated ${timeAgo(kit.updated_at)}`),
            kit.installed_path ? el("span", { class: "badge ok" }, icon("check"), "In your agents") : null))),
      el("div", { class: "card-actions" },
        el("a", { class: "btn", href: `#/skills/${kit.id}` }, icon("pencil"), "Edit"),
        el("button", { class: "btn ghost", onclick: () => draftFromNotebook(p) }, icon("refresh"), "Rebuild from a notebook")));
  }
  return el("section", { class: "card brand-card" }, head,
    el("div", { class: "card-actions" },
      el("button", { class: "btn primary", onclick: () => draftFromNotebook(p) }, icon("sparkles"), "Draft from their material"),
      el("button", { class: "btn ghost", onclick: async () => {
        const s = await api(`/api/projects/${p.id}/brand-kit`, { method: "POST" });
        location.hash = `#/skills/${s.id}`;
      } }, icon("pencil"), "Start from a template")));
}

function draftFromNotebook(p) {
  const ready = p.notebooks.filter(n => n.source_count);
  const startNew = async () => {
    const nb = await api("/api/notebooks", { method: "POST", body: { project_id: p.id, title: `${p.name}: own material` } });
    m.close();
    toast("Add the brand's website text, videos or posts, then use Studio → Brand Kit.", 6000);
    location.hash = `#/n/${nb.id}`;
  };
  const draft = async n => {
    await api(`/api/notebooks/${n.id}/studio`, { method: "POST", body: { kind: "brand_kit" } });
    m.close();
    toast("Drafting the brand kit (about a minute). It appears in Studio and links to this project.", 5000);
    location.hash = `#/n/${n.id}`;
  };
  const m = modal({
    title: "Draft a brand kit", iconNode: icon("megaphone"),
    body: [
      el("p", { class: "muted" }, `Cueline reads a notebook of ${p.name}'s own material (website, videos, posts, sales pages) and writes the brand kit with citations.`),
      ready.length
        ? el("ul", { class: "pick-list" }, ready.map(n => el("li", {}, el("button", { class: "pick-btn", onclick: () => draft(n) },
          el("span", {}, n.emoji || "📓"), el("div", {}, el("div", { class: "t" }, n.title), el("div", { class: "d" }, `${n.source_count} source${n.source_count === 1 ? "" : "s"}`))))))
        : el("div", { class: "callout" }, "No notebook with sources in this project yet. Create one and add the brand's own material first.")],
    foot: [el("button", { class: "btn ghost", onclick: () => m.close() }, "Cancel"), el("button", { class: ready.length ? "btn" : "btn primary", onclick: startNew }, icon("plus"), "New notebook for their material")],
  });
}
