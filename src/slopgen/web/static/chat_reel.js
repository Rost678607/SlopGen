// The chat room's reel: the video played in the page instead of encoded.
//
// The server hands over what the render stage drew (`pipeline/chat_reel.py`): the
// states, each a picture and a stretch of the clock with the window travelling down
// it or across a seam; the moving things laid on them as clips with alpha; the bar
// that does not scroll; the loop under a split screen; and every sound with the
// moment it goes off. What ffmpeg does with those in `make_chat_part` — crop, ramp,
// overlay — is done here on a canvas, with the same numbers, so the reel moves the
// way the video will and not the way a second animation thought it should.
//
// It is the room's picture whenever there is one that answers for the conversation
// on the screen. Every edit makes it answer for something that no longer exists, so
// an edit drops it and asks for the next; until that arrives the room shows its
// stills, which are drawn fresh and are therefore true.

let REEL = null;          // the reel being shown, or null
let reelRev = 0;          // bumped on every change to the conversation
let reelWanted = 0;       // the rev the last request was made for
let reelTimer = 0;
let reelBusy = false;

// -- getting one ----------------------------------------------------------------

// The conversation changed: the reel on screen is of something else now. Asked for
// again after a breath, so that typing a line and changing who sent it is one build
// and not two.
function reelStale() {
  reelRev++;
  if (REEL) {
    reelSilence();
    REEL = null;
    reelShowing();
  }
  clearTimeout(reelTimer);
  if (CHAT) reelTimer = setTimeout(reelLoad, 700);
  reelSay();
}

async function reelLoad() {
  reelTimer = 0;
  if (!CHAT || reelBusy) return;
  if (!chatConvs().some((c) => c.lines)) return reelSay();
  reelBusy = true;
  reelWanted = reelRev;
  const id = CHAT.id;
  reelSay();
  let got = null;
  try {
    got = await api(`/api/runs/${id}/chat/reel?video=${CHAT.video}`);
  } catch (e) {
    say(e.message, true);
  }
  reelBusy = false;
  if (!CHAT || CHAT.id !== id) return;
  // edited while it was being drawn: what came back is of the conversation before
  if (reelWanted !== reelRev) return reelLoad();
  if (!got) return reelSay();
  REEL = got;
  REEL.imgs = new Map();
  REEL.clips = new Map();
  reelPrimeSound();
  reelShowing();
  reelBoxSetup();
  reelSay();
  // the reel's clock is now the room's, and the rail is laid out on it
  drawRail();
  if (chatPlaying && !chatWatching) reelSound(chatT);
  reelDraw(chatT);
}

// The reel again, for the same conversation with more of it voiced: the takes a
// running voicing has made so far (`chat_reel.timeline` reads them off disk). Not a
// stale reel — the old one plays on until the new one is drawn, then gives way at
// the same moment of the video — and not more often than every few seconds, since
// each one is a drawing on a machine the voice model is already busy on.
let reelRefreshAt = 0;
let reelRefreshTimer = 0;

function reelRefresh() {
  if (!REEL) return;   // nothing on screen yet: the ordinary load is already coming
  const wait = 8000 - (performance.now() - reelRefreshAt);
  clearTimeout(reelRefreshTimer);
  if (wait > 0 || reelBusy) {
    reelRefreshTimer = setTimeout(reelRefresh, Math.max(wait, 1000));
    return;
  }
  reelRefreshAt = performance.now();
  reelLoad();
}

// What the reel is doing, beside the clock: the one wait in this room that is long
// enough to wonder about.
function reelSay() {
  const el = cq("#chat-reel-state");
  if (!el) return;
  el.textContent = REEL ? "" : (reelBusy || reelTimer ? lab("web.chat.reel.busy", "") : "");
  el.hidden = !el.textContent;
}

// The canvas is the picture while there is a reel and the cut is not playing.
function reelShowing() {
  const on = !!REEL && !chatWatching;
  if (reelBox) requestAnimationFrame(() => reelBox.place());
  cq("#chat-reel").hidden = !on;
  cq("#chat-shot-img").hidden = on || chatWatching;
  if (on) {
    const c = cq("#chat-reel");
    if (c.width !== REEL.width) c.width = REEL.width;
    if (c.height !== REEL.height) c.height = REEL.height;
  } else {
    for (const v of (REEL ? REEL.clips.values() : [])) v.pause();
  }
}

