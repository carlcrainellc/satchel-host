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

// Demo app "Files" 1.0 for a file drop: what's in the drop, newest first, with size, how it
// is shared, who added it and when (your own time zone). Encrypted files keep their name inside
// the encryption and only Satchel holds the keys, so downloading and adding stay in Satchel's
// built-in files view: this app only lists.
const MODES = {plain: "public, not encrypted", link: "encrypted, anyone with its link", gated: "encrypted, people given access"};
const size = (n) => { n = Number(n) || 0; return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB"; };
const icon = (mime, name) => {
  const m = String(mime || ""), x = String(name || "").toLowerCase();
  if (m.startsWith("image/")) return "🖼"; if (m.startsWith("text/") || /\.(txt|md|csv)$/.test(x)) return "📄";
  if (m.includes("pdf") || x.endsWith(".pdf")) return "📕"; if (/zip|tar|gzip/.test(m)) return "🗜"; return "📦";
};
let filter = "all";
const draw = safely("Files", () => {
  const recs = read(["drop.item", "drop.grant", "drop.revoke", "drop.request"]);
  const items = recs.filter((r) => r.t === "drop.item" && textOf(r, "sha256"));
  const grants = {};
  for (const r of recs) if (r.t === "drop.grant") { const s = textOf(r, "sha256"); grants[s] = (grants[s] || 0) + 1; }
  const asks = recs.filter((r) => r.t === "drop.request").length;
  const shown = items.slice().reverse().filter((r) => filter === "all" || (filter === "mine" && (r.mine || r.you)));
  const total = items.reduce((a, r) => a + (Number(bodyOf(r).size) || 0), 0);
  const cards = shown.slice(0, 100).map((r) => {
    const b = bodyOf(r), mode = MODES[textOf(r, "mode")] || "";
    // An encrypted file's name and type are inside the encryption; only plain files show them here.
    const name = textOf(r, "name") || (textOf(r, "mode") === "plain" ? "(no name)" : "Encrypted file " + textOf(r, "sha256").slice(0, 8));
    return {t: "row", s: "card", c: [
      {t: "text", v: icon(b.mime, name), s: "avatar " + hue(textOf(r, "sha256"))},
      {t: "col", s: "grow", c: [
        {t: "text", v: name, s: "bold wrap"},
        ...(textOf(r, "note") ? [{t: "text", v: textOf(r, "note"), s: "small wrap"}] : []),
        {t: "text", v: [size(b.size), textOf(r, "mime"), mode, grants[textOf(r, "sha256")] ? "shared with " + plural(grants[textOf(r, "sha256")], "more key", "more keys") : ""].filter(Boolean).join(" · "), s: "small muted"},
        {t: "text", v: whoOf(r) + " · " + whenOf(r), s: "small muted"},
      ]},
    ]};
  });
  panel.render({t: "col", s: "main", c: [
    {t: "row", s: "head", c: [{t: "text", v: "Files", s: "bold"}, {t: "text", v: plural(items.length, "file") + " · " + size(total), s: "chip"}]},
    {t: "row", c: [
      {t: "button", id: "f-all", v: "All files", s: filter === "all" ? "chip active" : "chip"},
      {t: "button", id: "f-mine", v: "Added by you", s: filter === "mine" ? "chip active" : "chip"},
    ]},
    ...(cards.length ? cards : [{t: "col", s: "card", c: [{t: "text", v: filter === "mine" ? "You haven't added a file here." : "No files here yet.", s: "muted"}]}]),
    ...(shown.length > 100 ? [{t: "text", v: `Showing the newest 100 of ${shown.length}.`, s: "small muted"}] : []),
    {t: "divider"},
    {t: "text", v: "Encrypted files are locked before they leave the sender's browser and only Satchel holds the keys, so this app can't open them. To download or add a file, switch to the built-in files view at the top of this tab." + (asks ? ` ${plural(asks, "access request")} so far; the owner answers them there.` : ""), s: "small muted wrap"},
  ]});
});
onView(draw);
onEvent((e) => {
  if (e.kind === "click" && e.id === "f-all") filter = "all";
  else if (e.kind === "click" && e.id === "f-mine") filter = "mine";
  draw();
});
draw();
