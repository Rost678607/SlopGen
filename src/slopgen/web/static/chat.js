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
  if (!cq("#chat-set").hidden) renderChatSettings();
  cq("#chat-state").textContent = chatState();
  const conv = chatActive();
  const list = cq("#chat-list");
  if (!conv) {
    list.innerHTML = `<p class="dim chat-empty">${esc(lab("web.chat.noconv", ""))}</p>`;
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
<div class="chat-row${open ? " on" : ""}${m.pinned ? " pinned" : ""}" data-i="${m.i}">
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
      <input data-f="stamp" data-i="${m.i}" value="${esc(m.stamp)}"></label>
    <label>${esc(lab("web.chat.react", "реакции"))}
      <input data-react="${m.i}" value="${esc((m.reactions || []).map(([e, n]) => n > 1 ? `${e}${n}` : e).join(" "))}"
             placeholder="🔥3 ❤️"></label>
  </div>
  ${d.votes ? `<label>${esc(lab("web.chat.score", "карма"))}
      <input type="number" data-f="score" data-i="${m.i}" value="${m.score}"></label>` : ""}
  <div class="row2">
    <label>${esc(lab("web.chat.nick", "ник в этом сообщении"))}
      <input data-f="nick" data-i="${m.i}" value="${esc(m.nick)}" placeholder="${
        esc(lab("web.chat.asthecard", "— как на карточке —"))}"></label>
    <label class="inline"><input type="checkbox" data-f="clear_before" data-i="${m.i}"${
      m.clear_before ? " checked" : ""}> ${esc(lab("web.chat.clearhere", "чистить экран здесь"))}</label>
  </div>
  <div class="chat-acts">
    <button class="ghost" data-act="up" data-i="${m.i}">↑</button>
    <button class="ghost" data-act="down" data-i="${m.i}">↓</button>
    <button class="ghost" data-act="split" data-i="${m.i}">${esc(lab("web.chat.split", "разрезать здесь"))}</button>
    <button class="ghost" data-act="after" data-i="${m.i}">${esc(lab("web.chat.after", "＋ ниже"))}</button>
    <span class="grow"></span>
    <button class="ghost danger" data-act="drop" data-i="${m.i}">${esc(lab("web.chat.drop", "убрать"))}</button>
  </div>
