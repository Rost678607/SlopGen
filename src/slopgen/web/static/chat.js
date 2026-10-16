// The chat room.
//
// Every other screen in this page edits what a stage produced. This one edits the
// thing the video IS: the conversations are not an input to a chat run, they are the
// run's whole material, and a run made by hand starts empty — so this is where the
// video comes into existence rather than where it is adjusted.
//
// Three columns, because the thing has three levels and flattening any of them was
// the first version's mistake. The pieces of conversation the video is made of; the
// messages; and what it will actually look like. A video is several chats shown one
// after another with a swipe between them — that is the format, not an edge case — so
// the pieces are objects in a list you add to and reorder, not a boundary flag buried
// in the messages. The messages of ALL of them run down the middle column in the
// order they are shown, a seam between each two, because that is what is played: a
// list that showed one piece at a time had no place to put a playhead that walks
// from the last line of one chat into the first of the next.
//
// Every edit is one request and the reply is the WHOLE document, for a sharp reason:
// a reply points at a message by index, so dropping or moving one rewrites pointers
// all over that conversation, and a screen patching one row would be showing
// something that no longer exists.

let CHAT = null;          // {id, title, video, doc}
let chatConv = 0;         // which conversation is being worked on (edits go to it)
let chatSel = -1;         // which message is open for editing, by index within it
const chatDrafts = new Map();  // typed-but-not-saved text, keyed "c:i"
let chatOpts = null;      // /api/options, for the lists the settings sheet offers
let chatT = 0;            // where the playhead is, in seconds of the video
let chatNow = -1;         // which message it is on, by the video's numbering
let chatPlaying = false;

const cq = (s) => document.querySelector(s);
const chatConvs = () => (CHAT && CHAT.doc.conversations) || [];
const chatActive = () => chatConvs()[chatConv] || null;
const draftKey = (c, i) => `${c}:${i}`;
const chatDraft = (c, m) => (chatDrafts.has(draftKey(c, m.i)) ? chatDrafts.get(draftKey(c, m.i)) : m.text);
// A message by the video's numbering — every conversation's lines one after another,
// which is how the clock and the preview count — and back.
const chatFirst = (c) => chatConvs().slice(0, c).reduce((n, x) => n + x.lines, 0);

// ---------------------------------------------------------------- opening it

async function openChat(id, title, video = 0) {
  let d;
  try {
    d = await api(`/api/runs/${id}/chat?video=${video}`);
  } catch (e) { return say(e.message, true); }
  CHAT = { id, title, video, doc: d };
  chatConv = 0;
  chatSel = -1;
  chatPause();
  chatT = 0;
  chatNow = -1;
  chatForget();
  chatDrafts.clear();
  // the lists the settings sheet offers are the start form's own, fetched once
  if (!chatOpts) { try { chatOpts = await api("/api/options"); } catch (e) { chatOpts = {}; } }
  cq("#chat-title").textContent = title;
  renderChat();
  cq("#chat").hidden = false;
  shootChat();
}

function closeChat() {
  chatPause();
  if (chatWatching) showStill();
  clearTimeout(reelTimer);
  reelTimer = 0;
  REEL = null;
  reelShowing();
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
    chatForget();
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
  chatForget();
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
  renderChatWatch();
  if (!cq("#chat-set").hidden) renderChatSettings();
  cq("#chat-state").textContent = chatState();
  const list = cq("#chat-msgs");
  if (!chatConvs().length) {
    // The front door, and the only place it can be: an empty room is the one screen
    // where nothing on it is obvious, and a line of grey text pointing at a button in
    // the header is not an answer to "where do I press".
    list.innerHTML = `<div class="chat-start">
      <p class="dim">${esc(lab("web.chat.noconv", ""))}</p>
      <button class="primary" id="chat-start-go">${
        esc(lab("web.chat.add.open", "＋ переписка"))}</button></div>`;
    cq("#chat-start-go").onclick = () => openAdd();
  } else {
    list.innerHTML = chatConvs().map(chatSectionHTML).join("");
  }
  drawRail();
}

// One conversation of the video: its title bar, its messages, and a way to add one.
// The bar is the seam — what a viewer sees between two pieces is a swipe to another
// chat with another name on top, so that is what the list draws between them.
function chatSectionHTML(conv) {
  const c = conv.c;
  return `<section class="chat-sec${c === chatConv ? " on" : ""}" data-sec="${c}" id="chat-sec-${c}">
    <div class="chat-convhead">
       <button class="pic chat-convpic" title="${
         esc(lab("web.chat.convpic", "картинка чата"))}">${
         conv.avatar
           ? `<img src="${tokd(`/api/avatar?name=${encodeURIComponent(conv.avatar)}`)}" alt="">`
           : `<i>${esc((conv.title[0] || "#").toUpperCase())}</i>`}</button>
       <input class="chat-convtitle" value="${esc(conv.title)}" placeholder="${
         esc(lab("web.chat.untitled", "без названия"))}">
       ${conv.source ? `<span class="dim">${esc(conv.source)}</span>` : ""}
     </div>`
    + (chatConvPic === c ? `<div class="chat-convpics pic-pick">${
        [""].concat(chatOpts.avatars || []).map((v) => `
          <button class="pic${v === conv.avatar ? " on" : ""}" data-convpic="${esc(v)}"
                  title="${esc(v || lab("web.chat.who.initials", ""))}">${
            v ? `<img src="${tokd(`/api/avatar?name=${encodeURIComponent(v)}`)}" alt="">`
              : `<i>#</i>`}</button>`).join("")}</div>` : "")
    + conv.messages.map((m) => chatRowHTML(conv, m)).join("")
    + (conv.messages.length ? ""
        : `<p class="dim chat-empty">${esc(lab("web.chat.empty", ""))}</p>`)
    + `<button class="ghost chat-add" data-add="${conv.messages.length}">${
         esc(lab("web.chat.add", "＋ сообщение"))}</button>
  </section>`
    // the native ad after this chat, as the stretch of the list — and of the rail —
    // it is in the video
    + (conv.ad_after && CHAT.doc.native_ad && c < chatConvs().length - 1 && conv.messages.length
      ? `<div class="chat-adsep" data-ad-after="${chatFirst(c) + conv.messages.length - 1}">${
          esc(lab("web.chat.ad.here", "📢 реклама"))}</div>` : "");
}

