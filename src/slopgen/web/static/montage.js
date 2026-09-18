// The montage room.
//
// Every other screen in this page is a form: rows in, rows out, and the server decides
// what they mean. This one is not, because the thing it edits is not rows. A picture
// track is two clocks laid over each other — the narration, and the stills cut across
// it — and the two gestures that matter are "start a shot on THAT word" and "put THIS
// picture here". Neither survives being typed.
//
// So: one horizontal timeline at a chosen number of pixels per second, three lanes
// over the same clock (the shots, the word cues, the lines), and a canvas that draws
// whatever the playhead is on. Everything the operator does is one request, and the
// reply is the WHOLE document again rather than a patch — almost every edit moves the
// clock under everything after it, and a screen holding half-stale seconds is worse
// than a screen that redraws.
//
// The preview is deliberately an approximation and says so. Grain, a tube's
// misregistered colour, a torn scanline are ffmpeg's arithmetic; canvas can impersonate
// them but never reproduce them. What it CAN be exact about is timing, which is the
// thing you are editing — so the cuts, the crop moves and the word sync are real, the
// look is a sketch, and `точный кадр` renders one true frame through the actual chain
// whenever the sketch is not enough.

let MONT = null;      // {id, title, video, doc}
let montPPS = 140;    // pixels per second — the timeline's zoom
let montSel = null;   // {kind: "shot"|"line", i}
// Which keyframe is being pointed at, as {shot, i}. It is a SELECTION and not a mode:
// the marker on the track and the row in the list are two views of one moment, so
// touching either lights both — otherwise a track of eight diamonds tells you when
// something happens and never which line of the list it is.
let montKeySel = null;
let montRaf = 0;
const montPics = new Map();  // card name -> HTMLImageElement | HTMLVideoElement

// What has been TYPED into a line and not yet written back, by line index.
//
// The text box is the one control here whose value is not the document's. Everything
// else on this screen commits the instant it is touched — a card, a cut, a move — and
// the reply redraws the panel, which is fine for a button and fatal for a box somebody
// is in the middle of a sentence in. Held here, a draft survives the redraw, survives
// selecting another line and coming back, and is written back on blur, so the only way
// to lose what you typed is to say so.
const montDrafts = new Map();
const draftOf = (sc) => (montDrafts.has(sc.i) ? montDrafts.get(sc.i) : sc.text);
const dirty = (sc) => draftOf(sc).trim() !== sc.text.trim();

const mq = (s) => document.querySelector(s);
const montTime = () => {
  const a = mq("#mont-audio");
  return a && !isNaN(a.currentTime) ? a.currentTime : 0;
};

const clock = (s) => {
  s = Math.max(0, s || 0);
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
};

// ---------------------------------------------------------------- opening it

async function openMontage(id, title, video = 0) {
  let d;
  try {
    d = await api(`/api/runs/${id}/montage?video=${video}`);
  } catch (e) { return say(e.message, true); }
  MONT = { id, title, video, doc: d };
  montSel = null;
  montPics.clear();
  montDrafts.clear();
  mq("#mont-title").textContent = title;
  reloadVoice();
  buildFxRows();
  fitZoom();
  renderMont();
  // it is reopened on the way back from the card editor, and the sheet may have been
  // standing open when we left: its controls are the document's, so they are redrawn
  // with it rather than left holding the run as it was ten minutes ago
  if (!mq("#mont-set").hidden) renderSettings();
  mq("#mont").hidden = false;
  drawFrame();
}

// A sensible zoom for THIS video rather than a constant: the words have to be readable
// side by side, because clicking one is the whole gesture of this screen, and how much
// room they need is a fact about how fast this narrator talks.
//
// The MEDIAN of what each word needs, not the worst case. A narration always holds a
// few pairs half a beat apart, and sizing for those spreads the ordinary ones over a
// thousand pixels a second — a timeline nobody can see two shots of at once. At the
// median the tight pairs overlap a little and everything else is comfortable, which is
// the right way round: the ones that overlap are the ones you zoom in for.
function fitZoom() {
  const need = [];
  for (const sc of MONT.doc.scenes) {
    for (let i = 1; i < sc.words.length; i++) {
      const g = sc.words[i].start - sc.words[i - 1].start;
      // roughly 7.6px per character at the lane's font size, plus the chip's padding
      if (g > 0.01) need.push((sc.words[i - 1].t.length * 7.6 + 12) / g);
    }
  }
  if (!need.length) return;
  need.sort((a, b) => a - b);
  montPPS = Math.round(Math.min(200, Math.max(70, need[Math.floor(need.length / 2)] || 120)));
  mq("#mont-zoom").value = montPPS;
}

// ---------------------------------------------------------------- the timeline

const X = (t) => Math.round(t * montPPS);

// ------------------------------------------------------------- the pipeline, by hand
//
// The same callables the chain would have run, pressed one at a time. What makes that
// worth having rather than merely possible is that the chain's ORDER is not the work's:
// you write, hear it, look at it, re-write one line, voice that line again, cut, cast,
// cut again. A stage whose input is not on the job yet is disabled — the server decides
// that (`montage.READY`), because what a stage needs is a fact about the stage.

function renderStages() {
  const d = MONT.doc;
  // «нарезать заново» is the wrong words for a track that does not exist yet
  mq("#mont-recut").textContent =
    lab(d.shots.length ? "web.mont.recut" : "js.mont.laytrack");
  const rail = (d.stages || []).map((st) => `
    <button data-stage="${esc(st.name)}" class="${st.done ? "done" : ""}"
      ${st.ready ? "" : "disabled"}
      title="${esc(st.ready ? lab("js.mont.press") : lab("js.mont.notready"))}"
    >${esc(word(st.name))}</button>`).join("");
  // The switch sits WITH the stages because it is about one of them: `дорожка картинки`
  // either asks a model which card fits each stretch, or lays the cuts and leaves every
  // shot for you. Left on the form it came from, one button here would quietly mean two
  // different things — and the one that asks a model needs a key the other does not.
  // Offered only when there is something to repair: a track whose moves all go
  // somewhere has nothing to recompute, and a standing button for it would be a
  // standing invitation to press something that does nothing.
  const frozen = d.frozen || 0;
  mq("#mont-stages").innerHTML = `<b class="railhead">${lab("js.mont.pipeline")}</b>` + rail
    + `<label class="inline byhand" title="${esc(lab("web.byhand.note"))}">
         <input type="checkbox" id="mont-byhand"${d.by_hand ? " checked" : ""}>
         <span>${lab("web.f.byhand")}</span></label>`
    + `<span class="grow"></span>`
    + (frozen ? `<button id="mont-unfreeze" class="ghost warn-btn"
         title="${esc(lab("js.mont.frozen-why"))}">${
         lab("js.mont.unfreeze")} ${frozen}</button>` : "")
    + (d.cut ? `<button id="mont-watch" class="ghost">${lab("js.watch-the-video")}</button>` : "");
  mq("#mont-stages").querySelectorAll("[data-stage]").forEach((b) => {
    b.onclick = () => press(b.dataset.stage);
  });
  mq("#mont-byhand").onchange = async (e) => {
    try {
      MONT.doc = await api(`/api/runs/${MONT.id}/montage/settings`, { method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ video: MONT.video, frame_by_hand: e.target.checked }) });
    } catch (err) { return say(err.message, true); }
    renderStages();
  };
  const watch = mq("#mont-watch");
  if (watch) watch.onclick = () =>
    window.open(tokd(`/api/runs/${MONT.id}/video`), "_blank");
  const thaw = mq("#mont-unfreeze");
  if (thaw) thaw.onclick = () =>
    send("/moves", { method: "POST", body: J({}) },
         (d2) => say(`${lab("js.mont.unfroze")} ${d2.fixed}`));
}

// One question, asked in the page. Resolves true when the operator says go and false
// on anything else — the No button, Esc, a click on the backdrop — so a caller reads
// like `if (!await ask(...)) return;`.
let askOff = null;

function ask({ title, what, lines = [], ok, danger = false }) {
  const box = mq("#mont-ask");
  mq("#ask-title").textContent = title;
  mq("#ask-body").innerHTML =
    (what ? `<p class="what">${esc(what)}</p>` : "")
    + (lines.length ? `<ul>${lines.map((l) =>
        `<li class="${esc(l.cls || "")}">${esc(l.text)}</li>`).join("")}</ul>` : "");
  const yes = mq("#ask-yes");
  yes.textContent = ok;
  yes.classList.toggle("danger", !!danger);
  box.hidden = false;
  yes.focus();
  return new Promise((done) => {
    const close = (answer) => {
      box.hidden = true;
      document.removeEventListener("keydown", onKey, true);
      askOff = null;
      done(answer);
    };
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); close(false); }
    };
    askOff = close;
    yes.onclick = () => close(true);
    mq("#ask-no").onclick = () => close(false);
    // the backdrop, but not the sheet standing on it
    box.onclick = (e) => { if (e.target === box) close(false); };
    document.addEventListener("keydown", onKey, true);
  });
}

// What a stage IS, what it will spend and what it will write over — all three asked of
// the server (`montage.stages`), because all three are facts about the stage and about
// this particular job. Nine identical chips was the first version, and it answered
// none of them: which one calls a model, which one replaces the lines you just typed,
// which one is free.
function stageQuestion(st) {
  const lines = [];
  if (st.llm) lines.push({ text: lab("js.mont.costs-llm"), cls: "cost" });
  for (const r of st.redo || []) {
    const words = lab("js.mont.redo." + r.what);
    lines.push({ text: r.n === undefined ? words : `${words} ${r.n}`,
                 cls: r.what === "kept" ? "" : "warn" });
  }
  if (st.done) lines.push({ text: lab("js.mont.already-done"), cls: "" });
  if (!lines.length) lines.push({ text: lab("js.mont.costs-nothing"), cls: "" });
  return { title: word(st.name), what: lab("js.mont.what." + st.name, ""),
           lines, ok: lab("js.mont.go"),
           danger: (st.redo || []).some((r) => r.what !== "kept") };
}

