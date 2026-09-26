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

// The session, when a cookie will not do. Inside Telegram this page can be a
// third-party iframe whose cookie the browser is entitled to discard, so the server
// also accepts the token as a header — and, for the things that cannot send one, as
// a query parameter. Empty everywhere else, and then both of these do nothing.
let TOK = "";
// A URL for something the BROWSER fetches on its own: <img>, <video>, <audio>,
// EventSource. None of them can carry a header, so the token rides in the query.
const tokd = (u) => (TOK ? u + (u.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(TOK) : u);

const api = async (url, opts) => {
  const o = { credentials: "same-origin", ...opts };
  if (TOK) o.headers = { ...(o.headers || {}), "X-Slopgen-Token": TOK };
  const r = await fetch(url, o);
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
// and its width in pixels, because an ad overlay is sized in them: 340 means nothing
// until it is read against the frame it sits on.
let videoWidth = 1080;

// Sign in as whoever Telegram says is holding the phone.
//
// `initData` is a signed query string the Mini App is handed on open; the server
// checks the signature against the bot token and against the same allow-list the chat
// is filtered by. So there is no password to type — which matters, because the Mini
// App exists precisely for the times you are not at the machine.
async function telegramSignIn() {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (!tg || !tg.initData) return false;
  try { tg.ready(); tg.expand(); } catch (e) { /* an older client; nothing depends on it */ }
  document.body.classList.add("in-telegram");
  const r = await fetch("/api/tg-login", {
    method: "POST", credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ init_data: tg.initData }),
  }).catch(() => null);
  if (!r || !r.ok) return false;
  TOK = (await r.json()).token || "";
  return true;
}

// ---------------------------------------------------------------- boot
(async function boot() {
  let me = await fetch("/api/me").then((r) => r.json());
  if (!me.signed_in && me.telegram && (await telegramSignIn())) me = { signed_in: true };
  if (!me.signed_in) {
    // A bot with no password has no form worth showing: the only way in is the button
    // in the chat, and a password box that can never be right is worse than a sentence.
    if (me.telegram && !me.needs_password) {
      $("#loginform").hidden = true;
      $("#loginerr").textContent = lab("js.open-from-bot");
    }
    return show("login");
  }
  show("app");
  const wasOn = readPlace().tab;
  showTab(TABS.includes(wasOn) ? wasOn : "gen");
  const cfg = await api("/api/config").catch(() => null);
  if (cfg) { videoAspect = cfg.video.width / cfg.video.height; videoWidth = cfg.video.width; }
  await loadWorlds();
  await loadOptions();
  restorePlace();
})();

$("#loginform").onsubmit = async (e) => {
  e.preventDefault();
  const body = new FormData(e.target);
  const r = await fetch("/api/login", { method: "POST", body, credentials: "same-origin" });
  if (!r.ok) { $("#loginerr").textContent = lab("js.no-good"); return; }
  TOK = (await r.json().catch(() => ({}))).token || "";
  show("app"); loadWorlds();
};

// The three doors mirror the terminal's home screen — make something, pick a run back
// up, set things up — because an operator who knows one should not have to learn the
// other. Everything else hangs under a door rather than lining up beside it.
const TABS = ["gen", "runs", "cfg", "models"];

// Where the operator was standing, kept per browser so a reload puts them back.
//
// This is a CONVENIENCE and is treated as one: it is read defensively, it is validated
// against what exists now rather than trusted, and every failure lands on the default
// door. A section renamed out of `CFG`, a world sub-tab that no longer exists, storage
// that throws because the browser is set to block it — each of those has to be a page
// that opens on "генерация", never a page that opens on nothing.
const PLACE_KEY = "slopgen.place";

function readPlace() {
  try { return JSON.parse(localStorage.getItem(PLACE_KEY)) || {}; }
  catch { return {}; }  // private window, blocked storage, or something else's key
}

function savePlace() {
  try {
    localStorage.setItem(PLACE_KEY, JSON.stringify(
      { tab: curTab, cfg: cfgSection, sub, mode: genMode }));
  } catch { /* not being able to remember is not worth interrupting anything for */ }
}

let curTab = "gen";

// Split out of `openTab` so boot can put the right door on screen BEFORE the two round
// trips it needs to fill anything in. Showing "генерация" for a third of a second and
// then jumping is worse than the bug being fixed.
function showTab(tab) {
  curTab = tab;
  document.querySelectorAll("nav [data-tab]").forEach((x) =>
    x.classList.toggle("on", x.dataset.tab === tab));
  TABS.forEach((t) => ($(`#tab-${t}`).hidden = t !== tab));
}

document.querySelectorAll("nav [data-tab]").forEach((b) => {
  b.onclick = () => openTab(b.dataset.tab);
});
function openTab(tab) {
  showTab(tab);
  savePlace();
  // The grid is measured, so it has to be measured while it is on screen. Reloading
  // into another door means the forms were last composed with no width to compose in.
  if (tab === "gen") { if (opts) queueMicrotask(compose); else loadOptions(); }
  if (tab === "runs") { if (!opts) loadOptions(); loadRuns(); }
  if (tab === "cfg" && !cfgSection) openCfg("fandoms");
  if (tab === "models") loadModels();
}

// Put back what was open, once there is data behind it. Called at the end of boot: the
// config sections read worlds and options, so restoring one any earlier would open a
// screen with nothing in it and no second attempt coming.
function restorePlace() {
  const p = readPlace();
  if (SUBS.some(([k]) => k === p.sub)) sub = p.sub;
  if (document.querySelector(`#mode-menu [data-mode="${p.mode}"]`)) setMode(p.mode);
  const tab = TABS.includes(p.tab) ? p.tab : "gen";
  if (tab !== "cfg") return openTab(tab);
  // set the section FIRST: `openTab` opens a default one only when none is chosen, and
  // opening "worlds" on the way to "voices" costs two requests and a visible flinch
  cfgSection = CFG.some(([k]) => k === p.cfg) ? p.cfg : "fandoms";
  openTab(tab);
  openCfg(cfgSection);
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
  ["ads", "js.ad-contracts", "ads"],
  ["accounts", "js.accounts", "list"],
  ["orchestration", "js.generator-chains", "orch"],
  ["effects", "js.effects", "effects"],
  ["presets", "js.presets", "list"],
  ["access", "js.access", "access"],
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
  savePlace();
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
  $("#cfg-ads").hidden = how !== "ads";
  $("#cfg-effects").hidden = how !== "effects";
  $("#cfg-access").hidden = how !== "access";
  $("#cfg-todo").hidden = !!how;
  if (how === "world") openSub(sub);
  else if (how === "keys") loadKeys();
  else if (how === "tts") loadTts();
  else if (how === "voices") loadVoices();
  else if (how === "orch") loadOrch();
  else if (how === "ads") loadAds();
  else if (how === "effects") loadEffects();
  else if (how === "access") loadAccess();
  else if (how === "list") loadConfigs(key, lab(entry[1]));
  else $("#cfg-todo-title").textContent = lab(entry[1]);
}

// ------------------------------------------------------------------ access
//
// Only the host is editable here. The password is what unlocks the network, so a page
// that may itself be ON the network does not get to set it — that stays a line in
// `configs/slopgen.toml`, typed by somebody sitting at the machine.
// Two states, so a checkbox: an operator either keeps this to themselves or lets the
// phone on the same wifi in. Binding to one specific interface address is a thing the
// config file can still say, and a checkbox cannot express three states — so a
// hand-written address is kept as it is, said out loud on the screen, and only
// replaced if the operator actually unticks the box.
const LOOPBACK_HOST = "127.0.0.1";
const NETWORK_HOST = "0.0.0.0";
let accessHost = LOOPBACK_HOST;   // what the file says, which may be neither of those

async function loadAccess() {
  const w = await api("/api/web");
  accessHost = w.host;
  const custom = ![LOOPBACK_HOST, NETWORK_HOST].includes(w.host);
  $("#acc-net").checked = w.host !== LOOPBACK_HOST;
  $("#acc-custom").hidden = !custom;
  $("#acc-custom").textContent = custom ? `${lab("js.set-in-the-file")} ${w.host}` : "";
  $("#acc-pass").value = "";
  $("#acc-pass-state").textContent = w.has_password ? lab("js.set") : lab("js.empty");
  // Editable only at the machine itself. The server refuses the write anyway, so this
  // is not the lock — it is saying which one this is, because a field that takes
  // typing and then rejects it is worse than a field that does not take typing.
  for (const el of [$("#acc-net"), $("#acc-pass")]) el.disabled = !w.local;
  $("#acc-save").hidden = !w.local;
  $("#acc-remote").hidden = w.local;
  accessRows(w);
  showBound(w);
}

// A password on loopback protects nothing and only asks the operator to type one, so
// the field appears when they tick the box — which is the moment it starts mattering.
function accessRows(w) {
  $("#acc-pass-row").hidden = !$("#acc-net").checked;
  $("#acc-clear").hidden = !w.local || !w.has_password;
}

// The setting and what it actually bound to are two different facts, and the screen
// shows both: asking for the network without a password silently stays on loopback,
// and a box that just echoes what was ticked would show a machine reachable from the
// network that is not. Either change only takes effect on restart, which is the other
// half of why the reading and the setting have to be shown apart.
function showBound(w) {
  const parts = [lab("js.bound-now") + " " + w.bound + ":" + w.port];
  if (!w.has_password && w.host !== w.bound) parts.push(lab("js.no-password-loopback"));
  $("#acc-state").textContent = parts.join(" · ");
}

// Ticking the box keeps a hand-written address if there is one — it is already a
// network address, and replacing it with 0.0.0.0 would widen what somebody narrowed
// on purpose.
const wantedHost = () => !$("#acc-net").checked ? LOOPBACK_HOST
  : (accessHost !== LOOPBACK_HOST ? accessHost : NETWORK_HOST);

async function saveAccess(body) {
  try {
    const w = await api("/api/web", { method: "PUT",
      headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    accessHost = w.host;
    $("#acc-pass").value = "";
    $("#acc-pass-state").textContent = w.has_password ? lab("js.set") : lab("js.empty");
    accessRows(w);
    showBound(w);
    // Saying "saved" for a setting that will be ignored is the lie this screen exists
    // to avoid: without a password the server comes back up on loopback regardless.
    const idle = w.host !== LOOPBACK_HOST && !w.has_password;
    say(idle ? lab("js.network-needs-a-password") : lab("js.saved-restart-to-apply"), idle);
  } catch (e) { say(e.message, true); }
}

$("#acc-net").onchange = () =>
  accessRows({ local: true, has_password: $("#acc-pass-state").textContent === lab("js.set") });
$("#acc-save").onclick = () =>
  saveAccess({ host: wantedHost(), password: $("#acc-pass").value });
// Clearing is its own button because an empty box means "leave the password alone":
// the form never shows what is set, so it cannot be the thing that unsets it.
$("#acc-clear").onclick = () => saveAccess({ host: wantedHost(), clear_password: true });

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
  wireDemo(d);
}

// ------------------------------------------------------------- hearing a voice
//
// The terminal's demo spoke a line, played it once and was done: to hear it again you
// paid for it again, which on the local model is another minute of CPU. Here a take is
// KEPT — as a blob in this tab, with an <audio> of its own — so two voices can be put
// next to each other and played back and forth, which is the actual question ("which
// of these") rather than the one a single playthrough answers ("did that sound ok").
//
// The cache is deliberately the tab's memory and nothing else. The server streams the
// audio back and keeps no file, so there is no folder anywhere filling up with takes,
// and closing the tab is what clears them — no sweeping, no expiry, no surprise on
// disk a week later. A reload costs the takes; that is the same bargain and the price
// of it being honestly ephemeral.
const demoTakes = new Map();  // key -> {id, url, engine, voice, lang, text}
let takeSeq = 0;

// What makes two takes the same take. Never put this in the markup: it joins free text
// the operator typed, and a NUL separator does not survive the HTML parser — it comes
// back as U+FFFD, so a `data-` attribute holding it stops matching the map it came
// from and every row's button quietly does nothing. Rows carry a serial number and the
// key stays in JS.
const demoKey = (t) => [t.engine, t.voice, t.lang, t.text].join("\u0000");

const takeRow = (id) => $(`#demo-takes [data-take="${id}"]`);

function forgetTake(id) {
  for (const [key, t] of demoTakes) {
    if (t.id !== id) continue;
    URL.revokeObjectURL(t.url);  // without this the blob outlives the row that played it
    demoTakes.delete(key);
    break;
  }
  drawTakes();
}

// Belt and braces: a closing tab frees its own blobs anyway, but revoking on the way
// out says out loud that nothing here is meant to survive the tab.
addEventListener("pagehide", () => {
  demoTakes.forEach((t) => URL.revokeObjectURL(t.url));
  demoTakes.clear();
});

function drawTakes() {
  const box = $("#demo-takes");
  if (!box) return;
  if (!demoTakes.size) { box.innerHTML = ""; return; }
  box.innerHTML = [...demoTakes.values()].reverse().map((t) => `
    <div class="panel cfg-item" data-take="${t.id}">
      <div class="row"><b>${esc(t.voice)}</b>
        <span class="dim">${esc(t.engine)} · ${esc(t.lang)}</span>
        <span class="grow"></span>
        <button data-drop class="ghost">${lab("js.forget")}</button></div>
      <audio controls preload="auto" src="${esc(t.url)}"></audio>
      <p class="dim">${esc(t.text)}</p>
    </div>`).join("");
  box.querySelectorAll("[data-take]").forEach((el) => {
    el.querySelector("[data-drop]").onclick = () => forgetTake(+el.dataset.take);
  });
}

// The picker offers what THIS engine can actually say, which is two different lists
// and not always both: its catalogue for the chosen language, and the clone cards. A
// clone is not a universal voice — `edge` and `azure` only read a catalogue, and handing
// one of them a card's name fails in the engine with "Invalid voice", which is a wrong
// answer to a question the form should not have asked. `qwen` has both, so where both
// exist they are grouped and labelled rather than run together: they are resolved from
// one namespace (the same one a run resolves --voice against) but they are not the same
// kind of thing, and which one you picked decides what a missing card means later.
function fillDemoVoices(d) {
  const eng = d.engines.find((e) => e.id === d.engine) || {};
  const lang = $("#demo-lang").value || "ru";
  // the engine's own catalogue as one group, then a group per voice CARD — the clones
  // are grouped by the person they are, because hearing one card's deliveries next to
  // each other is most of what the demo is for once there is more than one
  const groups = [
    [lab("js.demo-catalogue"), (eng.catalogue && eng.presets && eng.presets[lang]) || []],
  ].concat(eng.clones ? voiceGroups(d.cloned_cards) : [])
   .filter(([, names]) => names.length);
  const keep = $("#demo-voice").value;
  $("#demo-voice").innerHTML = groups.length === 0
    ? `<option value="">${lab("js.no-voices-yet")}</option>`
    : optgroupsHTML(groups, keep);
}

function wireDemo(d) {
  const langs = Object.keys(d.demo_text || { ru: "", en: "" });
  const langSel = $("#demo-lang"), voiceSel = $("#demo-voice"), textEl = $("#demo-text");
  const wasLang = langSel.value;
  langSel.innerHTML = langs.map((l) =>
    `<option${l === wasLang ? " selected" : ""}>${esc(l)}</option>`).join("");
  const setText = () => (textEl.value = d.demo_text[langSel.value] || "");
  if (!textEl.value) setText();
  langSel.onchange = () => { setText(); fillDemoVoices(d); };
  fillDemoVoices(d);

  $("#demo-go").onclick = async () => {
    const t = { engine: d.engine, voice: voiceSel.value, lang: langSel.value,
                text: textEl.value.trim() };
    if (!t.voice) return say(lab("js.demo-no-voice"), true);
    if (!t.text) return say(lab("js.demo-no-text"), true);
    const key = demoKey(t);
    // already spoken once — the whole point is not to pay for it twice
    if (demoTakes.has(key)) {
      const el = takeRow(demoTakes.get(key).id).querySelector("audio");
      el.currentTime = 0;
      el.play();
      return say(lab("js.demo-cached"));
    }
    const btn = $("#demo-go");
    btn.disabled = true;
    $("#demo-status").textContent = lab("js.demo-working");
    try {
      const r = await fetch("/api/tts/demo", { method: "POST",
        headers: { "content-type": "application/json" }, body: JSON.stringify(t) });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      const id = ++takeSeq;
      demoTakes.set(key, { ...t, id, url: URL.createObjectURL(await r.blob()) });
      drawTakes();
      takeRow(id).querySelector("audio").play();
      $("#demo-status").textContent = "";
    } catch (err) {
      $("#demo-status").textContent = "";
      say(`${lab("js.demo-failed")}: ${err.message}`, true);
    } finally { btn.disabled = false; }
  };
  drawTakes();
}

// What a measurement looks like on screen.
//
// The numbers are rendered from the fields rather than from the server's own one-line
// summary, because that summary is English prose and this screen is not. The PROBLEM
// texts are shown as they come: each one is a measured explanation written once in
// `tts/refs.py` — how far under it is, what it will do to every line made with it —
// and the terminal shows them the same way rather than keeping a second copy.
function dbLine(r) {
  const db = (v, silent) => (silent ? lab("js.silent") : v === null ? "?" : `${v} ${lab("js.db")}`);
  return `${r.duration.toFixed(1)} ${lab("js.sec")} · ${lab("js.peak")} ${db(r.peak_db, r.silent_peak)}` +
         ` · RMS ${db(r.rms_db)} · ${lab("js.floor")} ${db(r.floor_db, r.silent_floor)}`;
}

function reportHTML(d) {
  if (!d || !d.report) return "";
  const rows = [];
  if (d.before) rows.push(`<div class="dim">${lab("js.before")}: ${esc(dbLine(d.before))}</div>`);
  rows.push(`<div>${d.before ? lab("js.after") + ": " : ""}${esc(dbLine(d.report))}</div>`);
  if (d.said)
    rows.push(`<div class="dim">${lab("js.heard")}: ` +
      `${d.said.found}/${d.said.words} · ${Math.round(d.said.silent * 100)}% ` +
      `${lab("js.silence")} · ${lab("js.longest")} ${d.said.gap.toFixed(1)} ${lab("js.sec")}</div>`);
  const probs = [...(d.report.problems || []), ...((d.said && d.said.problems) || [])];
  for (const p of probs)
    rows.push(`<div class="${p.level === "error" ? "bad" : "warn"}">` +
              `${p.level === "error" ? "\u2717" : "\u26a0"} ${esc(p.text)}</div>`);
  return `<div class="report">${rows.join("")}</div>`;
}

// The voices room: a card is a person, and under it every recording of them, one per
// ROW. It used to be one full panel per recording — player, transcript box, three
// fields and five buttons, repeated — and a card with five intonations in it was a
// screen and a half of identical boxes you had to read to tell apart. A row says the
// three things you actually scan for (which one the video speaks with, what it is
// called, what it sounds like), and opens into the editor only when you ask it to.
let voicesData = [];    // what /api/voices last said, so a toggle costs no request
let voiceOpen = "";     // which delivery's editor is open, as `card  which`

const recKey = (card, which) => `${card}${which}`;
const clip = (t, n) => (t.length > n ? t.slice(0, n - 1) + "…" : t);

// What a row says about a recording while it is shut: how long it is, and — in the
// operator's own words, falling back to the transcript — what it is. The description is
// the interesting half and the one the writer reads too (see `llm/delivery.py`), which
// is a reason to have it in front of you while you cut the next take.
function recNote(s) {
  if (!s.has_sample) return `<span class="bad">${lab("js.the-sample-is-gone")}</span>`;
  const what = s.description || s.text;
  return `<span class="dim">${esc(s.seconds)} c${what ? " · " + esc(clip(what, 70)) : ""}</span>`;
}

const recRow = (card, s, open) => `
        <div class="rec" data-rec="${esc(s.which)}">
          <div class="rec-head${open ? " open" : ""}">
            <button data-toggle class="rec-name" aria-expanded="${open}">
              <span class="caret">${open ? "▾" : "▸"}</span>
              ${s.is_default
                ? `<span class="pill-on" title="${esc(lab("js.v.mainnote"))}">★</span>`
                : `<span class="pill-gap"></span>`}
              <b>${esc(s.which)}</b> ${recNote(s)}
            </button>
            ${s.is_default ? ""
              : `<button data-main class="ghost">${lab("js.v.makemain")}</button>`}
          </div>
          <div class="rec-body"${open ? "" : " hidden"}>
            ${sampleBody(s)}
            <div class="row">
              <button data-check class="ghost">${lab("js.check")}</button>
              <button data-clean class="ghost">${lab("js.denoise")}</button>
              <span class="grow"></span>
              <button data-save class="primary">${lab("js.save")}</button>
              <button data-del class="ghost danger">${lab("js.delete")}</button>
            </div>
          </div>
        </div>`;

const sampleBody = (s) => `
      <div data-report></div>
      ${s.has_sample ? `<audio controls preload="none" src="${tokd(s.url)}"></audio>` : ""}
      <label>${lab("js.v.rectext")}
        <textarea data-f="text" rows="2">${esc(s.text)}</textarea></label>
      <div class="grid">
        <label>${lab("js.v.recname")}<input data-rename value="${esc(s.which)}"></label>
        <label>${lab("js.v.recdescr")}<input data-f="description" value="${esc(s.description)}"
          placeholder="${esc(lab("js.v.recdescr.ph"))}"></label>
        <label class="wide">${lab("js.sample-url-for-cloud-engines")}<input data-f="ref_url" value="${esc(s.ref_url)}"></label>
      </div>`;

// The card: the person, and what belongs to them rather than to any one take of them.
// Its own line says the two things that are true of the whole card — the language, and
// which delivery it speaks with — and everything under it is a recording.
const cardHead = (v) => `
      <div class="row card-head"><b>${esc(v.name)}</b>
        <span class="dim">${esc(v.lang)} · ${v.samples.length
          ? `★ ${esc(v.default)}` : lab("js.v.none")}</span>
        <span class="grow"></span>
        <button data-cardsave class="primary">${lab("js.save")}</button>
        <button data-carddel class="ghost danger">${lab("js.delete")}</button></div>
      <div class="grid card-fields">
        <label>${lab("web.f.title")}<input data-rename value="${esc(v.name)}"></label>
        <label>${lab("js.language")}<input data-cf="lang" value="${esc(v.lang)}"></label>
        <label class="wide">${lab("js.v.who")}<input data-cf="description" value="${esc(v.description)}"></label>
      </div>`;

// …and the deliveries themselves, which are the only intonation control a cloning
// engine has: it imitates the reading of the sample it was shown, so an angry line is
// an angry TAKE of the same person and not a parameter. They sit on one level and the
// card points at one of them; `марта:зло` pins a run or a line to a delivery by name,
// while a bare `марта` is "whatever this card's default is" and follows the star.
//
// The import form is behind a button for the same reason the editors are shut: it is
// four fields and a file box, and a card is read far more often than it is added to.
const deliveries = (v, adding) => `
      <div class="recs">
        <div class="row"><b>${lab("js.v.deliveries")}</b></div>
        ${v.samples.map((s) => recRow(v.name, s, voiceOpen === recKey(v.name, s.which))).join("")
          || `<p class="dim">${lab("js.v.none")}</p>`}
        <div class="row">
          <button data-addrec class="ghost">${adding ? "−" : "+"} ${lab("js.v.addrec")}</button>
        </div>
        <form data-recnew class="grid"${adding ? "" : " hidden"}>
          <label>${lab("js.v.recname")}<input name="as" required placeholder="зло"></label>
          <label>${lab("js.v.recdescr")}<input name="description"
            placeholder="${esc(lab("js.v.recdescr.ph"))}"></label>
          <label class="wide">${lab("js.v.rectext")}
            <textarea name="text" rows="2"></textarea></label>
          <label class="wide">${lab("web.f.sample")}
            <input type="file" name="file" accept="audio/*" required></label>
          <label class="inline"><input type="checkbox" name="clean" value="true">
            <span>${lab("web.f.denoise")}</span></label>
          <label class="inline"><input type="checkbox" name="default" value="true">
            <span>${lab("js.v.newmain")}</span></label>
          <div class="row"><span class="grow"></span>
            <button class="primary">${lab("js.v.addrec")}</button></div>
        </form>
      </div>`;

async function loadVoices() {
  voicesData = await api("/api/voices");
  drawVoices();
}

// Drawn from what the last request said, so opening a row, shutting one or reaching for
// the import form costs nothing and cannot race with anything.
function drawVoices(adding = "") {
  $("#voices").innerHTML = voicesData.map((v) => `
    <div class="panel cfg-item" data-voice="${esc(v.name)}">
      ${cardHead(v)}
      ${deliveries(v, adding === v.name)}
    </div>`).join("") || `<p class="empty">${lab("js.no-voices-yet")}</p>`;
  $("#voices").querySelectorAll("[data-voice]").forEach((el) => {
    const name = el.dataset.voice;
    bindCard(el, name);
    el.querySelectorAll("[data-rec]").forEach((r) => {
      bindSample(r, name, r.dataset.rec);
      r.querySelector("[data-toggle]").onclick = () => {
        const key = recKey(name, r.dataset.rec);
        voiceOpen = voiceOpen === key ? "" : key;
        drawVoices(adding);
      };
    });
    el.querySelector("[data-addrec]").onclick = () =>
      drawVoices(adding === name ? "" : name);
    el.querySelector("[data-recnew]").onsubmit = async (e) => {
      e.preventDefault();
      const btn = e.target.querySelector("button");
      btn.disabled = true;
      try {
        // the card is named by the row this form sits in, not by a field in it
        const fd = new FormData(e.target);
        fd.append("name", name);
        const r = await api("/api/voices", { method: "POST", body: fd });
        say(lab("js.v.recadded"));
        // …and the new recording opens, because the next thing anybody does with a take
        // just imported is listen to it and read what the check says about it
        voiceOpen = recKey(name, r.added);
        await loadVoices();
        showReport(name, r.added, r);
      } catch (err) { say(err.message, true); }
      finally { btn.disabled = false; }
    };
  });
}

// The card's own fields and its delete. Scoped with `data-cf` rather than `data-f` so
// that saving the person never picks up a delivery's transcript out of a row below it.
function bindCard(el, name) {
  const url = `/api/voices/${encodeURIComponent(name)}`;
  el.querySelector("[data-cardsave]").onclick = async () => {
    const body = {};
    el.querySelectorAll("[data-cf]").forEach((i) => (body[i.dataset.cf] = i.value));
    // the name is a field like any other, and Save is what commits it: a rename moves
    // the card's file, the recordings named after it and the content types pointing at
    // it, and says so (see `ConfigStore.rename_voice`)
    body.rename = el.querySelector(".card-fields [data-rename]").value;
    const r = await api(url, { method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify(body) });
    sayRename(name, body.rename, r, lab("js.saved"));
    loadVoices();
  };
  el.querySelector("[data-carddel]").onclick = async () => {
    await api(url, { method: "DELETE" });
    say(`${name} ${lab("js.deleted")}`);
    loadVoices();
  };
}

// Where a freshly imported recording's measurement goes, once the list has been drawn
// again: onto its own row, and not into a box that is about to be replaced.
function showReport(name, which, r) {
  const card = document.querySelector(`#voices [data-voice="${CSS.escape(name)}"]`);
  if (!card) return;
  const holder = card.querySelector(`[data-rec="${CSS.escape(which)}"]`) || card;
  const box = holder && holder.querySelector("[data-report]");
  if (box) box.innerHTML = reportHTML(r);
}

// What to say after a Save that may also have been a rename. The notes come from the
// server and are the things the operator cannot see from here: a wav that moved, a
// content type that now names something else — and the one that never moves, a line
// already pinned inside a run.
function sayRename(was, now, reply, saved) {
  const notes = (reply && reply.notes) || [];
  if (was === now && !notes.length) { say(`${was} ${saved}`); return; }
  say(was === now ? `${was} ${saved}`
                  : `${was} → ${now}${notes.length ? " · " + notes.join(" · ") : ""}`);
  if (was !== now) say(lab("js.v.renamednote"));
}

// Bind one delivery's buttons. `el` is that delivery's own panel — nothing is nested
// inside anything else now, so every field this reads belongs to this recording.
function bindSample(el, name, which) {
  const q = `?which=${encodeURIComponent(which)}`;
  const at = (verb) => `/api/voices/${encodeURIComponent(name)}/${verb}${q}`;
  const title = `${name}:${which}`;
  el.querySelector("[data-save]").onclick = async () => {
    const body = {};
    el.querySelectorAll("[data-f]").forEach((i) => (body[i.dataset.f] = i.value));
    body.rename = el.querySelector("[data-rename]").value;
    const r = await api(`/api/voices/${encodeURIComponent(name)}${q}`, { method: "PUT",
      headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    sayRename(title, `${name}:${body.rename}`, r, lab("js.saved"));
    // a renamed delivery has a new address, and the panel bound to the old one would
    // edit nothing — so the room is drawn again rather than left looking right
    if (body.rename !== which) loadVoices();
  };
  el.querySelector("[data-del]").onclick = async () => {
    await api(`/api/voices/${encodeURIComponent(name)}${q}`, { method: "DELETE" });
    say(`${title} ${lab("js.deleted")}`);
    loadVoices();
  };
  // Point the card at this delivery. One button and not a form field, because it is not
  // a property of the recording being edited — it is the card's answer to "what does
  // this voice sound like", and pressing it changes what every unpinned line in every
  // run with this voice comes out as.
  const main = el.querySelector("[data-main]");
  if (main) main.onclick = async () => {
    await api(`/api/voices/${encodeURIComponent(name)}`, { method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ default: which }) });
    say(`${name} → ${which} ${lab("js.v.moved")}`);
    loadVoices();
  };
  // Measuring and denoising are two buttons rather than one, and denoising is not
  // something the card does to itself: it CHANGES the recording, in place, and
  // RNNoise is not idempotent — pressing it twice keeps eating at what is left.
  const box = el.querySelector("[data-report]");
  const work = async (btn, what, url) => {
    btn.disabled = true;
    box.innerHTML = `<div class="dim">${what}</div>`;
    try {
      const r = await api(url, { method: "POST" });
      box.innerHTML = reportHTML(r);
      if (r.before) say(`${title} — ${lab("js.cleaned")}`);
    } catch (err) { box.innerHTML = ""; say(err.message, true); }
    finally { btn.disabled = false; }
  };
  el.querySelector("[data-check]").onclick = (e) => work(e.target, lab("js.measuring"), at("check"));
  el.querySelector("[data-clean]").onclick = (e) => work(e.target, lab("js.cleaning"), at("clean"));
}

$("#voice-new").onsubmit = async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    const r = await api("/api/voices", { method: "POST", body: new FormData(e.target) });
    e.target.reset();
    say(lab("js.voice-added"));
    await loadVoices();
    showReport(r.name, r.added, r);
  } catch (err) { say(err.message, true); }
  finally { btn.disabled = false; }
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

