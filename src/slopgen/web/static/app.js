let L = {};
// A few labels are written for the terminal and say so — "(←/→ to adjust)" is about
// arrow keys, which a slider does not have. Overridden here rather than in the shared
// table, because the terminal's wording is right for the terminal.
// key -> the key to use INSTEAD, on the web. Values are keys, not words: this table
// is built when the file loads and no labels have arrived yet, so a word put here
// would be frozen at load in whatever language happened to be current.
const WEB_LABEL = {
  tts_rate: "js.speech-rate",
  voice: "js.voice-cloned",
};
const lab = (k, fallback) => {
  const via = WEB_LABEL[k];
  return (via && L[via]) || L[k] || fallback || k;
};

// The browser half. Everything here is about POINTING at pictures, which is the one
// job a terminal cannot do and the reason this frontend exists at all.
"use strict";
const $ = (s) => document.querySelector(s);
const api = async (url, opts) => {
  const r = await fetch(url, { credentials: "same-origin", ...opts });
  if (r.status === 401) { show("login"); throw new Error("auth"); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.status === 204 ? null : r.json();
};
// Inline, never a dialog. `alert()` blocks the whole renderer until somebody clicks
// it, which is wrong for a tool you leave running in a tab and fatal for anything
// driving the page — one unnoticed dialog and everything after it silently stops.
function say(text, bad = false) {
  let el = document.querySelector("#say");
  if (!el) {
    el = document.createElement("div");
    el.id = "say";
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.className = bad ? "bad" : "";
  el.hidden = !text;
  clearTimeout(say._t);
  say._t = setTimeout(() => (el.hidden = true), 5000);
}

const show = (which) => {
  $("#login").hidden = which !== "login";
  $("#app").hidden = which !== "app";
};

let world = null, cards = [], card = null, targets = [], sel = -1;
// The video's shape, which is also the shape of every crop window. Both come from the
// same place: a picture is fitted to the video's aspect before anything is cropped out
// of it, so a region is a scaled copy of the frame and not a free rectangle.
let videoAspect = 1080 / 1920;

// ---------------------------------------------------------------- boot
(async function boot() {
  const me = await fetch("/api/me").then((r) => r.json());
  if (!me.signed_in) return show("login");
  show("app");
  const cfg = await fetch("/api/config").then((r) => r.json()).catch(() => null);
  if (cfg) videoAspect = cfg.video.width / cfg.video.height;
  await loadWorlds();
  await loadOptions();
})();

$("#loginform").onsubmit = async (e) => {
  e.preventDefault();
  const body = new FormData(e.target);
  const r = await fetch("/api/login", { method: "POST", body });
  if (!r.ok) { $("#loginerr").textContent = lab("js.no-good"); return; }
  show("app"); loadWorlds();
};

// The three doors mirror the terminal's home screen — make something, pick a run back
// up, set things up — because an operator who knows one should not have to learn the
// other. Everything else hangs under a door rather than lining up beside it.
const TABS = ["gen", "runs", "cfg", "models"];
document.querySelectorAll("nav [data-tab]").forEach((b) => {
  b.onclick = () => openTab(b.dataset.tab);
});
function openTab(tab) {
  document.querySelectorAll("nav [data-tab]").forEach((x) =>
    x.classList.toggle("on", x.dataset.tab === tab));
  TABS.forEach((t) => ($(`#tab-${t}`).hidden = t !== tab));
  if (tab === "gen" && !opts) loadOptions();
  if (tab === "runs") { if (!opts) loadOptions(); loadRuns(); }
  if (tab === "cfg" && !cfgSection) openCfg("fandoms");
  if (tab === "models") loadModels();
}

// The same sections the terminal's Configuration screen has, in the same order. The
// ones not carried over yet say so instead of being missing: a section that is simply
// absent reads as "slopgen cannot do this", which is the wrong thing to learn.
const CFG = [
  ["fandoms", "js.worlds", "world"],
  ["llm", "js.model-profiles", "list"],
  ["tts", "js.voice-engine", "tts"],
  ["voices", "js.cloned-voices", "voices"],
  ["keys", "js.api-keys", "keys"],
  ["characters", "js.characters", "list"],
  ["visuals", "js.footage-profiles", "list"],
  ["ads", "js.ad-contracts", "list"],
  ["accounts", "js.accounts", "list"],
  ["orchestration", "js.generator-chains", "orch"],
  ["presets", "js.presets", "list"],
];
let cfgSection = null;
const drawCfgMenu = () => ($("#cfg-menu").innerHTML = CFG.map(([k, key, how]) =>
  `<button data-cfg="${k}"${how ? "" : ' class="dim-btn"'}>${esc(lab(key))}</button>`)
  .join(""));
function wireCfgMenu() {
  drawCfgMenu();
  $("#cfg-menu").querySelectorAll("[data-cfg]").forEach((b) => {
    b.onclick = () => openCfg(b.dataset.cfg);
  });
}
function openCfg(key) {
  cfgSection = key;
  const entry = CFG.find(([k]) => k === key) || [key, key, null];
  const how = entry[2];
  $("#cfg-menu").querySelectorAll("[data-cfg]").forEach((b) =>
    b.classList.toggle("on", b.dataset.cfg === key));
  $("#cfg-fandoms").hidden = how !== "world";
  $("#cfg-keys").hidden = how !== "keys";
  $("#cfg-list").hidden = how !== "list";
  $("#cfg-tts").hidden = how !== "tts";
  $("#cfg-voices").hidden = how !== "voices";
  $("#cfg-orch").hidden = how !== "orch";
  $("#cfg-todo").hidden = !!how;
  if (how === "world") openSub(sub);
  else if (how === "keys") loadKeys();
  else if (how === "tts") loadTts();
  else if (how === "voices") loadVoices();
  else if (how === "orch") loadOrch();
  else if (how === "list") loadConfigs(key, lab(entry[1]));
  else $("#cfg-todo-title").textContent = lab(entry[1]);
}

// ------------------------------------------------------------------ API keys
async function loadKeys() {
  const keys = await api("/api/keys");
  $("#keys").innerHTML = keys.map((k) => `
    <div class="who" data-var="${esc(k.var)}">
      <div class="row"><b>${esc(k.var)}</b>
        <span class="dim">${k.set ? (k.count > 1 ? `${k.count} ${lab("js.keys")}` : lab("js.set")) : lab("js.empty")}</span>
        <span class="grow"></span></div>
      <div class="row">
        <input type="password" placeholder="${k.set ? lab("js.set-type-to-replace-it") : lab("js.paste-a-key")}">
        <button data-save class="ghost">${lab("js.save")}</button></div>
      <div class="dim">${esc(lab(k.what))}</div>
    </div>`).join("");
  $("#keys").querySelectorAll("[data-save]").forEach((b) => {
    b.onclick = async () => {
      const el = b.closest("[data-var]");
      const value = el.querySelector("input").value;
      const r = await api(`/api/keys/${encodeURIComponent(el.dataset.var)}`,
        { method: "PUT", headers: { "content-type": "application/json" },
          body: JSON.stringify({ value }) });
      say(r.set ? `${el.dataset.var} ${lab("js.saved")}` : `${el.dataset.var} ${lab("js.cleared")}`);
      loadKeys();
    };
  });
}

// ------------------------------------------------------------------ the voice
async function loadTts() {
  const d = await api("/api/tts");
  $("#tts-check").checked = d.check_reference;
  $("#tts-check").onchange = async () => {
    await api("/api/tts", { method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ check_reference: $("#tts-check").checked }) });
    say(lab("js.saved2"));
  };
  $("#engines").innerHTML = d.engines.map((e) => {
    // an engine you cannot run is worth showing and worth explaining — hiding it
    // teaches that slopgen has fewer voices than it has
    const missing = e.keys.filter((k) => !k.set).map((k) => k.var);
    const why = [];
    if (missing.length) why.push(`${lab("js.needs-a-key")} ${missing.join(", ")}`);
    if (e.models.length) why.push(`${lab("js.needs-weights")} ${e.models.join(", ")}`);
    const notes = [e.gives_timings ? lab("js.own-timings") : lab("js.timings-via-the-aligner"),
                   e.clones ? lab("js.can-clone") : null,
                   e.catalogue ? lab("js.own-voice-catalogue") : null].filter(Boolean);
    return `<div class="who${e.id === d.engine ? " active" : ""}" data-engine="${esc(e.id)}">
      <div class="row"><b>${esc(e.label)}</b>
        ${e.id === d.engine ? `<span class="pill-on">${lab("js.in-use")}</span>`
          : `<button data-use class="ghost">${lab("js.use-it")}</button>`}
        <span class="grow"></span></div>
      <div class="dim">${esc(e.description)}</div>
      <div class="dim">${esc(notes.join(" · "))}${why.length ? " — " + esc(why.join("; ")) : ""}</div>
    </div>`;
  }).join("");
  $("#engines").querySelectorAll("[data-use]").forEach((b) => {
    b.onclick = async () => {
      const id = b.closest("[data-engine]").dataset.engine;
      await api("/api/tts", { method: "PUT", headers: { "content-type": "application/json" },
        body: JSON.stringify({ engine: id }) });
      say(`${lab("js.voices")} ${id}`);
      loadTts();
    };
  });
}

async function loadVoices() {
  const vs = await api("/api/voices");
  $("#voices").innerHTML = vs.map((v) => `
    <div class="panel cfg-item" data-voice="${esc(v.name)}">
      <div class="row"><b>${esc(v.name)}</b>
        <span class="dim">${v.has_sample ? `${v.seconds} c` : lab("js.the-sample-is-gone")} · ${esc(v.lang)}</span>
        <span class="grow"></span>
        <button data-save class="primary">${lab("js.save")}</button>
        <button data-del class="ghost">${lab("js.delete")}</button></div>
      ${v.has_sample ? `<audio controls preload="none" src="${v.url}"></audio>` : ""}
      <label>${lab("js.what-the-sample-says-word-for-word")}
        <textarea data-f="text" rows="2">${esc(v.text)}</textarea></label>
      <div class="grid">
        <label>${lab("js.language")}<input data-f="lang" value="${esc(v.lang)}"></label>
        <label>${lab("js.sample-url-for-cloud-engines")}<input data-f="ref_url" value="${esc(v.ref_url)}"></label>
      </div>
    </div>`).join("") || `<p class="empty">${lab("js.no-voices-yet")}</p>`;
  $("#voices").querySelectorAll("[data-voice]").forEach((el) => {
    const name = el.dataset.voice;
    el.querySelector("[data-save]").onclick = async () => {
      const body = {};
      el.querySelectorAll("[data-f]").forEach((i) => (body[i.dataset.f] = i.value));
      await api(`/api/voices/${encodeURIComponent(name)}`, { method: "PUT",
        headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      say(`${name} ${lab("js.saved")}`);
    };
    el.querySelector("[data-del]").onclick = async () => {
      await api(`/api/voices/${encodeURIComponent(name)}`, { method: "DELETE" });
      say(`${name} ${lab("js.deleted")}`);
      loadVoices();
    };
  });
}

$("#voice-new").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/api/voices", { method: "POST", body: new FormData(e.target) });
    e.target.reset();
    say(lab("js.voice-added"));
    loadVoices();
  } catch (err) { say(err.message, true); }
};

// --------------------------------------------------------- generator chains
let orchData = null;

async function loadOrch() {
  orchData = await api("/api/orchestrations");
  const names = Object.keys(orchData.items);
  $("#or-items").innerHTML = names.map((n) => orchCard(n, orchData.items[n])).join("")
    || `<p class="empty">${lab("js.no-chains-yet")}</p>`;
  bindOrch();
  $("#or-add").onclick = async () => {
    const name = $("#or-new").value.trim();
    if (!name) { say(lab("js.needs-a-name"), true); return; }
    await api(`/api/orchestrations/${encodeURIComponent(name)}`, { method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ stages: [{ model: orchData.models[0], metric: "percent",
                                        amount: 100, clip_seconds: 0 }] }) });
    $("#or-new").value = "";
    loadOrch();
  };
}

function orchCard(name, cfg) {
  return `<div class="panel cfg-item" data-orch="${esc(name)}">
    <div class="row"><b>${esc(name)}</b>
      <span class="grow"></span>
      <button data-add-stage class="ghost">${lab("js.step")}</button>
      <button data-save class="primary">${lab("js.save")}</button>
      <button data-del class="ghost">${lab("js.delete")}</button></div>
    <div class="stages">${cfg.stages.map(orchStage).join("")}</div>
  </div>`;
}

