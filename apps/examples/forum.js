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

// Demo app "Forum" 1.1. Topics and replies are app.data records in this collection:
//   {kind: "topic", text: "Title\nfirst post"}       a new topic
//   {kind: "reply", text: "..."} with refs [topic id]  a reply
// Times are in your own time zone. A removed topic never reaches the app; its replies are
// counted but not shown.
let status = "", open = null, shown = [];
function load() {
  const recs = read(["app.data"]);
  const topics = [], byId = {};
  let orphans = 0;
  for (const r of recs) {
    if (bodyOf(r).kind !== "topic") continue;
    const lines = textOf(r).split("\n");
    const t = {r, id: r.id, title: lines[0].trim() || "(no title)", text: lines.slice(1).join("\n").trim(), replies: [], last: r};
    topics.push(t); byId[t.id] = t;
  }
  for (const r of recs) {
    if (bodyOf(r).kind !== "reply") continue;
    const t = byId[(r.refs || [])[0]];
    if (!t) { orphans++; continue; }
    t.replies.push(r);
    if (Number(r.ts) > Number(t.last.ts)) t.last = r;
  }
  return {topics: topics.sort((a, b) => Number(b.last.ts) - Number(a.last.ts)), orphans};
}
const draw = safely("Forum", () => {
  const {topics, orphans} = load();
  shown = topics;
  const t = open && topics.find((x) => x.id === open);
  if (open && !t) { open = null; status = "That topic was removed."; }
  let c;
  if (t) {
    const card = (r, text) => ({t: "row", s: "card", c: [avatar(whoOf(r), r.author), {t: "col", s: "grow", c: [
      {t: "text", v: whoOf(r) + " · " + whenOf(r), s: "small muted"},
      {t: "text", v: text || "(no text)", s: "wrap"}]}]});
    c = [
      {t: "row", s: "head", c: [{t: "button", id: "back", v: "← All topics"}, {t: "text", v: t.title, s: "bold grow"}, {t: "text", v: plural(t.replies.length, "reply", "replies"), s: "chip"}]},
      card(t.r, t.text),
      ...(t.replies.length > 150 ? [{t: "text", v: `${t.replies.length - 150} earlier replies not shown.`, s: "small muted"}] : []),
      ...t.replies.slice(-150).map((r) => card(r, textOf(r))),
      {t: "textarea", id: "reply", v: "", ph: "Write a reply"},
      {t: "row", c: [{t: "button", id: "send", v: "Reply", s: "send"}]},
    ];
  } else {
    c = [
      {t: "row", s: "head", c: [{t: "text", v: "Forum", s: "bold grow"}, {t: "text", v: plural(topics.length, "topic"), s: "chip"}]},
      ...(topics.length ? topics.slice(0, 150).map((x, i) => ({t: "row", s: "card", c: [avatar(whoOf(x.r), x.r.author), {t: "col", s: "grow", c: [
        {t: "button", id: "t" + i, v: x.title},
        {t: "text", v: `${whoOf(x.r)} · ${plural(x.replies.length, "reply", "replies")} · last ${whenOf(x.last)}`, s: "small muted"}]}]}))
        : [{t: "col", s: "card", c: [{t: "text", v: "No topics yet. Start the first one below.", s: "muted"}]}]),
      ...(orphans ? [{t: "text", v: `${plural(orphans, "reply", "replies")} to removed topics not shown.`, s: "small muted"}] : []),
      {t: "divider"},
      {t: "text", v: "Start a topic", s: "bold"},
      {t: "input", id: "title", v: "", ph: "Title (one line)"},
      {t: "textarea", id: "text", v: "", ph: "First post"},
      {t: "row", c: [{t: "button", id: "new", v: "Start topic", s: "send"}]},
    ];
  }
  panel.render({t: "col", s: "main", c: [...c, {t: "text", v: status || "Times in your time zone. You approve every post in Satchel's sheet.", s: "small muted"}]});
});
onView(draw);
onEvent((e) => {
  const f = e.fields || {};
  if (e.kind === "click") {
    if (/^t\d+$/.test(e.id)) { const x = shown[+e.id.slice(1)]; if (x) { open = x.id; status = ""; } }
    else if (e.id === "back") { open = null; status = ""; }
    else if (e.id === "retry") status = "";
    else if (e.id === "new") {
      const title = String(f.title || "").trim(), text = String(f.text || "").trim();
      if (!title || /\n/.test(title)) status = "Give the topic a one-line title.";
      else if (title.length + text.length > 1990) status = "Up to about 2000 characters.";
      else { records.draft("app.data", {kind: "topic", text: title + "\n" + text}); status = "Check and approve the topic in Satchel's sheet…"; }
    } else if (e.id === "send" && open) {
      const text = String(f.reply || "").trim();
      if (!text || text.length > 2000) status = "A reply is 1 to 2000 characters.";
      else { records.draft("app.data", {kind: "reply", text}, [open]); status = "Check and approve the reply in Satchel's sheet…"; }
    }
  } else if (e.kind === "draft") status = e.ok ? "Posted." : "Not posted (" + e.error + ").";
  draw();
});
draw();