async function press(stage) {
  const st = (MONT.doc.stages || []).find((x) => x.name === stage);
  if (st && !await ask(stageQuestion(st))) return;
  // a stage reads the job; what is in the box is not on the job until it is written
  await flush();
  const rail = mq("#mont-stages");
  rail.querySelectorAll("button").forEach((b) => (b.disabled = true));
  rail.classList.add("busy");
  // A stage is minutes, not milliseconds — voicing forty lines, fetching forty clips —
  // and it reports on the RUN's stream while it goes, which is the log under its row.
  // So say where to look rather than leaving a dead screen.
  say(`${lab("js.mont.running")} ${word(stage)} — ${lab("js.mont.watch-the-log")}`);
  let d;
  try {
    d = await api(`/api/runs/${MONT.id}/montage/stage`, { method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ video: MONT.video, stage }) });
  } catch (e) {
    rail.classList.remove("busy");
    renderStages();
    return say(e.message, true);
  }
  rail.classList.remove("busy");
  MONT.doc = d;
  montSel = null;
  reloadVoice();
  renderMont();
  say(d.waiting ? d.waiting : `${word(stage)} — ${lab("js.mont.stage-done")}`, !!d.waiting);
}

function renderMont() {
  const d = MONT.doc;
  renderStages();
  const width = Math.max(X(d.total) + 40, 320);
  mq("#mont-lanes").style.width = width + "px";
  mq("#mont-state").textContent = stateLine(d);
  mq("#lane-ruler").innerHTML = rulerHTML(d.total);
  // An empty lane is where somebody stands when they do not know how to make a shot,
  // so that is where it is said — not in a grey line in the far corner. Both ways in
  // are here: cut the whole thing on the speech at once, or click the word a shot
  // should start on, which now works from nothing (see `montage.open_track`).
  mq("#lane-shots").innerHTML = d.shots.length
    ? d.shots.map(shotHTML).join("")
    : `<div class="notrack">${d.scenes.some((sc) => sc.voiced)
        ? `<span>${lab("js.mont.notrack")}</span>
           <button class="ghost" id="mont-lay">${lab("js.mont.laytrack")}</button>`
        : `<span>${lab("js.mont.notrack-silent")}</span>`}</div>`;
  mq("#lane-keys").innerHTML = d.shots.flatMap((sh, n) =>
    ((sh.move && sh.move.keys) || []).map((k, i) => {
      const on = montKeySel && montKeySel.shot === n && montKeySel.i === i;
      const mine = montSel && montSel.kind === "shot" && montSel.i === n;
      return `<i class="kf${on ? " on" : ""}${mine ? " mine" : ""}"
        data-shot="${n}" data-key="${i}" style="left:${X(sh.start + k.at)}px"
        title="${esc(`${(+k.at).toFixed(1)}${lab("js.s")} · ${k.of || lab("js.mont.whole")}`)}"></i>`;
    })).join("");
  mq("#lane-cues").innerHTML = d.scenes.flatMap((sc) =>
    sc.words.map((w, i) =>
      `<i class="cue" style="left:${X(w.start)}px" data-s="${sc.i}" data-w="${i}"
         title="${esc(w.t)}"></i>`)).join("");
  mq("#lane-lines").innerHTML = d.scenes.map(lineHTML).join("");
  renderSilent();
  bindLanes();
  renderInspector();
  showSelection();
  drawFrame();
}

function stateLine(d) {
  const bits = [`${d.shots.length} ${lab("js.mont.shots")}`, clock(d.total)];
  for (const b of d.blocking || []) bits.push(`${lab("js.mont.left." + b.what)} ${b.n}`);
  return bits.join(" · ");
}

function rulerHTML(total) {
  // a mark every second while there is room for one, every five when there is not
  const step = montPPS >= 90 ? 1 : montPPS >= 30 ? 5 : 10;
  let out = "";
  for (let t = 0; t <= total; t += step)
    out += `<i style="left:${X(t)}px"><b>${clock(t)}</b></i>`;
  return out;
}

function shotHTML(s, i) {
  const card = cardOf(s.card);
  const thumb = card ? `background-image:url('${tokd(card.poster || card.url)}')` : "";
  const sel = montSel && montSel.kind === "shot" && montSel.i === i;
  return `<div class="shot${sel ? " sel" : ""}${s.card ? "" : " empty"}"
      data-shot="${i}" style="left:${X(s.start)}px;width:${Math.max(X(s.duration) - 2, 8)}px">
    <div class="pic" style="${thumb}"></div>
    <div class="tag">${esc(s.card || lab("js.mont.nopicture"))}</div>
    ${s.move ? `<div class="kind">${esc((s.move.keys || []).length >= 2
        ? lab("mv.keys") : lab("mv." + s.move.kind, s.move.kind))}</div>` : ""}
    ${s.pinned ? `<span class="pin">✔</span>` : ""}
    <button class="uncut" title="${esc(lab("js.mont.uncut"))}">✕</button>
  </div>`;
}

function lineHTML(sc) {
  const sel = montSel && montSel.kind === "line" && montSel.i === sc.i;
  const words = sc.words.map((w, i) =>
    `<span class="wd" data-s="${sc.i}" data-w="${i}"
       style="left:${X(w.start - sc.start)}px">${esc(w.t)}</span>`).join("");
  // A silent line is given a width it has not earned, so that it is visible and can be
  // hit; it overlaps whatever follows, which is honest — it does not own those seconds
  // and will not until it is voiced.
  const w = sc.voiced ? Math.max(X(sc.duration) - 2, 8) : 110;
  return `<div class="line${sel ? " sel" : ""}${sc.voiced ? "" : " silent"}${sc.is_ad ? " ad" : ""}"
      data-line="${sc.i}" style="left:${X(sc.start)}px;width:${w}px">
    <div class="lhead">#${sc.i + 1}${sc.voiced ? "" : " · " + lab("js.mont.silent")}${
      // the speed, but only where this line differs from the run — a line voiced at
      // the run's own rate is pinned to it all the same, and "· 0%" on every line is
      // a column of noise saying nothing
      rateOf(sc) !== (MONT.doc.rate || 0)
        ? ` · ${rateOf(sc) > 0 ? "+" : ""}${rateOf(sc)}%` : ""}</div>
    <div class="words">${words}</div>
  </div>`;
}

// Every change of selection goes through here, and the reason is the text box: leaving
// a line is one of the two moments its draft has to be written back, and `blur` is not
// dependable for the other half of them — a panel rebuilt from innerHTML takes the
// focused element away without the browser announcing it, and a click that redraws the
// panel in the same tick can land before the blur does. So the commit is part of
// LEAVING rather than part of losing focus.
// Write back whatever is in the box before doing something that moves the lines under
// it. A draft is held BY INDEX, so adding or dropping a line has to drop the drafts —
// and dropping one that was never written back is losing what somebody typed. Found
// exactly that way: type a line, press ＋, and the line you had just written was blank.
async function flush() {
  if (montSel && montSel.kind === "line") await commitText(montSel.i);
}

async function pick(next) {
  const was = montSel;
  if (!(next && next.kind === "shot" && montKeySel && montKeySel.shot === next.i))
    montKeySel = null;
  const leaving = was && was.kind === "line"
    && !(next && next.kind === "line" && next.i === was.i);
  if (leaving) await commitText(was.i);
  montSel = next;
  renderMont();
}

// A line with no voice has no length, and a lane laid out in seconds has nowhere to
// draw it: a dozen of them share one x and collapse into a sliver nobody can hit. So
// they are listed here instead, as chips, until they have a length of their own.
function renderSilent() {
  const box = mq("#mont-silent");
  const mute = (MONT.doc.scenes || []).filter((sc) => !sc.voiced && !sc.is_ad);
  box.innerHTML =
    (mute.length ? `<b>${lab("js.mont.silentlines")}</b>` + mute.map((sc) => {
      const sel = montSel && montSel.kind === "line" && montSel.i === sc.i;
      const text = (montDrafts.has(sc.i) ? montDrafts.get(sc.i) : sc.text).trim();
      return `<button data-mute="${sc.i}" class="${sel ? "on" : ""}"
        title="${esc(text || lab("js.mont.nothing-written"))}">#${sc.i + 1}${
        text ? " " + esc(text.slice(0, 22)) + (text.length > 22 ? "…" : "") : ""}</button>`;
    }).join("") : "")
    + `<span class="grow"></span>
       <button class="ghost" id="mont-addline">${lab("js.mont.addline")}</button>`;
  box.querySelectorAll("[data-mute]").forEach((b) => {
    b.onclick = () => pick({ kind: "line", i: +b.dataset.mute });
  });
  mq("#mont-addline").onclick = async () => {
    await flush();
    send("/line", { method: "POST", body: J({ after: MONT.doc.scenes.length - 1 }) },
         (d) => { montDrafts.clear(); montSel = { kind: "line", i: d.at }; });
  };
}

const cardOf = (name) => (MONT.doc.cards || []).find((c) => c.name === name) || null;

