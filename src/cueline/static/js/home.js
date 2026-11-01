// Home: projects (optional folders) + all notebooks, filterable by project.
import { api, el, $, icon, openMenu, confirmDialog, timeAgo } from "./ui.js";
import { newProject } from "./projects.js";

let filter = "";  // "" = all, "none" = no project, else a project id

export async function renderHome(root) {
  $("#topbar-title").replaceChildren();
  document.title = "Cueline";
  const [notebooks, skills, projects, status] = await Promise.all([
    api("/api/notebooks"), api("/api/skills"), api("/api/projects"), api("/api/status")]);
  const byId = Object.fromEntries(projects.map(p => [p.id, p]));
  if (filter && filter !== "none" && !byId[filter]) filter = "";
  const grid = el("div", { class: "nb-grid" });
  const chips = el("div", { class: "chip-row" });
  const paint = () => {
    const shown = notebooks.filter(n => !filter || (filter === "none" ? !n.project_id : n.project_id === filter));
    grid.replaceChildren(
      el("button", { class: "nb-card create", onclick: () => createNotebook(filter && filter !== "none" ? filter : null) }, el("span", { class: "plus" }, icon("plus")), "Create new notebook"),
      ...shown.map(n => card(n, byId[n.project_id])));
    chips.replaceChildren(...[["", "All"], ...projects.map(p => [p.id, `${p.emoji || "📁"} ${p.name}`]), ["none", "No project"]]
      .map(([id, label]) => el("button", { class: `chip${filter === id ? " active" : ""}`, onclick: () => { filter = id; paint(); } }, label)));
  };
  paint();
  const newCount = status.updates_new || 0;
  const fresh = notebooks.filter(n => n.new_count);  // notebooks with sources auto-added since the last visit
  const freshCount = fresh.reduce((sum, n) => sum + n.new_count, 0);
  root.replaceChildren(el("div", { class: "home" },
    el("div", { class: "home-hero" },
      el("h1", {}, "Turn what you watch and read into ", el("em", {}, "content that works"), "."),
      el("p", {}, "Collect videos, articles and podcasts, or follow channels and feeds so new ones arrive by themselves. Ask questions with cited answers, then turn them into scripts, offers, pages and skills for your AI agents.")),
    !notebooks.length ? el("div", { class: "value-strip" },
      value(1, "Add or follow sources", "Videos, docs, channels, feeds"),
      value(2, "Ask & understand", "Answers cite the exact moment"),
      value(3, "Create in Studio", "Scripts, pages, decks, skills"),
      value(4, "Stay current", "New items wait in Updates")) : null,
    freshCount ? el("div", { class: "today-banner fresh" },
      el("span", { class: "ic" }, icon("sparkles")),
      el("span", { class: "txt" }, el("strong", {}, `${freshCount} new source${freshCount === 1 ? "" : "s"} added by your live sources`),
        el("span", { class: "fresh-links" }, fresh.map(n => el("a", { href: `#/n/${n.id}` }, `${n.emoji || "📓"} ${n.title}`, el("b", {}, n.new_count))))),
      el("button", { class: "btn ghost sm", onclick: async () => { await api("/api/sources/seen", { method: "POST" }); renderHome(root); } }, "Mark as seen")) : null,
    newCount ? el("a", { class: "today-banner", href: "#/updates" },
      el("span", { class: "ic" }, icon("inbox")),
      el("span", { class: "txt" }, el("strong", {}, `${newCount} new item${newCount === 1 ? "" : "s"} from your live sources`), "Read, ranked and waiting in Updates"),
      icon("right")) : null,
    el("div", { class: "section-head" }, el("h2", {}, "Projects"),
      el("button", { class: "btn ghost", onclick: () => newProject() }, icon("plus"), "New project")),
    projects.length
      ? el("div", { class: "project-row" }, projects.map(p => el("a", { class: "project-card", href: `#/p/${p.id}` },
        el("span", { class: "folder" }, p.emoji || "📁"),
        el("span", { class: "txt" }, el("strong", {}, p.name),
          el("span", {}, `${p.notebook_count} notebook${p.notebook_count === 1 ? "" : "s"}${p.inbox_count ? ` · ${p.inbox_count} to review` : ""}`)),
        p.brand_skill_id ? el("span", { class: "kit-dot", title: "Has a brand kit" }, icon("megaphone")) : null)))
      : el("p", { class: "muted small projects-hint" }, "Projects are optional folders for notebooks, e.g. one per brand. A project's brand kit becomes the default voice of its notebooks."),
    el("div", { class: "section-head" }, el("h2", {}, "Notebooks"),
      el("a", { class: "btn ghost", href: "#/skills" }, icon("library"), `${skills.length} skill${skills.length === 1 ? "" : "s"} in library`)),
    projects.length ? chips : null,
    grid));
}

function value(n, title, text) {
  return el("div", { class: "value" }, el("span", { class: "num" }, n), el("strong", {}, title), text);
}

function card(nb, project) {
  const menuBtn = el("button", { class: "icon-btn sm", "aria-label": "Notebook options" }, icon("more"));
  menuBtn.onclick = e => {
    e.preventDefault();
    e.stopPropagation();
    openMenu(menuBtn, [{
      label: "Delete notebook", icon: "trash", danger: true, action: async () => {
        if (!(await confirmDialog("Delete notebook?", `"${nb.title}" and everything in it (sources, live sources, chat, Studio outputs) will be deleted. Skills in your library stay.`))) return;
        await api(`/api/notebooks/${nb.id}`, { method: "DELETE" });
        renderHome($("#app"));
      },
    }]);
  };
  return el("a", { class: "nb-card", href: `#/n/${nb.id}` },
    el("div", { class: "emoji" }, nb.emoji || "📓"), menuBtn,
    el("h3", {}, nb.title),
    project ? el("span", { class: "tag-chip" }, `${project.emoji || "📁"} ${project.name}`) : null,
    el("div", { class: "meta" }, `${timeAgo(nb.updated_at)} · ${nb.source_count} source${nb.source_count === 1 ? "" : "s"}`,
      nb.follow_count ? el("span", { class: "live-pill" }, el("i"), `${nb.follow_count} live`) : null,
      nb.inbox_count ? el("span", { class: "badge", title: "Waiting in the inbox for you to add" }, `${nb.inbox_count} to review`) : null),
    nb.new_count ? el("span", { class: "new-dot", title: "Sources added automatically since your last visit" }, `${nb.new_count} new`) : null);
}

export async function createNotebook(projectId = null) {
  const nb = await api("/api/notebooks", { method: "POST", body: { project_id: typeof projectId === "string" ? projectId : null } });
  location.hash = `#/n/${nb.id}`;
}