// -- drawing one instant ------------------------------------------------------------

function reelStateAt(t) {
  const s = REEL.states;
  let lo = 0, hi = s.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (s[mid].start <= t + 1e-6) lo = mid; else hi = mid - 1;
  }
  return lo;
}

// A picture, fetched once and kept while it is near the playhead. A hundred states
// of a phone screen decoded at full size is a gigabyte, so the ones far behind are
// let go; they come back from the browser's cache if the head returns to them.
function reelImg(src) {
  let im = REEL.imgs.get(src);
  if (!im) {
    im = new Image();
    im.decoding = "async";
    im.onload = reelRedraw;
    im.src = tokd(src);
    REEL.imgs.set(src, im);
    if (REEL.imgs.size > 40) REEL.imgs.delete(REEL.imgs.keys().next().value);
  } else {
    // most recently wanted goes to the back of the queue to be let go
    REEL.imgs.delete(src);
    REEL.imgs.set(src, im);
  }
  return im;
}
const reelReady = (im) => im && im.complete && im.naturalWidth > 0;

// Something the still frame was waiting on has arrived — a picture, or a clip that has
// finished seeking to where the head is. Playing, the next frame picks it up anyway.
function reelRedraw() {
  if (REEL && !chatPlaying) reelDraw(chatT);
}

// A moving thing's clip, one element per clip AND per moment it was born: the same
// sticker sent twice is two stickers, each on its own clock (see `chat_render._tiles`).
function reelClip(src, key) {
  let v = REEL.clips.get(key);
  if (!v) {
    v = document.createElement("video");
    v.muted = true;
    v.loop = true;
    v.playsInline = true;
    v.preload = "auto";
    v.addEventListener("loadeddata", reelRedraw);
    v.addEventListener("seeked", reelRedraw);
    v.src = tokd(src);
    REEL.clips.set(key, v);
  }
  return v;
}

// Hold a clip to the moment it should be at. While playing it runs on its own and is
// only pulled back when it drifts; while still, it is put exactly there.
function reelHold(v, want, playing) {
  if (!(v.duration > 0)) return;
  want = ((want % v.duration) + v.duration) % v.duration;
  if (playing) {
    if (v.paused) v.play().catch(() => {});
    const off = Math.abs(v.currentTime - want);
    if (off > 0.25 && off < v.duration - 0.25) v.currentTime = want;
  } else {
    if (!v.paused) v.pause();
    if (Math.abs(v.currentTime - want) > 0.04) v.currentTime = want;
  }
}