function bindLanes() {
  // A word is where a shot BEGINS. That is the whole gesture of this screen, so it is
  // a plain click on the word itself — no mode to enter, nothing to drag — and the
  // shot before it ends there by definition.
  mq("#lane-lines").querySelectorAll(".wd").forEach((w) => {
    w.onclick = async (e) => {
      e.stopPropagation();
      // the word belongs to a line, and pressing it leaves that line for a shot — so
      // whatever was typed into it is written back first, like any other departure
      if (montSel && montSel.kind === "line") await commitText(montSel.i);
      send("/cut", { method: "POST", body: J({ scene: +w.dataset.s, word: +w.dataset.w }) },
           (d) => { montSel = { kind: "shot", i: d.at }; });
    };
  });
  mq("#lane-lines").querySelectorAll(".line").forEach((el) => {
    el.onclick = () => pick({ kind: "line", i: +el.dataset.line });
  });
  mq("#lane-shots").querySelectorAll(".shot").forEach((el) => {
    const i = +el.dataset.shot;
    el.onclick = () => pick({ kind: "shot", i });
    el.querySelector(".uncut").onclick = (e) => {
      e.stopPropagation();
      send(`/cut?video=${MONT.video}&shot=${i}`, { method: "DELETE" },
           () => { montSel = null; });
    };
  });
  bindKeyMarkers();
  // `offsetX` is measured against whatever was under the pointer — a tick, a label —
  // so it is asked of the lane itself, which is the thing the clock is drawn on.
  const scrub = (e) => seek((e.clientX - e.currentTarget.getBoundingClientRect().left) / montPPS);
  mq("#lane-ruler").onclick = scrub;
  mq("#lane-cues").onclick = scrub;
  const lay = mq("#mont-lay");
  if (lay) lay.onclick = () =>
    send("/recut", { method: "POST", body: J({ sensitivity: MONT.doc.sensitivity }) },
         () => { montSel = null; say(lab("js.mont.recut-done")); });
}

// Dragging a keyframe along the track.
//
// A press that does not move is a CLICK — it selects the moment and lights its row in
// the list — and one that does is a drag. The two share a gesture on purpose: the
// marker is the moment, and there is nothing else you would want to do to it. The
// threshold is the same few pixels the switches in `app.js` use, for the same reason:
// a finger never presses perfectly still.
const KF_SLOP = 3;

function bindKeyMarkers() {
  mq("#lane-keys").querySelectorAll(".kf").forEach((el) => {
    const shot = +el.dataset.shot, i = +el.dataset.key;
    let from = 0, at0 = 0, moved = false, holding = false;
    el.onpointerdown = (e) => {
      e.preventDefault();
      e.stopPropagation();
      const sh = MONT.doc.shots[shot];
      if (!sh) return;
      from = e.clientX;
      at0 = ((sh.move.keys || [])[i] || {}).at || 0;
      moved = false;
      holding = true;
      // Pointing at it selects it, and that happens FIRST: capturing the pointer is a
      // convenience — it keeps the moves coming once the cursor leaves the diamond —
      // and it is allowed to fail. Doing it first meant a failure took the selection
      // with it, which is the whole gesture lost to a nicety.
      montKeySel = { shot, i };
      try { el.setPointerCapture(e.pointerId); } catch { /* moves stay on the element */ }
      // Lit IN PLACE, and this is the whole reason there is a function for it: a full
      // redraw rebuilds the lane from innerHTML, which replaces the very node the
      // pointer is holding — the gesture then dies with it, capture and all. Found by
      // pressing a marker and watching it refuse to drag. Selecting a different shot
      // redraws the PANEL, which is not the node under the finger.
      const other = !(montSel && montSel.kind === "shot" && montSel.i === shot);
      if (other) montSel = { kind: "shot", i: shot };
      lightKey();
      if (other) renderInspector();
      showKeyRow();
    };
    el.onpointermove = (e) => {
      if (!holding) return;
      if (!moved && Math.abs(e.clientX - from) < KF_SLOP) return;
      moved = true;
      el.classList.add("dragging");
      const sh = MONT.doc.shots[shot];
      const at = Math.min(Math.max(at0 + (e.clientX - from) / montPPS, 0), sh.duration);
      el.style.left = X(sh.start + at) + "px";
      // the list follows the hand rather than waiting for the release: the row IS the
      // marker, and a number that lags behind the thing you are dragging reads as two
      // controls that disagree
      const box = mq(`#mont-insp .key[data-key="${i}"] .k-at`);
      if (box) box.value = at.toFixed(1);
      seekSilently(sh.start + at);   // and the picture shows what that moment looks like
    };
    const drop = (e) => {
      if (!holding) return;
      holding = false;
      try { el.releasePointerCapture(e.pointerId); } catch { /* never had it */ }
      el.classList.remove("dragging");
      if (!moved) return;   // a press, already handled
      const sh = MONT.doc.shots[shot];
      const at = Math.min(Math.max(at0 + (e.clientX - from) / montPPS, 0), sh.duration);
      const keys = (sh.move.keys || []).map((k, n) => ({
        at: n === i ? +at.toFixed(2) : k.at, of: k.of, scale: k.scale }));
      // sorted by time on the way in, so the moment being dragged may change its place
      // in the list — it is followed by WHERE IT LANDED rather than by its old index
      send("/shot/keys", { method: "PUT", body: J({ shot, keys }) }, () => {
        const now = (MONT.doc.shots[shot].move.keys || []);
        let best = 0;
        now.forEach((k, n) => {
          if (Math.abs(k.at - at) < Math.abs(now[best].at - at)) best = n;
        });
        montKeySel = { shot, i: best };
      });
    };
    el.onpointerup = drop;
    el.onpointercancel = drop;
  });
}

// The lit moment, in both of the places it is shown, without rebuilding either. A
// keyframe on the track and a row in the list are one thing seen twice, so the
// highlight is one toggle applied in two places rather than two renders.
function lightKey() {
  const sel = montKeySel;
  mq("#lane-keys").querySelectorAll(".kf").forEach((el) => {
    const mine = montSel && montSel.kind === "shot" && montSel.i === +el.dataset.shot;
    el.classList.toggle("mine", !!mine);
    el.classList.toggle("on", !!sel && sel.shot === +el.dataset.shot
                                    && sel.i === +el.dataset.key);
  });
  mq("#lane-shots").querySelectorAll(".shot").forEach((el) =>
    el.classList.toggle("sel", montSel && montSel.kind === "shot"
                               && montSel.i === +el.dataset.shot));
  mq("#mont-insp").querySelectorAll(".key").forEach((row, n) =>
    row.classList.toggle("on", !!sel && sel.i === n));
}

// Put the lit row where it can be seen — a key list is short, but the panel it sits in
// scrolls, and a highlight below the fold is no highlight.
function showKeyRow() {
  if (!montKeySel) return;
  const row = mq(`#mont-insp .key[data-key="${montKeySel.i}"]`);
  if (row) row.scrollIntoView({ block: "nearest" });
}

const J = (o) => JSON.stringify({ video: MONT.video, ...o });

// Every edit is the same shape: one request, the whole document back, redraw. `after`
// runs between the two so a caller can say what should be selected once the numbers it
// selects by have changed.
async function send(path, opts, after) {
  const o = { ...opts };
  // Only a STRING body is JSON. Declaring `application/json` over a FormData one
  // stops the browser writing the multipart boundary, and the upload arrives as a
  // body the server cannot take apart — a file well that silently rejects everything.
  if (typeof o.body === "string") o.headers = { "content-type": "application/json" };
  let d;
  try {
    d = await api(`/api/runs/${MONT.id}/montage${path}`, o);
  } catch (e) { return say(e.message, true); }
  MONT.doc = d;
  if (after) after(d);
  renderMont();
  return d;
}

// ---------------------------------------------------------------- what is selected

// Selecting something off-screen and being shown a panel about it, with the thing
// itself nowhere on the clock, is how you lose your place in a nine-thousand-pixel
// timeline. So the track follows the selection — never the other way round, which
// would fight the playhead.
function showSelection() {
  if (!montSel) return;
  const el = mq(montSel.kind === "shot"
    ? `#lane-shots [data-shot="${montSel.i}"]` : `#lane-lines [data-line="${montSel.i}"]`);
  if (!el) return;
  const box = mq("#mont-time");
  const x = parseFloat(el.style.left) || 0;
  if (x < box.scrollLeft || x > box.scrollLeft + box.clientWidth - 80)
    box.scrollLeft = Math.max(0, x - 80);
}

// Where the caret was, so a redraw does not take it out of the box being typed in.
// Every edit on this screen redraws the whole panel (the clock moves under almost all
// of them), and a panel rebuilt from innerHTML is a new textarea with the cursor at
// position zero.
function keepCaret(box, draw) {
  const el = document.activeElement;
  const at = el && box.contains(el) && "selectionStart" in el
    ? { id: el.id, start: el.selectionStart, end: el.selectionEnd } : null;
  draw();
  if (!at) return;
  const back = at.id && mq("#" + at.id);
  if (!back) return;
  back.focus();
  try { back.setSelectionRange(at.start, at.end); } catch { /* not a text field */ }
}

function renderInspector() {
  const box = mq("#mont-insp");
  if (!montSel) {
    // With nothing on the track there is nothing to select, and the screen would be a
    // dead end: the two ways to put words in a video are pressing `сценарий` up there
    // and typing one yourself, and both have to be reachable from here.
    box.innerHTML = `<p class="dim">${lab(MONT.doc.scenes.length
      ? "js.mont.pickone" : "js.mont.empty")}</p>
      <div class="row"><button class="ghost" id="i-first">${lab("js.mont.addline")}</button></div>`;
    mq("#i-first").onclick = () =>
      send("/line", { method: "POST", body: J({ after: MONT.doc.scenes.length - 1 }) },
           (d) => { montDrafts.clear(); montSel = { kind: "line", i: d.at }; });
    return;
  }
  keepCaret(box, () => {
    box.innerHTML = montSel.kind === "shot" ? shotInspector() : lineInspector();
    if (montSel.kind === "shot") bindShotInspector(); else bindLineInspector();
  });
  if (montSel.kind === "line") {
    const text = mq("#i-text");
    // a line with nothing written on it was added to be written on
    if (text && !text.value.trim() && document.activeElement !== text) text.focus();
  }
}