// ------------------------------------------------------------- ad contracts
//
// A contract is ONE sponsor said three ways at once — a banner in the corner, a
// mention inside the narration, a line under the video — and the generic config form
// said none of them. `overlay` and `native` are nested blocks, and a nested block in
// that form is a one-line text box reading `[object Object]`: unreadable, and worse
// than unreadable when saved, because the string went back over the block and the
// model refused the whole contract. `modes` was a JSON array to be typed by hand,
// which is the same fact as the blocks said a second time and a second place to get
// it wrong: the pipeline wants BOTH the block and the mode before it will run that
// half of the ad (see `pipeline/context.overlay_ad_on`).
//
// So: one card per contract, one block per way of advertising, and a tick on the two
// that are config blocks of their own. The tick is the only thing that decides — it
// writes the block and the mode together, and they cannot disagree because there is
// no longer anywhere to disagree. Everything under a tick belongs to that half and is
// gone from the screen when it is off.
//
// The card is otherwise about showing what a number MEANS. `width = 340` is pixels of
// a frame the page knows the width of, so it is drawn in place; `{url}` is a
// substitution, so the finished line is written out underneath; the assets folder is
// a path that fails four stages into a run, so the files in it are counted here.

let adsData = null;
// What a half held before it was ticked off, so ticking it back on does not hand back
// an empty form. Per name, in memory only: an undo for a gesture, not a draft store.
const adKept = {};

const AD_CORNERS = ["top_left", "top_right", "bottom_left", "bottom_right"];
// Where the renderer actually puts the thing (`media/ffmpeg._overlay_xy`), in the
// video's own pixels — a narrow margin at the sides and a wide band top and bottom.
// Copied rather than guessed at, so the banner sits in the preview where it will sit
// in the video; a preview that is only roughly right about the corner is a preview
// that has to be checked against a render anyway, which is the thing it is for.
const AD_MARGIN = 40, AD_TOP = 140, AD_BOTTOM = 420;

const adDefaults = (name, half) => half === "overlay"
  ? { assets_dir: `assets/ads/${name}/overlay`, text: "", position: "top_right",
      start_s: 6, duration_s: 8, width: 340 }
  : { assets_dir: `assets/ads/${name}/native`, talking_points: "" };

async function loadAds() {
  adsData = await api("/api/configs/ads");
  drawAds();
  $("#ad-add").onclick = async () => {
    const name = $("#ad-new").value.trim();
    if (!name) { say(lab("js.needs-a-name"), true); return; }
    // A new contract starts with both halves on and both folders named after it. The
    // empty-body default is a contract that advertises in no way at all, which is a
    // worse thing to hand somebody than two blocks they can empty.
    await api(`/api/configs/ads/${encodeURIComponent(name)}`, { method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ modes: ["overlay", "native"],
                             overlay: adDefaults(name, "overlay"),
                             native: adDefaults(name, "native") }) });
    $("#ad-new").value = "";
    loadAds();
  };
}

function drawAds() {
  const names = Object.keys(adsData.items);
  $("#ad-items").innerHTML = names.map((n) => adCard(n, adsData.items[n])).join("")
    || `<p class="empty">${lab("js.nothing-here-yet")}</p>`;
  bindAds();
}

// The frame, in its own pixels — an overlay is placed and sized in them.
const adFrame = () => ({ w: videoWidth, h: Math.round(videoWidth / videoAspect) });

function adCard(name, c) {
  const on = (half) => !!c[half];
  const nothing = !on("overlay") && !on("native") && !(c.description || {}).snippet;
  return `<div class="panel cfg-item ad-card" data-ad="${esc(name)}">
    <div class="row"><b>${esc(name)}</b>
      ${nothing ? `<span class="pill-off">${esc(lab("js.ad.silent"))}</span>` : ""}
      <span class="grow"></span>
      <button data-save class="primary">${lab("js.save")}</button>
      <button data-del class="ghost">${lab("js.delete")}</button></div>

    <label class="wide">${esc(lab("js.ad.link"))}
      <input data-a="url" value="${esc(c.url || "")}" placeholder="${esc(lab("js.ad.link.ph"))}">
      <span class="dim">${esc(lab("js.ad.link.note"))}</span></label>

    ${adHalf(name, "overlay", c)}
    ${adHalf(name, "native", c)}
    ${adDescr(c)}
    <p class="dim">${esc(lab("js.ad.perrun"))}</p>
  </div>`;
}

// One half of a contract: the tick that decides whether it exists, and its own fields
// under it. Folded away when off, because a form for something switched off is a form
// that invites being filled in and then ignored.
function adHalf(name, half, c) {
  const block = c[half];
  return `<div class="ad-half${block ? " on" : ""}" data-half="${half}">
    <label class="inline"><input type="checkbox" data-on="${half}"${block ? " checked" : ""}>
      <b>${esc(lab("js.ad." + half))}</b>
      <span class="dim">${esc(lab("js.ad." + half + ".note"))}</span></label>
    ${block ? `<div class="ad-body">${
      half === "overlay" ? adOverlay(name, block) : adNative(name, block)}</div>` : ""}
  </div>`;
}

function adOverlay(name, ov) {
  const f = adFrame();
  const pct = Math.round((ov.width / f.w) * 100);
  return `<div class="ad-split">
    <div class="ad-screen" style="aspect-ratio:${f.w}/${f.h}">
      ${AD_CORNERS.map((p) => `<button class="ad-corner ${p}${p === ov.position ? " on" : ""}"
        data-pos="${p}" title="${esc(lab("fx.place." + p, p))}"></button>`).join("")}
      <div class="ad-banner" style="${adBannerStyle(ov, f)}">
        <i>${esc(lab("js.ad.banner"))}</i>
        <span class="cap${ov.text ? "" : " none"}">${esc(ov.text || lab("js.ad.nocap"))}</span>
      </div>
    </div>
    <div class="ad-fields">
      <label class="wide">${esc(lab("js.ad.caption"))}
        <input data-a="overlay.text" value="${esc(ov.text || "")}"
               placeholder="${esc(lab("js.ad.caption.ph"))}"></label>
      <label class="wide">${esc(lab("js.ad.width"))}
        <span class="row"><input type="range" min="40" max="${f.w}" step="10"
               data-a="overlay.width" value="${esc(ov.width)}">
        <b class="ad-wide">${esc(ov.width)} px · ${pct}%</b></span></label>
      <div class="grid">
        <label>${esc(lab("js.ad.from"))}
          <input type="number" step="0.5" min="0" data-a="overlay.start_s" value="${esc(ov.start_s)}"></label>
        <label>${esc(lab("js.ad.holds"))}
          <input type="number" step="0.5" min="0.5" data-a="overlay.duration_s" value="${esc(ov.duration_s)}"></label>
      </div>
      <p class="dim ad-when">${esc(adWhen(ov))}</p>
      ${adFolder(name, "overlay", ov)}
    </div>
  </div>`;
}

const adWhen = (ov) => `${lab("js.ad.window")} ${(+ov.start_s).toFixed(1)}–${
  (+ov.start_s + +ov.duration_s).toFixed(1)} ${lab("js.s")}. ${lab("js.ad.short")}`;

function adBannerStyle(ov, f) {
  const w = Math.min(Math.max(ov.width / f.w, 0.05), 0.95) * 100;
  const side = ov.position.endsWith("left")
    ? `left:${(AD_MARGIN / f.w) * 100}%` : `right:${(AD_MARGIN / f.w) * 100}%`;
  const vert = ov.position.startsWith("top")
    ? `top:${(AD_TOP / f.h) * 100}%` : `bottom:${(AD_BOTTOM / f.h) * 100}%`;
  // The caption hangs UNDER the picture in both bands — the renderer pins it fourteen
  // pixels below the banner's bottom edge whichever corner it is in
  // (`media/ffmpeg._delivery_cmd`), so the preview does not flip it for the lower one.
  return `width:${w}%;${side};${vert}`;
}

function adNative(name, na) {
  return `<label class="wide">${esc(lab("js.ad.points"))}
      <textarea data-a="native.talking_points" rows="3"
        placeholder="${esc(lab("js.ad.points.ph"))}">${esc(na.talking_points || "")}</textarea>
      <span class="dim">${esc(lab("js.ad.points.note"))}</span></label>
    ${adFolder(name, "native", na)}`;
}

// The folder, and what is in it. The count is the server's (`web/app._ad_assets`) and
// therefore belongs to the path as SAVED — so editing the path greys it out and says
// so, rather than going on reporting somebody else's folder.
function adFolder(name, half, block) {
  const a = (adsData.assets[name] || {})[half];
  const dir = block.assets_dir || "";
  // No count at all means this half was ticked on a moment ago and the folder it names
  // has not been looked at yet — the same "ask me again once you save" as a path that
  // has just been retyped, and said in the same words.
  let state = "", stale = !a;
  if (a) {
    if (!a.there) state = `<span class="bad">${esc(lab("js.ad.nofolder"))}</span>`;
    else if (!a.count) state = `<span class="bad">${esc(lab("js.ad.empty." + half))}</span>`;
    // count after the word, not before it: "1 файлов" is the price of putting a
    // number in front of a Russian noun, and a colon costs nothing
    else state = `<span class="ok">${esc(lab("js.ad.files." + half))}: ${a.count}</span>` +
      `<span class="dim"> — ${esc(a.names.join(", "))}</span>`;
  }
  return `<label class="wide">${esc(lab("js.ad.folder"))}
    <input data-a="${half}.assets_dir" data-dir="${esc(a ? a.dir : "")}" value="${esc(dir)}">
    <span class="dim">${esc(lab("js.ad.folder." + half))}</span></label>
  <p class="ad-files${stale ? " stale" : ""}" data-stale="${esc(lab("js.ad.stale"))}">${state}</p>`;
}

// The line under the video. `{url}` is the whole reason this field is not just text,
// so the finished line is written out under it — with the link the field above holds.
function adDescr(c) {
  const snip = (c.description || {}).snippet || "";
  // Always here, never ticked: there is no block to create, only a line to write. The
  // lit edge follows whether anything is written, which is the same question the two
  // ticks above answer for their own halves.
  return `<div class="ad-half${snip ? " on" : ""}" data-half="description">
    <label class="inline"><b>${esc(lab("js.ad.descr"))}</b>
      <span class="dim">${esc(lab("js.ad.descr.note"))}</span></label>
    <div class="ad-body">
      <label class="wide"><textarea data-a="description.snippet" rows="2"
        placeholder="${esc(lab("js.ad.snippet.ph"))}">${esc(snip)}</textarea></label>
      <div class="row">
        <button class="ghost" data-url>${esc(lab("js.ad.puturl"))}</button>
        <span class="dim ad-out">${esc(adOut(c))}</span></div>
    </div>
  </div>`;
}

const adOut = (c) => {
  const snip = (c.description || {}).snippet || "";
  return snip ? `→ ${snip.replaceAll("{url}", c.url || lab("js.ad.nolink"))}` : "";
};

function bindAds() {
  $("#ad-items").querySelectorAll("[data-ad]").forEach((el) => {
    const name = el.dataset.ad;
    const c = adsData.items[name];

    // Every field writes straight into the contract, so the card is never a second
    // copy of it: the preview under a slider, the finished description line and the
    // body that is saved all read the one object.
    el.querySelectorAll("[data-a]").forEach((inp) => {
      inp.oninput = () => {
        const [a, b] = inp.dataset.a.split(".");
        const v = inp.type === "number" || inp.type === "range"
          ? (inp.value === "" ? 0 : +inp.value) : inp.value;
        if (b) { (c[a] = c[a] || {})[b] = v; } else c[a] = v;
        adLive(el, c, inp);
      };
    });

    el.querySelectorAll("[data-on]").forEach((box) => {
      box.onchange = () => {
        const half = box.dataset.on;
        if (box.checked) c[half] = (adKept[name] || {})[half] || adDefaults(name, half);
        else {
          (adKept[name] = adKept[name] || {})[half] = c[half];
          c[half] = null;
        }
        // modes and the block are one answer: written here, together, and nowhere else
        c.modes = ["overlay", "native"].filter((h) => !!c[h]);
        drawAds();
      };
    });

    const put = el.querySelector("[data-url]");
    if (put) put.onclick = () => {
      const box = el.querySelector('[data-a="description.snippet"]');
      const at = box.selectionStart ?? box.value.length;
      box.value = box.value.slice(0, at) + "{url}" + box.value.slice(box.selectionEnd ?? at);
      box.focus();
      box.selectionStart = box.selectionEnd = at + 5;
      box.dispatchEvent(new Event("input"));
    };

    el.querySelector("[data-save]").onclick = async () => {
      try {
        await api(`/api/configs/ads/${encodeURIComponent(name)}`, { method: "PUT",
          headers: { "content-type": "application/json" }, body: JSON.stringify(c) });
        say(`${name} ${lab("js.saved")}`);
        loadAds();   // the folders are counted again, against the paths just written
      } catch (e) { say(e.message, true); }
    };
    el.querySelector("[data-del]").onclick = async () => {
      await api(`/api/configs/ads/${encodeURIComponent(name)}`, { method: "DELETE" });
      say(`${name} ${lab("js.deleted")}`);
      loadAds();
    };

    el.querySelectorAll(".ad-corner").forEach((b) => {
      b.onclick = () => {
        c.overlay.position = b.dataset.pos;
        el.querySelectorAll(".ad-corner").forEach((o) => o.classList.toggle("on", o === b));
        adLive(el, c);
      };
    });
  });
}

// Redraw only what a keystroke can change, in place. Rebuilding the card would be
// simpler and would take the focus and the caret with it on every character typed.
function adLive(el, c, inp) {
  const out = el.querySelector(".ad-out");
  if (out) out.textContent = adOut(c);
  const descr = el.querySelector('[data-half="description"]');
  if (descr) descr.classList.toggle("on", !!(c.description || {}).snippet);
  if (inp && inp.dataset.a.endsWith("assets_dir")) {
    const p = inp.closest("label").nextElementSibling;
    p.classList.toggle("stale", inp.value.trim() !== inp.dataset.dir);
  }
  const ov = c.overlay;
  if (!ov) return;
  const f = adFrame();
  const banner = el.querySelector(".ad-banner");
  if (banner) {
    banner.setAttribute("style", adBannerStyle(ov, f));
    const cap = banner.querySelector(".cap");
    cap.textContent = ov.text || lab("js.ad.nocap");
    cap.classList.toggle("none", !ov.text);
  }
  const wide = el.querySelector(".ad-wide");
  if (wide) wide.textContent = `${ov.width} px · ${Math.round((ov.width / f.w) * 100)}%`;
  const when = el.querySelector(".ad-when");
  if (when) when.textContent = adWhen(ov);
}

// ------------------------------------------------------------- the effects base
//
// The other half of what a picture can do. The frame base says WHAT is on screen; this
// says what goes off on top of it — an arrow, a circle, a sting — and the editor here
// is where an effect's animation is written down, once, for every video that ever
// fires it.
//
// The preview is a 9:16 box with the thing in it, run off the very keys in the list
// beside it. Approximate, exactly as the montage canvas is: what the render does is
// ffmpeg's answer, and the question this box answers is "does 0.15s of fade read as a
// pop or as a stutter", which is a question about taste and can be asked in a browser.

let fxAll = [];
let fxCur = null;      // the effect being edited
let fxKeys = [];       // its animation, as the list is currently edited
let fxTimer = null;    // the preview's own clock

const FX_ANCHORS = ["point", "screen", "full"];
const FX_PLACES = ["center", "top", "bottom", "left", "right",
                   "top_left", "top_right", "bottom_left", "bottom_right"];
const FX_FILLS = ["hold", "clip"];