function reelDraw(t) {
  if (!REEL || chatWatching) return;
  const c = cq("#chat-reel");
  const g = c.getContext("2d");
  const W = REEL.width, H = REEL.height, top = REEL.top;
  const k0 = reelStateAt(t);
  // the next few, so that a message never waits on its own picture
  for (let k = k0 + 1; k < Math.min(k0 + 6, REEL.states.length); k++)
    reelImg(REEL.states[k].src);
  // The state that is up, or — while its picture is still on its way — the nearest
  // one before it that has arrived: a held frame reads as a hitch, a black one as a
  // cut.
  let k = k0;
  while (k > 0 && !reelReady(reelImg(REEL.states[k].src))) k--;
  const st = REEL.states[k];
  const im = reelImg(st.src);
  if (!reelReady(im)) return;
  const used = new Set();

  const local = t - st.start;
  if (st.ad) return reelDrawAd(g, st, im, local, t, used);
  // The window, by `make_chat_part`'s arithmetic: a straight ramp over `travel`
  // seconds from where the state begins, clamped to the picture padded out to at
  // least one screen.
  const r = st.travel > 0 ? Math.min(Math.max(local / st.travel, 0), 1) : 1;
  const iw = Math.max(im.naturalWidth, W), ih = Math.max(im.naturalHeight, top);
  let x = 0, y;
  if (st.swipe) {
    x = Math.min(Math.max(W * r, 0), iw - W);
    y = 0;
  } else {
    y = Math.abs(st.to_y - st.from_y) > 0.5 ? st.from_y + (st.to_y - st.from_y) * r : st.from_y;
    y = Math.min(Math.max(y, 0), ih - top);
  }
  g.fillStyle = "#000";
  g.fillRect(0, 0, W, H);
  g.save();
  g.beginPath();
  g.rect(0, 0, W, top);
  g.clip();
  // the wallpaper stays where the screen is; the conversation travels over it
  if (st.ground) {
    const under = reelImg(st.ground);
    if (reelReady(under)) g.drawImage(under, 0, 0);
  }
  g.drawImage(im, -x, -y);
  for (const tile of st.tiles) {
    const at = [tile.x - x, tile.y - y, tile.w, tile.h];
    if (tile.length > 0) {
      const key = `${tile.src}|${tile.t0}`;
      const v = reelClip(tile.src, key);
      used.add(key);
      reelHold(v, t - tile.t0, chatPlaying);
      if (v.readyState >= 2) g.drawImage(v, ...at);
      else if (tile.poster && reelReady(reelImg(tile.poster))) g.drawImage(reelImg(tile.poster), ...at);
    } else {
      const still = reelImg(tile.src);
      if (reelReady(still)) g.drawImage(still, ...at);
    }
  }
  if (st.overlay) {
    const bar = reelImg(st.overlay);
    if (reelReady(bar)) g.drawImage(bar, 0, 0);
  }
  g.restore();

  reelUnder(g, t, used);
  reelFinish(g, t, used);
}

// the loop under a split screen, cropped to fill its strip as the assembler does
function reelUnder(g, t, used) {
  const W = REEL.width, H = REEL.height, top = REEL.top;
  if (top < H) {
    const u = REEL.under.find((p) => p.at <= t + 1e-6 && t < p.at + p.dur) || REEL.under[REEL.under.length - 1];
    if (u) {
      const key = `under|${u.src}`;
      const v = reelClip(u.src, key);
      used.add(key);
      reelHold(v, u.seek + (t - u.at), chatPlaying);
      if (v.readyState >= 2) {
        const sh = H - top;
        const s = Math.max(W / v.videoWidth, sh / v.videoHeight);
        const sw = W / s, shh = sh / s;
        g.drawImage(v, (v.videoWidth - sw) / 2, (v.videoHeight - shh) / 2, sw, shh, 0, top, W, sh);
      }
    }
  }
}

// What goes over everything, and the bookkeeping after: the subtitles, then the
// banner on top of them as the delivery pass lays it, then every clip that is not on
// screen stopped.
function reelFinish(g, t, used) {
  reelSubs(g, t);
  reelBanner(g, t, used);
  for (const [key, v] of REEL.clips) if (!used.has(key) && !v.paused) v.pause();
}

// The native ad between two chats, by `make_chat_part`'s arithmetic for it: the old
// screen, the clip and the new screen side by side, and the window swiping across the
// first join, holding on the clip, swiping across the second.
function reelDrawAd(g, st, pair, local, t, used) {
  const W = REEL.width, H = REEL.height, top = REEL.top;
  const sw = Math.min(Math.max(st.travel, 1e-3), st.dur / 2);
  let x;
  if (local < sw) x = W * (local / sw);
  else if (local < st.dur - sw) x = W;
  else x = W + W * Math.min((local - (st.dur - sw)) / sw, 1);
  const key = `ad|${st.ad}|${st.start}`;
  const v = reelClip(st.ad, key);
  used.add(key);
  reelHold(v, local, chatPlaying);
  g.fillStyle = "#000";
  g.fillRect(0, 0, W, H);
  g.save();
  g.beginPath();
  g.rect(0, 0, W, top);
  g.clip();
  g.drawImage(pair, 0, 0, W, top, -x, 0, W, top);
  if (v.readyState >= 2) {
    // cover the chat's window, as `scale…increase,crop` does
    const s = Math.max(W / v.videoWidth, top / v.videoHeight);
    const sw2 = W / s, sh2 = top / s;
    g.drawImage(v, (v.videoWidth - sw2) / 2, (v.videoHeight - sh2) / 2, sw2, sh2, W - x, 0, W, top);
  }
  g.drawImage(pair, W, 0, W, top, 2 * W - x, 0, W, top);
  g.restore();
  reelUnder(g, t, used);
  reelFinish(g, t, used);
}