// Bring a conversation's section into view: the buttons on the left are a table of
// contents for the list, not a switch between lists.
function chatScrollTo(c) {
  const sec = cq(`#chat-sec-${c}`);
  if (sec) sec.scrollIntoView({ block: "start", behavior: "smooth" });
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
      </div>${chatAdSlotHTML(c)}`).join("") +
    `<button class="ghost" data-cact="add">${esc(lab("web.chat.addconv", "＋ переписка"))}</button>`;
}

// The place between this chat and the next, where the native ad may go. Offered only
// between two chats — the ad is a swipe away from one and into the other — and only
// when the run's contract has a clip and its mode lets one in.
function chatAdSlotHTML(c) {
  if (c.c >= chatConvs().length - 1) return "";
  if (!CHAT.doc.native_ad && !c.ad_after) return "";
  const on = c.ad_after;
  return `<button class="ghost chat-adslot${on ? " on" : ""}" data-adslot="${c.c}"${
    CHAT.doc.native_ad ? "" : " disabled"} title="${esc(lab("web.chat.ad.slot.why", ""))}">${
    esc(lab(on ? "web.chat.ad.slot.on" : "web.chat.ad.slot", on ? "📢 реклама ✕" : "＋ реклама"))}</button>`;
}

// What a message carries, as a thumbnail and a word. The picture is the server's
// first frame of it (see `/api/chat/media`), because half of what Telegram sends — a
// `.tgs` sticker, a WebM, a voice message — is nothing a browser can show by itself.
function mediaChip(md, big = false) {
  if (!md) return "";
  const q = md.ref ? `ref=${encodeURIComponent(md.ref)}` : `name=${encodeURIComponent(md.file)}`;
  const word = lab(`web.chat.media.${md.kind}`, md.kind);
  const len = md.duration ? ` ${Math.floor(md.duration / 60)}:${
    String(Math.round(md.duration % 60)).padStart(2, "0")}` : "";
  const pic = md.kind === "voice" ? "" :
    `<img src="/api/chat/media?${q}&size=${big ? 240 : 96}" alt="" loading="lazy">`;
  return `<span class="chat-media${big ? " big" : ""}">${pic}<i>${
    esc((md.emoji ? md.emoji + " " : "") + word + len)}</i></span>`;
}

function chatRowHTML(conv, m) {
  const open = conv.c === chatConv && m.i === chatSel;
  const n = chatFirst(conv.c) + m.i;
  const head = m.clear_before
    ? `<div class="chat-seam dim">${esc(lab("web.chat.cleared", "экран чистится"))}</div>` : "";
  const reply = m.reply_to >= 0 && conv.messages[m.reply_to]
    ? `<span class="chat-reply">↳ ${esc(trim(conv.messages[m.reply_to].text, 40))}</span>` : "";
  const react = (m.reactions || [])
    .map(([e, n]) => `<span class="chat-react">${esc(rxLabel(e))}${n > 1 ? n : ""}</span>`).join("");
  return head + `
<div class="chat-row${open ? " on" : ""}${n === chatNow ? " now" : ""}${m.pinned ? " pinned" : ""}"
     data-i="${m.i}" data-n="${n}" draggable="true">
  <div class="chat-line" data-open="${m.i}">
    <b class="chat-who">${esc(m.nick || m.persona || lab("web.chat.nobody", "—"))}</b>
    ${mediaChip(m.media)}
    <span class="chat-text">${esc(trim(chatDraft(conv.c, m), 160))}</span>
    ${reply}${react}
  </div>
  ${open ? chatEditHTML(m) : ""}
</div>`;
}

// Which conversation's tray of chat pictures is open under its title, or -1. One at
// a time and shut by default: the picture is set once and the row of thumbnails is
// the width of the list.
let chatConvPic = -1;

// A custom reaction is stored as `custom:<file>|<fallback>`; the lists show the
// fallback, which is what it stands for.
const rxLabel = (e) => e.startsWith("custom:") ? (e.split("|")[1] || "★") : e;

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
  ${m.media ? `<div class="chat-media-row">${mediaChip(m.media, true)}
    <button class="ghost danger" data-act="unmedia" data-i="${m.i}">${
      esc(lab("web.chat.media.drop", "убрать вложение"))}</button></div>` : ""}
  <textarea class="chat-area" data-text="${m.i}" rows="3">${esc(chatDraft(chatConv, m))}</textarea>
  ${m.source_text ? `<p class="dim chat-src">${esc(lab("web.chat.wassaid", "было"))}: ${
    esc(trim(m.source_text, 180))}</p>` : ""}
  <div class="row2">
    <label>${esc(lab("web.chat.who", "от кого"))}
      <select data-f="persona" data-i="${m.i}">${opts([""].concat(who), m.persona)}</select></label>
    <label>${esc(lab("web.chat.answers", "отвечает на"))}
      <select data-f="reply_to" data-i="${m.i}">${answers}</select></label>
  </div>
  ${chatVoiceHTML(m)}
  <div class="row2">
    <label>${esc(lab("web.chat.stamp", "время"))}
      <input type="time" data-f="stamp" data-i="${m.i}" value="${esc(m.stamp)}"></label>
    <label>${esc(lab("web.chat.react", "реакции"))}
      <span class="rx-edit">${(m.reactions || []).map(([e, n], k) => `
        <span class="rx-chip">
          <button class="rx-emoji" data-rx="pick" data-k="${k}" title="${
            esc(lab("web.chat.rx.swap", ""))}">${esc(rxLabel(e))}</button>
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
    <label title="${esc(lab("web.chat.nick.why", ""))}">${esc(lab("web.chat.nick", "имя только здесь"))}
      <input data-f="nick" data-i="${m.i}" value="${esc(m.nick)}" placeholder="${
        esc(m.persona || lab("web.chat.asthecard", "— как на карточке —"))}"></label>
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