// Ready-made animations, as the moments they are made of. They are a starting point
// and not a type: pressing one fills the list, and the list is what is saved — so an
// operator who wants the pop a tenth slower edits a number instead of asking for a
// switch nobody has built yet. `h` is the effect's own hold, so a preset stretches to
// whatever length the thing is up for.
const FX_PRESETS = {
  none: () => [],
  rise: (h) => [
    { at: 0, scale: 0.85, dx: 0, dy: 0.12, alpha: 0 },
    { at: Math.min(0.22, h / 3), scale: 1, dx: 0, dy: 0, alpha: 1 },
    { at: Math.max(h - 0.25, h / 2), scale: 1, dx: 0, dy: 0, alpha: 1 },
    { at: h, scale: 1.04, dx: 0, dy: -0.12, alpha: 0 },
  ],
  pop: (h) => [
    { at: 0, scale: 0.35, dx: 0, dy: 0, alpha: 0 },
    { at: Math.min(0.12, h / 4), scale: 1.18, dx: 0, dy: 0, alpha: 1 },
    { at: Math.min(0.26, h / 2), scale: 1, dx: 0, dy: 0, alpha: 1 },
    { at: Math.max(h - 0.15, h / 2), scale: 1, dx: 0, dy: 0, alpha: 1 },
    { at: h, scale: 1, dx: 0, dy: 0, alpha: 0 },
  ],
  blink: (h) => [
    { at: 0, scale: 1, dx: 0, dy: 0, alpha: 1 },
    { at: h * 0.25, scale: 1, dx: 0, dy: 0, alpha: 0.15 },
    { at: h * 0.5, scale: 1, dx: 0, dy: 0, alpha: 1 },
    { at: h * 0.75, scale: 1, dx: 0, dy: 0, alpha: 0.15 },
    { at: h, scale: 1, dx: 0, dy: 0, alpha: 1 },
  ],
  float: (h) => [
    { at: 0, scale: 1, dx: 0, dy: 0.25, alpha: 0 },
    { at: Math.min(0.3, h / 3), scale: 1, dx: 0, dy: 0, alpha: 1 },
    { at: h, scale: 1.1, dx: 0, dy: -0.7, alpha: 0 },
  ],
};

async function loadEffects() {
  fxAll = await api("/api/effects");
  $("#fx-state").textContent = fxAll.length
    ? `${fxAll.length} · ${fxAll.filter((e) => e.usable).length} ${lab("js.fx.live")}`
    : "";
  $("#fx-cards").innerHTML = fxAll.map((e) => `
    <div class="frame-card ${e.retired ? "retired" : ""}" data-fx="${esc(e.name)}">
      <div class="thumb ${e.usable ? "" : "none"} ${e.kind === "sound" ? "sound" : ""}"
           ${e.url ? `style="background-image:url('${tokd(e.url)}')"` : ""}>
        ${e.kind === "sound" ? "🔊" : e.usable ? "" : lab("js.no-picture-yet")}
        <span class="pills">
          ${e.sound_url && e.kind !== "sound" ? `<span class="pill">🔊</span>` : ""}
          ${e.anchor !== "screen" ? `<span class="pill">${esc(lab("fx.anchor." + e.anchor, e.anchor))}</span>` : ""}
        </span>
      </div>
      <div class="meta"><b>${esc(e.name)}</b><span>${esc((e.description || "").slice(0, 60))}</span></div>
    </div>`).join("") || `<p class="empty">${lab("js.fx.none")}</p>`;
  $("#fx-cards").querySelectorAll("[data-fx]").forEach((el) => {
    el.onclick = () => openEffect(el.dataset.fx);
  });
  if (fxCur) {
    const again = fxAll.find((e) => e.name === fxCur.name);
    if (again) openEffect(again.name); else closeEffect();
  }
}

function closeEffect() {
  fxCur = null;
  stopFxPreview();
  $("#fx-editor").hidden = true;
}

function openEffect(name) {
  fxCur = fxAll.find((e) => e.name === name);
  if (!fxCur) return;
  fxKeys = (fxCur.keys || []).map((k) => ({ ...k }));
  $("#fx-editor").hidden = false;
  $("#fx-name").textContent = fxCur.name;
  $("#fx-descr").value = fxCur.description || "";
  $("#fx-note").value = fxCur.note || "";
  $("#fx-width").value = fxCur.width;
  $("#fx-hold").value = fxCur.hold;
  $("#fx-volume").value = fxCur.volume;
  $("#fx-retired").checked = !!fxCur.retired;
  $("#fx-del").textContent = lab("web.fx.del");
  $("#fx-del").classList.remove("danger");
  fillSel("#fx-anchor", FX_ANCHORS, fxCur.anchor, "fx.anchor.");
  fillSel("#fx-place", FX_PLACES, fxCur.place, "fx.place.");
  fillSel("#fx-fill", FX_FILLS, fxCur.fill, "fx.fill.");
  $("#fx-material").textContent = [
    fxCur.url ? lab("js.fx.haspic") : lab("js.fx.nopic"),
    fxCur.sound_url ? lab("js.fx.hassound") : lab("js.fx.nosound"),
  ].join(" · ");
  fxLoop = { from: fxCur.loop_from || 0, to: fxCur.loop_to || 0,
             loops: fxCur.loops || 1 };
  fxNotes();
  drawFxLoop();
  fxTime = 0;
  fxKeySel = fxKeys.length ? 0 : -1;
  // the picture is re-inserted per effect, and the two handles have to survive that:
  // they are part of the control, not part of what is being controlled
  $("#fx-piece").innerHTML =
    `<span class="grip" title="${esc(lab("js.fx.grip"))}"></span>`
    + `<span class="spin" title="${esc(lab("js.fx.spin"))}"></span>`;
  drawFxPresets();
  drawFxTrack();
  drawFxStage();
  bindFxStage();
}

function fillSel(sel, names, value, prefix) {
  const el = $(sel);
  el.innerHTML = names.map((n) =>
    `<option value="${n}">${esc(lab(prefix + n, n))}</option>`).join("");
  el.value = value;
}

function fxNotes() {
  // What `width` is a fraction OF depends on where the thing ends up, and the two
  // fields it does NOT apply to are dimmed rather than hidden: a control that is
  // simply gone reads as a control this build does not have.
  const anchor = $("#fx-anchor").value;
  $("#fx-width-note").textContent = lab("js.fx.width." + anchor, lab("js.fx.width.screen"));
  $("#fx-place").closest("label").classList.toggle("off", anchor !== "screen");
  $("#fx-width").closest("label").classList.toggle("off", anchor === "full");
}

function drawFxPresets() {
  $("#fx-presets").innerHTML = Object.keys(FX_PRESETS).map((k) =>
    `<button data-preset="${k}">${esc(lab("js.fx.preset." + k, k))}</button>`).join("");
  $("#fx-presets").querySelectorAll("[data-preset]").forEach((b) => {
    b.onclick = () => {
      fxKeys = FX_PRESETS[b.dataset.preset](fxHold())
        .map((k) => ({ ...k, at: +k.at.toFixed(2) }));
      fxKeySel = fxKeys.length ? 0 : -1;
      drawFxTrack();
      drawFxStage();
    };
  });
}

// -- the animation, as a stage and a strip -----------------------------------
//
// Two controls and they are the same edit seen twice: WHERE the thing is, dragged on
// a canvas the shape of the video, and WHEN it is there, on a strip of moments under
// it. The numbers stay — an operator who knows they want 0.15s types 0.15 — but they
// are no longer the only way in, which is what made the first version of this screen
// a spreadsheet about a picture nobody could see.

let fxTime = 0;      // the playhead, in seconds into the FIRING (loops included)
let fxKeySel = -1;   // which moment is being edited
let fxPlaying = false;
let fxLoop = { from: 0, to: 0, loops: 1 };   // the two separators and the count

// Two clocks, and keeping them apart is the whole of the loop. The ANIMATION's clock
// is where the moments and the separators live and it is what the strip draws; the
// FIRING's clock is how long the thing is actually up for, which is the way in plus
// the middle however many times plus the way out. The browser's copy of
// `EffectSpec.clock` / `.span`, and it has to stay a copy: the preview is only worth
// looking at while it agrees with the render.
const fxHold = () => Math.max(+$("#fx-hold").value || 1.2, 0.2);
const fxCycles = () => fxLoop.to - fxLoop.from > 0.02;
const fxCycleLen = () => Math.max(fxLoop.to - fxLoop.from, 0.05);
const fxLoopN = () => Math.max(fxLoop.loops | 0, 1);

function fxSpan() {
  if (!fxCycles()) return fxHold();
  return fxLoop.from + fxLoopN() * fxCycleLen() + Math.max(fxHold() - fxLoop.to, 0);
}

/** firing seconds → animation seconds */
function fxClock(t) {
  if (!fxCycles()) return t;
  if (t < fxLoop.from) return t;
  const ran = t - fxLoop.from, total = fxLoopN() * fxCycleLen();
  if (ran < total - 1e-9) return fxLoop.from + (ran % fxCycleLen());
  return fxLoop.to + (ran - total);
}

/** animation seconds → firing seconds, taking the FIRST pass through the middle —
 *  which is what pressing a place on the strip can honestly mean */
function fxFiring(ct) {
  if (!fxCycles() || ct <= fxLoop.to) return ct;
  return fxLoop.from + fxLoopN() * fxCycleLen() + (ct - fxLoop.to);
}

function fxKeyAt(t) {
  // the moment being edited, if the playhead is on one — within a hair, since a
  // marker is dragged in seconds and «on it» cannot be an equality
  return fxKeys.findIndex((k) => Math.abs(k.at - t) < 0.02);
}

function drawFxTrack() {
  const hold = fxHold();
  const px = (t) => (Math.min(Math.max(t, 0), hold) / hold) * 100;
  const step = hold > 4 ? 1 : hold > 1.6 ? 0.5 : 0.2;
  let ticks = "";
  for (let t = 0; t <= hold + 1e-6; t += step)
    ticks += `<i style="left:${px(t)}%"><b>${t.toFixed(step < 1 ? 1 : 0)}</b></i>`;
  $("#fx-ruler").innerHTML = ticks;
  // the middle, shaded, with a handle at each end — the three parts have to be
  // legible at a glance or the moments in them mean nothing
  $("#fx-seps").innerHTML = fxCycles()
    ? `<i class="band" style="left:${px(fxLoop.from)}%;width:${px(fxLoop.to) - px(fxLoop.from)}%"></i>
       <i class="sep" data-sep="from" style="left:${px(fxLoop.from)}%"></i>
       <i class="sep" data-sep="to" style="left:${px(fxLoop.to)}%"></i>` : "";
  $("#fx-keyrow").innerHTML = fxKeys.map((k, i) =>
    `<i class="fxk${i === fxKeySel ? " on" : ""}" data-k="${i}" style="left:${px(k.at)}%"
        title="${k.at.toFixed(2)}${lab("js.s")}"></i>`).join("");
  // the head is at the moment of the ANIMATION being shown, so during a repeat it
  // runs the middle again and again — which is the loop, seen
  $("#fx-head").style.left = px(fxClock(fxTime)) + "%";
  $("#fx-clock").textContent = fxCycles()
    ? `${fxTime.toFixed(2)} / ${fxSpan().toFixed(2)} · ×${fxLoopN()}`
    : `${fxTime.toFixed(2)} / ${hold.toFixed(2)}`;
  bindFxTrack();
  drawFxKeyFields();
}

function bindFxTrack() {
  const track = $("#fx-track");
  const seek = (e) => {
    const r = track.getBoundingClientRect();
    const ct = Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1) * fxHold();
    fxTime = fxFiring(ct);
    const on = fxKeyAt(ct);
    if (on >= 0) fxKeySel = on;
    drawFxTrack();
    drawFxStage();
  };
  track.onpointerdown = (e) => {
    if (e.target.classList.contains("fxk") || e.target.classList.contains("sep")) return;
    fxPlaying = false;
    seek(e);
  };
  bindFxSeps();
  $("#fx-keyrow").querySelectorAll(".fxk").forEach((el) => {
    const i = +el.dataset.k;
    el.onpointerdown = (e) => {
      e.stopPropagation();
      fxPlaying = false;
      fxKeySel = i;
      // the playhead goes to the moment being taken hold of, because the stage above
      // is what says what that moment looks like — selecting one and being shown
      // another is the confusion this whole screen exists to remove
      fxTime = fxFiring(fxKeys[i].at);
      const r = $("#fx-track").getBoundingClientRect();
      el.setPointerCapture(e.pointerId);
      // NOTHING here redraws the strip. Rebuilding it from innerHTML replaces the very
      // element the pointer was captured on, so the move and up listeners are left on a
      // node no longer in the document and the drag ends before it begins — the same
      // bug the crop-region boxes carry a comment about. A drag moves the marker by
      // hand; the list is rebuilt on RELEASE, when nothing is holding anything.
      const paint = () => {
        el.style.left = (Math.min(fxKeys[i].at, fxHold()) / fxHold()) * 100 + "%";
        $("#fx-head").style.left = (fxClock(fxTime) / fxHold()) * 100 + "%";
        drawFxStage();
      };
      const move = (ev) => {
        const t = Math.min(Math.max((ev.clientX - r.left) / r.width, 0), 1) * fxHold();
        fxKeys[i].at = +t.toFixed(2);
        fxTime = fxFiring(fxKeys[i].at);
        paint();
      };
      const up = () => {
        el.removeEventListener("pointermove", move);
        el.removeEventListener("pointerup", up);
        // sorting is left to the release too: a moment dragged past its neighbour would
        // otherwise change index mid-drag and the pointer would be holding another one
        const mine = fxKeys[fxKeySel];
        fxKeys.sort((a, b) => a.at - b.at);
        fxKeySel = fxKeys.indexOf(mine);
        drawFxTrack();
      };
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerup", up);
      $("#fx-keyrow").querySelectorAll(".fxk").forEach((x) => x.classList.toggle("on", x === el));
      drawFxKeyFields();
      paint();
    };
  });
}

// The separators, dragged. They move on the ANIMATION's clock like everything else on
// this strip, and they carry the moments' meaning with them: drag the first one past a
// key and that key stops being the way in and becomes part of what repeats.
function bindFxSeps() {
  $("#fx-seps").querySelectorAll(".sep").forEach((el) => {
    const which = el.dataset.sep;
    el.onpointerdown = (e) => {
      e.stopPropagation();
      fxPlaying = false;
      const r = $("#fx-track").getBoundingClientRect();
      el.setPointerCapture(e.pointerId);
      const band = $("#fx-seps .band");
      const move = (ev) => {
        const t = Math.min(Math.max((ev.clientX - r.left) / r.width, 0), 1) * fxHold();
        // they may not cross: a middle that ends before it begins is not a middle
        if (which === "from") fxLoop.from = Math.min(+t.toFixed(2), fxLoop.to - 0.05);
        else fxLoop.to = Math.max(+t.toFixed(2), fxLoop.from + 0.05);
        // by hand, for the reason the moments are (see above): a redraw here would take
        // the handle out from under the pointer
        const pc = (v) => (v / fxHold()) * 100;
        el.style.left = pc(which === "from" ? fxLoop.from : fxLoop.to) + "%";
        if (band) {
          band.style.left = pc(fxLoop.from) + "%";
          band.style.width = pc(fxLoop.to) - pc(fxLoop.from) + "%";
        }
        drawFxLoop();
        drawFxStage();
      };
      const up = () => {
        el.removeEventListener("pointermove", move);
        el.removeEventListener("pointerup", up);
        drawFxTrack();
      };
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerup", up);
    };
  });
}

// The same three parts as numbers, for when a number is what you have. They are
// LENGTHS rather than positions — the way in, the middle, the way out — because that
// is what somebody means when they say a pulse is a fifth of a second; the separators
// are where those lengths land, and the editor keeps the two in step rather than
// asking the operator to.
function drawFxLoop() {
  const on = fxCycles();
  $("#fx-loop").checked = on;
  $("#fx-loop-fields").hidden = !on;
  $("#fx-hold").closest("label").classList.toggle("off", on);
  if (on) {
    $("#fx-loop-in").value = +fxLoop.from.toFixed(2);
    $("#fx-loop-cycle").value = +fxCycleLen().toFixed(2);
    $("#fx-loop-out").value = +Math.max(fxHold() - fxLoop.to, 0).toFixed(2);
    $("#fx-loops").value = fxLoopN();
  }
  $("#fx-loop-note").textContent = on
    ? `${lab("js.fx.loop.total")} ${fxSpan().toFixed(2)}${lab("js.s")}`
    : lab("js.fx.loop.off");
}

$("#fx-loop").onchange = () => {
  if ($("#fx-loop").checked) {
    // a middle nobody has placed yet is the middle half of the animation, which is
    // where it is in every effect that has one
    const h = fxHold();
    // three is what a pulse is: once reads as a mistake, twice as a stutter. One
    // means the effect has never been looped, not that somebody chose one repeat.
    fxLoop = { from: +(h * 0.25).toFixed(2), to: +(h * 0.75).toFixed(2),
               loops: fxLoop.loops > 1 ? fxLoop.loops : 3 };
  } else {
    fxLoop = { from: 0, to: 0, loops: fxLoop.loops };
  }
  fxTime = 0;
  drawFxLoop();
  drawFxTrack();
  drawFxStage();
};

["#fx-loop-in", "#fx-loop-cycle", "#fx-loop-out", "#fx-loops"].forEach((id) => {
  $(id).onchange = () => {
    const inS = Math.max(+$("#fx-loop-in").value || 0, 0);
    const cyc = Math.max(+$("#fx-loop-cycle").value || 0.05, 0.05);
    const out = Math.max(+$("#fx-loop-out").value || 0, 0);
    fxLoop.from = +inS.toFixed(2);
    fxLoop.to = +(inS + cyc).toFixed(2);
    fxLoop.loops = Math.min(Math.max(+$("#fx-loops").value | 0, 1), 99);
    // the animation's clock is the three parts laid end to end, so it follows them
    $("#fx-hold").value = +(fxLoop.to + out).toFixed(2);
    fxTime = Math.min(fxTime, fxSpan());
    drawFxLoop();
    drawFxTrack();
    drawFxStage();
  };
});

// The four numbers of the moment under the playhead. Only one moment's, because a
// table of every moment is what this screen used to be: five rows of five fields, and
// nothing anywhere saying which of them was the one you were looking at.
function drawFxKeyFields() {
  const box = $("#fx-keyfields");
  const k = fxKeys[fxKeySel];
  if (!k) {
    box.innerHTML = `<p class="dim">${esc(lab(fxKeys.length
      ? "js.fx.pickmoment" : "js.fx.nomoments"))}</p>`;
    return;
  }
  const f = (name, step, min, max) =>
    `<label>${esc(lab("js.fx.k." + name))}<input type="number" data-k="${name}"
       step="${step}" ${min !== undefined ? `min="${min}"` : ""}
       ${max !== undefined ? `max="${max}"` : ""} value="${+(k[name] || 0).toFixed(3)}"></label>`;
  box.innerHTML = f("at", 0.05, 0) + f("scale", 0.05, 0.01) + f("dx", 0.05)
                + f("dy", 0.05) + f("alpha", 0.05, 0, 1) + f("rotate", 5, -360, 360);
  box.querySelectorAll("input").forEach((inp) => {
    inp.onchange = () => {
      k[inp.dataset.k] = +inp.value;
      if (inp.dataset.k === "at") {
        fxKeys.sort((a, b) => a.at - b.at);
        fxKeySel = fxKeys.indexOf(k);
        fxTime = k.at;
      }
      drawFxTrack();
      drawFxStage();
    };
  });
}

$("#fx-addkey").onclick = () => {
  // it takes the values the animation ALREADY has at this instant, so adding a moment
  // never changes what the effect does — it only gives you somewhere to change it
  const ct = fxClock(fxTime);
  const now = fxAt(fxKeys, ct);
  fxKeys.push({ at: +ct.toFixed(2), scale: +now.scale.toFixed(3),
                dx: +now.dx.toFixed(3), dy: +now.dy.toFixed(3),
                alpha: +now.alpha.toFixed(3), rotate: +(now.rotate || 0).toFixed(1) });
  fxKeys.sort((a, b) => a.at - b.at);
  fxKeySel = fxKeyAt(ct);
  drawFxTrack();
  drawFxStage();
};

$("#fx-delkey").onclick = () => {
  if (fxKeySel < 0) return;
  fxKeys.splice(fxKeySel, 1);
  fxKeySel = Math.min(fxKeySel, fxKeys.length - 1);
  drawFxTrack();
  drawFxStage();
};

// -- the stage ---------------------------------------------------------------

const fxAt = (keys, t) => {
  // the same reading the renderer uses: held before the first moment and after the
  // last, straight travel in between (see pipeline/effects._key_at)
  if (!keys.length) return { scale: 1, dx: 0, dy: 0, alpha: 1, rotate: 0 };
  if (t <= keys[0].at) return keys[0];
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (t <= b.at) {
      const p = (t - a.at) / Math.max(b.at - a.at, 1e-6);
      return {
        scale: a.scale + (b.scale - a.scale) * p,
        dx: a.dx + (b.dx - a.dx) * p,
        dy: a.dy + (b.dy - a.dy) * p,
        alpha: a.alpha + (b.alpha - a.alpha) * p,
        rotate: (a.rotate || 0) + ((b.rotate || 0) - (a.rotate || 0)) * p,
      };
    }
  }
  return keys[keys.length - 1];
};

// Where the effect sits on the stage right now, in px — the browser's copy of
// `pipeline/effects.draw_for`, and it has to be: what this shows is where the render
// puts it. `point` has no card here, so it is shown over a stand-in square, which is
// the thing its size is measured against on a real card.
function fxLayout() {
  const box = $("#fx-screen");
  const w = box.clientWidth, h = box.clientHeight;
  const anchor = $("#fx-anchor").value;
  const spec = fxCur || {};
  const ratio = spec.ratio || 1;
  const k = fxAt(fxKeys, fxClock(Math.min(fxTime, fxSpan())));
  let cx = 0.5, cy = 0.5, width = Math.max(+$("#fx-width").value || 0.3, 0.01);
  if (anchor === "full") {
    width = Math.max(1, ratio / videoAspect);
  } else if (anchor === "screen") {
    const p = ({ center: [0.5, 0.5], top: [0.5, 0.22], bottom: [0.5, 0.76],
                 left: [0.24, 0.5], right: [0.76, 0.5], top_left: [0.24, 0.22],
                 top_right: [0.76, 0.22], bottom_left: [0.24, 0.76],
                 bottom_right: [0.76, 0.76] }[$("#fx-place").value] || [0.5, 0.5]);
    cx = p[0]; cy = p[1];
  }
  const size = Math.max(width * k.scale, 0.01);
  // the offsets are in the effect's OWN widths, so in pixels they are simply `px` —
  // and in its own AXES, so they turn with it (see `effects.draw_for`): an arrow
  // pointed left has to come in from the right, or its tip lands beside the thing
  const px = size * w;
  const off = spun(k.dx, k.dy, k.rotate || 0);
  return { px, ph: px / ratio, alpha: k.alpha, rotate: k.rotate || 0,
           left: cx * w + off.x * px - px / 2,
           top: cy * h + off.y * px - px / ratio / 2, w, h, anchor };
}

