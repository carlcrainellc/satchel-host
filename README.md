# satchel-host

Tools for two jobs: running a host for a Satchel community, and writing apps that run
inside Satchel.

## What a host is

A Satchel community keeps its posts, pages and files on a **host**: a small server program
you run yourself. One host can hold any of these:

- chat rooms
- a wiki
- a file drop (a shared place to upload files)
- a profile directory
- a directory of communities (a list people can browse to find and join communities)
- an app collection (a set of apps for your community)
- a mail host
- a message board

Everything on a host is a **record**: one chat message, one wiki edit, one profile. Each
record is **signed**, which means it carries a digital stamp made with the key of the person
who wrote it. Satchel checks that stamp on every record before it shows anything. So the
host can store and hand out posts, but it can't fake them or change them without being
caught.

Two safety habits are built in:

- Your **root key** (the master key that proves the host is yours) stays on a separate
  machine that is offline. It never goes on the server.
- A new host only answers on its own machine (`127.0.0.1`) at first. It is reachable by
  others only after you turn on its `.sesh` name, the address people use to reach it. The
  host guide shows how.

## What an app is

An app is a small JavaScript program kept in a community's app collection. Satchel runs it
in a **sandbox**: a closed-off space where it can't reach the network, the page around it or
anyone's key. Inside that space an app can do three things:

- read the records of one collection (one room, one wiki and so on),
- draw a panel on the screen,
- suggest a new record. Nothing is posted until the person checks it and signs it.

To write one, start with [docs/WRITE-YOUR-FIRST-APP.md](docs/WRITE-YOUR-FIRST-APP.md).

## Running a host

You need Python 3.10 or newer and the `cryptography` package. On Debian or Ubuntu:
`sudo apt install python3 python3-cryptography`.

[docs/HOST-GUIDE.md](docs/HOST-GUIDE.md) walks through every step. Here is the short version.
Some steps happen on your offline machine and some on the server:

    ./bin/satchel-host root init                                     # on your offline machine
    sudo ./bin/satchel-host init --type rooms --name lobby --sesh new  # on the server
    ./bin/satchel-host root sign request.json --days 90              # offline machine
    sudo ./bin/satchel-host install-delegation delegation.json --prefix /opt/satchel-host/lobby
    sudo ./bin/satchel-host status --prefix /opt/satchel-host/lobby
    ./bin/satchel-host link --prefix /opt/satchel-host/lobby         # invite links to share

What each step does:

1. `root init` makes your root key on the offline machine.
2. `init` sets up the host on the server. It makes the host's own everyday key and a file
   called `request.json`. `--sesh new` also makes a new `.sesh` name for it.
3. You carry `request.json` to the offline machine. `root sign` uses the root key to approve
   the server for up to 90 days. This approval is called a **delegation**, and it is saved
   as `delegation.json`. That file is public and safe to copy.
4. `install-delegation` puts the approval on the server and starts the host. On a machine
   without systemd, start it yourself with `satchel-host run --prefix …`.
5. `status` shows whether the host is healthy and how many days the approval has left.
6. `link` prints the invite links you can share.

### Host types

Pick one with `--type`:

| Type | Holds |
|---|---|
| `rooms` | chat rooms. `--room` adds more rooms, and `--voice` lets people post voice clips. |
| `wiki` | a wiki |
| `files` | a file drop |
| `people` | a profile directory |
| `directory` | a directory of communities |
| `apps` | an app collection |
| `mail` | a mail host |
| `board` | a message board |

The `rooms`, `wiki`, `files`, `people`, `directory` and `apps` types can also hold an app
collection next to their main content: add `--apps NAME`.

### Later commands

Once the host is running, you'll use these:

- `renew`: get a fresh approval before the 90 days run out.
- `upgrade`: move to a new release.
- `backup`: save a copy of the host's data and host key. Keep it private.
- `moderate`: hide, pin or remove posts.
- `app publish` and `app use`: add apps and choose which apps a collection opens with.
- `root directory-entry` and then `directory-submit`: list your community in a directory.

### Invite links

Invite links look like this:

    srnet://<name>.sesh/room/hikers#root=<root pin>

The **root pin** is the public half of your root key. Satchel remembers it the first time
someone joins, so later it can tell your real host from a fake one. Satchel opens these
links exactly as they are. To open a collection in one of its apps, add `&app=<name>` to
the end.

## Writing apps

| Where | What |
|---|---|
| [docs/WRITE-YOUR-FIRST-APP.md](docs/WRITE-YOUR-FIRST-APP.md) | builds a guestbook app, from an empty file to a running app |
| [docs/APP-API.md](docs/APP-API.md) | everything an app can call, how to describe its panel, and its limits |
| [docs/API.md](docs/API.md) | the `sesh` API: how web pages and apps ask Satchel to do things |
| [apps/starter/hello.js](apps/starter/hello.js) | the starter app |
| [apps/examples/](apps/examples/) | bigger apps: chat, forum, wiki, polls, files, profiles and more |

## Releases

Each release comes with four files: `satchel-host-<ver>.tar.gz` (the code),
`SHA256SUMS` (a fingerprint of the code), `SHA256SUMS.sig` (a signature on that fingerprint)
and `allowed_signers` (the key that made the signature). Check them before you unpack. You
need OpenSSH 8.1 or newer:

    sha256sum -c SHA256SUMS
    ssh-keygen -Y verify -f allowed_signers -I release@satchel -n satchel-release \
        -s SHA256SUMS.sig < SHA256SUMS

The first command checks that the download isn't damaged or changed. The second checks
that the release key really signed it. Both should succeed. Also make sure the key in
`allowed_signers` matches the published release key. `RELEASING.md` shows how a release is
built.

## What's in here

| Path | What |
|---|---|
| `bin/satchel-host` | the tool you use to set up and run a host |
| `typed/` | the host program itself: records, rules, saved checkpoints, rooms, apps, mail, voice |
| `mbfmt/ mbhost/ mbclient/` | the older message board: its data format, its host, and a small client that `scripts/demo.py` uses |
| `apps/` | the starter app and the example apps |
| `docs/` | the host guide, the app guides and the license notes |
| `tests/` | tests (see below) |

## Tests

    python3 -m unittest discover -s tests

This runs every step from the host guide in a temporary folder, including publishing an
app. To also test with a real client (joining, reading the directory and sending mail), set
`PY_CLIENT` to a checkout of the Python reference client.

To test a host against Satchel itself, set `SATCHEL` to a Satchel checkout (run
`npm install` in it first) and run:

    SATCHEL=/path/to/satchel node tests/e2e_satchel.mjs

This sets up a rooms host with an app collection and publishes the starter app. Then
Satchel's own code downloads the app, checks it, runs it, and posts a record through it.

## License

Copyright the Satchel authors. Licensed under the Mozilla Public License 2.0, see `LICENSE`.
`docs/LICENSES.md` has the details.
