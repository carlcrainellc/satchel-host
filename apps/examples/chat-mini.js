// Demo app "Chat (last 5)" 1.2: the five newest messages in this room, one line each, and a
// box to post. Times are in your own time zone (Satchel 0.2.1+ sends them with each record;
// older builds don't, so those show UTC and say so). Deleted messages never reach the app.
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
const MAX = 500;
const pad = (n) => String(n).padStart(2, "0");
const utcHm = (ts) => { const d = new Date(Number(ts) * 1000); return pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()); };
const timeOf = (m) => (m.time ? String(m.time) : (Number.isFinite(Number(m.ts)) ? utcHm(m.ts) + " UTC" : "--:--"));
const bodyOf = (r) => (r && r.body && typeof r.body === "object" ? r.body : {});

function lines() {
  const recs = coll.read({types: ["room.msg", "room.join"], limit: MAX, tail: true}) || [];
  const nick = {};
  for (const r of recs) {
    const b = bodyOf(r);
    if (r.t === "room.join" && b.nick) nick[r.author] = String(b.nick);
  }
  const who = (r) => (r.who ? String(r.who) : nick[r.author] || String(r.author || "?").slice(0, 8));
  return recs.filter((r) => r.t === "room.msg").slice(-5).map((m) => ({
    t: "item", s: m.mine ? "accent" : undefined,
    v: `[${timeOf(m)}] <${who(m)}${m.you || m.mine ? " (you)" : ""}> ${String(bodyOf(m).text || "")}${m.sending ? (m.sent ? " (sent)" : " (sending…)") : ""}`,
  })).map((x) => (x.s === undefined ? (delete x.s, x) : x));
}

function draw() {
  let items;
  try { items = lines(); }
  catch (err) {
    log("draw failed: " + (err && err.message));
    panel.render({t: "col", c: [
      {t: "heading", v: "Last 5"},
      {t: "text", v: "Couldn't draw the messages here (" + String(err && err.message || err).slice(0, 200) + ").", s: "card"},
      {t: "text", v: "The room is fine. Use the Built-in chat button at the top to read it.", s: "small muted"},
      {t: "button", id: "retry", v: "Try again"},
    ]});
    return;
  }
  panel.render({t: "col", c: [
    {t: "heading", v: "Last 5"},
    items.length ? {t: "list", c: items} : {t: "para", v: "No messages yet. Say hello.", s: "muted"},
    {t: "row", c: [{t: "input", id: boxId(), v: unsent, ph: "Say something"}, {t: "button", id: "send", v: "Say", s: "send"}]},
    {t: "text", v: status || "Times in your time zone.", s: "small muted"},
  ]});
}

onView(draw);
onEvent((e) => {
  if (e.kind === "click" && e.id === "send") {
    const text = typed(e);
    if (!text) status = "Write something first.";
    else if (text.length > 500) status = "Messages can be up to 500 characters.";
    else { records.draft("room.msg", {text}); sent(text); status = "Sending…"; }
  } else if (e.kind === "click" && e.id === "retry") status = "";
  else if (e.kind === "draft") { answered(e); status = e.ok ? "" : NOT_SENT(e); }
  else return;
  draw();
});
draw();