function drawFxStage() {
  if (!fxCur) return;
  const piece = $("#fx-piece"), box = $("#fx-screen");
  const lay = fxLayout();
  box.classList.toggle("full", lay.anchor === "full");
  $("#fx-target").hidden = lay.anchor !== "point";
  if (!piece.querySelector("img,.sound")) {
    piece.insertAdjacentHTML("afterbegin", fxCur.url
      ? `<img src="${tokd(fxCur.url)}" alt="" draggable="false">`
      : `<span class="sound">🔊</span>`);
  }
  // the handles hang OUTSIDE the box and the box is turned, so they turn with it —
  // which is what you want: the corner stays the corner of the thing it resizes.
  const spin = piece.querySelector(".spin");
  if (spin) spin.hidden = !fxCur.url;
  piece.style.width = lay.px + "px";
  piece.style.height = lay.ph + "px";
  piece.style.left = lay.left + "px";
  piece.style.top = lay.top + "px";
  piece.style.opacity = lay.alpha;
  piece.style.transform = lay.rotate ? `rotate(${lay.rotate}deg)` : "";
  // a fully transparent moment still has to be grabbable, or the one thing you want
  // to fix about it — where it comes in from — cannot be dragged
  piece.classList.toggle("ghosted", lay.alpha < 0.08);
}

// Dragging the thing itself. It edits the SELECTED moment, because an animation is a
// run of moments and "move it" with none of them chosen is not a question anyone can
// answer — so with nothing selected it takes the moment under the playhead, and with
// no moments at all it makes one there and moves that.
function bindFxStage() {
  const piece = $("#fx-piece");
  piece.onpointerdown = (e) => {
    if (!fxCur) return;
    e.preventDefault();
    fxPlaying = false;
    if (fxKeySel < 0 || Math.abs((fxKeys[fxKeySel] || {}).at - fxTime) > 0.25) {
      const on = fxKeyAt(fxTime);
      if (on >= 0) fxKeySel = on;
      else { $("#fx-addkey").onclick(); }
    }
    const k = fxKeys[fxKeySel];
    if (!k) return;
    const grip = e.target.classList.contains("grip");
    const spin = e.target.classList.contains("spin");
    const lay = fxLayout();
    // the centre the turn is measured about, in page coordinates
    const r0 = piece.getBoundingClientRect();
    const mid = { x: r0.left + r0.width / 2, y: r0.top + r0.height / 2 };
    const at0 = Math.atan2(e.clientY - mid.y, e.clientX - mid.x);
    // A turn aims the WHOLE animation unless Alt says otherwise, and that is the
    // difference between aiming a thing and animating it. Turning one moment and
    // leaving the rest at zero does not point the arrow — it makes it spin up to the
    // angle and back down again, which is what "the animation breaks when I rotate it"
    // was. Alt is how a swing is authored: one moment at a time, on purpose.
    const one = e.altKey;
    const from = { x: e.clientX, y: e.clientY, dx: k.dx, dy: k.dy, scale: k.scale,
                   rotate: k.rotate || 0,
                   turns: fxKeys.map((m) => m.rotate || 0) };
    piece.setPointerCapture(e.pointerId);
    const move = (ev) => {
      const mx = (ev.clientX - from.x) / Math.max(lay.px, 1);
      const my = (ev.clientY - from.y) / Math.max(lay.px, 1);
      if (spin) {
        // how far the pointer has swung ROUND the centre, which is the only reading of
        // a turn that survives the thing being moved or resized mid-drag
        const now = Math.atan2(ev.clientY - mid.y, ev.clientX - mid.x);
        let deg = from.rotate + (now - at0) * 180 / Math.PI;
        // a modifier snaps to fifteens, because most of what anybody aims at is a
        // right angle or a diagonal and nudging one degree at a time is not aiming
        if (ev.shiftKey) deg = Math.round(deg / 15) * 15;
        const wrap = (v) => +((v % 360 + 540) % 360 - 180).toFixed(1);
        if (one) k.rotate = wrap(deg);
        else {
          // every moment turns by the same amount, so whatever swing the animation
          // already had is kept and the whole of it is simply aimed elsewhere
          const by = deg - from.rotate;
          fxKeys.forEach((m, i) => (m.rotate = wrap((from.turns[i] || 0) + by)));
        }
      } else if (grip) {
        // the corner is the moment's own size, read as the DISTANCE from the centre —
        // which is the one reading that still means "bigger" when the thing has been
        // turned. Along x it would mean bigger at 0° and sideways at 90°.
        const was = Math.hypot(from.x - mid.x, from.y - mid.y);
        const now = Math.hypot(ev.clientX - mid.x, ev.clientY - mid.y);
        k.scale = clamp(from.scale * (now / Math.max(was, 1)), 0.02, 8);
      } else {
        // the pointer moves in the FRAME's axes and the offset lives in the effect's,
        // so the delta is turned back the other way before it is stored — otherwise
        // dragging a turned effect right moves it sideways by exactly its angle
        const back = spun(mx, my, -(k.rotate || 0));
        k.dx = +(from.dx + back.x).toFixed(3);
        k.dy = +(from.dy + back.y).toFixed(3);
      }
      drawFxStage();
      drawFxKeyFields();
    };
    const up = () => {
      piece.removeEventListener("pointermove", move);
      piece.removeEventListener("pointerup", up);
      drawFxTrack();
    };
    piece.addEventListener("pointermove", move);
    piece.addEventListener("pointerup", up);
    drawFxTrack();
  };
}

function playFx() {
  fxPlaying = !fxPlaying;
  $("#fx-play").textContent = fxPlaying ? "❚❚" : "▶";
  if (fxTimer) cancelAnimationFrame(fxTimer);
  if (!fxPlaying) return;
  let last = performance.now();
  const step = (now) => {
    if (!fxPlaying) return;
    const span = fxSpan();
    fxTime += (now - last) / 1000;
    last = now;
    if (fxTime > span + 0.35) fxTime = 0;   // a beat of nothing between runs
    drawFxTrack();
    drawFxStage();
    fxTimer = requestAnimationFrame(step);
  };
  fxTimer = requestAnimationFrame(step);
}

function stopFxPreview() {
  fxPlaying = false;
  if (fxTimer) cancelAnimationFrame(fxTimer);
  fxTimer = null;
}

// -- what the editor writes back ---------------------------------------------

$("#fx-anchor").onchange = () => { fxNotes(); drawFxStage(); };
["#fx-place", "#fx-width"].forEach((id) => {
  $(id).onchange = () => drawFxStage();
});
// the length is the strip's own scale, so changing it redraws both
$("#fx-hold").onchange = () => {
  fxLoop.to = Math.min(fxLoop.to, fxHold());
  fxLoop.from = Math.min(fxLoop.from, Math.max(fxLoop.to - 0.05, 0));
  fxTime = Math.min(fxTime, fxSpan());
  drawFxLoop();
  drawFxTrack();
  drawFxStage();
};
$("#fx-play").onclick = () => playFx();

