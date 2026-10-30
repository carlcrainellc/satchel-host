# Example apps

These are the demo apps that ship with the Satchel test hosts. They're bigger than the
starter app and show more of what an app can do. `how-apps-work.md` explains, for people
using Satchel, how apps, built-in views and pointers fit together.

| File | Draws | Records it uses |
|---|---|---|
| `notes.js` | shared notes | `app.data` |
| `poll.js` | quick polls with votes | `app.data` |
| `forum.js` | topics and replies | `app.data` |
| `apps-home.js` | the home screen of an app collection | `app.data` |
| `chat.js` | a chat room as message bubbles | `room.msg`, `room.join`, `room.topic` |
| `chat-mini.js` | the five newest messages and a post box | `room.msg` |
| `harbor.js` | a chat room with a sidebar, the connect API, contacts, mail and voice | `room.msg`, `room.join`, `room.topic`, `sesh` calls |
| `wiki.js` | a wiki with pages and history | `wiki.page`, `wiki.rev`, `wiki.revert` |
| `directory.js` | a directory as cards with a Join button | `directory.entry` |
| `profile.js` | profiles | `id.profile` |
| `files.js` | what's in a file drop | `drop.*`, read only |
| `notices.js` | a host's notices as cards | `app.data`, read only |
| `hostile.js` | nothing useful: it tries to break out of the sandbox, to test it | |

`lib/kit.js` holds the helpers that most of these start with (times, names, a screen that's
never blank); the apps have it pasted in at the top, since an app is a single file.
`lib/sesh-kit.js` is a helper for apps that use the `sesh` connect API (`docs/API.md`).

Publish any of them the same way as the starter (`docs/WRITE-YOUR-FIRST-APP.md`). Apps that
draw another collection, like `chat.js` for a room, are published in an app collection and
then pointed at with `satchel-host app use --coll <room> --app <app collection>:chat`.
Apps that post need `--caps read,draft`.