function shotInspector() {
  const s = MONT.doc.shots[montSel.i];
  if (!s) return `<p class="dim">${lab("js.mont.pickone")}</p>`;
  const card = cardOf(s.card);
  const targets = card ? card.targets.filter((t) => t.label) : [];
  const strip = (MONT.doc.cards || []).map((c) => `
    <div class="frame-card${c.name === s.card ? " on" : ""}" data-pick="${esc(c.name)}"
         title="${esc(c.description || c.name)}">
      <div class="thumb${c.fit === "pad" ? " pad" : ""}" style="background-image:url('${tokd(c.poster || c.url)}')">
        ${c.kind === "video" ? `<span class="pill">${lab("js.clip")}</span>` : ""}
        ${c.targets.length ? `<span class="pill">${c.targets.length}</span>` : ""}
      </div>
      <div class="meta"><b>${esc(c.name)}</b></div>
    </div>`).join("");
  return `
  <div class="insp-head"><b>${lab("js.mont.shot")} ${montSel.i + 1}</b>
    <span class="dim">${s.start.toFixed(1)}–${(s.start + s.duration).toFixed(1)}${lab("js.s")}</span>
    <span class="grow"></span>
    ${s.card ? `<button class="ghost" id="i-mark">${lab("js.mark-it-up")}</button>` : ""}
    ${s.card ? `<button class="ghost" id="i-clear">${lab("js.mont.clearshot")}</button>` : ""}
  </div>
  ${s.said ? `<p class="said"><span class="dim">${lab("js.said-here")}</span> ${esc(s.said)}</p>` : ""}
  ${moveBlock(s, card, targets)}
  <h4 class="insp-h">${lab("js.mont.whatshown")}</h4>
  <div class="strip">${strip || `<p class="empty">${lab("js.the-base-is-empty")}</p>`}</div>
  <div class="take" id="i-take"><span class="say">${lab("js.mont.newcard")}</span>
    <input type="file" hidden accept="image/*,video/*"></div>`;
}

// The shot's ANIMATION, as its own named block. It used to be an unlabelled row of six
// buttons wedged between the narration and the card strip, which is not a control panel
// — it is six buttons. Three things were wrong with it and all three are here:
//
//   * it had no name, so nothing said these were the camera move at all;
//   * on a shot with no card it was inert (a move is built FROM a card's geometry) and
//     said nothing about why, so pressing one looked like a dead button;
//   * and it only ever offered the move's KIND. When the travel starts and how long it
//     runs are the other half of an animation — the planner rolls them off a die around
//     measured fractions — and there was no way to say "hold, then move on that word".
function moveBlock(s, card, targets) {
  const m = s.move;
  const dur = Math.max(s.duration, 0.1);
  if (!s.card) {
    return `<div class="block dim-block"><h4 class="insp-h">${lab("js.mont.camera")}</h4>
      <p class="dim">${lab("js.mont.camera-needs-card")}</p></div>`;
  }
  const lead = m ? m.from : 0;
  const span = m ? Math.max(m.to - m.from, 0) : 0;
  const keys = (m && m.keys) || [];
  const step = 0.1;
  // A clip used to get neither the moves nor the timing, on the grounds that it has
  // motion of its own. Half the clips a world collects are locked-off shots of a
  // room — stills with dust in them — and refusing a push-in on one was refusing the
  // obvious. Both kinds of card get the same controls now.
  // TWO ways to animate a shot and the operator picks which, rather than the mode
  // being inferred from whether any keys happen to exist. Simple is the default and
  // stays the default: six named presets cover most shots, and a key list to build
  // "hold, then push in" by hand would be work where a button would do. The preset
  // survives underneath a run of keys, so the choice is reversible in both
  // directions and neither costs what you had.
  const keyed = keys.length >= 2;
  return `<div class="block"><h4 class="insp-h">${lab("js.mont.camera")}
      <span class="chips modes" id="i-modes">
        <button data-mode="simple" class="${keyed ? "" : "on"}">${lab("js.mont.simple")}</button>
        <button data-mode="keys" class="${keyed ? "on" : ""}">${lab("js.mont.bykeys")}</button>
      </span>
      <span class="grow"></span>
      <button class="ghost" id="i-play">${lab("js.mont.playshot")}</button></h4>
    ${keyed ? "" : `
    <div class="row chips" id="i-moves">${(MONT.doc.moves || []).map((k) =>
      `<button data-move="${esc(k)}" class="${m && m.kind === k ? "on" : ""}">${
        esc(lab("mv." + k, k))}</button>`).join("")}</div>
    ${targets.length ? `<label class="inline">${lab("js.mont.target")}
      <select id="i-target"><option value="">${lab("w.none", "— нет —")}</option>${
        targets.map((t) => `<option${t.label === s.target ? " selected" : ""}>${esc(t.label)}</option>`)
          .join("")}</select></label>`
      : `<p class="dim">${lab("js.mont.no-regions")}</p>`}
    <div class="timing">
      <label>${lab("js.mont.move-lead")} <span class="dose" id="i-lead-v">${lead.toFixed(1)}${lab("js.s")}</span>
        <input type="range" id="i-lead" min="0" max="${dur.toFixed(2)}" step="${step}" value="${lead.toFixed(2)}"></label>
      <label>${lab("js.mont.move-span")} <span class="dose" id="i-span-v">${span.toFixed(1)}${lab("js.s")}</span>
        <input type="range" id="i-span" min="0.1" max="${dur.toFixed(2)}" step="${step}" value="${span.toFixed(2)}"></label>
      <p class="dim" id="i-move-note">${moveNote(lead, span, dur)}</p>
    </div>`}
    ${keyed ? keyList(s, card, keys, dur)
            + `<div class="row keysbar"><button class="ghost" id="i-addkey">${
                lab("js.mont.addkey")}</button><span class="grow"></span></div>` : ""}
    ${s.soft ? `<p class="warn-note">${lab("js.mont.soft-card")}</p>` : ""}
  </div>`;
}

// Hand-placed moments, one row each: when, where to look, how close. Three answers and
// no more, because everything the six presets do is two of these and everything they
// cannot — hold on the room, come in on the desk, pan to the door — is three or four.
//
// The whole list goes back on every change (see the `keys` route): keys are ordered by
// WHEN, so an index is not a stable name for one, and dragging the second past the
// third would renumber everything after it under any per-key addressing.
// How close the window is, as the magnification a viewer sees: 1.0 of the picture is
// ×1, half of it is ×2. The number the operator is actually judging.
const zoomPct = (scale) => "×" + (1 / Math.max(scale, 0.01)).toFixed(2);

function keyList(s, card, keys, dur) {
  const regions = card ? card.targets.filter((t) => t.label) : [];
  const floor = 0.1;
  const lit = (i) => (montKeySel && montKeySel.shot === montSel.i
                      && montKeySel.i === i ? " on" : "");
  return `<div class="keys">${keys.map((k, i) => `
    <div class="key${lit(i)}" data-key="${i}">
      <input type="number" class="k-at" min="0" max="${dur.toFixed(2)}" step="0.1"
             value="${(+k.at).toFixed(1)}" title="${esc(lab("js.mont.key-at"))}">
      <select class="k-of" title="${esc(lab("js.mont.target"))}">
        <option value=""${k.of ? "" : " selected"}>${lab("js.mont.whole")}</option>
        ${regions.map((t) => `<option${t.label === k.of ? " selected" : ""}>${
          esc(t.label)}</option>`).join("")}
      </select>
      <input type="range" class="k-scale" min="${floor}" max="1" step="0.01"
             value="${(+k.scale).toFixed(2)}" title="${esc(lab("js.mont.key-close"))}">
      <span class="dose">${zoomPct(k.scale)}</span>
      <button class="ghost k-drop" title="${esc(lab("js.mont.key-drop"))}">✕</button>
    </div>`).join("")}
    <p class="dim">${lab("js.mont.keys-note")}</p></div>`;
}

// What the numbers come to, said the way the shot is actually watched: still, moving,
// still. It is the one readout that makes two sliders legible as one animation.
function moveNote(lead, span, dur) {
  const tail = Math.max(dur - lead - span, 0);
  return `${lab("js.mont.still")} ${lead.toFixed(1)} · ${lab("js.mont.moving")} ${
    Math.min(span, dur - lead).toFixed(1)} · ${lab("js.mont.still")} ${tail.toFixed(1)}${lab("js.s")}`;
}

