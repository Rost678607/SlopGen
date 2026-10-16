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
  reelSay();
  // the reel's clock is now the room's, and the rail is laid out on it
  drawRail();
  if (chatPlaying && !chatWatching) reelSound(chatT);
  reelDraw(chatT);
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

  // The window, by `make_chat_part`'s arithmetic: a straight ramp over `travel`
  // seconds from where the state begins, clamped to the picture padded out to at
  // least one screen.
  const local = t - st.start;
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

  // the loop under a split screen, cropped to fill its strip as the assembler does
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
  // whatever is not on screen stops playing
  for (const [key, v] of REEL.clips) if (!used.has(key) && !v.paused) v.pause();
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