// -- the banner ------------------------------------------------------------------
//
// Where it goes is the run's (`RunParams.ad_box`), dragged into place on this very
// frame; the reel knows only where it falls back to. Drawn in its window of the clock,
// and whenever it is being moved, so that it can be placed from any moment.
let reelBoxBusy = false;
let reelBox = null;   // the draggable box over the picture (see `boxEditor`)

// The banner's box over the room's picture: dragged, it moves the banner on the canvas
// as it goes; let go, it is written onto the run — straight to the route and not
// through `chatDo`, because a new place for the banner changes nothing in the
// conversation and must not throw the reel away.
function reelBoxSetup() {
  if (reelBox) return reelBox.place();
  const frame = cq("#chat-frame");
  reelBox = boxEditor(frame, {
    rect: () => {
      const c = cq("#chat-reel");
      if (!REEL || !REEL.overlay || chatWatching || c.hidden) return null;
      // the canvas is fitted into its box (`object-fit: contain`), so the frame is
      // the fitted rectangle inside it and not the element's own
      const fr = frame.getBoundingClientRect(), cr = c.getBoundingClientRect();
      const s = Math.min(cr.width / REEL.width, cr.height / REEL.height);
      const w = REEL.width * s, h = REEL.height * s;
      return { left: cr.left - fr.left + (cr.width - w) / 2,
               top: cr.top - fr.top + (cr.height - h) / 2, width: w, height: h };
    },
    aspect: () => REEL.overlay.aspect * REEL.width / REEL.height,
    get: () => reelBoxNow(),
    set: (b) => { CHAT.doc.run_settings.ad_box = b; reelBoxBusy = true; reelDraw(chatT); },
    commit: (b) => { reelBoxBusy = false; reelBoxSave(b); },
    reset: () => { CHAT.doc.run_settings.ad_box = ""; reelBoxSave(""); },
  });
  new ResizeObserver(() => reelBox.place()).observe(cq("#chat-reel"));
  reelBox.place();
}

async function reelBoxSave(b) {
  try {
    const d = await api(`/api/runs/${CHAT.id}/chat/settings`, {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ video: CHAT.video, ad_box: b }),
    });
    CHAT.doc.run_settings = d.run_settings;
    CHAT.doc.completed = d.completed;
    renderChatStages();
  } catch (e) { say(e.message, true); }
  reelDraw(chatT);
  reelBox.place();
}

function reelBoxNow() {
  const ov = REEL && REEL.overlay;
  if (!ov) return null;
  const set = (CHAT.doc.run_settings || {}).ad_box;
  return set && typeof set === "object" ? set : ov.default;
}

function reelBanner(g, t, used) {
  const ov = REEL.overlay;
  if (!ov) return;
  const on = (t >= ov.start && t <= ov.start + ov.dur) || reelBoxBusy;
  if (!on) return;
  const b = reelBoxNow();
  const W = REEL.width, H = REEL.height;
  const w = b.w * W, h = w * ov.aspect, x = b.x * W, y = b.y * H;
  let pic = null;
  if (ov.video) {
    const key = `banner|${ov.src}`;
    pic = reelClip(ov.src, key);
    used.add(key);
    reelHold(pic, t - ov.start, chatPlaying);
    if (pic.readyState < 2) pic = null;
  } else {
    pic = reelImg(ov.src);
    if (!reelReady(pic)) pic = null;
  }
  if (pic) g.drawImage(pic, x, y, w, h);
  if (ov.text) {
    // under the banner, or over it when it sits too low — as `ffmpeg.finalize` does
    g.save();
    g.font = `44px "DejaVu Sans", sans-serif`;
    g.textBaseline = "top";
    g.lineJoin = "round";
    g.lineWidth = 6;
    g.strokeStyle = "#000";
    g.fillStyle = "#fff";
    const below = y + h + 14;
    const ty = below + 60 < H ? below : Math.max(y - 58, 0);
    g.strokeText(ov.text, x, ty);
    g.fillText(ov.text, x, ty);
    g.restore();
  }
}

