// Demo app "Harbor Chat" 1.4: a chat room with a sidebar (rooms, places, who's here,
// contacts) and bubbles. It reads the room's own records (room.msg, room.join, room.topic)
// and posts room.msg. It shows the sesh connect API: "Connect" asks Satchel to connect this
// site to you; Satchel shows its own sheet. With the contacts and seal grants it lists your
// contacts and can send them mail, and seal a private note; Satchel asks every time.
// 1.2: a record it can't read is skipped, and if drawing ever fails it shows why and a
// "Try again" button instead of an empty panel.
// 1.3: a 🎙 button next to Send (Satchel builds with sesh.voice()). It asks Satchel to open its
// own recorder for this room; you record, listen and send there. The app never gets the audio.
// 1.4: the message box clears when you press Send (and gets the text back if it didn't go),
// and the status says "Sending…": with "Don't ask again" ticked there's no sheet to approve.
// Your post shows at once, marked "sending…" then "sent", until it is in the room's log.
const canVoice = typeof sesh.voice === "function";
// The composer: a fresh id after each send clears it (a redraw keeps typed text otherwise);
// if the post didn't go, the text comes back so nothing is lost.
let box = 0, unsent = "", sending = "";
const boxId = () => (box ? "text-" + box : "text");
const typed = (e) => String((e.fields || {})[boxId()] || "").trim();
const sent = (text) => { sending = text; unsent = ""; box++; };
const answered = (e) => { if (!e.ok && !e.queued && sending) unsent = sending; sending = ""; };
const NOT_SENT = (e) => (e.queued ? "Not confirmed yet: Satchel will send the same post again." : "Not sent (" + e.error + "). Your text is back in the box.");
let status = "", me = null, grants = {}, here = null, contacts = null, compose = null, note = null;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// Satchel 0.2.1+ sends who, time and day in your own time zone. Older ones don't: say UTC.
const oldDay = (ts) => { const d = new Date(ts * 1000); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} (UTC)`; };
const oldHm = (ts) => { const d = new Date(ts * 1000); return String(d.getUTCHours()).padStart(2, "0") + ":" + String(d.getUTCMinutes()).padStart(2, "0"); };
const initial = (n) => (String(n).match(/[A-Za-z0-9]/) || ["?"])[0].toUpperCase();
const hue = (k) => "hue" + ((parseInt(String(k).slice(0, 2), 16) || 0) % 6 + 1);
const avatar = (name, key) => ({t: "text", v: initial(name), s: "avatar " + hue(key)});

function load() {
  const recs = (coll.read({types: ["room.msg", "room.join", "room.topic"], limit: 500, tail: true}) || [])
    .filter((r) => r && typeof r.author === "string" && r.body && typeof r.body === "object");
  const nick = {}, names = {}, you = {}, seen = [];
  let topic = "";
  for (const r of recs) {
    if (r.t === "room.join" && r.body.nick) nick[r.author] = String(r.body.nick);
    if (r.who) names[r.author] = r.who;
    if (r.you) you[r.author] = true;
    if (r.t === "room.topic") topic = String(r.body.text || "");
    const i = seen.indexOf(r.author); if (i >= 0) seen.splice(i, 1); seen.push(r.author);
  }
  const who = (a) => names[a] || nick[a] || a.slice(0, 8);
  return {msgs: recs.filter((r) => r.t === "room.msg").slice(-150), who, you, topic, seen: seen.slice(-8).reverse()};
}

function side(who, you, seen) {
  const c = [{t: "text", v: "Harbor Chat", s: "bold"}];
  const rooms = here ? here.places.filter((p) => p.kind === "room") : [];
  const other = here ? here.places.filter((p) => p.kind !== "room") : [];
  c.push({t: "text", v: "ROOMS", s: "small muted"});
  if (rooms.length) for (const p of rooms) c.push({t: "button", id: "go-" + p.id, v: "# " + p.title, s: p.current ? "active" : undefined});
  else c.push({t: "button", id: "room", v: "# " + (here && here.place ? here.place.title : "Lobby"), s: "active"});
  if (other.length) {
    c.push({t: "text", v: "PLACES ON " + String(here.host).toUpperCase(), s: "small muted"});
    for (const p of other) c.push({t: "button", id: "go-" + p.id, v: p.title});
  }
  c.push({t: "text", v: "HERE TODAY", s: "small muted"});
  for (const a of seen) c.push({t: "row", c: [avatar(who(a), a), {t: "text", v: who(a) + (you[a] ? " (you)" : "")}]});
  if (contacts) {
    c.push({t: "text", v: "CONTACTS", s: "small muted"});
    if (!contacts.length) c.push({t: "text", v: "No contacts yet.", s: "small muted"});
    contacts.slice(0, 12).forEach((x, i) => c.push({t: "button", id: "mail-" + i, v: "✉ " + x.name}));
  }
  if (me && grants.seal) c.push({t: "button", id: "note", v: "🔒 Private note"});
  c.push({t: "text", v: me ? "Connected as " + me : "Not connected", s: "small muted"});
  if (!me || !grants.contacts) c.push({t: "button", id: "connect", v: me ? "Show my contacts" : "Connect"});
  return {t: "col", s: "side", c: c.map((x) => (x.s === undefined ? (delete x.s, x) : x))};
}

const pending = (m) => (m && m.sending ? (m.sent ? " · sent" : " · sending…") : "");   // Satchel 0.2.4+: your post before it is in the log
function bubbles(msgs, who) {
  const rows = [];
  let lastDay = "";
  for (const m of msgs) {
    const d = m.day || oldDay(m.ts), t = m.time || oldHm(m.ts);
    if (d !== lastDay) { rows.push({t: "text", v: d, s: "sep"}); lastDay = d; }
    const bubble = {t: "col", s: m.mine ? "bubble mine" : "bubble", c: [
      {t: "text", v: (m.mine ? t : who(m.author) + " · " + t) + pending(m), s: m.mine ? "small muted right" : "small muted"},
      {t: "text", v: String(m.body.text || ""), s: "wrap"},
    ]};
    rows.push({t: "row", s: m.mine ? "mine tight" : "tight", c: m.mine ? [bubble] : [avatar(who(m.author), m.author), bubble]});
  }
  return rows;
}

function mainCol(title, topic, rows) {
  const c = [{t: "row", s: "head", c: [{t: "text", v: "# " + title, s: "bold"}, ...(topic ? [{t: "text", v: topic, s: "muted small"}] : [])]}];
  if (compose) {
    c.push({t: "col", s: "grow", c: [
      {t: "text", v: "Mail to " + compose.name, s: "bold"},
      {t: "text", v: "Satchel seals it and asks you before it goes.", s: "small muted"},
      {t: "input", id: "subject", v: "", ph: "Subject"},
      {t: "textarea", id: "body", v: "", ph: "Write to " + compose.name},
      {t: "row", c: [{t: "button", id: "mail-send", v: "Send mail", s: "send"}, {t: "button", id: "mail-cancel", v: "Back to chat"}]},
    ]});
  } else if (note) {
    c.push({t: "col", s: "grow", c: [
      {t: "text", v: "Private note", s: "bold"},
      {t: "text", v: "Sealed for you only. This tab keeps the sealed text until you close it.", s: "small muted"},
      ...(note.sealed ? [{t: "text", v: note.sealed.slice(0, 60) + "…", s: "mono small"},
        ...(note.opened ? [{t: "text", v: note.opened, s: "card wrap"}] : [{t: "button", id: "note-open", v: "Open it"}])]
        : [{t: "textarea", id: "note-text", v: "", ph: "Only you can read this"}, {t: "button", id: "note-seal", v: "Seal it", s: "send"}]),
      {t: "button", id: "note-close", v: "Back to chat"},
    ]});
  } else {
    c.push({t: "col", s: "grow", c: rows.length ? rows : [{t: "para", v: "No messages yet. Say hello.", s: "muted"}]});
    c.push({t: "row", s: "pill", c: [{t: "input", id: boxId(), v: unsent, ph: "Message " + title}, {t: "button", id: "send", v: "Send", s: "send"},
      ...(canVoice ? [{t: "button", id: "mic", v: "🎙"}] : [])]});
  }
  if (status) c.push({t: "text", v: status, s: "small muted"});
  return {t: "col", s: "main", c};
}

function drawAll() {
  const {msgs, who, you, topic, seen} = load();
  const title = here && here.place ? here.place.title : "Lobby";
  panel.render({t: "row", c: [side(who, you, seen), mainCol(title, topic, bubbles(msgs, who))]});
}
// Never leave the panel empty: if drawing fails, say so and offer a way on.
function draw() {
  try { drawAll(); } catch (err) {
    const why = String((err && err.message) || err).slice(0, 200);
    log("draw failed: " + why);
    panel.render({t: "col", s: "main", c: [
      {t: "row", s: "head", c: [{t: "text", v: "Harbor Chat", s: "bold"}]},
      {t: "col", s: "card", c: [
        {t: "text", v: "This app couldn't draw the records here.", s: "bold"},
        {t: "text", v: why, s: "small muted wrap"},
        {t: "text", v: "Nothing is lost: the records are safe on the host. Try again, or open the place's built-in view.", s: "small wrap"},
      ]},
      {t: "button", id: "retry", v: "Try again", s: "send"},
    ]});
  }
}

onView(draw);
onEvent((e) => {
  const id = e.kind === "click" ? e.id : null;
  if (id === "retry") { status = "";
  } else if (id === "send") {
    const text = typed(e);
    if (!text) { status = "Write a message first."; draw(); return; }
    if (text.length > 500) { status = "Messages can be up to 500 characters."; draw(); return; }
    sesh.post("room.msg", {text}); sent(text);
    status = "Sending…";
  } else if (id === "mic") {
    if (!me || !grants.post) { sesh.connect({post: true, seal: true, contacts: true}); status = "Connect first, then press 🎙 again."; }
    else { sesh.voice("mic"); status = "Opening Satchel's recorder…"; }
  } else if (id === "connect") {
    sesh.connect({post: true, seal: true, contacts: true});
    status = "Answer Satchel's sheet to connect…";
  } else if (id && id.startsWith("go-")) {
    sesh.visit(id.slice(3));
  } else if (id && id.startsWith("mail-") && /^mail-\d+$/.test(id)) {
    const c = contacts && contacts[Number(id.slice(5))];
    if (c) { compose = c; note = null; status = ""; }
  } else if (id === "mail-cancel") { compose = null; status = "";
  } else if (id === "mail-send") {
    if (!compose) return;
    sesh.sendMail(compose.key, (e.fields.subject || "").trim(), e.fields.body || "");
    status = "Check and approve the mail in the sheet…";
  } else if (id === "note") { note = {}; compose = null; status = "";
  } else if (id === "note-close") { note = null; status = "";
  } else if (id === "note-seal") {
    const text = (e.fields["note-text"] || "").trim();
    if (!text) { status = "Write something first."; draw(); return; }
    sesh.seal(text, ["me"], "note");
    status = "Approve sealing in Satchel's sheet…";
  } else if (id === "note-open" && note && note.sealed) {
    sesh.open([note.sealed], "note");
    status = "Approve opening in Satchel's sheet…";
  } else if (e.kind === "connect") {
    if (e.ok && e.me) { me = e.me.name; grants = e.grants || {}; if (grants.contacts) sesh.contacts(); }
    status = e.ok ? "" : "Not connected (" + e.error + ").";
  } else if (e.kind === "contacts") {
    contacts = e.ok ? e.list : null;
    if (!e.ok) status = "No contacts (" + e.error + ").";
  } else if (e.kind === "here") {
    if (e.ok) here = e;
  } else if (e.kind === "visit") {
    if (!e.ok) status = "Couldn't open that place (" + e.error + ").";
  } else if (e.kind === "mail") {
    status = e.ok ? "Mail sent" + (compose ? " to " + compose.name : "") + "." : "Not sent (" + e.error + ").";
    if (e.ok) compose = null;
  } else if (e.kind === "seal") {
    if (e.ok && note) note.sealed = e.sealed;
    status = e.ok ? "Sealed." : "Not sealed (" + e.error + ").";
  } else if (e.kind === "open") {
    const it = e.ok && e.items && e.items[0];
    if (it && note) note.opened = it.text;
    status = e.ok ? (it ? "" : "That note isn't for you.") : "Not opened (" + e.error + ").";
  } else if (e.kind === "voice") {
    status = e.ok ? "Record in Satchel's window, listen, then Send there." : "No recorder (" + e.error + ").";
  } else if (e.kind === "draft") {
    answered(e); status = e.ok ? "" : NOT_SENT(e);
  } else return;
  draw();
});
// Older Satchel builds don't know here(); the sidebar then shows just this room.
try { sesh.here(); } catch (err) { log("no here(): " + err.message); }
draw();
