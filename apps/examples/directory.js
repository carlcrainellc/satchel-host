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

// Demo app "Directory" 1.1: the directory as cards with a Join button. Reads directory.entry
// records and, like the built-in view, counts an entry only when it is signed by the root key
// in its link; the newest entry for a link wins. Join opens Satchel's own join sheet, where you
// check and confirm. Removed entries never reach the app.
let status = "", shown = [];
const KINDS = {room: ["#", "Chat room"], app: ["A", "Apps"], wiki: ["W", "Wiki"], id: ["@", "People"], directory: ["@", "Directory"],
  drop: ["↓", "Files"], board: ["B", "Board"], shop: ["$", "Shop"]};
const rootOf = (link) => { const m = /#root=([0-9a-f]{64})/.exec(link); return m ? m[1] : null; };
const kindOf = (link) => { const m = /^srnet:\/\/[^/]+\/([a-z]+)\//.exec(link); return m ? m[1] : "?"; };
function entries() {
  const by = {};
  for (const r of read(["directory.entry"])) {
    const link = textOf(r, "link");
    if (!link || rootOf(link) !== r.author) continue;
    by[link] = {r, link, title: textOf(r, "title") || "(untitled)", desc: textOf(r, "desc"), kind: kindOf(link)};
  }
  return Object.values(by).sort((a, b) => (a.title.toLowerCase() < b.title.toLowerCase() ? -1 : 1));
}
const draw = safely("Directory", () => {
  shown = entries();
  panel.render({t: "col", s: "main", c: [
    {t: "row", s: "head", c: [{t: "text", v: "Directory", s: "bold grow"}, {t: "text", v: plural(shown.length, "place"), s: "chip"}]},
    {t: "text", v: "Everything this host lists, signed by its owner. Join shows Satchel's sheet first.", s: "small muted"},
    ...(shown.length ? shown.map((x, i) => {
      const [ic, word] = KINDS[x.kind] || ["?", x.kind];
      return {t: "row", s: "card", c: [{t: "text", v: ic, s: "avatar " + hue(rootOf(x.link).slice(2) + i)},
        {t: "col", s: "grow", c: [
          {t: "row", c: [{t: "text", v: x.title, s: "bold grow"}, {t: "text", v: word, s: "chip"}]},
          ...(x.desc ? [{t: "text", v: x.desc, s: "small wrap"}] : []),
          {t: "text", v: "Listed " + whenOf(x.r), s: "small muted"}]},
        {t: "button", id: "j" + i, v: "Join", s: "send"}]};
    }) : [{t: "col", s: "card", c: [{t: "text", v: "Nothing is listed here yet.", s: "muted"}]}]),
    {t: "text", v: status, s: "small muted"},
  ]});
});
onView(draw);
onEvent((e) => {
  if (e.kind === "click" && /^j\d+$/.test(e.id)) {
    const x = shown[+e.id.slice(1)];
    if (x) { links.join(x.link); status = "Opened Satchel's join sheet for " + x.title + "."; }
  } else if (e.kind === "click" && e.id === "retry") status = "";
  draw();
});
draw();
