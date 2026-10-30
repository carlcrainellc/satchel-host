# Running your own host

*satchel-host 0.5.0*

satchel-host runs a piece of a Satchel community on your own server: chat rooms, a wiki, a
file drop, a profile directory, a directory of communities, an app collection, a mail host or
an older-style message board. Your server only stores and serves signed data. Satchel checks
every record before it shows it, so the server can't forge posts or quietly change them.

## What you need

- A server with Python 3.10 or newer and the `cryptography` package
  (`apt install python3-cryptography` on Debian/Ubuntu). On a machine with systemd the tool
  installs a locked-down service; anywhere else, `satchel-host run` runs the host in the
  foreground.
- A **second machine for the root key**, ideally one that stays offline (an old laptop,
  or a live USB). The root key is the identity of your host. If someone steals it, they can
  pose as you, so it never goes on the server.
- About 10 minutes.

## The two keys, in plain words

| Key | Lives on | Does |
|---|---|---|
| **Root key** | your offline machine | Vouches for your server for up to 90 days at a time. Its public half (the **root pin**) travels in every invite link, and Satchel pins it on first join. |
| **Host key** | the server | Signs the day-to-day data. Made automatically. If the server is compromised you just stop renewing it. |

## Step by step

**1. On the offline machine: create the root key (once).**

    ./bin/satchel-host root init --dir ./satchel-root

Write down the root pin it prints. Back up `satchel-root/root.key` (USB stick, paper copy).

**2. On the server: prepare the host.**

    sudo ./bin/satchel-host init --type rooms --name lobby --room hikers --room maps

Types:

| Type | Serves |
|---|---|
| `rooms` | one or more chat rooms (`--room NAME` adds another, repeatable) |
| `wiki` | a wiki |
| `files` | an encrypted file drop (files up to 4 MiB, ciphertext only) |
| `people` | a profile directory |
| `directory` | a directory of communities; each entry is signed by the listed collection's root key |
| `apps` | an app collection: small JavaScript apps that Satchel runs in a sandbox |
| `mail` | a mail host: sealed store-and-forward mail |
| `board` | a message board in the older single-board format |

Names start with a letter and use lowercase letters, digits and dashes. Options: `--port`,
`--prefix` (default `/opt/satchel-host/<name>`), `--admin <pubkey>` (wiki, rooms, people,
directory, apps), `--apps NAME` (any of those: also serve an app collection called NAME on the
same host, see "Apps" below) and `--voice` (rooms: take voice clips, see "Voice clips").

`init` also asks about the host's own **`.sesh` name**: **make** a new key, **import** a key file
you already have (for example one from a vanity-name search), or **skip** and add one later.
Flags do the same without a prompt: `--sesh new`, `--sesh-import FILE`, `--sesh skip`. Without a
terminal the default is skip. `satchel-host sesh --prefix … --sesh new` adds a name later.

This creates a host key and a file called `request.json`.

**3. Carry `request.json` to the offline machine and sign it.**

    ./bin/satchel-host root sign request.json --days 90

Before you answer `y`, check that the host key it shows matches the one the server printed.
This writes `delegation.json`, which is public and safe to copy around.

**4. Back on the server: install it.**

    sudo ./bin/satchel-host install-delegation delegation.json --prefix /opt/satchel-host/lobby

With systemd, this installs a locked-down system service and starts it. Without systemd, or
with `--no-systemd`, start the host with `satchel-host run --prefix …`. Either way it prints
your connect info: the type, the collections, the root pin and an invite link for each
collection.

**5. Check on it any time.**

    sudo ./bin/satchel-host status --prefix /opt/satchel-host/lobby
    ./bin/satchel-host link --prefix /opt/satchel-host/lobby

`status` shows health, days left on the delegation, the `.sesh` name and the invite links.
`link` prints just the links, ready to share.

## Letting people in

Out of the box your host listens **only on 127.0.0.1**. To open it up, start a separate
Session Router instance for the host's own `.sesh` name: `init` already wrote its key and
config to `<prefix>/sesh/`, and `sesh/ENABLE.txt` lists the exact steps. The instance maps
`http://<name>.sesh` to the host on loopback. `satchel-host sesh-help` explains the same in
short.

