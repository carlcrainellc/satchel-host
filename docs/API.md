# The connect API (`sesh`)

Host apps (the JS apps a place points at, see `APP-API.md`) can ask Satchel to connect the
site to the person using it. The app gets the person's public name and key for that site,
and can propose posts, seal and open private notes, and send mail to the person's contacts.
Keys and passphrases never leave Satchel, and Satchel does all the sealing itself.

The built-in screens don't use this API; they talk to Satchel directly. Normal websites
don't get it at all: it exists only inside the app sandbox on `.sesh` places.

## What an app can call

`sesh` is a global in every host app. It can't be replaced.

| call | what it does |
|---|---|
| `sesh.version` | `1` |
| `sesh.connect({post, seal, contacts})` | asks to connect. Each key is `true` or `false`; any other key or value stops the app. The answer comes back as an event (below). |
| `sesh.post(t, body, refs)` | proposes a record, like `records.draft`. Only for record types the place already takes (`room.msg` in a chat room, and so on). |
| `sesh.contacts()` | asks for the contact list. Only if the site was granted `contacts`. |
| `sesh.seal(text, to, tag)` | asks Satchel to seal a note. `to` is `"me"` or a list of `"me"` and contact keys. Needs the `seal` grant, and a sheet every time. |
| `sesh.open(items, tag)` | asks Satchel to open sealed notes (1 to 50). Needs the `seal` grant, and a sheet every time. |
| `sesh.sendMail(to, subject, body, tag)` | asks Satchel to send mail to one contact key. Needs the `seal` grant, and a sheet every time. |
| `sesh.here()` | the place you're in and the other places on the same host. |
| `sesh.visit(id)` | goes to one of the places from `here()`. |
| `sesh.voice(tag)` | in a chat room: opens Satchel's own recorder window for the room (a mic button for your app). The person records, listens, then sends or discards it there. Needs the `post` grant; at most once every 10 s. The answer says only whether the recorder opened (`opened: true`); the app never gets the audio or the microphone, and sees no `room.voice` records. |

`tag` is any short string you like; it comes back on the answer so you can tell asks apart.

There is no call to sign arbitrary bytes, export a key, read a passphrase, or spend
money. Those don't exist in the API, so an app can't ask for them. Posts are typed
records only.

## The connect sheet

`sesh.connect` opens a sheet from the Satchel button on the toolbar. Its text comes from
Satchel, never from the site. It shows:

- the place's name (marked as chosen by the site), its address, and whether the host is
  verified;
- what the app asked for, as checkboxes: your public name and key (always), posting signed
  records, sealed mail and files, reading contacts (off unless ticked);
- which profile will be connected. People can keep several profiles, each with its own key,
  and pick a different one per site, or a throwaway key that lives only in memory.

The answer arrives as an event:

    {kind: "connect", ok: true, me: {name: "maple", key: "<hex>"},
     grants: {identity: true, post: true, seal: false, contacts: false}}
    {kind: "connect", ok: false, error: "cancelled"}

If the site is already connected with everything the app asks for, the answer comes back
without a sheet. Asking for more opens the sheet again. People can disconnect a site from
the strip's profile menu or from My Satchel; the next `connect` asks again.

## Posts

Every post still goes through a sheet showing exactly what will be signed, with the
profile it is signed as. A person can tick "Don't ask again" for one record type on one
site (for example chat messages in this room). That choice is never offered for mail, files,
money, or a record type the place didn't take before. Ticking it doesn't connect the
site or show the app who you are.

The result is a `draft` event, the same as `records.draft`:

    {kind: "draft", ok: true, id: "<record id>", error: null, queued: false}

`queued: true` (with `ok: false`) means the post was signed but no host answered in time.
It may have arrived; Satchel keeps that same signed post and sends it again, and a host that
already has it doesn't store a second copy. Don't offer the text to send again.

Don't tell people to approve a chat post: when they've said "Don't ask again" no sheet
appears. Say "Sending…" and let Satchel's sheet speak for itself. For mail, sealing and
opening a sheet always appears.