function bindShotInspector() {
  const s = MONT.doc.shots[montSel.i];
  const cast = (over) => send("/shot", { method: "PUT", body: J({
    shot: montSel.i, card: s.card, move: (s.move && s.move.kind) || "",
    target: s.target || "", ...over }) });
  mq("#mont-insp").querySelectorAll("[data-pick]").forEach((el) => {
    // Picking the card that is already there is how you re-roll its move: the kind is
    // chosen off a die among the ones the card can make, and looking at the result and
    // asking for another is a normal thing to want.
    el.onclick = () => cast({ card: el.dataset.pick, move: "", target: "" });
  });
  mq("#mont-insp").querySelectorAll("[data-move]").forEach((el) => {
    // A card cannot make every move: a pan needs two regions marked on it, a zoom
    // needs one. Asking for one it cannot make leaves the move where it was, and the
    // chips redraw showing that — but a button that lights up somewhere else is a
    // button that looks broken, so it is also said.
    el.onclick = async () => {
      const want = el.dataset.move;
      const d = await cast({ move: want });
      const got = d && d.shots[montSel.i] && d.shots[montSel.i].move;
      if (got && got.kind !== want) say(`${lab("js.mont.nomove")} ${lab("mv." + got.kind, got.kind)}`, true);
    };
  });
  const tgt = mq("#i-target");
  if (tgt) tgt.onchange = () => cast({ target: tgt.value });

  // The travel, in seconds into the shot. The readout follows the finger and the run is
  // only told once it is let go — writing a whole job to the checkpoint on every pixel
  // of a drag is the same waste the filter sliders avoid.
  const lead = mq("#i-lead"), span = mq("#i-span");
  if (lead && span) {
    const dur = Math.max(s.duration, 0.1);
    const paint = () => {
      // the travel may not run past the end of the shot, and the slider that was NOT
      // touched is the one that gives way
      mq("#i-lead-v").textContent = (+lead.value).toFixed(1) + lab("js.s");
      mq("#i-span-v").textContent = (+span.value).toFixed(1) + lab("js.s");
      mq("#i-move-note").textContent = moveNote(+lead.value, +span.value, dur);
    };
    lead.oninput = span.oninput = paint;
    lead.onchange = span.onchange = () =>
      cast({ lead: +lead.value, span: +span.value });
  }

  // Watching it is the only way to judge it — a still frame cannot show a move. This
  // plays the selected shot and stops at its far edge.
  const play = mq("#i-play");
  if (play) play.onclick = () => playShot(s);

  // -- the keys, sent back whole on every change ---------------------------
  // `reset` is the row whose REGION was just picked: it sends no size at all, so the
  // server resolves the region as it was MARKED — centre and frame both. Carrying the
  // old slider value across is how picking a region used to leave the window where it
  // was and look like the pick had done nothing.
  const readKeys = (reset = -1) => [...mq("#mont-insp").querySelectorAll(".key")]
    .map((row, n) => ({
      at: +row.querySelector(".k-at").value,
      of: row.querySelector(".k-of").value,
      ...(n === reset ? {} : { scale: +row.querySelector(".k-scale").value }),
    }));
  const putKeys = (keys) => send("/shot/keys", { method: "PUT",
    body: J({ shot: montSel.i, keys }) });
  mq("#mont-insp").querySelectorAll(".key").forEach((row, i) => {
    const size = row.querySelector(".k-scale"), out = row.querySelector(".dose");
    // The row and the marker are one moment seen twice, so pointing at either lights
    // both — and the playhead goes to it, because the thing you are about to change is
    // easier to judge when the picture is showing that instant.
    row.onpointerdown = () => {
      montKeySel = { shot: montSel.i, i };
      lightKey();
      const sh = MONT.doc.shots[montSel.i];
      if (sh) seekSilently(sh.start + (+row.querySelector(".k-at").value || 0));
    };
    // the readout follows the finger; the run hears about it once it is let go
    size.oninput = () => (out.textContent = zoomPct(+size.value));
    size.onchange = row.querySelector(".k-at").onchange = () => putKeys(readKeys());
    row.querySelector(".k-of").onchange = () => putKeys(readKeys(i));
    row.querySelector(".k-drop").onclick = () =>
      putKeys(readKeys().filter((_, n) => n !== i));
  });
  // Where a new moment goes: the playhead when it is inside this shot, so "put a key
  // HERE" means what it looks like; otherwise the far end, which is where one usually
  // goes when you are not looking at a particular instant.
  const whereNow = () => {
    const t = montTime();
    return t > s.start && t < s.start + s.duration
      ? +(t - s.start).toFixed(2) : +s.duration.toFixed(2);
  };
  // Going to keys SEEDS from the preset rather than starting blank: the move you were
  // looking at is the one you want to start editing, and a two-key list is exactly
  // what a preset already is.
  const seeded = () => (s.move
    ? s.move.points.map((pt) => ({ at: pt.at, of: s.target || "", scale: pt.scale }))
    : []);
  mq("#mont-insp").querySelectorAll("[data-mode]").forEach((el) => {
    el.onclick = () => {
      const keyed = (s.move && s.move.keys || []).length >= 2;
      if (el.dataset.mode === "keys") { if (!keyed) putKeys(seeded()); }
      else if (keyed) putKeys([]);   // the preset is still underneath, untouched
    };
  });
  const addKey = mq("#i-addkey");
  // no `scale`: a fresh key on a region is that region as it was marked, and one on
  // nothing in particular is the whole picture
  if (addKey) addKey.onclick = () =>
    putKeys([...readKeys(), { at: whereNow(), of: s.target || "" }]);
  const clear = mq("#i-clear");
  if (clear) clear.onclick = () => cast({ card: "" });
  const mark = mq("#i-mark");
  if (mark) mark.onclick = () => openMarkup(MONT.doc.world, s.card);
  dropTarget(mq("#i-take"), async (file) => {
    const body = new FormData(); body.append("file", file);
    const q = `?video=${MONT.video}&shot=${montSel.i}&description=${
      encodeURIComponent((s.said || "").slice(0, 80))}`;
    const d = await send(`/shot/card${q}`, { method: "POST", body });
    // a card arrives with nothing marked on it, so it can only be held or drifted
    // across until somebody draws a region — and the operator is right here
    if (d && d.card) { say(lab("js.card-added-say-what-is-in-it")); openMarkup(d.world, d.card); }
  });
}

// The speed this line would be voiced at. A line that has never been pinned follows the
// RUN's rate, so that is where the slider starts — reading 0 on a run set to +20 and
// then pinning 0 the moment anything was voiced is the slider lying about two things at
// once.
const rateOf = (sc) =>
  (sc.rate === null || sc.rate === undefined ? (MONT.doc.rate || 0) : sc.rate);

function lineInspector() {
  const sc = MONT.doc.scenes[montSel.i];
  if (!sc) return `<p class="dim">${lab("js.mont.pickone")}</p>`;
  return `
  <div class="insp-head"><b>${lab("js.mont.line")} ${sc.i + 1}</b>
    <span class="dim">${sc.start.toFixed(1)}–${(sc.start + sc.duration).toFixed(1)}${lab("js.s")}</span>
    <span class="grow"></span>
    <button class="ghost" id="i-before">${lab("js.mont.addbefore")}</button>
    <button class="ghost" id="i-after">${lab("js.mont.addafter")}</button>
    <button class="ghost danger" id="i-drop">${lab("js.mont.dropline")}</button>
  </div>
  <p class="dim">${lab("js.mont.twohalves")}</p>
  <label>${lab("js.mont.said")}<textarea id="i-text" rows="3">${esc(draftOf(sc))}</textarea></label>
  <div class="row">
    <button class="ghost${dirty(sc) ? " unsaved" : ""}" id="i-settext">${
      lab(dirty(sc) ? "js.mont.savetext-dirty" : "js.mont.savetext")}</button>
    <span class="grow"></span>
    <label class="inline">${lab("web.f.rate")}
      <input type="range" id="i-rate" min="-50" max="50" step="5" value="${rateOf(sc)}">
      <span class="dose" id="i-rate-v">${rateOf(sc)}</span></label>
    <button class="primary" id="i-voice">${lab("js.mont.revoice")}</button>
  </div>
  <div class="take" id="i-take-voice"><span class="say">${lab("js.mont.ownvoice")}</span>
    <input type="file" hidden accept="audio/*"></div>`;
}

// Write a line's text back, if what is in the box differs from what the document holds.
// Returns once the document is current, so anything that acts on the TEXT can wait for
// it — `озвучить заново` most of all: voicing a line while an unsaved edit sat in the
// box read the old words aloud, which is the kind of thing you only notice on playback.
async function commitText(i) {
  const sc = MONT.doc.scenes[i];
  if (!sc || !dirty(sc)) { montDrafts.delete(i); return; }
  const text = montDrafts.get(i);
  await send("/text", { method: "POST", body: J({ scene: i, text }) },
             () => montDrafts.delete(i));
}

function bindLineInspector() {
  const i = montSel.i;
  const rate = mq("#i-rate"), out = mq("#i-rate-v");
  rate.oninput = () => (out.textContent = rate.value);
  const box = mq("#i-text");
  box.oninput = () => {
    montDrafts.set(i, box.value);
    const sc = MONT.doc.scenes[i];
    const save = mq("#i-settext");
    save.classList.toggle("unsaved", dirty(sc));
    save.textContent = lab(dirty(sc) ? "js.mont.savetext-dirty" : "js.mont.savetext");
  };
  // Leaving the box IS saving it. The text half of a line is the cheap half — no
  // synthesis, no LLM, the same span re-divided among the new words — so there is
  // nothing to protect the operator from, and plenty to protect them from losing.
  box.onblur = () => commitText(i);
  box.onkeydown = (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); commitText(i); }
  };
  mq("#i-settext").onclick = async () => {
    await commitText(i);
    say(lab("js.mont.textsaved"));
  };
  mq("#i-voice").onclick = async (e) => {
    e.target.disabled = true;
    await commitText(i);              // say what is written, not what was written
    say(lab("js.mont.voicing"));
    await send("/voice", { method: "POST", body: J({ scene: i, rate: +rate.value }) },
               () => { reloadVoice(); say(lab("js.mont.voiced")); });
    const back = mq("#i-voice");
    if (back) back.disabled = false;
  };
  // A new line arrives silent and is selected straight away, because the only reason
  // to add one is to write it: the box below is where the caret goes.
  // the indices below the insertion all shift, and a draft is held BY index — so
  // whatever is in the box goes back first (see `flush`)
  const add = async (after) => {
    await flush();
    send("/line", { method: "POST", body: J({ after }) },
         (d) => { montDrafts.clear(); montSel = { kind: "line", i: d.at }; });
  };
  mq("#i-before").onclick = () => add(i - 1);
  mq("#i-after").onclick = () => add(i);
  mq("#i-drop").onclick = () =>
    send(`/line?video=${MONT.video}&scene=${i}`, { method: "DELETE" },
         () => { montDrafts.clear(); montSel = null; reloadVoice(); });
  dropTarget(mq("#i-take-voice"), async (file) => {
    await commitText(i);  // the recogniser times the recording against this line's TEXT
    const body = new FormData(); body.append("file", file);
    say(lab("js.mont.aligning"));
    await send(`/voice/file?video=${MONT.video}&scene=${i}`, { method: "POST", body },
               () => { reloadVoice(); say(lab("js.mont.voiced")); });
  });
}

