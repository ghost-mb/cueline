// Skill library: list · editor/preview · import from agents · install into ~/agent-skills.
import { api, el, $, icon, modal, renderMarkdown, confirmDialog, toast, timeAgo, download } from "./ui.js";

const ORIGIN = { studio: "Made in Studio", imported: "Imported", manual: "Written by you" };
let list = [];
let current = null;
let dirty = false;

export async function renderSkills(root, selectedId) {
  $("#topbar-title").replaceChildren(el("span", { style: { fontSize: "1.3rem", fontWeight: "500", paddingLeft: "6px" } }, "Skill library"));
  document.title = "Skills · Cueline";
  list = await api("/api/skills");
  const left = el("section", { class: "panel" });
  const right = el("section", { class: "panel" });
  root.replaceChildren(el("div", { class: "skills-page" }, left, right));
  renderList(left, right, selectedId);
  const pick = selectedId && list.find(s => s.id === selectedId) ? selectedId : list[0]?.id;
  if (pick) await select(pick, left, right);
  else renderEmpty(right);
}

function renderList(left, right, activeId) {
  const search = el("input", { type: "search", placeholder: "Search skills", "aria-label": "Search skills" });
  const ul = el("ul", { class: "skill-list" });
  const paint = () => {
    const q = search.value.toLowerCase();
    const rows = list.filter(s => !q || (s.name + " " + (s.description || "")).toLowerCase().includes(q));
    ul.replaceChildren(...rows.map(s => el("li", {
      class: `skill-row${current && s.id === current.id ? " active" : ""}`,
      onclick: () => { if (dirty && !confirm("Discard unsaved changes?")) return; location.hash = `#/skills/${s.id}`; },
    },
    el("div", { class: "n" }, icon("wand"), el("span", {}, s.name)),
    el("div", { class: "d" }, s.description || ""),
    el("div", { class: "badges" },
      el("span", { class: "badge" }, ORIGIN[s.origin] || s.origin), el("span", { class: "badge" }, `v${s.version}`),
      s.installed_path ? el("span", { class: "badge ok" }, icon("check"), "Installed") : null))));
    if (!rows.length) ul.append(el("div", { class: "empty-state" }, list.length ? "No matches." : "No skills yet."));
  };
  search.oninput = paint;
  left.replaceChildren(
    el("div", { class: "panel-head" }, el("h2", {}, `Skills (${list.length})`)),
    el("div", { class: "panel-body" },
      el("div", { style: { display: "flex", gap: "8px", marginBottom: "10px" } },
        el("button", { class: "btn primary", style: { flex: "1" }, onclick: newSkill }, icon("plus"), "New"),
        el("button", { class: "btn", style: { flex: "1" }, onclick: () => openImport() }, icon("download"), "Import")),
      search, el("div", { style: { height: "10px" } }), ul));
  paint();
  left._repaint = paint;
}

function renderEmpty(right) {
  right.replaceChildren(el("div", { class: "empty-state", style: { margin: "auto", maxWidth: "460px" } }, icon("wand"),
    el("strong", {}, "Your skill library is empty"),
    "Skills are reusable playbooks. Create one from any notebook with Studio → Skill, write one yourself, or import the skills your agents already use.",
    el("div", { style: { display: "flex", gap: "8px", justifyContent: "center", marginTop: "16px" } },
      el("button", { class: "btn primary", onclick: () => openImport() }, "Import from my agents"),
      el("a", { class: "btn", href: "#/" }, "Go to notebooks"))));
}