</div>`;
}

// Who is in the video, and whether they are anybody outside it. A name with a card
// behind it has a picture and a voice that survive into the next video; one without is
// just a name on a bubble, and the button says which.
function renderChatCast() {
  const d = CHAT.doc;
  cq("#chat-cast").innerHTML =
    `<h4>${esc(lab("web.chat.cast", "кто в переписке"))}</h4>` +
    (d.cast.length ? d.cast.map((c) => `
      <div class="chat-person${c.carded ? " carded" : ""}">
        <b>${esc(c.name)}</b>
        <span class="dim">${c.lines}</span>
        <span class="dim">${esc(c.voice || lab("web.chat.silent", "не читается"))}</span>
        <span class="grow"></span>
        <button class="ghost" data-card="${esc(c.name)}">${
          esc(lab(c.carded ? "web.chat.editcard" : "web.chat.makecard", "карточка"))}</button>
      </div>`).join("")
     : `<p class="dim">${esc(lab("web.chat.nocast", ""))}</p>`);
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
      const d = await chatDo("/conversation", { body: { at: -1 } });
      if (d) { chatConv = d.conversations.length - 1; chatSel = -1; renderChat(); }
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
  else if (what === "up" && i > 0) { chatSel = i - 1; await chatDo("/move", { body: { i, to: i - 1 } }); }
  else if (what === "down") { chatSel = i + 1; await chatDo("/move", { body: { i, to: i + 1 } }); }
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
  const react = e.target.closest("[data-react]");
  if (react) {
    await chatDo("/message", { method: "PUT",
      body: { i: +react.dataset.react, reactions: parseReactions(react.value) } });
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

// `🔥3 ❤️ 👍2` — the emoji, each with how many of it. Written as one field because
// that is how it reads on the bubble, and because two parallel lists of unequal length
// is the shape every reaction editor gets wrong.
function parseReactions(text) {
  return (text || "").split(/\s+/).filter(Boolean).map((bit) => {
    const m = bit.match(/^(.*?)(\d+)$/);
    return m && m[1] ? [m[1], +m[2]] : [bit, 1];
  });
}

cq("#chat-cast").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-card]");
  if (!b) return;
  const name = b.dataset.card;
  const was = CHAT.doc.cast.find((c) => c.name === name) || {};
  // One prompt per field is crude and it is also the honest size of this: a card is
  // four short strings, and a modal with four inputs would be a modal to maintain.
  const voice = prompt(lab("web.chat.ask.voice", "голос (пусто — не читается)"), was.voice || "");
  if (voice === null) return;
  const avatar = prompt(lab("web.chat.ask.avatar", "аватарка из assets/avatars"), was.avatar || "");
  if (avatar === null) return;
  await chatDo("/persona", { method: "PUT", body: { name, voice, avatar } });
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
  shootChat(chatSel);
});

// -- the paste box ---------------------------------------------------------

cq("#chat-paste").onclick = () => {
  cq("#chat-paste-text").value = "";
  cq("#chat-paste-box").hidden = false;
  cq("#chat-paste-text").focus();
};
cq("#chat-paste-cancel").onclick = () => { cq("#chat-paste-box").hidden = true; };
cq("#chat-paste-go").onclick = async () => {
  const text = cq("#chat-paste-text").value;
  if (!text.trim()) return;
  const d = await chatDo("/import", { body: { text } });
  cq("#chat-paste-box").hidden = true;
  if (d) say(`${d.added} ${lab("web.chat.lines", "сообщений")}`);
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

cq("#chat-exports").onclick = async () => {
  chatExpFile = "";
  chatExpPieces = [];
  cq("#chat-exp-pieces").innerHTML = `<p class="dim">${esc(lab("web.chat.exports.pick", ""))}</p>`;
  cq("#chat-exp-go").hidden = true;
  cq("#chat-exp-box").hidden = false;
  await loadExports();
};
cq("#chat-exp-cancel").onclick = () => { cq("#chat-exp-box").hidden = true; };

async function loadExports() {
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

cq("#chat-exp-go").onclick = async () => {
  const want = [...cq("#chat-exp-pieces").querySelectorAll("[data-piece]")]
    .filter((b) => b.checked).map((b) => +b.dataset.piece);
  if (!want.length || !CHAT) return;
  const d = await chatDo("/exports/take", { body: { name: chatExpFile, pieces: want } });
  cq("#chat-exp-box").hidden = true;
  if (d) {
    chatConv = Math.max(0, d.conversations.length - want.length);
    chatSel = -1;
    renderChat();
    say(`${d.added} ${lab("web.chat.exports.added", "")}`);
    shootChat();
  }
};

// -- the live sources ------------------------------------------------------
//
// Reddit and a signed-in Telegram account, in the same sheet as the exports base
// because it is the same gesture: look at what is there, take one. The difference is
// that one of them has to be signed into first, and that sign-in is three steps —
// Telegram sends a code and waits for it, so no form can be filled in once.

let chatSrc = "reddit";

cq("#chat-fetch").onclick = async () => {
  cq("#chat-src-box").hidden = false;
  cq("#chat-src-rows").innerHTML = "";
  const sorts = cq("#chat-src-sort");
  sorts.innerHTML = ["hot", "top", "new", "rising"]
    .map((s) => `<option value="${s}">${s}</option>`).join("");
  await pickSource(chatSrc);
};
cq("#chat-src-cancel").onclick = () => { cq("#chat-src-box").hidden = true; };

cq("#chat-src-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("[data-src]");
  if (b) pickSource(b.dataset.src);
});

async function pickSource(which) {
  chatSrc = which;
  cq("#chat-src-tabs").querySelectorAll("[data-src]").forEach(
    (b) => b.classList.toggle("on", b.dataset.src === which));
  cq("#chat-src-rows").innerHTML = "";
  const tg = which === "telegram";
  const made = which === "invent";
  cq("#chat-src-sort").hidden = tg || made;
  cq("#chat-src-q").placeholder = tg ? lab("web.chat.src.tgwhere", "@channel") : "r/AskReddit";
  cq("#chat-tg-login").hidden = !tg;
  cq("#chat-invent").hidden = !made;
  // nothing is browsed when the conversation is being written: there is no list of
  // what is out there, only a topic and a model
  cq("#chat-src-where").hidden = made || (tg && true);
  if (tg) await renderTg();
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

// The sign-in, drawn from where it has got to. One field at a time, because that is
// what the thing IS: a number, then a code Telegram sends to it, then a password for
// the accounts that have one.
async function renderTg() {
  let s;
  try { s = await api("/api/chat/telegram"); } catch (e) { return say(e.message, true); }
  const state = cq("#chat-tg-state");
  const step = cq("#chat-tg-step");
  cq("#chat-src-where").hidden = !s.signed_in;
  if (!s.keys) {
    state.textContent = lab("web.chat.tg.nokeys", "");
    step.innerHTML = "";
    return;
  }
  if (s.signed_in) {
    state.textContent = `${lab("web.chat.tg.as", "вошли как")} ${s.who}`;
    step.innerHTML = `<button class="ghost" id="chat-tg-out">${
      esc(lab("web.chat.tg.out", "выйти"))}</button>
      <button class="primary" id="chat-tg-dialogs">${
      esc(lab("web.chat.src.look", "посмотреть"))}</button>`;
    cq("#chat-tg-out").onclick = async () => {
      try { await api("/api/chat/telegram/logout", { method: "POST" }); }
      catch (e) { say(e.message, true); }
      await renderTg();
    };
    cq("#chat-tg-dialogs").onclick = () => browseSource("");
    return;
  }
  const waiting = s.step === "code" ? "code" : s.step === "password" ? "password" : "phone";
  state.textContent = lab(`web.chat.tg.${waiting}`, "");
  step.innerHTML = `<input id="chat-tg-in" ${waiting === "password" ? 'type="password"' : ""}>
    <button class="primary" id="chat-tg-go">${esc(lab("web.chat.tg.go", "дальше"))}</button>`;
  cq("#chat-tg-go").onclick = async () => {
    const value = cq("#chat-tg-in").value.trim();
    if (!value) return;
    const at = { phone: "/login", code: "/code", password: "/password" }[waiting];
    const key = waiting;
    try {
      await api(`/api/chat/telegram${at}`, { method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ [key]: value }) });
    } catch (e) { say(e.message, true); }
    await renderTg();
  };
  cq("#chat-tg-in").focus();
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
  if (!b || !CHAT) return;
  b.disabled = true;
  const d = await chatDo("/fetch", { body: { source: chatSrc, where: b.dataset.take } });
  b.disabled = false;
  if (d) {
    cq("#chat-src-box").hidden = true;
    chatConv = Math.max(0, d.conversations.length - 1);
    chatSel = -1;
    renderChat();
    say(`${d.added} ${lab("web.chat.exports.added", "")}`);
    shootChat();
  }
});
