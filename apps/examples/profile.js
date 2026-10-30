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

// Demo app "People" 1.1: profiles in an id collection. Shows each key's newest name and bio,
// newest first, with when it was last changed (your own time zone), and lets you propose your
// own (name up to 32 characters, bio up to 280). Removed profiles never reach the app.
let status = "";
function profiles() {
  const by = {};
  for (const r of read(["id.profile"])) by[r.author] = {r, key: String(r.author || ""), mine: !!(r.mine || r.you), name: textOf(r, "name") || "anon", bio: textOf(r, "bio")};
  return Object.values(by).sort((a, b) => Number(b.r.ts) - Number(a.r.ts));
}
const draw = safely("People", () => {
  const ps = profiles(), me = ps.find((p) => p.mine);
  panel.render({t: "col", s: "main", c: [
    {t: "row", s: "head", c: [{t: "text", v: "People", s: "bold grow"}, {t: "text", v: plural(ps.length, "profile"), s: "chip"}]},
    ...(ps.length ? ps.map((p) => ({t: "row", s: "card", c: [
      avatar(p.name, p.key),
      {t: "col", s: "grow", c: [{t: "text", v: p.name + (p.mine ? " (you)" : ""), s: "bold"},
        {t: "text", v: p.bio || "No bio yet.", s: p.bio ? "small wrap" : "small muted"},
        {t: "text", v: p.key.slice(0, 12) + "… · updated " + whenOf(p.r), s: "small muted mono"}]},
    ]})) : [{t: "col", s: "card", c: [{t: "text", v: "Nobody has a profile here yet.", s: "muted"}]}]),
    {t: "divider"},
    {t: "text", v: me ? "Change your profile" : "Add your profile", s: "bold"},
    {t: "input", id: "name", v: me ? me.name : "", ph: "Name"},
    {t: "textarea", id: "bio", v: me ? me.bio : "", ph: "A line or two about you"},
    {t: "row", c: [{t: "button", id: "save", v: "Save", s: "send"}]},
    {t: "text", v: status || "Your profile is public here and signed with your key.", s: "small muted"},
  ]});
});
onView(draw);
onEvent((e) => {
  const f = e.fields || {};
  if (e.kind === "click" && e.id === "save") {
    const name = String(f.name || "").trim(), bio = String(f.bio || "").trim();
    if (!name || name.length > 32 || /\n/.test(name)) status = "Name: 1 to 32 characters, one line.";
    else if (bio.length > 280) status = "Bio: up to 280 characters.";
    else { records.draft("id.profile", bio ? {name, bio} : {name}); status = "Check and approve it in Satchel's sheet…"; }
  } else if (e.kind === "click" && e.id === "retry") status = "";
  else if (e.kind === "draft") status = e.ok ? "Saved." : "Not saved (" + e.error + ").";
  draw();
});
draw();
