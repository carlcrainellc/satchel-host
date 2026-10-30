# Writing an app for an app collection

An app is one JavaScript file. The collection's owner publishes it as an `app.bundle`
record, and Satchel runs it in a QuickJS sandbox inside a worker, inside the collection's
tab. The app can read its own collection, draw a small panel and propose records. It
can't do anything else.

## Publishing

With satchel-host this is two commands (`docs/WRITE-YOUR-FIRST-APP.md` shows them in full):
`satchel-host root app-bundle` on the machine with the root key, then
`satchel-host app publish` on the host within 10 minutes. Underneath, that does this:

1. Put the file on the collection's host as a blob (`/v1/blob/<sha256>`). Up to 256 KiB, UTF-8.
2. Post an `app.bundle@1` record to the collection, signed with the collection's root key:

   | field | value |
   |---|---|
   | `name` | `a-z`, `0-9`, `-`, up to 32 characters, starting with a letter |
   | `title` | shown to the user, up to 60 characters |
   | `ver` | `1`, `1.2` or `1.2.3` |
   | `sha256`, `size` | of the file |
   | `entry` | `main` |
   | `caps` | `read` or `read,draft` |
   | `desc` | optional, up to 300 characters |

Bundles signed by any other key are ignored. If there are several bundles with the same
name, the newest one counts. Satchel fetches the file, checks size, hash and UTF-8,
and refuses it if anything differs.

The first time someone runs an app, Satchel asks them. It asks again whenever the
hash changes.

## What the app gets

These are globals. They can't be replaced.

| call | what it does | limits |
|---|---|---|
| `coll.read({since, types, limit, tail})` | the collection's `app.data` records as plain objects `{id, seq, t, author, ts, body, refs}`, oldest first. With `tail: true` you get the newest `limit` records instead of the oldest | own collection only; `limit` up to 500, default 100 |
| `panel.render(tree)` | replaces the panel with a UI tree (below) | up to 2,000 nodes, 32 deep, 4,000 characters per string |
| `records.draft(t, body, refs)` | proposes a record; only with `caps: read,draft` | `t` must be `app.data`; 1 waiting at a time; 10 a minute |
| `onView(fn)` | `fn({count})` runs when the records change, and once at start | |
| `onEvent(fn)` | `fn(ev)` runs on clicks, changes and draft results | |
| `later(ms, fn)` | runs `fn` once after `ms` | 100 ms minimum, 10 waiting |
| `log(msg)` | a line in the log under the panel | 1 KiB, 60 a minute |

`Date` works in whole seconds only. There is no network, no DOM, no `import`, no
WebAssembly, no access to keys, wallets, files or other collections.

### app.data records

`{kind, text}`: `kind` is `a-z`, `0-9`, `_`, up to 24 characters, starting with a letter;
`text` is up to 2,000 characters. The app decides what kinds mean.

Every draft opens a sheet in the tab showing exactly what will be signed. Nothing is sent
until the user confirms. The result comes back as an event:

    {kind: "draft", ok: true, id: "<record id>", error: null}
    {kind: "draft", ok: false, id: null, error: "cancelled"}

### UI tree

Nodes are plain objects with a type `t`:

| `t` | keys |
|---|---|
| `text`, `heading`, `para` | `v` |
| `divider` | |
| `list`, `row`, `col` | `c` (children) |
| `item` | `v`, `c` |
| `button` | `id`, `v` |
| `input`, `textarea` | `id`, `v`, `ph` |
| `select` | `id`, `v`, `opts` (up to 100 strings) |

Any node can also have `s`: up to 3 style names separated by spaces, from a fixed list:
`bubble`, `mine`, `avatar`, `sep`, `muted`, `small`, `bold`, `mono`, `right`, `center`,
`card`, `grow`, `accent`, `tight`, `wrap`, `side`, `main`, `head`, `chip`, `active`, `send`,
`pill`, `dot` and `hue1` to `hue6`. Apps can't send CSS, colours or URLs.

All values are text and are shown as text. Unknown types, keys or style names make
`panel.render` throw. `id` is `a-z`, `0-9`, `_`, `-`, up to 32 characters.

What the user types into inputs is kept across renders, as long as the app sends the same
`v` for that input. Enter in an input clicks the next button.

### Events

    {kind: "click", id: "add", fields: {text: "what was typed"}}
    {kind: "change", id: "pick", value: "b", fields: {...}}

`fields` holds the current value of every input, textarea and select in the panel.

## Apps for other collections (pointers)

An app can also draw a chat room, wiki, directory or profile collection. The app's code still
lives in an app collection; the other collection only points at it.

- The owner adds `apps` to the collection's signed settings (policy):
  `"apps": [{"coll": "demoapps", "name": "chat", "sha256": "<64 hex>"}]`. At most 8 entries.
  `satchel-host app use` writes these entries for you.
- `coll` is an app collection on the same host. It must hold an `app.bundle@1` named `name`,
  signed by the same root key as the pointing collection, whose hash is `sha256`.
- A pointer counts only when the policy itself is signed (its hash is in the verified
  checkpoint). If the bundle is missing, has another hash or another signer, the app isn't
  offered, and Satchel says why.
- To open a collection straight in one app, add `&app=<name>` to its link:
  `srnet://<host>/room/chat#root=<hex>&app=chat`. A config entry can do the same with
  `"app": "chat"`. The Run sheet names the pointer and the hash before anything runs.
- Apps opened on these collections can only propose the record types that collection
  already takes: `room.msg` and `room.join`, `board.post`, `wiki.page` and `wiki.rev`,
  `directory.entry`, and `id.profile` (name and bio only). Key changes, file shares, mail and
  moderation stay in Satchel's own screens.
- Mail and file drops are built in and are never drawn by an app.

The same records can therefore have several views: Satchel's own screen and any number of
apps. Posts from any of them land in the same collection.

## Collections with no view

If a collection has no built-in view Satchel can use and no app it can run, Satchel
shows its records as a read-only list (type, author, time, text). Nothing is run and there is
no post box.

## Budgets

- 16 MiB memory and 256 KiB stack.
- 500 ms CPU for the first run of the file, 50 ms for each event after that. Promise jobs
  count too.
- If a call runs over its budget it's cut off. Two cut-offs within a minute stop the app.
- Running out of memory stops the app.
- If the worker doesn't answer within 3 s at start or 1 s per call, the tab ends it.

## Example

    let shown = 0;
    function draw(note) {
      const notes = coll.read({types: ["app.data"], limit: 50});
      panel.render({t: "col", c: [
        {t: "heading", v: "Notes"},
        {t: "list", c: notes.map((r) => ({t: "item", v: r.body.text}))},
        {t: "row", c: [{t: "input", id: "text", ph: "new note"}, {t: "button", id: "add", v: "Add note"}]},
        {t: "para", v: note || ""},
      ]});
    }
    onView(() => draw());
    onEvent((e) => {
      if (e.id === "add" && e.fields.text) records.draft("app.data", {kind: "note", text: e.fields.text});
      if (e.kind === "draft") draw(e.ok ? "posted" : "not posted: " + e.error);
    });

`apps/starter/hello.js` is a small app with styles and a status line,
`apps/examples/notes.js` is a fuller version of this one, and `apps/examples/hostile.js` is
the test app that tries to break out.