// Who reads this message, how, and whether it has been read. Two selects that write
// ONE field (`ChatMsg.voice`, a voice spec): the voice, and which of its recordings —
// a delivery is the only intonation a cloning engine has (`config.models.VoiceConfig`)
// and it is spelled `card:delivery`, so both selects simply offer whole specs. Empty
// is "as this person reads", which the label spells out, since it is invisible
// otherwise.
function chatVoiceHTML(m) {
  const cards = chatOpts.voice_cards || [];
  const own = m.voice || "";
  const base = (spec) => (spec || "").split(":")[0];
  const card = cards.find((c) => c.name === base(own === "none" ? "" : own || m.speaks));
  // what this message would be read with if it chose nothing: the run's one voice
  // for everybody when there is one (`none` is nobody), otherwise the person's card
  const run = (CHAT.doc.run_settings || {}).chat_voice || "";
  const card0 = (CHAT.doc.cast.find((c) => c.name === m.persona) || {}).voice || "";
  const fallback = run === "none" ? "" : (run || card0);
  const sel = (value, list) => list.map(([v, w]) =>
    `<option value="${esc(v)}"${v === value ? " selected" : ""}>${esc(w)}</option>`).join("");
  const voices = [["", `${lab("web.chat.voice.theirs", "как у человека")}: ${
      fallback || lab("web.chat.silent", "не читается")}`],
    ["none", lab("web.chat.voice.none", "— не читать —")]]
    .concat(cards.map((c) => [c.name, c.name]));
  // the voice select shows the CARD of the message's own voice, delivery or not
  const shown = own === "none" ? "none" : base(own);
  const ways = card && card.deliveries.length
    ? [[own && own !== "none" ? base(own) : "", `${lab("web.chat.voice.usual", "как обычно")} (${
        card.default})`]].concat(card.deliveries.map((d) => [d.spec, d.which]))
    : [];
  const wayNow = own.includes(":") ? own : (ways[0] || [""])[0];
  const state = !m.speaks ? lab("web.chat.take.mute", "")
    : (!m.laid ? lab("web.chat.take.unlaid", "")
      : (m.take ? lab("web.chat.take.has", "") : lab("web.chat.take.none", "")));
  return `
  <div class="row2">
    <label>${esc(lab("web.chat.voice", "голос"))}
      <select data-f="voice" data-i="${m.i}">${sel(shown, voices)}</select></label>
    <label title="${esc(lab("web.chat.voice.way.why", ""))}">${esc(lab("web.chat.voice.way", "интонация"))}
      <select data-f="voice" data-i="${m.i}"${ways.length > 1 ? "" : " disabled"}>${
        sel(wayNow, ways)}</select></label>
  </div>
  <div class="chat-acts chat-take">
    <button class="ghost" data-act="revoice" data-i="${m.i}"${
      m.speaks && m.laid ? "" : " disabled"}>${esc(lab("web.chat.revoice", "🔊 переозвучить"))}</button>
    <span class="dim">${esc(state)}</span>
  </div>`;
}

// Who is in the video, and whether they are anybody outside it. A name with a card
// behind it has a picture and a voice that survive into the next video; one without is
// just a name on a bubble, and the button says which.
let chatWho = "";   // which person is open for editing, by name