// One helper for both file wells: click to choose, drag to drop, the same words while
// it is in flight.
//
// The words live in a span of their own and the `<input>` is its sibling, which is not
// tidiness: writing the "sending…" line into the label's own `textContent` would take
// the file input out of the DOM with it, and the well would work exactly once.
// A <div> and not a <label>, which is the one place this differs from the well on the
// asks panel: a label wrapping the input forwards a click to it by itself, so opening
// the picker by hand on top of that is one gesture asking for two dialogues.
function dropTarget(el, send1) {
  if (!el) return;
  const input = el.querySelector("input");
  const words = el.querySelector(".say");
  const go = async (file) => {
    if (!file) return;
    const was = words.textContent;
    words.textContent = lab("js.sending");
    try { await send1(file); } catch (e) { say(e.message, true); }
    words.textContent = was;
    input.value = "";  // so the same file chosen twice still fires `change`
  };
  el.onclick = () => input.click();
  input.onchange = () => go(input.files[0]);
  ["dragover", "dragenter"].forEach((k) =>
    el.addEventListener(k, (e) => { e.preventDefault(); el.classList.add("over"); }));
  ["dragleave", "drop"].forEach((k) =>
    el.addEventListener(k, () => el.classList.remove("over")));
  el.addEventListener("drop", (e) => { e.preventDefault(); go(e.dataTransfer.files[0]); });
}

function reloadVoice() {
  // the whole track is one file and it has just changed under us; the cache buster is
  // not decoration, the server serves it `no-store` and the element still holds the old
  const a = mq("#mont-audio");
  // Nothing voiced yet is the ordinary opening state of a video made by hand, not a
  // fault: there is no track to build, so it is not asked for and the play button
  // simply has nothing to play.
  const voiced = (MONT.doc.scenes || []).some((sc) => sc.voiced);
  mq("#mont-play").disabled = !voiced;
  if (!voiced) { a.removeAttribute("src"); a.load(); return; }
  const at = a.currentTime;
  a.src = tokd(`/api/runs/${MONT.id}/montage/audio?video=${MONT.video}&v=${Date.now()}`);
  a.addEventListener("loadedmetadata", () => { a.currentTime = Math.min(at, a.duration || 0); },
                     { once: true });
}

// ---------------------------------------------------------------- the picture

function picOf(name) {
  if (!name) return null;
  if (montPics.has(name)) return montPics.get(name);
  const card = cardOf(name);
  if (!card) return null;
  let el;
  if (card.kind === "video") {
    el = document.createElement("video");
    el.muted = true; el.loop = true; el.playsInline = true; el.preload = "auto";
  } else {
    el = new Image();
  }
  el.crossOrigin = "anonymous";
  // a picture that arrives after the frame was drawn has to cause another one, or the
  // preview stays empty until something else happens to move
  el.addEventListener(card.kind === "video" ? "loadeddata" : "load", () => drawFrame());
  el.src = tokd(card.url);
  montPics.set(name, el);
  return el;
}

const lerp = (a, b, p) => a + (b - a) * p;

/** Where the crop window sits at this moment of a shot, as {cx, cy, scale}.
 *
 * Over the MOMENTS the move passes through, which the server sends whether the shot
 * carries a preset pair or a run of hand-placed keys (`KenBurns.points`). Two forms,
 * one reading — and the same one ffmpeg walks, so what the canvas shows and what the
 * render does are the same arithmetic rather than two guesses at it. */
function windowAt(shot, into) {
  const pts = shot.move && shot.move.points;
  if (!pts || !pts.length) return { cx: 0.5, cy: 0.5, scale: 1 };
  if (into <= pts[0].at) return pts[0];
  const last = pts[pts.length - 1];
  if (into >= last.at) return last;
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1];
    if (into > b.at) continue;
    const p = b.at > a.at ? (into - a.at) / (b.at - a.at) : 1;
    return { cx: lerp(a.cx, b.cx, p), cy: lerp(a.cy, b.cy, p),
             scale: lerp(a.scale, b.scale, p) };
  }
  return last;
}

// The offscreen the card is FITTED into before anything is cropped out of it — the
// same order ffmpeg works in (`media/ffmpeg.photo_filter` fits, then zoompans), which
// is what makes a crop window three numbers instead of four.
const montFit = document.createElement("canvas");

function drawFrame() {
  if (!MONT) return;
  const cv = mq("#mont-canvas");
  const ctx = cv.getContext("2d");
  const t = montTime();
  const shot = MONT.doc.shots.find((s) => t >= s.start && t < s.start + s.duration)
            || MONT.doc.shots[MONT.doc.shots.length - 1];
  mq("#mont-clock").textContent = `${clock(t)} / ${clock(MONT.doc.total)}`;
  moveHead(t);
  const pic = shot ? picOf(shot.card) : null;
  const ready = pic && (pic.tagName === "IMG" ? pic.complete && pic.naturalWidth
                                              : pic.readyState >= 2);
  mq("#mont-blank").hidden = !!ready;
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (!ready) return;
  const card = cardOf(shot.card);
  const sw = pic.naturalWidth || pic.videoWidth, sh = pic.naturalHeight || pic.videoHeight;
  montFit.width = cv.width; montFit.height = cv.height;
  const fc = montFit.getContext("2d");
  fc.clearRect(0, 0, cv.width, cv.height);
  drawFitted(fc, pic, sw, sh, cv.width, cv.height, card);
  if (pic.tagName === "VIDEO") {
    // A clip card loops to fill its shot rather than being retimed to it, so where it
    // should be is the shot's own clock folded into the clip's length.
    //
    // It PLAYS rather than being seeked frame by frame: a seek per animation frame is
    // a decode per animation frame and no browser keeps up, so the preview stuttered
    // along in third-of-a-second steps. Letting it run and nudging it only when it has
    // actually drifted is what every player does, and it costs nothing.
    const want = (t - shot.start) % (pic.duration || 1);
    const running = !mq("#mont-audio").paused || montRaf;
    if (running && pic.paused) pic.play().catch(() => { /* it will be seeked instead */ });
    else if (!running && !pic.paused) pic.pause();
    if (Math.abs(pic.currentTime - want) > 0.3) pic.currentTime = want;
  }
  const w = windowAt(shot, t - shot.start);
  const s = Math.min(Math.max(w.scale, 0.05), 1);
  paintLook(ctx, cv, montFit,
            (w.cx - s / 2) * cv.width, (w.cy - s / 2) * cv.height,
            s * cv.width, s * cv.height, t);
  drawCaption(ctx, cv, t);
}

function drawFitted(fc, pic, sw, sh, cw, ch, card) {
  const fit = card ? card.fit : "crop";
  const ax = card ? card.fit_x : 0.5, ay = card ? card.fit_y : 0.5;
  if (fit === "pad") {
    const k = Math.min(cw / sw, ch / sh);
    const w = sw * k, h = sh * k;
    fc.fillStyle = "#000";
    fc.fillRect(0, 0, cw, ch);
    fc.drawImage(pic, (cw - w) / 2, (ch - h) / 2, w, h);
    return;
  }
  const k = Math.max(cw / sw, ch / sh);
  const w = sw * k, h = sh * k;
  fc.drawImage(pic, -(w - cw) * ax, -(h - ch) * ay, w, h);
}

// ---------------------------------------------------------------- the look, sketched
//
// What the doses mean here is what they mean in `media/filters`, read off the same
// numbers — but the arithmetic is a canvas's and cannot be ffmpeg's. It is close enough
// to decide with (is this too much grain, does the tube read) and never close enough to
// judge a delivery by, which is exactly why the exact-frame button sits beside it.

const dose = (k) => ((MONT.doc.filters || {})[k] || 0) / 100;

function paintLook(ctx, cv, src, sx, sy, sw, sh, t) {
  const bw = dose("bw"), film = dose("film");
  const frame = Math.floor(t * 30);
  const flicker = film ? 1 + 0.05 * film * Math.sin(frame / 2.7) : 1;
  const parts = [];
  if (bw) parts.push(`grayscale(${bw})`, `contrast(${(1 + 0.25 * bw).toFixed(2)})`);
  if (film) parts.push(`sepia(${(0.4 * film).toFixed(2)})`,
                       `saturate(${(1 - 0.15 * film).toFixed(2)})`,
                       `brightness(${flicker.toFixed(3)})`,
                       `contrast(${(1 - 0.12 * film).toFixed(2)})`);
  const vhs = dose("vhs"), crt = dose("crt"), glitch = dose("glitch");
  if (vhs) parts.push(`blur(${(vhs * 1.4).toFixed(2)}px)`, `saturate(${1 + 0.4 * vhs})`);
  ctx.save();
  ctx.filter = parts.length ? parts.join(" ") : "none";
  ctx.drawImage(src, sx, sy, sw, sh, 0, 0, cv.width, cv.height);
  ctx.filter = "none";
  ctx.restore();

  if (dose("bloom")) {
    const d = dose("bloom");
    ctx.save();
    ctx.globalCompositeOperation = "screen";
    ctx.globalAlpha = 0.1 + 0.45 * d;
    ctx.filter = `blur(${(6 + 24 * d).toFixed(1)}px) brightness(1.2)`;
    ctx.drawImage(src, sx, sy, sw, sh, 0, 0, cv.width, cv.height);
    ctx.restore();
  }
  if (crt || glitch) splitChannels(ctx, cv, src, sx, sy, sw, sh, crt * 3 + glitch * 6);
  const vig = Math.max(dose("vignette"), film * 0.7, crt * 0.5);
  if (vig) {
    const g = ctx.createRadialGradient(cv.width / 2, cv.height / 2, cv.width * 0.25,
                                       cv.width / 2, cv.height / 2, cv.height * 0.62);
    g.addColorStop(0, "rgba(0,0,0,0)");
    g.addColorStop(1, `rgba(0,0,0,${(0.25 + 0.65 * vig).toFixed(2)})`);
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, cv.width, cv.height);
  }
  if (crt) scanlines(ctx, cv, crt, t);
  const grain = Math.max(dose("grain"), film * 0.6, vhs * 0.7);
  if (grain) noise(ctx, cv, grain, frame);
  if (glitch) tear(ctx, cv, src, sx, sy, sw, sh, glitch, frame);
}

