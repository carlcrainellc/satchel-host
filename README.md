# satchel-host

Tools for running a host for Satchel communities and for
writing Satchel apps.

A host keeps the data for a community: chat rooms, a wiki, a file drop, a profile directory,
a directory of communities, an app collection or a mail host. It only stores and serves signed
records. Satchel checks every record itself before it shows anything, so a host can't forge
posts or change them quietly. Your root key stays on a machine that's offline, and the host
listens only on `127.0.0.1` until you open it up through its own `.sesh` name.

Apps are small JavaScript programs that a collection publishes. Satchel runs them in a
sandbox and they can read one collection, draw a panel and suggest records for the person to
sign. If you want to write one, start with
[docs/WRITE-YOUR-FIRST-APP.md](docs/WRITE-YOUR-FIRST-APP.md).

## Running a host

You need Python 3.10 or newer and the `cryptography` package
(`sudo apt install python3 python3-cryptography` on Debian/Ubuntu).
[docs/HOST-GUIDE.md](docs/HOST-GUIDE.md) goes through it step by step. The short version:

    ./bin/satchel-host root init                                     # on your offline machine
    sudo ./bin/satchel-host init --type rooms --name lobby --sesh new  # on the server
    ./bin/satchel-host root sign request.json --days 90              # offline machine
    sudo ./bin/satchel-host install-delegation delegation.json --prefix /opt/satchel-host/lobby
    sudo ./bin/satchel-host status --prefix /opt/satchel-host/lobby
    ./bin/satchel-host link --prefix /opt/satchel-host/lobby         # invite links to share

Host types: `rooms` (`--room` adds more rooms, `--voice` takes voice clips), `wiki`, `files`,
`people`, `directory`, `apps`, `mail` and `board`. Any hub type can also carry an app
collection with `--apps NAME`. Later on you'll use `renew`, `upgrade`, `backup`,
`moderate`, `app publish` / `app use`, and `root directory-entry` with `directory-submit`
to list your community in a directory.

Invite links look like `srnet://<name>.sesh/room/hikers#root=<root pin>`. Satchel opens
them as they are; add `&app=<name>` to open the collection in one of its apps.

## Writing apps

| Where | What |
|---|---|
| [docs/WRITE-YOUR-FIRST-APP.md](docs/WRITE-YOUR-FIRST-APP.md) | a guestbook app from an empty file to a running app |
| [docs/APP-API.md](docs/APP-API.md) | everything an app can call, the panel format and the limits |
| [docs/API.md](docs/API.md) | the `sesh` API that web pages and apps use to ask Satchel for things |
| [apps/starter/hello.js](apps/starter/hello.js) | the starter app |
| [apps/examples/](apps/examples/) | bigger apps: chat, forum, wiki, polls, files, profiles and more |

## Releases

Each release has `satchel-host-<ver>.tar.gz`, `SHA256SUMS`, `SHA256SUMS.sig` and
`allowed_signers`. Check them before you unpack (OpenSSH 8.1 or newer):

    sha256sum -c SHA256SUMS
    ssh-keygen -Y verify -f allowed_signers -I release@satchel -n satchel-release \
        -s SHA256SUMS.sig < SHA256SUMS

Both should succeed, and the key in `allowed_signers` should match the published release
key. `RELEASING.md` shows how a release is built.

## What's in here

| Path | What |
|---|---|
| `bin/satchel-host` | the setup and operator tool |
| `typed/` | the host: records, policies, checkpoints, rooms, apps, mail, voice |
| `mbfmt/ mbhost/ mbclient/` | the older message board format, its host and a small client used by `scripts/demo.py` |
| `apps/` | the starter app and the example apps |
| `docs/` | host guide, app guides and license notes |
| `tests/` | tests, see below |

## Tests

    python3 -m unittest discover -s tests

This runs every flow from the host guide in a temporary directory, including publishing an
app. Set `PY_CLIENT` to a checkout of the Python reference client to also join, read the
directory and send mail with a real client.

To check a host against Satchel itself, point `SATCHEL` at a Satchel checkout (with its
`npm install` done) and run

    SATCHEL=/path/to/satchel node tests/e2e_satchel.mjs

It sets up a rooms host with an app collection, publishes the starter app, and has Satchel's
own code fetch, check and run it, then post a record through it.

## License

Copyright the Satchel authors. Licensed under the Mozilla Public License 2.0, see `LICENSE`.
`docs/LICENSES.md` has the details.