Then share the invite links from `satchel-host link`. Each one looks like

    srnet://<name>.sesh/room/hikers#root=<root pin>

and carries everything Satchel needs: the address, the collection and your root pin. A
bare `<name>.sesh` also works: Satchel lists every collection the host offers. Adding
`&app=<name>` to a link opens the collection in one of its apps (see "Apps").

## Listing in a directory

A directory lists communities whose operators opted in. Each entry must be signed by the
root key of the collection it lists, so nobody can list your community but you, and nobody
can point your title at someone else's host.

On the offline machine, sign an entry for one of your own links:

    ./bin/satchel-host root directory-entry --link 'srnet://<name>.sesh/room/hikers#root=<pin>' \
        --title "Hikers" --desc "Weekend walks, routes and meetups" --directory-name directory

Carry `directory-entry.json` to any online machine and post it:

    ./bin/satchel-host directory-submit directory-entry.json --to http://<directory>.sesh/c/directory

To run a directory yourself, use `--type directory`. Satchel shows only entries signed by the
listed collection's own root key.

## Apps

An app collection holds small JavaScript apps. Satchel runs them in a sandbox and lets them
read one collection, draw a panel and propose records that the person signs themselves.
`docs/APP-API.md` describes what an app can do, and `docs/WRITE-YOUR-FIRST-APP.md` walks
through one.

Every app is published by your root key, so it's a two-machine job like the delegation:

**1. Offline: sign the bundle.**

    ./bin/satchel-host root app-bundle hello.js --name hello --title "Guestbook" --ver 1 \
        --caps read,draft --coll apps

This writes `hello-1.bundle.json` with the source and a record signed by your root key.
`--caps read` makes an app that can only look; `read,draft` lets it propose records.

**2. On the server, within 10 minutes: publish it.**

    sudo ./bin/satchel-host app publish hello-1.bundle.json --prefix /opt/satchel-host/lobby

The host only takes records signed in the last 10 minutes, so carry the file over and publish
it straight away. If you miss the window, sign it again. The host refuses bundles that
aren't signed by your root key, and nobody can upload app code over the network.

**3. Choose which apps a collection opens with.**

    sudo ./bin/satchel-host app use --prefix /opt/satchel-host/lobby --coll hikers \
        --app apps:hello --default hello

This puts a pointer (collection, name and SHA-256 of the code) into the room's signed
settings. Satchel runs an app only when the pointer, the hash and both signatures match.
Up to 8 apps per collection. `--default` picks the one Satchel opens first, `--clear` removes
them all, and `app list` shows what's published and pointed at. A new version of an app
needs a new `app use`, because the pointer names the exact code.

To add an app collection to a host you already run, use `app add-collection --name apps`,
then sign and install the new `request.json` like the first time.

## Voice clips

`init --type rooms … --voice` lets people post short voice clips (up to 30 seconds) in the
host's rooms. Clip files are deleted 7 days after upload. In each room, each key can store up
to 4 MiB of clips and post 60 clips in any 24 hours.

## Running a mail host

    sudo ./bin/satchel-host init --type mail --name post1

A mail host keeps sealed messages for people until they pick them up. It can't read them
and can't see who sent them. It listens on `127.0.0.1:48915` by default. Signing and
installing work the same as for the other types, and `satchel-host link` prints the mail host
URL people add as one of their mail hosts.

**Open or closed.** A mail host is **open** by default: anyone can create an inbox, up to
1000. To stop new inboxes (existing ones keep working):

    sudo ./bin/satchel-host mail --prefix /opt/satchel-host/post1 --closed

`--open` turns sign-up back on and `--max-inboxes N` changes the cap. You can also start
closed with `satchel-host init --type mail --name post1 --closed`.

- Each inbox holds up to 256 messages or 8 MiB, plus a "requests" pile for mail from
  strangers: 32 messages of up to 16 KiB, 20 per hour.
- Messages are deleted after their owner's Satchel confirms it saved them, or after 7 days
  (up to 30 if the sender asks) if never confirmed.
- Total storage is capped at 1 GiB.

## Keeping it running

- **Renew every 90 days or sooner.** `status` warns you two weeks ahead. Run
  `satchel-host renew`, sign the new `request.json` on the offline machine, then run
  `install-delegation` again. The server refuses a delegation from a different root unless
  you pass `--allow-root-change`.
