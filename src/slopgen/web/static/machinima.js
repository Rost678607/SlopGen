// Machinima: the block editor in the montage room, and a world's Minecraft tab.
//
// A block is a run of the video PLAYED in the world's Minecraft build and filmed under
// its own words. Its direction file is the thing edited here — scenes, cast, crowds,
// actions, shots — as a form over the JSON, never as JSON: every name in it comes from
// a dropdown filled from the world's markup, so a direction cannot name a point that
// does not exist. Every key is anchored to a WORD of the narration (or between two, or
// at a second), and the words are listed with their seconds right above the form.
//
// Playing and filming need the game and take minutes, so they run on the server in a
// thread of their own and this page polls; a draft (no shaders, half size) is for
// looking at while directing, «compile» makes the final with shaders.
"use strict";

// One closure: every script of this page shares one global scope, and names like `sel`
// or `render` are already somebody else's there. Only the three entry points leave it.
(() => {

let MC = null; // {run, video, id, block, vocab, dir, scene, poll}

const mcq = (s) => document.querySelector(s);
const mcLab = (k, fb) => lab("js.mc." + k, fb);

// ---------------------------------------------------------------- reading/writing paths

function getAt(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? o : o[/^\d+$/.test(k) ? +k : k]), obj);
}
function setAt(obj, path, value) {
  const ks = path.split(".");
  let o = obj;
  for (let n = 0; n < ks.length - 1; n++) {
    const k = /^\d+$/.test(ks[n]) ? +ks[n] : ks[n];
    if (o[k] == null) o[k] = /^\d+$/.test(ks[n + 1]) ? [] : {};
    o = o[k];
  }
  const last = /^\d+$/.test(ks[ks.length - 1]) ? +ks[ks.length - 1] : ks[ks.length - 1];
  if (value === undefined) delete o[last]; else o[last] = value;
}

// ---------------------------------------------------------------- the vocabulary

const placeOf = (name) => (MC.vocab.places || []).find((p) => p.name === name) || null;
const scene = () => MC.dir.scenes[MC.scene];
const aliases = () => (scene().cast || []).map((c) => c.as);
const faces = () => (MC.vocab.characters || []).flatMap((c) => c.faces.map((f) => `${c.name}/${f.name}`));