function renderChatCast() {
  const d = CHAT.doc;
  // Who is in the conversation that is OPEN. The list used to pool every piece in the
  // video, so three imported chats put thirty strangers in one column and the one
  // list on the screen that should have followed the selection did not move when the
  // selection did. `here` is their line count in this piece; `lines` stays the whole
  // video's, because a card is a thing of the run and not of the piece.
  const here = (c) => (c.by_conv || [])[chatConv] || 0;
  const me = (d.settings || {}).me || "";
  // Two lists and no third. Who is in the conversation that is OPEN, and — apart —
  // the cards nobody uses anywhere in this video, which are the ones that can be
  // thrown away. Somebody who speaks in ANOTHER piece belongs to that piece's list
  // and is reached by opening it; carrying them here as a third group was carrying
  // the pooled list that switching the conversation was supposed to get rid of.
  const cast = d.cast.filter((c) => here(c) > 0);
  // the account the chat is being read from goes to the top, because it is the one
  // entry in the list that is about the whole picture rather than about one person
  cast.sort((a, b) => (b.name === me) - (a.name === me));
  const spare = d.cast.filter((c) => !c.lines);
  cq("#chat-cast").innerHTML =
    `<h4>${esc(lab("web.chat.cast", "кто в переписке"))}</h4>` +
    (cast.length
      ? cast.map((c) => personHTML(c, here(c), c.name === me)).join("")
      : `<p class="dim">${esc(lab("web.chat.nocast", ""))}</p>`) +
    (spare.length
      ? `<h4 class="quiet">${esc(lab("web.chat.cast.quiet", ""))}</h4>` +
        spare.map((c) => personHTML(c, 0, false)).join("")
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
function personHTML(c, here, isMe) {
  const open = c.name === chatWho;
  // how much they say HERE, and — when that is not the whole story — how much in the
  // video altogether, because a card is edited for the run and not for this piece
  const count = here
    ? (c.lines > here ? `${here} / ${c.lines}` : `${here}`)
    : (c.lines ? `0 / ${c.lines}` : "0");
  const head = `
    <div class="chat-person${c.carded ? " carded" : ""}${open ? " on" : ""}${
      here ? "" : " quiet"}${isMe ? " main" : ""}" data-person="${esc(c.name)}">
      <b>${esc(c.name)}</b>
      ${isMe ? `<span class="tag">${esc(lab("web.chat.who.me", "это я"))}</span>` : ""}
      <span class="dim">${count}</span>
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
      <label title="${esc(lab("web.chat.who.name.why", ""))}">${esc(lab("web.chat.who.name", "ник"))}
        <input data-rename-to="${esc(c.name)}" value="${esc(c.name)}"></label>
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
        ${CHAT.doc.tree ? `<label>${esc(lab("web.chat.who.handle", "u/ник"))}
          <input data-p="handle" value="${esc(c.handle)}"></label>` : ""}
        <label>${esc(lab("web.chat.who.colour", "цвет"))}
          <input data-p="colour" value="${esc(c.colour)}" placeholder="#rrggbb"></label>
      </div>
      <div class="chat-acts">
        <button class="ghost${isMe ? " on" : ""}" data-me="${esc(c.name)}">${
          esc(lab(isMe ? "web.chat.who.unme" : "web.chat.who.beme", ""))}</button>
        <span class="grow"></span>
        ${c.lines ? "" : `<button class="ghost warn" data-drop-who="${esc(c.name)}">${
          esc(lab("web.chat.who.drop", "удалить карточку"))}</button>`}
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
// the conversations, the drawing knows only the order they are shown in. Nothing
// while the conversation is playing: the playhead owns the frame then, and an edit
// made under it would yank the picture off the line that is up.
function shootChat(at = -1) {
  if (!CHAT || chatPlaying) return;
  const n = chatFirst(chatConv) + (at < 0 ? (chatActive() || { lines: 0 }).lines - 1 : at);
  if (n >= 0) chatShow(n);
}

// -- frames, fetched ahead -----------------------------------------------------
//
// One frame per message, kept until the conversation changes, so playing it back
// asks the server for each line once and scrubbing back over it asks for nothing.
// Fetched rather than set as an <img> source because the next ones are wanted
// BEFORE the playhead gets to them — a frame asked for on arrival lands a few hundred
// milliseconds into a message that may only be up for one second — and with the
// token as a header: in the query it would be `t`, which this endpoint reads as a
// second of the video.
const chatFrames = new Map();   // n -> Promise<object URL>

function chatFrame(n) {
  if (chatFrames.has(n)) return chatFrames.get(n);
  const o = { credentials: "same-origin" };
  if (TOK) o.headers = { "X-Slopgen-Token": TOK };
  const got = fetch(`/api/runs/${CHAT.id}/chat/preview?video=${CHAT.video}&at=${n}`, o)
    .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(r.statusText))))
    .then((b) => URL.createObjectURL(b));
  got.catch(() => chatFrames.delete(n));
  chatFrames.set(n, got);
  return got;
}

// The cache answers for a conversation that no longer exists the moment anything in
// it is edited, so every new document throws it away.
function chatForget() {
  for (const got of chatFrames.values()) got.then((u) => URL.revokeObjectURL(u), () => {});
  chatFrames.clear();
  // and the reel, which is of the same conversation (see chat_reel.js)
  reelStale();
}

let chatWant = -1;   // the frame last asked to be on screen

function chatShow(n) {
  chatWant = n;
  // only if it is still the one wanted: a slow frame must not land over a later one
  chatFrame(n).then((u) => { if (chatWant === n) cq("#chat-shot-img").src = u; }, () => {});
  if (!chatPlaying) return;
  const ahead = chatPace().at.length;
  for (let k = 1; k <= 3 && n + k < ahead; k++) chatFrame(n + k);
}

// The settings, with the frame in front of you. Drawn from what the server says it
// will accept (`chat_api.SHEET`), so a setting added there reaches this screen without
// touching this file — the browser knows the four kinds of control and nothing about
// what any of them mean.
function renderChatSettings() {
  const d = CHAT.doc;
  const v = d.settings || {};
  const rows = (d.sheet || []).filter(
    (r) => (!r.when || r.when === v.skin) && (!r.aspect || r.aspect === v.aspect)).map((r) => {
    // A heading and not a setting. Twenty-two controls in a narrow column with nothing
    // between them is a list nobody reaches the bottom of — which is how "there is no
    // way to turn the split screen on" happens to a screen that has one.
    if (r.head) return `<h5>${esc(lab(r.head, ""))}</h5>`;
    if (r.note) return `<p class="dim">${esc(lab(r.note, ""))}</p>`;
    if (r.hide) return "";   // answered elsewhere; see SHEET
    // a row of the run's own (`chat_api.RUN_FIELDS`) reads the run's value
    const val = r.run ? (d.run_settings || {})[r.f] : v[r.f];
    if (r.kind === "check")
      return `<label class="inline"><input type="checkbox" data-s="${r.f}"${
        val ? " checked" : ""}> ${esc(lab(r.l, r.f))}</label>`;
    if (r.kind === "number")
      return `<label>${esc(lab(r.l, r.f))}<input type="number" data-s="${r.f}"
        min="${r.min}" max="${r.max}" step="${r.step || 1}" value="${esc(String(val))}"></label>`;
    if (r.kind === "select") {
      // `cast` is the only list that comes out of the document rather than out of
      // the config store: it is whoever is in this video. Everybody, not just this
      // conversation's — the setting belongs to the run, and the account being read
      // from is as likely to be in the third piece as in the first.
      const source = r.opts === "cast"
        ? (CHAT.doc.cast || []).map((c) => c.name)
        : (chatOpts[r.opts] || []);
      // a list may come as {v, note} pairs (the ads do); the value is what is stored
      const list = (r.blank ? [""] : []).concat(source.map((x) => (x && x.v !== undefined ? x.v : x)));
      // the blank choice is named by `blank_l` whether or not the others are looked
      // up — `opt_l` on "" asked for the label of the bare prefix and printed "w."
      // `words` names the rest the way the start form does (`word`, as "none" is
      // "тишина" on the music), so the two screens say the same thing
      const named = (o) => (o === "" ? lab(r.blank_l || "w.none", "— нет —")
        : (r.opt_l ? lab(r.opt_l + o, o) : (r.words ? word(o) : o)));
      // the two lists that are pictures get a picker that shows them (see `wirePicks`)
      const pics = { avatars: "avatars", chat_backgrounds: "chat_bg" }[r.opts];
      return `<label>${esc(lab(r.l, r.f))}<select data-s="${r.f}"${
        pics ? ` data-pics="${pics}"` : ""}>${list.map((o) =>
        `<option value="${esc(o)}"${o === val ? " selected" : ""}>${esc(named(o))}</option>`
      ).join("")}</select></label>`;
    }
    return `<label>${esc(lab(r.l, r.f))}<input data-s="${r.f}" value="${esc(String(val || ""))}"></label>`;
  });
  cq("#chat-set").innerHTML =
    `<h4>${esc(lab("web.chat.settings", "настройки ролика"))}</h4>` + rows.join("") +
    `<p class="dim">${esc(lab("web.chat.settings.note", ""))}</p>`;
  wirePicks(cq("#chat-set"));
}

// -- playing it ------------------------------------------------------------
//
// The timeline is the list. A rail down its left edge, one stretch of it per message
// and exactly as tall as that message's row, so the playhead is never anywhere but ON
// the line that is up. The price is an even speed, and it is paid on purpose: the
// head crawls down a line read for six seconds, runs down a one-word one and jumps
// the seam between two chats — and that unevenness is the very thing being judged
// when a conversation is watched before it is made, which is how long each line holds.
//
// The pace is the server's (`chat_api._pace`): the real clock once the scenes are
// laid and voiced, and until then the same rules guessed at, which the clock beside
// the button marks with a `≈`.

// The reel's clock when there is a reel — it is the clock its pictures and sounds are
// laid on — and the document's until then.
const chatPace = () => (REEL
  ? { exact: REEL.exact, total: REEL.total, at: REEL.marks, dur: REEL.durs }
  : (CHAT && CHAT.doc.pace) || { exact: false, total: 0, at: [], dur: [] });
let chatGeo = [];     // n -> {top, h}: where that message's row is, down the list
let chatAdGeo = new Map();  // message an ad follows -> {top, h, row} of its separator
let chatLoopAt = 0;   // performance.now() of the last step

function drawRail() {
  const rail = cq("#chat-rail");
  rail.querySelectorAll(".seg").forEach((x) => x.remove());
  chatGeo = [];
  if (!CHAT) return;
  const msgs = cq("#chat-msgs");
  const top = msgs.getBoundingClientRect().top;
  const p = chatPace();
  msgs.querySelectorAll(".chat-row[data-n]").forEach((row) => {
    const n = +row.dataset.n;
    const b = row.getBoundingClientRect();
    chatGeo[n] = { top: b.top - top, h: b.height };
    const seg = document.createElement("div");
    seg.className = "seg";
    seg.dataset.n = n;
    seg.style.top = `${b.top - top}px`;
    seg.style.height = `${b.height}px`;
    if (p.at[n] !== undefined)
      seg.title = `${p.at[n].toFixed(1)}s · ${p.dur[n].toFixed(1)}s`;
    rail.appendChild(seg);
  });
  // the native ads, each a stretch of the rail where its separator is in the list
  chatAdGeo = new Map();
  msgs.querySelectorAll(".chat-adsep[data-ad-after]").forEach((row) => {
    const b = row.getBoundingClientRect();
    chatAdGeo.set(+row.dataset.adAfter, { top: b.top - top, h: b.height, row });
    const seg = document.createElement("div");
    seg.className = "seg ad";
    seg.style.top = `${b.top - top}px`;
    seg.style.height = `${b.height}px`;
    rail.appendChild(seg);
  });
  rail.hidden = !chatGeo.length;
  chatNow = -1;   // re-mark the rows just drawn, without asking for a frame
  chatPlace(true);
}

// Put the head where `chatT` says, and — when that is a different message than
// before — say so: the row lit, the frame of that message up, the row scrolled to.
function chatPlace(quiet = false) {
  const p = chatPace();
  const head = cq("#chat-rail-head");
  if (!p.at.length || !chatGeo.length) {
    head.hidden = true;
    chatClock();
    return;
  }
  chatT = Math.min(Math.max(chatT, 0), p.total);
  let lo = 0, hi = p.at.length - 1;   // the last message to have started by now
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (p.at[mid] <= chatT + 1e-6) lo = mid; else hi = mid - 1;
  }
  const n = lo;
  const g = chatGeo[n];
  head.hidden = !g;
  // inside a native ad the head is on the ad's own stretch, not crawling down the
  // last message of the chat before it
  const ad = REEL && (REEL.ads || []).find((a) => chatT >= a.at && chatT < a.at + a.dur);
  const ag = ad && chatAdGeo.get(ad.after);
  cq("#chat-msgs").querySelectorAll(".chat-adsep.now").forEach((r) => r.classList.remove("now"));
  if (ag) {
    head.style.top = `${ag.top + ((chatT - ad.at) / ad.dur) * ag.h}px`;
    ag.row.classList.add("now");
  } else if (g) {
    const end = (REEL && (REEL.ads || []).find((a) => a.after === n)) || null;
    // the message before an ad holds until the ad starts, not until the next message
    const span = end ? Math.max(end.at - p.at[n], 1e-3) : p.dur[n];
    const k = span > 0 ? Math.min(Math.max((chatT - p.at[n]) / span, 0), 1) : 0;
    head.style.top = `${g.top + k * g.h}px`;
  }
  if (n !== chatNow) {
    chatNow = n;
    chatMark();
    if (!quiet) chatArrive(n);
  }
  chatClock();
  // standing still, the reel shows the instant the head is on; playing, `chatStep`
  // draws every frame anyway
  if (!chatPlaying) reelDraw(chatT);
}

function chatMark() {
  const msgs = cq("#chat-msgs");
  msgs.querySelectorAll(".chat-row.now").forEach((r) => r.classList.remove("now"));
  const row = msgs.querySelector(`.chat-row[data-n="${chatNow}"]`);
  if (row) row.classList.add("now");
  cq("#chat-rail").querySelectorAll(".seg").forEach((s) => {
    s.classList.toggle("past", +s.dataset.n < chatNow);
    s.classList.toggle("now", +s.dataset.n === chatNow);
  });
  // and which piece it is in, on the list of pieces
  let c = -1;
  for (let k = 0, first = 0; k < chatConvs().length; first += chatConvs()[k].lines, k++)
    if (chatNow >= first && chatNow < first + chatConvs()[k].lines) c = k;
  cq("#chat-convs").querySelectorAll(".chat-conv").forEach(
    (el) => el.classList.toggle("playing", +el.dataset.conv === c));
}

function chatArrive(n) {
  if (!chatWatching && !REEL) chatShow(n);
  if (!chatPlaying) return;
  const row = cq(`#chat-msgs .chat-row[data-n="${n}"]`);
  if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function chatClock() {
  const p = chatPace();
  const clock = cq("#chat-clock");
  clock.textContent = p.at.length
    ? `${chatT.toFixed(1)} / ${p.total.toFixed(1)}s${p.exact ? "" : " ≈"}` : "";
  clock.title = lab(p.exact ? "web.chat.pace.exact" : "web.chat.pace.guess", "");
  const go = cq("#chat-play");
  go.textContent = lab(chatPlaying ? "web.chat.pause" : "web.chat.play", "");
  go.disabled = !p.at.length;
}

function chatStep(now) {
  if (!chatPlaying) return;
  if (chatWatching) {
    chatT = cq("#chat-video").currentTime;
  } else {
    // The reel's sound sets the clock while it is actually running. A browser that
    // will not start sound without a gesture leaves the context suspended and its
    // clock standing, and the picture must not stand with it.
    chatT = reelLive() ? reelNow() : chatT + (now - chatLoopAt) / 1000;
    if (chatT >= chatPace().total) {
      chatT = chatPace().total;
      chatPlace();
      return chatPause();
    }
  }
  chatLoopAt = now;
  chatPlace();
  if (REEL && !chatWatching) { reelDraw(chatT); reelKeepMusic(); }
  requestAnimationFrame(chatStep);
}

function chatPlay() {
  if (!CHAT || !chatPace().at.length || chatPlaying) return;
  // with the cut on screen the cut is what plays, and its own events start the clock
  if (chatWatching) return void cq("#chat-video").play().catch(() => {});
  if (chatT >= chatPace().total - 1e-3) chatT = 0;   // from the top, once it has ended
  chatPlaying = true;
  chatLoopAt = performance.now();
  chatNow = -1;   // arrive at the current line afresh: its frame, and the next ones
  if (REEL) reelSound(chatT);
  chatPlace();
  requestAnimationFrame(chatStep);
}

function chatPause() {
  const v = cq("#chat-video");
  if (chatWatching && !v.paused) v.pause();
  chatPlaying = false;
  reelSilence();
  reelDraw(chatT);   // which also stops the clips where they are
  chatClock();
}

// The head was moved while playing: every sound starts again from where it now is.
function chatJumped() {
  if (chatPlaying && REEL && !chatWatching) reelSound(chatT);
}

// Put the head at the start of a message, which is where opening one should leave
// it: pressing play after picking a line plays from that line.
function chatSeek(n) {
  const p = chatPace();
  if (p.at[n] === undefined) return;
  chatT = p.at[n];
  if (chatWatching) cq("#chat-video").currentTime = chatT;
  chatPlace(true);
  chatJumped();
}

cq("#chat-play").onclick = () => (chatPlaying ? chatPause() : chatPlay());

// Scrubbing: a point on the rail is a point inside one message's row, and so a moment
// inside that message's time. A point on a title bar between two chats is the start
// of the first message under it.
function chatSeekY(e) {
  if (!chatGeo.length) return;
  const p = chatPace();
  const y = e.clientY - cq("#chat-msgs").getBoundingClientRect().top;
  // a point on an ad's separator is a moment of that ad
  for (const [after, ag] of chatAdGeo) {
    const ad = REEL && (REEL.ads || []).find((a) => a.after === after);
    if (ad && y >= ag.top && y < ag.top + ag.h) {
      chatT = ad.at + ((y - ag.top) / ag.h) * ad.dur;
      if (chatWatching) cq("#chat-video").currentTime = chatT;
      chatPlace();
      chatJumped();
      return;
    }
  }
  let n = chatGeo.findIndex((g) => g && y < g.top + g.h);
  if (n < 0) n = chatGeo.length - 1;
  const g = chatGeo[n];
  if (!g || p.at[n] === undefined) return;
  chatT = p.at[n] + Math.min(Math.max((y - g.top) / g.h, 0), 1) * p.dur[n];
  if (chatWatching) cq("#chat-video").currentTime = chatT;
  chatPlace();
  chatJumped();
}

cq("#chat-rail").addEventListener("pointerdown", (e) => {
  cq("#chat-rail").setPointerCapture(e.pointerId);
  chatSeekY(e);
});
cq("#chat-rail").addEventListener("pointermove", (e) => { if (e.buttons) chatSeekY(e); });

// A row grows when it is opened, a textarea is dragged taller, the window narrows —
// and every one of those moves where the messages after it sit on the rail.
new ResizeObserver(() => { if (CHAT && !cq("#chat").hidden) drawRail(); })
  .observe(cq("#chat-msgs"));

// -- watching the real thing -----------------------------------------------
//
// The still is exact about everything except the one thing the format is made of:
// movement. A message arriving a piece at a time, the view rolling up after it, the
// swipe between two chats — none of that is in a frame, and no amount of frames makes
// it. So once there is a cut, the room plays it — on the same rail, which follows the
// video and drives it.
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
  chatPlaying = false;
  cq("#chat-shot-img").hidden = false;
  renderChatWatch();
  reelShowing();
  reelDraw(chatT);
  chatClock();
}

cq("#chat-watch").onclick = () => {
  if (chatWatching) return showStill();
  chatPause();
  chatWatching = true;
  const v = cq("#chat-video");
  // cache-busted, because the whole point of pressing this is to see the cut that was
  // just made and not the one the browser kept from before `сборка` ran
  v.src = tokd(`/api/runs/${CHAT.id}/video?x=${Date.now()}`);
  v.hidden = false;
  cq("#chat-shot-img").hidden = true;
  reelShowing();
  // from where the playhead stands, so the rail and the picture start out agreeing
  v.addEventListener("loadedmetadata", () => { v.currentTime = chatT; }, { once: true });
  renderChatWatch();
  v.play().catch(() => {});
};

// The video's own controls count as much as the button: whichever starts it, the
// rail follows, and whichever stops it, the rail stops.
cq("#chat-video").addEventListener("play", () => {
  if (!chatWatching || chatPlaying) return;
  chatPlaying = true;
  chatLoopAt = performance.now();
  chatClock();
  requestAnimationFrame(chatStep);
});
for (const ev of ["pause", "ended"])
  cq("#chat-video").addEventListener(ev, () => { chatPlaying = false; chatClock(); });
cq("#chat-video").addEventListener("seeked", () => {
  if (!chatWatching) return;
  chatT = cq("#chat-video").currentTime;
  chatPlace();
});

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
    // Hand the run to the supervisor rather than walking the chain from here. Pressing
    // the stages one at a time out of the browser worked, and told you nothing: each
    // call is a single request that can take minutes, so the room sat on a disabled
    // button, and the runs list — the one place in this program that draws a progress
    // bar, streams the log and offers to stop — went on calling the run `ждёт`, because
    // from its side nothing had started. There was no way to tell building from hung.
    const rows = await api("/api/runs");
    const row = rows.find((r) => r.id === CHAT.id);
    if (!row || !row.run_dir) throw new Error(lab("web.chat.build.nodir", "нет папки"));
    await api(`/api/runs/${CHAT.id}/resume`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ run_dir: row.run_dir }),
    });
    say(lab("web.chat.build.started", ""));
    // …and out to the list, which is where it can now be watched. Staying put would
    // leave the operator looking at the one screen that cannot show what is happening.
    closeChat();
    return;
  } catch (e) {
    say(e.message, true);
  } finally {
    go.disabled = false;
    go.textContent = was;
  }
}

