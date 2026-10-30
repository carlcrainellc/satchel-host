// Guestbook: the starter app. It lists the notes people left in this app collection and
// lets you leave one. Publish it with caps "read,draft" so it can propose records.
//
// Everything here is described in docs/APP-API.md. The short version:
//   coll.read(...)        reads this collection's app.data records
//   panel.render(tree)    draws the panel
//   records.draft(t, b)   proposes a record; the person sees it and signs it, or doesn't
//   onView / onEvent      tell you when records change or someone clicks

let status = "";

function draw() {
  const notes = coll.read({types: ["app.data"], limit: 50, tail: true})
    .filter((r) => r.body.kind === "hello");
  panel.render({t: "col", c: [
    {t: "heading", v: "Guestbook"},
    notes.length
      ? {t: "list", c: notes.map((r) => ({t: "item", v: r.author.slice(0, 8) + ": " + r.body.text}))}
      : {t: "para", v: "Nobody has signed yet.", s: "muted"},
    {t: "row", c: [
      {t: "input", id: "text", ph: "Say hello"},
      {t: "button", id: "sign", v: "Sign"},
    ]},
    {t: "text", v: status, s: "small muted"},
  ]});
}

onView(draw);

onEvent((e) => {
  if (e.kind === "click" && e.id === "sign") {
    const text = String(e.fields.text || "").trim();
    if (!text) status = "Write something first.";
    else if (text.length > 200) status = "Keep it under 200 characters.";
    else { records.draft("app.data", {kind: "hello", text}); status = "Waiting for you to confirm…"; }
  } else if (e.kind === "draft") {
    status = e.ok ? "Thanks for signing." : "Not signed (" + e.error + ").";
  } else {
    return;
  }
  draw();
});