function opts(values, cur, { blank = false, labels = null } = {}) {
  const vs = blank ? ["", ...values] : values;
  const has = vs.includes(cur);
  return (has || cur == null || cur === "" ? "" : `<option value="${esc(cur)}" selected>⚠ ${esc(cur)}</option>`) +
    vs.map((v, i) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(labels ? labels[i] : (v || "—"))}</option>`).join("");
}
function targets(kinds = ["actor", "point", "area"]) {
  const p = placeOf(scene().place) || { points: [], areas: [], lines: [] };
  return [
    ...(kinds.includes("actor") ? aliases() : []),
    ...(kinds.includes("point") ? p.points.map((x) => x.name) : []),
    ...(kinds.includes("area") ? p.areas.map((x) => x.name) : []),
    ...(kinds.includes("line") ? p.lines.map((x) => x.name) : []),
  ];
}
const sel = (path, values, cur, o = {}) =>
  `<select data-p="${path}"${o.cls ? ` class="${o.cls}"` : ""}>${opts(values, cur, o)}</select>`;
const num = (path, cur, step = 0.1, w = 60) =>
  `<input type="number" data-p="${path}" data-t="num" step="${step}" value="${cur ?? ""}" style="width:${w}px">`;
const txt = (path, cur, ph = "", w = 90) =>
  `<input data-p="${path}" value="${esc(cur ?? "")}" placeholder="${esc(ph)}" style="width:${w}px">`;

// ---------------------------------------------------------------- anchors

// The words of the block, as the anchors name them.
const wordKey = (w) => `${w.scene}:${w.i}`;
const wordLabels = () => MC.block.words.map((w) => `${w.t.toFixed(1)}s · ${w.text}`);
const wordKeys = () => MC.block.words.map(wordKey);
const refOf = (key) => {
  const w = MC.block.words.find((x) => wordKey(x) === key);
  return w ? { scene: w.scene, i: w.i, text: w.text } : null;
};
const keyOfRef = (r) => (r ? `${r.scene}:${r.i}` : "");

function anchorHTML(path) {
  const a = getAt(MC.dir, path) || {};
  const form = a.between ? "between" : (a.sec != null ? "sec" : "word");
  const modes = `<select data-anchor-mode="${path}">${opts(["word", "between", "sec"], form,
    { labels: [mcLab("on-word", "на слове"), mcLab("between", "между словами"), mcLab("sec", "секунды")] })}</select>`;
  let body = "";
  if (form === "word") {
    body = `<select data-anchor-word="${path}.word">${opts(wordKeys(), keyOfRef(a.word), { labels: wordLabels() })}</select>
      ${num(path + ".offset", a.offset ?? 0, 0.05, 54)}<span class="dim">с</span>`;
  } else if (form === "between") {
    const [x, y] = a.between || [null, null];
    body = `<select data-anchor-word="${path}.between.0">${opts(wordKeys(), keyOfRef(x), { labels: wordLabels() })}</select>
      <input type="range" min="0" max="1" step="0.05" data-p="${path}.frac" data-t="num" value="${a.frac ?? 0.5}" title="${a.frac ?? 0.5}">
      <select data-anchor-word="${path}.between.1">${opts(wordKeys(), keyOfRef(y), { labels: wordLabels() })}</select>`;
  } else {
    body = `${num(path + ".sec", a.sec ?? 0, 0.1, 64)}<span class="dim">с</span>`;
  }
  return `<span class="mc-anchor">${modes}${body}</span>`;
}

function setAnchorMode(path, mode) {
  const first = MC.block.words[0];
  const ref = first ? { scene: first.scene, i: first.i, text: first.text } : null;
  if (mode === "word") setAt(MC.dir, path, { word: ref, offset: 0 });
  else if (mode === "between") setAt(MC.dir, path, { between: [ref, ref], frac: 0.5 });
  else setAt(MC.dir, path, { sec: 0 });
}

// ---------------------------------------------------------------- the form

function castHTML(sc, s) {
  const p = placeOf(sc.place) || { points: [], areas: [] };
  const where = [...p.points.map((x) => x.name), ...p.areas.map((x) => x.name)];
  return (sc.cast || []).map((c, k) => {
    const b = `scenes.${s}.cast.${k}`;
    return `<div class="mc-row">
      ${txt(b + ".as", c.as, "a", 44)}
      ${sel(b + ".actor", faces(), c.actor)}
      <span class="dim">${mcLab("at", "на")}</span>${sel(b + ".at", where, c.at)}
      <span class="dim">${mcLab("facing", "смотрит")}</span>${sel(b + ".facing", targets(), c.facing || "", { blank: true })}
      <span class="dim">${mcLab("holds", "в руке")}</span>${sel(b + ".hold", (MC.vocab.props || []).map((x) => x.name), c.hold || "", { blank: true })}
      <button class="ghost" data-del="${b}">✕</button></div>`;
  }).join("");
}

function crowdHTML(sc, s) {
  const p = placeOf(sc.place) || { areas: [], lines: [] };
  const refs = (MC.vocab.characters || []).flatMap((c) => [c.name, ...c.faces.map((f) => `${c.name}/${f.name}`)]);
  return (sc.crowd || []).map((cr, k) => {
    const b = `scenes.${s}.crowd.${k}`;
    const mix = Object.entries(cr.mix || {}).map(([ref, share], m) => `
      <span class="mc-mix">${sel(`${b}.mixkey.${m}`, refs, ref, { cls: "mixkey" })}
        <input type="number" min="0" max="100" step="5" data-mix="${b}" data-ref="${esc(ref)}" value="${Math.round(share * 100)}" style="width:52px">%
        <button class="ghost" data-mix-del="${b}" data-ref="${esc(ref)}">✕</button></span>`).join("");
    return `<div class="mc-row mc-crowd">
      <span class="dim">${mcLab("in", "в зоне")}</span>${sel(b + ".in", p.areas.map((x) => x.name), cr.in)}
      <span class="dim">${mcLab("density", "плотность")}</span>
      <input type="range" min="0" max="1" step="0.05" data-p="${b}.density" data-t="num" value="${cr.density ?? 0.3}">
      <b class="dim">${(cr.density ?? 0.3).toFixed(2)}</b>
      ${sel(b + ".do", MC.vocab.behaviours, cr.do || "stand", { labels: MC.vocab.behaviours.map((x) => mcLab("do." + x, x)) })}
      ${["queue", "flow"].includes(cr.do) ? `<span class="dim">${mcLab("along", "по линии")}</span>${sel(b + ".along", p.lines.map((x) => x.name), cr.along || "")}` : ""}
      ${["watch", "queue"].includes(cr.do) ? `<span class="dim">${mcLab("toward", "к")}</span>${sel(b + ".toward", targets(), cr.toward || "", { blank: true })}` : ""}
      <button class="ghost" data-del="${b}">✕</button>
      <div class="mc-mixes"><span class="dim">${mcLab("mix", "состав")}</span>${mix}
        <button class="ghost" data-mix-add="${b}">＋</button>
        <span class="dim">${mcLab("exclude", "кроме")}</span>${txt(b + ".exclude_text", (cr.exclude || []).join(", "), "Персонаж/лицо, …", 180)}</div>
    </div>`;
  }).join("");
}

function actionFields(a, b) {
  const need = (MC.vocab.verbs || {})[a.do] || [];
  const out = [];
  const who = Array.isArray(a.who) ? a.who.join(", ") : (a.who || "");
  if (a.do === "dialogue") out.push(`<span class="dim">${mcLab("who", "кто")}</span>${txt(b + ".who_text", who, "a, b", 80)}`);
  else out.push(sel(b + ".who", aliases(), who));
  if (["walk", "run", "turn", "look"].includes(a.do)) out.push(`→ ${sel(b + ".to", targets(), a.to || "")}`);
  if (["sit", "use", "appear", "leave", "particles"].includes(a.do)) out.push(`${mcLab("on", "у")} ${sel(b + ".on", targets(["point"]), a.on || "", { blank: true })}`);
  if (a.do === "gesture") out.push(sel(b + ".name", MC.vocab.gestures.map((g) => g.name), a.name || "", { labels: MC.vocab.gestures.map((g) => `${g.name} — ${g.description}`) }));
  if (a.do === "pose") out.push(sel(b + ".name", MC.vocab.poses.map((g) => g.name), a.name || "", { labels: MC.vocab.poses.map((g) => `${g.name} — ${g.description}`) }));
  if (a.do === "particles") out.push(txt(b + ".name", a.name, "smoke", 80));
  if (["hold", "wear"].includes(a.do)) out.push(sel(b + ".item", (MC.vocab.props || []).map((x) => x.name), a.item || ""));
  if (a.do === "hold") out.push(sel(b + ".hand", ["main", "off"], a.hand || "main"));
  if (a.do === "wear") out.push(sel(b + ".slot", ["head", "chest", "legs", "feet"], a.slot || "head"));
  if (["gesture", "pose", "stand", "sit"].includes(a.do)) out.push(sel(b + ".ease", MC.vocab.eases, a.ease || "smooth", { labels: MC.vocab.eases.map((x) => mcLab("ease." + x, x)) }));
  if (["look", "gesture", "particles", "dialogue"].includes(a.do)) {
    out.push(a.until
      ? `<span class="dim">${mcLab("until", "до")}</span>${anchorHTML(b + ".until")}<button class="ghost" data-del="${b}.until">✕</button>`
      : `<button class="ghost" data-until="${b}">${mcLab("add-until", "＋ до…")}</button>`);
  }
  if (a.do === "dialogue") {
    const turns = (a.turns || []).map((t, k) => `<span class="mc-turn">${sel(`${b}.turns.${k}.speaker`, Array.isArray(a.who) ? a.who : [], t.speaker)}
        ${t.until ? `<span class="dim">${mcLab("until", "до")}</span>${anchorHTML(`${b}.turns.${k}.until`)}` : `<button class="ghost" data-until="${b}.turns.${k}">${mcLab("add-until", "＋ до…")}</button>`}
        <button class="ghost" data-del="${b}.turns.${k}">✕</button></span>`).join("");
    out.push(`<div class="mc-turns"><span class="dim">${mcLab("turns", "реплики")}</span>${turns}
      <button class="ghost" data-turn-add="${b}">＋</button></div>`);
  }
  return out.join(" ");
}

function actionsHTML(sc, s) {
  return (sc.action || []).map((a, k) => {
    const b = `scenes.${s}.action.${k}`;
    return `<div class="mc-row">${anchorHTML(b + ".at")}
      ${sel(b + ".do", Object.keys(MC.vocab.verbs || {}), a.do, { cls: "mc-verb", labels: Object.keys(MC.vocab.verbs || {}).map((v) => mcLab("verb." + v, v)) })}
      ${actionFields(a, b)}
      <button class="ghost" data-del="${b}">✕</button></div>`;
  }).join("");
}

const pairOf = (v, d) => (Array.isArray(v) ? v : [v ?? d, v ?? d]);
function rangePair(path, value, min, max, step, unit) {
  const [a, b] = pairOf(value, min);
  return `<span class="mc-pair"><input type="range" min="${min}" max="${max}" step="${step}" data-pair="${path}" data-k="0" value="${a}">
    <b>${(+a).toFixed(1)}${unit}</b>→<input type="range" min="${min}" max="${max}" step="${step}" data-pair="${path}" data-k="1" value="${b}">
    <b>${(+b).toFixed(1)}${unit}</b></span>`;
}

function shotKind(c) {
  if (c.fixed) return "fixed";
  if (c.rail) return "rail";
  if (c.style === "dialogue") return "dialogue";
  return "on";
}

function shotsHTML(sc, s) {
  const p = placeOf(sc.place) || { cameras: [] };
  return (sc.shots || []).map((sh, k) => {
    const b = `scenes.${s}.shots.${k}.camera`;
    const c = sh.camera || {};
    const kind = shotKind(c);
    let f = "";
    if (kind === "on") {
      f = `${sel(b + ".on", aliases(), c.on || "")}
        ${sel(b + ".side", MC.vocab.sides, c.side || "front", { labels: MC.vocab.sides.map((x) => mcLab("side." + x, x)) })}
        <span class="dim">${mcLab("distance", "дистанция")}</span>${rangePair(b + ".distance", c.distance ?? 3, 0.5, 16, 0.1, " бл")}
        <span class="dim">${mcLab("height", "выше глаз")}</span>${num(b + ".height", c.height ?? 0, 0.1, 50)}
        ${c.side === "orbit" ? `<span class="dim">${mcLab("orbit", "облёт")}</span>${num(b + ".orbit", c.orbit ?? 90, 5, 54)}°` : ""}`;
    } else if (kind === "dialogue") {
      const [x, y] = c.pair || ["", ""];
      f = `${sel(b + ".pair.0", aliases(), x)} ↔ ${sel(b + ".pair.1", aliases(), y)}
        <span class="dim">${mcLab("distance", "дистанция")}</span>${rangePair(b + ".distance", c.distance ?? 1.8, 0.5, 8, 0.1, " бл")}`;
    } else if (kind === "fixed") {
      f = `${sel(b + ".fixed", p.cameras.filter((x) => x.kind === "fixed").map((x) => x.name), c.fixed || "")}
        <span class="dim">${mcLab("aim", "на")}</span>${sel(b + ".aim", targets(), c.aim || "", { blank: true })}
        <span class="dim">${mcLab("zoom", "зум")}</span>${rangePair(b + ".fov", c.fov ?? 70, 15, 110, 1, "°")}`;
    } else {
      f = `${sel(b + ".rail", p.cameras.filter((x) => x.kind === "rail").map((x) => x.name), c.rail || "")}
        <span class="dim">${mcLab("travel", "проезд")}</span>${rangePair(b + ".travel", c.travel ?? [0, 1], 0, 1, 0.05, "")}
        <span class="dim">${mcLab("aim", "на")}</span>${sel(b + ".aim", targets(), c.aim || "", { blank: true })}`;
    }
    return `<div class="mc-row">${anchorHTML(`scenes.${s}.shots.${k}.at`)}
      <select data-shot-kind="${b}">${opts(["on", "dialogue", "fixed", "rail"], kind,
        { labels: [mcLab("cam.on", "на персонаже"), mcLab("cam.dialogue", "восьмёрка"), mcLab("cam.fixed", "стационарная"), mcLab("cam.rail", "на рельсе")] })}</select>
      ${f}
      ${sel(b + ".ease", MC.vocab.eases, c.ease || "smooth", { labels: MC.vocab.eases.map((x) => mcLab("ease." + x, x)) })}
      <button class="ghost" data-del="scenes.${s}.shots.${k}">✕</button></div>`;
  }).join("");
}

function render() {
  const b = MC.block;
  const d = MC.dir;
  const s = MC.scene;
  const sc = d.scenes[s];
  const work = b.work && b.work.state === "running"
    ? `<span class="busy">${mcLab("work." + b.work.what, b.work.what)}…</span>`
    : (b.work && b.work.state === "error" ? `<span class="bad">${esc(b.work.message)}</span>` : "");
  const clip = MC.view === "draft" ? b.draft : (b.final || b.draft);
  mcq("#mc-sheet").innerHTML = `
    <div class="row"><b>${mcLab("title", "Машинима")} ${esc(b.id)}</b>
      <span class="dim">${b.start.toFixed(1)}–${(b.start + b.duration).toFixed(1)}с</span>
      ${b.stale ? `<span class="warn">${mcLab("stale", "голос сдвинулся — переиграть")}</span>` : ""}
      ${work}<span class="grow"></span>
      <span class="dim">${MC.game ? "● " + mcLab("game-on", "игра подключена") : "○ " + mcLab("game-off", "игра не запущена")}</span>
      <button class="ghost" id="mc-direct">${mcLab("direct", "режиссёр заново")}</button>
      <button class="ghost" id="mc-drop">${mcLab("drop", "убрать блок")}</button>
      <button class="primary" id="mc-close">${mcLab("close", "готово")}</button></div>
    <div class="mc-cols">
      <div class="mc-left">
        ${clip ? `<video id="mc-video" src="${tokd(clip)}" controls loop playsinline></video>`
               : `<div class="mc-noclip">${mcLab("noclip", "клипа ещё нет — сыграй черновик")}</div>`}
        <div class="row">
          ${b.draft && b.final ? `<button class="ghost" id="mc-view">${MC.view === "draft" ? mcLab("show-final", "чистовик") : mcLab("show-draft", "черновик")}</button>` : ""}
          <span class="grow"></span>
          <button id="mc-draft">${mcLab("draft", "черновик")}</button>
          <button class="primary" id="mc-final">${mcLab("compile", "скомпилировать")}</button></div>
        <p class="said">${b.words.map((w) => `<span class="w" title="${w.t.toFixed(2)}с">${esc(w.text)}</span>`).join(" ")}</p>
        ${b.why ? `<p class="dim">${mcLab("why", "задумано:")} ${esc(b.why)}</p>` : ""}
        ${(b.issues || []).length ? `<ul class="mc-issues">${b.issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : ""}
      </div>
      <div class="mc-right">
        <div class="chips">${d.scenes.map((x, k) => `<button data-scene="${k}"${k === s ? ' class="on"' : ""}>${k + 1} ${esc(x.place || "?")}</button>`).join("")}
          <button class="ghost" id="mc-scene-add">＋ ${mcLab("scene", "сцена")}</button>
          ${d.scenes.length > 1 ? `<button class="ghost" id="mc-scene-del">✕ ${mcLab("scene", "сцена")}</button>` : ""}</div>
        ${s > 0 ? `<div class="mc-row"><span class="dim">${mcLab("cut-at", "склейка на")}</span>${anchorHTML(`scenes.${s}.at`)}</div>` : ""}
        <div class="mc-row">
          <span class="dim">${mcLab("place", "место")}</span>${sel(`scenes.${s}.place`, MC.vocab.places.map((p) => p.name), sc.place)}
          <span class="dim">${mcLab("time", "время")}</span>${sel(`scenes.${s}.time`, MC.vocab.times, sc.time || "day", { labels: MC.vocab.times.map((x) => mcLab("time." + x, x)) })}
          <span class="dim">${mcLab("weather", "погода")}</span>${sel(`scenes.${s}.weather`, MC.vocab.weathers, sc.weather || "clear", { labels: MC.vocab.weathers.map((x) => mcLab("weather." + x, x)) })}
          ${placeOf(sc.place) && placeOf(sc.place).shot ? `<img class="mc-thumb" src="${tokd(placeOf(sc.place).shot)}">` : ""}</div>
        <h4>${mcLab("cast", "актёры")} <button class="ghost" data-add="cast">＋</button></h4>${castHTML(sc, s)}
        <h4>${mcLab("crowd", "массовка")} <button class="ghost" data-add="crowd">＋</button></h4>${crowdHTML(sc, s)}
        <h4>${mcLab("actions", "действия")} <button class="ghost" data-add="action">＋</button></h4>${actionsHTML(sc, s)}
        <h4>${mcLab("shots", "планы")} <button class="ghost" data-add="shots">＋</button></h4>${shotsHTML(sc, s)}
        <div class="row"><span class="dim" id="mc-saved"></span><span class="grow"></span>
          <button class="primary" id="mc-save">${mcLab("save", "сохранить")}</button></div>
        ${(MC.errors || []).length ? `<ul class="mc-issues">${MC.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
      </div>
    </div>`;
  bind();
}

// ---------------------------------------------------------------- editing

function firstWordAnchor() {
  const w = MC.block.words[0];
  return w ? { word: { scene: w.scene, i: w.i, text: w.text }, offset: 0 } : { sec: 0 };
}

function add(kind) {
  const sc = scene();
  const p = placeOf(sc.place) || { points: [], areas: [], cameras: [] };
  sc[kind] = sc[kind] || [];
  if (kind === "cast") {
    const used = new Set(aliases());
    const alias = "abcdefghijklmnopqrstuvwxyz".split("").find((c) => !used.has(c)) || "x";
    sc.cast.push({ as: alias, actor: faces()[0] || "", at: (p.points[0] || p.areas[0] || {}).name || "" });
  } else if (kind === "crowd") {
    const c = (MC.vocab.characters || [])[0];
    sc.crowd.push({ in: (p.areas[0] || {}).name || "", density: 0.3, mix: c ? { [c.name]: 1 } : {}, do: "stand" });
  } else if (kind === "action") {
    sc.action.push({ at: firstWordAnchor(), do: "walk", who: aliases()[0] || "", to: (p.points[0] || {}).name || "" });
  } else if (kind === "shots") {
    sc.shots.push({ at: firstWordAnchor(), camera: { on: aliases()[0] || "", side: "front", distance: 3 } });
  }
}

function bind() {
  const root = mcq("#mc-sheet");
  root.querySelectorAll("[data-p]").forEach((el) => {
    el.onchange = el.oninput = (ev) => {
      const path = el.dataset.p;
      let v = el.type === "checkbox" ? el.checked : el.value;
      if (el.dataset.t === "num") v = v === "" ? undefined : +v;
      if (path.endsWith(".who_text")) {
        setAt(MC.dir, path.replace(/\.who_text$/, ".who"), v.split(",").map((x) => x.trim()).filter(Boolean));
      } else if (path.endsWith(".exclude_text")) {
        setAt(MC.dir, path.replace(/\.exclude_text$/, ".exclude"), v.split(",").map((x) => x.trim()).filter(Boolean));
      } else if (/\.mixkey\.\d+$/.test(path)) {
        const base = path.replace(/\.mixkey\.\d+$/, "");
        const cr = getAt(MC.dir, base);
        const m = +path.split(".").pop();
        const entries = Object.entries(cr.mix || {});
        if (entries[m]) entries[m][0] = v;
        cr.mix = Object.fromEntries(entries);
      } else {
        setAt(MC.dir, path, v);
      }
      // a choice that changes what the rest of the form offers redraws it; a slider
      // only updates its own number, or dragging it would rebuild it under the mouse
      if (ev.type === "change" && /(\.place|\.do|\.on|\.side)$/.test(path)) render();
      else if (el.type === "range") { const b = el.nextElementSibling; if (b && b.tagName === "B") b.textContent = (+v).toFixed(2); }
    };
  });
  root.querySelectorAll("[data-anchor-mode]").forEach((el) => {
    el.onchange = () => { setAnchorMode(el.dataset.anchorMode, el.value); render(); };
  });
  root.querySelectorAll("[data-anchor-word]").forEach((el) => {
    el.onchange = () => setAt(MC.dir, el.dataset.anchorWord, refOf(el.value));
  });
  root.querySelectorAll("[data-pair]").forEach((el) => {
    el.oninput = () => {
      const cur = pairOf(getAt(MC.dir, el.dataset.pair), +el.value).slice();
      cur[+el.dataset.k] = +el.value;
      setAt(MC.dir, el.dataset.pair, cur[0] === cur[1] ? cur[0] : cur);
      el.nextElementSibling.textContent = (+el.value).toFixed(1);
    };
  });
  root.querySelectorAll("[data-shot-kind]").forEach((el) => {
    el.onchange = () => {
      const path = el.dataset.shotKind;
      const cam = { ease: (getAt(MC.dir, path) || {}).ease || "smooth" };
      if (el.value === "on") Object.assign(cam, { on: aliases()[0] || "", side: "front", distance: 3 });
      if (el.value === "dialogue") Object.assign(cam, { style: "dialogue", pair: aliases().slice(0, 2), distance: 1.8 });
      if (el.value === "fixed") Object.assign(cam, { fixed: ((placeOf(scene().place) || { cameras: [] }).cameras.find((c) => c.kind === "fixed") || {}).name || "" });
      if (el.value === "rail") Object.assign(cam, { rail: ((placeOf(scene().place) || { cameras: [] }).cameras.find((c) => c.kind === "rail") || {}).name || "", travel: [0, 1] });
      setAt(MC.dir, path, cam);
      render();
    };
  });
  root.querySelectorAll("[data-del]").forEach((el) => {
    el.onclick = () => {
      const path = el.dataset.del;
      const parent = path.split(".").slice(0, -1).join(".");
      const last = path.split(".").pop();
      const arr = getAt(MC.dir, parent);
      if (Array.isArray(arr)) arr.splice(+last, 1); else setAt(MC.dir, path, undefined);
      render();
    };
  });
  root.querySelectorAll("[data-add]").forEach((el) => { el.onclick = () => { add(el.dataset.add); render(); }; });
  root.querySelectorAll("[data-until]").forEach((el) => {
    el.onclick = () => { setAt(MC.dir, el.dataset.until + ".until", firstWordAnchor()); render(); };
  });
  root.querySelectorAll("[data-turn-add]").forEach((el) => {
    el.onclick = () => {
      const a = getAt(MC.dir, el.dataset.turnAdd);
      a.turns = a.turns || [];
      a.turns.push({ speaker: (Array.isArray(a.who) ? a.who : [])[a.turns.length % 2] || "" });
      render();
    };
  });
  root.querySelectorAll("[data-mix]").forEach((el) => {
    el.onchange = () => {
      const cr = getAt(MC.dir, el.dataset.mix);
      cr.mix[el.dataset.ref] = Math.max(0, +el.value) / 100;
    };
  });
  root.querySelectorAll("[data-mix-add]").forEach((el) => {
    el.onclick = () => {
      const cr = getAt(MC.dir, el.dataset.mixAdd);
      const c = (MC.vocab.characters || []).find((x) => !(x.name in (cr.mix || {})));
      if (c) cr.mix = { ...(cr.mix || {}), [c.name]: 0.2 };
      render();
    };
  });
  root.querySelectorAll("[data-mix-del]").forEach((el) => {
    el.onclick = () => { const cr = getAt(MC.dir, el.dataset.mixDel); delete cr.mix[el.dataset.ref]; render(); };
  });
  root.querySelectorAll("[data-scene]").forEach((el) => { el.onclick = () => { MC.scene = +el.dataset.scene; render(); }; });
  const on = (id, fn) => { const el = mcq(id); if (el) el.onclick = fn; };
  on("#mc-scene-add", () => {
    MC.dir.scenes.push({ at: firstWordAnchor(), place: scene().place, time: scene().time, weather: scene().weather,
                         cast: [], crowd: [], action: [], shots: [] });
    MC.scene = MC.dir.scenes.length - 1;
    render();
  });
  on("#mc-scene-del", () => { MC.dir.scenes.splice(MC.scene, 1); MC.scene = Math.max(0, MC.scene - 1); render(); });
  on("#mc-save", () => saveDir());
  on("#mc-draft", () => shoot(false));
  on("#mc-final", () => shoot(true));
  on("#mc-direct", () => redirect());
  on("#mc-drop", () => dropBlock());
  on("#mc-close", () => closeMachinima());
  on("#mc-view", () => { MC.view = MC.view === "draft" ? "final" : "draft"; render(); });
}

// ---------------------------------------------------------------- the server

async function saveDir(quiet = false) {
  try {
    const r = await api(`/api/runs/${MC.run}/machinima/${MC.id}`, {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ video: MC.video, direction: MC.dir }) });
    MC.errors = r.errors;
    MC.block = { ...r.block, work: MC.block.work };
    if (!quiet) say(r.errors.length ? mcLab("saved-errors", "сохранено — есть ошибки, см. список") : mcLab("saved", "сохранено"), r.errors.length > 0);
    render();
    return r.errors.length === 0;
  } catch (e) { say(e.message, true); return false; }
}

async function shoot(final) {
  if (!(await saveDir(true))) return say(mcLab("fix-first", "сначала исправь ошибки в режиссуре"), true);
  try {
    MC.block.work = await api(`/api/runs/${MC.run}/machinima/${MC.id}/shoot`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ video: MC.video, final }) });
  } catch (e) { return say(e.message, true); }
  render();
  poll();
}

async function redirect() {
  try {
    MC.block.work = await api(`/api/runs/${MC.run}/machinima/${MC.id}/direct`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ video: MC.video }) });
  } catch (e) { return say(e.message, true); }
  render();
  poll();
}

function poll() {
  clearTimeout(MC.poll);
  MC.poll = setTimeout(async () => {
    if (!MC) return;
    let w;
    try { w = await api(`/api/runs/${MC.run}/machinima/${MC.id}/work?video=${MC.video}`); } catch (e) { return; }
    if (w.state === "running") { MC.block.work = w; return poll(); }
    await reload(MC.id);
    if (w.state === "error") say(w.message, true);
    // the track behind the sheet changed too: the block has a clip now, or none
    try { const d = await api(`/api/runs/${MC.run}/montage?video=${MC.video}`); takeDoc(d); renderMont(); } catch (e) { /* the room redraws on its next edit */ }
  }, 2500);
}

async function reload(bid) {
  const r = await api(`/api/runs/${MC.run}/machinima?video=${MC.video}`);
  MC.vocab = r.vocab;
  MC.game = r.game;
  const b = r.blocks.find((x) => x.id === bid);
  if (!b) return closeMachinima();
  MC.block = b;
  MC.dir = b.direction ? JSON.parse(JSON.stringify(b.direction)) : blankDirection();
  if (MC.scene >= MC.dir.scenes.length) MC.scene = 0;
  render();
}

function blankDirection() {
  const place = (MC.vocab.places[0] || {}).name || "";
  return { scenes: [{ place, time: "day", weather: "clear", cast: [], crowd: [], action: [], shots: [] }] };
}

async function dropBlock() {
  try {
    const d = await api(`/api/runs/${MC.run}/machinima/${MC.id}?video=${MC.video}`, { method: "DELETE" });
    takeDoc(d);
    renderMont();
  } catch (e) { return say(e.message, true); }
  closeMachinima();
}

async function openMachinima(bid) {
  MC = { run: MONT.id, video: MONT.video, id: bid, scene: 0, view: "final", errors: [] };
  mcq("#mont-mc").hidden = false;
  mcq("#mc-sheet").innerHTML = `<p class="dim">${mcLab("loading", "загружаю разметку мира…")}</p>`;
  try { await reload(bid); } catch (e) { say(e.message, true); closeMachinima(); }
  if (MC && MC.block && MC.block.work && MC.block.work.state === "running") poll();
}

function closeMachinima() {
  if (MC) clearTimeout(MC.poll);
  MC = null;
  mcq("#mont-mc").hidden = true;
}

// The montage room's own buttons for this live in the shot inspector (montage.js);
// they are answered here, by delegation, so that room does not have to know any of it.
document.addEventListener("click", async (ev) => {
  const open = ev.target.closest("[data-mc-open]");
  if (open) return openMachinima(open.dataset.mcOpen);
  const make = ev.target.closest("[data-mc-new]");
  if (make) {
    const [from, to] = make.dataset.mcNew.split("-").map(Number);
    try {
      const d = await api(`/api/runs/${MONT.id}/machinima`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ video: MONT.video, from, to }) });
      takeDoc(d);
      renderMont();
      const s = d.shots[from];
      if (s && s.block) openMachinima(s.block);
    } catch (e) { say(e.message, true); }
  }
});

// ---------------------------------------------------------------- a world's Minecraft tab

let MCW = null; // {name, data}

async function loadMinecraftWorld(name) {
  const box = document.querySelector("#w-minecraft");
  if (!box) return;
  box.innerHTML = `<p class="dim">${mcLab("loading", "загружаю…")}</p>`;
  let data;
  try { data = await api(`/api/worlds/${encodeURIComponent(name)}/minecraft`); }
  catch (e) { box.innerHTML = `<p class="bad">${esc(e.message)}</p>`; return; }
  MCW = { name, data };
  drawMinecraftWorld();
}

function drawMinecraftWorld() {
  const box = document.querySelector("#w-minecraft");
  const { data } = MCW;
  const c = data.config || {};
  const g = data.game;
  let html = `<div class="panel"><h3>${mcLab("w.where", "где построен мир")}</h3>
    <div class="row">
      <label class="inline">${mcLab("w.instance", "инстанс Prism")} <input id="mcw-instance" value="${esc(c.instance || "")}" placeholder="Object"></label>
      <label class="inline">${mcLab("w.world", "мир (сейв)")} <input id="mcw-world" value="${esc(c.world || "")}" placeholder="ОБЪЕКТ"></label>
      <label class="inline">${mcLab("w.prism", "папка Prism")} <input id="mcw-prism" value="${esc(c.prism_dir || "")}" placeholder="~/.local/share/PrismLauncher"></label>
      <button class="primary" id="mcw-save-config">${mcLab("save", "сохранить")}</button></div>
    ${data.configured ? `<p class="dim">${g ? `● ${mcLab("game-on", "игра подключена")}: slopcam ${esc(g.version || "")}, Flashback ${esc(g.flashback || "—")}, Easy NPC ${esc(g.easy_npc || "—")}`
      : `○ ${mcLab("w.game-off", "игра не запущена или открыт другой мир — разметка читается из сейва")}`}</p>` : ""}
  </div>`;
  if (data.configured && data.registry) {
    const reg = data.registry;
    const fileUrl = (rel) => tokd(`/api/worlds/${encodeURIComponent(MCW.name)}/minecraft/file?path=${encodeURIComponent(rel)}`);
    const kinds = [["areas", "zone", ["zone", "forbidden"]], ["points", "point", ["landmark", "seat", "entry"]],
                   ["lines", "line", ["route", "queue", "rail"]], ["cameras", "camera", []]];
    html += reg.places.map((p, pi) => {
      const els = kinds.flatMap(([list, kind, roles]) => (reg[list] || []).map((e, ei) => ({ e, ei, list, kind, roles })))
        .filter(({ e }) => e.place === p.id);
      return `<div class="panel mcw-place">
        <div class="row">${p.screenshot ? `<img class="mc-thumb" src="${fileUrl(p.screenshot)}">` : ""}
          <input data-reg="places.${pi}.name" value="${esc(p.name)}" style="font-weight:bold">
          <span class="grow"></span><span class="dim">${els.length} ${mcLab("w.elements", "элементов")}</span></div>
        <textarea data-reg="places.${pi}.description" rows="2" placeholder="${esc(mcLab("w.place-hint", "что это за место в мире"))}">${esc(p.description || "")}</textarea>
        ${els.map(({ e, ei, list, kind, roles }) => `<div class="mc-row">
          <span class="dim">${mcLab("kind." + kind, kind)}</span>
          <input data-reg="${list}.${ei}.name" value="${esc(e.name)}" style="width:140px">
          ${roles.map((r) => `<label class="inline"><input type="checkbox" data-role="${list}.${ei}" value="${r}"${(e.roles || []).includes(r) ? " checked" : ""}> ${mcLab("role." + r, r)}</label>`).join("")}
          <input data-reg="${list}.${ei}.description" value="${esc(e.description || "")}" placeholder="${esc(mcLab("w.el-hint", "описание для режиссёра"))}" class="grow">
          ${e.screenshot ? `<img class="mc-thumb small" src="${fileUrl(e.screenshot)}">` : ""}</div>`).join("")}
      </div>`;
    }).join("") || `<div class="panel"><p class="empty">${mcLab("w.empty", "ничего не размечено — зайди в игру: Esc → «Разметка»")}</p></div>`;
    html += `<div class="panel"><h3>${mcLab("w.characters", "персонажи и подвиды")}</h3>
      ${reg.characters.map((ch, ci) => ch.variants.map((v, vi) => `<div class="mc-row"><b>${esc(ch.name)} / ${esc(v.name)}</b>
        <span class="dim">${esc(v.body)}</span>
        <input data-reg="characters.${ci}.variants.${vi}.description" value="${esc(v.description || "")}" class="grow"
               placeholder="${esc(mcLab("w.face-hint", "чем отличается этот подвид"))}"></div>`).join("")).join("")
        || `<p class="dim">${mcLab("w.no-characters", "подвиды добавляются в игре: инструмент «Свойства» по NPC")}</p>`}
      <h3>${mcLab("w.props", "реквизит")}</h3>
      ${reg.props.map((p, i) => `<div class="mc-row"><b>${esc(p.name)}</b>
        <input data-reg="props.${i}.description" value="${esc(p.description || "")}" class="grow"></div>`).join("")
        || `<p class="dim">${mcLab("w.no-props", "реквизит добавляется в игре: E → «Реквизит»")}</p>`}
      <div class="row"><span class="dim">${mcLab("w.from", "разметка из")} ${esc(data.from)}</span><span class="grow"></span>
        <button class="primary" id="mcw-save-reg">${mcLab("w.save-markup", "сохранить разметку")}</button></div></div>`;
    html += `<div class="panel"><h3>${mcLab("w.library", "позы и жесты")}</h3>
      <p class="dim">${Object.entries(data.library.poses).map(([n, d]) => `<b>${esc(n)}</b> — ${esc(d)}`).join(" · ")}</p>
      <p class="dim">${Object.entries(data.library.gestures).map(([n, d]) => `<b>${esc(n)}</b> — ${esc(d)}`).join(" · ")}</p></div>`;
  }
  box.innerHTML = html;
  const saveConfig = box.querySelector("#mcw-save-config");
  if (saveConfig) saveConfig.onclick = async () => {
    try {
      await api(`/api/worlds/${encodeURIComponent(MCW.name)}/minecraft`, {
        method: "PUT", headers: { "content-type": "application/json" },
        body: JSON.stringify({ instance: box.querySelector("#mcw-instance").value,
                               world: box.querySelector("#mcw-world").value,
                               prism_dir: box.querySelector("#mcw-prism").value }) });
      say(mcLab("saved", "сохранено"));
      loadMinecraftWorld(MCW.name);
    } catch (e) { say(e.message, true); }
  };
  box.querySelectorAll("[data-reg]").forEach((el) => {
    el.onchange = () => setAt(MCW.data.registry, el.dataset.reg, el.value);
  });
  box.querySelectorAll("[data-role]").forEach((el) => {
    el.onchange = () => {
      const e = getAt(MCW.data.registry, el.dataset.role);
      const roles = new Set(e.roles || []);
      if (el.checked) roles.add(el.value); else roles.delete(el.value);
      e.roles = [...roles];
    };
  });
  const saveReg = box.querySelector("#mcw-save-reg");
  if (saveReg) saveReg.onclick = async () => {
    try {
      const r = await api(`/api/worlds/${encodeURIComponent(MCW.name)}/minecraft/registry`, {
        method: "PUT", headers: { "content-type": "application/json" },
        body: JSON.stringify({ registry: MCW.data.registry }) });
      say(r.saved_to === "game" ? mcLab("w.saved-game", "сохранено — уже видно в игре") : mcLab("w.saved-save", "сохранено в сейв"));
    } catch (e) { say(e.message, true); }
  };
}

window.openMachinima = openMachinima;
window.closeMachinima = closeMachinima;
window.loadMinecraftWorld = loadMinecraftWorld;
})();