cq("#chat-build").onclick = buildChat;
cq("#chat-close").onclick = closeChat;
// Drawn again from scratch — the frames kept for playing are thrown away with it —
// at whichever message the playhead is on, or the open one when it is on none.
cq("#chat-shot").onclick = () => {
  chatForget();
  chatNow >= 0 ? chatShow(chatNow) : shootChat(chatSel);
};
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

cq("#chat-convs").addEventListener("click", async (e) => {
  const slot = e.target.closest("[data-adslot]");
  if (slot) {
    const c = +slot.dataset.adslot;
    const on = !(chatConvs()[c] || {}).ad_after;
    await chatDo("/conversation/ad", { method: "PUT", body: { c, on } });
    return;
  }
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
  // A name on the list is a place in the list below: it scrolls there, and makes
  // that the piece being worked on — whose people the cast column shows.
  const pick = e.target.closest("[data-conv]");
  if (!pick) return;
  chatTake(+pick.dataset.conv);
  renderChat();
  chatScrollTo(chatConv);
  shootChat();
});

// Whichever conversation a gesture in the list lands in is the one being worked on,
// before any handler reads it: every request carries `c`, and the list now holds all
// of them. The open message belongs to its conversation, so moving to another one
// closes it — an index in one piece means nothing in the next.
function chatTake(c) {
  if (c === chatConv) return;
  chatConv = c;
  chatSel = -1;
  chatConvPic = -1;
}