function orchStage(st) {
  const sel = (f, list, v) => `<select data-s="${f}">${list.map((o) =>
    `<option${String(o) === String(v) ? " selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  return `<div class="stage-row">
    <label>${lab("js.generator")}${sel("model", orchData.models, st.model)}</label>
    <label>${lab("js.how-many")}<input type="number" step="any" data-s="amount" value="${esc(st.amount)}"></label>
    <label>${lab("js.of-what")}${sel("metric", orchData.metrics, st.metric)}</label>
    <label>${lab("js.clip-length-s")}<input type="number" step="any" data-s="clip_seconds" value="${esc(st.clip_seconds)}"></label>
    <label>${lab("js.keys2")}${sel("key_mode", orchData.key_modes, st.key_mode)}</label>
    <label>${lab("js.which-key")}<input data-s="key" value="${esc(st.key || "")}" placeholder="${lab("js.first")}"></label>
    <button data-drop class="ghost">×</button>
  </div>`;
}

function bindOrch() {
  $("#or-items").querySelectorAll("[data-orch]").forEach((el) => {
    const name = el.dataset.orch;
    const read = () => [...el.querySelectorAll(".stage-row")].map((row) => {
      const o = {};
      row.querySelectorAll("[data-s]").forEach((i) => {
        o[i.dataset.s] = i.type === "number" ? (i.value === "" ? 0 : +i.value) : i.value;
      });
      return o;
    });
    el.querySelector("[data-save]").onclick = async () => {
      try {
        await api(`/api/orchestrations/${encodeURIComponent(name)}`, { method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ stages: read() }) });
        say(`${name} ${lab("js.saved22")}`);
      } catch (e) { say(e.message, true); }
    };
    el.querySelector("[data-del]").onclick = async () => {
      await api(`/api/orchestrations/${encodeURIComponent(name)}`, { method: "DELETE" });
      say(`${name} ${lab("js.deleted2")}`);
      loadOrch();
    };
    el.querySelector("[data-add-stage]").onclick = () => {
      orchData.items[name] = { stages: read().concat([{ model: orchData.models[0],
        metric: "percent", amount: 100, clip_seconds: 0, key_mode: "rotate", key: "" }]) };
      $("#or-items").innerHTML = Object.keys(orchData.items)
        .map((n) => orchCard(n, orchData.items[n])).join("");
      bindOrch();
    };
    el.querySelectorAll("[data-drop]").forEach((b, i) => {
      b.onclick = () => {
        const stages = read();
        stages.splice(i, 1);
        orchData.items[name] = { stages };
        $("#or-items").innerHTML = Object.keys(orchData.items)
          .map((n) => orchCard(n, orchData.items[n])).join("");
        bindOrch();
      };
    });
  });
}

// ------------------------------------------------------- the list-shaped kinds
let cfgData = null;

async function loadConfigs(kind, label) {
  cfgData = await api(`/api/configs/${kind}`);
  $("#cl-title").textContent = label;
  const names = Object.keys(cfgData.items);
  $("#cl-items").innerHTML = names.map((n) => cfgCard(kind, n, cfgData.items[n])).join("")
    || `<p class="empty">${lab("js.nothing-here-yet")}</p>`;
  bindConfigs(kind);
  $("#cl-add").onclick = async () => {
    const name = $("#cl-new").value.trim();
    if (!name) { say(lab("js.needs-a-name"), true); return; }
    // an empty body validates into the model's own defaults, which is a better
    // starting point than a blank form the operator has to fill in blind
    await api(`/api/configs/${kind}/${encodeURIComponent(name)}`,
      { method: "PUT", headers: { "content-type": "application/json" }, body: "{}" });
    $("#cl-new").value = "";
    loadConfigs(kind, label);
  };
}

function cfgCard(kind, name, item) {
  const active = kind === "llm" && cfgData.active === name;
  return `<div class="panel cfg-item${active ? " active" : ""}" data-name="${esc(name)}">
    <div class="row"><b>${esc(name)}</b>
      ${active ? `<span class="pill-on">${lab("js.in-use")}</span>` : ""}
      ${kind === "llm" && !active ? `<button data-use class="ghost">${lab("js.use-it2")}</button>` : ""}
      <span class="grow"></span>
      <button data-save class="primary">${lab("js.save")}</button>
      <button data-del class="ghost">${lab("js.delete")}</button></div>
    <div class="grid">${cfgData.schema.map((f) => cfgField(f, item[f.name])).join("")}</div>
  </div>`;
}

// A config field's own name is the fallback, not the label: `price_cached` says
// nothing to somebody who has not read the model. The table is the same one the
// terminal reads, so both interfaces name the field identically.
const FIELD_LABELS = {
  provider: "js.provider", base_url: "js.api-address", model: "js.model",
  key_env: "js.key-variable", temperature: "js.temperature",
  web_search: "js.live-web-search", price_in: ["js.input-price", "js.m"],
  price_cached: ["js.cache-hit-price", "js.m"], price_out: ["js.output-price", "js.m"],
  appearance: "js.look", plurality: "js.how-many-of-them", visual_prompt: "js.compiled-look",
  dirty: "js.rebuild-the-look", age: "js.age",
  lang: "js.language", content_type: "js.content-type", visuals: "js.footage-profile",
  count: "js.how-many-videos", ad: "js.ad", push: "js.account",
  description: "js.description", url: "js.link", modes: "js.modes", platform: "js.platform",
  ref: "js.sample-file", text: "js.what-it-says", ref_url: "js.sample-url",
  source: "js.source", linkage: "js.binding", assets_dir: "js.file-folder",
  manual: "js.i-make-it-myself", ai_model: "js.ai-generator", interval_s: "js.photo-interval-s",
  motion: "js.photo-motion", continuous: "js.continuous-clip",
  enabled: "js.on", width_pct: "js.width", position: "js.position",
  background: "js.background", foreground: "js.foreground", name: "js.name",
};
// A price is two words around a unit — "input price, $/M" — so its entry is the
// pair, joined here rather than pre-joined in the table.
const fieldLabel = (n) => {
  const v = FIELD_LABELS[n];
  const own = Array.isArray(v) ? `${lab(v[0])} $${lab(v[1])}` : v ? lab(v) : n;
  return lab("cfg.f." + n, own);
};

function cfgField(f, v) {
  const id = `f-${f.name}`;
  const name = fieldLabel(f.name);
  if (f.kind === "bool")
    return `<label class="inline"><input type="checkbox" data-f="${f.name}"${v ? " checked" : ""}> ${esc(name)}</label>`;
  if (f.kind === "choice")
    return `<label>${esc(name)}<select data-f="${f.name}">${f.options.map((o) =>
      `<option${String(o) === String(v) ? " selected" : ""}>${esc(o)}</option>`).join("")}</select></label>`;
  if (f.kind === "number")
    return `<label>${esc(name)}<input type="number" step="any" data-f="${f.name}" value="${esc(v ?? 0)}"></label>`;
  if (f.kind === "list" || f.kind === "map")
    // lists and maps go through as JSON: they are rare, and a bespoke widget for each
    // would be more surface than the thing it edits
    return `<label class="wide">${esc(name)} <span class="dim">JSON</span>
      <textarea data-f="${f.name}" data-json rows="2">${esc(JSON.stringify(v ?? (f.kind === "list" ? [] : {})))}</textarea></label>`;
  return `<label>${esc(name)}<input data-f="${f.name}" value="${esc(v ?? "")}"></label>`;
}

function bindConfigs(kind) {
  $("#cl-items").querySelectorAll(".cfg-item").forEach((el) => {
    const name = el.dataset.name;
    el.querySelector("[data-save]").onclick = async () => {
      const body = {};
      let bad = null;
      el.querySelectorAll("[data-f]").forEach((inp) => {
        const f = inp.dataset.f;
        if (inp.type === "checkbox") body[f] = inp.checked;
        else if (inp.type === "number") body[f] = inp.value === "" ? 0 : +inp.value;
        else if (inp.dataset.json !== undefined) {
          try { body[f] = JSON.parse(inp.value || "null"); }
          catch { bad = f; }
        } else body[f] = inp.value;
      });
      if (bad) { say(`${bad}${lab("js.not-json")}`, true); return; }
      try {
        await api(`/api/configs/${kind}/${encodeURIComponent(name)}`,
          { method: "PUT", headers: { "content-type": "application/json" },
            body: JSON.stringify(body) });
        say(`${name} ${lab("js.saved")}`);
      } catch (e) { say(e.message, true); }
    };
    el.querySelector("[data-del]").onclick = async () => {
      await api(`/api/configs/${kind}/${encodeURIComponent(name)}`, { method: "DELETE" });
      say(`${name} ${lab("js.deleted")}`);
      openCfg(kind);
    };
    const use = el.querySelector("[data-use]");
    if (use) use.onclick = async () => {
      await api("/api/configs/llm/active", { method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ profile: name }) });
      say(`${lab("js.now-running")} ${name}`);
      openCfg(kind);
    };
  });
}

// inside a world: its lore, who is in it, and the pictures it is told out of
const SUBS = [["lore", "js.lore"], ["cast", "js.who-lives-here"],
              ["frames", "js.frame-base"]];
let sub = "lore";
const drawSubMenu = () => ($("#w-sub").innerHTML = SUBS.map(([k, key]) =>
  `<button data-sub="${k}"${k === sub ? ' class="on"' : ""}>${esc(lab(key))}</button>`)
  .join(""));
function wireSubMenu() {
  drawSubMenu();
  $("#w-sub").querySelectorAll("[data-sub]").forEach((b) => {
    b.onclick = () => openSub(b.dataset.sub);
  });
}
function openSub(which) {
  sub = which;
  $("#w-sub").querySelectorAll("[data-sub]").forEach((b) =>
    b.classList.toggle("on", b.dataset.sub === which));
  $("#w-lore").hidden = which !== "lore";
  $("#w-castbox").hidden = which !== "cast";
  $("#w-frames").hidden = which !== "frames";
  if (which === "frames") loadCards();
  else loadWorld();
}

async function loadWorlds() {
  const ws = await api("/api/worlds");
  const sel = $("#world");
  sel.innerHTML = ws.map((w) => `<option value="${w.name}">${w.name} · ${w.usable}/${w.cards}</option>`).join("");
  sel.onchange = () => { world = sel.value; openSub(sub); };
  world = ws.length ? ws[0].name : null;
}

// ---------------------------------------------------------------- the base
async function loadCards() {
  cards = await api(`/api/worlds/${encodeURIComponent(world)}/cards`);
  $("#cards").innerHTML = cards.map((c) => `
    <div class="card ${c.retired ? "retired" : ""}" data-name="${esc(c.name)}">
      <div class="thumb ${c.usable ? "" : "none"}"
           ${c.usable && c.kind === "image" ? `style="background-image:url('${c.url}')"` : ""}>
        ${c.usable ? "" : lab("js.no-picture-yet")}
        ${c.targets.length ? `<span class="pill">${c.targets.length}</span>` : ""}
        ${c.kind === "video" ? `<span class="pill">${lab("js.clip")}</span>` : ""}
      </div>
      <div class="meta"><b>${esc(c.name)}</b><span>${esc((c.description || "").slice(0, 60))}</span></div>
    </div>`).join("") || `<p class="empty">${lab("js.this-world-has-no-cards-yet")}</p>`;
  document.querySelectorAll("#cards .card").forEach((el) => {
    el.onclick = () => openCard(el.dataset.name);
  });
}

const drop = $("#drop");
drop.onclick = () => $("#upload").click();
$("#upload").onchange = (e) => e.target.files[0] && upload(e.target.files[0]);
["dragover", "dragenter"].forEach((k) => drop.addEventListener(k, (e) => {
  e.preventDefault(); drop.classList.add("over");
}));
["dragleave", "drop"].forEach((k) => drop.addEventListener(k, () => drop.classList.remove("over")));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
});

async function upload(file) {
  // no prompt() either: the description is what the matcher reads, so it wants the
  // editor's own field and a proper look at the picture, not a one-line modal
  const body = new FormData();
  body.append("file", file);
  body.append("description", file.name.replace(/\.[^.]+$/, ""));
  const c = await api(`/api/worlds/${encodeURIComponent(world)}/cards`, { method: "POST", body });
  await loadCards();
  say(lab("js.card-added-say-what-is-in-it"));
  openCard(c.name);
}

// ---------------------------------------------------------------- the editor
function openCard(name) {
  card = cards.find((c) => c.name === name);
  targets = card.targets.map((t) => ({ ...t }));
  sel = -1;
  $("#ed-name").textContent = card.name;
  $("#ed-descr").value = card.description || "";
  $("#ed-prompt").value = card.prompt || "";
  $("#ed-note").value = card.note || "";
  const img = $("#pic"), vid = $("#vid");
  // A card with no file has nothing to mark up: a crop target is a pair of
  // coordinates ON a picture, so drawing regions over an empty stage would be
  // measuring a thing that is not there. The card still opens — its texts are worth
  // editing while the picture is being made — but the geometry stays shut.
  $("#stage").classList.toggle("nofile", !card.usable);
  $("#nofile").hidden = card.usable;
  if (!card.usable) {
    img.hidden = vid.hidden = true;
    $("#boxes").innerHTML = "";
    $("#safe").hidden = true;
    listTargets();
    $("#editor").hidden = false;
    return;
  }
  if (card.kind === "video") {
    vid.src = card.url; vid.hidden = false; img.hidden = true; vid.play().catch(() => {});
  } else {
    img.src = card.url; img.hidden = false; vid.hidden = true;
  }
  $("#editor").hidden = false;
  drawTargets();
}
$("#close").onclick = () => { $("#editor").hidden = true; $("#vid").pause(); };

// The stage is letterboxed AND the picture is centre-cropped to the video's aspect
// before the pipeline crops anything out of it. So a region's fractions are of the
// SURVIVING part, not of the file — which is what this returns. Marking on the whole
// picture would quietly place regions in the strips that get cut away.
function frame() {
  const el = card && card.kind === "video" ? $("#vid") : $("#pic");
  const nw = el.naturalWidth || el.videoWidth || 1;
  const nh = el.naturalHeight || el.videoHeight || 1;
  const box = el.getBoundingClientRect(), st = $("#stage").getBoundingClientRect();
  const s = Math.min(box.width / nw, box.height / nh) || 1;
  const pw = nw * s, ph = nh * s;
  // the picture is letterboxed inside its element, so the element's corner is not the
  // picture's corner — this holds whether the element is sized to the picture or the
  // picture is `contain`ed inside a full-size element
  const shown = { x: box.left - st.left + (box.width - pw) / 2,
                  y: box.top - st.top + (box.height - ph) / 2, w: pw, h: ph };
  // the centre crop: cover the video's aspect, keep the middle
  let w = shown.w, h = shown.h;
  if (w / h > videoAspect) w = h * videoAspect; else h = w / videoAspect;
  return { x: shown.x + (shown.w - w) / 2, y: shown.y + (shown.h - h) / 2, w, h,
           cropped: Math.abs(shown.w * shown.h - w * h) > 1 };
}

function drawSafe() {
  const f = frame(), el = $("#safe");
  el.hidden = !f.cropped;
  if (f.cropped) {
    Object.assign(el.style, { left: f.x + "px", top: f.y + "px",
                              width: f.w + "px", height: f.h + "px" });
    el.dataset.note = lab("js.the-dark-part-is-cropped-the-frame-is-fi");
  }
}

function drawTargets() {
  drawSafe();
  const f = frame();
  $("#boxes").innerHTML = targets.map((t, i) => {
    const w = t.scale * f.w, h = t.scale * f.h;
    return `<div class="box ${i === sel ? "sel" : ""}" data-i="${i}"
      style="left:${f.x + t.cx * f.w - w / 2}px;top:${f.y + t.cy * f.h - h / 2}px;
             width:${w}px;height:${h}px"><span class="tag">${esc(t.label || lab("js.untitled"))}</span>
      <span class="grip"></span></div>`;
  }).join("");
  document.querySelectorAll(".box").forEach(bindBox);
  listTargets();
}

// Moving and resizing an existing region, which the `move` cursor has been promising
// since the first version without anything behind it.
function bindBox(b) {
  const i = +b.dataset.i;
  b.onpointerdown = (e) => {
    e.stopPropagation();
    sel = i;
    const grip = e.target.classList.contains("grip");
    const f = frame(), t = targets[i];
    const st = $("#stage").getBoundingClientRect();
    const from = { x: e.clientX - st.left, y: e.clientY - st.top, cx: t.cx, cy: t.cy, s: t.scale };
    b.setPointerCapture(e.pointerId);
    const move = (ev) => {
      const x = ev.clientX - st.left, y = ev.clientY - st.top;
      if (grip) {
        // the window keeps the video's shape, so one number is the whole of a resize
        const side = clamp(from.s + (x - from.x) / f.w * 2, 0.1, 1);
        t.scale = side;
      } else {
        t.cx = from.cx + (x - from.x) / f.w;
        t.cy = from.cy + (y - from.y) / f.h;
      }
      t.cx = clamp(t.cx, t.scale / 2, 1 - t.scale / 2);
      t.cy = clamp(t.cy, t.scale / 2, 1 - t.scale / 2);
      place(b, t, f);
    };
    const up = () => {
      b.removeEventListener("pointermove", move);
      b.removeEventListener("pointerup", up);
      drawTargets();
    };
    b.addEventListener("pointermove", move);
    b.addEventListener("pointerup", up);
    // Selection is applied by hand rather than by redrawing. Redrawing here was the
    // bug: it replaced the very element the pointer had just been captured on, so the
    // move and up listeners were left on a node no longer in the document and a drag
    // ended before it began. The list is rebuilt on release, when nothing is holding
    // anything.
    document.querySelectorAll(".box").forEach((x) => x.classList.toggle("sel", x === b));
    document.querySelectorAll("#targets li").forEach((li) =>
      li.classList.toggle("sel", +li.dataset.i === i));
  };
}

function place(b, t, f) {
  const w = t.scale * f.w, h = t.scale * f.h;
  Object.assign(b.style, { left: f.x + t.cx * f.w - w / 2 + "px",
                           top: f.y + t.cy * f.h - h / 2 + "px",
                           width: w + "px", height: h + "px" });
}

function listTargets() {
  const active = document.activeElement;
  const keep = active && active.tagName === "INPUT" && active.closest("#targets")
    ? { i: +active.closest("li").dataset.i, f: active.dataset.f,
        pos: active.selectionStart }
    : null;
  $("#targets").innerHTML = targets.map((t, i) => `
    <li class="${i === sel ? "sel" : ""}" data-i="${i}">
      <div class="row">
        <input data-f="label" value="${esc(t.label)}" placeholder="${lab("js.what-it-is")}">
        <input data-f="of" value="${esc(t.of)}" placeholder="${lab("js.whose-name")}" style="max-width:40%">
        <button data-del="${i}" class="ghost">×</button>
      </div>
      <div class="geo">cx ${t.cx.toFixed(3)} · cy ${t.cy.toFixed(3)} · ${(t.scale * 100).toFixed(0)}%</div>
    </li>`).join("") || `<li style="color:var(--dim);border:none;background:none;padding:0">
      ${lab("js.nothing-marked-up-yet-such-a-card-can-on")}</li>`;
  document.querySelectorAll("#targets input").forEach((inp) => {
    // Only the model and the one label on screen. Rebuilding the list here was the
    // first version, and it destroyed the very field being typed into — the caret
    // survived exactly one letter before the element under it was replaced.
    inp.oninput = () => {
      const i = +inp.closest("li").dataset.i;
      targets[i][inp.dataset.f] = inp.value;
      const tag = document.querySelector(`.box[data-i="${i}"] .tag`);
      if (tag && inp.dataset.f === "label") tag.textContent = inp.value || lab("js.untitled");
    };
  });
  document.querySelectorAll("#targets [data-del]").forEach((b) => {
    b.onclick = () => { targets.splice(+b.dataset.del, 1); sel = -1; drawTargets(); };
  });
  document.querySelectorAll("#targets li").forEach((li) => {
    li.onclick = (e) => { if (e.target.tagName !== "INPUT" && e.target.tagName !== "BUTTON") {
      sel = +li.dataset.i; drawTargets(); } };
  });
  if (keep) {
    const back = document.querySelector(`#targets li[data-i="${keep.i}"] input[data-f="${keep.f}"]`);
    if (back) { back.focus(); back.setSelectionRange(keep.pos, keep.pos); }
  }
}

