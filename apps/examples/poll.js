// Demo app: quick polls kept as app.data records in this collection.
//   {kind: "poll", text: "Question\nOption 1\nOption 2..."}       starts a poll
//   {kind: "vote", text: "<option number>"} with refs [poll id]    a vote; each key's newest vote counts
let status = "";
let shown = [];   // polls as last drawn, so button ids map to the same poll after new records arrive

function parsePoll(r) {
  const lines = String(r.body.text || "").split("\n").map((s) => s.trim()).filter(Boolean);
  if (lines.length < 3) return null;
  return {id: r.id, ts: r.ts, author: r.author, q: lines[0], opts: lines.slice(1, 9)};
}

function load() {
  const recs = coll.read({types: ["app.data"], limit: 500});
  const polls = [], byId = {};
  for (const r of recs) {
    if (r.body.kind !== "poll") continue;
    const p = parsePoll(r);
    if (p) { polls.push(p); byId[p.id] = p; p.votes = {}; }
  }
  for (const r of recs) {
    if (r.body.kind !== "vote" || !r.refs || !byId[r.refs[0]]) continue;
    const p = byId[r.refs[0]], n = parseInt(r.body.text, 10);
    if (!(n >= 1 && n <= p.opts.length)) continue;
    const old = p.votes[r.author];
    if (!old || r.seq > old.seq) p.votes[r.author] = {n, seq: r.seq};
  }
  polls.sort((a, b) => b.ts - a.ts);
  return polls.slice(0, 20);
}

function draw() {
  shown = load();
  const items = shown.map((p, i) => {
    const counts = p.opts.map(() => 0);
    for (const k in p.votes) counts[p.votes[k].n - 1]++;
    const total = counts.reduce((a, b) => a + b, 0);
    return {t: "item", v: p.q, c: [
      {t: "text", v: `${total} vote${total === 1 ? "" : "s"}, from ${p.author.slice(0, 8)}`},
      {t: "list", c: p.opts.map((o, j) => ({t: "row", c: [
        {t: "text", v: `${o}: ${counts[j]}`},
        {t: "button", id: `v-${i}-${j + 1}`, v: "Vote"},
      ]}))},
    ]};
  });
  panel.render({t: "col", c: [
    {t: "heading", v: "Polls"},
    {t: "para", v: shown.length ? "Newest first. Your latest vote in a poll is the one that counts." : "No polls yet. Start one below."},
    {t: "list", c: items},
    {t: "divider"},
    {t: "heading", v: "Start a poll"},
    {t: "input", id: "q", v: "", ph: "question"},
    {t: "textarea", id: "opts", v: "", ph: "options, one per line (2 to 8)"},
    {t: "button", id: "start", v: "Start poll"},
    {t: "text", v: status},
  ]});
}

function say(s) { status = s; draw(); }
function propose(body, refs, note) {
  try { records.draft("app.data", body, refs); say(note); } catch (err) { say("Not sent: " + (err && err.message || err)); }
}

onView(draw);
onEvent((e) => {
  if (e.kind === "click" && e.id === "start") {
    const q = (e.fields.q || "").replace(/\s+/g, " ").trim();
    const opts = (e.fields.opts || "").split("\n").map((s) => s.replace(/\s+/g, " ").trim()).filter(Boolean);
    if (!q) return say("Write a question first.");
    if (opts.length < 2 || opts.length > 8) return say("A poll needs 2 to 8 options, one per line.");
    propose({kind: "poll", text: [q, ...opts].join("\n").slice(0, 2000)}, [], "Check the post and confirm it.");
  } else if (e.kind === "click" && e.id.startsWith("v-")) {
    const [, i, n] = e.id.split("-");
    const p = shown[+i];
    if (!p || !(+n >= 1 && +n <= p.opts.length)) return say("That poll changed. Try again.");
    propose({kind: "vote", text: String(+n)}, [p.id], `Voting "${p.opts[+n - 1]}". Check the post and confirm it.`);
  } else if (e.kind === "draft") {
    log("draft " + (e.ok ? "ok" : e.error));
    say(e.ok ? "Posted." : "Not posted (" + e.error + ").");
  }
});
draw();