for (const ev of ["click", "change", "input", "dragstart"])
  cq("#chat-list").addEventListener(ev, (e) => {
    const sec = e.target.closest("[data-sec]");
    if (sec) chatTake(+sec.dataset.sec);
  }, true);

cq("#chat-list").addEventListener("click", async (e) => {
  const open = e.target.closest("[data-open]");
  if (open) {
    const i = +open.dataset.open;
    chatSel = chatSel === i ? -1 : i;
    renderChat();
    if (chatSel >= 0) {
      chatSeek(chatFirst(chatConv) + chatSel);
      shootChat(chatSel);
    }
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
  if (what === "revoice") {
    // one line of synthesis is seconds to a minute, so the button says it is working
    act.disabled = true;
    act.textContent = lab("web.chat.revoicing", "озвучиваю…");
    await chatDo("/voice", { body: { i } });
    return shootChat(chatSel);
  }
  if (what === "drop") { chatSel = -1; await chatDo("/message", { method: "DELETE", body: { i } }); }
  else if (what === "restamp") { await chatDo("/restamp", { body: { step: 1 } }); }
  else if (what === "unmedia") {
    await chatDo("/message", { method: "PUT", body: { i, drop_media: true } });
  }
  else if (what === "split") {
    chatSel = -1;
    const was = chatConv;
    const d = await chatDo("/split", { body: { i } });
    if (d) { chatConv = was + 1; renderChat(); chatScrollTo(chatConv); }
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
// A line moves within its own conversation and no further: a reply never crosses a
// seam, and taking a line to another chat is a cut and a paste, not a drag.
let chatDrag = -1;
const chatSameSec = (row) => row && +row.closest("[data-sec]").dataset.sec === chatConv;

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
  if (!chatSameSec(row) || +row.dataset.i === chatDrag) return;
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
  if (!chatSameSec(row) || from < 0) return;
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
  if (area) chatDrafts.set(draftKey(chatConv, +area.dataset.text), area.value);
});

cq("#chat-list").addEventListener("click", async (e) => {
  if (e.target.closest(".chat-convpic")) {
    chatConvPic = chatConvPic === chatConv ? -1 : chatConv;
    return renderChat();
  }
  const pic = e.target.closest("[data-convpic]");
  if (!pic) return;
  chatConvPic = -1;
  await chatDo("/conversation", { method: "PUT", body: { avatar: pic.dataset.convpic } });
  shootChat(chatSel);
});

cq("#chat-list").addEventListener("change", async (e) => {
  const title = e.target.closest(".chat-convtitle");
  if (title) return void await chatDo("/conversation", { method: "PUT", body: { title: title.value } });
  const area = e.target.closest("[data-text]");
  if (area) {
    const i = +area.dataset.text;
    chatDrafts.delete(draftKey(chatConv, i));
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
  const be = e.target.closest("[data-me]");
  if (be) {
    // pressing it on the one who already is turns it off, which is the only way back
    // to a conversation where nobody's messages take the right-hand side
    const now = (CHAT.doc.settings || {}).me === be.dataset.me ? "" : be.dataset.me;
    await chatDo("/settings", { method: "PUT", body: { me: now } });
    return shootChat(chatSel);
  }
  const rm = e.target.closest("[data-drop-who]");
  if (rm) {
    const who = rm.dataset.dropWho;
    // asked twice in place, as every other removal on this page is: the button is
    // the confirmation, pressed again within a few seconds
    if (!armTwice(rm, who, lab("web.chat.who.dropsure", "").replace("%s", who))) return;
    chatWho = "";
    await chatDo("/persona", { method: "DELETE", body: { name: who } });
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

// The name, typed in place and applied when the field is left or Enter is pressed —
// it was a dialog box over the page, which showed the current name only inside
// itself and was gone the moment it was answered. Cleared, it springs back: a
// person has to be called something.
cq("#chat-cast").addEventListener("change", async (e) => {
  const f = e.target.closest("[data-rename-to]");
  if (!f) return;
  const was = f.dataset.renameTo;
  const now = f.value.trim();
  if (!now || now === was) { f.value = was; return; }
  chatWho = now;
  const d = await chatDo("/rename", { body: { was, now } });
  if (!d) { chatWho = was; f.value = was; return; }
  shootChat(chatSel);
});
cq("#chat-cast").addEventListener("keydown", (e) => {
  const f = e.target.closest("[data-rename-to]");
  if (!f) return;
  if (e.key === "Enter") f.blur();
  if (e.key === "Escape") { f.value = f.dataset.renameTo; f.blur(); }
});

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
  // The stage reports on the RUN's own stream (see `montage_api.run_stage`), which
  // the runs list draws as a bar and this screen did not look at: voicing forty lines
  // was a minute of a grey button. It is read from the same place and written on the
  // button itself — "озвучка 12/40".
  const id = CHAT.id;
  const tick = setInterval(async () => {
    try {
      const row = (await api("/api/runs")).find((r) => r.id === id);
      const p = row && row.progress;
      if (p && p.total) b.textContent = `${word(b.dataset.stage)} ${p.done}/${p.total}`;
    } catch (err) { /* the bar is a courtesy; the stage goes on without it */ }
  }, 800);
  try {
    await api(`/api/runs/${CHAT.id}/montage/stage`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ video: CHAT.video, stage: b.dataset.stage }),
    });
    say(`${word(b.dataset.stage)} ✓`);
  } catch (err) { say(err.message, true); }
  finally {
    clearInterval(tick);
    b.disabled = false;
    b.textContent = word(b.dataset.stage);
  }
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
    chatScrollTo(chatConv);
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
      ${f.chats > 1 ? `<span class="dim">${f.chats} ${esc(lab("web.chat.exports.chats", "чатов"))}</span>` : ""}
      <span class="grow"></span>
      <span class="dim">${Math.max(1, Math.round(f.size / 1024))} KB</span>
      <button class="ghost exp-del" data-del="${esc(f.unit)}" title="${
        esc(lab("web.cfg.exports.del", ""))}">✕</button>
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
    say(out.already ? `${out.name} — ${lab("web.cfg.exports.already")}` : `${out.name} · ${out.format}`);
  } catch (err) { say(err.message, true); }
  e.target.value = "";
  await loadExports();
};

