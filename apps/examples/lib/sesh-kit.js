// sesh-kit: a small helper for host apps that use the connect API (docs/API.md).
// Paste it at the top of your app; it only uses the globals the sandbox gives every app.
//
//   const kit = seshKit({onChange: draw});
//   kit.connect({post: true});      // opens Satchel's connect sheet
//   kit.me                          // {name, key} once connected, else null
//   kit.post("room.msg", {text})    // proposes a post; Satchel shows the sheet
//   kit.handle(ev)                  // call from onEvent; returns true if it was kit's event
//
function seshKit(opts = {}) {
  const onChange = typeof opts.onChange === "function" ? opts.onChange : () => {};
  const kit = {
    me: null, grants: {}, status: "", busy: false,
    connect(want = {post: true}) {
      if (kit.busy) return;
      kit.busy = true; kit.status = "Answer Satchel's sheet to connect…"; onChange();
      sesh.connect(want);
    },
    post(t, body, refs) {
      kit.status = "Check the post in Satchel's sheet…"; onChange();
      sesh.post(t, body, refs);
    },
    handle(ev) {
      if (!ev || (ev.kind !== "connect" && ev.kind !== "draft")) return false;
      if (ev.kind === "connect") {
        kit.busy = false;
        if (ev.ok && ev.me) { kit.me = ev.me; kit.grants = ev.grants || {}; kit.status = ""; }
        else kit.status = "Not connected (" + (ev.error || "no answer") + ").";
      } else kit.status = ev.ok ? "" : "Not sent (" + (ev.error || "no answer") + ").";
      onChange();
      return true;
    },
  };
  return kit;
}