// drag a new region
let dragFrom = null;
$("#stage").addEventListener("pointerdown", (e) => {
  if (e.target.closest(".box")) return;
  const st = $("#stage").getBoundingClientRect();
  dragFrom = { x: e.clientX - st.left, y: e.clientY - st.top };
  $("#stage").setPointerCapture(e.pointerId);
});
$("#stage").addEventListener("pointermove", (e) => {
  if (!dragFrom) return;
  const st = $("#stage").getBoundingClientRect();
  const r = band(e.clientX - st.left, e.clientY - st.top);
  const p = $("#preview");
  p.hidden = false;
  Object.assign(p.style, { left: r.x + "px", top: r.y + "px",
                           width: r.w + "px", height: r.h + "px" });
});

// The band grows from its CENTRE, and the centre is where the pointer went down. That
// is what a crop target actually is — Rect is a centre and a size, not two corners —
// and it is what the operator is doing: putting a finger on the thing, then pulling
// out to say how much around it to take. Dragging a corner would make them aim at a
// spot that is not in the picture yet.
//
// It is locked to the video's aspect from the first pixel too, so what is dragged is
// what will be shown. Dragging freely and squaring it up on release is the same
// picture arriving somewhere nobody pointed at.
function band(x, y) {
  const f = frame();
  const cx = clamp((dragFrom.x - f.x) / f.w, 0, 1);
  const cy = clamp((dragFrom.y - f.y) / f.h, 0, 1);
  // how big a window can be here at all: a centred one runs out at the nearest edge
  const room = 2 * Math.min(cx, 1 - cx, cy, 1 - cy);
  const reach = 2 * Math.max(Math.abs(x - dragFrom.x) / f.w, Math.abs(y - dragFrom.y) / f.h);
  const side = clamp(reach, 0, Math.max(Math.min(room, 1), 0.1));
  // only a centre pressed almost exactly on an edge gets nudged, and only far enough
  // to fit the smallest window zoompan will take
  const ccx = clamp(cx, side / 2, 1 - side / 2);
  const ccy = clamp(cy, side / 2, 1 - side / 2);
  const w = side * f.w, h = side * f.h;
  return { x: f.x + ccx * f.w - w / 2, y: f.y + ccy * f.h - h / 2,
           w, h, side, cx: ccx, cy: ccy };
}
$("#stage").addEventListener("pointerup", (e) => {
  if (!dragFrom) return;
  const st = $("#stage").getBoundingClientRect();
  const x = e.clientX - st.left, y = e.clientY - st.top;
  $("#preview").hidden = true;
  const r = band(x, y);
  dragFrom = null;
  if (r.side < 0.04) return;                     // a tap, not a drag
  const t = { label: "", of: "", cx: r.cx, cy: r.cy, scale: Math.max(0.1, r.side) };
  targets.push(t); sel = targets.length - 1; drawTargets();
  const inp = document.querySelector(`#targets li[data-i="${sel}"] input`);
  if (inp) inp.focus();
});
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
addEventListener("resize", () => !$("#editor").hidden && drawTargets());
$("#pic").onload = drawTargets;