cq("#chat-exp-list").addEventListener("click", async (e) => {
  // the ✕ first, so that removing an export does not also open it
  const del = e.target.closest("[data-del]");
  if (del) {
    if (await dropChatExport(del, del.dataset.del)) {
      if (chatExpFile === del.dataset.del || chatExpFile.startsWith(del.dataset.del + "/"))
        chatExpFile = "";
      loadExports();
    }
    return;
  }
  const row = e.target.closest("[data-exp]");
  if (!row || !CHAT) return;
  chatExpFile = row.dataset.exp;
  let d;
  try {
    d = await api(`/api/runs/${CHAT.id}/chat/exports/read?video=${CHAT.video}` +
                  `&name=${encodeURIComponent(chatExpFile)}`);
  } catch (err) { return say(err.message, true); }
  chatExpPieces = d.pieces;
  // One chat is simply opened: there is nothing to choose between.
  if (chatExpPieces.length === 1) return openPick("export", chatExpFile, chatExpPieces[0].p);
  await loadExports();
  cq("#chat-exp-pieces").innerHTML = "";  // a fresh list: no filter left from the last one
  renderExpPieces();
});

// The chats of a multi-chat export, one row each, opened by clicking — an account
// export is forty chats, and ticking boxes to then open only the first ticked one
// was a form where a list belonged. The filter is for the same forty.
function renderExpPieces() {
  const box = cq("#chat-exp-pieces");
  const q = (cq("#chat-exp-find") && cq("#chat-exp-find").value || "").trim().toLowerCase();
  const shown = chatExpPieces.filter((p) => !q ||
    `${p.title} ${p.who.join(" ")}`.toLowerCase().includes(q));
  const find = chatExpPieces.length > 6
    ? `<input id="chat-exp-find" type="search" value="${esc(q)}" placeholder="${
        esc(lab("web.chat.exports.find", "найти чат"))}">` : "";
  if (!cq("#chat-exp-find") || chatExpPieces.length <= 6) box.innerHTML = find + `<div id="chat-exp-rows"></div>`;
  cq("#chat-exp-rows").innerHTML = chatExpPieces.length ? shown.map((p) => `
    <div class="exp-piece" data-piece="${p.p}">
      <span class="what">
        <b>${esc(p.title || lab("web.chat.untitled", "без названия"))} · ${p.lines}</b>
        <i>${esc(p.who.join(", "))}</i>
        <i>${esc(p.first)}</i>
      </span>
    </div>`).join("")
    : `<p class="dim">${esc(lab("web.chat.exports.empty", ""))}</p>`;
  cq("#chat-exp-go").hidden = true;
}