// -- the subtitles -------------------------------------------------------------------
//
// Drawn as libass would burn the stage's ASS (`stages/subtitles.py`): the same places,
// the same sizes, an outline and a soft shadow, the popping word and the karaoke
// sweep. Close rather than identical — a browser's font engine is not libass — but
// what they say, where and when, is the video's.

// `&HAABBGGRR`, where AA is TRANSPARENCY, as a CSS colour
function assColour(c) {
  const h = String(c || "").replace(/^&H/i, "").padStart(8, "0");
  const a = 1 - parseInt(h.slice(0, 2), 16) / 255;
  const [b, g, r] = [h.slice(2, 4), h.slice(4, 6), h.slice(6, 8)].map((x) => parseInt(x, 16));
  return `rgba(${r},${g},${b},${a.toFixed(3)})`;
}

function reelSubs(g, t) {
  const sb = REEL.subs;
  if (!sb || !sb.events.length) return;
  const ev = sb.events.find((e) => e.s <= t && t < e.e);
  if (!ev) return;
  const pop = sb.style === "word_pop";
  // ASS sizes a font by its whole height, ascender to descender; a canvas by its em
  const size = (pop ? sb.size : Math.round(sb.size * 0.62)) * 0.86;
  const outline = pop ? sb.outline : Math.max(sb.outline - 3, 2);
  const W = REEL.width;
  let x, y, scale = 1;
  if (pop) {
    // \an5\pos(540,1430): centred on that point, popping from 70% past 106% to 100%
    x = 540; y = 1430;
    const ms = (t - ev.s) * 1000;
    scale = ms < 110 ? 0.70 + 0.36 * (ms / 110) : (ms < 220 ? 1.06 - 0.06 * ((ms - 110) / 110) : 1);
  } else {
    // bottom centre, 320 up from the foot of the frame
    x = W / 2; y = REEL.height - 320;
  }
  g.save();
  g.translate(x, y);
  g.scale(scale, scale);
  g.font = `bold ${size}px "${sb.font}", "DejaVu Sans", sans-serif`;
  g.textBaseline = pop ? "middle" : "alphabetic";
  g.lineJoin = "round";
  // the pieces of the line and the colour each is in right now: one piece for a
  // word or a phrase, one per word for karaoke, which turns each word from the
  // accent to the main colour as it is spoken (ASS's `\k`)
  const pieces = ev.words
    ? ev.words.map((w) => [w.text, t >= w.s ? sb.primary : sb.accent])
    : [[ev.text, ev.accent ? sb.accent : sb.primary]];
  const gap = g.measureText(" ").width;
  const widths = pieces.map(([s]) => g.measureText(s).width);
  let at = -(widths.reduce((a, b) => a + b, 0) + gap * (pieces.length - 1)) / 2;
  for (let k = 0; k < pieces.length; k++) {
    const [text, colour] = pieces[k];
    // the shadow, two pixels down and right, half see-through
    g.lineWidth = outline * 2;
    g.strokeStyle = g.fillStyle = "rgba(0,0,0,0.5)";
    g.strokeText(text, at + 2, 2);
    g.fillText(text, at + 2, 2);
    g.strokeStyle = "#000";
    g.strokeText(text, at, 0);
    g.fillStyle = assColour(colour);
    g.fillText(text, at, 0);
    at += widths[k] + gap;
  }
  g.restore();
}

// -- the sound ----------------------------------------------------------------------
//
// One AudioContext, and the room's clock is read off it while the reel plays: the
// sounds are scheduled on that clock, so reading the picture's time anywhere else
// would let the two drift apart over a minute of video.

let reelAudio = null;     // the AudioContext
let reelClock = null;     // {t, at}: video second `t` is context second `at`
let reelSources = [];     // what is scheduled now, to be stopped on a pause or a jump
const reelBufs = new Map();   // src -> Promise<AudioBuffer|null>
let reelMusic = null;     // the <audio> playing the music