$("#save").onclick = async () => {
  const body = {
    description: $("#ed-descr").value, prompt: $("#ed-prompt").value,
    note: $("#ed-note").value, targets,
  };
  const updated = await api(`/api/worlds/${encodeURIComponent(world)}/cards/${encodeURIComponent(card.name)}`,
    { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  Object.assign(card, updated);
  const s = $("#saved"); s.textContent = lab("js.saved2"); s.classList.add("show");
  setTimeout(() => s.classList.remove("show"), 1400);
  loadCards();
};

// Preview the move the pipeline would make between the first two regions. It is a
// straight interpolation of the crop rectangle, which is what the renderer does too —
// so what you see here is the shape of the real thing, not an impression of it.
$("#playmove").onclick = () => {
  if (targets.length < 2) { say(lab("js.at-least-two-regions-are-needed"), true); return; }
  const [a, b] = targets, f = frame(), box = $("#preview");
  box.hidden = false;
  const t0 = performance.now(), dur = 2600, lead = 0.39, span = 0.33;
  (function step(now) {
    const t = Math.min((now - t0) / dur, 1);
    const p = clamp((t - lead) / span, 0, 1);      // hold → move → hold
    const cx = a.cx + (b.cx - a.cx) * p, cy = a.cy + (b.cy - a.cy) * p;
    const s = a.scale + (b.scale - a.scale) * p;
    box.style.left = f.x + (cx - s / 2) * f.w + "px";
    box.style.top = f.y + (cy - s / 2) * f.h + "px";
    box.style.width = s * f.w + "px";
    box.style.height = s * f.h + "px";
    if (t < 1) requestAnimationFrame(step); else setTimeout(() => (box.hidden = true), 300);
  })(t0);
};

// ---------------------------------------------------------------- the world itself
let worldInfo = null, openDoc = null;

async function loadWorld() {
  if (!world) return;
  worldInfo = await api(`/api/worlds/${encodeURIComponent(world)}`);
  $("#w-stale").textContent = worldInfo.canon
    ? (worldInfo.canon_stale ? lab("js.the-canon-is-stale-it-will-be-rebuilt-on") : lab("js.the-canon-is-fresh"))
    : lab("js.the-canon-is-not-built-yet");
  $("#w-doc").innerHTML = worldInfo.docs.map((d) => `<option>${esc(d)}</option>`).join("")
    || `<option value="">${lab("js.no-documents")}</option>`;
  $("#w-doc").onchange = () => showDoc($("#w-doc").value);
  if (worldInfo.docs.length) showDoc(worldInfo.docs[0]);
  else { $("#w-doc-text").value = ""; openDoc = null; }

  $("#w-cast").innerHTML = worldInfo.cast.map((c) => `
    <div class="who" data-who="${esc(c.name)}">
      <div class="row"><b>${esc(c.name)}</b>
        <span class="dim">${c.dirty ? lab("js.the-look-will-be-rebuilt") : (c.has_look ? lab("js.look-ready") : lab("js.look-not-built"))}</span>
        <span class="grow"></span>
        <select data-f="plurality">
          ${["one", "many", "class"].map((k) =>
            `<option value="${k}"${k === c.plurality ? " selected" : ""}>${esc(word(k))}</option>`).join("")}
        </select>
        <button data-save class="ghost">${lab("js.save")}</button></div>
      <textarea data-f="appearance" rows="2" placeholder="${lab("js.what-it-looks-like")}">${esc(c.appearance)}</textarea>
    </div>`).join("") || `<p class="empty">${lab("js.nobody-has-been-described-in-this-world-")}</p>`;
  $("#w-cast").querySelectorAll("[data-save]").forEach((b) => {
    b.onclick = async () => {
      const el = b.closest(".who");
      const body = {
        appearance: el.querySelector("[data-f=appearance]").value,
        plurality: el.querySelector("[data-f=plurality]").value,
      };
      const r = await api(`/api/worlds/${encodeURIComponent(world)}/cast/${encodeURIComponent(el.dataset.who)}`,
        { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      say(r.dirty ? `${r.name}${lab("js.the-look-will-be-rebuilt-on-the-next-run")}` : `${r.name} ${lab("js.saved")}`);
      loadWorld();
    };
  });
}

async function showDoc(name) {
  if (!name) return;
  const d = await api(`/api/worlds/${encodeURIComponent(world)}/docs/${encodeURIComponent(name)}`);
  openDoc = name;
  $("#w-doc-text").value = d.text;
}

$("#w-doc-save").onclick = async () => {
  if (!openDoc) return;
  await api(`/api/worlds/${encodeURIComponent(world)}/docs/${encodeURIComponent(openDoc)}`,
    { method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: $("#w-doc-text").value }) });
  say(lab("js.document-saved-the-canon-will-be-rebuilt"));
  loadWorld();
};

// ---------------------------------------------------------------- starting a run
let opts = null;
// The labels come from the core table the terminal reads, so both interfaces say the
// same words for the same field. `data-l` marks an element whose first text node is a
// label; everything else on the page is written in place.

// The words the pipeline uses for its own moments and settings. They are identifiers
// in the code and stay identifiers on the wire — only what is SHOWN is translated,
// and the translation lives in the label table with everything else rather than in a
// map here, so switching the interface language reaches these too.
const MOVE_KINDS = ["hold", "push_in", "drift", "zoom_in", "zoom_out", "pan"];
const word = (w) => lab("w." + w, w);

// An info line is assembled in the pipeline out of its own vocabulary — a move kind,
// a fit grade, a stage name. Translating it word by word here keeps those words as
// identifiers everywhere they matter, and readable only where they are read.
const humanise = (text) =>
  String(text).replace(/[a-z_]+/g, (w) =>
    lab(MOVE_KINDS.includes(w) ? "mv." + w : "w." + w, w));

// Fields the terminal only shows once something else is set. Showing them always is
// not merely noise: an ad mode with no ad contract, or a per-part toggle on a
// one-part drama, invite an answer to a question that is not being asked.
function applyConditions(form) {
  queueMicrotask(compose);
  form.querySelectorAll("[data-when]").forEach((el) => {
    const m = el.dataset.when.match(/^([a-z_]+)\s*(!=|[=><])\s*(.+)$/);
    if (!m) return;
    const [, name, op, value] = m;
    const src = form.querySelector(`[name="${name}"]`);
    if (!src) return;
    const v = src.type === "checkbox" ? (src.checked ? "1" : "") : src.value;
    let on;
    // `!=` is what a checkbox needs: asking where to publish is only worth doing while
    // the run is NOT a rehearsal, and that is a condition on something being off.
    if (op === "=") on = value === "*" ? !!v : value.split("|").includes(v);
    else if (op === "!=") on = value === "*" ? !v : !value.split("|").includes(v);
    else if (op === ">") on = +v > +value;
    else on = +v < +value;
    el.hidden = !on;
  });
}

// re-evaluate whenever anything in a form changes; cheap, and it means a condition
// never has to remember to wire up its own listener
document.querySelectorAll("form.cards").forEach((form) => {
  form.addEventListener("change", () => applyConditions(form));
  form.addEventListener("input", () => applyConditions(form));
});

// Put the launch card on the LAST row of the last column. Grid cannot be told "last
// row" — the row count is implicit and only known once everything is placed — so it
// is measured after layout and pinned. Re-measured on resize, because the column
// count changes with the width and the last row moves with it.
// ------------------------------------------------------------------ the compositor
//
// Two halves. `buildCards` turns a card spec into DOM; `compose` decides where each
// card goes. Neither knows what the other's cards are for, which is the point: adding
// a setting means adding a line to `forms.js`, and nothing gets placed by hand.

function fieldHTML(f) {
  if (Array.isArray(f) && f[0] === "row2")
    return `<div class="row2">${f.slice(1).map(fieldHTML).join("")}</div>`;
  if (f.when)
    return `<div data-when="${esc(f.when)}">${f.rows.map(fieldHTML).join("")}</div>`;
  if (f.slot) {  // an empty box some other code fills in, with whatever it is found by
    const at = Object.entries(f).filter(([k]) => k !== "slot")
      .map(([k, v]) => `${k}="${esc(v)}"`).join(" ");
    return `<div ${at}></div>`;
  }
  if (f.note !== undefined)
    return `<p class="${esc(f.cls || "dim")}"${f.note ? ` data-l="${esc(f.note)}"` : ""}></p>`;

  const at = [f.id && `id="${esc(f.id)}"`, f.cls && `class="${esc(f.cls)}"`,
              f.ph && `data-lp="${esc(f.ph)}"`].filter(Boolean).join(" ");
  const name = `name="${esc(f.f)}"`;
  // A checkbox reads box-then-words, and the words need somewhere of their own to
  // live: an <input> is void, so a label key on the box itself has nothing to fill.
  if (f.inline)
    return `<label class="inline"${f.when1 ? ` data-when="${esc(f.when1)}"` : ""}>` +
           `<input type="${esc(f.kind)}" ${name}${f.checked ? " checked" : ""} ${at}>` +
           `<span data-l="${esc(f.l || "")}"></span></label>`;
  // A condition can sit on the field itself as well as on a group of them — `data-when`
  // is read off whatever element carries it, so both spellings work.
  const label = (f.l ? ` data-l="${esc(f.l)}"` : "") +
                (f.when1 ? ` data-when="${esc(f.when1)}"` : "");
  const dose = f.dose
    ? ` <span class="dose${f.dose.cls ? " " + esc(f.dose.cls) : ""}"` +
      `${f.dose.id ? ` id="${esc(f.dose.id)}"` : ""}>${esc(f.dose.v ?? "0")}</span>`
    : "";
  let control;
  if (f.kind === "select") control = `<select ${name} ${at}></select>`;
  else if (f.kind === "text" && f.rows)
    control = `<textarea ${name} rows="${f.rows}" ${at}></textarea>`;
  else {
    const num = ["min", "max", "step", "value"]
      .filter((k) => f[k] !== undefined).map((k) => `${k}="${esc(f[k])}"`).join(" ");
    control = `<input type="${esc(f.kind === "text" ? "text" : f.kind)}" ${name} ${num} ${at}>`;
  }
  return `<label${label}>${dose}${control}</label>`;
}

function cardHTML(c) {
  const launch = (c.cls || []).includes("launch");
  const head = launch
    ? `<b data-l="${esc(c.title)}"></b><span class="dim" data-l="${esc(c.sub || "")}"></span>`
    : `<h4 data-l="${esc(c.title)}"></h4>`;
  const foot = c.go
    ? `<button class="${esc(c.gocls || "primary")}" data-l="${esc(c.go)}"></button>` : "";
  return `<div class="${["card", ...(c.cls || [])].join(" ")}">` +
         `${head}${(c.rows || []).map(fieldHTML).join("")}${foot}</div>`;
}

// Built once, before anything fills a select or wires a handler: those all go by name
// and id, and find exactly what the hand-written markup used to give them.
function buildCards() {
  for (const [mode, cards] of Object.entries(FORMS)) {
    const form = document.querySelector(`form.cards[data-mode="${mode}"]`);
    if (form) form.innerHTML = cards.map(cardHTML).join("");
  }
}

// `compose` — where each card goes.
//
// The panel is a tiling: one spacing in both directions, columns that end within a
// card's edge of each other, and no corner left standing empty. CSS auto-flow cannot
// produce it — in source order a card may only start at or below the row the previous
// one started, so a short column keeps its hole forever, and `dense` fills holes by
// reordering the cards, which moves settings around under the operator's hands
// between one screen width and the next.
//
// So placement is decided here, and it is a SEARCH rather than a rule. Dropping each
// card into whichever column is shortest so far is the obvious rule, and it is what
// left a quarter of the panel empty: it commits a column before it knows a tall card
// is still coming, and nothing later can undo the commitment. A beam over the cards,
// scored on where the deepest column ends, looks far enough ahead to avoid that.
//
// Rows are a hairline tall and a card spans as many as its content comes to, so a
// height is a height and never a multiple of some row size. Nothing is stretched to
// square the bottom off: the slack would go INSIDE a card, and a settings card with
// two fields and four hundred pixels of nothing under them reads as broken. Balance
// is what keeps the bottom edge tidy — not padding.

// Deepest column first: a layout that ends one card lower is worse than a ragged one
// that ends higher. Then the spread, so among layouts of the same height the tidy
// edge wins. Then leftmost-first, which is not cosmetic — it is what stops two
// equally good packings from swapping the same two cards between one width and the
// next, and what keeps the first card in the form at the top left where it is read.
const deepest = (s) => Math.max(...s.foot);

// What stretching can close for a given packing, and what it cannot.
//
// A column that ends short has slack, and the slack is spread over EVERY card in that
// column rather than dumped into the one at the foot of it. Same total emptiness
// either way, but forty pixels added to each of five cards is invisible while two
// hundred added to one reads as a card that failed to load.
//
// A card spanning two columns takes the smaller of the two shares, because taking the
// larger would push its own column past the floor. That leaves a remainder, which the
// next pass hands to the one-column cards; a few passes and there is nothing left but
// rounding.
//
// What no amount of stretching closes is a wide card that is the foot of one of its
// columns while a neighbour sits below it in the other: it may not grow past that
// neighbour, so the strip beside it stays open — the empty band the operator found
// under a card with a dragged-out textarea. Only a different packing fixes that one,
// so the residue is measured here and the packing is chosen by it.
function fill(st) {
  const cols = st.foot.length;
  const n = st.h.length;
  // Within a column, cards stack in the order the beam placed them.
  const order = [...st.h.keys()].sort((x, y) => st.top[x] - st.top[y] || st.at[x] - st.at[y]);
  const grown = st.h.slice();
  const ends = (i) => st.at[i] + st.wide[i];

  const lay = () => {
    const depth = new Array(cols).fill(0);
    const top = new Array(n);
    for (const i of order) {
      let t = 0;
      for (let c = st.at[i]; c < ends(i); c++) t = Math.max(t, depth[c]);
      top[i] = t;
      for (let c = st.at[i]; c < ends(i); c++) depth[c] = t + grown[i];
    }
    return { top, depth, floor: Math.max(...depth) };
  };

  const share = new Array(cols).fill(0);
  for (let i = 0; i < n; i++) for (let c = st.at[i]; c < ends(i); c++) share[c]++;

  let out = lay();
  for (let pass = 0; pass < 40; pass++) {
    const slack = out.depth.map((d) => out.floor - d);
    if (Math.max(...slack) < 1) break;
    let any = false;
    for (let i = 0; i < n; i++) {
      let add = Infinity;
      for (let c = st.at[i]; c < ends(i); c++) add = Math.min(add, slack[c] / share[c]);
      if (add >= 1) { grown[i] += Math.floor(add); any = true; }
    }
    if (!any) break;
    out = lay();
  }

  // Whatever the rounding leaves goes to the card at the foot of each column. By this
  // point that is a pixel or two, not a lump — and it is also what closes the last gap
  // when a blocked wide card kept the passes above from converging.
  for (let i = 0; i < n; i++) {
    let stop = out.floor;
    for (let k = 0; k < n; k++) {
      if (k === i) continue;
      const apart = st.at[k] + st.wide[k] <= st.at[i] || st.at[i] + st.wide[i] <= st.at[k];
      if (apart || out.top[k] < out.top[i] + grown[i]) continue;
      stop = Math.min(stop, out.top[k]);
    }
    grown[i] = Math.max(grown[i], stop - out.top[i]);
  }
  out = lay();

  const covered = grown.reduce((a, g, i) => a + g * st.wide[i], 0);
  // The sum of SQUARES of what each card had to stretch, which is smallest when the
  // stretching is spread thin. The total is fixed once the floor is — it is the panel
  // area minus the cards' own — so the only thing left to choose is whether it lands
  // as a little padding under everything or as one card standing half empty next to a
  // tall neighbour. The square is what makes the search prefer the first.
  const even = grown.reduce((a, g, i) => a + (g - st.h[i]) ** 2, 0);
  return { floor: out.floor, top: out.top, grown, even,
           hole: out.floor * cols - covered };
}
function tidier(a, b) {
  const d = deepest(a) - deepest(b);
  if (d) return d;
  const spread = (deepest(a) - Math.min(...a.foot)) - (deepest(b) - Math.min(...b.foot));
  if (spread) return spread;
  for (let i = 0; i < a.at.length; i++) if (a.at[i] !== b.at[i]) return a.at[i] - b.at[i];
  return 0;
}

function compose(root = document) {
  let moved = false;
  root.querySelectorAll(".cards").forEach((grid) => {
    // A hidden grid measures every card at zero, and a card one hairline tall is a
    // card the others are drawn on top of. Leave it as it is; showing it recomposes.
    if (!grid.offsetParent) return;
    const all = [...grid.querySelectorAll(":scope > .card")].filter((c) => !c.hidden);
    if (!all.length) return;
    // Released FIRST, and not only so the heights measure true: a card still holding
    // `grid-column: 4` from a wider screen makes the browser invent columns to put it
    // in, and those inventions are what the track count would otherwise be read from.
    // Back into the grid's flow to be measured: a card still holding a position from
    // a wider screen measures whatever that screen gave it.
    all.forEach((c) => {
      c.style.position = c.style.top = c.style.left = c.style.width = "";
      c.style.gridColumn = c.style.gridRow = c.style.minHeight = c.style.height = "";
    });
    const cs = getComputedStyle(grid);
    const track = cs.gridTemplateColumns.split(" ").filter(Boolean).map(parseFloat);
    const cols = track.length;
    const gap = parseFloat(cs.columnGap) || 0;
    if (!cols || !track.every((w) => w > 0)) return;
    // getBoundingClientRect measures what is PAINTED; getComputedStyle and the styles
    // written back are CSS pixels. A CSS `zoom` anywhere up the tree makes those two
    // different units, and mixing them puts every card a few percent out — enough to
    // eat the gap and stack cards on top of each other. Everything below is CSS px.
    const scale = grid.getBoundingClientRect().width / parseFloat(cs.width) || 1;

    // Width is settled before any height is read, because the height DEPENDS on it:
    // the filter card lays its switches out in as many inner columns as it is given,
    // and stands 527px tall in one track against 291px across two. A card measured at
    // one width and placed at another is a card that overlaps its neighbour.
    const rowsAt = (c, wide) => {
      c.style.gridColumn = `span ${wide}`;
      return Math.max(1, Math.ceil(c.getBoundingClientRect().height / scale + gap));
    };
    const items = all.map((c) => {
      const launch = c.classList.contains("launch");
      const wide = cols >= 2 && c.classList.contains("w2") ? 2 : 1;
      // The launch card is the one whose width is worth searching over. It is pinned
      // to the right edge, so a wide card ending above it cannot grow past it and the
      // strip beside it stays open — which is exactly the empty band that showed up at
      // two columns. Letting the button take the same width closes it, and the hole
      // count below decides whether it needs to.
      const opts = launch && cols >= 2
        ? [{ wide: 1, h: rowsAt(c, 1) }, { wide: 2, h: rowsAt(c, 2) }]
        : [{ wide, h: rowsAt(c, wide) }];
      return { card: c, launch, opts };
    });

    // The beam: each step tries the next card in every column it could start in and
    // keeps the most promising states. Small enough to be near-exhaustive — a dozen
    // cards, four columns — and it is the lookahead that the greedy rule lacked.
    const BEAM = 200;
    let beam = [{ foot: new Array(cols).fill(0), at: [], top: [], wide: [], h: [] }];
    for (const it of items) {
      const next = [];
      for (const st of beam) {
        for (const o of it.opts) {
          if (o.wide > cols) continue;
          // The launch card is the form's submit and belongs at the bottom right, so
          // its column is not up for discussion — only what is stacked above it.
          const from = it.launch ? cols - o.wide : 0;
          // Only the LEFTMOST column offering a given height is tried. Two columns a
          // card would sit at the same height in are the same choice as far as the
          // packing goes, and taking the right-hand one is how the first card in the
          // form ended up in the top right corner with the sixth card beside it.
          // Reading order is not a tie-break to apply at the end — it has to be built
          // into which placements are considered at all.
          const offered = new Set();
          for (let x = from; x + o.wide <= cols; x++) {
            const top = Math.max(...st.foot.slice(x, x + o.wide));
            if (offered.has(top)) continue;
            offered.add(top);
            const foot = st.foot.slice();
            for (let k = x; k < x + o.wide; k++) foot[k] = top + o.h;
            next.push({ foot, at: [...st.at, x], top: [...st.top, top],
                        wide: [...st.wide, o.wide], h: [...st.h, o.h] });
          }
        }
      }
      next.sort(tidier);
      // Two states with the same column depths pose the same problem from here on, so
      // only the better of them is worth carrying. Without this the beam fills up with
      // two hundred spellings of one future and stops looking ahead at all.
      const seen = new Set();
      beam = [];
      for (const st of next) {
        // Width is part of the key: two states that reach the same depths by giving
        // the launch card different widths are NOT the same future.
        const key = st.foot.join(",") + "|" + st.wide.join(",");
        if (seen.has(key)) continue;
        seen.add(key);
        beam.push(st);
        if (beam.length >= BEAM) break;
      }
    }

    // The beam prunes on panel height, so every state left is about as short as the
    // shortest. Among those the one to take is the one stretching can leave FLAT —
    // holes come from the shape of the packing, not from its height, so a state that
    // ends level everywhere is available at no cost in height almost every time.
    let best = beam[0], laid = fill(best);
    for (const st of beam) {
      const f = fill(st);
      const better = f.hole !== laid.hole ? f.hole < laid.hole
                   : f.floor !== laid.floor ? f.floor < laid.floor
                   : f.even < laid.even;
      if (better) {
        best = st;
        laid = f;
      }
    }

    // Placed by hand, in pixels, off the grid's own column geometry. The tracks are
    // whatever `auto-fill` decided, so the columns still follow the window; only the
    // vertical placement is taken away from the grid, for the rounding reason above.
    const leftOf = (x) => track.slice(0, x).reduce((a, w) => a + w + gap, 0);
    const widthOf = (x, n) =>
      track.slice(x, x + n).reduce((a, w) => a + w, 0) + (n - 1) * gap;

    items.forEach((it, i) => {
      const c = it.card;
      const box = [`${leftOf(best.at[i])}px`, `${widthOf(best.at[i], best.wide[i])}px`,
                   `${laid.top[i]}px`];
      if (c.style.left !== box[0] || c.style.width !== box[1] || c.style.top !== box[2]) {
        moved = true;
      }
      c.style.position = "absolute";
      [c.style.left, c.style.width, c.style.top] = box;
      // A MINIMUM, never a fixed height. The card has to fill its slot so the space
      // under it is the gap and nothing else — but a fixed height also freezes it, and
      // a textarea dragged taller by its grip then grows straight out through the
      // bottom of the card it is in. As a minimum the card follows its content, and
      // the recompose that follows re-tiles around the new size.
      c.style.minHeight = `${laid.grown[i] - gap}px`;
    });
    // Nothing is in the flow any more, so the panel has no height of its own.
    grid.style.height = `${laid.floor - gap}px`;
  });
  return moved;
}



addEventListener("resize", () => compose());

// A card changes height for reasons that are not a window resize: a conditional field
// appearing, a select filling in, a notice showing up. Watching the cards themselves
// catches all of those without every one of those places having to remember to ask.
// `compose` sets heights, which the observer sees as a resize like any other. The
// guard against that used to DROP whatever arrived while a pass was running — and a
// textarea dragged taller arrives exactly then, so the field kept its new height, the
// card kept its old one, and the text grew out through the bottom of the card. Now a
// dropped event is remembered instead, and `compose` says whether it actually moved
// anything, so the follow-up passes stop on their own rather than at some fixed count.
const recompose = new ResizeObserver(() => {
  if (recompose._busy) { recompose._again = true; return; }
  recompose.soon();
});
recompose.soon = () => {
  clearTimeout(recompose._t);
  recompose._t = setTimeout(() => {
    recompose._busy = true;
    recompose._again = false;
    const moved = compose();
    setTimeout(() => {
      recompose._busy = false;
      if (moved && recompose._again) recompose.soon();
    }, 0);
  }, 0);
};
// Dragging a textarea's grip is the one size change on this page a person makes by
// hand, and it is the one the layout has to follow. It cannot be left to the
// ResizeObserver: observer callbacks are delivered off the rendering loop, exactly
// like animation frames, and a window that is not being painted — backgrounded,
// occluded, minimised — delivers none of them at all, not even the initial one. That
// is the same trap that once left the whole panel unplaced. Pointer events arrive
// either way, so they drive this and the observer is only the backup.
let dragging = null, dragAt = 0;
addEventListener("pointerdown", (e) => {
  if (e.target.tagName === "TEXTAREA" && e.target.closest(".cards .card")) dragging = e.target;
}, true);
addEventListener("pointermove", () => {
  // Throttled by the clock rather than by a frame, for the reason above. Re-tiling on
  // every move would spend a forced layout per card per event; a tenth of a second
  // keeps up with a drag and costs nothing.
  if (!dragging || Date.now() - dragAt < 100) return;
  dragAt = Date.now();
  compose();
}, true);
for (const done of ["pointerup", "pointercancel"]) {
  addEventListener(done, () => {
    if (!dragging) return;
    dragging = null;
    compose();
  }, true);
}

function watchCards(root = document) {
  root.querySelectorAll(".cards > .card").forEach((c) => recompose.observe(c));
  // Textareas too, and not for symmetry: `compose` gives every card an explicit
  // height, so dragging a textarea's resize grip makes the field taller WITHOUT
  // changing the card, and the field simply grows out through the bottom of it.
  // Watching the field is what turns that drag back into a recompose.
  root.querySelectorAll(".cards .card textarea").forEach((t) => recompose.observe(t));
}

function applyLabels(root = document) {
  root.querySelectorAll("[data-l]").forEach((el) => {
    const text = lab(el.dataset.l, null);
    if (!text) return;
    // Replace only the leading text, leaving whatever control follows it. A card built
    // from the spec has no text of its own — the label key IS the text — so one is put
    // in front of the control rather than nothing happening.
    const first = [...el.childNodes].find((n) => n.nodeType === 3 && n.textContent.trim());
    if (first) first.textContent = first.textContent.replace(/\S.*\S|\S/, text);
    else el.insertBefore(document.createTextNode(text), el.firstChild);
  });
  // A placeholder or a tooltip is text the operator reads exactly like a label; left
  // untranslated they are the half of the screen that stays in the other language.
  for (const [attr, key] of [["placeholder", "lp"], ["title", "lt"]]) {
    root.querySelectorAll(`[data-${key}]`).forEach((el) => {
      const text = lab(el.dataset[key], null);
      if (text) el.setAttribute(attr, text);
    });
  }
}
// one set of chosen breakpoints per mode: they are different lists (a drama has `cut`,
// a fandom has `picture`), so one shared set would carry a stage across to a mode that
// does not run it
const chosenBps = { fandom: new Set(), info: new Set(), drama: new Set() };
let genMode = "fandom";

document.querySelectorAll("#mode-menu [data-mode]").forEach((b) => {
  b.onclick = () => {
    // a loop's mode is the one thing about it that cannot change, so walking to
    // another mode's form is walking out of the edit
    if (editing && editing.mode !== b.dataset.mode) stopEditing();
    genMode = b.dataset.mode;
    document.querySelectorAll("#mode-menu [data-mode]").forEach((x) =>
      x.classList.toggle("on", x.dataset.mode === genMode));
    document.querySelectorAll("form[data-mode]").forEach((f) =>
      (f.hidden = f.dataset.mode !== genMode));
    compose();
  };
});

async function loadOptions() {
  opts = await api("/api/options");
  L = opts.labels || {};
  buildCards();
  applyLabels();
  wireCfgMenu();
  wireSubMenu();
  watchCards();
  const langs = $("#ui-lang");
  langs.innerHTML = [["ru", "Русский"], ["en", "English"]]
    .map(([v, n]) => `<option value="${v}"${v === opts.ui_lang ? " selected" : ""}>${n}</option>`).join("");
  // the interface language is about the page, not about anything a run does, so it
  // lives beside the doors rather than inside a settings screen
  langs.onchange = async () => {
    await api("/api/ui", { method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ lang: langs.value }) });
    location.reload();
  };
  // An entry is either a bare name or `{v, note}` — a config entry the operator wrote,
  // shown with the line they wrote about it. The value submitted is the name in both
  // cases, so nothing downstream can tell the difference.
  const fill = (sel, list, blank) => {
    if (!sel) return;
    sel.innerHTML = (blank ? [""] : []).concat(list).map((x) => {
      const v = x && x.v !== undefined ? x.v : x;
      const note = x && x.note ? ` — ${lab(x.key || "", x.note)}` : "";
      return `<option value="${esc(v)}">${esc(v ? word(v) + note : lab("w.none", "— нет —"))}</option>`;
    }).join("");
  };
  fill($("#f-world"), opts.worlds);
  fill($("#f-voice"), opts.voices);
  fill($("#f-fit"), opts.fits);
  $("#f-fit").value = "close";
  document.querySelectorAll(".f-lang, #f-lang").forEach((el) => fill(el, opts.languages));
  fill($("#i-type"), opts.content_types, true);
  fill($("#i-visuals"), opts.visuals);
  fill($("#d-orch"), opts.orchestrations, true);
  // by class, not by id: the same control exists in all three forms, and an id can
  // only ever name one of them — which is how these ended up empty after the rebuild
  document.querySelectorAll(".f-ad").forEach((el) => fill(el, opts.ads, true));
  document.querySelectorAll(".f-push").forEach((el) => fill(el, opts.accounts, true));
  // With no account configured there is nowhere to publish, so the pair of controls
  // about publishing governs nothing: the dropdown offers only "none", and the switch
  // that turns publishing off turns off something that was never going to happen. Say
  // so where the dropdown was, and keep the run a rehearsal.
  const canPublish = opts.accounts.length > 0;
  document.querySelectorAll(".f-push").forEach((el) => {
    const row = el.closest("label") || el;
    row.hidden = !canPublish;
    if (canPublish) return;
    let note = row.nextElementSibling;
    if (!note || !note.classList.contains("no-push")) {
      note = document.createElement("p");
      note.className = "dim no-push";
      row.after(note);
    }
    note.textContent = lab("js.nowhere-to-publish");
  });
  document.querySelectorAll('[name="dry_run"]').forEach((el) => {
    if (canPublish) return;
    el.checked = true;
    (el.closest("label") || el).hidden = true;
  });

  // the cast is chips too, for the same reason the breakpoints are: a short list of
  // names, picked several at a time
  $("#d-cast").innerHTML = opts.characters
    .map((c) => `<button type="button" data-who="${esc(c)}">${esc(c)}</button>`).join("")
    || `<span class="dim">${lab("js.no-characters-yet-add-them-in-the-config")}</span>`;
  $("#d-cast").querySelectorAll("[data-who]").forEach((b) => {
    b.onclick = () => b.classList.toggle("on");
  });

  // the breakpoints are chips rather than a multi-select: they are a short list of
  // named moments, and picking three out of a scrolling box is worse than tapping them
  document.querySelectorAll(".bps").forEach((box) => {
    const mode = box.dataset.for;
    box.innerHTML = (opts.breakpoints[mode] || [])
      .map((b) => `<button type="button" data-bp="${esc(b)}">${esc(word(b))}</button>`).join("");
    box.querySelectorAll("[data-bp]").forEach((b) => {
      b.onclick = () => {
        const k = b.dataset.bp, set = chosenBps[mode];
        set.has(k) ? set.delete(k) : set.add(k);
        b.classList.toggle("on", set.has(k));
      };
    });
  });
  // the loop's two choices. Both are the loop's whole vocabulary — who picks the topic,
  // what a parked video means — and both are changeable again while it runs.
  document.querySelectorAll(".f-loopsrc").forEach((el) => fill(el, ["ai", "me"]));
  document.querySelectorAll(".f-looppark").forEach((el) => fill(el, ["hold", "go_on"]));
  // the shared block: same controls in every mode, filled once
  document.querySelectorAll(".f-voice-pick").forEach((el) => fill(el, opts.cloned_voices, true));
  document.querySelectorAll(".f-tts").forEach((el) => fill(el, opts.tts_engines, true));
  document.querySelectorAll(".f-subs").forEach((el) => fill(el, opts.subtitle_styles, true));
  document.querySelectorAll(".f-admode").forEach((el) => {
    fill(el, opts.ad_modes);
    el.value = "both";  // the model's own default, not whatever sorts first
  });
  document.querySelectorAll(".fx-rows").forEach((box) => {
    // Not a <label>. Wrapping a range in one makes its name and its readout part of
    // the control's hit area, where a click does nothing — which is exactly what a
    // dead zone at either end of the slider feels like. The name is a plain span, and
    // only the track is clickable.
    box.innerHTML = opts.filters.map((f) => `
      <div class="slider" title="${esc(f.note)}">
        <div class="top"><span>${esc(lab("fx." + f.key, f.key))}</span>
          <span class="grow"></span><span class="dose">0</span></div>
        <input type="range" data-fx="${esc(f.key)}" min="0" max="100" step="5" value="0">
      </div>`).join("");
    box.querySelectorAll("[data-fx]").forEach((r) => {
      const out = r.previousElementSibling.querySelector(".dose");
      r.oninput = () => (out.textContent = r.value);
    });
  });
  // the plain sliders each own their readout the same way
  document.querySelectorAll('input[type=range][name="tts_rate"]').forEach((r) => {
    const out = r.closest(".card").querySelector(".rate-val");
    r.oninput = () => (out.textContent = r.value);
  });
  document.querySelectorAll('input[type=range][name="profanity"]').forEach((r) => {
    const out = r.closest(".card").querySelector(".prof-val");
    r.oninput = () => (out.textContent = r.value);
  });
  document.querySelectorAll("form.cards").forEach(applyConditions);
  $("#f-sens").oninput = () => ($("#sens-val").textContent = $("#f-sens").value);

  // what a fandom's picture is made of, and where it comes from — the choice that was
  // missing entirely, so the frame base could only be reached by hardcoding it
  const medium = $("#f-medium"), source = $("#f-source");
  fill(medium, ["photo", "video"]);
  medium.onchange = () => {
    const list = medium.value === "video" ? opts.video_sources : opts.photo_sources;
    fill(source, list);
    source.value = medium.value === "video" ? "wan2.1" : "frames";
    source.onchange();
  };
  source.onchange = () => {
    const note = lab("src." + source.value, lab("src.auto", ""));
    document.querySelectorAll("#startform .src-note").forEach((el) => (el.textContent = note));
    applyConditions($("#startform"));
  };
  medium.onchange();

  // Last, once every select is filled and every conditional row has been settled: the
  // compositor measures cards, and a card measured before its contents arrived is a
  // card placed for a size it no longer has.
  compose();
}