async function select(id, left, right) {
  current = await api(`/api/skills/${id}`);
  dirty = false;
  left._repaint && left._repaint();
  const ta = el("textarea", { spellcheck: "false", "aria-label": "SKILL.md" });
  ta.value = current.body;
  const preview = el("div", { class: "preview" });
  const saveState = el("span", { class: "save-state" }, `v${current.version} · saved ${timeAgo(current.updated_at)}`);
  const saveBtn = el("button", { class: "btn primary", disabled: true }, "Save");
  let mode = "edit";
  const editTab = el("button", { class: "tab active" }, icon("pencil"), "Edit");
  const prevTab = el("button", { class: "tab" }, icon("book"), "Preview");
  const setMode = m => {
    mode = m;
    editTab.classList.toggle("active", m === "edit");
    prevTab.classList.toggle("active", m === "preview");
    ta.classList.toggle("hidden", m !== "edit");
    preview.classList.toggle("hidden", m !== "preview");
    if (m === "preview") preview.replaceChildren(renderMarkdown(ta.value.replace(/^---\n[\s\S]*?\n---\n/, "")));
  };
  editTab.onclick = () => setMode("edit");
  prevTab.onclick = () => setMode("preview");
  ta.oninput = () => { dirty = ta.value !== current.body; saveBtn.disabled = !dirty; saveState.textContent = dirty ? "Unsaved changes" : `v${current.version}`; };
  ta.onkeydown = e => { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); if (dirty) save(); } };
  const save = async () => {
    current = await api(`/api/skills/${current.id}`, { method: "PUT", body: { body: ta.value } });
    dirty = false;
    saveBtn.disabled = true;
    saveState.textContent = `v${current.version} · saved`;
    list = await api("/api/skills");
    left._repaint();
    renderHead();
    toast("Skill saved");
  };
  saveBtn.onclick = save;
  const head = el("div", { class: "head" });
  const renderHead = () => head.replaceChildren(
    el("div", { class: "t" },
      el("h1", {}, current.name),
      el("p", {}, current.description || "No description yet. Add a description: line to the frontmatter."),
      el("div", { class: "badges", style: { display: "flex", gap: "6px", flexWrap: "wrap" } },
        el("span", { class: "badge" }, ORIGIN[current.origin] || current.origin),
        el("span", { class: "badge" }, `Version ${current.version}`),
        current.installed_path ? el("span", { class: "badge ok", title: current.installed_path }, icon("check"), `Installed ${timeAgo(current.installed_at)}`) : el("span", { class: "badge" }, "Not installed"),
        current.origin === "studio" && current.origin_ref ? el("a", { class: "badge accent", href: `#/n/${current.origin_ref}`, style: { textDecoration: "none" } }, "From notebook ↗") : null)),
    el("div", { class: "actions" },
      el("button", { class: "btn", onclick: () => openInstall(current, () => select(current.id, left, right)) }, icon("install"), current.installed_path ? "Update in agents" : "Install to agents"),
      el("button", { class: "icon-btn", title: "Download SKILL.md", onclick: () => download(`/api/skills/${current.id}/download`) }, icon("download")),
      el("button", {
        class: "icon-btn", title: "Delete from library", onclick: async () => {
          if (!(await confirmDialog("Delete skill?", `"${current.name}" will be removed from Cueline's library. Installed copies in ~/agent-skills are not touched.`))) return;
          await api(`/api/skills/${current.id}`, { method: "DELETE" });
          dirty = false;
          location.hash = "#/skills";
          renderSkills($("#app"));
        },
      }, icon("trash"))));
  renderHead();
  right.replaceChildren(el("div", { class: "skill-editor" }, head,
    el("div", { class: "body" },
      el("div", { style: { display: "flex", gap: "8px", alignItems: "center", marginBottom: "10px", flexWrap: "wrap" } }, editTab, prevTab, el("span", { style: { flex: "1" } }), saveState, saveBtn),
      ta, preview)));
  setMode(mode);
}

export function hasUnsavedSkill() {
  return dirty;
}

async function newSkill() {
  const s = await api("/api/skills", { method: "POST", body: {} });
  location.hash = `#/skills/${s.id}`;
}

