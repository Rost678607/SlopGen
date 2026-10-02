// The chat room.
//
// Every other screen in this page edits what a stage produced. This one edits the
// thing the video IS: the conversations are not an input to a chat run, they are the
// run's whole material, and a run made by hand starts empty — so this is where the
// video comes into existence rather than where it is adjusted.
//
// Three columns, because the thing has three levels and flattening any of them was
// the first version's mistake. The pieces of conversation the video is made of; the
// messages of whichever is open; and what it will actually look like. A video is
// several chats shown one after another with a swipe between them — that is the
// format, not an edge case — so the pieces are objects in a list you add to and
// reorder, not a boundary flag buried in the messages.
//
// Every edit is one request and the reply is the WHOLE document, for a sharp reason:
// a reply points at a message by index, so dropping or moving one rewrites pointers
// all over that conversation, and a screen patching one row would be showing
// something that no longer exists.

let CHAT = null;          // {id, title, video, doc}
let chatConv = 0;         // which conversation is open
let chatSel = -1;         // which message is open for editing, by index within it
const chatDrafts = new Map();  // typed-but-not-saved text, keyed "c:i"
let chatOpts = null;      // /api/options, for the lists the settings sheet offers
let chatAt = -1;          // where the playhead is, in seconds; -1 = follow the selection

const cq = (s) => document.querySelector(s);
const chatConvs = () => (CHAT && CHAT.doc.conversations) || [];
const chatActive = () => chatConvs()[chatConv] || null;
const draftKey = (i) => `${chatConv}:${i}`;
const chatDraft = (m) => (chatDrafts.has(draftKey(m.i)) ? chatDrafts.get(draftKey(m.i)) : m.text);

// ---------------------------------------------------------------- opening it

async function openChat(id, title, video = 0) {
  let d;
  try {
    d = await api(`/api/runs/${id}/chat?video=${video}`);
  } catch (e) { return say(e.message, true); }
  CHAT = { id, title, video, doc: d };
  chatConv = 0;
  chatSel = -1;
  chatAt = -1;
  chatDrafts.clear();
  // the lists the settings sheet offers are the start form's own, fetched once
  if (!chatOpts) { try { chatOpts = await api("/api/options"); } catch (e) { chatOpts = {}; } }
  cq("#chat-title").textContent = title;
  renderChat();
  cq("#chat").hidden = false;
  shootChat();
}

function closeChat() {
  cq("#chat").hidden = true;
  CHAT = null;
  loadRuns();
}

