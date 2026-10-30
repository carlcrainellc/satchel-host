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

// Demo app "Wiki" 1.1 for a wiki collection. Reads wiki.page, wiki.rev and wiki.revert, follows
// the same head rule as the built-in view (a revision counts only if its parent is the current
// head) and proposes wiki.page / wiki.rev with the head as parent. Text up to 4000 characters.
// "Last edited" is in your own time zone. Removed revisions never reach the app, so a page
// whose history has a gap stops at the last revision before it, as the built-in view does.
let status = "", open = null, editing = false, pendingRev = null, shown = [];

function pages() {
  const ps = {}, texts = {};
  const get = (slug) => (ps[slug] = ps[slug] || {slug, title: slug, head: null, text: "", revs: 0, last: null, made: null});
  for (const r of read(["wiki.page", "wiki.rev", "wiki.revert"], 500, false)) {
    const slug = textOf(r, "slug");
    if (!slug && r.t !== "wiki.revert") continue;
    if (r.t === "wiki.page") { const p = get(slug); if (!p.revs) p.title = textOf(r, "title") || slug; if (!p.made) p.made = r; continue; }
    const parent = (r.refs || [])[0] || null;
    let p, text;
    if (r.t === "wiki.rev") { p = get(slug); if (parent !== p.head) continue; text = textOf(r); }
    else { const tgt = texts[parent]; if (!tgt) continue; p = get(tgt.slug); if (slug && slug !== p.slug) continue; text = tgt.text; }
    texts[r.id] = {slug: p.slug, text};
    p.head = r.id; p.text = text; p.revs++; p.last = r;
  }
  return Object.values(ps).sort((a, b) => (a.title.toLowerCase() < b.title.toLowerCase() ? -1 : 1));
}
const edited = (p) => (p.last ? `${plural(p.revs, "revision")} · last edited ${whenOf(p.last)} by ${whoOf(p.last)}` : "No text yet");

const draw = safely("Wiki", () => {
  const all = pages();
  shown = all;
  const p = open && all.find((x) => x.slug === open);
  let body;
  if (p && editing) {
    body = [
      {t: "row", s: "head", c: [{t: "text", v: "Editing: " + p.title, s: "bold grow"}]},
      {t: "textarea", id: "text", v: p.text.slice(0, 4000), ph: "Page text"},
      {t: "input", id: "summary", v: "", ph: "What changed (optional)"},
      {t: "row", c: [{t: "button", id: "save", v: "Save", s: "send"}, {t: "button", id: "cancel", v: "Cancel"}]},
    ];
  } else if (p) {
    body = [
      {t: "row", s: "head", c: [{t: "button", id: "back", v: "← All pages"}, {t: "text", v: p.title, s: "bold grow"}, {t: "button", id: "edit", v: "Edit", s: "send"}]},
      {t: "col", s: "card", c: [{t: "para", v: p.text || "(This page is empty. Press Edit to write it.)", s: p.text ? "wrap" : "muted"}]},
      {t: "text", v: edited(p), s: "small muted"},
    ];
  } else {
    body = [
      {t: "row", s: "head", c: [{t: "text", v: "Wiki", s: "bold grow"}, {t: "text", v: plural(all.length, "page"), s: "chip"}]},
      ...(all.length ? all.map((x, i) => ({t: "row", s: "card", c: [{t: "text", v: initial(x.title), s: "avatar " + hue(x.head || x.slug)},
        {t: "col", s: "grow", c: [{t: "button", id: "p" + i, v: x.title}, {t: "text", v: edited(x), s: "small muted"}]}]}))
        : [{t: "col", s: "card", c: [{t: "text", v: "No pages yet. Make the first one below.", s: "muted"}]}]),
      {t: "divider"},
      {t: "text", v: "New page", s: "bold"},
      {t: "row", c: [{t: "input", id: "slug", v: "", ph: "page-name"}, {t: "input", id: "title", v: "", ph: "Title"},
        {t: "button", id: "new", v: "Make page", s: "send"}]},
    ];
  }
  panel.render({t: "col", s: "main", c: [...body, {t: "text", v: status || "Times in your time zone. Every edit is signed and kept; you approve each one.", s: "small muted"}]});
});

onView(draw);
onEvent((e) => {
  const all = pages(), f = e.fields || {};
  if (e.kind === "click") {
    if (/^p\d+$/.test(e.id)) { const x = shown[+e.id.slice(1)]; if (x) { open = x.slug; editing = false; status = ""; } }
    else if (e.id === "back") { open = null; editing = false; status = ""; }
    else if (e.id === "edit") editing = true;
    else if (e.id === "cancel") editing = false;
    else if (e.id === "retry") status = "";
    else if (e.id === "save") {
      const p = all.find((x) => x.slug === open);
      if (!p) status = "That page is gone.";
      else {
        const text = String(f.text || "");
        if (text.length > 4000) status = "Pages can be up to 4000 characters.";
        else {
          const body = {slug: p.slug, text};
          const sum = String(f.summary || "").trim();
          if (sum) body.summary = sum.slice(0, 200);
          records.draft("wiki.rev", body, p.head ? [p.head] : []);
          status = "Check and approve the edit in Satchel's sheet…";
        }
      }
    } else if (e.id === "new") {
      const slug = String(f.slug || "").trim().toLowerCase(), title = String(f.title || "").trim() || slug;
      if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(slug)) status = "Page name: lower-case letters, digits and dashes.";
      else if (all.some((x) => x.slug === slug)) status = "That page exists.";
      else { records.draft("wiki.page", {slug, title}); pendingRev = slug; status = "Check and approve the new page in Satchel's sheet…"; }
    }
  } else if (e.kind === "draft") {
    if (e.ok && pendingRev) { open = pendingRev; editing = true; pendingRev = null; status = "Page made. Write its first text."; }
    else { pendingRev = null; status = e.ok ? "Saved." : "Not saved (" + e.error + ")."; if (e.ok) editing = false; }
  }
  draw();
});
draw();
