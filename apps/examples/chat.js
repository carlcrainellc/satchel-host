// ---- shared by the demo apps: times, names and a screen that is never blank ----
// Satchel 0.2.1+ sends each record's who, time and day in your own time zone. Older builds
// don't (the sandbox clock is UTC), so then the time says UTC.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n) => String(n).padStart(2, "0");
const okTs = (r) => !!r && Number.isFinite(Number(r.ts));
const timeOf = (r) => (r && r.time ? String(r.time) : okTs(r) ? (() => { const d = new Date(Number(r.ts) * 1000); return pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()) + " UTC"; })() : "");
const dayOf = (r) => (r && r.day ? String(r.day) : okTs(r) ? (() => { const d = new Date(Number(r.ts) * 1000); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`; })() : "");
const whenOf = (r) => [dayOf(r), timeOf(r)].filter(Boolean).join(" · ");
const bodyOf = (r) => (r && r.body && typeof r.body === "object" ? r.body : {});
const textOf = (r, k = "text") => { const v = bodyOf(r)[k]; return v === undefined || v === null ? "" : String(v); };
const whoOf = (r) => (r && r.who ? String(r.who) : String((r && r.author) || "someone").slice(0, 8)) + (r && (r.you || r.mine) ? " (you)" : "");
const initial = (n) => (String(n).match(/[A-Za-z0-9]/) || ["?"])[0].toUpperCase();
const hue = (k) => "hue" + ((parseInt(String(k || "").slice(0, 2), 16) || 0) % 6 + 1);
const initials = (n) => { const p = String(n).split(/[\s._-]+/).filter(Boolean); return ((p.length > 1 ? p[0][0] + p[1][0] : p[0] ? p[0].slice(0, 2) : "") || "?").toUpperCase(); };
const avatar = (name, key) => ({t: "text", v: initials(name), s: "avatar " + hue(key)});
const read = (types, limit = 500, tail = true) => (coll.read({types, limit, tail}) || []).filter((r) => r && typeof r === "object");
const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
// Draw with fn(); if it throws, show what went wrong and a way on instead of an empty panel.
function safely(title, fn) {
  return function () {
    try { fn(); } catch (err) {
      const why = String((err && err.message) || err).slice(0, 200);
      log("draw failed: " + why);
      panel.render({t: "col", s: "main", c: [
        {t: "row", s: "head", c: [{t: "text", v: title, s: "bold"}]},
        {t: "col", s: "card", c: [
          {t: "text", v: "This app couldn't draw the records here.", s: "bold"},
          {t: "text", v: why, s: "small muted wrap"},
          {t: "text", v: "Nothing is lost: the records are safe on the host. Try again, or open the place's built-in view.", s: "small wrap"},
        ]},
        {t: "button", id: "retry", v: "Try again", s: "send"},
      ]});
    }
  };
}
// ---- end of the shared part ----

// Demo app "Chat" 1.2: a messenger-style view of a chat room. It reads the room's own records
// (room.msg, room.join, room.topic) and posts room.msg, so it shows the same messages as the
// built-in room view, drawn as bubbles with day separators. Times are in your own time zone.
// Removed messages never reach the app.
let status = "";
// 1.2: the box clears on Send and gets the text back if the post didn't go; "Sending…", not
// "approve": with "Don't ask again" ticked no sheet appears. Your post shows at once, marked
// "sending…" then "sent", until it is in the room's log (Satchel 0.2.4+).
// The composer: a fresh id after each send clears it (a redraw keeps typed text otherwise);
// if the post didn't go, the text comes back so nothing is lost.
let box = 0, unsent = "", sending = "";
const boxId = () => (box ? "text-" + box : "text");
const typed = (e) => String((e.fields || {})[boxId()] || "").trim();
const sent = (text) => { sending = text; unsent = ""; box++; };
const answered = (e) => { if (!e.ok && !e.queued && sending) unsent = sending; sending = ""; };
const NOT_SENT = (e) => (e.queued ? "Not confirmed yet: Satchel will send the same post again." : "Not sent (" + e.error + "). Your text is back in the box.");
function load() {
  const recs = read(["room.msg", "room.join", "room.topic"]);
  const nick = {};
  let topic = "";
  for (const r of recs) {
    if (r.t === "room.join" && textOf(r, "nick")) nick[r.author] = textOf(r, "nick");
    if (r.t === "room.topic") topic = textOf(r);
  }
  return {msgs: recs.filter((r) => r.t === "room.msg").slice(-150), nick, topic};
}
const draw = safely("Chat", () => {
  const {msgs, nick, topic} = load();
  const rows = [];
  let lastDay = "", lastAuthor = "";
  for (const m of msgs) {
    const d = dayOf(m);
    if (d !== lastDay) { rows.push({t: "text", v: d, s: "sep"}); lastDay = d; lastAuthor = ""; }
    const mine = !!(m.mine || m.you), who = m.who || nick[m.author] || String(m.author || "someone").slice(0, 8);
    const first = m.author !== lastAuthor;
    lastAuthor = m.author;
    const bubble = {t: "col", s: mine ? "bubble mine" : "bubble", c: [
      ...(first && !mine ? [{t: "text", v: who, s: "bold small"}] : []),
      {t: "text", v: textOf(m), s: "wrap"},
      {t: "text", v: timeOf(m) + (m.sending ? (m.sent ? " · sent" : " · sending…") : ""), s: "small muted right"},
    ]};
    rows.push({t: "row", s: mine ? "mine tight" : "tight", c: mine ? [bubble] : [first ? avatar(who, m.author) : {t: "text", v: "", s: "avatar muted"}, bubble]});
  }
  panel.render({t: "col", s: "main", c: [
    {t: "row", s: "head", c: [{t: "text", v: "Chat", s: "bold"}, ...(topic ? [{t: "text", v: topic, s: "muted small"}] : [])]},
    {t: "col", s: "grow", c: rows.length ? rows : [{t: "para", v: "No messages yet. Say hello.", s: "muted"}]},
    {t: "row", s: "pill", c: [{t: "input", id: boxId(), v: unsent, ph: "Message"}, {t: "button", id: "send", v: "Send", s: "send"}]},
    {t: "text", v: status || "Times in your time zone.", s: "small muted"},
  ]});
});
onView(draw);
onEvent((e) => {
  if (e.kind === "click" && e.id === "send") {
    const text = typed(e);
    if (!text) status = "Write a message first.";
    else if (text.length > 500) status = "Messages can be up to 500 characters.";
    else { records.draft("room.msg", {text}); sent(text); status = "Sending…"; }
  } else if (e.kind === "click" && e.id === "retry") status = "";
  else if (e.kind === "draft") { answered(e); status = e.ok ? "Sent." : NOT_SENT(e); }
  draw();
});
draw();