$("#startform").onsubmit = async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = {
    fandom: f.get("fandom"), voice: f.get("voice"), lang: f.get("lang") || "ru",
    medium: f.get("medium"), source: f.get("source"),
    scenario: f.get("scenario"), title: f.get("title"),
    duration_s: +f.get("duration_s"), count: +f.get("count"),
    dry_run: f.get("dry_run") === "on",
    frame_fit: f.get("frame_fit"), cut_sensitivity: +f.get("cut_sensitivity"),
    breakpoints: [...chosenBps.fandom],
    ...commonOf(e.target),
  };
  if (editing && editing.mode === "fandom") return applyToLoop(body);
  let out;
  try {
    out = await api("/api/runs/fandom", { method: "POST",
      headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  } catch (err) { say(err.message, true); return; }
  started(out);
};

$("#infoform").onsubmit = (e) => submitRun(e, "info", (f) => ({
  lang: f.get("lang"), content_type: f.get("content_type"), visuals: f.get("visuals"),
  idea: f.get("idea"), title: f.get("title"),
  duration_s: +f.get("duration_s"), count: +f.get("count"),
  profanity: +f.get("profanity"), ad: f.get("ad"), push: f.get("push"),
  dry_run: f.get("dry_run") === "on", breakpoints: [...chosenBps.info],
}));

$("#dramaform").onsubmit = (e) => submitRun(e, "drama", (f) => ({
  lang: f.get("lang"), scenario: f.get("scenario"), title: f.get("title"),
  orchestration: f.get("orchestration"),
  cast: [...$("#d-cast").querySelectorAll(".on")].map((b) => b.dataset.who),
  duration_s: +f.get("duration_s"), parts: +f.get("parts"), count: +f.get("count"),
  clip_seconds: +(f.get("clip_seconds") || 0),
  duration_tol_s: +(f.get("duration_tol_s") || 0),
  parts_iterative: f.get("parts_iterative") === "on",
  profanity: +f.get("profanity"), ad: f.get("ad"), push: f.get("push"),
  dry_run: f.get("dry_run") === "on", breakpoints: [...chosenBps.drama],
}));

// --------------------------------------------------- retuning a running loop
//
// Every setting of a loop can be changed while it runs, and "every" is only true if it
// is the SAME set the form starts a run with — so it is that form, filled from the
// loop, with its launch button pointed at the loop instead of at a new run. Nothing
// here knows what the settings ARE; that is exactly why none of them can be forgotten.
//
// What the form may not say about a running loop is taken back off it by the server
// (its mode, its count, where it writes), and the topic is left out here: while a loop
// is running that is the queue's business, not the form's.
let editing = null;  // {id, mode, title} while a loop's settings are open

function launchLabel(form, key) {
  const btn = form.querySelector(".card.launch button");
  if (!btn) return;
  btn.dataset.l = key;
  applyLabels(btn.parentElement);
}

function editLoop(l) {
  editing = { id: l.id, mode: l.mode, title: l.title };
  const chip = document.querySelector(`#mode-menu [data-mode="${l.mode}"]`);
  if (chip) chip.click();
  fillForm(l);
  const form = document.querySelector(`form.cards[data-mode="${l.mode}"]`);
  launchLabel(form, "web.loop.apply");
  $("#editing-what").textContent = `${lab("js.loop.editing")} ${l.title}`;
  $("#editing").hidden = false;
  openTab("gen");
  compose();
}

function stopEditing() {
  if (!editing) return;
  const form = document.querySelector(`form.cards[data-mode="${editing.mode}"]`);
  editing = null;
  $("#editing").hidden = true;
  if (!form) return;
  form.querySelectorAll('[name="loop_on"], [name="loop_topics"]')
      .forEach((el) => ((el.closest("label") || el).hidden = false));
  launchLabel(form, "web.go");
  applyConditions(form);
  compose();
}

$("#editing-cancel").onclick = () => stopEditing();

// The inverse of the submit builders: a loop's settings back into the controls that
// mean them. Field names ARE the parameter names wherever they can be, so most of this
// is one assignment; what is left is the handful the form says its own way — a
// narrator, a picture source, a cast, the filters, the breakpoints.
function fillForm(l) {
  const form = document.querySelector(`form.cards[data-mode="${l.mode}"]`);
  if (!form) return;
  const p = l.params || {};
  // first, because the source list is rebuilt from it
  const medium = form.querySelector('[name="medium"]');
  if (medium && p.medium) { medium.value = p.medium; if (medium.onchange) medium.onchange(); }
  const src = form.querySelector('[name="source"]');
  if (src && l.picture_source) { src.value = l.picture_source; if (src.onchange) src.onchange(); }
  form.querySelectorAll("[name]").forEach((el) => {
    const n = el.name;
    if (n.startsWith("loop_") || ["title", "medium", "source"].includes(n)) return;
    if (n === "idea" || n === "scenario") { el.value = ""; return; }  // the queue's
    const v = n === "voice" ? p.fandom_voice : p[n];
    if (v === undefined || v === null) return;
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v;
  });
  form.querySelectorAll("[data-fx]").forEach((r) => {
    r.value = (p.filters || {})[r.dataset.fx] || 0;
    r.dispatchEvent(new Event("input"));
  });
  form.querySelectorAll("input[type=range]").forEach((r) => r.dispatchEvent(new Event("input")));
  const cast = new Set(l.cast || []);
  form.querySelectorAll("[data-who]").forEach((b) => b.classList.toggle("on", cast.has(b.dataset.who)));
  chosenBps[l.mode] = new Set(l.breakpoints || []);
  form.querySelectorAll("[data-bp]").forEach((b) =>
    b.classList.toggle("on", chosenBps[l.mode].has(b.dataset.bp)));
  // the loop's own controls come along, minus the two questions that are not being
  // asked here: whether to loop at all, and what the next topics are
  const on = form.querySelector('[name="loop_on"]');
  if (on) { on.checked = true; (on.closest("label") || on).hidden = true; }
  const put = (n, v) => { const el = form.querySelector(`[name="${n}"]`); if (el) el.value = v; };
  put("loop_source", l.source); put("loop_limit", l.limit); put("loop_park", l.on_park);
  const topics = form.querySelector('[name="loop_topics"]');
  if (topics) { topics.value = ""; (topics.closest("label") || topics).hidden = true; }
  applyConditions(form);
}

async function applyToLoop(body) {
  try {
    await api(`/api/loops/${editing.id}/params`, { method: "PUT",
      headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  } catch (err) { say(err.message, true); return; }
  say(lab("js.loop.retuned"));
  stopEditing();
  openTab("runs");
}

// The loop block, read off the card every mode form carries. It is sent with every
// run: `on` off means what it says, and the server starts a plain run — the loop is
// one flag on the same settings rather than a second way of describing a video.
function loopOf(form) {
  const f = new FormData(form);
  return {
    on: f.get("loop_on") === "on",
    source: f.get("loop_source") || "ai",
    limit: +(f.get("loop_limit") || 0),
    on_park: f.get("loop_park") || "hold",
    topics: String(f.get("loop_topics") || "").split("\n")
      .map((t) => t.trim()).filter(Boolean),
  };
}

function commonOf(form) {
  const f = new FormData(form);
  const filters = {};
  form.querySelectorAll("[data-fx]").forEach((r) => {
    if (+r.value > 0) filters[r.dataset.fx] = +r.value;
  });
  return {
    voice_override: f.get("voice_override") || "",
    tts_engine: f.get("tts_engine") || "",
    tts_rate: +(f.get("tts_rate") || 0),
    subtitle_style: f.get("subtitle_style") || "",
    ad_mode: f.get("ad_mode") || "both",
    visual_notes: f.get("visual_notes") || "",
    visual_style: f.get("visual_style") || "",
    clean_subtitles: f.get("clean_subtitles") === "on",
    keep_temp: f.get("keep_temp") === "on",
    loop: loopOf(form),
    filters,
  };
}

async function submitRun(e, mode, build) {
  e.preventDefault();
  const body = { ...commonOf(e.target), ...build(new FormData(e.target)) };
  if (editing && editing.mode === mode) return applyToLoop(body);
  let out;
  try {
    out = await api(`/api/runs/${mode}`, { method: "POST",
      headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  } catch (err) { say(err.message, true); return; }
  started(out);
}

// A loop answers with a loop rather than a run — it has iterations where a run has a
// count — and that is how the page tells which of the two it just started.
function started(out) {
  const looped = out && out.iterations !== undefined;
  say(lab(looped ? "js.loop.started" : "js.the-run-has-started-it-is-on-the-runs-ta"));
  loadRuns();
  if (looped) loadLoops();
}

// ---------------------------------------------------------------- model weights
async function loadModels() {
  const ms = await api("/api/models");
  $("#models").innerHTML = ms.map((m) => {
    const j = m.job;
    const pct = j && j.total ? Math.round(100 * j.done / j.total) : 0;
    return `<div class="panel cfg-item${m.installed ? " active" : ""}" data-model="${esc(m.id)}">
      <div class="row"><b>${esc(m.label)}</b>
        <span class="dim">${esc(m.size_human)}${m.installed ? ` ${lab("js.on-disk")} ${esc(m.on_disk)}` : ""}</span>
        <span class="grow"></span>
        ${m.installed ? `<span class="pill-on">${lab("js.installed")}</span><button data-del class="ghost">${lab("js.delete")}</button>`
          : j && ["queued", "running"].includes(j.status)
            ? `<span class="dim">${esc(j.label || j.note || lab("js.downloading"))} ${pct}%</span>`
            : `<button data-get class="primary">${lab("js.download")}</button>`}
      </div>
      <div class="dim">${esc(m.description)}</div>
      <div class="dim">${m.used_by ? lab("js.needed-for") + " " + esc(m.used_by) + " · " : ""}${esc(m.license)}</div>
      ${j && j.status === "failed" ? `<div class="why">${esc(j.note)}</div>` : ""}
      ${j && ["queued", "running"].includes(j.status)
        ? `<div class="bar"><i style="width:${pct}%"></i></div>` : ""}
    </div>`;
  }).join("");
  $("#models").querySelectorAll("[data-get]").forEach((b) => {
    b.onclick = async () => {
      const id = b.closest("[data-model]").dataset.model;
      await api(`/api/models/${encodeURIComponent(id)}`, { method: "POST" });
      say(lab("js.downloading-this-takes-a-while"));
      loadModels();
    };
  });
  $("#models").querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = async () => {
      const id = b.closest("[data-model]").dataset.model;
      await api(`/api/models/${encodeURIComponent(id)}`, { method: "DELETE" });
      say(lab("js.deleted2"));
      loadModels();
    };
  });
  // a download reports through polling rather than a stream: it is one number
  // changing, and a whole EventSource for a percentage is more plumbing than it earns
  clearTimeout(loadModels._t);
  if (ms.some((m) => m.job && ["queued", "running"].includes(m.job.status)))
    loadModels._t = setTimeout(loadModels, 1500);
}

// ---------------------------------------------------------------- what a run wants
async function openAsks(id, title) {
  const d = await api(`/api/runs/${id}/asks`);
  $("#panel-title").textContent = `${title} ${lab("js.missing")} ${d.pending} ${lab("js.of")} ${d.shots.length}`;
  $("#panel-apply").hidden = true;
  $("#panel-body").innerHTML = d.shots.length ? d.shots.map((sh) => {
    const url = `/api/runs/${id}/asks/${encodeURIComponent(sh.video)}/${encodeURIComponent(sh.id)}/file`;
    // what arrived is shown, not merely reported: a wrong file under the right name
    // reads identically in a manifest
    const preview = sh.delivered
      ? (sh.photo ? `<img class="got" src="${url}" alt="">`
                  : `<video class="got" src="${url}" controls preload="metadata"></video>`)
      : "";
    return `<div class="ask ${sh.status === "delivered" ? "done" : ""}" data-v="${esc(sh.video)}" data-s="${esc(sh.id)}">
      <div class="head"><b>${esc(sh.id)}</b>
        <span class="dim">${sh.size[0]}×${sh.size[1]} · ${esc(sh.status)}${sh.target_s ? ` · ~${sh.target_s.toFixed(1)}${lab("js.s")}` : ""}</span>
        <span class="grow"></span>
        <button class="ghost" data-copy>${lab("js.copy-the-prompt")}</button>
        ${sh.delivered ? `<button class="ghost" data-undo>${lab("js.replace")}</button>` : ""}</div>
      <pre>${esc(sh.prompt)}</pre>
      ${preview}
      ${sh.status === "delivered" ? "" : `<label class="take">${lab("js.drop-a-picture-here")}
        <input type="file" hidden accept="image/*,video/*"></label>`}
    </div>`;
  }).join("") : `<p class="empty">${lab("js.this-run-is-not-asking-for-anything")}</p>`;
  bindAsks(id);
  $("#panel").hidden = false;
}

function bindAsks(id) {
  $("#panel-body").querySelectorAll(".ask").forEach((el) => {
    const v = el.dataset.v, sid = el.dataset.s;
    el.querySelector("[data-copy]").onclick = () =>
      navigator.clipboard.writeText(el.querySelector("pre").textContent);
    const undo = el.querySelector("[data-undo]");
    if (undo) undo.onclick = async () => {
      await api(`/api/runs/${id}/asks/${encodeURIComponent(v)}/${encodeURIComponent(sid)}`,
                { method: "DELETE" });
      say(lab("js.you-can-bring-it-again"));
      openAsks(id, $("#panel-title").textContent.split(" — ")[0]);
    };
    const take = el.querySelector(".take");
    if (!take) return;
    const input = take.querySelector("input");
    const send = async (file) => {
      const body = new FormData(); body.append("file", file);
      take.textContent = lab("js.sending");
      try {
        await api(`/api/runs/${id}/asks/${encodeURIComponent(v)}/${encodeURIComponent(sid)}`,
                  { method: "POST", body });
        take.textContent = lab("js.accepted");
        el.classList.add("done");
      } catch (e) { take.textContent = lab("js.did-not-work") + e.message; }
    };
    take.onclick = () => input.click();
    input.onchange = () => input.files[0] && send(input.files[0]);
    ["dragover", "dragenter"].forEach((k) => take.addEventListener(k, (e) => {
      e.preventDefault(); take.classList.add("over"); }));
    ["dragleave", "drop"].forEach((k) => take.addEventListener(k, () => take.classList.remove("over")));
    take.addEventListener("drop", (e) => { e.preventDefault(); e.dataTransfer.files[0] && send(e.dataTransfer.files[0]); });
  });
}

// ---------------------------------------------------------------- a parked breakpoint
let reviewState = null;
async function openReview(id, title) {
  const d = await api(`/api/runs/${id}/review`);
  if (!d.stage) { say(lab("js.this-run-is-not-sitting-at-a-breakpoint"), true); return; }
  reviewState = { id, video: d.video, stage: d.stage, rows: d.rows };
  $("#panel-title").textContent = `${title} — ${word(d.stage)}`;
  $("#panel-apply").hidden = false;
  // one generic renderer for every breakpoint: review.py already says how each row is
  // edited (`kind`) and what it may become (`options`), so nothing here knows what a
  // canon sheet or a picture track is
  $("#panel-body").innerHTML = d.rows.map((r, i) => `
    <div class="rrow"><div class="lab">${esc(r.label)}${r.info ? " · " + esc(humanise(r.info)) : ""}</div>
      ${r.readonly ? `<div>${esc(r.value)}</div>`
        : r.kind === "choice"
          ? `<select data-i="${i}">${["", ...r.options].map((o) =>
              `<option${o === r.value ? " selected" : ""}>${esc(o)}</option>`).join("")}</select>`
          : `<textarea data-i="${i}" rows="${r.value.length > 90 ? 3 : 1}">${esc(r.value)}</textarea>`}
    </div>`).join("");
  $("#panel-body").querySelectorAll("[data-i]").forEach((el) => {
    el.oninput = () => (reviewState.rows[+el.dataset.i].value = el.value);
    el.onchange = () => (reviewState.rows[+el.dataset.i].value = el.value);
  });
  $("#panel").hidden = false;
}

$("#panel-apply").onclick = async () => {
  if (!reviewState) return;
  const id = reviewState.id;
  const r = (await api("/api/runs")).find((x) => x.id === id);
  await api(`/api/runs/${id}/review`, { method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ video: reviewState.video, stage: reviewState.stage,
                           rows: reviewState.rows }) });
  // the button says "and go on", so it goes on: reviewing is the reply to a run that
  // stopped to ask something, and leaving it stopped afterwards is half an answer
  if (r && r.run_dir) {
    await api(`/api/runs/${id}/resume`, { method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ run_dir: r.run_dir }) });
  }
  $("#panel").hidden = true;
  reviewState = null;
  say(lab("js.changes-applied-the-run-goes-on"));
  loadRuns();
};
$("#panel-close").onclick = () => { $("#panel").hidden = true; reviewState = null; };