- **Upgrade:** unpack the new release, verify it, and run its
  `./bin/satchel-host upgrade --prefix …`. It swaps in the new code, keeps your keys, data,
  delegations and `.sesh` name, keeps the previous code in `<prefix>/app.prev`, and restarts
  the service. It refuses to move to an older version unless you pass `--force`.
- **Back up:** `satchel-host backup --prefix …` writes a `.tar.gz` with your data and host key.
  Keep it private. It never contains the root key.
- **Remove:** `satchel-host uninstall --prefix …` stops the service and keeps the data.
  Add `--purge --yes` to delete everything.

## Moderating

Find a post's id in Satchel (⋯ menu) or in `/v1/log`. Every action is a signed, public
record, so users can see what you did.

- **Hide** (spam, off-topic): the post stays on your disk and keeps syncing. Satchel hides
  it by default, and users can choose to show it.
  `satchel-host moderate --prefix … hide post <id> --reason spam` (`unhide` reverses it;
  `hide author <key>` hides everything from one key).
- **Pin** up to 3 posts at the top of a room or board:
  `satchel-host moderate --prefix … pin post <id>` (`unpin` to drop one).
- **Header:** the description and rules shown at the top of the room:
  `satchel-host header --prefix … --description "…" --rules "…"`.

Add `--coll <room>` for a room other than the host's main one.

### Removing content

Use **remove** for content you must not keep: child abuse material, doxxing, credible threats,
or anything a valid legal order covers.

    satchel-host moderate --prefix … remove post <id> --reason "illegal content"

It asks you to type `REMOVE` (or pass `--yes`). Your host then deletes the post text from its
disk at once. For a file, it deletes the file's bytes too. The post leaves a small signed
placeholder, so the room's history still verifies. Mirrors and Satchel delete their
copies the next time they sync, and the post shows as "Removed by host: <reason>". Nobody can
show it again. A remove can't be undone, and the record, including your reason, is public, so
don't put personal data in the reason. Removing content doesn't take back copies someone
already downloaded or screenshotted. Report illegal material to the authorities where the
law requires it.

## File limits (files hosts)

`satchel-host init --type files … --max-file-mb 25 --quota-mb 500` sets the largest file and
the MiB each uploader key may store (0 means no quota). Change them later with
`satchel-host files --prefix … --max-file-mb 50`. The limits are in your signed policy, so
Satchel refuses a file that's too large before uploading it. Large files upload in 1 MiB chunks
and resume after an interruption. Make sure your disk can hold the total you allow.

## What your host does and doesn't do

- It checks every record's signature and its small proof-of-work puzzle, and it applies rate
  limits. New keys get a slightly harder puzzle.
- You can hide, remove and pin posts (see "Moderating" above). Every action is signed and public.
- Apps are added only by you, with your root key. Satchel asks people before it runs one,
  and again whenever its code changes.
- Files are stored as ciphertext and served only by their SHA-256 hash. You set the largest
  file size (25 MiB by default) and can set a storage quota per uploader (see "File limits").
- You are responsible for what you host under your local laws.

## Security defaults the installer sets

Runs as an unprivileged `satchelhost` user. The data directory is mode 700 and the host key
is mode 600. Under systemd the service has no capabilities, a read-only system, no home
access, private /tmp and devices, and its network is limited to localhost by systemd's IP
filter. The host refuses to bind a public address.

## .sesh key files

- Import checks the key, copies it unchanged with mode 600, and shows the resulting name.
  It never prints the secret.
- Accepted key files: a 32-byte seed or a 64-byte seed+public key (as hex or raw bytes), or a
  `d1:s64:…e` identity file.
- `init` and `sesh` refuse to overwrite an existing `.sesh` key.

## Verifying a download

Releases ship `satchel-host-<ver>.tar.gz`, `SHA256SUMS`, `SHA256SUMS.sig` and
`allowed_signers`. Check them before unpacking:

    sha256sum -c SHA256SUMS
    ssh-keygen -Y verify -f allowed_signers -I release@satchel -n satchel-release \
        -s SHA256SUMS.sig < SHA256SUMS

Compare the key in `allowed_signers` with the release key published alongside the release.
