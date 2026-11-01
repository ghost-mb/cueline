// AI model dialog: pick the provider (ChatGPT connector or an API key) and the model. Keys go to the server once
// and never come back: the browser only sees "key saved (…abcd)".
import { api, el, icon, toast, modal } from "./ui.js";

export async function openAiSettings(onSaved, firstRun = false) {
  let s;
  try { s = await api("/api/ai"); } catch (e) { toast(e.message || "Cueline server offline"); return; }
  let provider = s.provider;
  const chosen = Object.fromEntries(s.providers.map(p => [p.id, p.model]));
  const models = {};  // provider -> [{id,label}] once loaded

  const group = el("div", { class: "radio-cards grid2", role: "radiogroup", "aria-label": "AI provider" });
  const detail = el("div", { class: "ai-detail" });
  const saveBtn = el("button", { class: "btn primary" }, "Use this model");
  const P = id => s.providers.find(p => p.id === id);

  const paint = () => {
    group.replaceChildren(...s.providers.map(p => el("button", {
      class: `radio-card${provider === p.id ? " active" : ""}`, role: "radio", "aria-checked": String(provider === p.id),
      onclick: () => { provider = p.id; paint(); },
    }, el("span", { class: "dot" }), el("span", {},
      el("strong", {}, p.label),
      el("span", {}, p.kind === "connector" ? "Login, no key" : p.kind === "local" ? "On this computer, no key" : p.has_key ? `Key saved (…${p.key_hint})` : "Needs an API key")))));
    paintDetail();
  };

  const paintDetail = () => {
    const p = P(provider);
    const parts = [];
    if (p.note) parts.push(el("p", { class: "muted small", style: { marginTop: "0" } }, p.note + "."));
    if (p.kind === "key") parts.push(keyRow(p));
    if (p.kind !== "key" || p.has_key) parts.push(modelRow(p));
    if (p.kind === "key") parts.push(el("p", { class: "muted small" }, `${p.label} bills your account per use. The key is stored only on this computer (data/keys.json).`));
    detail.replaceChildren(...parts);
    saveBtn.disabled = p.kind === "key" && !p.has_key;
  };

  const keyRow = p => {
    const input = el("input", { type: "password", placeholder: p.has_key ? "Paste a new key to replace it" : "Paste your API key",
      autocomplete: "off", spellcheck: "false", "aria-label": `${p.label} API key`, style: { flex: "1" } });
    const save = el("button", { class: "btn", onclick: async () => {
      if (!input.value.trim()) { input.focus(); return; }
      save.disabled = true; save.textContent = "Checking…";
      try {
        const r = await api(`/api/ai/keys/${p.id}`, { method: "PUT", body: { key: input.value.trim() } });
        p.has_key = true; p.key_hint = input.value.trim().slice(-4); models[p.id] = r.models;
        toast(`${p.label} key works and is saved.`); paint();
      } catch (e) { toast(e.message || "That key didn't work."); save.disabled = false; save.textContent = "Save key"; }
    } }, "Save key");
    const remove = p.has_key ? el("button", { class: "btn ghost sm", onclick: async () => {
      s = await api(`/api/ai/keys/${p.id}`, { method: "DELETE" });
      if (s.provider !== provider) provider = s.provider;
      toast(`${p.label} key removed.`); paint(); onSaved && onSaved();
    } }, "Remove key") : null;
    return el("div", { class: "field" },
      el("span", { class: "field-label" }, `${p.label} API key`, p.keys_url ? el("a", { href: p.keys_url, target: "_blank", rel: "noopener noreferrer", class: "small", style: { marginLeft: "8px" } }, "Get a key") : null),
      el("div", { class: "row", style: { display: "flex", gap: "8px" } }, input, save), remove);
  };

  const modelRow = p => {
    const select = el("select", { "aria-label": "Model", onchange: () => { chosen[p.id] = select.value; } });
    const fill = list => {
      const cur = chosen[p.id];
      const opts = p.kind === "connector" ? [{ id: "", label: "Default (Codex picks)" }, ...list] : list;
      if (cur && !opts.some(m => m.id === cur)) opts.unshift({ id: cur, label: cur });
      if (!cur && p.kind !== "connector" && opts.length) chosen[p.id] = opts[0].id;
      select.replaceChildren(...opts.map(m => el("option", { value: m.id, selected: m.id === (chosen[p.id] || "") }, !m.id || m.label === m.id ? m.label : `${m.label} (${m.id})`)));
    };
    if (models[p.id]) fill(models[p.id]);
    else {
      select.append(el("option", {}, "Loading models…"));
      api(`/api/ai/models?provider=${p.id}`).then(r => { models[p.id] = r.models; fill(r.models); })
        .catch(e => { fill([]); toast(e.message || "Could not list models"); });
    }
    return el("label", { class: "field" }, "Model", select);
  };

  saveBtn.onclick = async () => {
    saveBtn.disabled = true;
    try {
      await api("/api/ai", { method: "PUT", body: { provider, model: chosen[provider] || "" } });
      m.close(); toast(`Cueline now uses ${P(provider).label}${chosen[provider] ? " · " + chosen[provider] : ""}.`); onSaved && onSaved();
    } catch (e) { toast(e.message || "Could not save"); saveBtn.disabled = false; }
  };

  // Images (AI-image infographics) and podcast voices: each saved on change, separate from the text model above.
  const mediaRow = (path, label, aria) => {
    const box = el("div", { class: "field" });
    const paintRow = d => {
      const row = d.providers.find(p => p.id === d.provider);
      const save = async body => {
        try { paintRow(await api(path, { method: "PUT", body })); toast(`${label} saved.`); }
        catch (e) { toast(e.message || "Could not save"); }
      };
      const select = el("select", { "aria-label": aria, onchange: () => save({ provider: select.value }) },
        ...d.providers.map(p => el("option", { value: p.id, selected: p.id === d.provider, disabled: !p.ready },
          p.label + (p.ready ? "" : " (add its key above first)"))));
      const model = row.has_model ? el("input", { value: row.model, "aria-label": `${aria} model`, spellcheck: "false",
        style: { flex: "1" }, onchange: () => save({ provider: row.id, model: model.value }) }) : null;
      box.replaceChildren(el("span", { class: "field-label" }, label),
        el("div", { style: { display: "flex", gap: "8px" } }, select, model), el("p", { class: "muted small" }, row.note + "."));
    };
    api(path).then(paintRow).catch(() => box.remove());
    return box;
  };
  const images = mediaRow("/api/ai/images", "Images (AI-image infographics)", "Image engine");
  const voices = mediaRow("/api/ai/voices", "Podcast voices", "Voice engine");

  paint();
  const m = modal({
    title: firstRun ? "Pick your AI" : "AI model", iconNode: icon("sparkles"),
    body: [el("p", { class: "muted", style: { marginTop: "0" } }, firstRun
      ? "Cueline needs an AI to read your sources and write. Paste an API key (Claude, OpenAI, Gemini or OpenRouter), or pick ChatGPT if the Codex CLI is installed and logged in (codex login)."
      : "Choose what writes Cueline's answers, Studio outputs and update summaries."), group, detail, images, voices],
    foot: [el("button", { class: "btn ghost", onclick: () => m.close() }, "Cancel"), saveBtn],
  });
}