// Red and blue pulled apart — the one thing a tube and a failing signal have in common
// and the one a plain CSS filter cannot do. Each channel is isolated on its own
// offscreen (multiply by a pure primary) and then added back, offset.
const montCh = [document.createElement("canvas"), document.createElement("canvas")];

function splitChannels(ctx, cv, src, sx, sy, sw, sh, px) {
  if (px < 0.4) return;
  ["#f00", "#00f"].forEach((tint, k) => {
    const c = montCh[k];
    c.width = cv.width; c.height = cv.height;
    const cc = c.getContext("2d");
    cc.globalCompositeOperation = "source-over";
    cc.clearRect(0, 0, c.width, c.height);
    cc.drawImage(src, sx, sy, sw, sh, 0, 0, c.width, c.height);
    cc.globalCompositeOperation = "multiply";
    cc.fillStyle = tint;
    cc.fillRect(0, 0, c.width, c.height);
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.globalAlpha = 0.28;
    ctx.drawImage(c, (k ? -px : px), 0);
    ctx.restore();
  });
}

function scanlines(ctx, cv, d, t) {
  ctx.save();
  ctx.globalAlpha = 0.12 + 0.3 * d;
  ctx.fillStyle = "#000";
  for (let y = 0; y < cv.height; y += 3) ctx.fillRect(0, y, cv.width, 1);
  ctx.restore();
  // the band of light sliding down the tube — 7s to cross, as in media/filters
  const y = ((t / 7) % 1) * (cv.height + 200) - 100;
  const g = ctx.createLinearGradient(0, y - 90, 0, y + 90);
  g.addColorStop(0, "rgba(255,255,255,0)");
  g.addColorStop(0.5, `rgba(255,255,255,${(0.04 + 0.1 * d).toFixed(3)})`);
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, y - 90, cv.width, 180);
}

// One tile of noise, regenerated a few times a second and scrolled, rather than a
// full-frame ImageData every frame — the second is honest and costs more than the
// preview is worth, and moving grain is moving grain.
let montNoise = null, montNoiseAt = -1;

function noise(ctx, cv, d, frame) {
  const n = Math.floor(frame / 2);
  if (!montNoise || montNoiseAt !== n) {
    montNoise = montNoise || document.createElement("canvas");
    montNoise.width = montNoise.height = 128;
    const nc = montNoise.getContext("2d");
    const img = nc.createImageData(128, 128);
    for (let i = 0; i < img.data.length; i += 4) {
      const v = 120 + Math.random() * 135;
      img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
      img.data[i + 3] = 255;
    }
    nc.putImageData(img, 0, 0);
    montNoiseAt = n;
  }
  ctx.save();
  ctx.globalCompositeOperation = "overlay";
  ctx.globalAlpha = 0.06 + 0.4 * d;
  const p = ctx.createPattern(montNoise, "repeat");
  ctx.fillStyle = p;
  ctx.fillRect(0, 0, cv.width, cv.height);
  ctx.restore();
}

function tear(ctx, cv, src, sx, sy, sw, sh, d, frame) {
  // The signal fails in bursts, not continuously: a tear every so many frames, and the
  // dose decides how often and how far. Seeded off the frame so the same moment tears
  // the same way twice — a preview that reshuffles while you look at it is unreadable.
  const period = Math.max(4, Math.round(40 - 30 * d));
  if (frame % period > 2) return;
  const rows = 2 + Math.round(6 * d);
  for (let i = 0; i < rows; i++) {
    const seed = Math.sin((frame + i * 31) * 12.9898) * 43758.5453;
    const r = seed - Math.floor(seed);
    const y = Math.floor(r * cv.height);
    const h = 6 + Math.floor(r * 40 * d);
    const dx = (r - 0.5) * cv.width * 0.3 * d;
    ctx.drawImage(src, sx, sy + (y / cv.height) * sh, sw, (h / cv.height) * sh,
                  dx, y, cv.width, h);
  }
}