cq("#chat-exp-pieces").addEventListener("input", (e) => {
  if (e.target.id === "chat-exp-find") renderExpPieces();
});

// An export's pieces are opened and chosen from like everything else. Taking a whole
// one unread was the same mistake in a smaller coat: an exported chat is a year of
// somebody's messages and the video wants nine of them.
cq("#chat-exp-pieces").addEventListener("click", async (e) => {
  const row = e.target.closest("[data-piece]");
  if (!row || !CHAT) return;
  await openPick("export", chatExpFile, +row.dataset.piece);
});

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
  if (d) {
    chatConv = d.conversations.length - 1; chatSel = -1;
    renderChat(); chatScrollTo(chatConv); shootChat();
  }
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
    chatScrollTo(chatConv);
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
      ${mediaChip(ln.media)}
      <span class="what">${esc(ln.text)}</span>
      ${ln.reactions.length ? `<span class="rx">${
        esc(ln.reactions.map(([e, n]) => rxLabel(e) + (n > 1 ? n : "")).join(" "))}</span>` : ""}
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
    title: PICK.title, avatar: PICK.avatar || "",
    source: `${PICK.source}:${PICK.where}`, lines } });
  if (d) {
    closePick();
    cq("#chat-src-box").hidden = true;
    chatConv = Math.max(0, d.conversations.length - 1);
    chatSel = -1;
    renderChat();
    chatScrollTo(chatConv);
    say(`${lines.length} ${lab("web.chat.lines", "сообщений")}`);
    shootChat();
  }
};
