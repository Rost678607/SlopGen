// The chat room.
//
// Every other screen in this page edits what a stage produced. This one edits the
// thing the video IS: a conversation is not an input to a chat run, it is the run's
// whole material, and a chat run made by hand starts empty — so this is where the
// video comes into existence rather than where it is adjusted.
//
// It is a list and not a timeline, which is the opposite choice from the montage room
// next door, and for the same reason that room is a timeline: you edit a thing in the
// shape it has. A conversation is an ordered list of messages, the gestures that
// matter are "say this instead", "this answers that" and "move this up", and all
// three survive being typed. What does NOT survive being typed is what it will look
// like, so the preview is a real frame drawn by the real renderer (`/chat/preview`),
// one request away whenever the arrangement has changed enough to be worth a look.
//
// Every edit is one request and the reply is the WHOLE document, as it is next door
// and for a sharper reason: a reply points at a message by index, so dropping or
// moving one rewrites pointers all over the list, and a screen patching one row would
// be showing a conversation that no longer exists.

let CHAT = null;          // {id, title, video, doc}
let chatSel = -1;         // which message is open for editing, by index
const chatDrafts = new Map();  // typed-but-not-saved text, by index

const cq = (s) => document.querySelector(s);
const chatMsg = (i) => (CHAT && CHAT.doc.messages[i]) || null;
const chatDraft = (m) => (chatDrafts.has(m.i) ? chatDrafts.get(m.i) : m.text);

// ---------------------------------------------------------------- opening it