async function openImport() {
  const listEl = el("div", {}, el("div", { class: "empty-state" }, el("span", { class: "spinner" })));
  const search = el("input", { type: "search", placeholder: "Filter skills" });
  const go = el("button", { class: "btn primary", disabled: true }, "Import");
  const m = modal({
    title: "Import skills from your agents", iconNode: icon("download"),
    body: [el("p", { class: "muted small", style: { marginTop: "0" } }, "Cueline copies the SKILL.md into its library (read-only: your agents' files are never changed). Imported skills can be applied in chat and Studio."),
      search, el("div", { style: { height: "10px" } }), listEl],
    foot: [el("button", { class: "btn ghost", onclick: () => m.close() }, "Cancel"), go],
  });
  const items = await api("/api/skills/importable");
  const chosen = new Set();
  const paint = () => {
    const q = search.value.toLowerCase();
    const groups = {};
    items.filter(i => !q || (i.name + " " + i.description + " " + i.folder).toLowerCase().includes(q)).forEach(i => (groups[i.root] ||= []).push(i));
    const ul = el("ul", { class: "pick-list" });
    for (const [root, rows] of Object.entries(groups)) {
      ul.append(el("li", { class: "pick-group" }, `${root} · ${rows.length}`));
      for (const i of rows) {
        const cb = el("input", { type: "checkbox", disabled: i.imported });
        cb.checked = chosen.has(i.path) || i.imported;
        cb.onchange = () => { cb.checked ? chosen.add(i.path) : chosen.delete(i.path); go.disabled = !chosen.size; go.textContent = chosen.size ? `Import ${chosen.size}` : "Import"; };
        ul.append(el("li", {}, el("label", {}, cb, el("span", {},
          el("div", { class: "t" }, i.name, i.imported ? el("span", { class: "badge ok", style: { marginLeft: "6px" } }, "In library") : null),
          el("div", { class: "d" }, i.description || i.folder)))));
      }
    }
    listEl.replaceChildren(items.length ? ul : el("div", { class: "empty-state" }, "No skills found in your AI agents' skill folders."));
  };
  search.oninput = paint;
  paint();
  go.onclick = async () => {
    go.disabled = true;
    const made = await api("/api/skills/import", { method: "POST", body: { paths: [...chosen] } });
    m.close();
    toast(`Imported ${made.length} skill${made.length === 1 ? "" : "s"}`);
    location.hash = `#/skills/${made[0].id}`;
    renderSkills($("#app"), made[0].id);
  };
}

async function openInstall(skill, after) {
  let preview;
  try { preview = await api(`/api/skills/${skill.id}/install`); }
  catch (e) { toast(e.message, 5000); return; }
  const body = [
    el("p", { style: { marginTop: "0" } }, "This writes the skill to ", el("strong", {}, preview.agent), "'s skills folder. Agents load new skills in new sessions."),
    el("div", { class: "callout" }, "Target: ", el("code", {}, preview.target)),
  ];
  if (preview.identical) body.push(el("p", { class: "muted" }, "The installed copy is already identical to this version. Nothing to update."));
  else if (preview.exists) {
    body.push(el("p", { class: "small" }, el("strong", {}, "A skill with this name is already installed."), " Review the changes. The current file is backed up to Cueline's data folder before it's replaced."));
    body.push(el("div", { class: "diff" }, preview.diff.split("\n").map(l => el("div", {
      class: l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : l.startsWith("@@") ? "hunk" : "",
    }, l || " "))));
  }
  const boxes = preview.links.filter(l => !l.linked).map(l => [l.agent, el("input", { type: "checkbox", checked: true })]);
  for (const l of preview.links) {
    const box = boxes.find(([agent]) => agent === l.agent);
    body.push(box ? el("label", { class: "check", style: { marginTop: "14px" } }, box[1], el("span", {}, `Also link it into ${l.agent} (`, el("code", {}, l.path), ")"))
      : el("p", { class: "muted small" }, `Already linked into ${l.agent}.`));
  }
  const go = el("button", { class: "btn primary", disabled: preview.identical && !boxes.length }, icon("install"), preview.exists ? "Update skill" : "Install skill");
  const m = modal({ title: preview.exists ? `Update "${skill.name}" in your agents?` : `Install "${skill.name}" into your agents?`, iconNode: icon("install"), body,
    foot: [el("button", { class: "btn ghost", onclick: () => m.close() }, "Cancel"), go] });
  go.onclick = async () => {
    go.disabled = true;
    try {
      const r = await api(`/api/skills/${skill.id}/install`, { method: "POST", body: { confirm: true, links: boxes.filter(([, b]) => b.checked).map(([agent]) => agent) } });
      m.close();
      toast(`Skill ${r.updated ? "updated" : "installed"} for ${[r.agent, ...r.linked].join(", ")}.`, 4000);
      after();
    } catch (e) { toast(e.message, 5000); go.disabled = false; }
  };
}