// ---------------------------------------------------------------- loops
//
// A loop is not a run and does not stream: it makes runs, and they stream. What it has
// instead is a plan that may be edited between videos, so its card is CONTROLS — who
// picks the topics, how many are left, which stages stop for review — and the videos it
// has made are the ordinary run rows underneath.
let loopTimer = null;

async function loadLoops() {
  let loops;
  try { loops = await api("/api/loops"); } catch { return; }
  $("#loops").innerHTML = loops.map(loopCard).join("");
  bindLoops(loops);
  // A loop has nothing to push, so the page asks. Only while one is alive: a settled
  // loop changes when the operator changes it, and that redraws the card anyway.
  clearTimeout(loopTimer);
  if (loops.some((l) => l.live) && !$("#tab-runs").hidden)
    loopTimer = setTimeout(loadLoops, 4000);
}

const LOOP_STATUS = { running: "js.running", queued: "js.queued", waiting: "js.loop.waiting",
                      held: "js.loop.held", done: "js.done", failed: "js.failed",
                      stopped: "js.stopped" };

function chip(attr, value, on, text) {
  return `<button type="button" data-${attr}="${esc(value)}" class="${on ? "on" : ""}">${esc(text)}</button>`;
}

function loopCard(l) {
  const of = l.limit ? `${l.made}/${l.started} ${lab("js.loop.of")} ${l.limit}`
                     : `${l.made}/${l.started} · ${lab("js.loop.nolimit")}`;
  const bps = (opts && opts.breakpoints[l.mode]) || [];
  return `
    <div class="run loop" data-loop="${l.id}">
      <div class="top">
        <span class="st st-${l.live ? "running" : "done"}">${esc(lab(LOOP_STATUS[l.status] || l.status))}</span>
        <b>${esc(l.title)}</b><span class="dim">${esc(of)}</span>
        <span class="grow"></span>
        <button data-loopact="edit" class="ghost">${esc(lab("js.loop.settings"))}</button>
        ${l.live ? `<button data-loopact="stop" class="ghost">${esc(lab("js.loop.stop"))}</button>` : ""}
      </div>
      ${l.note ? `<div class="why">${esc(lab(l.note, humanise(l.note)))}</div>` : ""}
      <div class="row">
        <span class="dim">${esc(lab("js.loop.who"))}</span>
        <span class="chips">${chip("src", "ai", l.source === "ai", word("ai"))}${chip("src", "me", l.source === "me", word("me"))}</span>
        <span class="dim">${esc(lab("js.loop.limit"))}</span>
        <input class="lim" type="number" min="0" value="${l.limit}">
        <span class="dim">${esc(lab("js.loop.park"))}</span>
        <span class="chips">${chip("park", "hold", l.on_park === "hold", word("hold"))}${chip("park", "go_on", l.on_park === "go_on", word("go_on"))}</span>
      </div>
      <div class="row">
        <input class="topic" placeholder="${esc(lab("js.loop.addtopic"))}">
        <button data-loopact="add" class="ghost">${esc(lab("web.add"))}</button>
        <span class="grow"></span>
        <span class="dim">${l.topics.length ? esc(lab("js.loop.queued")) + " " + l.topics.length : ""}</span>
      </div>
      ${l.topics.length ? `<ol class="queue">${l.topics.map((t, i) =>
        `<li>${esc(t)}<button data-drop="${i}" class="ghost">×</button></li>`).join("")}</ol>` : ""}
      ${bps.length ? `<div class="row"><span class="dim">${esc(lab("web.card.bps"))}</span>
        <span class="chips">${bps.map((b) => chip("lbp", b, l.breakpoints.includes(b), word(b))).join("")}</span></div>` : ""}
      <div class="its">${l.iterations.slice(-12).map((it) =>
        `<span class="it">#${it.n} ${esc(lab(STATUS[it.status] || "js.running"))}${it.topic ? " · " + esc(it.topic) : ""}</span>`).join("")}</div>
    </div>`;
}

function bindLoops(loops) {
  const byId = Object.fromEntries(loops.map((l) => [l.id, l]));
  const put = async (id, body) => {
    try { await api(`/api/loops/${id}`, { method: "PUT",
      headers: { "content-type": "application/json" }, body: JSON.stringify(body) }); }
    catch (e) { say(e.message, true); }
    loadLoops();
  };
  document.querySelectorAll("#loops .loop").forEach((box) => {
    const id = box.dataset.loop, l = byId[id];
    box.querySelectorAll("[data-src]").forEach((b) =>
      (b.onclick = () => put(id, { source: b.dataset.src })));
    box.querySelectorAll("[data-park]").forEach((b) =>
      (b.onclick = () => put(id, { on_park: b.dataset.park })));
    box.querySelectorAll("[data-lbp]").forEach((b) => (b.onclick = () => {
      const on = new Set(l.breakpoints);
      on.has(b.dataset.lbp) ? on.delete(b.dataset.lbp) : on.add(b.dataset.lbp);
      put(id, { breakpoints: [...on] });
    }));
    box.querySelectorAll("[data-drop]").forEach((b) => (b.onclick = () =>
      put(id, { topics: l.topics.filter((_, i) => i !== +b.dataset.drop) })));
    const lim = box.querySelector(".lim");
    if (lim) lim.onchange = () => put(id, { limit: +lim.value });
    const topic = box.querySelector(".topic");
    const add = () => {
      const t = topic.value.trim();
      if (t) { topic.value = ""; put(id, { add_topics: [t] }); }
    };
    if (topic) topic.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } };
    box.querySelectorAll("[data-loopact]").forEach((b) => (b.onclick = async () => {
      if (b.dataset.loopact === "add") return add();
      if (b.dataset.loopact === "edit") return editLoop(l);
      try { await api(`/api/loops/${id}/stop`, { method: "POST" }); } catch (e) { say(e.message, true); }
      loadLoops();
    }));
  });
}

// ---------------------------------------------------------------- runs
const streams = new Map();

async function loadRuns() {
  loadLoops();
  const runs = await api("/api/runs");
  $("#runs").innerHTML = runs.map((r) => `
    <div class="run" data-id="${r.id}">
      <div class="top">
        <span class="st st-${r.status}">${statusWord(r)}</span>
        <b>${esc(r.title)}</b>
        <span class="grow"></span>
        ${actions(r).map((a) =>
          `<button data-act="${a.act}" data-id="${r.id}" class="${a.primary ? "primary" : "ghost"}">${esc(a.label)}</button>`).join("")}
      </div>
      ${waitingFor(r)}
      <div class="log" id="log-${r.id}"></div>
    </div>`).join("") || `<p class="empty">${lab("js.no-runs-yet")}</p>`;
  const byId = Object.fromEntries(runs.map((r) => [r.id, r]));
  document.querySelectorAll("#runs [data-act]").forEach((b) => {
    b.onclick = () => act(b.dataset.act, byId[b.dataset.id]);
  });
  // Only LIVE runs get a stream. Watching every run in the list was a quiet disaster
  // once the server started finding old ones on disk: forty runs meant forty
  // EventSources, and a browser allows about six connections to one origin at a time
  // — so the pool ran out, every later fetch queued behind it, and the page looked
  // frozen. A finished run has nothing to stream anyway; its log is already written.
  runs.filter((r) => live(r)).forEach((r) => watch(r.id));
  runs.filter((r) => !live(r) && streams.has(r.id))
      .forEach((r) => { streams.get(r.id).es.close(); streams.delete(r.id); });
}

const live = (r) => ["running", "queued"].includes(r.status);

// A run's status is one word on the wire and another on screen. The table holds the
// KEY, looked up per render: a run list is redrawn constantly and must follow the
// interface language, not the language the file was loaded in.
const STATUS = { running: "js.running", queued: "js.queued", paused: "js.waiting",
                 review: "js.in-review", done: "js.done", failed: "js.failed",
                 stopped: "js.stopped" };
const statusWord = (r) => (STATUS[r.status] ? lab(STATUS[r.status]) : r.status);

// What this run can actually DO — never a button that will answer "nothing here".
// A status alone cannot decide it: a stopped run may or may not have been sitting on
// a breakpoint, and a paused one is only worth opening if it is still owed pictures.
// That is why the server reports what each run is waiting for (see `parked`).
function actions(r) {
  const p = r.parked || {};
  if (live(r)) return [{ act: "stop", label: lab("js.stop-it") }];
  const out = [];
  if (p.asks) out.push({ act: "asks", label: `${lab("js.give-it-pictures")} ${p.asks}`, primary: true });
  if (p.review_stage) out.push({ act: "review", label: `${lab("js.review")} ${p.review_stage}`, primary: true });
  if (p.asks || p.review_stage) out.push({ act: "resume", label: lab("js.go-on") });
  else if (r.status === "failed") out.push({ act: "resume", label: lab("js.try-again") });
  else if (r.status !== "done") out.push({ act: "resume", label: lab("js.resume") });
  if (p.video) out.push({ act: "video", label: lab("js.watch-the-video") });
  return out;
}

function waitingFor(r) {
  const p = r.parked || {};
  const bits = [];
  if (p.asks) bits.push(`${lab("js.pictures-missing")} ${p.asks}`);
  if (p.review_stage) bits.push(`${lab("js.sitting-at")}${word(p.review_stage)}${lab("js.waiting-on-you")}`);
  if (!bits.length && r.message) bits.push(lab(r.message, r.message));
  return bits.length ? `<div class="why">${esc(bits.join(" · "))}</div>` : "";
}

async function act(what, r) {
  if (!r) return;
  try {
    if (what === "stop") { await api(`/api/runs/${r.id}/stop`, { method: "POST" }); loadRuns(); }
    else if (what === "asks") await openAsks(r.id, r.title);
    else if (what === "review") await openReview(r.id, r.title);
    else if (what === "video") window.open(`/api/runs/${r.id}/video`, "_blank");
    else if (what === "resume") {
      await api(`/api/runs/${r.id}/resume`, { method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ run_dir: r.run_dir }) });
      say(lab("js.resuming"));
      loadRuns();
    }
  } catch (e) { say(e.message, true); }
}

// One EventSource per run, kept across tab switches: the backlog arrives first, so a
// page that shows up late still sees everything that happened.
function watch(id) {
  if (streams.has(id)) { streams.get(id).lines.forEach((l) => appendLog(id, l)); return; }
  const es = new EventSource(`/api/runs/${id}/events`);
  const state = { es, lines: [] };
  streams.set(id, state);
  es.onmessage = (m) => {
    const e = JSON.parse(m.data);
    const line = `${e.video >= 0 ? "[" + e.video + "] " : ""}${e.stage} ${e.status} ${e.message}`.trim();
    state.lines.push(line);
    appendLog(id, line);
    if (["done", "failed", "stopped", "paused", "review"].includes(e.status) && e.stage === "run") loadRuns();
  };
}
function appendLog(id, line) {
  const el = document.getElementById(`log-${id}`);
  if (!el) return;
  el.textContent += (el.textContent ? "\n" : "") + line;
  el.scrollTop = el.scrollHeight;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