async function openChat(id, title, video = 0) {
  let d;
  try {
    d = await api(`/api/runs/${id}/chat?video=${video}`);
  } catch (e) { return say(e.message, true); }
  CHAT = { id, title, video, doc: d };
  chatSel = -1;
  chatDrafts.clear();
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

// One request, one whole document back. `quiet` is for the edits that happen while
// somebody is typing elsewhere on the screen — it redraws the list without stealing
// the caret back to the top.
async function chatDo(path, opts = {}, quiet = false) {
  if (!CHAT) return null;
  const body = Object.assign({ video: CHAT.video }, opts.body || {});
  try {
    const d = await api(`/api/runs/${CHAT.id}/chat${path}`, {
      method: opts.method || "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    CHAT.doc = d;
    if (!quiet) renderChat();
    return d;
  } catch (e) { say(e.message, true); return null; }
}

// ---------------------------------------------------------------- drawing it

function renderChat() {
  if (!CHAT) return;
  const d = CHAT.doc;
  renderChatStages();
  renderChatCast();
  cq("#chat-state").textContent = chatState(d);
  const list = cq("#chat-list");
  list.innerHTML = d.messages.map(chatRowHTML).join("")
    + `<button class="ghost chat-add" data-add="${d.messages.length}">${
         esc(lab("web.chat.add", "＋ сообщение"))}</button>`;
  if (!d.messages.length)
    list.insertAdjacentHTML("afterbegin",
      `<p class="dim chat-empty">${esc(lab("web.chat.empty", ""))}</p>`);
}

function chatState(d) {
  const bits = [`${d.messages.length} ${lab("web.chat.lines", "сообщений")}`];
  if (d.excerpts > 1) bits.push(`${d.excerpts} ${lab("web.chat.excerpts", "куска")}`);
  // what stands between this conversation and a video, said on the screen rather
  // than three stages later in a traceback
  for (const why of d.blocking || []) bits.push(lab("web.chat.block." + why, why));
  return bits.join(" · ");
}

function chatRowHTML(m) {
  const d = CHAT.doc;
  const open = m.i === chatSel;
  const head = [];
  if (m.excerpt !== (d.messages[m.i - 1] || { excerpt: m.excerpt }).excerpt || m.i === 0)
    head.push(`<div class="chat-seam">${esc(lab("web.chat.excerpt", "кусок"))} ${m.excerpt + 1}</div>`);
  else if (m.clear_before)
    head.push(`<div class="chat-seam dim">${esc(lab("web.chat.cleared", "экран чистится"))}</div>`);
  const reply = m.reply_to >= 0 && d.messages[m.reply_to]
    ? `<span class="chat-reply">↳ ${esc(trim(d.messages[m.reply_to].text, 40))}</span>` : "";
  const react = (m.reactions || [])
    .map(([e, n]) => `<span class="chat-react">${esc(e)}${n > 1 ? n : ""}</span>`).join("");
  return head.join("") + `
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
  const who = d.cast.map((c) => c.name);
  if (m.persona && !who.includes(m.persona)) who.push(m.persona);
  const opts = (list, value) => list.map((v) =>
    `<option value="${esc(v)}"${v === value ? " selected" : ""}>${esc(v || "—")}</option>`).join("");
  const answers = [`<option value="-1">${esc(lab("web.chat.noreply", "— никому —"))}</option>`]
    .concat(d.messages.filter((o) => o.i !== m.i).map((o) =>
      `<option value="${o.i}"${o.i === m.reply_to ? " selected" : ""}>${
        esc(`${o.persona}: ${trim(o.text, 30)}`)}</option>`)).join("");
  return `
<div class="chat-edit">
  <textarea class="chat-area" data-text="${m.i}" rows="3">${esc(chatDraft(m))}</textarea>
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
    <button class="ghost" data-act="split" data-i="${m.i}">${esc(lab("web.chat.split", "новый кусок отсюда"))}</button>
    <button class="ghost" data-act="after" data-i="${m.i}">${esc(lab("web.chat.after", "＋ ниже"))}</button>
    <span class="grow"></span>
    <button class="ghost danger" data-act="drop" data-i="${m.i}">${esc(lab("web.chat.drop", "убрать"))}</button>
  </div>
</div>`;
}

// Who is in this conversation, and whether they are anybody outside it. A name with a
// card behind it has a picture and a voice that survive into the next video; one
// without is just a name on a bubble, and the button says which.
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

async function shootChat(at = -1) {
  if (!CHAT || !CHAT.doc.messages.length) return;
  const img = cq("#chat-shot-img");
  img.src = tokd(`/api/runs/${CHAT.id}/chat/preview?video=${CHAT.video}&at=${at}&t=${Date.now()}`);
}

// ---------------------------------------------------------------- the gestures

cq("#chat-close").onclick = closeChat;
cq("#chat-shot").onclick = () => shootChat(chatSel);

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
    if (d) { chatSel = d.messages.length - 1; renderChat(); }
    return;
  }
  const act = e.target.closest("[data-act]");
  if (!act) return;
  const i = +act.dataset.i;
  const what = act.dataset.act;
  if (what === "drop") { chatSel = -1; await chatDo("/message", { method: "DELETE", body: { i } }); }
  else if (what === "up" && i > 0) { chatSel = i - 1; await chatDo("/move", { body: { i, to: i - 1 } }); }
  else if (what === "down") { chatSel = i + 1; await chatDo("/move", { body: { i, to: i + 1 } }); }
  else if (what === "split") await chatDo("/split", { body: { i } });
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
  const d = CHAT.doc;
  return d.messages.length ? d.messages[d.messages.length - 1].persona : "";
}

// The text box commits on blur, not on every keystroke: the reply is the whole
// document and a redraw mid-sentence would take the caret with it. The draft survives
// the redraws in between (see `chatDrafts`), so the only way to lose what you typed is
// to say so.
cq("#chat-list").addEventListener("input", (e) => {
  const area = e.target.closest("[data-text]");
  if (area) chatDrafts.set(+area.dataset.text, area.value);
});

cq("#chat-list").addEventListener("change", async (e) => {
  const area = e.target.closest("[data-text]");
  if (area) {
    const i = +area.dataset.text;
    chatDrafts.delete(i);
    await chatDo("/message", { method: "PUT", body: { i, text: area.value } });
    return shootChat(chatSel);
  }
  const react = e.target.closest("[data-react]");
  if (react) {
    const i = +react.dataset.react;
    await chatDo("/message", { method: "PUT", body: { i, reactions: parseReactions(react.value) } });
    return shootChat(chatSel);
  }
  const f = e.target.closest("[data-f]");
  if (!f) return;
  const i = +f.dataset.i;
  const key = f.dataset.f;
  let value = f.type === "checkbox" ? f.checked : f.value;
  if (key === "reply_to" || key === "score") value = +value;
  await chatDo("/message", { method: "PUT", body: { i, [key]: value } });
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
  const avatar = prompt(lab("web.chat.ask.avatar", "аватарка из assets/avatars (без расширения)"), was.avatar || "");
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
  // the stage wrote the job; the room's document is whatever is on it now
  await reloadChat();
  shootChat(chatSel);
});

// Re-read the document without losing where you were. `openChat` resets the selection
// and the drafts, which is right when the room opens and wrong after a stage has run
// underneath you — the line you were looking at is still the line you were looking at.
async function reloadChat() {
  if (!CHAT) return;
  try {
    CHAT.doc = await api(`/api/runs/${CHAT.id}/chat?video=${CHAT.video}`);
  } catch (e) { return say(e.message, true); }
  if (chatSel >= CHAT.doc.messages.length) chatSel = -1;
  renderChat();
}

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
