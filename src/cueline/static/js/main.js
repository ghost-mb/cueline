// Router + top bar + AI status.
import { api, el, $, icon, toast, appState } from "./ui.js";
import { renderHome, createNotebook } from "./home.js";
import { renderNotebook, leaveNotebook } from "./notebook.js";
import { renderSkills, hasUnsavedSkill } from "./skills.js";
import { applyTheme, openAppearance } from "./theme.js";
import { renderUpdates, stopUpdatesPolling } from "./updates.js";
import { renderProject } from "./projects.js";
import { openAiSettings } from "./ai.js";
import { renderSearch } from "./search.js";

const app = $("#app");
let lastHash = location.hash;

async function route() {
  if (hasUnsavedSkill() && lastHash.startsWith("#/skills") && !confirm("Discard unsaved changes to this skill?")) {
    history.replaceState(null, "", lastHash);
    return;
  }
  lastHash = location.hash;
  document.querySelectorAll(".menu, .overlay").forEach(n => n.remove());
  const hash = location.hash || "#/";
  const nbMatch = hash.match(/^#\/n\/([a-f0-9]+)$/);
  const skMatch = hash.match(/^#\/skills(?:\/([a-f0-9]+))?$/);
  const upMatch = hash.match(/^#\/updates(?:\/([a-f0-9]+))?$/);
  const prMatch = hash.match(/^#\/p\/([a-f0-9]+)$/);
  const seMatch = hash.match(/^#\/search(?:\/(.*))?$/);
  if (/^#\/(today|clients)/.test(hash)) { location.replace(/^#\/today/.test(hash) ? "#/updates" : "#/"); return; }  // v2.0 links
  if (!nbMatch) leaveNotebook();
  if (!upMatch) stopUpdatesPolling();
  document.querySelectorAll(".nav-link").forEach(a => a.classList.toggle("active",
    (a.id === "nav-updates" && !!upMatch) || (a.id === "nav-skills" && !!skMatch)));
  try {
    if (nbMatch) await renderNotebook(app, nbMatch[1]);
    else if (skMatch) await renderSkills(app, skMatch[1]);
    else if (upMatch) await renderUpdates(app, upMatch[1]);
    else if (prMatch) await renderProject(app, prMatch[1]);
    else if (seMatch) await renderSearch(app, decodeURIComponent(seMatch[1] || ""));
    else await renderHome(app);
  } catch (e) {
    toast(e.message, 5000);
  }
}

async function pollStatus() {
  try {
    const s = await api("/api/status");
    appState.ai = s.ai;
    appState.hermes = s.hermes;
    if (!appState.studioTypes.length) appState.studioTypes = s.studio_types;
    const box = $("#ai-status");
    const st = s.ai.state === "idle" ? "starting" : s.ai.state;
    box.className = `ai-status ${st}`;
    box.title = (s.ai.error || `AI: ${s.ai.label}${s.ai.model ? " · " + s.ai.model : ""}`) + " (click to change)";
    const n = s.updates_new || 0;
    $("#nav-updates .count")?.remove();
    if (n) $("#nav-updates").append(el("span", { class: "count" }, n > 99 ? "99+" : n));
    const until = s.ai.paused_until ? new Date(s.ai.paused_until).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
    if (until) box.title = `${s.ai.label} usage limit reached. Background reading resumes around ${until}. Click to switch model.`;
    box.querySelector(".label").textContent = st === "ready" ? `${s.ai.label}${s.ai.model ? " · " + s.ai.model : ""}`
      : st === "paused" ? `${s.ai.label}: limit reached, resumes ${until}`
      : st === "error" ? `${s.ai.label}: needs attention` : "AI warming up…";
  } catch {
    $("#ai-status").className = "ai-status error";
    $("#ai-status .label").textContent = "Cueline server offline";
  }
}

applyTheme();
$("#nav-theme").replaceChildren(icon("palette"));
$("#nav-theme").addEventListener("click", e => {
  if (document.querySelector(".appearance")) document.querySelector(".appearance").remove();
  else openAppearance(e.currentTarget);
});
$("#nav-search").replaceChildren(icon("search"));
$("#nav-updates").replaceChildren(icon("inbox"), el("span", { class: "lbl" }, "Updates"));
$("#nav-skills").replaceChildren(icon("library"), el("span", { class: "lbl" }, "Skills"));
$("#nav-create").replaceChildren(icon("plus"), el("span", { class: "lbl" }, "Create notebook"));
$("#nav-create").addEventListener("click", createNotebook);
$("#ai-status").addEventListener("click", () => openAiSettings(pollStatus));
window.addEventListener("hashchange", route);
window.addEventListener("beforeunload", e => { if (hasUnsavedSkill()) e.preventDefault(); });

await pollStatus();
// First run without a working AI (usually no Codex CLI): ask the user to pick one.
if (appState.ai?.state === "error" && appState.ai.provider === "codex") openAiSettings(pollStatus, true);
setInterval(pollStatus, 8000);
route();
