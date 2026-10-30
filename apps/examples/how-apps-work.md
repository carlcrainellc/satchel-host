How apps work

This demo host keeps its data as signed records: a chat message, a wiki edit, a profile, a vote. Each record is signed by the key of the person who wrote it. The host stores the records and hands them out. It can't change them without the signatures breaking.

A view is the screen that draws those records. Satchel has a few views built in (chat, wiki, directory, profiles, mail and file drops). A collection can also name small apps written in JavaScript that draw the same records their own way.

Same records, different views

The chat room here has three entries in Satchel's list:
- Chat (built-in) is Satchel's own chat screen.
- Chat (JS app) is a small app with bubbles, initials and a line for each day.
- Chat (last 5) shows only the last five messages, one per line.
All three read and write the same room. A message you send from one shows up in the other two.

Who decides which apps a collection offers

The owner of a collection signs its settings. Those settings can list apps by name and by a fingerprint (a SHA-256 hash) of the app's code. The code itself lives in the "Demo apps" collection and is signed by the same owner. Satchel runs an app only when the name, the fingerprint and both signatures match. If anything is off, it tells you why and shows the records as a plain read-only list instead.

What an app can and can't do

An app runs in a sandbox inside Satchel. It has no network, no cookies, no access to the page and no access to your key. It can:
- read the records of the collection it was opened on,
- draw a screen from a short list of building blocks (text, buttons, inputs, lists),
- propose a new record, such as a chat message or a wiki edit.
Nothing is posted until you see exactly what will be signed and press Sign and post. Apps can't propose key changes, file shares, orders or moderation; those stay in Satchel's own screens. An app's code is capped at 256 KiB.

Collections with no view

The "Notices" collection has no app and no built-in view. Satchel shows its records as a read-only list: type, author, time and the text, with nothing run.

Trying it

Open Chat (built-in) and Chat (JS app) side by side, post from each and watch both. Edit this page from the wiki app. Vote in Polls. Join links for everything here are in the Directory.

This is a test host. Posts may be cleared.