$("#fx-save").onclick = async () => {
  if (!fxCur) return;
  const body = {
    description: $("#fx-descr").value, note: $("#fx-note").value,
    anchor: $("#fx-anchor").value, place: $("#fx-place").value,
    fill: $("#fx-fill").value, retired: $("#fx-retired").checked,
    width: +$("#fx-width").value, hold: +$("#fx-hold").value,
    volume: +$("#fx-volume").value, keys: fxKeys,
    loop_from: fxLoop.from, loop_to: fxLoop.to, loops: fxLoopN(),
  };
  await api(`/api/effects/${encodeURIComponent(fxCur.name)}`, {
    method: "PUT", headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  $("#fx-saved").textContent = lab("js.saved");
  setTimeout(() => ($("#fx-saved").textContent = ""), 1500);
  await loadEffects();
};

// Asked twice, exactly as a card is, and for the same reason: the second press takes
// the file with it, and a base people tidy is a base where that has to be deliberate.
$("#fx-del").onclick = async (e) => {
  if (!fxCur) return;
  const key = `fx:${fxCur.name}`;
  if (!armed.has(key))
    return armForget(e.currentTarget, key, "web.fx.del", "js.fx.del-sure");
  clearTimeout(armed.get(key));
  armed.delete(key);
  const name = fxCur.name;
  try {
    await api(`/api/effects/${encodeURIComponent(name)}?purge=true`, { method: "DELETE" });
  } catch (err) { return say(err.message, true); }
  say(`${name} — ${lab("js.fx.deleted")}`);
  closeEffect();
  loadEffects();
};

function bindFxDrop(dropId, inputId, send) {
  const drop = $(dropId);
  drop.onclick = () => $(inputId).click();
  $(inputId).onchange = (e) => e.target.files[0] && send(e.target.files[0]);
  ["dragover", "dragenter"].forEach((k) => drop.addEventListener(k, (e) => {
    e.preventDefault(); drop.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((k) =>
    drop.addEventListener(k, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    if (e.dataTransfer.files[0]) send(e.dataTransfer.files[0]);
  });
}

bindFxDrop("#fx-drop", "#fx-upload", async (file) => {
  const body = new FormData();
  body.append("file", file);
  body.append("name", file.name.replace(/\.[^.]+$/, ""));
  const e = await api("/api/effects", { method: "POST", body });
  await loadEffects();
  openEffect(e.name);
  say(lab("js.fx.added"));
});

bindFxDrop("#fx-drop2", "#fx-upload2", async (file) => {
  if (!fxCur) return;
  const body = new FormData();
  body.append("file", file);
  await api(`/api/effects/${encodeURIComponent(fxCur.name)}/material`,
            { method: "POST", body });
  await loadEffects();
});

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
  // The name is a field, first, like everything else about the entry — Save commits a
  // change to it, and the server moves the file and repoints whatever named it (see
  // `ConfigStore.rename_config`). It used to be a heading you could only read, which
  // made a typo in a name permanent short of deleting the entry and typing it again.
  return `<div class="panel cfg-item${active ? " active" : ""}" data-name="${esc(name)}">
    <div class="row"><b>${esc(name)}</b>
      ${active ? `<span class="pill-on">${lab("js.in-use")}</span>` : ""}
      ${kind === "llm" && !active ? `<button data-use class="ghost">${lab("js.use-it2")}</button>` : ""}
      <span class="grow"></span>
      <button data-save class="primary">${lab("js.save")}</button>
      <button data-del class="ghost">${lab("js.delete")}</button></div>
    <div class="grid">
      <label>${lab("js.name")}<input data-rename value="${esc(name)}"></label>
      ${cfgData.schema.map((f) => cfgField(f, item[f.name])).join("")}</div>
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
      body.rename = el.querySelector("[data-rename]").value;
      try {
        const r = await api(`/api/configs/${kind}/${encodeURIComponent(name)}`,
          { method: "PUT", headers: { "content-type": "application/json" },
            body: JSON.stringify(body) });
        sayRename(name, body.rename, r, lab("js.saved"));
        if (body.rename !== name) loadConfigs(kind, $("#cl-title").textContent);
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
  savePlace();
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
    <div class="frame-card ${c.retired ? "retired" : ""}" data-name="${esc(c.name)}">
      <div class="thumb ${c.usable ? "" : "none"} ${c.fit === "pad" ? "pad" : ""}"
           ${c.usable ? `style="background-image:url('${tokd(c.poster || c.url)}')"` : ""}>
        ${c.usable ? "" : lab("js.no-picture-yet")}
        ${c.targets.length ? `<span class="pill">${c.targets.length}</span>` : ""}
        ${c.kind === "video" ? `<span class="pill">${lab("js.clip")}</span>` : ""}
      </div>
      <div class="meta"><b>${esc(c.name)}</b><span>${esc((c.description || "").slice(0, 60))}</span></div>
    </div>`).join("") || `<p class="empty">${lab("js.this-world-has-no-cards-yet")}</p>`;
  document.querySelectorAll("#cards .frame-card").forEach((el) => {
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
//
// The editor is an overlay over the whole page rather than a corner of the Worlds tab,
// which is what lets a run open it: a picture handed to a parked run IS a card in the
// world, and the regions on it are the difference between a card that can be shown
// once and one that is worth four shots. Everything below hangs off `world` and
// `cards`, so both are set here rather than assumed — the operator may not have opened
// the Worlds tab at all this session, and the world that matters is the RUN's, not
// whichever one the select is showing.
async function openMarkup(w, name) {
  if (!w || !name) return;
  world = w;
  const sel = $("#world");
  if (sel && [...sel.options].some((o) => o.value === w)) sel.value = w;
  await loadCards();
  if (!cards.some((c) => c.name === name)) { say(lab("js.no-such-card"), true); return; }
  openCard(name);
}

function openCard(name) {
  card = cards.find((c) => c.name === name);
  targets = card.targets.map((t) => ({ ...t }));
  sel = -1;
  $("#ed-name").textContent = card.name;
  $("#ed-descr").value = card.description || "";
  $("#ed-prompt").value = card.prompt || "";
  $("#ed-note").value = card.note || "";
  $("#ed-retired").checked = !!card.retired;
  $("#ed-del").textContent = lab("web.frames.del");
  $("#ed-del").classList.remove("danger");
  fillFit();
  const img = $("#pic"), vid = $("#vid");
  // A card with no file has nothing to mark up: a crop target is a pair of
  // coordinates ON a picture, so drawing regions over an empty stage would be
  // measuring a thing that is not there. The card still opens — its texts are worth
  // editing while the picture is being made — but the geometry stays shut.
  $("#stage").classList.toggle("nofile", !card.usable);
  $("#nofile").hidden = card.usable;
  cardFx = (card.effects || []).map((h) => ({ ...h }));
  // the base may not have been looked at this session, and the list of effects is what
  // the rows are built out of
  (fxAll.length ? Promise.resolve(fxAll) : api("/api/effects").then((e) => (fxAll = e)))
    .then(listCardFx).catch(() => listCardFx());
  if (!card.usable) {
    img.hidden = vid.hidden = true;
    $("#boxes").innerHTML = "";
    $("#safe").hidden = true;
    listTargets();
    $("#editor").hidden = false;
    return;
  }
  if (card.kind === "video") {
    vid.src = tokd(card.url); vid.hidden = false; img.hidden = true; vid.play().catch(() => {});
  } else {
    img.src = tokd(card.url); img.hidden = false; vid.hidden = true;
  }
  $("#editor").hidden = false;
  drawTargets();
}
$("#close").onclick = () => { $("#editor").hidden = true; $("#vid").pause(); };

// The two answers to "this picture is not the shape of the video". Filled from here
// rather than at boot because the editor is opened long after the options are, and
// there are exactly two of them either way.
const FITS = ["crop", "pad"];

function fillFit() {
  const sel = $("#ed-fit");
  sel.innerHTML = FITS.map(
    (f) => `<option value="${f}">${esc(lab("w.fit." + f, f))}</option>`
  ).join("");
  sel.value = (card && card.fit === "pad") ? "pad" : "crop";
  fitNote();
}

function fitNote() {
  // A square-ish picture loses nothing either way, and saying so is worth more than
  // leaving the operator to wonder why the choice did nothing.
  const f = card && card.usable ? frame() : null;
  $("#fit-note").textContent =
    !f ? ""
    : !f.loose ? lab("js.this-one-is-already-the-right-shape")
    : lab(card.fit === "pad" ? "js.fit-note-pad" : "js.fit-note-crop");
}

$("#ed-fit").onchange = () => {
  if (!card) return;
  // through `refit`, so the regions already drawn stay on what they are regions OF
  refit(() => { card.fit = $("#ed-fit").value === "pad" ? "pad" : "crop"; });
  fitNote();
};

// Sliding the frame along the overspill. Only the axis that actually overspills
// moves, which falls out of the arithmetic rather than needing a branch: dividing by
// an overspill of zero would be meaningless, so a zero one simply keeps its 0.5.
$("#safe").addEventListener("pointerdown", (e) => {
  if (!card || !e.target.classList.contains("fitgrip")) return;
  const f0 = frame();
  if (f0.pad || !f0.loose) return;
  e.preventDefault();
  e.stopPropagation();  // the stage would otherwise start drawing a region
  const saved = targetsInPicture(f0);
  const from = { x: e.clientX, y: e.clientY,
                 ax: typeof card.fit_x === "number" ? card.fit_x : 0.5,
                 ay: typeof card.fit_y === "number" ? card.fit_y : 0.5 };
  const grip = e.target;
  grip.setPointerCapture(e.pointerId);
  const move = (ev) => {
    if (Math.abs(f0.over.x) > 1) {
      card.fit_x = clamp(from.ax + (ev.clientX - from.x) / f0.over.x, 0, 1);
    }
    if (Math.abs(f0.over.y) > 1) {
      card.fit_y = clamp(from.ay + (ev.clientY - from.y) / f0.over.y, 0, 1);
    }
    targetsFromPicture(frame(), saved);
    drawTargets();
  };
  const up = () => {
    grip.removeEventListener("pointermove", move);
    grip.removeEventListener("pointerup", up);
    grip.removeEventListener("pointercancel", up);
  };
  grip.addEventListener("pointermove", move);
  grip.addEventListener("pointerup", up);
  grip.addEventListener("pointercancel", up);
});

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
  // What the finished frame is, out of this picture — the browser's copy of
  // `media/ffmpeg.fit_chain`, and it has to agree with it or the editor is drawing
  // regions on a frame the pipeline will not render.
  //
  // CROP shrinks to the video's aspect: the frame is inside the picture and the rest
  // is lost. PAD grows to it: the frame is BIGGER than the picture, and the parts of
  // it the picture does not reach are the black bars. Either way the frame is exactly
  // the video's shape, which is what keeps a region three numbers instead of four
  // (see Rect) and what makes every fraction below mean the same thing it means to
  // ffmpeg.
  let w = shown.w, h = shown.h;
  const pad = card && card.fit === "pad";
  if (pad) {
    if (w / h > videoAspect) h = w / videoAspect; else w = h * videoAspect;
  } else if (w / h > videoAspect) {
    w = h * videoAspect;
  } else {
    h = w / videoAspect;
  }
  // Where the frame sits in the picture. Under pad it is centred and not placeable —
  // a bar is what is left over, and an off-centre bar is a crop with extra steps — so
  // only the crop reads the card's placement. The fraction is of the OVERSPILL, so it
  // is meaningful whatever the two shapes turn out to be, exactly as in ffmpeg.
  const ax = pad ? 0.5 : (card && typeof card.fit_x === "number" ? card.fit_x : 0.5);
  const ay = pad ? 0.5 : (card && typeof card.fit_y === "number" ? card.fit_y : 0.5);
  const over = { x: shown.w - w, y: shown.h - h };
  return { x: shown.x + over.x * ax, y: shown.y + over.y * ay, w, h,
           shown, pad, over,
           // is there anything to place? a picture already the video's shape
           // overspills by nothing, and its frame has nowhere to go
           loose: Math.max(Math.abs(over.x), Math.abs(over.y)) > 1,
           cropped: !pad && Math.abs(shown.w * shown.h - w * h) > 1 };
}

// A region is stored as a fraction OF THE FRAME, and the frame moves when the fit
// changes — so the same three numbers point somewhere else afterwards. These two
// carry a region through such a change by the only thing that stayed still, the
// picture: read where it sits on the picture before, put it back there after.
//
// Remapping rather than warning, because the operator drew "her face", not "the
// middle third". A warning would be honest about the numbers and useless about the
// intent; this keeps the intent and lets the numbers follow.
function targetsInPicture(f) {
  const s = f.shown;
  return targets.map((t) => ({
    ...t,
    px: (f.x + t.cx * f.w - s.x) / s.w,
    py: (f.y + t.cy * f.h - s.y) / s.h,
    pw: (t.scale * f.w) / s.w,
  }));
}

function targetsFromPicture(f, saved) {
  const s = f.shown;
  targets = saved.map((t) => {
    const scale = clamp((t.pw * s.w) / f.w, 0.1, 1);
    const cx = (s.x + t.px * s.w - f.x) / f.w;
    const cy = (s.y + t.py * s.h - f.y) / f.h;
    const { px, py, pw, ...rest } = t;
    return { ...rest, scale,
             cx: clamp(cx, scale / 2, 1 - scale / 2),
             cy: clamp(cy, scale / 2, 1 - scale / 2) };
  });
}

/** Change the fit, carrying the regions across so they stay on what they were of. */
function refit(change) {
  const saved = targetsInPicture(frame());
  change();
  targetsFromPicture(frame(), saved);
  drawTargets();
}

function drawSafe() {
  const f = frame(), el = $("#safe");
  // Under pad the frame is bigger than the picture and nothing is lost, so there is
  // no dark part — what the outline shows then is where the black bars will be, which
  // is worth seeing for a different reason: it is how much of the screen goes to them.
  el.hidden = !(f.cropped || (f.pad && f.loose));
  if (el.hidden) return;
  Object.assign(el.style, { left: f.x + "px", top: f.y + "px",
                            width: f.w + "px", height: f.h + "px" });
  el.classList.toggle("padding", !!f.pad);
  // draggable only when cropping and only when there is overspill to slide along
  el.classList.toggle("movable", !f.pad && f.loose);
  el.dataset.note = f.pad ? lab("js.the-bars-are-here")
                          : lab("js.the-dark-part-is-cropped-the-frame-is-fi");
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
  drawCardFxGhost();
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
  // the hooks name regions by LABEL, so a region renamed here has to be re-offered
  // there — otherwise the select quietly shows the old name as if it still existed
  if ($("#card-fx")) listCardFx();
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

// -- what this card can do ---------------------------------------------------
//
// The effect itself is global (see the effects base); what a card holds is the
// PAIRING — which effect, on which of its regions, and one line saying what firing it
// means here. That line is what the model picks on, so it is the field the list puts
// first and the only one that is ever really written.

let cardFx = [];      // the hooks of the card being edited
let cardFxSel = -1;   // which of them is being placed on the picture

// The three placements a hook can have, as the buttons that set them. They are the
// whole of `CardEffect.points` (see the model): a point, a box, or nothing at all.
const FX_PLACINGS = {
  // one point: it sits there, at the effect's own size
  point: (h, f) => [{ cx: 0.5, cy: 0.5 }].map((p) => centreOf(h, p)),
  // two: the box between them is where AND how big, which is what a drag leaves
  box: (h) => {
    const c = centreOf(h, { cx: 0.5, cy: 0.5 });
    const r = 0.14;
    return [{ cx: c.cx - r, cy: c.cy - r * 1.0 }, { cx: c.cx + r, cy: c.cy + r * 1.0 }];
  },
  // none: it is not on the picture at all and falls back to the frame
  none: () => [],
};

// Where a hook currently is, so switching between the three keeps the place somebody
// already chose instead of jumping back to the middle.
function centreOf(h, fallback) {
  const pts = (h && h.points) || [];
  if (!pts.length) return fallback;
  const xs = pts.map((p) => p.cx), ys = pts.map((p) => p.cy);
  return { cx: (Math.min(...xs) + Math.max(...xs)) / 2,
           cy: (Math.min(...ys) + Math.max(...ys)) / 2 };
}

const fxSpecOf = (name) => (fxAll || []).find((e) => e.name === name) || null;

/** An offset given in the effect's own axes, said in the frame's. Degrees clockwise,
 *  y down — the same convention CSS `rotate()` and ffmpeg's `rotate` use, so what the
 *  preview draws and what the render draws are one arithmetic. */
function spun(dx, dy, deg) {
  if (!deg) return { x: dx, y: dy };
  const r = deg * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
  return { x: dx * c - dy * s, y: dx * s + dy * c };
}

/** A hook's placement in PICTURE fractions: {cx, cy, w, h} of the fitted frame, or
 *  null where it is not on the picture. The browser's copy of `effects.spot`, and it
 *  has to stay the browser's copy of it: what is drawn here is where the render puts
 *  it, and two answers to that would make the editor a liar. */
function fxSpot(h) {
  const pts = (h.points || []);
  const spec = fxSpecOf(h.effect);
  if (!pts.length || !spec) return null;
  const ratio = spec.ratio || 1;   // the file's own width / height
  if (pts.length === 1) {
    const w = (h.width > 0 ? h.width : spec.width);
    return { cx: pts[0].cx, cy: pts[0].cy, w, h: w / ratio / videoAspect };
  }
  const xs = pts.map((p) => p.cx), ys = pts.map((p) => p.cy);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y0 = Math.min(...ys), y1 = Math.max(...ys);
  // the box IS the size (see `effects.spot`): the wider of its two sides in frame-width
  // units decides, and the drawn height follows from the file's own shape
  const w = Math.max(x1 - x0, (y1 - y0) * videoAspect, 0.01);
  return { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, w, h: w / ratio / videoAspect };
}

// The ghost on the picture: the effect itself, where it will actually be drawn, with a
// grip on its corner. It is the placement — drag it and the points move — so it is an
// element over the stage rather than something painted into the picture.
function drawCardFxGhost() {
  const box = $("#fxplace");
  if (!box) return;
  const h = cardFx[cardFxSel];
  if (!card || !card.usable || !h) { box.innerHTML = ""; return; }
  const spot = fxSpot(h);
  if (!spot) {
    box.innerHTML = `<p class="fxhint">${esc(lab("js.fx.drop-hint"))}</p>`;
    return;
  }
  const spec = fxSpecOf(h.effect);
  const f = frame();
  const inner = spec && spec.url
    ? `<img src="${tokd(spec.url)}" alt="" draggable="false">`
    : `<span class="sound">🔊</span>`;
  // the anchor itself, which is what the drag actually moves — an effect whose
  // animation carries it far from its point would otherwise have nothing on screen
  // saying what it is pinned TO
  const dots = (h.points || []).map((pt) =>
    `<i class="fxdot" style="left:${f.x + pt.cx * f.w}px;top:${f.y + pt.cy * f.h}px"></i>`).join("");
  box.innerHTML = dots
    + `<div class="fxghost">${inner}`
    + `<span class="grip" title="${esc(lab("js.fx.grip"))}"></span>`
    + (spec && spec.url ? `<span class="spin" title="${esc(lab("js.fx.spin"))}"></span>` : "")
    + `</div>`;
  const el = box.querySelector(".fxghost");
  placeGhost(el, h);
  bindGhost(el, h);
}

/** Put the ghost where the render will put the effect — the one function both the
 *  drawing and the dragging go through, so a drag cannot drift away from what a
 *  redraw would have shown.
 *
 *  SETTLED, not resting: it is drawn the way the effect will look once its animation
 *  has arrived, offsets and rotation and all. Otherwise an arrow that hangs above what
 *  it points at is drawn sitting on it, and every arrow in the base gets placed one
 *  arrow-length too high by an operator doing exactly what the picture told them. */
function placeGhost(el, h) {
  const spot = fxSpot(h);
  if (!el || !spot) return;
  const f = frame();
  const k = settledKey(fxSpecOf(h.effect));
  const w = spot.w * k.scale * f.w, hh = spot.h * k.scale * f.h;
  // the offset turns with the thing, aim included — the same reading the render uses
  const off = spun(k.dx, k.dy, (k.rotate || 0) + (h.turn || 0));
  el.style.left = f.x + spot.cx * f.w + off.x * w - w / 2 + "px";
  el.style.top = f.y + spot.cy * f.h + off.y * w - hh / 2 + "px";
  el.style.width = w + "px";
  el.style.height = hh + "px";
  el.style.opacity = Math.max(k.alpha, 0.25);
  // the animation's own turning plus this card's aim, which is what the render adds
  // up too (see `effects.aim`)
  const turn = (k.rotate || 0) + (h.turn || 0);
  el.style.transform = turn ? `rotate(${turn}deg)` : "";
}

/** The moment an effect has ARRIVED at: the most solid one, and the latest of those
 *  when several tie. What an animation is for is getting somewhere, and that is the
 *  state worth drawing on a card — the ones before it are the arrival. */
function settledKey(spec) {
  const keys = (spec && spec.keys) || [];
  if (!keys.length) return { scale: 1, dx: 0, dy: 0, alpha: 1, rotate: 0 };
  let best = keys[0];
  for (const k of keys) if (k.alpha >= best.alpha) best = k;
  return best;
}

function bindGhost(el, h) {
  el.onpointerdown = (e) => {
    e.stopPropagation();          // the stage would start drawing a crop region
    e.preventDefault();
    const grip = e.target.classList.contains("grip");
    const spin = e.target.classList.contains("spin");
    const f = frame();
    const st = $("#stage").getBoundingClientRect();
    const r0 = el.getBoundingClientRect();
    const mid = { x: r0.left + r0.width / 2, y: r0.top + r0.height / 2 };
    const at0 = Math.atan2(e.clientY - mid.y, e.clientX - mid.x);
    const from = { x: e.clientX - st.left, y: e.clientY - st.top,
                   pts: (h.points || []).map((p) => ({ ...p })), width: h.width,
                   turn: h.turn || 0 };
    const spec = fxSpecOf(h.effect);
    el.setPointerCapture(e.pointerId);
    const move = (ev) => {
      const dx = (ev.clientX - st.left - from.x) / f.w;
      const dy = (ev.clientY - st.top - from.y) / f.h;
      if (spin) {
        // how far the pointer has swung ROUND the centre — the same reading the
        // effect's own editor uses, so the two gestures are one gesture
        const now = Math.atan2(ev.clientY - mid.y, ev.clientX - mid.x);
        let deg = from.turn + (now - at0) * 180 / Math.PI;
        if (ev.shiftKey) deg = Math.round(deg / 15) * 15;
        h.turn = +((deg % 360 + 540) % 360 - 180).toFixed(1);
      } else if (!grip) {
        // the body moves the whole placement, points and all
        h.points = from.pts.map((p) => ({ cx: clamp(p.cx + dx, 0, 1),
                                          cy: clamp(p.cy + dy, 0, 1) }));
      } else if (from.pts.length >= 2) {
        // the corner moves the second point: the box is the size
        const last = from.pts.length - 1;
        h.points = from.pts.map((p, i) => i === last
          ? { cx: clamp(p.cx + dx, 0, 1), cy: clamp(p.cy + dy, 0, 1) } : { ...p });
      } else {
        // a single point has no box, so the corner is the per-card SIZE — which is
        // what `CardEffect.width` is for, and the one number this override ever means
        const base = from.width > 0 ? from.width : (spec ? spec.width : 0.3);
        h.width = clamp(base + dx * 2, 0.02, 3);
      }
      // the ghost is MOVED, not redrawn: a redraw replaces the element the pointer is
      // captured on and the drag dies after one event, which is what "it barely moves"
      // was. The dots and the list catch up on release.
      placeGhost(el, h);
    };
    const up = () => {
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      drawCardFxGhost();
      listCardFx();
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
  };
}

const PLACINGS = ["point", "box", "none"];

function placingOf(h) {
  const n = ((h && h.points) || []).length;
  return n === 0 ? "none" : n === 1 ? "point" : "box";
}

function listCardFx() {
  const base = (fxAll || []).filter((e) => e.usable && !e.retired);
  if (!base.length) {
    $("#card-fx").innerHTML = `<li class="dimrow">${lab("js.fx.nobase")}</li>`;
    $("#card-fx-add").disabled = true;
    drawCardFxGhost();
    return;
  }
  $("#card-fx-add").disabled = false;
  $("#card-fx").innerHTML = cardFx.map((h, i) => {
    const spec = fxSpecOf(h.effect);
    const kind = spec ? spec.anchor : "point";
    const now = placingOf(h);
    return `
    <li data-i="${i}" class="${i === cardFxSel ? "sel" : ""}">
      <div class="row">
        <select data-f="effect">${base.map((e) =>
          `<option value="${esc(e.name)}"${e.name === h.effect ? " selected" : ""}>${esc(e.name)}</option>`).join("")}</select>
        <button data-del="${i}" class="ghost">×</button>
      </div>
      <div class="row">
        <input data-f="label" value="${esc(h.label || "")}" placeholder="${lab("js.fx.means")}">
      </div>
      ${kind === "point" ? `<div class="row chips small">
        ${PLACINGS.map((k) => `<button data-place="${k}" class="${k === now ? "on" : ""}"
            >${esc(lab("js.fx.placing." + k))}</button>`).join("")}
        <span class="grow"></span>
        <label class="inline turn">${esc(lab("js.fx.turn"))}
          <input data-f="turn" type="number" step="15" min="-180" max="180"
                 value="${+(h.turn || 0).toFixed(1)}"></label>
      </div>
      <p class="dim">${esc(lab("js.fx.placing.hint." + now))} ${
        esc(lab("js.fx.turn.hint"))}</p>` : `<p class="dim">${esc(lab("fx.anchor." + kind, kind))} — ${
        esc(lab("js.fx.placing.hint.frame"))}</p>`}
    </li>`;
  }).join("") || `<li class="dimrow">${lab("js.fx.nohooks")}</li>`;
  $("#card-fx").querySelectorAll("li[data-i]").forEach((li) => {
    const i = +li.dataset.i;
    li.onclick = (e) => {
      if (e.target.closest("button") || e.target.closest("select")
          || e.target.tagName === "INPUT") return;
      cardFxSel = cardFxSel === i ? -1 : i;
      listCardFx();
    };
    li.querySelectorAll("[data-f]").forEach((el) => {
      el.onchange = () => {
        cardFx[i][el.dataset.f] = el.dataset.f === "turn" ? +el.value : el.value;
        cardFxSel = i;
        listCardFx();
      };
      if (el.tagName === "INPUT" && el.dataset.f === "label")
        el.oninput = () => (cardFx[i].label = el.value);
    });
    li.querySelectorAll("[data-place]").forEach((b) => {
      b.onclick = (e) => {
        e.stopPropagation();
        const h = cardFx[i];
        h.points = FX_PLACINGS[b.dataset.place](h);
        cardFxSel = i;
        listCardFx();
      };
    });
    li.querySelector("[data-del]").onclick = (e) => {
      e.stopPropagation();
      cardFx.splice(i, 1);
      if (cardFxSel >= cardFx.length) cardFxSel = -1;
      listCardFx();
    };
  });
  drawCardFxGhost();
}

$("#card-fx-add").onclick = () => {
  const base = (fxAll || []).filter((e) => e.usable && !e.retired);
  if (!base.length) return;
  const spec = base[0];
  // it arrives PLACED, in the middle of the picture, because the commonest next thing
  // to do is drag it onto something — and a hook with nothing on the stage reads as a
  // control that did nothing
  cardFx.push({ effect: spec.name, label: "", width: 0, turn: 0,
                points: spec.anchor === "point" ? [{ cx: 0.5, cy: 0.5 }] : [] });
  cardFxSel = cardFx.length - 1;
  listCardFx();
};

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
// the note depends on the picture's real shape, which is not known until it loads
$("#pic").onload = () => { drawTargets(); fitNote(); };

$("#save").onclick = async () => {
  const body = {
    description: $("#ed-descr").value, prompt: $("#ed-prompt").value,
    note: $("#ed-note").value, retired: $("#ed-retired").checked, targets,
    effects: cardFx.filter((h) => h.effect),
    // the fit travels with the regions, and it has to: they are fractions OF the
    // frame the fit decides, so one saved without the other is a region on a frame
    // that is no longer there
    fit: card.fit || "crop",
    fit_x: typeof card.fit_x === "number" ? card.fit_x : 0.5,
    fit_y: typeof card.fit_y === "number" ? card.fit_y : 0.5,
  };
  const updated = await api(`/api/worlds/${encodeURIComponent(world)}/cards/${encodeURIComponent(card.name)}`,
    { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  Object.assign(card, updated);
  const s = $("#saved"); s.textContent = lab("js.saved2"); s.classList.add("show");
  setTimeout(() => s.classList.remove("show"), 1400);
  loadCards();
};

// Deleting a card takes its picture with it, so the button asks: the first press turns
// it into the question, a second within five seconds answers it, and walking away
// answers no — the same guard the runs list puts on a folder, for the same reason.
//
// Retiring is the softer half and lives in the form as a tick, because it is a property
// of the card rather than an act: a retired card keeps its picture and its place, and
// simply stops being spent. It is what a wrong delivery taken back leaves behind, so
// there has to be a way to undo it — which a delete button alone could not offer.
$("#ed-del").onclick = async (e) => {
  const key = `card:${world}/${card.name}`;
  if (!armed.has(key))
    return armForget(e.currentTarget, key, "web.frames.del", "web.frames.del.sure");
  clearTimeout(armed.get(key));
  armed.delete(key);
  try {
    await api(`/api/worlds/${encodeURIComponent(world)}/cards/${encodeURIComponent(card.name)}?purge=true`,
              { method: "DELETE" });
  } catch (err) { return say(err.message, true); }
  say(`${card.name} — ${lab("js.card-deleted")}`);
  $("#editor").hidden = true;
  $("#vid").pause();
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

// The gap answer travels as a NAME and is drawn as a slider, so the two spellings have
// to meet somewhere. Here, off the server's own ordered list — a fourth rung added
// there reaches the slider without touching this file. The fallback matters on the
// first paint, before /api/options has landed.
const INVENT_LEVELS = ["no", "gaps", "free"];
const inventLevel = (i) => {
  const list = (opts && opts.invent_levels) || INVENT_LEVELS;
  return list[Math.max(0, Math.min(list.length - 1, +i || 0))];
};
const inventIndex = (name) => {
  const list = (opts && opts.invent_levels) || INVENT_LEVELS;
  const i = list.indexOf(name);
  return i < 0 ? 0 : i;
};

// An info line is assembled in the pipeline out of its own vocabulary — a move kind,
// a fit grade, a stage name. Translating it word by word here keeps those words as
// identifiers everywhere they matter, and readable only where they are read.
const humanise = (text) =>
  String(text).replace(/[a-z_]+/g, (w) =>
    lab(MOVE_KINDS.includes(w) ? "mv." + w : "w." + w, w));

// The Picture card, filled from one visuals profile.
//
// Both generator dropdowns are prefilled, not just the one on screen: which of them
// the source reveals can change with the next click, and the one that appears has to
// be right already. A profile naming no generator — every stock and local source, and
// every user-assisted one — leaves each list at its own default, which is exactly what
// `_visuals_values` does in the terminal.
function fillPicture(name) {
  pictureFrom((opts.visuals_full || {})[name]);
}

// …and the same card filled from a visuals config of any provenance: the profile a
// name points at, or the OVERRIDE a loop is already carrying. Both callers need it,
// and the second one is why this is a function of a config rather than of a name —
// `fillForm` opens a loop whose picture may be an ad-hoc copy that no name reaches,
// and a card filled from the name instead would quietly rebuild the profile and hand
// the loop back its own override erased (see params.manual_visuals).
function pictureFrom(p) {
  const form = $("#infoform");
  if (!p || !form) return;
  const bg = p.background || {}, fg = p.foreground || {};
  const set = (n, v) => {
    const el = form.querySelector(`[name="${n}"]`);
    if (!el) return;
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v === undefined || v === null ? "" : v;
  };
  const model = (v, list, dflt) => ((list || []).includes(v) ? v : dflt);
  set("bg_source", bg.source);
  set("bg_manual", bg.manual);
  set("bg_link", bg.linkage);
  set("bg_dir", bg.assets_dir);
  set("bg_ai_vmodel", model(bg.ai_model, opts.ai_video_models, "auto"));
  set("bg_ai_pmodel", model(bg.ai_model, opts.ai_photo_models, "flux"));
  set("bg_interval", bg.interval_s);
  set("bg_motion", bg.motion);
  set("bg_cont", bg.continuous);
  set("fg_on", fg.enabled);
  set("fg_source", fg.source);
  set("fg_manual", fg.manual);
  set("fg_ai_vmodel", model(fg.ai_model, opts.ai_video_models, "auto"));
  set("fg_ai_pmodel", model(fg.ai_model, opts.ai_photo_models, "flux"));
  set("fg_width", fg.width_pct);
  set("fg_pos", fg.position);
  applyConditions(form);
}

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

// ------------------------------------------------------------------ switches
//
// Every checkbox in the panel is drawn as a switch (see app.css), and a switch that
// only takes a click is half of one: the shape says "push me across", so pushing it
// across has to work. This is that gesture — press the knob, drag, let go where you
// meant to land — and it is bound ONCE, at the document, for every checkbox there
// will ever be: the cards are built from `forms.js` and the config panels are rebuilt
// on every visit, so anything wired per element would have to be wired again after
// each of them.
//
// All it writes is `--p`, how far across the switch is, 0 to 1. The stylesheet reads
// the knob's position AND the track's colour off that one number, so the colour flows
// with the finger for free, and letting go — where the inline value is dropped and
// the property falls back to the state's own 0 or 1 — is a transition rather than a
// jump. Nothing here knows a colour or a pixel.
//
// A click is left entirely alone. Under the slop threshold nothing here fires and the
// browser toggles the box itself, which is what keeps the label, the keyboard and
// every form reader working the way they did — this adds a way to reach the state, not
// a second definition of it.
(() => {
  const SLOP = 3;      // below this the pointer was pressed, not dragged
  const EDGE = 2;      // the knob's inset at either end, from the CSS
  let sw = null, x0 = 0, from = 0, travel = 1, moved = false, swallow = false;

  // where the switch would stand if the pointer stopped here
  const at = (x) => Math.max(0, Math.min(1, from + (x - x0) / travel));

  document.addEventListener("pointerdown", (e) => {
    const t = e.target;
    if (!(t instanceof HTMLInputElement) || t.type !== "checkbox" || t.disabled) return;
    const r = t.getBoundingClientRect();
    // the knob's width is asked of the stylesheet rather than known here, which is
    // what lets the bigger switch a fingertip gets set one token and be done
    const knob = parseFloat(getComputedStyle(t).getPropertyValue("--knob-w")) || 14;
    // a new gesture disarms the previous one's leftovers: the click swallow below is
    // released HERE rather than on a timer, because a timer races the click it is
    // waiting for — and losing that race toggles the box straight back
    sw = t; x0 = e.clientX; moved = false; swallow = false;
    travel = Math.max(1, r.width - knob - EDGE * 2);
    from = t.checked ? 1 : 0;
    t.setPointerCapture(e.pointerId);
  });

  document.addEventListener("pointermove", (e) => {
    if (!sw) return;
    if (!moved) {
      if (Math.abs(e.clientX - x0) < SLOP) return;
      moved = true;
      sw.classList.add("dragging");
    }
    sw.style.setProperty("--p", at(e.clientX));
  });

  const drop = (e, keep) => {
    if (!sw) return;
    const t = sw;
    sw = null;
    t.classList.remove("dragging");
    t.style.removeProperty("--p");      // and it eases home to the state it lands in
    if (!moved || keep) return;         // a press (the browser toggles it itself), or
                                        // a gesture taken away mid-drag: leave it be
    // The click that follows this pointerup would undo the drag — it toggles the box,
    // and the box is already where the drag put it. So it is swallowed in the capture
    // phase; if no click ever comes (a drag let go somewhere that dispatches none),
    // the next pointerdown clears the flag before anything can be swallowed by it.
    swallow = true;
    const on = at(e.clientX) > 0.5;
    if (on === t.checked) return;
    t.checked = on;
    t.dispatchEvent(new Event("input", { bubbles: true }));
    t.dispatchEvent(new Event("change", { bubbles: true }));
  };
  document.addEventListener("pointerup", drop);
  // cancelled, not finished — the system took the pointer (a scroll took over, a
  // window went away). The knob springs back to the state nobody changed.
  document.addEventListener("pointercancel", (e) => drop(e, true));
  document.addEventListener("click", (e) => {
    if (!swallow) return;
    swallow = false;
    e.preventDefault();
    e.stopPropagation();
  }, true);
})();

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
  // The wizard's AI line: an instruction to the model and the button that acts on it.
  // Same shape as the one over a breakpoint's rows, and for the same reason — you tell
  // it what to change in words, not by hunting for the setting that means it.
  if (f.ai)
    return `<div class="row airow" id="${esc(f.ai)}">` +
           `<span class="mark">\u2728</span>` +
           `<input data-lp="${esc(f.ph)}">` +
           `<button type="button" class="ghost" data-l="${esc(f.go)}"></button></div>`;
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
  // Two buttons, when a card has a second way to act on what is filled in. The
  // secondary one is `type="button"` on purpose: it is not a submit, it goes somewhere
  // else with the same settings (see the fandom card's «собрать вручную»).
  const second = c.go2
    ? `<button type="button" id="${esc(c.go2.id)}" class="${esc(c.go2.cls || "ghost")}"` +
      ` data-l="${esc(c.go2.l)}"></button>` : "";
  const first = c.go
    ? `<button class="${esc(c.gocls || "primary")}" data-l="${esc(c.go)}"></button>` : "";
  const foot = second ? `<div class="row golf">${first}${second}</div>` : first;
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

function setMode(mode) {
  genMode = mode;
  document.querySelectorAll("#mode-menu [data-mode]").forEach((x) =>
    x.classList.toggle("on", x.dataset.mode === genMode));
  document.querySelectorAll("form[data-mode]").forEach((f) =>
    (f.hidden = f.dataset.mode !== genMode));
  savePlace();
  compose();
}

document.querySelectorAll("#mode-menu [data-mode]").forEach((b) => {
  b.onclick = () => {
    // a loop's mode is the one thing about it that cannot change, so walking to
    // another mode's form is walking out of the edit
    if (editing && editing.mode !== b.dataset.mode) stopEditing();
    setMode(b.dataset.mode);
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
  wireWizardAi();
  wireByHand();
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
  const fill = (sel, list, blank, empty) => {
    if (!sel) return;
    sel.innerHTML = (blank ? [""] : []).concat(list).map((x) => {
      const v = x && x.v !== undefined ? x.v : x;
      const note = x && x.note ? ` — ${lab(x.key || "", x.note)}` : "";
      // …and the blank line does not always mean "nothing": for the music it is the
      // run's own roll, which is an answer rather than the absence of one
      return `<option value="${esc(v)}">${
        esc(v ? word(v) + note : lab(empty || "w.none", "— нет —"))}</option>`;
    }).join("");
  };
  fill($("#f-world"), opts.worlds);
  fill($("#f-voice"), opts.voices);
  // blank is the ordinary answer: the world names its own catalogue, and this is the
  // override for a run that wants a different set of forms than the world usually does
  fill($("#f-shapes"), opts.shape_catalogues || [], true);
  fill($("#f-fit"), opts.fits);
  $("#f-fit").value = "close";
  document.querySelectorAll(".f-lang, #f-lang").forEach((el) => fill(el, opts.languages));
  fill($("#i-type"), opts.content_types, true);
  fill($("#i-visuals"), opts.visuals);
  // The Picture card: the visuals profile taken apart into its own controls. These
  // are the lists the terminal's wizard offers, filled here by class because two of
  // them (the generator pickers) appear once for the background and once for the
  // inserts.
  const fillAll = (cls, list) =>
    document.querySelectorAll(cls).forEach((el) => fill(el, list || []));
  fillAll(".f-bgsrc", opts.bg_sources);
  fillAll(".f-fgsrc", opts.fg_sources);
  fillAll(".f-aivid", opts.ai_video_models);
  fillAll(".f-aiphoto", opts.ai_photo_models);
  fillAll(".f-bglink", opts.bg_linkages);
  fillAll(".f-motion", opts.motions);
  fillAll(".f-fgpos", opts.fg_positions);
  // …and this one is set rather than left where the list starts: the picture fields
  // are overwritten from the profile a moment later, but the overlay's corner is not,
  // and `top_left` sorts first while `top_right` is what the model actually defaults
  // to (the ad-mode select below is set for the same reason).
  document.querySelectorAll(".f-ovpos").forEach((el) => {
    fill(el, opts.overlay_positions || []);
    el.value = "top_right";
  });
  // …and then filled FROM the profile, which is what keeps taking the card apart from
  // changing anything: a card nobody touched rebuilds the profile it was filled from,
  // and the run carries that profile's NAME rather than a copy of it (the comparison
  // is `params.manual_visuals`). Picking a different profile re-fills the card, the
  // way `GenerateScreen._vprofile` does in the terminal.
  const iv = $("#i-visuals");
  if (iv) {
    iv.addEventListener("change", () => fillPicture(iv.value));
    fillPicture(iv.value);
  }
  fill($("#d-orch"), opts.orchestrations, true);
  // by class, not by id: the same control exists in all three forms, and an id can
  // only ever name one of them — which is how these ended up empty after the rebuild
  // …plus the reserved word that means "the contract is typed in below rather than
  // picked from configs/ads/" — one control for one question, the way the music
  // select spells silence (see params.AD_MANUAL)
  document.querySelectorAll(".f-ad")
    .forEach((el) => fill(el, (opts.ads || []).concat(["manual"]), true));
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
  // the clone picker, in every mode's form: grouped by card, not a flat list of specs
  document.querySelectorAll(".f-voice-pick").forEach((el) => {
    el.innerHTML = optgroupsHTML(voiceGroups(opts.voice_cards), el.value,
                                 lab("w.none", "— нет —"));
  });
  document.querySelectorAll(".f-tts").forEach((el) => fill(el, opts.tts_engines, true));
  document.querySelectorAll(".f-subs").forEach((el) => fill(el, opts.subtitle_styles, true));
  // what plays under the voice. Blank is the ordinary answer and the interesting one:
  // the run rolls a track for itself, and the montage room plays that very one.
  document.querySelectorAll(".f-music")
    .forEach((el) => fill(el, opts.music || [], true, "w.music.roll"));
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
  // What the writer may do where the world's records stop. A slider and not a
  // checkbox because the answer that most worlds want is the MIDDLE one — invent, but
  // only to get unstuck — and a box has nowhere to put it. Its readout is the word
  // rather than the number, and the line under it changes with the position: the
  // three answers differ in what they let into a finished video, which is not
  // something a title can carry.
  document.querySelectorAll('input[type=range][name="fandom_invent"]').forEach((r) => {
    const card = r.closest(".card");
    const out = card.querySelector(".invent-val");
    const note = card.querySelector(".invent-note");
    const paint = () => {
      const level = inventLevel(r.value);
      if (out) out.textContent = lab("inv." + level);
      if (note) note.textContent = lab("inv.note." + level);
    };
    r.oninput = paint;
    paint();
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

// What the fandom form MEANS, read once. Two buttons send it now — start the chain,
// or make the run and open the montage room on it — and a second copy of thirteen
// fields is a second place for them to drift.
function fandomBody(form) {
  const f = new FormData(form);
  return {
      fandom: f.get("fandom"), voice: f.get("voice"), lang: f.get("lang") || "ru",
      // sent whatever the narrator is: the field is hidden for the other two, and a
      // hidden field still carries whatever was last typed in it, which the server
      // ignores for anyone but the usher
      viewer_role: f.get("viewer_role") || "",
      fandom_invent: inventLevel(f.get("fandom_invent")),
      medium: f.get("medium"), source: f.get("source"),
      scenario: f.get("scenario"), title: f.get("title"),
      duration_s: +f.get("duration_s"), count: +f.get("count"),
      dry_run: f.get("dry_run") === "on",
      frame_fit: f.get("frame_fit"), cut_sensitivity: +f.get("cut_sensitivity"),
      frame_by_hand: f.get("frame_by_hand") === "on",
      frame_effects: f.get("frame_effects") === "on",
    breakpoints: [...chosenBps.fandom],
    ...commonOf(form),
  };
}

$("#startform").onsubmit = (e) => {
  e.preventDefault();
  return launchOnce(e.target, async () => {
    const body = fandomBody(e.target);
    if (editing && editing.mode === "fandom") return applyToLoop(body);
    let out;
    try {
      out = await api("/api/runs/fandom", { method: "POST",
        headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    } catch (err) { say(err.message, true); return; }
    started(out);
  });
};

// The other door out of the same form: make the run, run NOTHING, and open the montage
// room on it. Starting the chain and stopping it at a breakpoint cannot serve this —
// by the time a screen appeared, the run would have decided the things you opened it
// to decide.
//
// Wired after the cards are BUILT rather than at load: this button is drawn from
// `forms.js` like every other field, so at the time this file is evaluated it does not
// exist yet — and reaching for it there throws, which takes the rest of the file's
// top-level with it (`loadRuns` and `loadLoops` then die on their own uninitialised
// state, which is how this showed up).
function wireByHand() {
  const go = $("#f-by-hand-go");
  if (!go) return;
  go.onclick = async () => {
    if (editing) return say(lab("js.byhand-not-in-a-loop"), true);
    let out;
    try {
      out = await api("/api/runs/fandom/by-hand", { method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(fandomBody($("#startform"))) });
    } catch (err) { return say(err.message, true); }
    openMontage(out.id, out.title);
  };
}

// The Picture card as the server reads it. Every field is sent, shown or hidden: what
// a hidden row holds is still the profile's own answer, and which rows are on screen
// is decided by the source — so `manual_visuals` compares the whole card against the
// whole profile rather than guessing which half of it was visible.
function pictureOf(f) {
  return {
    bg_source: f.get("bg_source") || "",
    bg_manual: f.get("bg_manual") === "on",
    bg_link: f.get("bg_link") || "",
    bg_dir: f.get("bg_dir") || "",
    bg_ai_vmodel: f.get("bg_ai_vmodel") || "",
    bg_ai_pmodel: f.get("bg_ai_pmodel") || "",
    bg_interval: +(f.get("bg_interval") || 3.5),
    bg_motion: f.get("bg_motion") || "",
    bg_cont: f.get("bg_cont") === "on",
    fg_on: f.get("fg_on") === "on",
    fg_source: f.get("fg_source") || "",
    fg_manual: f.get("fg_manual") === "on",
    fg_ai_vmodel: f.get("fg_ai_vmodel") || "",
    fg_ai_pmodel: f.get("fg_ai_pmodel") || "",
    fg_width: +(f.get("fg_width") || 78),
    fg_pos: f.get("fg_pos") || "",
  };
}

$("#infoform").onsubmit = (e) => submitRun(e, "info", (f) => ({
  lang: f.get("lang"), content_type: f.get("content_type"), visuals: f.get("visuals"),
  idea: f.get("idea"), title: f.get("title"),
  ...pictureOf(f),
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

// ------------------------------------------------ the wizard's AI help
//
// The terminal's wizard has this under the plot: a line where you say what you want in
// words and a button that writes it. It is NOT the rewrite that sits over a parked
// breakpoint — that one edits lines a run has already produced; this one writes the
// brief you launch WITH, while there is no run yet.
//
// What comes back is put in the field and tinted, never sent anywhere: it is a
// proposal, and the launch button is still yours to press.
function wireAi(boxId, act) {
  const box = document.querySelector(`#${boxId}`);
  if (!box) return;
  const input = box.querySelector("input"), btn = box.querySelector("button");
  const go = async () => {
    btn.disabled = true;
    say(lab("js.ai-working"));
    try { say(await act(input.value.trim()) || lab("js.ai-done")); }
    catch (e) { say(e.message, true); }
    finally { btn.disabled = false; }
  };
  btn.onclick = go;
  // Enter in the instruction must not reach the form: this line lives INSIDE the
  // generation form, and a stray submit would launch the run instead of asking.
  input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } };
}

// Tint what the model wrote, and drop the tint the moment a hand touches it — the mark
// says "this text is the model's", and after an edit that is no longer true.
function aiFilled(el, text) {
  el.value = text;
  el.classList.add("ai-filled");
  el.addEventListener("input", () => el.classList.remove("ai-filled"), { once: true });
}

// Called from `loadOptions`, not here: the cards these live in are built there.
function wireWizardAi() {
wireAi("f-brief-ai", async (instruction) => {
  const form = $("#startform"), f = new FormData(form);
  const r = await api("/api/ai/brief", { method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ fandom: f.get("fandom"), lang: f.get("lang") || "ru",
                           current: f.get("scenario") || "", instruction,
                           duration_s: +f.get("duration_s") || 0,
                           tts_rate: +(f.get("tts_rate") || 0) }) });
  if (!r.brief) return lab("js.ai-nothing");
  aiFilled(form.querySelector('[name="scenario"]'), r.brief);
  return lab("js.ai-done");
});

wireAi("i-topic-ai", async (instruction) => {
  const form = $("#infoform"), f = new FormData(form);
  const r = await api("/api/ai/topic", { method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ lang: f.get("lang") || "ru",
                           content_type: f.get("content_type") || "",
                           current: f.get("idea") || "", instruction }) });
  if (!r.topic) return lab("js.ai-nothing");
  aiFilled(form.querySelector('[name="idea"]'), r.topic);
  return lab("js.ai-done");
});

wireAi("d-story-ai", async (instruction) => {
  const form = $("#dramaform"), f = new FormData(form);
  const r = await api("/api/ai/story", { method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ lang: f.get("lang") || "ru", scenario: f.get("scenario") || "",
                           instruction,
                           cast: [...$("#d-cast").querySelectorAll(".on")].map((b) => b.dataset.who),
                           duration_s: +f.get("duration_s") || 0,
                           tts_rate: +(f.get("tts_rate") || 0) }) });
  const said = [];
  if (r.scenario) { aiFilled(form.querySelector('[name="scenario"]'), r.scenario); said.push(lab("js.ai-plot")); }
  // Saved characters it wants in this run: the chips are the cast, so turning them on
  // IS adding them.
  const add = new Set(r.add || []);
  let on = 0;
  $("#d-cast").querySelectorAll("[data-who]").forEach((b) => {
    if (add.has(b.dataset.who) && !b.classList.contains("on")) { b.classList.add("on"); on++; }
  });
  if (on) said.push(`${lab("js.ai-cast")} ${on}`);
  // People it made up. They are not in the library, so there is no chip to light and
  // nothing here may quietly create one — the names are reported and the choice is the
  // operator's, in the character editor.
  if ((r.invented || []).length) said.push(`${lab("js.ai-invented")} ${r.invented.join(", ")}`);
  return said.length ? said.join(" · ") : lab("js.ai-nothing");
});
}

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
    // the gap answer is a name on the wire and a position on the slider
    else if (n === "fandom_invent") el.value = inventIndex(v);
    else el.value = v;
  });
  form.querySelectorAll("[data-fx]").forEach((r) => {
    r.value = (p.filters || {})[r.dataset.fx] || 0;
    r.dispatchEvent(new Event("input"));
  });
  form.querySelectorAll("input[type=range]").forEach((r) => r.dispatchEvent(new Event("input")));
  // The Picture card, which the loop above cannot reach: its fields are not settings
  // of their own, they are one setting taken apart (`manual_visuals`), so the loop
  // finds no `params.bg_source` and leaves them showing whatever they showed last.
  // Filled from the override when the loop carries one and from the named profile
  // when it does not — the same two answers `manual_visuals` compares.
  pictureFrom(p.manual_visuals || (opts.visuals_full || {})[p.visuals]);
  // …and an ad contract typed in rather than picked, for exactly the same reason: it
  // is `manual_ad`, and `params.ad` is empty whenever one is set, so the select would
  // read "no ad" and the retune would drop the contract.
  const mad = p.manual_ad;
  if (mad) {
    const ad = form.querySelector('[name="ad"]');
    if (ad) ad.value = "manual";
    const ov = mad.overlay || {}, nat = mad.native || {};
    const set = (n, v) => {
      const el = form.querySelector(`[name="${n}"]`);
      if (el && v !== undefined && v !== null) el.value = v;
    };
    set("ad_url", mad.url);
    set("ov_text", ov.text);
    set("ov_pos", ov.position);
    set("ov_start", ov.start_s);
    set("ov_dur", ov.duration_s);
    set("ad_points", nat.talking_points);
  }
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
  put("loop_ahead", l.ahead || 0);
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
    ahead: +(f.get("loop_ahead") || 0),
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
    tts_deliveries: f.get("tts_deliveries") === "on",
    subtitle_style: f.get("subtitle_style") || "",
    ad_mode: f.get("ad_mode") || "both",
    visual_notes: f.get("visual_notes") || "",
    visual_style: f.get("visual_style") || "",
    clean_subtitles: f.get("clean_subtitles") === "on",
    write_metadata: f.get("write_metadata") === "on",
    keep_temp: f.get("keep_temp") === "on",
    music: f.get("music") || "",
    // the hand-typed ad contract. Sent whatever the ad field says: the server builds
    // it only for the reserved word (see params.manual_ad), and a form that has been
    // switched back to a named contract must not be able to smuggle one in.
    ad_url: f.get("ad_url") || "",
    ov_text: f.get("ov_text") || "",
    ov_pos: f.get("ov_pos") || "top_right",
    ov_start: +(f.get("ov_start") || 6),
    ov_dur: +(f.get("ov_dur") || 8),
    ad_points: f.get("ad_points") || "",
    loop: loopOf(form),
    filters,
  };
}

// A launch is ONE press, however many the mouse sent.
//
// These handlers awaited their fetch and nothing else, which leaves them re-entrant: a
// double click — a bouncing microswitch is enough, and that is how this was found —
// fires submit twice, and two POSTs a few hundred milliseconds apart are two runs, or
// two LOOPS with the same title and the same queue, each with a thread of its own,
// each generating. Nothing downstream can undo that: a loop is a folder with a plan in
// it, and by the time the second card appears both are already working.
//
// The server deliberately does not deduplicate. Two videos of the same thing is a
// thing people ask for on purpose, a launch is not idempotent in any useful sense, and
// a server that started guessing which presses were meant would be wrong in the
// direction that loses work. So the press is made once HERE, where the difference
// between one press and two is actually known.
const launching = new WeakSet();
async function launchOnce(form, send) {
  if (launching.has(form)) return;
  launching.add(form);
  const go = [...form.querySelectorAll("button.big-go, button[type=submit]")];
  go.forEach((b) => (b.disabled = true));
  try { await send(); }
  finally {
    launching.delete(form);
    go.forEach((b) => (b.disabled = false));
  }
}

async function submitRun(e, mode, build) {
  e.preventDefault();
  return launchOnce(e.target, async () => {
    const body = { ...commonOf(e.target), ...build(new FormData(e.target)) };
    if (editing && editing.mode === mode) return applyToLoop(body);
    let out;
    try {
      out = await api(`/api/runs/${mode}`, { method: "POST",
        headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    } catch (err) { say(err.message, true); return; }
    started(out);
  });
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
//
// Which run's asks are on screen, so a delivery can redraw them. The title is the one
// thing a redraw needs that the server does not send, and the panel used to recover it
// by splitting the heading it had itself written — which held only as long as the
// translated word between the two happened to carry a dash.
let asksOf = null;

async function openAsks(id, title) {
  asksOf = { id, title };
  const d = await api(`/api/runs/${id}/asks`);
  $("#panel-title").textContent = `${title} ${lab("js.missing")} ${d.pending} ${lab("js.of")} ${d.shots.length}`;
  $("#panel-apply").hidden = true;
  // the same panel carries both screens; asks have no rows to rewrite and no plan
  $("#panel-ai").hidden = true;
  $("#panel-replan").hidden = true;
  const base = d.base || [];
  $("#panel-body").innerHTML = d.shots.length ? d.shots.map((sh) => {
    const url = `/api/runs/${id}/asks/${encodeURIComponent(sh.video)}/${encodeURIComponent(sh.id)}/file`;
    // what arrived is shown, not merely reported: a wrong file under the right name
    // reads identically in a manifest. A card taken out of the base is shown from the
    // world instead — there is nothing in the inbox to show, and the picture is still
    // the only honest answer to "which one did I pick".
    const fromBase = sh.from_base ? base.find((c) => c.name === sh.card) : null;
    const preview = sh.delivered
      ? (sh.photo ? `<img class="got" src="${tokd(url)}" alt="">`
                  : `<video class="got" src="${tokd(url)}" controls preload="metadata"></video>`)
      : fromBase
        ? `<div class="picked"><img class="got" src="${tokd(fromBase.url)}" alt="">
             <span class="dim">${lab("js.pinned-from-the-base")} <b>${esc(sh.card)}</b></span></div>`
        : "";
    const owed = sh.status !== "delivered";
    return `<div class="ask ${owed ? "" : "done"}" data-v="${esc(sh.video)}" data-s="${esc(sh.id)}">
      <div class="head"><b>${esc(sh.id)}</b>
        <span class="dim">${sh.size[0]}×${sh.size[1]} · ${esc(sh.status)}${sh.target_s ? ` · ~${sh.target_s.toFixed(1)}${lab("js.s")}` : ""}</span>
        <span class="grow"></span>
        <button class="ghost" data-copy>${lab("js.copy-the-prompt")}</button>
        ${owed && sh.id.startsWith("frame_") ? `<button class="ghost" data-base>${lab("js.from-the-base")}</button>` : ""}
        ${sh.card ? `<button class="ghost" data-mark="${esc(sh.card)}">${lab("js.mark-it-up")}</button>` : ""}
        ${owed ? "" : `<button class="ghost" data-undo>${lab("js.replace")}</button>`}</div>
      ${sh.said ? `<p class="said"><span class="dim">${lab("js.said-here")}</span> ${esc(sh.said)}</p>` : ""}
      <pre>${esc(sh.prompt)}</pre>
      ${preview}
      ${owed ? `<div class="basestrip" hidden></div>
        <label class="take">${lab("js.drop-a-picture-here")}
        <input type="file" hidden accept="image/*,video/*"></label>` : ""}
    </div>`;
  }).join("") : `<p class="empty">${lab("js.this-run-is-not-asking-for-anything")}</p>`;
  bindAsks(id, d.world || "", base);
  $("#panel").hidden = false;
}

function bindAsks(id, forWorld, base) {
  const again = () => openAsks(id, asksOf ? asksOf.title : "");
  $("#panel-body").querySelectorAll(".ask").forEach((el) => {
    const v = el.dataset.v, sid = el.dataset.s;
    const ask = (path, opts) =>
      api(`/api/runs/${id}/asks/${encodeURIComponent(v)}/${encodeURIComponent(sid)}${path}`, opts);
    el.querySelector("[data-copy]").onclick = () =>
      navigator.clipboard.writeText(el.querySelector("pre").textContent);
    const mark = el.querySelector("[data-mark]");
    if (mark) mark.onclick = () => openMarkup(forWorld, mark.dataset.mark);
    // The base, offered as PICTURES. The matcher declined every one of these by reading
    // its description, and picking between pictures by their names would be the same
    // mistake in the other direction — so the strip is thumbnails, and clicking one
    // answers the ask. It is drawn on the first press rather than with the row: a world
    // with forty cards and twenty asks would otherwise be eight hundred thumbnails, all
    // but one strip of them behind a closed panel.
    const openBase = el.querySelector("[data-base]");
    const strip = el.querySelector(".basestrip");
    if (openBase && strip) openBase.onclick = () => {
      if (!strip.hidden) {
        strip.hidden = true;
        openBase.textContent = lab("js.from-the-base");
        return;
      }
      if (!strip.dataset.drawn) {
        strip.innerHTML = base.length ? base.map((c) => `
          <div class="frame-card" data-pick="${esc(c.name)}" title="${esc(c.description || c.name)}">
            <div class="thumb" style="background-image:url('${tokd(c.poster || c.url)}')">
              ${c.kind === "video" ? `<span class="pill">${lab("js.clip")}</span>` : ""}
              ${c.targets.length ? `<span class="pill">${c.targets.length}</span>` : ""}
            </div>
            <div class="meta"><b>${esc(c.name)}</b></div>
          </div>`).join("") : `<p class="empty">${lab("js.the-base-is-empty")}</p>`;
        strip.dataset.drawn = "1";
        strip.querySelectorAll("[data-pick]").forEach((c) => {
          c.onclick = async () => {
            try {
              await ask("/card", { method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({ card: c.dataset.pick }) });
            } catch (e) { return say(e.message, true); }
            say(`${lab("js.pinned-from-the-base")} ${c.dataset.pick}`);
            again();
          };
        });
      }
      strip.hidden = false;
      openBase.textContent = lab("js.hide-the-base");
    };
    const undo = el.querySelector("[data-undo]");
    if (undo) undo.onclick = async () => {
      await ask("", { method: "DELETE" });
      say(lab("js.you-can-bring-it-again"));
      again();
    };
    const take = el.querySelector(".take");
    if (!take) return;
    const input = take.querySelector("input");
    const send = async (file) => {
      const body = new FormData(); body.append("file", file);
      take.textContent = lab("js.sending");
      let got;
      try {
        got = await ask("", { method: "POST", body });
      } catch (e) { take.textContent = lab("js.did-not-work") + e.message; return; }
      // redrawn off the manifest rather than dressed up here: the delivery is now
      // recorded there, and the count in the heading, the preview and the replace
      // button are all things only the server can be right about
      await again();
      // a frame is a card in the world the moment it is handed over, and a card with
      // no regions marked can only be held and drifted across — so the offer to mark
      // it up is made now, while the operator is still looking at the picture
      if (got && got.card) {
        say(lab("js.card-added-say-what-is-in-it"));
        openMarkup(got.world, got.card);
      }
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
//
// One generic renderer for every breakpoint: `pipeline/review.py` already says how each
// row is edited (`kind`), what it may become (`options`), which item it belongs to
// (`field`) and what may be done to the document as a whole (`variable`, `cuttable`),
// so nothing here knows what a canon sheet or a picture track is.
//
// It renders ITEMS and not rows, which the flat version could not. A scene is five rows
// — what is said, what is shown, who is in it, which generator, how long — and they are
// one thing: they move together, they are dropped together, and each of them needs
// saying which of the five it is. Flattened, the screen showed five identical boxes all
// captioned "#1", nothing could be added, dropped or reordered, and a part separator
// came out as the untranslated string `bp.f.part`. The pipeline has supported every bit
// of this since the terminal got it; only this file had not.
let reviewState = null;
let dragGroup = null;

// The row field that opens a new item — the same set as `review.HEAD_FIELDS`, and it
// has to stay the same: `apply` groups the rows it gets back by exactly this rule.
const HEAD_FIELDS = new Set(["text", "name", "part", "plan_subject"]);
const PART_FIELD = "part";
// The head of the fandom plan block (`review.PLAN_HEAD`). Its presence in a document
// is what says this script was written from a plan, and therefore can be written from
// one again — which is the whole of how the rewrite button decides to show itself.
const PLAN_HEAD = "plan_subject";

// Rows into items. A row whose field is a head field opens one; the rest attach to
// whatever is open (see `review.group_rows`).
function groupRows(rows) {
  const out = [];
  rows.forEach((r) => {
    if (HEAD_FIELDS.has(r.field || "text") || !out.length) out.push({ head: r, extras: [] });
    else out[out.length - 1].extras.push(r);
  });
  return out;
}
const flatten = (groups) => groups.flatMap((g) => [g.head, ...g.extras]);

// What to caption one row with. A document of named fields (a topic, a canon sheet, a
// title) sends an i18n key as its label and the caption is that; a document of numbered
// items sends "#3" for every one of its rows, and the caption is which PART of item 3
// this row is — the question the flat renderer left unanswered.
const rowLabel = (r) => (String(r.label || "").startsWith("bp.")
  ? lab(r.label) : lab("bp.field." + (r.field || "text"), r.field || ""));

// A blank item, cut to the shape of an existing one: the same rows in the same order,
// emptied. `src: null` is what tells the pipeline this is an insertion rather than a
// rewrite (see `review._apply_script`), and each row's kind and options come along so a
// new scene still gets its generator dropdown instead of a bare box.
function blankGroup(model) {
  const blank = (r) => ({ ...r, value: "", src: null, info: "", readonly: false });
  return { head: blank(model.head), extras: model.extras.map(blank) };
}

function moveButtons(on) {
  if (!on) return "";
  return `<button type="button" data-mv="-1" title="${esc(lab("bp.up"))}">▲</button>` +
         `<button type="button" data-mv="1" title="${esc(lab("bp.down"))}">▼</button>` +
         `<button type="button" data-drop="1" title="${esc(lab("bp.remove"))}">✖</button>`;
}

function reviewControl(r, i) {
  if (r.readonly) return `<div>${esc(r.value)}</div>`;
  if (r.kind === "choice")
    return `<select data-i="${i}">${["", ...r.options].map((o) =>
      `<option${o === r.value ? " selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  return `<textarea data-i="${i}" rows="${r.value.length > 90 ? 3 : 1}">${esc(r.value)}</textarea>`;
}

// The caption of one item, numbered by where it SITS.
//
// The number the server sent is the number the item had when the document was built,
// and every structural edit invalidates it — drop the second of five and the three
// below go on calling themselves 3, 4, 5. Separators were already numbered by position
// (see `review.part_row`); items were not, and a screen where two cards both say #3 is
// worse than one with no numbers at all.
//
// Only the number is replaced, never the rest of the label: `#3 · AD` marks the ad
// break (`review._scene_label`) and that half is not positional.
const itemLabel = (label, n) => (/^#\d+/.test(label) ? label.replace(/^#\d+/, `#${n}`)
                                                     : label);

function renderReview() {
  const d = reviewState;
  const groups = groupRows(d.rows);
  // Separators are numbered by where they SIT and never by the number stored in them:
  // moving one is how a video is re-cut, and the numbers follow (see review.part_row).
  let part = 0, no = 0;
  const html = groups.map((g, gi) => {
    const head = g.head;
    if ((head.field || "") === PART_FIELD) {
      part += 1;
      return `<div class="rgroup sep" data-g="${gi}"${d.cuttable ? " draggable=\"true\"" : ""}>
        <div class="ghead">${d.cuttable ? '<span class="handle">≡</span>' : ""}
          <b>${esc(lab("bp.sep").replace("{n}", part))}</b>
          <span class="grow"></span>${moveButtons(d.cuttable)}</div>
        <p class="dim">${esc(lab("bp.sep_hint").replace("{n}", part))}</p></div>`;
    }
    no += 1;
    const title = String(head.label || "").startsWith("bp.")
      ? "" : itemLabel(String(head.label || ""), no);
    const rows = [head, ...g.extras].map((r) => `
      <div class="rrow"><div class="lab">${esc(rowLabel(r))}${
        r.info ? " · " + esc(humanise(r.info)) : ""}</div>
        ${reviewControl(r, d.rows.indexOf(r))}</div>`).join("");
    const head_html = title || d.variable
      ? `<div class="ghead">${d.variable ? '<span class="handle">≡</span>' : ""}` +
        `<b>${esc(title)}</b><span class="grow"></span>${moveButtons(d.variable)}</div>`
      : "";
    return `<div class="rgroup" data-g="${gi}"${d.variable ? " draggable=\"true\"" : ""}>` +
           `${head_html}${rows}</div>`;
  }).join("");
  const foot = [
    d.variable ? `<button type="button" data-add="item">${esc(lab("bp.add"))}</button>` : "",
    d.cuttable ? `<button type="button" data-add="part">${esc(lab("bp.cut"))}</button>` : "",
  ].filter(Boolean).join("");
  const note = [d.note_key ? lab(d.note_key) : "", d.note_extra].filter(Boolean).join(" · ");
  $("#panel-body").innerHTML = html
    + (foot ? `<div class="rfoot">${foot}</div>` : "")
    + (note ? `<p class="dim rnote">${esc(note)}</p>` : "");
  bindReview();
}

// Every structural edit is the same shape: rearrange the GROUPS, flatten them back into
// `rows`, redraw. Nothing is sent anywhere until "apply" — a breakpoint is a draft the
// operator is holding, and a run that resumed itself halfway through a re-cut would be
// the worst possible reading of a moved separator.
function bindReview() {
  const body = $("#panel-body");
  body.querySelectorAll("[data-i]").forEach((el) => {
    el.oninput = () => (reviewState.rows[+el.dataset.i].value = el.value);
    el.onchange = () => (reviewState.rows[+el.dataset.i].value = el.value);
  });
  const groups = () => groupRows(reviewState.rows);
  const commit = (gs) => { reviewState.rows = flatten(gs); renderReview(); };
  const isPart = (g) => (g.head.field || "") === PART_FIELD;
  body.querySelectorAll(".rgroup").forEach((el) => {
    const gi = +el.dataset.g;
    el.querySelectorAll("[data-mv]").forEach((b) => (b.onclick = () => {
      const gs = groups();
      const to = gi + +b.dataset.mv;
      if (to < 0 || to >= gs.length) return;
      [gs[gi], gs[to]] = [gs[to], gs[gi]];
      commit(gs);
    }));
    const drop = el.querySelector("[data-drop]");
    if (drop) drop.onclick = () => {
      const gs = groups();
      // Everything may go, the last item included. Refusing that was meant to keep a
      // document with something to say, and it instead stood between the operator and
      // the commonest reason to open this screen at all: throwing the model's attempt
      // away and writing the piece by hand. What a document may not BE is empty when it
      // is applied, and that is checked where it is true (see `#panel-apply`) rather
      // than three edits earlier.
      //
      // The shape is what actually had to survive, and it is remembered rather than
      // guarded: `bp.add` cuts a new item to the shape of an existing one, so on a
      // cleared document there would have been nothing to cut it from.
      if (!isPart(gs[gi])) reviewState.shape = blankGroup(gs[gi]);
      gs.splice(gi, 1);
      commit(gs);
    };
    if (!el.hasAttribute("draggable")) return;
    const handle = el.querySelector(".handle");
    if (handle) {
      handle.ondragstart = (e) => { dragGroup = gi; e.dataTransfer.effectAllowed = "move"; };
      handle.ondragend = () => { el.classList.remove("dragging"); dragGroup = null; };
    }
    el.ondragstart = (e) => {
      dragGroup = gi;
      e.dataTransfer.effectAllowed = "move";
      el.classList.add("dragging");
    };
    el.ondragend = () => { el.classList.remove("dragging"); dragGroup = null; };
    el.ondragover = (e) => { if (dragGroup !== null) { e.preventDefault(); el.classList.add("over"); } };
    el.ondragleave = () => el.classList.remove("over");
    el.ondrop = (e) => {
      el.classList.remove("over");
      if (dragGroup === null || dragGroup === gi) return;
      e.preventDefault();
      const gs = groups();
      const [moved] = gs.splice(dragGroup, 1);
      dragGroup = null;
      gs.splice(gi, 0, moved);
      commit(gs);
    };
  });
  body.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => {
    const gs = groups();
    if (b.dataset.add === "part") {
      gs.push({ head: { label: "bp.f.part", value: "", src: null, info: "",
                        readonly: true, field: PART_FIELD, kind: "text", options: [] },
                extras: [] });
    } else {
      // shaped like the last real item, because that is the shape this document's
      // items have — a script's five rows, a registry's three. On a document cleared to
      // nothing there is none left to copy, and the shape of the last one dropped is
      // what stands in for it.
      const model = [...gs].reverse().find((g) => !isPart(g));
      const fresh = model ? blankGroup(model) : reviewState.shape;
      if (!fresh) return;
      gs.push(blankGroup(fresh));
    }
    commit(gs);
    // a new item is empty, and the only reason to add one is to type in it
    const boxes = $("#panel-body").querySelectorAll(".rgroup:last-of-type textarea");
    if (boxes.length) boxes[0].focus();
  }));
}

async function openReview(id, title) {
  const d = await api(`/api/runs/${id}/review`);
  if (!d.stage) { say(lab("js.this-run-is-not-sitting-at-a-breakpoint"), true); return; }
  reviewState = { id, video: d.video, stage: d.stage, rows: d.rows,
                  subject: d.subject, variable: d.variable, cuttable: d.cuttable,
                  note_key: d.note_key, note_extra: d.note_extra };
  $("#panel-title").textContent = `${title} — ${word(d.stage)}`;
  $("#panel-apply").hidden = false;
  // Rewriting from the plan is offered exactly where there IS one: the plan block is
  // in the document or it is not, and asking the rows is how the browser finds out
  // without being told which modes plan and which do not.
  $("#panel-replan").hidden = !d.rows.some((r) => (r.field || "") === PLAN_HEAD);
  // The AI edit line, on any breakpoint that has prose to edit. A chip set or a
  // generator choice is not prose, so a document made only of those gets no line.
  $("#panel-ai").hidden = !d.rows.some((r) => !r.readonly && (r.kind || "text") === "text");
  $("#panel-ai-text").value = "";
  renderReview();
  $("#panel").hidden = false;
}

$("#panel-apply").onclick = async () => {
  if (!reviewState) return;
  // The one thing a document may not be. Every item can be dropped — clearing the board
  // and writing the piece by hand is a normal way to use this screen — but applying
  // nothing is not an edit, it is a video with no scenes. The pipeline already declines
  // it (`review._apply_script` keeps the old scenes when the new list comes out empty),
  // silently, which reads as the edit having been thrown away.
  if (reviewState.variable
      && !groupRows(reviewState.rows).some((g) => (g.head.field || "") !== PART_FIELD)) {
    say(lab("bp.apply_empty"), true);
    return;
  }
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
// Write the beats again from the plan above them. The one breakpoint action that does
// not end the review: the run stays parked, the document is fetched again, and what
// comes back is a new script under the same plan — so the operator can argue with the
// plan several times over without the pipeline moving on behind their back.
$("#panel-replan").onclick = async () => {
  if (!reviewState) return;
  const btn = $("#panel-replan");
  btn.disabled = true;
  // it is several LLM calls and a minute or two of them; a button that merely goes
  // quiet for that long reads as a button that did nothing
  say(lab("js.replan-working"));
  try {
    const { id, video, stage, rows } = reviewState;
    const r = await api(`/api/runs/${id}/review/replan`, { method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ video, stage, rows }) });
    // refetched rather than patched: the rewrite decides how many beats there are,
    // and the document is the server's to describe
    const title = $("#panel-title").textContent.split(" — ")[0];
    await openReview(id, title);
    say(`${lab("js.replan-done")} ${r.beats}`);
  } catch (e) { say(e.message, true); } finally { btn.disabled = false; }
};

// Hand the model the lines and one instruction — "shorter", "make scene 3 angrier",
// "split this into two beats" — and it returns the whole list edited. The reply is put
// into the fields rather than applied: it is a draft to look at, and the operator still
// presses the button that goes on.
$("#panel-ai-go").onclick = async () => {
  if (!reviewState) return;
  const instruction = $("#panel-ai-text").value.trim();
  if (!instruction) return;
  const btn = $("#panel-ai-go");
  btn.disabled = true;
  say(lab("js.ai-working"));
  try {
    const r = await api(`/api/runs/${reviewState.id}/review/ai`, { method: "POST",
      headers: { "content-type": "application/json" },
      // the video index comes too: it is how the server finds the job, and the job is
      // what carries the world this piece is set in (see `review.world_context`)
      body: JSON.stringify({ instruction, rows: reviewState.rows, video: reviewState.video,
                             subject: reviewState.subject, variable: reviewState.variable }) });
    reviewState.rows = r.rows;
    renderReview();
    $("#panel-ai-text").value = "";
    say(r.changed ? lab("js.ai-done") : lab("js.ai-nothing"), !r.changed);
  } catch (e) { say(e.message, true); } finally { btn.disabled = false; }
};

// The list underneath is redrawn on the way out, because the panel is where a run's
// answer is given: pictures are delivered and a breakpoint is applied here, and what
// the row says about the run — how many pictures are still owed, what it is parked on —
// was decided before any of that happened.
$("#panel-close").onclick = () => {
  $("#panel").hidden = true; reviewState = null; asksOf = null; loadRuns();
};

// ---------------------------------------------------------------- loops
//
// A loop is not a run and does not stream: it makes runs, and they stream. What it has
// instead is a PLAN that may be edited between videos, so its card is controls — who
// picks the topics, how many are left, which stages stop for review — and the videos it
// has made are the ordinary run rows underneath.
//
// Most of the card is the queue, and the queue IS the plan. An entry in it is a whole
// video: its topic, and its own answers to the settings where it wants to differ from
// the loop (see pipeline/loop.QueueItem). All of it is edited here, in the card —
// reordered, retyped, given a longer length, thrown away — because walking to another
// tab to change one video's length is how a queue stops being worth keeping. The one
// thing that still opens the big form is the loop's OWN settings, and that is right:
// they are a whole run, and it is the whole form that describes one.
let loopTimer = null;
let loopsData = [];
let dragQueue = null;

// What the operator has open, selected and half-typed, kept OUT of the DOM. The card is
// redrawn every few seconds from the server and again after every edit; state that
// lived in the markup would be lost each time — an open editor would slam shut under
// the hand that opened it.
const qUI = new Map();
function uiOf(id) {
  if (!qUI.has(id))
    qUI.set(id, { open: new Set(), more: new Set(), sel: new Set(),
                  // the bulk form: which fields are ticked, in the order they were
                  // ticked, and what has been typed into them
                  pick: [], vals: {}, bmore: false });
  return qUI.get(id);
}

// Whether a redraw would land on top of somebody. Polling exists to show what the loop
// is doing; it must never take the caret out of a field being typed in or shut a panel
// mid-edit, so while the queue is being worked on the poll skips its turn. Every edit
// forces a redraw of its own, so nothing is stale for longer than the operator's hands.
function loopsBusy() {
  const box = $("#loops");
  const held = box && document.activeElement && document.activeElement !== document.body
    && box.contains(document.activeElement);
  return !!held || [...qUI.values()].some((u) => u.open.size || u.sel.size);
}

function renderLoops() {
  $("#loops").innerHTML = loopsData.map(loopCard).join("");
  bindLoops(loopsData);
}

async function loadLoops(force = false) {
  let loops;
  try { loops = await api("/api/loops"); } catch { return; }
  loopsData = loops;
  if (force || !loopsBusy()) renderLoops();
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

// ---------------------------------------------- one queued video's settings
//
// Drawn from what the server says a video of this mode may be given (`/api/options` →
// `overrides`), so nothing here knows what a narrator or a filter is. Two states per
// field, and the difference between them is the whole idea: a field the entry does not
// answer shows the LOOP's value, greyed — change it and it becomes this video's, press
// ↺ and it goes back to being the loop's. That is what makes a queue of ten videos
// editable one setting at a time rather than ten forms deep.

const ovSpecs = (mode) => ((opts && opts.overrides && opts.overrides[mode]) || []);

// What this entry would be made with if it said nothing: the loop's own answer.
function inherited(l, f) {
  if (f === "breakpoints") return l.breakpoints || [];
  const v = (l.params || {})[f];
  return v === undefined || v === null ? "" : v;
}

function ovControl(spec, value, own) {
  const cls = own ? "" : " inherit";
  const n = `data-ov="${esc(spec.f)}"`;
  if (spec.kind === "check")
    return `<input type="checkbox" ${n} class="${cls.trim()}"${value ? " checked" : ""}>`;
  // Most choices are named in the shared `w.` vocabulary; a setting whose values would
  // collide there (the gap answer's "no" and "free" are ordinary English words, and
  // `humanise` would then rewrite them inside every info line) names its own prefix.
  const optWord = (o) => lab((spec.opt_l || "w.") + o, o);
  if (spec.kind === "select")
    return `<select ${n} class="${cls.trim()}">` + (spec.options || []).map((o) =>
      `<option value="${esc(o)}"${String(o) === String(value) ? " selected" : ""}>` +
      `${esc(o ? optWord(o) : lab(spec.blank_l || "w.none", "—"))}</option>`).join("") + "</select>";
  if (spec.kind === "chips") {
    const on = new Set(value || []);
    return `<span class="chips${cls}" ${n}>` + (spec.options || []).map((o) =>
      `<button type="button" data-c="${esc(o)}" class="${on.has(o) ? "on" : ""}">${esc(word(o))}</button>`
    ).join("") + "</span>";
  }
  if (spec.kind === "fx") {
    const dose = value || {};
    return `<span class="fxset${cls}" ${n}>` + ((opts && opts.filters) || []).map((f) => `
      <span class="slider" title="${esc(f.note)}">
        <span class="top"><span>${esc(lab("fx." + f.key, f.key))}</span>
          <span class="grow"></span><span class="dose">${+(dose[f.key] || 0)}</span></span>
        <input type="range" data-fx="${esc(f.key)}" min="0" max="100" step="5" value="${+(dose[f.key] || 0)}">
      </span>`).join("") + "</span>";
  }
  if (spec.kind === "range")
    return `<span class="slider${cls}" ${n}><span class="top"><span class="grow"></span>` +
      `<span class="dose">${esc(value)}</span></span>` +
      `<input type="range" min="${spec.min}" max="${spec.max}" step="${spec.step || 1}" value="${esc(value)}"></span>`;
  if (spec.kind === "area")
    return `<textarea ${n} rows="2" class="${cls.trim()}">${esc(value)}</textarea>`;
  const num = spec.kind === "number"
    ? ` type="number" min="${spec.min}" max="${spec.max}" step="${spec.step || 1}"` : ' type="text"';
  return `<input${num} ${n} class="${cls.trim()}" value="${esc(value)}">`;
}

// Read one field back out of the DOM, off the element that carries its name. The
// inverse of `ovControl` and the only other place that knows the six kinds, which is
// why the two sit together: adding a kind means editing both or neither.
function ovRead(spec, el) {
  if (!el) return null;
  if (spec.kind === "chips") return [...el.querySelectorAll("button.on")].map((b) => b.dataset.c);
  if (spec.kind === "fx") {
    const out = {};
    el.querySelectorAll("[data-fx]").forEach((r) => { if (+r.value > 0) out[r.dataset.fx] = +r.value; });
    return out;
  }
  if (spec.kind === "range") return +el.querySelector("input").value;
  if (spec.kind === "check") return el.checked;
  if (spec.kind === "number") return +el.value;
  return el.value;
}

function ovRow(l, item, spec) {
  const own = Object.prototype.hasOwnProperty.call(item.over || {}, spec.f);
  const value = own ? item.over[spec.f] : inherited(l, spec.f);
  return `<div class="ovrow${own ? " own" : ""}" data-f="${esc(spec.f)}">
    <div class="lab"><span>${esc(lab(spec.l, spec.f))}</span>
      ${own ? `<button type="button" class="ghost undo" title="${esc(lab("js.q.inherit"))}">↺</button>` : ""}</div>
    ${ovControl(spec, value, own)}</div>`;
}

function ovBlock(l, item, ui) {
  const specs = ovSpecs(l.mode);
  const more = ui.more.has(item.id);
  const main = specs.filter((s) => s.main), extra = specs.filter((s) => !s.main);
  return `<div class="qedit">
    <div class="ovgrid">${main.map((s) => ovRow(l, item, s)).join("")}</div>
    ${extra.length ? `<button type="button" class="ghost qmore">${
      esc(lab(more ? "js.q.less" : "js.q.more"))}</button>` : ""}
    ${more ? `<div class="ovgrid">${extra.map((s) => ovRow(l, item, s)).join("")}</div>` : ""}
  </div>`;
}

// ---------------------------------------------------------------- the queue

function queueRow(l, item, i, ui) {
  const n = Object.keys(item.over || {}).length;
  const open = ui.open.has(item.id);
  return `<li data-item="${esc(item.id)}"${open ? ' class="open"' : ""}>
    <div class="qrow">
      <span class="handle" draggable="true" title="${esc(lab("js.q.drag"))}">⠿</span>
      <input type="checkbox" class="qpick"${ui.sel.has(item.id) ? " checked" : ""}>
      <span class="qn">${i + 1}</span>
      <input class="qtopic" value="${esc(item.topic)}" placeholder="${esc(lab("js.q.blank"))}">
      ${item.by === "ai" ? `<span class="qai" title="${esc(lab("js.q.byai"))}">✨</span>` : ""}
      <button type="button" class="ghost qset${n ? " on" : ""}" title="${esc(lab("js.q.settings"))}">⚙${n || ""}</button>
      <button type="button" class="ghost qup">↑</button>
      <button type="button" class="ghost qdown">↓</button>
      <button type="button" class="ghost qdel">×</button>
    </div>
    ${open ? ovBlock(l, item, ui) : ""}</li>`;
}

// The bulk form: as many fields as you like, applied to as many videos as you like, but
// ONLY the ones you ticked. That tick is the whole design. A form filled in for six
// entries and applied whole can only say what all six are to BECOME, so it flattens
// every difference between them — the operator who wanted six of them two minutes long
// would silently have given all six the same voice as well. Ticking is what turns "what
// these videos are" into "what I am changing about them".
//
// A ticked field jumps to the top and lights up, so what is about to be written is one
// short list at the top of the panel rather than something to find again among thirty
// controls; touching a control ticks it, because touching it is what wanting it means.
function bulkRow(l, ui, spec) {
  const on = ui.pick.includes(spec.f);
  const value = on && spec.f in ui.vals ? ui.vals[spec.f] : inherited(l, spec.f);
  return `<div class="ovrow${on ? " own" : ""}" data-f="${esc(spec.f)}">
    <div class="lab"><input type="checkbox" class="bpick"${on ? " checked" : ""}>
      <span>${esc(lab(spec.l, spec.f))}</span></div>
    ${ovControl(spec, value, on)}</div>`;
}

function bulkBar(l, ui) {
  const specs = ovSpecs(l.mode);
  if (!specs.length) return "";
  const picked = ui.pick.map((f) => specs.find((s) => s.f === f)).filter(Boolean);
  const rest = specs.filter((s) => !ui.pick.includes(s.f));
  const main = rest.filter((s) => s.main), extra = rest.filter((s) => !s.main);
  const grid = (list) => `<div class="ovgrid">${list.map((s) => bulkRow(l, ui, s)).join("")}</div>`;
  return `<div class="bulk">
    <div class="row"><b>${esc(lab("js.q.picked"))} ${ui.sel.size}</b>
      <span class="dim">${esc(lab("js.q.bulknote"))}</span></div>
    ${picked.length ? `<div class="picked">${grid(picked)}</div>` : ""}
    ${main.length ? grid(main) : ""}
    ${extra.length ? `<button type="button" class="ghost bmore">${
      esc(lab(ui.bmore ? "js.q.less" : "js.q.more"))}</button>` : ""}
    ${ui.bmore && extra.length ? grid(extra) : ""}
    <div class="row">
      <button type="button" class="primary bset"${picked.length ? "" : " disabled"}>${
        esc(lab("js.q.applyto"))} ${ui.sel.size}</button>
      <button type="button" class="ghost bclear"${picked.length ? "" : " disabled"}>${
        esc(lab("js.q.inherit"))}</button>
      <span class="grow"></span>
      <button type="button" class="ghost bdup">${esc(lab("js.q.dup"))}</button>
      <button type="button" class="ghost bdel">${esc(lab("js.q.drop"))}</button>
    </div>
  </div>`;
}

function loopCard(l) {
  const ui = uiOf(l.id);
  const items = l.topics || [];
  const have = new Set(items.map((i) => i.id));
  ui.sel = new Set([...ui.sel].filter((id) => have.has(id)));
  ui.open = new Set([...ui.open].filter((id) => have.has(id)));
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
        ${l.live
          // Told to stop and not stopped yet is its own state, and it lasts as long as
          // the video being made — `stop` deliberately never tears one in half. Shown as
          // the button it already is, pressed: the same press again does nothing, and
          // repeating it is exactly what an operator does when a card will not react.
          // Beside it, the one thing that DOES end it now, named for what it costs.
          ? (l.stopping
              ? `<span class="dim">${esc(lab("js.loop.stopping"))}</span>
                 ${l.at_run ? `<button data-loopact="stopnow" class="ghost">${esc(lab("js.loop.stop-now"))}</button>` : ""}`
              : `<button data-loopact="stop" class="ghost">${esc(lab("js.loop.stop"))}</button>`)
          // An ended loop is not a finished one: its queue, its settings and its tally
          // are all still here, and until these existed the card was a museum piece —
          // six topics waiting and nothing to press. Start carries on through what is
          // left; delete throws the plan away and leaves every video it made alone.
          : `<button data-loopact="start" class="ghost">${esc(lab("js.loop.start"))}</button>
             <button data-loopact="drop" class="ghost">${esc(lab("js.loop.forget"))}</button>`}
      </div>
      ${l.note ? `<div class="why">${esc(lab(l.note, humanise(l.note)))}</div>` : ""}
      <div class="row">
        <span class="dim">${esc(lab("js.loop.who"))}</span>
        <span class="chips">${chip("src", "ai", l.source === "ai", word("ai"))}${chip("src", "me", l.source === "me", word("me"))}</span>
        <span class="dim">${esc(lab("js.loop.limit"))}</span>
        <input class="lim" type="number" min="0" value="${l.limit}">
        <span class="dim">${esc(lab("js.loop.park"))}</span>
        <span class="chips">${chip("park", "hold", l.on_park === "hold", word("hold"))}${chip("park", "go_on", l.on_park === "go_on", word("go_on"))}</span>
        ${l.source === "ai" ? `<span class="dim" title="${esc(lab("js.loop.ahead.note"))}">${esc(lab("js.loop.ahead"))}</span>
          <input class="ahead" type="number" min="0" max="50" value="${l.ahead || 0}">` : ""}
      </div>
      <div class="row qhead">
        <span class="dim">${esc(lab("js.loop.queued"))} ${items.length}</span>
        <input class="topic" placeholder="${esc(lab("js.loop.addtopic"))}">
        <button data-loopact="add" class="ghost">${esc(lab("web.add"))}</button>
        <button data-loopact="ai" class="ghost">✨ ${esc(lab("js.q.askai"))}</button>
        <span class="grow"></span>
        ${items.length ? `<button data-loopact="all" class="ghost">${
          esc(lab(ui.sel.size === items.length ? "js.q.none" : "js.q.all"))}</button>` : ""}
      </div>
      ${ui.sel.size ? bulkBar(l, ui) : ""}
      ${items.length ? `<ol class="queue">${items.map((it, i) => queueRow(l, it, i, ui)).join("")}</ol>`
                     : `<p class="dim qempty">${esc(lab("js.q.empty"))}</p>`}
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
    loadLoops(true);
  };
  // The queue is sent WHOLE. Entries carry ids, so the server needs no telling which
  // kind of edit this was — reordering, retyping and removing are all "the queue is now
  // this", and one door is one thing that can go wrong.
  const putQueue = async (l) => {
    try {
      await api(`/api/loops/${l.id}/queue`, { method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ items: l.topics }) });
    } catch (e) { say(e.message, true); }
    loadLoops(true);
  };
  const patch = async (l, body) => {
    try {
      await api(`/api/loops/${l.id}/queue`, { method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ids: [...uiOf(l.id).sel], ...body }) });
    } catch (e) { say(e.message, true); }
    loadLoops(true);
  };

  document.querySelectorAll("#loops .loop").forEach((box) => {
    const id = box.dataset.loop, l = byId[id], ui = uiOf(id);
    const items = l.topics || [];
    const specs = ovSpecs(l.mode);
    box.querySelectorAll("[data-src]").forEach((b) =>
      (b.onclick = () => put(id, { source: b.dataset.src })));
    box.querySelectorAll("[data-park]").forEach((b) =>
      (b.onclick = () => put(id, { on_park: b.dataset.park })));
    box.querySelectorAll("[data-lbp]").forEach((b) => (b.onclick = () => {
      const on = new Set(l.breakpoints);
      on.has(b.dataset.lbp) ? on.delete(b.dataset.lbp) : on.add(b.dataset.lbp);
      put(id, { breakpoints: [...on] });
    }));
    const lim = box.querySelector(".lim");
    if (lim) lim.onchange = () => put(id, { limit: +lim.value });
    const ahead = box.querySelector(".ahead");
    if (ahead) ahead.onchange = () => put(id, { ahead: +ahead.value });
    const topic = box.querySelector(".topic");
    const add = () => {
      const t = topic.value.trim();
      if (t) { topic.value = ""; put(id, { add_topics: [t] }); }
    };
    if (topic) topic.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } };

    box.querySelectorAll("[data-loopact]").forEach((b) => (b.onclick = async () => {
      const what = b.dataset.loopact;
      if (what === "add") return add();
      if (what === "edit") return editLoop(l);
      if (what === "start") {
        try { await api(`/api/loops/${l.id}/start`, { method: "POST" }); say(lab("js.loop.started")); }
        catch (e) { say(e.message, true); }
        return loadLoops(true);
      }
      if (what === "drop") {
        // the same two-press question the runs list asks, for the same reason: this
        // deletes a plan somebody spent time queueing
        if (!armed.has(l.id))
          return armForget(b, l.id, "js.loop.forget", "js.loop.forget-sure");
        clearTimeout(armed.get(l.id));
        armed.delete(l.id);
        try { await api(`/api/loops/${l.id}`, { method: "DELETE" }); say(lab("js.deleted")); }
        catch (e) { say(e.message, true); }
        return loadLoops(true);
      }
      // Stopping the loop's own video, which is the only thing that ends a stopping
      // loop before that video does. Two presses, like every other button here that
      // throws work away: what is dropped is a video already half paid for.
      if (what === "stopnow") {
        if (!armed.has(`now:${l.id}`))
          return armForget(b, `now:${l.id}`, "js.loop.stop-now", "js.loop.stop-now-sure");
        clearTimeout(armed.get(`now:${l.id}`));
        armed.delete(`now:${l.id}`);
        try {
          await api(`/api/loops/${l.id}/stop`, { method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ now: true }) });
          say(lab("js.loop.dropped-the-video"));
        } catch (e) { say(e.message, true); }
        loadRuns();
        return loadLoops(true);
      }
      if (what === "all") {
        ui.sel = ui.sel.size === items.length ? new Set() : new Set(items.map((i) => i.id));
        return renderLoops();
      }
      if (what === "ai") {
        b.disabled = true;
        say(lab("js.ai-working"));
        try {
          await api(`/api/loops/${id}/queue/ai`, { method: "POST",
            headers: { "content-type": "application/json" }, body: JSON.stringify({ n: 3 }) });
          say(lab("js.q.asked"));
        } catch (e) { say(e.message, true); }
        b.disabled = false;
        return loadLoops(true);
      }
      try { await api(`/api/loops/${id}/stop`, { method: "POST" }); } catch (e) { say(e.message, true); }
      loadLoops(true);
    }));

    // ---- the rows ----
    box.querySelectorAll(".queue > li").forEach((li, i) => {
      const item = items[i];
      if (!item) return;
      const move = (to) => {
        if (to < 0 || to >= items.length) return;
        const [it] = l.topics.splice(i, 1);
        l.topics.splice(to, 0, it);
        putQueue(l);
      };
      li.querySelector(".qup").onclick = () => move(i - 1);
      li.querySelector(".qdown").onclick = () => move(i + 1);
      li.querySelector(".qdel").onclick = () => { l.topics.splice(i, 1); putQueue(l); };
      li.querySelector(".qpick").onchange = (e) => {
        e.target.checked ? ui.sel.add(item.id) : ui.sel.delete(item.id);
        renderLoops();
      };
      li.querySelector(".qset").onclick = () => {
        ui.open.has(item.id) ? ui.open.delete(item.id) : ui.open.add(item.id);
        renderLoops();
      };
      const tp = li.querySelector(".qtopic");
      tp.onchange = () => {
        if (tp.value === item.topic) return;
        item.topic = tp.value;
        putQueue(l);
      };
      tp.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); tp.blur(); } };

      // Dragging is the gesture the list's shape promises; the arrows are the one it
      // keeps for a phone and for a keyboard. Only the handle starts a drag, so a row
      // can still be selected and its topic still typed in.
      const handle = li.querySelector(".handle");
      handle.ondragstart = (e) => {
        dragQueue = { loop: id, from: i };
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", String(i));
        li.classList.add("dragging");
      };
      handle.ondragend = () => { li.classList.remove("dragging"); dragQueue = null; };
      li.ondragover = (e) => {
        if (!dragQueue || dragQueue.loop !== id) return;
        e.preventDefault();
        li.classList.add("over");
      };
      li.ondragleave = () => li.classList.remove("over");
      li.ondrop = (e) => {
        li.classList.remove("over");
        if (!dragQueue || dragQueue.loop !== id) return;
        e.preventDefault();
        const from = dragQueue.from;
        dragQueue = null;
        if (from === i) return;
        const [it] = l.topics.splice(from, 1);
        l.topics.splice(i, 0, it);
        putQueue(l);
      };

      const edit = li.querySelector(".qedit");
      if (!edit) return;
      const more = li.querySelector(".qmore");
      if (more) more.onclick = () => {
        ui.more.has(item.id) ? ui.more.delete(item.id) : ui.more.add(item.id);
        renderLoops();
      };
      bindOv(edit, specs, (spec, value) => {
        item.over = { ...(item.over || {}), [spec.f]: value };
        putQueue(l);
      }, (spec) => {
        const over = { ...(item.over || {}) };
        delete over[spec.f];
        item.over = over;
        putQueue(l);
      });
    });

    // ---- the bulk form ----
    const bulk = box.querySelector(".bulk");
    if (!bulk) return;
    const tick = (f, on) => {
      ui.pick = ui.pick.filter((x) => x !== f);
      if (on) ui.pick.push(f);
      renderLoops();  // a ticked field moves to the top, so this has to redraw
    };
    bulk.querySelectorAll(".ovrow").forEach((row) => {
      const f = row.dataset.f;
      row.querySelector(".bpick").onchange = (e) => tick(f, e.target.checked);
    });
    // Touching a control is what wanting the field means, so it ticks it. Redrawing on
    // `change` and not on `input` is what keeps that from happening under the hand: a
    // text field commits on blur and a slider on release, both after the gesture.
    bindOv(bulk, specs, (spec, value) => {
      ui.vals[spec.f] = value;
      tick(spec.f, true);
    }, null);
    if (bulk.querySelector(".bmore"))
      bulk.querySelector(".bmore").onclick = () => { ui.bmore = !ui.bmore; renderLoops(); };
    const picked = () => ui.pick.map((f) => specs.find((s) => s.f === f)).filter(Boolean);
    bulk.querySelector(".bset").onclick = () => {
      const set = {};
      for (const spec of picked())
        set[spec.f] = ovRead(spec, bulk.querySelector(`.ovrow[data-f="${spec.f}"] [data-ov]`));
      ui.pick = [];
      ui.vals = {};
      patch(l, { set });
    };
    bulk.querySelector(".bclear").onclick = () => {
      const fields = ui.pick.slice();
      ui.pick = [];
      ui.vals = {};
      patch(l, { clear: fields });
    };
    bulk.querySelector(".bdel").onclick = () => {
      l.topics = l.topics.filter((it) => !ui.sel.has(it.id));
      ui.sel = new Set();
      putQueue(l);
    };
    // A copy carries the settings and not the id: it is another video like this one,
    // not another name for the same one.
    bulk.querySelector(".bdup").onclick = () => {
      const out = [];
      for (const it of l.topics) {
        out.push(it);
        if (ui.sel.has(it.id)) out.push({ topic: it.topic, over: { ...(it.over || {}) }, by: it.by });
      }
      l.topics = out;
      ui.sel = new Set();
      putQueue(l);
    };
  });
}

// Wire one block of override controls. `set` is called with the field and its new value
// whenever a control is touched and `clear` when ↺ gives the field back to the loop;
// both are null in the bulk bar, where nothing is committed until the button is.
function bindOv(root, specs, set, clear) {
  if (!root) return;
  root.querySelectorAll("[data-ov]").forEach((el) => {
    const spec = specs.find((s) => s.f === el.dataset.ov);
    if (!spec) return;
    const commit = () => {
      el.classList.remove("inherit");
      if (set) set(spec, ovRead(spec, el));
    };
    if (spec.kind === "chips") {
      el.querySelectorAll("[data-c]").forEach((b) => (b.onclick = () => {
        b.classList.toggle("on");
        commit();
      }));
      return;
    }
    if (spec.kind === "fx" || spec.kind === "range") {
      el.querySelectorAll("input[type=range]").forEach((r) => {
        const dose = (r.closest(".slider") || el).querySelector(".dose");
        r.oninput = () => { if (dose) dose.textContent = r.value; };
        r.onchange = commit;
      });
      return;
    }
    el.onchange = commit;
  });
  root.querySelectorAll(".ovrow .undo").forEach((b) => (b.onclick = () => {
    const spec = specs.find((s) => s.f === b.closest(".ovrow").dataset.f);
    if (spec && clear) clear(spec);
  }));
}

// ---------------------------------------------------------------- runs
const streams = new Map();

async function loadRuns() {
  loadLoops();
  const runs = await api("/api/runs");
  // The list redraws on every event, and a half-asked question cannot survive the row
  // it was asked on being replaced — leaving it armed would turn the next click on a
  // fresh-looking button into a delete.
  armed.forEach(clearTimeout);
  armed.clear();
  $("#runs").innerHTML = runs.map((r) => `
    <div class="run" data-id="${r.id}">
      <div class="top">
        <span class="st st-${r.status}">${statusWord(r)}</span>
        <b>${esc(r.title)}</b>
        <span class="grow"></span>
        ${actions(r).map((a) =>
          `<button data-act="${a.act}" data-id="${r.id}"${a.sure ? ` data-sure="${esc(a.sure)}"` : ""
           }${a.title ? ` title="${esc(a.title)}"` : ""
           } class="${a.primary ? "primary" : "ghost"}">${esc(a.label)}</button>`).join("")}
      </div>
      ${progressBar(r)}
      ${waitingFor(r)}
      <div class="log" id="log-${r.id}"></div>
    </div>`).join("") || `<p class="empty">${lab("js.no-runs-yet")}</p>`;
  const byId = Object.fromEntries(runs.map((r) => [r.id, r]));
  document.querySelectorAll("#runs [data-act]").forEach((b) => {
    b.onclick = () => act(b.dataset.act, byId[b.dataset.id], b, b.dataset.sure);
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
  // The montage room, on any parked fandom video whose lines have been voiced (see
  // `runs.parked`). It is not tied to the picture breakpoint even though that is where
  // a by-hand run stops: a run parked for pictures has the same timeline and the same
  // reasons to look at it, and being able to see what you are about to be asked FOR is
  // most of what makes the asking answerable.
  if (p.montage) out.push({ act: "montage", label: lab("js.montage"),
                            primary: !p.asks && !p.review_stage });
  // …and the same room on a video that is already CUT, which is a different gesture
  // and therefore a different word: pressing it takes the render off so the timeline
  // can be edited again (see `montage_api.reopen`), which un-finishes the run. The one
  // that made it does not matter — the cards a matcher chose are exactly what you come
  // back to overrule, and you can only see that it chose badly in the finished video.
  if (p.recut) out.push({ act: "recut", label: lab("js.recut"), title: lab("js.recut-why"),
                          primary: !p.asks && !p.review_stage && !p.montage });
  if (p.asks || p.review_stage) out.push({ act: "resume", label: lab("js.go-on") });
  else if (r.status === "failed") out.push({ act: "resume", label: lab("js.try-again") });
  else if (r.status !== "done") out.push({ act: "resume", label: lab("js.resume") });
  if (p.video) out.push({ act: "video", label: lab("js.watch-the-video") });
  // Last, and on every run that is not moving: this deletes the folder, video and all.
  //
  // It was briefly hidden on a PARKED run, after one went to `rmtree` from the row that
  // was asking to be reviewed — but hiding it was the wrong repair. A parked run is
  // exactly the kind somebody wants rid of (it is the one that came out wrong, which is
  // why it is sitting there), and taking the button away left no way to do it at all.
  // What was wrong was the ease, not the existence: an unremarkable button beside the
  // one that answers the question. So it stays, and the SECOND press is where the
  // parked case is spelled out — `js.forget-parked-sure` says what is about to be
  // thrown away instead of asking a generic "sure?".
  out.push({ act: "forget", label: lab("js.forget-run"),
             sure: p.asks || p.review_stage ? "js.forget-parked-sure" : "js.forget-run-sure" });
  return out;
}

// What a running video is DOING, over a bar — the one thing the terminal always had
// and the browser did not. The tally was already on the wire (`Run.progress`); what
// was missing was anywhere to put it and any word for what was counting.
//
// The bar is only drawn while the run is moving. A settled run's last tally is not
// information — it is the tally of whatever stage happened to be running when it
// stopped, and a full green bar over a failed run is a lie told by furniture.
//
// A stage with no countable inside it (the script, the metadata) reports nothing, so
// the bar has no width to take. It still gets a NAME and an indeterminate stripe: the
// question the whole strip answers is "is anything happening", and "writing the
// script, no idea how far" is a real answer to it where an empty row is not.
function progressBar(r) {
  if (!live(r) || !r.stage) return "";
  const p = r.progress;
  const known = p && p.total > 0;
  const pct = known ? Math.min(100, Math.round((p.done / p.total) * 100)) : 0;
  // "Script (raw text the LLM wrote)" is a breakpoint's caption; the bar wants the
  // name alone, which is the half before the parenthesis — the terminal cuts it the
  // same way (see tui ProgressScreen._set_stage).
  const name = lab("bp.stage." + r.stage, word(r.stage)).split(" (")[0];
  // which of the batch, but only when there IS a batch — "video 0 · script" on a
  // single-video run is a column heading pretending to be information
  const where = r.stage_video >= 0 && r.count > 1
    ? `${lab("col.video", "video")} ${r.stage_video} · ` : "";
  const tally = known ? `${p.done}/${p.total} ${lab("unit." + p.unit, p.unit)}` : "";
  return `<div class="prog" data-prog="${r.id}">
    <div class="ptop"><span class="pstage">${esc(where)}<b>${esc(name)}</b></span>
      <span class="grow"></span><span class="ptally">${esc(tally)}</span></div>
    <div class="pbar${known ? "" : " idle"}"><i style="width:${known ? pct : 100}%"></i></div>
  </div>`;
}

// The same strip, updated in place. A redraw of the whole list would take the caret
// out of anything being typed in a card above it and restart every log's scroll, so a
// tick moves the two things that changed and touches nothing else.
function paintProgress(id, ev) {
  const box = document.querySelector(`[data-prog="${id}"]`);
  if (!box) return;
  const known = ev.total > 0;
  const bar = box.querySelector(".pbar");
  bar.classList.toggle("idle", !known);
  bar.firstElementChild.style.width =
    (known ? Math.min(100, Math.round((ev.done / ev.total) * 100)) : 100) + "%";
  box.querySelector(".ptally").textContent =
    known ? `${ev.done}/${ev.total} ${lab("unit." + ev.status, ev.status)}` : "";
}

function waitingFor(r) {
  const p = r.parked || {};
  const bits = [];
  if (p.asks) bits.push(`${lab("js.pictures-missing")} ${p.asks}`);
  if (p.review_stage) bits.push(`${lab("js.sitting-at")}${word(p.review_stage)}${lab("js.waiting-on-you")}`);
  if (!bits.length && r.message) bits.push(lab(r.message, r.message));
  return bits.length ? `<div class="why">${esc(bits.join(" · "))}</div>` : "";
}

// Deleting a run deletes its folder, and a finished video is in there. So the button
// asks: the first press turns it into the question, a second press within five seconds
// answers it, and walking away answers "no". A modal would do the same job by taking
// the whole screen hostage over one row.
const armed = new Map();  // run id -> timer that disarms it

// The wording is a parameter because the two things this guards are not the same
// deletion: a run takes its folder and the video in it, a loop takes only its plan.
function armForget(btn, id, word = "js.forget-run", sure = "js.forget-run-sure") {
  clearTimeout(armed.get(id));
  armed.set(id, setTimeout(() => {
    armed.delete(id);
    btn.textContent = lab(word);
    btn.classList.remove("danger");
  }, 5000));
  btn.textContent = lab(sure);
  btn.classList.add("danger");
}

async function act(what, r, btn, sure) {
  if (!r) return;
  sure = sure || "js.forget-run-sure";
  try {
    if (what === "stop") { await api(`/api/runs/${r.id}/stop`, { method: "POST" }); loadRuns(); }
    else if (what === "forget") {
      if (!armed.has(r.id)) return armForget(btn, r.id, "js.forget-run", sure);
      clearTimeout(armed.get(r.id));
      armed.delete(r.id);
      await api(`/api/runs/${r.id}`, { method: "DELETE" });
      say(`${r.title} ${lab("js.deleted")}`);
      loadRuns();
    }
    else if (what === "asks") await openAsks(r.id, r.title);
    else if (what === "montage") await openMontage(r.id, r.title);
    else if (what === "recut") {
      // Un-finish it first, then walk straight into the room: two requests, because
      // taking the render off a cut video is a change to the run and opening a screen
      // is not. The old file stays on disk until the new cut overwrites it, which is
      // what makes «посмотреть ролик» still worth pressing while you work.
      await api(`/api/runs/${r.id}/montage/reopen`, { method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ video: 0 }) });
      say(lab("js.recut-opened"));
      await openMontage(r.id, r.title);
      loadRuns();
    }
    else if (what === "review") await openReview(r.id, r.title);
    else if (what === "video") window.open(tokd(`/api/runs/${r.id}/video`), "_blank");
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
  const es = new EventSource(tokd(`/api/runs/${id}/events`));
  const state = { es, lines: [] };
  streams.set(id, state);
  es.onmessage = (m) => {
    const e = JSON.parse(m.data);
    // A progress tick is the bar, not the log. It arrives on the same stream because
    // there is only one stream, and it carries a stage name of its own so that telling
    // the two apart is a comparison rather than a guess at the message.
    if (e.stage === "progress") return paintProgress(id, e);
    const line = `${e.video >= 0 ? "[" + e.video + "] " : ""}${e.stage} ${e.status} ${e.message}`.trim();
    state.lines.push(line);
    appendLog(id, line);
    // A stage starting renames the bar and empties it, so the tally of the stage that
    // just finished never sits under the name of the one that just began.
    if (e.status === "start" && e.video >= 0) loadRuns();
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
