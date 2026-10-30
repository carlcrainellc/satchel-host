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

// Demo app "Demo apps" 1.0: the home of the apps place. It shows what the place's records hold,
// polls and shared notes, side by side with the same rules as the Polls and Notes apps:
//   {kind: "poll", text: "Question\nOption 1\nOption 2..."}       a poll (2 to 8 options)
//   {kind: "vote", text: "<option number>"} with refs [poll id]    a vote; each key's newest counts
//   {kind: "note", text: "..."}                                     a shared note
// Times are in your own time zone. Removed records never reach the app.
let status = "", tab = "polls", shown = [];
function load() {
  const recs = read(["app.data"]);
  const polls = [], byId = {}, notes = [];
  for (const r of recs) {
    const k = bodyOf(r).kind;
    if (k === "note" && textOf(r).trim()) notes.push(r);
    if (k !== "poll") continue;
    const lines = textOf(r).split("\n").map((x) => x.trim()).filter(Boolean);
    if (lines.length < 3) continue;
    const p = {r, id: r.id, q: lines[0], opts: lines.slice(1, 9), votes: {}};
    polls.push(p); byId[p.id] = p;
  }
  for (const r of recs) {
    const p = bodyOf(r).kind === "vote" && byId[(r.refs || [])[0]];
    if (!p) continue;
    const n = parseInt(textOf(r), 10);
    if (!(n >= 1 && n <= p.opts.length)) continue;
    const old = p.votes[r.author];
    if (!old || Number(r.seq) > old.seq) p.votes[r.author] = {n, seq: Number(r.seq), mine: !!(r.mine || r.you)};
  }
  return {polls: polls.sort((a, b) => Number(b.r.ts) - Number(a.r.ts)).slice(0, 20), notes: notes.sort((a, b) => Number(b.ts) - Number(a.ts)).slice(0, 50)};
}
function pollCard(p, i) {
  const counts = p.opts.map(() => 0);
  let mine = 0;
  for (const k in p.votes) { counts[p.votes[k].n - 1]++; if (p.votes[k].mine) mine = p.votes[k].n; }
  const total = counts.reduce((a, b) => a + b, 0);
  return {t: "col", s: "card", c: [
    {t: "row", c: [avatar(whoOf(p.r), p.r.author), {t: "col", s: "grow", c: [{t: "text", v: p.q, s: "bold wrap"},
      {t: "text", v: `${whoOf(p.r)} · ${whenOf(p.r)} · ${plural(total, "vote")}`, s: "small muted"}]}]},
    ...p.opts.map((o, j) => {
      const pct = total ? Math.round((counts[j] * 100) / total) : 0;
      return {t: "row", c: [{t: "text", v: `${o}`, s: mine === j + 1 ? "bold grow" : "grow"},
        {t: "text", v: `${counts[j]} · ${pct}%`, s: "chip"},
        {t: "button", id: `v-${i}-${j + 1}`, v: mine === j + 1 ? "Your vote" : "Vote", s: mine === j + 1 ? "active" : "send"}]};
    }),
  ]};
}
const draw = safely("Demo apps", () => {
  const {polls, notes} = load();
  shown = polls;
  const tabs = {t: "row", c: [
    {t: "button", id: "tab-polls", v: "Polls · " + polls.length, s: tab === "polls" ? "chip active" : "chip"},
    {t: "button", id: "tab-notes", v: "Notes · " + notes.length, s: tab === "notes" ? "chip active" : "chip"}]};
  const body = tab === "polls" ? [
    ...(polls.length ? polls.map(pollCard) : [{t: "col", s: "card", c: [{t: "text", v: "No polls yet. Start one below.", s: "muted"}]}]),
    {t: "divider"},
    {t: "text", v: "Start a poll", s: "bold"},
    {t: "input", id: "q", v: "", ph: "Question"},
    {t: "textarea", id: "opts", v: "", ph: "Options, one per line (2 to 8)"},
    {t: "row", c: [{t: "button", id: "start", v: "Start poll", s: "send"}]},
  ] : [
    ...(notes.length ? notes.map((r) => ({t: "row", s: "card", c: [avatar(whoOf(r), r.author), {t: "col", s: "grow", c: [
      {t: "text", v: textOf(r), s: "wrap"}, {t: "text", v: whoOf(r) + " · " + whenOf(r), s: "small muted"}]}]}))
      : [{t: "col", s: "card", c: [{t: "text", v: "No notes yet. Add one below.", s: "muted"}]}]),
    {t: "row", s: "pill", c: [{t: "input", id: "text", v: "", ph: "Write a note"}, {t: "button", id: "add", v: "Add note", s: "send"}]},
  ];
  panel.render({t: "col", s: "main", c: [
    {t: "row", s: "head", c: [{t: "text", v: "Demo apps", s: "bold grow"}, {t: "text", v: "polls and notes", s: "small muted"}]},
    tabs, ...body,
    {t: "text", v: status || "Times in your time zone. Your newest vote in a poll is the one that counts; you approve every post.", s: "small muted"},
  ]});
});
function propose(body, refs, note) {
  try { records.draft("app.data", body, refs); status = note; } catch (err) { status = "Not sent: " + ((err && err.message) || err); }
}
onView(draw);
onEvent((e) => {
  const f = e.fields || {};
  if (e.kind === "click") {
    if (e.id === "tab-polls" || e.id === "tab-notes") { tab = e.id.slice(4); status = ""; }
    else if (e.id === "retry") status = "";
    else if (e.id === "start") {
      const q = String(f.q || "").replace(/\s+/g, " ").trim();
      const opts = String(f.opts || "").split("\n").map((x) => x.replace(/\s+/g, " ").trim()).filter(Boolean);
      if (!q) status = "Write a question first.";
      else if (opts.length < 2 || opts.length > 8) status = "A poll needs 2 to 8 options, one per line.";
      else propose({kind: "poll", text: [q, ...opts].join("\n").slice(0, 2000)}, [], "Check and approve the poll in Satchel's sheet…");
    } else if (/^v-\d+-\d+$/.test(e.id)) {
      const [, i, n] = e.id.split("-"), p = shown[+i];
      if (!p || !(+n >= 1 && +n <= p.opts.length)) status = "That poll changed. Try again.";
      else propose({kind: "vote", text: String(+n)}, [p.id], `Voting "${p.opts[+n - 1]}". Check and approve it in Satchel's sheet…`);
    } else if (e.id === "add") {
      const text = String(f.text || "").trim();
      if (!text) status = "Write something first.";
      else if (text.length > 2000) status = "Notes can be up to 2000 characters.";
      else propose({kind: "note", text}, [], "Check and approve the note in Satchel's sheet…");
    }
  } else if (e.kind === "draft") status = e.ok ? "Posted." : "Not posted (" + e.error + ").";
  draw();
});
draw();