// The burned-in caption, approximated: one word at a time, which is the shipped default
// (`word_pop`). It is here to show SYNC and nothing else — the real one is drawn by
// libass in the font and colours the run's subtitle style names.
function drawCaption(ctx, cv, t) {
  let word = null;
  for (const sc of MONT.doc.scenes) {
    for (const w of sc.words) if (t >= w.start && t < w.end + 0.12) word = w.t;
  }
  if (!word) return;
  ctx.save();
  ctx.font = `700 ${Math.round(cv.width * 0.115)}px system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineWidth = cv.width * 0.014;
  ctx.strokeStyle = "#000";
  ctx.fillStyle = "#fff";
  const y = cv.height * 0.745;
  ctx.strokeText(word.toUpperCase(), cv.width / 2, y);
  ctx.fillText(word.toUpperCase(), cv.width / 2, y);
  ctx.restore();
}

// ---------------------------------------------------------------- transport

function moveHead(t) {
  const head = mq("#mont-head");
  head.hidden = false;
  head.style.left = X(t) + "px";
  const box = mq("#mont-time");
  const x = X(t);
  if (x < box.scrollLeft + 60 || x > box.scrollLeft + box.clientWidth - 60)
    box.scrollLeft = Math.max(0, x - box.clientWidth * 0.4);
}

function seek(t) {
  const a = mq("#mont-audio");
  a.currentTime = Math.max(0, Math.min(t, MONT.doc.total));
  drawFrame();
}

// The same, for a preview running off its own timer rather than off the voice: setting
// `currentTime` on an element with nothing loaded is harmless, and it is what `montTime`
// reads, so the picture and the playhead stay one clock either way.
const seekSilently = (t) => {
  mq("#mont-audio").currentTime = Math.max(0, Math.min(t, MONT.doc.total));
  drawFrame();
};

// Playing ONE shot: the clock is the whole video's, so the end is a moment to stop at
// rather than a different track to play.
let montPlayUntil = null;

function playShot(shot) {
  const a = mq("#mont-audio");
  // Decided up front, not by waiting for a refusal: `play()` on an element with
  // nothing loaded does not reject, it simply never settles — so a `catch` as the
  // fallback is a button that goes quiet forever. A move is judged by watching it, and
  // watching it does not need the sound, so where there is no clock to run off, the
  // picture is stepped through on a timer of its own.
  if (a.readyState < 2) return stepThrough(shot);
  montPlayUntil = shot.start + shot.duration;
  a.currentTime = shot.start;
  a.play().catch(() => { montPlayUntil = null; stepThrough(shot); });
}

// The preview without an audio clock: a plain timer over the shot's own seconds.
function stepThrough(shot) {
  cancelAnimationFrame(montRaf);
  const began = performance.now();
  const step = () => {
    const at = shot.start + (performance.now() - began) / 1000;
    if (at >= shot.start + shot.duration) { seek(shot.start); return; }
    seekSilently(at);
    montRaf = requestAnimationFrame(step);
  };
  step();
}

function tick() {
  if (montPlayUntil !== null && montTime() >= montPlayUntil) {
    montPlayUntil = null;
    mq("#mont-audio").pause();
    return;
  }
  drawFrame();
  montRaf = requestAnimationFrame(tick);
}

function bindTransport() {
  const a = mq("#mont-audio");
  // `play()` is a promise and it is allowed to say no — a track still being fetched, a
  // tab the browser has not decided is worth making a noise. Swallowing that leaves a
  // button that visibly does nothing; it is the one thing on this screen that has to
  // work every time.
  const toggle = () => {
    montPlayUntil = null;   // the transport plays the video, not one shot of it
    return a.paused ? a.play().catch((e) => say(e.message, true)) : a.pause();
  };
  mq("#mont-play").onclick = toggle;
  // Moved without playing — a click on the ruler, a seek from an edit — still has to
  // repaint: the canvas is only driven by the animation frame while the track is
  // running, and outside that the picture would sit on whatever second it stopped at.
  a.onseeked = () => drawFrame();
  a.onloadedmetadata = () => drawFrame();
  // Without a voice track there is no clock to run, but there is still a montage: the
  // seconds are the server's and the ruler is drawn from them, so scrubbing and cutting
  // go on working. Say what is missing rather than leaving a play button that does
  // nothing — a track that failed to build and a track that is merely quiet look the
  // same from here.
  a.onerror = () => {
    mq("#mont-play").disabled = true;
    say(lab("js.mont.noaudio"), true);
  };
  a.onplay = () => { mq("#mont-play").textContent = "⏸"; cancelAnimationFrame(montRaf); tick(); };
  a.onpause = () => { mq("#mont-play").textContent = "▶"; cancelAnimationFrame(montRaf); drawFrame(); };
  a.onended = () => { mq("#mont-play").textContent = "▶"; cancelAnimationFrame(montRaf); };
  mq("#mont-zoom").oninput = () => { montPPS = +mq("#mont-zoom").value; renderMont(); };
  // Space plays. It is the one shortcut this screen genuinely needs — you press it a
  // hundred times an hour — and it stays out of the way of anything being typed.
  document.addEventListener("keydown", (e) => {
    if (mq("#mont").hidden || e.code !== "Space") return;
    const el = document.activeElement;
    if (el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) return;
    e.preventDefault();
    a.paused ? a.play() : a.pause();
  });
  mq("#mont-frame").onclick = async () => {
    const img = mq("#mont-true");
    const btn = mq("#mont-frame");
    btn.disabled = true;
    img.src = tokd(`/api/runs/${MONT.id}/montage/still?video=${MONT.video}&at=${montTime().toFixed(3)}&v=${Date.now()}`);
    img.hidden = false;
    img.onload = img.onerror = () => { btn.disabled = false; };
    // it is a comparison, so it goes away on the next touch of anything
    img.onclick = () => { img.hidden = true; };
  };
}

// ---------------------------------------------------------------- the look, for real

function buildFxRows() {
  const box = mq("#mont-fx-rows");
  box.innerHTML = (opts.filters || []).map((f) => `
    <div class="slider" title="${esc(f.note)}">
      <div class="top"><span>${esc(lab("fx." + f.key, f.key))}</span>
        <span class="grow"></span><span class="dose">${(MONT.doc.filters || {})[f.key] || 0}</span></div>
      <input type="range" data-fx="${esc(f.key)}" min="0" max="100" step="5"
             value="${(MONT.doc.filters || {})[f.key] || 0}">
    </div>`).join("");
  box.querySelectorAll("[data-fx]").forEach((r) => {
    const out = r.previousElementSibling.querySelector(".dose");
    // the sketch follows the finger; the run is only told once it is let go, because
    // writing the checkpoint on every pixel of a drag is forty writes of a whole job
    r.oninput = () => {
      out.textContent = r.value;
      MONT.doc.filters = { ...MONT.doc.filters, [r.dataset.fx]: +r.value };
      drawFrame();
    };
    r.onchange = saveFilters;
  });
}

async function saveFilters() {
  const spec = {};
  mq("#mont-fx-rows").querySelectorAll("[data-fx]").forEach((r) => {
    if (+r.value > 0) spec[r.dataset.fx] = +r.value;
  });
  const state = mq("#mont-fx-state");
  state.textContent = lab("js.saving");
  try {
    MONT.doc = await api(`/api/runs/${MONT.id}/montage/settings`, { method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ video: MONT.video, filters: spec }) });
    state.textContent = lab("js.mont.fxsaved");
  } catch (e) { state.textContent = ""; say(e.message, true); }
}

// ------------------------------------------------------- the run's settings
//
// Every stage of the chain is a button on the rail at the top of this room, and a
// stage reads the RUN's settings. So the voice `озвучка` will use, the style
// `субтитры` will write and the switch `описание` asks about all have to be reachable
// from in here: a run built by hand otherwise keeps half its settings on a form it
// left long ago, and changing one of them means building the run again.
//
// A sheet rather than another block in the left column, because these are read once
// and set once while that column is for the things you keep touching — the look, and
// the frame it draws. Each control commits on its own, the way everything in this room
// does; «готово» only closes the sheet.
//
// What is NOT here is as deliberate: the writer's settings (the brief, the world, how
// far it may invent, how much swearing) are not shown, because `сценарий` throws away
// every line when pressed and a room whose whole point is hand-made lines is the wrong
// place to make that inviting.
const SETTINGS = [
  {
    title: "web.card.voice",
    rows: [
      { f: "tts_engine", kind: "select", opts: "tts_engines", l: "web.f.engine" },
      { f: "voice_override", kind: "select", opts: "cloned_voices", l: "web.f.clone" },
      { f: "tts_rate", kind: "range", min: -50, max: 50, step: 5, l: "web.f.rate" },
      { f: "tts_source", kind: "flag", on: "manual", off: "engine",
        l: "web.f.ttsmanual", note: "web.ttsmanual.note" },
    ],
  },
  {
    title: "web.card.subs",
    rows: [
      { f: "subtitle_style", kind: "select", opts: "subtitle_styles", l: "web.f.style" },
      { f: "clean_subtitles", kind: "check", l: "web.f.clean" },
    ],
  },
  {
    title: "web.mont.settings.out",
    rows: [
      { f: "write_metadata", kind: "check", l: "web.f.meta", note: "web.f.meta.note" },
      { f: "push", kind: "select", opts: "accounts", l: "web.f.publish" },
      { f: "dry_run", kind: "check", l: "web.f.dry" },
      { f: "keep_temp", kind: "check", l: "web.f.keeptmp" },
    ],
  },
];

function settingRow(row, cur) {
  const v = cur[row.f];
  const l = esc(lab(row.l));
  const note = row.note ? `<p class="dim">${esc(lab(row.note))}</p>` : "";
  if (row.kind === "select") {
    const list = ((opts && opts[row.opts]) || [])
      .map((x) => (x && x.v !== undefined ? x.v : x));
    // A value the list does not offer is still the run's answer — a catalogue voice
    // named on the command line, an account since renamed — and a select that quietly
    // dropped it would rewrite that setting the moment anything else here was saved.
    const all = v && !list.includes(v) ? list.concat([v]) : list;
    const options = [""].concat(all).map((x) =>
      `<option value="${esc(x)}"${x === (v || "") ? " selected" : ""}>${
        esc(x ? word(x) : lab("w.none", "— нет —"))}</option>`).join("");
    return `<label class="setrow"><span>${l}</span>
      <select data-set="${esc(row.f)}">${options}</select></label>${note}`;
  }
  if (row.kind === "range") {
    const n = v || 0;
    return `<div class="slider">
      <div class="top"><span>${l}</span><span class="grow"></span>
        <span class="dose">${esc(String(n))}</span></div>
      <input type="range" data-set="${esc(row.f)}" min="${row.min}" max="${row.max}"
             step="${row.step}" value="${esc(String(n))}"></div>${note}`;
  }
  // a checkbox, either over a bool or over a pair of words (`tts_source`)
  const on = row.on ? v === row.on : !!v;
  const pair = row.on ? ` data-on="${esc(row.on)}" data-off="${esc(row.off)}"` : "";
  return `<label class="inline"><input type="checkbox" data-set="${esc(row.f)}"${pair}${
    on ? " checked" : ""}><span>${l}</span></label>${note}`;
}

function renderSettings() {
  const cur = MONT.doc.settings || {};
  const box = mq("#set-rows");
  box.innerHTML = SETTINGS.map((g) =>
    `<section><b>${esc(lab(g.title))}</b>${
      g.rows.map((r) => settingRow(r, cur)).join("")}</section>`).join("");
  box.querySelectorAll("[data-set]").forEach((el) => {
    const f = el.dataset.set;
    if (el.type === "range") {
      // the number follows the finger; the run is told once it is let go, because
      // every step of a drag would be a whole checkpoint written
      const dose = el.previousElementSibling.querySelector(".dose");
      el.oninput = () => { dose.textContent = el.value; };
      el.onchange = () => setOne(f, +el.value);
    } else if (el.type === "checkbox") {
      el.onchange = () => setOne(f, el.dataset.on
        ? (el.checked ? el.dataset.on : el.dataset.off) : el.checked);
    } else {
      el.onchange = () => setOne(f, el.value);
    }
  });
}

// One control, one request — and the whole document back, because a setting is the
// run's and the room draws the run. A refused value (an engine that is not installed,
// an account that is gone) leaves the document alone, and the redraw below puts the
// control back to what the run actually says rather than leaving a lie on the screen.
async function setOne(field, value) {
  const state = mq("#set-state");
  state.textContent = lab("js.saving");
  const d = await send("/settings", { method: "PUT", body: J({ [field]: value }) });
  state.textContent = d ? lab("js.mont.set-saved") : "";
  renderSettings();
}

function openSettings() {
  mq("#set-state").textContent = "";
  renderSettings();
  mq("#mont-set").hidden = false;
}

// ---------------------------------------------------------------- leaving

function bindMontage() {
  bindTransport();
  // whatever is standing open over the room takes Escape first, and only an empty
  // room hands it to the door
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || mq("#mont").hidden) return;
    if (!mq("#mont-set").hidden) mq("#set-close").click();
    else if (mq("#mont-ask").hidden) mq("#mont-close").click();
  });
  mq("#mont-settings").onclick = openSettings;
  mq("#set-close").onclick = () => { mq("#mont-set").hidden = true; };
  // the backdrop, but not the sheet standing on it
  mq("#mont-set").onclick = (e) => {
    if (e.target === mq("#mont-set")) mq("#mont-set").hidden = true;
  };
  mq("#mont-close").onclick = () => {
    mq("#mont-set").hidden = true;
    mq("#mont-audio").pause();
    cancelAnimationFrame(montRaf);
    mq("#mont").hidden = true;
    MONT = null;
    loadRuns();
  };
  mq("#mont-recut").onclick = async () => {
    const cast = (MONT.doc.shots || []).filter((s) => s.card).length;
    if (!await ask({
      title: lab("web.mont.recut"), what: lab("js.mont.recut-sure"),
      lines: cast ? [{ text: `${lab("js.mont.redo.cards")} ${cast}`, cls: "warn" }] : [],
      ok: lab("js.mont.go"), danger: !!cast })) return;
    send("/recut", { method: "POST", body: J({ sensitivity: MONT.doc.sensitivity }) },
         () => { montSel = null; say(lab("js.mont.recut-done")); });
  };
  mq("#mont-apply").onclick = async () => {
    const left = MONT.doc.blocking || [];
    if (left.length && !await ask({
      title: lab("web.mont.apply"), what: lab("js.mont.blocked"),
      lines: left.map((b) => ({ text: `${lab("js.mont.left." + b.what)} ${b.n}`,
                                cls: "warn" })),
      ok: lab("js.mont.go"), danger: true })) return;
    let out;
    try {
      out = await api(`/api/runs/${MONT.id}/montage/apply`, { method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ video: MONT.video, anyway: left.length > 0 }) });
    } catch (e) { return say(e.message, true); }
    // the button says "and go on", so it goes on — the same bargain the breakpoint
    // panel's apply makes
    if (out.run_dir) {
      await api(`/api/runs/${MONT.id}/resume`, { method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ run_dir: out.run_dir }) });
    }
    mq("#mont-audio").pause();
    cancelAnimationFrame(montRaf);
    mq("#mont").hidden = true;
    MONT = null;
    say(lab("js.changes-applied-the-run-goes-on"));
    loadRuns();
  };
}

bindMontage();

// The card markup editor opens OVER this screen (a shot's `разметить`, and a picture
// handed over for a stretch that had none), and what it edits is the regions a crop
// move converges on. So the document is re-read on the way back: a region drawn while
// the montage was open changes what the moves here can do, and a screen still holding
// the card as it was before would offer a zoom the card can now make and not show it.
document.addEventListener("DOMContentLoaded", () => {
  const back = mq("#close");
  if (back) back.addEventListener("click", () => {
    if (!mq("#mont").hidden && MONT) openMontage(MONT.id, MONT.title, MONT.video);
  });
});