Text boxes keep what the person typed when the app redraws with the same `v`. To clear a
composer after sending, draw it under a new `id` (Harbor uses `text-1`, `text-2`, …). Satchel
builds after 0.2.4 also clear a box that still holds exactly the text of a post once it went
out.

## Contacts

    sesh.contacts()
    → {kind: "contacts", ok: true, list: [{name, key}, ...]}

Only the names and public keys of the profile in use, and only when the site was granted
`contacts`.

## Records

`coll.read()` gives each record a few extra fields so an app doesn't have to work them out:

- `who`: a friendly name. Your own contact name for that key if you have one, else the
  name they chose in this place, else a short made-up handle. Never the raw key.
- `you`: `true` for your own posts.
- `time`, `day`, `date`: when it was posted, in this computer's time zone (for example
  `"10:29 AM"`, `"Today"`). The sandbox clock itself runs in UTC, so use these, not `ts`.
- `sending`, `sent`: your own post shows up at once, before the room has it back. Until
  then it carries `sending: true`, and `sent: true` once a host has taken it. It turns into a
  normal record (no `sending`) when the next read brings it back, and it goes away if every
  host refuses it. Mark it however you like ("sending…", "sent"), or skip records with `sending`.

`view`, `draft-result` and `result` events get 5 seconds to answer (`ui` events still get
1 second), since a reply often waits on a post being signed.

## Sealed notes

    sesh.seal("meet at 6", ["me", "<contact key>"], "n1")
    → {kind: "seal", ok: true, tag: "n1", sealed: "sealed1.…"}

    sesh.open(["sealed1.…"], "n1")
    → {kind: "open", ok: true, tag: "n1",
       items: [{text: "meet at 6", ts: 1793030400, from: {name: "maple", key: "<hex>", you: true}}]}

A sealed note is plain text you can post or keep like any other string. Only the people it
was sealed for can open it, and only on the same site: a note sealed on one place won't open
on another. An item that isn't for you, or was changed, comes back as `null`.

Satchel seals with ChaCha20-Poly1305, wraps the note key for each reader with X25519, and
signs the note with the sender's key. The app only ever sees the sealed string and, after
the person agrees, the opened text.

## Mail

    sesh.sendMail("<contact key>", "Hello", "See you at the pier.", "m1")
    → {kind: "mail", ok: true, tag: "m1"}

Mail goes only to keys already in the person's contacts, from the profile whose mail is
open. The sheet shows who it goes to, the subject and the whole body.

## Here and visit

    sesh.here()
    → {kind: "here", ok: true, host: "Otter Bay",
       place: {title: "Lobby", kind: "chat"},
       places: [{id: "a1b2c3d4", title: "Lobby", kind: "chat", current: true}, ...]}

    sesh.visit("a1b2c3d4")
    → {kind: "visit", ok: true}   (then the tab moves to that place)

## Always asked

Seal, open and mail open a sheet every time. "Don't ask again" is never offered for them,
or for posts of mail, files, money, or a record type the place didn't take before.

## Limits

Connect, post and other asks share the app's rate limit (10 a minute). An app that keeps
asking is stopped. Answers are checked before the app sees them.

## Helper

`apps/examples/lib/sesh-kit.js` is a short helper you can paste at the top of an app. It
keeps track of the connection and gives you `kit.connect()`, `kit.me`, and a way to react to
answers without writing the event plumbing yourself. The Harbor Chat demo
(`apps/examples/harbor.js`) shows the same flow written out by hand.

## Example

    let me = null;
    function draw() {
      panel.render({t: "col", c: [
        {t: "text", v: me ? "Hi " + me.name : "Not connected"},
        me ? {t: "button", id: "hello", v: "Say hello"} : {t: "button", id: "connect", v: "Connect"},
      ]});
    }
    onView(draw);
    onEvent((e) => {
      if (e.kind === "click" && e.id === "connect") sesh.connect({post: true});
      if (e.kind === "click" && e.id === "hello") sesh.post("room.msg", {text: "hello"});
      if (e.kind === "connect" && e.ok) { me = e.me; draw(); }
    });
    draw();
