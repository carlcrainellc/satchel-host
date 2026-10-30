# Write your first app

This walks through the starter app, `apps/starter/hello.js`: a guestbook where people leave
a short note. By the end it runs in Satchel from your own host. It takes about 15 minutes.

## What an app is

An app is one JavaScript file. Satchel runs it in a sandbox, so it has no network, no page
and no keys. It gets a few calls instead:

- `coll.read(...)` returns the records in the app's collection;
- `panel.render(tree)` draws the panel from plain objects;
- `records.draft(type, body)` proposes a record. Satchel shows the person exactly what will
  be signed, and nothing is posted unless they say yes;
- `onView(fn)` and `onEvent(fn)` call you when records change or someone clicks.

`docs/APP-API.md` has the full list and the limits.

## 1. Read the starter

Open `apps/starter/hello.js`. It does three things.

It reads the newest 50 records and keeps the ones it wrote:

    const notes = coll.read({types: ["app.data"], limit: 50, tail: true})
      .filter((r) => r.body.kind === "hello");

Apps store their data as `app.data` records with a `kind` and a `text`. The `kind` is yours
to choose, so several apps can share a collection without reading each other's records.

It draws the panel: a heading, the notes, a text box, a button and a status line. Each node
is an object with a type `t`. `s` picks from a fixed set of styles, like `muted` or `small`.

    {t: "row", c: [
      {t: "input", id: "text", ph: "Say hello"},
      {t: "button", id: "sign", v: "Sign"},
    ]},

It answers clicks. A click event carries the value of every input in `e.fields`, so the
note is `e.fields.text`. When the person confirms or cancels, a `draft` event comes back
and the status line says what happened.

## 2. Change something

Give it your own heading, or change `"hello"` to a kind of your own. If you change the kind,
old notes won't show, since the filter only keeps your kind.

## 3. Set up a host to try it on

You need a host with an app collection. Make the root key once, on the machine you'll keep
it on:

    ./bin/satchel-host root init

Then set up a rooms host with an app collection called `apps`. For a test it can live in your
home directory and run in the foreground:

    ./bin/satchel-host init --type rooms --name lobby --apps apps \
        --prefix ~/satchel-test/lobby --sesh new
    ./bin/satchel-host root sign ~/satchel-test/lobby/request.json --days 30
    ./bin/satchel-host install-delegation delegation.json --prefix ~/satchel-test/lobby --no-systemd
    ./bin/satchel-host run --prefix ~/satchel-test/lobby

Leave `run` going in its own terminal. For a real host, follow `docs/HOST-GUIDE.md` instead
and keep the root key offline.

## 4. Sign and publish the app

Sign a bundle with the root key. `read,draft` lets the app propose records; an app that only
shows things needs just `read`.

    ./bin/satchel-host root app-bundle apps/starter/hello.js --name hello \
        --title "Guestbook" --ver 1 --caps read,draft --coll apps

That writes `hello-1.bundle.json`. Publish it on the host within 10 minutes, because the
host only takes records signed in the last 10 minutes:

    ./bin/satchel-host app publish hello-1.bundle.json --prefix ~/satchel-test/lobby

## 5. Point a collection at it

Apps can draw other collections too. This makes the room `lobby` offer the guestbook and
open with it:

    ./bin/satchel-host app use --prefix ~/satchel-test/lobby --coll lobby \
        --app apps:hello --default hello

The host signs the change into its next checkpoint, and Satchel picks it up on its next sync.
`app list` shows what's published and which collection points at what.

## 6. Open it in Satchel

To reach the host from Satchel, give it its `.sesh` name: follow `<prefix>/sesh/ENABLE.txt`
(or `satchel-host sesh-help`). Then print the links:

    ./bin/satchel-host link --prefix ~/satchel-test/lobby

Open the link for `lobby`. You can add `&app=hello` to open straight in the app:

    srnet://<name>.sesh/room/lobby#root=<root pin>&app=hello

Satchel asks before it runs the app the first time, and again whenever the code changes.
Type a note, press Sign, check the sheet and confirm. The note shows up in the list.

## Changing the app later

Edit the file, sign it with a higher `--ver`, publish it, and run `app use` again. The
pointer names the exact code by its SHA-256, so a room keeps the old version until you point
it at the new one.

## Where next

- `docs/APP-API.md`: every call, the panel format and the budgets.
- `docs/API.md`: the `sesh` calls for connecting a profile, posting other record types, mail
  and voice.
- `apps/examples/`: a chat room, a forum, a wiki, polls, a file list, profiles and more.
