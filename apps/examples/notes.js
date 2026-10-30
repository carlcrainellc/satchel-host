// Demo app for the local test host: shared notes kept as app.data records.
let status = "";
function draw() {
  const notes = coll.read({types: ["app.data"], limit: 200}).filter((r) => r.body.kind === "note");
  panel.render({t: "col", c: [
    {t: "heading", v: "Notes"},
    {t: "para", v: notes.length ? `${notes.length} note${notes.length === 1 ? "" : "s"} in this collection` : "No notes yet."},
    {t: "list", c: notes.map((n) => ({t: "item", v: n.body.text}))},
    {t: "divider"},
    {t: "row", c: [{t: "input", id: "text", v: "", ph: "write a note"}, {t: "button", id: "add", v: "Add note"}]},
    {t: "text", v: status},
  ]});
}
onView(draw);
onEvent((e) => {
  if (e.kind === "click" && e.id === "add") {
    const text = (e.fields.text || "").trim();
    if (!text) { status = "Write something first."; draw(); return; }
    records.draft("app.data", {kind: "note", text});
    status = "Waiting for you to approve the post…";
    draw();
  } else if (e.kind === "draft") {
    status = e.ok ? "Posted." : "Not posted (" + e.error + ").";
    log("draft " + (e.ok ? "ok" : e.error));
    draw();
  }
});
draw();