async function chatDo(path, opts = {}) {
  if (!CHAT) return null;
  const body = Object.assign({ video: CHAT.video, c: chatConv }, opts.body || {});
  try {
    const d = await api(`/api/runs/${CHAT.id}/chat${path}`, {
      method: opts.method || "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    CHAT.doc = d;
    if (chatConv >= d.conversations.length) chatConv = Math.max(0, d.conversations.length - 1);
    renderChat();
    return d;
  } catch (e) { say(e.message, true); return null; }
}

// Re-read the document without losing where you were. `openChat` resets the selection
// and the drafts, which is right when the room opens and wrong after a stage has run
// underneath you — the line you were looking at is still the line you were looking at.
async function reloadChat() {
  if (!CHAT) return;
  try {
    CHAT.doc = await api(`/api/runs/${CHAT.id}/chat?video=${CHAT.video}`);
  } catch (e) { return say(e.message, true); }
  if (chatConv >= chatConvs().length) chatConv = Math.max(0, chatConvs().length - 1);
  const conv = chatActive();
  if (!conv || chatSel >= conv.messages.length) chatSel = -1;
  renderChat();
}

// ---------------------------------------------------------------- drawing it

function renderChat() {
  if (!CHAT) return;
  renderChatStages();
  renderChatConvs();
  renderChatCast();
  renderChatClock();
  renderChatWatch();
  if (!cq("#chat-set").hidden) renderChatSettings();
  cq("#chat-state").textContent = chatState();
  const conv = chatActive();
  const list = cq("#chat-list");
  if (!conv) {
    // The front door, and the only place it can be: an empty room is the one screen
    // where nothing on it is obvious, and a line of grey text pointing at a button in
    // the header is not an answer to "where do I press".
    list.innerHTML = `<div class="chat-start">
      <p class="dim">${esc(lab("web.chat.noconv", ""))}</p>
      <button class="primary" id="chat-start-go">${
        esc(lab("web.chat.add.open", "＋ переписка"))}</button></div>`;
    cq("#chat-start-go").onclick = () => openAdd();
    return;
  }
  list.innerHTML =
    `<div class="chat-convhead">
       <input id="chat-convtitle" value="${esc(conv.title)}" placeholder="${
         esc(lab("web.chat.untitled", "без названия"))}">
       ${conv.source ? `<span class="dim">${esc(conv.source)}</span>` : ""}
     </div>`
    + conv.messages.map(chatRowHTML).join("")
    + `<button class="ghost chat-add" data-add="${conv.messages.length}">${
         esc(lab("web.chat.add", "＋ сообщение"))}</button>`;
  if (!conv.messages.length)
    list.insertAdjacentHTML("beforeend",
      `<p class="dim chat-empty">${esc(lab("web.chat.empty", ""))}</p>`);
}

function chatState() {
  const d = CHAT.doc;
  const msgs = chatConvs().reduce((n, c) => n + c.lines, 0);
  const bits = [`${chatConvs().length} ${lab("web.chat.excerpts", "переписок")}`,
                `${msgs} ${lab("web.chat.lines", "сообщений")}`];
  // what stands between these conversations and a video, said on the screen rather
  // than three stages later in a traceback
  for (const why of d.blocking || []) bits.push(lab("web.chat.block." + why, why));
  return bits.join(" · ");
}

// The pieces the video is made of, in the order they are shown. Selecting one opens
// it; everything else on the screen is about whichever is open.
function renderChatConvs() {
  cq("#chat-convs").innerHTML =
    `<h4>${esc(lab("web.chat.pieces", "переписки"))}</h4>` +
    chatConvs().map((c) => `
      <div class="chat-conv${c.c === chatConv ? " on" : ""}" data-conv="${c.c}">
        <b>${esc(c.title || `${lab("web.chat.excerpt", "кусок")} ${c.c + 1}`)}</b>
        <span class="dim">${c.lines}</span>
        <span class="grow"></span>
        <button class="ghost" data-cact="up" data-c="${c.c}" title="↑">↑</button>
        <button class="ghost" data-cact="down" data-c="${c.c}" title="↓">↓</button>
        ${c.c ? `<button class="ghost" data-cact="join" data-c="${c.c}">${
          esc(lab("web.chat.join", "склеить"))}</button>` : ""}
        <button class="ghost danger" data-cact="drop" data-c="${c.c}">✕</button>
      </div>`).join("") +
    `<button class="ghost" data-cact="add">${esc(lab("web.chat.addconv", "＋ переписка"))}</button>`;
}

function chatRowHTML(m) {
  const conv = chatActive();
  const open = m.i === chatSel;
  const head = m.clear_before
    ? `<div class="chat-seam dim">${esc(lab("web.chat.cleared", "экран чистится"))}</div>` : "";
  const reply = m.reply_to >= 0 && conv.messages[m.reply_to]
    ? `<span class="chat-reply">↳ ${esc(trim(conv.messages[m.reply_to].text, 40))}</span>` : "";
  const react = (m.reactions || [])
    .map(([e, n]) => `<span class="chat-react">${esc(e)}${n > 1 ? n : ""}</span>`).join("");
  return head + `
<div class="chat-row${open ? " on" : ""}${m.pinned ? " pinned" : ""}" data-i="${m.i}"
     draggable="true">
  <div class="chat-line" data-open="${m.i}">
    <b class="chat-who">${esc(m.nick || m.persona || lab("web.chat.nobody", "—"))}</b>
    <span class="chat-text">${esc(trim(chatDraft(m), 160))}</span>
    ${reply}${react}
  </div>
  ${open ? chatEditHTML(m) : ""}
</div>`;
}

const trim = (s, n) => (s || "").length > n ? (s || "").slice(0, n - 1) + "…" : (s || "");

// The panel under the open message. Everything about a message that is not its words
// lives here rather than on the row, because a list where every row carries eight
// controls is a list nobody can read down.
function chatEditHTML(m) {
  const d = CHAT.doc;
  const conv = chatActive();
  const who = d.cast.map((c) => c.name);
  if (m.persona && !who.includes(m.persona)) who.push(m.persona);
  const opts = (list, value) => list.map((v) =>
    `<option value="${esc(v)}"${v === value ? " selected" : ""}>${esc(v || "—")}</option>`).join("");
  const answers = [`<option value="-1">${esc(lab("web.chat.noreply", "— никому —"))}</option>`]
    .concat(conv.messages.filter((o) => o.i !== m.i).map((o) =>
      `<option value="${o.i}"${o.i === m.reply_to ? " selected" : ""}>${
        esc(`${o.persona}: ${trim(o.text, 30)}`)}</option>`)).join("");
  return `
<div class="chat-edit">
  <textarea class="chat-area" data-text="${m.i}" rows="3">${esc(chatDraft(m))}</textarea>
  ${m.source_text ? `<p class="dim chat-src">${esc(lab("web.chat.wassaid", "было"))}: ${
    esc(trim(m.source_text, 180))}</p>` : ""}
  <div class="row2">
    <label>${esc(lab("web.chat.who", "от кого"))}
      <select data-f="persona" data-i="${m.i}">${opts([""].concat(who), m.persona)}</select></label>
    <label>${esc(lab("web.chat.answers", "отвечает на"))}
      <select data-f="reply_to" data-i="${m.i}">${answers}</select></label>
  </div>
  <div class="row2">
    <label>${esc(lab("web.chat.stamp", "время"))}
      <input type="time" data-f="stamp" data-i="${m.i}" value="${esc(m.stamp)}"></label>
    <label>${esc(lab("web.chat.react", "реакции"))}
      <span class="rx-edit">${(m.reactions || []).map(([e, n], k) => `
        <span class="rx-chip">
          <button class="rx-emoji" data-rx="pick" data-k="${k}" title="${
            esc(lab("web.chat.rx.swap", ""))}">${esc(e)}</button>
          <button data-rx="less" data-k="${k}">−</button>
          <b>${n}</b>
          <button data-rx="more" data-k="${k}">+</button>
          <button class="rx-off" data-rx="drop" data-k="${k}">✕</button>
        </span>`).join("")}
        <span class="rx-add">${COMMON_RX.map((e) =>
          `<button data-rx="add" data-e="${esc(e)}">${esc(e)}</button>`).join("")}
          <button data-rx="other">…</button></span>
      </span></label>
  </div>
  ${d.votes ? `<label>${esc(lab("web.chat.score", "карма"))}
      <input type="number" data-f="score" data-i="${m.i}" value="${m.score}"></label>` : ""}
  <div class="row2">
    <label>${esc(lab("web.chat.day", "дата"))}
      <input type="date" data-f="day" data-i="${m.i}" value="${esc(m.day || "")}"></label>
    <label>${esc(lab("web.chat.nick", "ник в этом сообщении"))}
      <input data-f="nick" data-i="${m.i}" value="${esc(m.nick)}" placeholder="${
        esc(lab("web.chat.asthecard", "— как на карточке —"))}"></label>
    <label class="inline"><input type="checkbox" data-f="clear_before" data-i="${m.i}"${
      m.clear_before ? " checked" : ""}> ${esc(lab("web.chat.clearhere", "чистить экран здесь"))}</label>
  </div>
  <div class="chat-acts">
    <button class="ghost" data-act="split" data-i="${m.i}">${esc(lab("web.chat.split", "разрезать здесь"))}</button>
    <button class="ghost" data-act="after" data-i="${m.i}">${esc(lab("web.chat.after", "＋ ниже"))}</button>
    <button class="ghost" data-act="restamp" title="${esc(lab("web.chat.restamp.why", ""))}">${
      esc(lab("web.chat.restamp", "время по порядку"))}</button>
    <span class="grow"></span>
    <button class="ghost danger" data-act="drop" data-i="${m.i}">${esc(lab("web.chat.drop", "убрать"))}</button>
  </div>
</div>`;
}

// Who is in the video, and whether they are anybody outside it. A name with a card
// behind it has a picture and a voice that survive into the next video; one without is
// just a name on a bubble, and the button says which.
let chatWho = "";   // which person is open for editing, by name

function renderChatCast() {
  const d = CHAT.doc;
  cq("#chat-cast").innerHTML =
    `<h4>${esc(lab("web.chat.cast", "кто в переписке"))}</h4>` +
    (d.cast.some((c) => !c.silent)
      ? d.cast.filter((c) => !c.silent).map(personHTML).join("")
      : `<p class="dim">${esc(lab("web.chat.nocast", ""))}</p>`) +
    // the address book behind the video: people with a card who have not said anything
    // in THIS one. Apart, because the cast of a video is who is in it.
    (d.cast.some((c) => c.silent)
      ? `<h4 class="quiet">${esc(lab("web.chat.cast.quiet", ""))}</h4>` +
        d.cast.filter((c) => c.silent).map(personHTML).join("")
      : "") +
    `<button class="ghost" id="chat-who-new">${
      esc(lab("web.chat.who.new", "＋ человек"))}</button>`;
  cq("#chat-who-new").onclick = async () => {
    const name = prompt(lab("web.chat.who.newname", "имя"), "");
    if (!name || !name.trim()) return;
    await chatDo("/persona", { method: "PUT", body: { name: name.trim() } });
    chatWho = name.trim();
    renderChat();
  };
}

// One person, and — when they are the one being looked at — everything a card holds.
// It was two `prompt()` boxes in a row, which is the shape that asks you to remember
// what the first one said while you answer the second, and offers no way at all to
// see what a field currently is.
function personHTML(c) {
  const open = c.name === chatWho;
  const head = `
    <div class="chat-person${c.carded ? " carded" : ""}${open ? " on" : ""}${
      c.silent ? " quiet" : ""}" data-person="${esc(c.name)}">
      <b>${esc(c.name)}</b>
      <span class="dim">${c.lines}</span>
      <span class="dim">${esc(c.voice || lab("web.chat.silent", "не читается"))}</span>
      <span class="grow"></span>
      ${c.avatar ? `<img class="who-pic" src="${tokd(`/api/avatar?name=${
        encodeURIComponent(c.avatar)}`)}" alt="">` : ""}
    </div>`;
  if (!open) return head;
  const pics = [""].concat(chatOpts.avatars || []);
  if (c.found && !pics.includes(c.found)) pics.push(c.found);
  const voices = [""].concat(chatOpts.chat_voices || []);
  const pick = (list, value, blank) => list.map((v) =>
    `<option value="${esc(v)}"${v === value ? " selected" : ""}>${
      esc(v || lab(blank, "— нет —"))}</option>`).join("");
  return head + `
    <div class="who-edit">
      <label>${esc(lab("web.chat.who.voice", "голос"))}
        <select data-p="voice">${pick(voices, c.voice, "web.chat.silent")}</select></label>
      <label>${esc(lab("web.chat.who.pic", "аватарка"))}
        <span class="pic-pick">${pics.map((v) => `
          <button class="pic${v === c.avatar ? " on" : ""}" data-pic="${esc(v)}"
                  title="${esc(v || lab("web.chat.who.initials", ""))}">${
            v ? `<img src="${tokd(`/api/avatar?name=${encodeURIComponent(v)}`)}" alt="">`
              : `<i>${esc((c.name[0] || "?").toUpperCase())}</i>`}</button>`).join("")}
        </span></label>
      <div class="row2">
        <label>${esc(lab("web.chat.who.handle", "@ник"))}
          <input data-p="handle" value="${esc(c.handle)}"></label>
        <label>${esc(lab("web.chat.who.colour", "цвет"))}
          <input data-p="colour" value="${esc(c.colour)}" placeholder="#rrggbb"></label>
      </div>
      <div class="chat-acts">
        <button class="ghost" data-rename="${esc(c.name)}">${
          esc(lab("web.chat.who.rename", "переименовать"))}</button>
      </div>
    </div>`;
}

// The chain, as the montage room draws it and pressing the very same endpoint: what
// that route does is run one stage of THIS run's chain by hand, which is as true of a
// conversation as of a cut.
function renderChatStages() {
  const d = CHAT.doc;
  const done = new Set(d.completed || []);
  cq("#chat-stages").innerHTML = (d.stages || []).map((s) =>
    `<button data-stage="${esc(s)}" class="${done.has(s) ? "done" : ""}">${
      esc(word(s))}</button>`).join("");
}

// The frame as it stands at one message of the open conversation. `at` is counted
// across the whole video, because that is what the renderer lays out — the room knows
// the conversations, the drawing knows only the order they are shown in.
function shootChat(at = -1) {
  if (!CHAT) return;
  chatAt = -1;
  const before = chatConvs().slice(0, chatConv).reduce((n, c) => n + c.lines, 0);
  const n = at < 0 ? before + ((chatActive() || { lines: 0 }).lines - 1) : before + at;
  if (n < 0) return;
  cq("#chat-shot-img").src =
    tokd(`/api/runs/${CHAT.id}/chat/preview?video=${CHAT.video}&at=${n}&t=${Date.now()}`);
}

// The settings, with the frame in front of you. Drawn from what the server says it
// will accept (`chat_api.SHEET`), so a setting added there reaches this screen without
// touching this file — the browser knows the four kinds of control and nothing about
// what any of them mean.
function renderChatSettings() {
  const d = CHAT.doc;
  const v = d.settings || {};
  const rows = (d.sheet || []).filter(
    (r) => !r.when || r.when === v.skin).map((r) => {
    // A heading and not a setting. Twenty-two controls in a narrow column with nothing
    // between them is a list nobody reaches the bottom of — which is how "there is no
    // way to turn the split screen on" happens to a screen that has one.
    if (r.head) return `<h5>${esc(lab(r.head, ""))}</h5>`;
    const val = v[r.f];
    if (r.kind === "check")
      return `<label class="inline"><input type="checkbox" data-s="${r.f}"${
        val ? " checked" : ""}> ${esc(lab(r.l, r.f))}</label>`;
    if (r.kind === "number")
      return `<label>${esc(lab(r.l, r.f))}<input type="number" data-s="${r.f}"
        min="${r.min}" max="${r.max}" step="${r.step || 1}" value="${esc(String(val))}"></label>`;
    if (r.kind === "select") {
      const list = (r.blank ? [""] : []).concat(chatOpts[r.opts] || []);
      const word = (o) => (r.opt_l ? lab(r.opt_l + o, o)
        : (o || lab(r.blank_l || "w.none", "— нет —")));
      return `<label>${esc(lab(r.l, r.f))}<select data-s="${r.f}">${list.map((o) =>
        `<option value="${esc(o)}"${o === val ? " selected" : ""}>${esc(word(o))}</option>`
      ).join("")}</select></label>`;
    }
    return `<label>${esc(lab(r.l, r.f))}<input data-s="${r.f}" value="${esc(String(val || ""))}"></label>`;
  });
  cq("#chat-set").innerHTML =
    `<h4>${esc(lab("web.chat.settings", "настройки ролика"))}</h4>` + rows.join("") +
    `<p class="dim">${esc(lab("web.chat.settings.note", ""))}</p>`;
}

// The clock. Hidden until there is one — a conversation being built has no timings at
// all, and a strip pretending otherwise would be the one control on this screen that
// lies. Every message is a tick and every swipe is a brighter one, because what you
// scrub for is usually a seam.
function renderChatClock() {
  const c = (CHAT.doc.clock || { total: 0, marks: [] });
  const box = cq("#chat-time");
  box.hidden = !(c.total > 0);
  if (box.hidden) return;
  const track = cq("#chat-track");
  // where one conversation gives way to the next, by the video's own numbering
  const seams = new Set();
  let n = 0;
  for (const conv of CHAT.doc.conversations || []) {
    if (n) seams.add(n);
    n += conv.lines;
  }
  track.querySelectorAll(".mark").forEach((m) => m.remove());
  for (const m of c.marks) {
    const el = document.createElement("div");
    el.className = "mark" + (seams.has(m.i) ? " swipe" : "");
    el.style.left = `${(m.at / c.total) * 100}%`;
    track.appendChild(el);
  }
  chatHead(chatAt < 0 ? 0 : chatAt);
}

// Named `chatHead` rather than the obvious thing: the montage room already has a
// function of that obvious name and loads after this file, so the plain one silently
// became ITS playhead — the strip here moved nothing and said nothing, with no error
// anywhere. All three rooms share one global scope, so everything in this file is
// named as if the other two existed, because they do.
function chatHead(at) {
  const c = CHAT.doc.clock || { total: 0 };
  if (!(c.total > 0)) return;
  const k = Math.min(Math.max(at / c.total, 0), 1);
  cq("#chat-head").style.left = `${k * 100}%`;
  cq("#chat-clock").textContent = `${at.toFixed(2)} / ${c.total.toFixed(1)}s`;
}

// ---------------------------------------------------------------- the gestures

// -- watching the real thing -----------------------------------------------
//
// The still is exact about everything except the one thing the format is made of:
// movement. A message arriving a piece at a time, the view rolling up after it, the
// swipe between two chats — none of that is in a frame, and no amount of frames makes
// it. So once there is a cut, the room plays it.
let chatWatching = false;

function renderChatWatch() {
  const has = !!(CHAT && CHAT.doc.video);
  cq("#chat-watch").hidden = !has;
  if (!has && chatWatching) showStill();
  cq("#chat-watch").textContent =
    lab(chatWatching ? "web.chat.watch.still" : "web.chat.watch", "");
}

function showStill() {
  chatWatching = false;
  const v = cq("#chat-video");
  v.pause();
  v.removeAttribute("src");
  v.load();
  v.hidden = true;
  cq("#chat-shot-img").hidden = false;
  cq("#chat-time").hidden = !(CHAT && CHAT.doc.clock && CHAT.doc.clock.total > 0);
  renderChatWatch();
}

cq("#chat-watch").onclick = () => {
  if (chatWatching) return showStill();
  chatWatching = true;
  const v = cq("#chat-video");
  // cache-busted, because the whole point of pressing this is to see the cut that was
  // just made and not the one the browser kept from before `сборка` ran
  v.src = tokd(`/api/runs/${CHAT.id}/video?x=${Date.now()}`);
  v.hidden = false;
  cq("#chat-shot-img").hidden = true;
  cq("#chat-time").hidden = true;
  renderChatWatch();
  v.play().catch(() => {});
};

// -- making the video --------------------------------------------------------
//
// "How do I actually assemble it" had no answer on this screen. The chain was a row of
// chips that read as status rather than as buttons, with nothing saying they were to
// be pressed left to right — so this presses them, in order, stopping at the first one
// that will not go and saying which. The chips stay, because redoing ONE stage is the
// other half of how this screen is used.
async function buildChat() {
  if (!CHAT) return;
  const go = cq("#chat-build");
  go.disabled = true;
  const was = go.textContent;
  try {
    for (const stage of CHAT.doc.stages || []) {
      if (stage === "source") continue;   // the conversation is already here
      go.textContent = `${lab("web.chat.build.at", "")} ${word(stage)}…`;
      await api(`/api/runs/${CHAT.id}/montage/stage`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ video: CHAT.video, stage }),
      });
    }
    say(lab("web.chat.build.done", "готово"));
  } catch (e) {
    say(e.message, true);
  } finally {
    go.disabled = false;
    go.textContent = was;
  }
  await reloadChat();
  if (CHAT && CHAT.doc.video) cq("#chat-watch").click();
}

cq("#chat-build").onclick = buildChat;
cq("#chat-close").onclick = closeChat;
cq("#chat-shot").onclick = () => (chatAt >= 0 ? shootAt(chatAt) : shootChat(chatSel));
cq("#chat-settings").onclick = () => {
  const box = cq("#chat-set");
  box.hidden = !box.hidden;
  if (!box.hidden) renderChatSettings();
};

cq("#chat-set").addEventListener("change", async (e) => {
  const el = e.target.closest("[data-s]");
  if (!el) return;
  const value = el.type === "checkbox" ? el.checked
    : (el.type === "number" ? +el.value : el.value);
  await chatDo("/settings", { method: "PUT", body: { [el.dataset.s]: value } });
  // Back to the live preview, whatever the playhead was on. The frames behind the
  // timeline are the ones ALREADY DRAWN, so after a change of skin they show the old
  // one — a picture that contradicts the control you just moved. The message preview
  // is laid fresh on every request and therefore tells the truth; the timeline starts
  // telling it again when `рисование` has run.
  shootChat(chatSel);
});

// Scrubbing. The frame comes out of the states already drawn, interpolated at that
// instant by the very arithmetic the renderer uses, so it costs no encoder and cannot
// disagree with the video.
function scrubTo(e) {
  const c = CHAT.doc.clock || { total: 0 };
  if (!(c.total > 0)) return;
  const box = cq("#chat-track").getBoundingClientRect();
  chatAt = Math.min(Math.max((e.clientX - box.left) / box.width, 0), 1) * c.total;
  chatHead(chatAt);
}

cq("#chat-track").addEventListener("pointerdown", (e) => {
  cq("#chat-track").setPointerCapture(e.pointerId);
  scrubTo(e);
});
cq("#chat-track").addEventListener("pointermove", (e) => {
  if (e.buttons) scrubTo(e);
});
cq("#chat-track").addEventListener("pointerup", (e) => { scrubTo(e); shootAt(chatAt); });

function shootAt(at) {
  if (!CHAT) return;
  cq("#chat-shot-img").src = tokd(
    `/api/runs/${CHAT.id}/chat/preview?video=${CHAT.video}&t=${at.toFixed(3)}&x=${Date.now()}`);
}

cq("#chat-convs").addEventListener("click", async (e) => {
  const act = e.target.closest("[data-cact]");
  if (act) {
    const c = +act.dataset.c;
    const what = act.dataset.cact;
    if (what === "add") {
      return openAdd();
    } else if (what === "drop") {
      chatSel = -1;
      await chatDo("/conversation", { method: "DELETE", body: { c } });
    } else if (what === "join") {
      chatSel = -1; chatConv = c - 1;
      await chatDo("/join", { body: { c } });
    } else if (what === "up" && c > 0) {
      chatConv = c - 1;
      await chatDo("/conversation/move", { body: { c, to: c - 1 } });
    } else if (what === "down") {
      chatConv = c + 1;
      await chatDo("/conversation/move", { body: { c, to: c + 1 } });
    }
    return shootChat();
  }
  const pick = e.target.closest("[data-conv]");
  if (!pick) return;
  chatConv = +pick.dataset.conv;
  chatSel = -1;
  renderChat();
  shootChat();
});

cq("#chat-list").addEventListener("click", async (e) => {
  const open = e.target.closest("[data-open]");
  if (open) {
    const i = +open.dataset.open;
    chatSel = chatSel === i ? -1 : i;
    renderChat();
    if (chatSel >= 0) shootChat(chatSel);
    return;
  }
  const add = e.target.closest("[data-add]");
  if (add) {
    const d = await chatDo("/message", { body: { at: +add.dataset.add, persona: lastWho() } });
    if (d) { chatSel = (chatActive() || { messages: [] }).messages.length - 1; renderChat(); }
    return shootChat(chatSel);
  }
  const act = e.target.closest("[data-act]");
  if (!act) return;
  const i = +act.dataset.i;
  const what = act.dataset.act;
  if (what === "drop") { chatSel = -1; await chatDo("/message", { method: "DELETE", body: { i } }); }
  else if (what === "restamp") { await chatDo("/restamp", { body: { step: 1 } }); }
  else if (what === "split") {
    chatSel = -1;
    const was = chatConv;
    const d = await chatDo("/split", { body: { i } });
    if (d) { chatConv = was + 1; renderChat(); }
  }
  else if (what === "after") {
    const d = await chatDo("/message", { body: { at: i + 1, persona: lastWho() } });
    if (d) { chatSel = i + 1; renderChat(); }
  }
  shootChat(chatSel);
});

// -- dragging a message somewhere else -------------------------------------
//
// Two arrow buttons moved a line one place per press, which for "this belongs nine
// lines up" is nine presses and nine redraws. A conversation is a list and a list is
// dragged. The line under the cursor says where it would land, because a drop with no
// target drawn is a guess about what the program understood.
let chatDrag = -1;

cq("#chat-list").addEventListener("dragstart", (e) => {
  const row = e.target.closest(".chat-row");
  if (!row) return;
  chatDrag = +row.dataset.i;
  row.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
  // Firefox refuses to start a drag without payload, and the payload is never read
  e.dataTransfer.setData("text/plain", String(chatDrag));
});

cq("#chat-list").addEventListener("dragover", (e) => {
  if (chatDrag < 0) return;
  const row = e.target.closest(".chat-row");
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  cq("#chat-list").querySelectorAll(".drop-here, .drop-after")
    .forEach((x) => x.classList.remove("drop-here", "drop-after"));
  if (!row || +row.dataset.i === chatDrag) return;
  // above or below the midline, which is the only thing a cursor can mean here
  const box = row.getBoundingClientRect();
  row.classList.add(e.clientY > box.top + box.height / 2 ? "drop-after" : "drop-here");
});

cq("#chat-list").addEventListener("dragend", () => {
  chatDrag = -1;
  cq("#chat-list").querySelectorAll(".dragging, .drop-here, .drop-after")
    .forEach((x) => x.classList.remove("dragging", "drop-here", "drop-after"));
});

cq("#chat-list").addEventListener("drop", async (e) => {
  const row = e.target.closest(".chat-row");
  e.preventDefault();
  const from = chatDrag;
  chatDrag = -1;
  if (!row || from < 0) return;
  const box = row.getBoundingClientRect();
  const after = e.clientY > box.top + box.height / 2;
  let to = +row.dataset.i + (after ? 1 : 0);
  // a line taken out from above shifts everything under it up by one, so the place it
  // is going is one lower than the place that was pointed at
  if (from < to) to -= 1;
  if (to === from) return;
  chatSel = to;
  await chatDo("/move", { body: { i: from, to } });
  shootChat(chatSel);
});

// Whoever spoke last, as the author of the next message. A guess rather than a rule,
// and the right one far more often than "nobody": a conversation is written down one
// exchange at a time and the field is one click from being changed.
function lastWho() {
  const conv = chatActive();
  return conv && conv.messages.length ? conv.messages[conv.messages.length - 1].persona : "";
}

// The text box commits on blur, not on every keystroke: the reply is the whole
// document and a redraw mid-sentence would take the caret with it. The draft survives
// the redraws in between, so the only way to lose what you typed is to say so.
cq("#chat-list").addEventListener("input", (e) => {
  const area = e.target.closest("[data-text]");
  if (area) chatDrafts.set(draftKey(+area.dataset.text), area.value);
});

cq("#chat-list").addEventListener("change", async (e) => {
  const title = e.target.closest("#chat-convtitle");
  if (title) return void await chatDo("/conversation", { method: "PUT", body: { title: title.value } });
  const area = e.target.closest("[data-text]");
  if (area) {
    const i = +area.dataset.text;
    chatDrafts.delete(draftKey(i));
    await chatDo("/message", { method: "PUT", body: { i, text: area.value } });
    return shootChat(chatSel);
  }

  const f = e.target.closest("[data-f]");
  if (!f) return;
  const key = f.dataset.f;
  let value = f.type === "checkbox" ? f.checked : f.value;
  if (key === "reply_to" || key === "score") value = +value;
  await chatDo("/message", { method: "PUT", body: { i: +f.dataset.i, [key]: value } });
  shootChat(chatSel);
});

// The handful somebody actually presses. Offered as buttons rather than left to a
// field, because `🔥3 ❤️` was a syntax to learn for a thing that is three taps.
const COMMON_RX = ["❤️", "👍", "🔥", "😁", "😭", "🤡", "👎", "🙏"];

// Reactions are edited as chips: the emoji, how many of it, and a way to be rid of it.
// It was one text field in a format of its own — which is a syntax to get wrong, and
// no way at all to see what the counts are without reading it back.
cq("#chat-list").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-rx]");
  if (!b || chatSel < 0) return;
  e.stopPropagation();
  const m = (chatActive() || { messages: [] }).messages[chatSel];
  if (!m) return;
  const rx = (m.reactions || []).map(([x, n]) => [x, n]);
  const k = +b.dataset.k;
  const what = b.dataset.rx;
  if (what === "add") {
    const at = rx.findIndex(([x]) => x === b.dataset.e);
    at >= 0 ? rx[at][1]++ : rx.push([b.dataset.e, 1]);
  } else if (what === "other" || what === "pick") {
    const was = what === "pick" ? rx[k][0] : "";
    const got = prompt(lab("web.chat.rx.which", "эмодзи"), was);
    if (!got || !got.trim()) return;
    what === "pick" ? (rx[k][0] = got.trim()) : rx.push([got.trim(), 1]);
  } else if (what === "more") rx[k][1]++;
  else if (what === "less") rx[k][1] > 1 ? rx[k][1]-- : rx.splice(k, 1);
  else if (what === "drop") rx.splice(k, 1);
  await chatDo("/message", { method: "PUT", body: { i: chatSel, reactions: rx } });
  shootChat(chatSel);
}, true);

cq("#chat-cast").addEventListener("click", async (e) => {
  const re = e.target.closest("[data-rename]");
  if (re) {
    const now = prompt(lab("web.chat.who.rename", "переименовать"), re.dataset.rename);
    if (!now || !now.trim() || now === re.dataset.rename) return;
    chatWho = now.trim();
    await chatDo("/rename", { body: { was: re.dataset.rename, now: now.trim() } });
    return shootChat(chatSel);
  }
  const row = e.target.closest("[data-person]");
  if (!row) return;
  chatWho = chatWho === row.dataset.person ? "" : row.dataset.person;
  renderChatCast();
});

cq("#chat-cast").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-pic]");
  if (!b || !chatWho) return;
  e.stopPropagation();
  await chatDo("/persona", { method: "PUT",
    body: { name: chatWho, avatar: b.dataset.pic } });
  shootChat(chatSel);
}, true);

cq("#chat-cast").addEventListener("change", async (e) => {
  const f = e.target.closest("[data-p]");
  if (!f || !chatWho) return;
  await chatDo("/persona", { method: "PUT",
    body: { name: chatWho, [f.dataset.p]: f.value } });
  shootChat(chatSel);
});

cq("#chat-stages").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-stage]");
  if (!b || !CHAT) return;
  b.disabled = true;
  try {
    await api(`/api/runs/${CHAT.id}/montage/stage`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ video: CHAT.video, stage: b.dataset.stage }),
    });
    say(`${word(b.dataset.stage)} ✓`);
  } catch (err) { say(err.message, true); }
  finally { b.disabled = false; }
  await reloadChat();
  if (!chatWatching) shootChat(chatSel);
});

// -- pasting a block in ----------------------------------------------------

cq("#chat-paste-go").onclick = async () => {
  const text = cq("#chat-paste-text").value;
  if (!text.trim()) return;
  const d = await chatDo("/import", { body: { text } });
  cq("#chat-src-box").hidden = true;
  if (d) {
    chatConv = Math.max(0, d.conversations.length - 1);
    chatSel = -1;
    renderChat();
    say(`${d.added} ${lab("web.chat.lines", "сообщений")}`);
  }
  shootChat();
};

// -- the exports base ------------------------------------------------------
//
// Files somebody exported from a client, kept rather than read once: the same thread
// is cut three different ways over a month. Picking a file shows what is IN it before
// anything lands in the video, because a whole-account Telegram export is four hundred
// chats and two of them are worth a video.

let chatExpFile = "";     // which file of the base is open
let chatExpPieces = [];   // and what it turned out to hold

async function loadExports() {
  if (!chatExpFile) {
    cq("#chat-exp-pieces").innerHTML =
      `<p class="dim">${esc(lab("web.chat.exports.pick", ""))}</p>`;
    cq("#chat-exp-go").hidden = true;
  }
  if (!CHAT) return;
  let d;
  try {
    d = await api(`/api/runs/${CHAT.id}/chat/exports?video=${CHAT.video}`);
  } catch (e) { return say(e.message, true); }
  const box = cq("#chat-exp-list");
  box.innerHTML = d.exports.length ? d.exports.map((f) => `
    <div class="exp-file${f.format ? "" : " bad"}${f.name === chatExpFile ? " on" : ""}"${
      f.format ? ` data-exp="${esc(f.name)}"` : ""}>
      <b>${esc(f.name)}</b>
      <span class="fmt">${esc(f.format || lab("web.chat.exports.unknown", "?"))}</span>
      <span class="grow"></span>
      <span class="dim">${Math.max(1, Math.round(f.size / 1024))} KB</span>
    </div>`).join("")
    : `<p class="dim">${esc(lab("web.chat.exports.none", ""))}</p>`;
}

cq("#chat-exp-file").onchange = async (e) => {
  const file = e.target.files && e.target.files[0];
  if (!file || !CHAT) return;
  const body = new FormData();
  body.append("file", file);
  try {
    const out = await api(`/api/runs/${CHAT.id}/chat/exports`, { method: "POST", body });
    say(`${out.name} · ${out.format}`);
  } catch (err) { say(err.message, true); }
  e.target.value = "";
  await loadExports();
};

cq("#chat-exp-list").addEventListener("click", async (e) => {
  const row = e.target.closest("[data-exp]");
  if (!row || !CHAT) return;
  chatExpFile = row.dataset.exp;
  let d;
  try {
    d = await api(`/api/runs/${CHAT.id}/chat/exports/read?video=${CHAT.video}` +
                  `&name=${encodeURIComponent(chatExpFile)}`);
  } catch (err) { return say(err.message, true); }
  chatExpPieces = d.pieces;
  await loadExports();
  const box = cq("#chat-exp-pieces");
  box.innerHTML = chatExpPieces.length ? chatExpPieces.map((p) => `
    <label class="exp-piece">
      <input type="checkbox" data-piece="${p.p}" checked>
      <span class="what">
        <b>${esc(p.title || lab("web.chat.untitled", "без названия"))} · ${p.lines}</b>
        <i>${esc(p.who.join(", "))}</i>
        <i>${esc(p.first)}</i>
      </span>
    </label>`).join("")
    : `<p class="dim">${esc(lab("web.chat.exports.empty", ""))}</p>`;
  cq("#chat-exp-go").hidden = !chatExpPieces.length;
});

// An export's pieces are opened and chosen from like everything else. Taking a whole
// one unread was the same mistake in a smaller coat: an exported chat is a year of
// somebody's messages and the video wants nine of them.
cq("#chat-exp-go").onclick = async () => {
  const first = [...cq("#chat-exp-pieces").querySelectorAll("[data-piece]")]
    .filter((b) => b.checked).map((b) => +b.dataset.piece)[0];
  if (first === undefined || !CHAT) return;
  await openPick("export", chatExpFile, first);
};

// -- the live sources ------------------------------------------------------
//
// Reddit and a signed-in Telegram account, in the same sheet as the exports base
// because it is the same gesture: look at what is there, take one. The difference is
// that one of them has to be signed into first, and that sign-in is three steps —
// Telegram sends a code and waits for it, so no form can be filled in once.

let chatSrc = "export";

// Where a conversation comes from, all of it behind one button. It used to be three
// in the header — paste, sources, exports — which is three answers to one question
// with nothing on the screen saying they were the same question: somebody looking for
// "browse my Telegram" had no reason to press any of them. One door, one row of tabs.
async function openAdd(tab) {
  if (!CHAT) return;
  cq("#chat-src-box").hidden = false;
  cq("#chat-src-rows").innerHTML = "";
  cq("#chat-src-sort").innerHTML = ["hot", "top", "new", "rising"]
    .map((s) => `<option value="${s}">${s}</option>`).join("");
  await pickSource(tab || chatSrc);
}

cq("#chat-add").onclick = () => openAdd();
cq("#chat-src-cancel").onclick = () => { cq("#chat-src-box").hidden = true; };

cq("#chat-manual-go").onclick = async () => {
  const d = await chatDo("/conversation", { body: { at: -1 } });
  cq("#chat-src-box").hidden = true;
  if (d) { chatConv = d.conversations.length - 1; chatSel = -1; renderChat(); shootChat(); }
};

cq("#chat-src-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("[data-src]");
  if (b) pickSource(b.dataset.src);
});

async function pickSource(which) {
  chatSrc = which;
  cq("#chat-src-tabs").querySelectorAll("[data-src]").forEach(
    (b) => b.classList.toggle("on", b.dataset.src === which));
  cq("#chat-src-rows").innerHTML = "";
  // Every pane off, then the one that is wanted on: six states written as one rule
  // rather than six pairs of flags, which is how a tab ends up showing two of them.
  for (const [pane, when] of [["#chat-pane-paste", "paste"],
                              ["#chat-pane-manual", "manual"],
                              ["#chat-pane-export", "export"],
                              ["#chat-invent", "invent"],
                              ["#chat-tg-login", "telegram"]])
    cq(pane).hidden = which !== when;
  cq("#chat-pane-pick").hidden = true;
  cq("#chat-src-rows").hidden = false;
  PICK = null;
  // the search row belongs to the one source that has something to search; Telegram's
  // appears only once somebody is signed in, which `renderTg` decides
  cq("#chat-src-where").hidden = which !== "reddit";
  cq("#chat-src-sort").hidden = which !== "reddit";
  cq("#chat-src-q").placeholder = which === "telegram"
    ? lab("web.chat.src.tgwhere", "@channel") : "r/AskReddit";
  if (which === "telegram") await renderTg();
  if (which === "export") await loadExports();
  if (which === "paste") { cq("#chat-paste-text").value = ""; cq("#chat-paste-text").focus(); }
}

cq("#chat-invent-go").onclick = async () => {
  if (!CHAT) return;
  const go = cq("#chat-invent-go");
  go.disabled = true;
  const d = await chatDo("/invent", { body: {
    topic: cq("#chat-invent-topic").value.trim(),
    want: +(cq("#chat-invent-n").value || 1) } });
  go.disabled = false;
  if (d) {
    cq("#chat-src-box").hidden = true;
    chatConv = Math.max(0, d.conversations.length - 1);
    chatSel = -1;
    renderChat();
    say(`${d.added} ${lab("web.chat.exports.added", "")}`);
    shootChat();
  }
};

// Whether this machine can read Telegram at all. The signing in itself is NOT here:
// it belongs to the machine and not to one video, so it lives in the configuration
// beside the keys, and this only reports what it finds and says where to go.
async function renderTg() {
  let s;
  try { s = await api("/api/chat/telegram"); } catch (e) { return say(e.message, true); }
  const state = cq("#chat-tg-state");
  const step = cq("#chat-tg-step");
  cq("#chat-src-where").hidden = !s.signed_in;
  if (s.signed_in) {
    state.textContent = `${lab("web.chat.tg.as", "вошли как")} ${s.who}`;
    step.innerHTML = `<button class="primary" id="chat-tg-dialogs">${
      esc(lab("web.chat.src.look", "посмотреть"))}</button>`;
    cq("#chat-tg-dialogs").onclick = () => browseSource("");
    return;
  }
  state.textContent = lab("web.chat.tg.notin", "");
  step.innerHTML = `<button class="ghost" id="chat-tg-cfg">${
    esc(lab("web.cfg.tg", "аккаунт Telegram"))}</button>`;
  cq("#chat-tg-cfg").onclick = () => {
    // out of the room and into the configuration, which is where the account is:
    // the tab first, then the section, because opening a section of a hidden tab
    // leaves the operator looking at the page they were already on
    cq("#chat-src-box").hidden = true;
    closeChat();
    openTab("cfg");
    openCfg("telegram");
  };
}

cq("#chat-src-go").onclick = () => browseSource(cq("#chat-src-q").value.trim());

async function browseSource(where) {
  const box = cq("#chat-src-rows");
  box.innerHTML = `<p class="dim">…</p>`;
  let d;
  try {
    d = await api(`/api/chat/browse?source=${chatSrc}` +
                  `&where=${encodeURIComponent(where)}` +
                  `&sort=${encodeURIComponent(cq("#chat-src-sort").value || "hot")}`);
  } catch (e) { box.innerHTML = ""; return say(e.message, true); }
  box.innerHTML = d.rows.length ? d.rows.map((r) => `
    <div class="src-row">
      <span class="what">
        <b>${esc(r.title)}</b>
        <i>${esc(r.note)}</i>
        ${r.text ? `<i>${esc(r.text)}</i>` : ""}
      </span>
      <button class="ghost" data-take="${esc(r.id)}">${
        esc(lab("web.chat.src.take", "взять"))}</button>
    </div>`).join("")
    : `<p class="dim">${esc(lab("web.chat.src.none", ""))}</p>`;
}

cq("#chat-src-rows").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-take]");
  if (b) await openPick(chatSrc, b.dataset.take);
});

// -- choosing which messages go in -----------------------------------------
//
// The step that was missing, and the one that was noticed missing: taking a chat used
// to mean taking its last hundred-odd messages sight unseen, which for a chat of ten
// thousand is an arbitrary stretch of somebody's year. A chat is not a thing you take.
// A stretch of it is — so it is read here first, and chosen from.

let PICK = null;   // {source, where, piece, lines, before, more, title}
const picked = new Set();
let pickAnchor = -1;

async function openPick(source, where, piece = 0, before = 0, keep = false) {
  const rows = cq("#chat-pick-rows");
  rows.innerHTML = `<p class="dim">…</p>`;
  // the picker REPLACES the tab's own pane rather than stacking under it: a sheet
  // showing the file list, the piece list and the messages at once is three screens
  // of scrolling to answer one question
  for (const pane of ["#chat-pane-paste", "#chat-pane-manual", "#chat-pane-export",
                      "#chat-invent", "#chat-tg-login", "#chat-src-rows",
                      "#chat-src-where"])
    cq(pane).hidden = true;
  cq("#chat-pane-pick").hidden = false;
  let d;
  try {
    d = await api(`/api/runs/${CHAT.id}/chat/peek?video=${CHAT.video}` +
                  `&source=${encodeURIComponent(source)}` +
                  `&where=${encodeURIComponent(where)}&piece=${piece}&before=${before}`);
  } catch (e) { closePick(); return say(e.message, true); }
  // Older messages are PREPENDED, so what was already chosen stays chosen and keeps
  // its place: walking back through a chat must not undo the choosing done so far.
  const older = keep && PICK ? PICK.lines : [];
  const shift = d.lines.length;
  if (keep && PICK) {
    const was = new Set(picked);
    picked.clear();
    was.forEach((i) => picked.add(i + shift));
  } else {
    picked.clear();
    // A short conversation is almost always wanted whole, and a long window almost
    // never is. The rule is not a guess about taste: with forty lines, "all" is one
    // click from right; with two hundred it is two hundred clicks from it.
    if (d.lines.length <= 40) d.lines.forEach((ln) => picked.add(ln.i));
  }
  // The older window goes in FRONT, and everything that was already there shifts by
  // its length — the indices and the replies inside them both, or a reply would point
  // at whatever now sits on its old number.
  PICK = { source, where, piece: d.piece, title: d.title, before: d.before,
           more: d.more, pieces: d.pieces,
           lines: d.lines.concat(older.map((ln) => ({
             ...ln, i: ln.i + shift,
             reply_to: ln.reply_to >= 0 ? ln.reply_to + shift : -1 }))) };
  pickAnchor = -1;
  renderPick();
}

function closePick() {
  PICK = null;
  // back to whatever tab we came from, drawn by the one thing that knows how
  pickSource(chatSrc);
}

function renderPick() {
  if (!PICK) return;
  cq("#chat-pick-title").textContent = PICK.title || lab("web.chat.untitled", "");
  cq("#chat-pick-count").textContent =
    `${picked.size} / ${PICK.lines.length}`;
  const sel = cq("#chat-pick-piece");
  sel.hidden = (PICK.pieces || []).length < 2;
  if (!sel.hidden)
    sel.innerHTML = PICK.pieces.map((p) =>
      `<option value="${p.p}"${p.p === PICK.piece ? " selected" : ""}>${
        esc(p.title || lab("web.chat.untitled", ""))} · ${p.lines}</option>`).join("");
  // The older ones are ABOVE, as they are in any client: the window is the most recent
  // stretch, so walking back means scrolling up, and the band at the top is what says
  // there is more up there and that it is on its way.
  const banner = PICK.more
    ? `<div class="pick-more${pickBusy ? " busy" : ""}">${
        esc(lab(pickBusy ? "web.chat.pick.loading" : "web.chat.pick.up", ""))}</div>`
    : "";
  cq("#chat-pick-rows").innerHTML = banner + PICK.lines.map((ln) => `
    <div class="pick-row${picked.has(ln.i) ? " on" : ""}" data-pick="${ln.i}">
      <span class="at">${esc(ln.stamp || "")}</span>
      <span class="who">${esc(ln.who || "—")}</span>
      <span class="what">${esc(ln.text)}</span>
      ${ln.reactions.length ? `<span class="rx">${
        esc(ln.reactions.map(([e, n]) => n > 1 ? `${e}${n}` : e).join(" "))}</span>` : ""}
    </div>`).join("");
}

// Loading as you scroll, rather than a button to press. `pickBusy` is both the guard
// against firing twice on one flick and the thing the banner reads to say it is
// working — one flag, because two would be two chances to disagree about it.
let pickBusy = false;

cq("#chat-pick-rows").addEventListener("scroll", async (e) => {
  const box = e.target;
  if (pickBusy || !PICK || !PICK.more || box.scrollTop > 80) return;
  pickBusy = true;
  renderPick();
  const was = box.scrollHeight;
  await openPick(PICK.source, PICK.where, PICK.piece, PICK.before, true);
  pickBusy = false;
  renderPick();
  // hold the reading position: the list grew upwards, so the same message stays under
  // the same pixel instead of the view jumping to the top of a thousand new lines
  box.scrollTop = box.scrollHeight - was;
});

cq("#chat-pick-back").onclick = closePick;
cq("#chat-pick-all").onclick = () => {
  PICK.lines.forEach((ln) => picked.add(ln.i));
  renderPick();
};
cq("#chat-pick-none").onclick = () => { picked.clear(); renderPick(); };
cq("#chat-pick-piece").onchange = (e) =>
  openPick(PICK.source, PICK.where, +e.target.value, 0, false);

cq("#chat-pick-rows").addEventListener("click", (e) => {
  const row = e.target.closest("[data-pick]");
  if (!row || !PICK) return;
  const i = +row.dataset.pick;
  const at = PICK.lines.findIndex((ln) => ln.i === i);
  // shift takes the run between this and the last one touched, because picking a
  // stretch of a conversation is the common case and forty clicks is not a gesture
  if (e.shiftKey && pickAnchor >= 0) {
    const [lo, hi] = at < pickAnchor ? [at, pickAnchor] : [pickAnchor, at];
    const want = !picked.has(i);
    for (let k = lo; k <= hi; k++)
      want ? picked.add(PICK.lines[k].i) : picked.delete(PICK.lines[k].i);
  } else {
    picked.has(i) ? picked.delete(i) : picked.add(i);
    pickAnchor = at;
  }
  renderPick();
});

cq("#chat-pick-go").onclick = async () => {
  if (!PICK || !picked.size) return;
  const lines = PICK.lines.filter((ln) => picked.has(ln.i));
  const d = await chatDo("/take", { body: {
    title: PICK.title, source: `${PICK.source}:${PICK.where}`, lines } });
  if (d) {
    closePick();
    cq("#chat-src-box").hidden = true;
    chatConv = Math.max(0, d.conversations.length - 1);
    chatSel = -1;
    renderChat();
    say(`${lines.length} ${lab("web.chat.lines", "сообщений")}`);
    shootChat();
  }
};