function reelCtx() {
  if (!reelAudio) {
    try { reelAudio = new AudioContext(); } catch (e) { reelAudio = null; }
    // Sound allowed late — the browser held it until a gesture — joins the picture
    // where the picture has got to, rather than where it was when play was pressed.
    if (reelAudio) reelAudio.onstatechange = () => {
      if (reelAudio.state === "running" && chatPlaying && REEL && !chatWatching
          && !reelLive.was) reelSound(chatT);
      reelLive.was = reelAudio.state === "running";
    };
  }
  return reelAudio;
}

// Whether the sound is what the clock is read off right now.
function reelLive() {
  return !!(REEL && reelClock && reelAudio && reelAudio.state === "running");
}

function reelBuf(src) {
  if (!reelBufs.has(src)) {
    const o = { credentials: "same-origin" };
    if (TOK) o.headers = { "X-Slopgen-Token": TOK };
    const ac = reelCtx();
    reelBufs.set(src, !ac ? Promise.resolve(null) : fetch(src, o)
      .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(r.statusText))))
      .then((b) => ac.decodeAudioData(b))
      .catch(() => null));
  }
  return reelBufs.get(src);
}

// Decoded ahead, so that pressing play does not wait on sixty small downloads.
function reelPrimeSound() {
  for (const s of REEL.sounds) reelBuf(s.src);
}

// The video's second right now, while the reel plays.
function reelNow() {
  if (reelClock && reelAudio) return reelClock.t + (reelAudio.currentTime - reelClock.at);
  return chatT;
}

// Start every sound from `t` on: the ones already under way partway in, the rest on
// the moment they go off.
function reelSound(t) {
  reelSilence();
  const ac = reelCtx();
  if (!ac) return;
  ac.resume().catch(() => {});
  const clock = { t, at: ac.currentTime + 0.06 };
  reelClock = clock;
  for (const s of REEL.sounds) {
    if (s.dur > 0 && s.at + s.dur <= t) continue;
    reelBuf(s.src).then((buf) => {
      if (!buf || reelClock !== clock) return;
      let when = clock.at + (s.at - clock.t);
      let offset = 0;
      if (when < ac.currentTime) {
        offset = (ac.currentTime - when) * s.rate;
        when = ac.currentTime;
      }
      if (offset >= buf.duration) return;
      const src = ac.createBufferSource();
      src.buffer = buf;
      src.playbackRate.value = s.rate || 1;
      const gain = ac.createGain();
      gain.gain.value = s.gain;
      src.connect(gain).connect(ac.destination);
      src.start(when, offset);
      reelSources.push(src);
    });
  }
  if (REEL.music) {
    if (!reelMusic) reelMusic = new Audio();
    const want = tokd(REEL.music.src);
    if (reelMusic.dataset.src !== want) {
      reelMusic.src = want;
      reelMusic.dataset.src = want;
      reelMusic.loop = true;   // the assembler loops it under a video longer than it
    }
    reelMusic.volume = Math.min(Math.max(REEL.music.gain, 0), 1);
    const go = () => {
      if (reelClock !== clock) return;
      const d = reelMusic.duration;
      reelMusic.currentTime = d > 0 ? reelNow() % d : 0;
      reelMusic.play().catch(() => {});
    };
    reelMusic.readyState >= 1 ? go() : reelMusic.addEventListener("loadedmetadata", go, { once: true });
  }
}

function reelSilence() {
  reelClock = null;
  for (const s of reelSources) { try { s.stop(); } catch (e) { /* already done */ } }
  reelSources = [];
  if (reelMusic) reelMusic.pause();
}

// Kept to the clock: a music file the browser buffers late drifts, and a second of it
// is audible against the pops.
function reelKeepMusic() {
  if (!reelMusic || reelMusic.paused || !(reelMusic.duration > 0)) return;
  const want = reelNow() % reelMusic.duration;
  if (Math.abs(reelMusic.currentTime - want) > 0.3) reelMusic.currentTime = want;
}
