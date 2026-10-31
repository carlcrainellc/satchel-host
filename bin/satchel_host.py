#!/usr/bin/env python3
"""satchel-host: set up and run your own host for Satchel (rooms, wiki, files, people,
directory, apps, mail, board) with an OFFLINE root key.

Host machine:   init -> (send request.json to root machine) -> install-delegation -> status
Root machine:   root init  /  root sign request.json  /  root directory-entry  /  root app-bundle
Apps:           app publish, app use, app list, app add-collection
Upkeep:         link, renew, upgrade, backup, uninstall, mail, sesh, sesh-help, directory-submit,
                directory-refresh, directory pending|approve|reject

Safe defaults: binds 127.0.0.1 only, no root key ever on the host, delegations <= 90 days,
Session Router / .sesh exposure is prepared but switched on only by you.
"""
from __future__ import annotations

import argparse
import grp
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PACK_ROOT)
import mbfmt as F  # noqa: E402

VERSION = "0.5.1"
# host type -> (process, collection kind).  "board" runs the message-board host; the rest
# run the typed hub (hub types can also carry app collections, see --apps).
TYPES = {"board": ("board", None), "wiki": ("hub", "wiki"),
         "rooms": ("hub", "room"), "files": ("hub", "drop"), "people": ("hub", "id"),
         "directory": ("hub", "directory"), "apps": ("hub", "app"), "mail": ("mail", None)}
DEFAULT_PORT = {"board": 48900, "wiki": 48911, "rooms": 48912, "files": 48913,
                "people": 48914, "mail": 48915, "directory": 48916, "apps": 48917}
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")  # always fullmatch
APP_DIRS = tuple(d for d in ("mbfmt", "mbhost", "typed", "vendor") if os.path.isdir(os.path.join(PACK_ROOT, d)))
SERVICE_USER = "satchelhost"
USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")  # always fullmatch
PREFIX_RE = re.compile(r"/[A-Za-z0-9._/-]{0,200}")  # absolute, one token, no quotes or spaces
MAX_DAYS = F.MAX_DELEGATION_S // 86400
REQ_KIND = "srnet-host-delegation-request/v1"


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def write_private(path, data: bytes, mode=0o600):
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def jdump(obj) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode()


def load_cfg(prefix):
    p = os.path.join(prefix, "host.json")
    if not os.path.exists(p):
        die(f"no host at {prefix} (run init first)")
    with open(p) as f:
        return json.load(f)


def save_cfg(cfg):
    write_private(os.path.join(cfg["prefix"], "host.json"), jdump(cfg), 0o644)


def host_key(cfg):
    with open(os.path.join(cfg["prefix"], "data", "host.key"), "rb") as f:
        return F.key_from_seed(f.read())


def safe_coll(name):
    """A collection name, checked before it becomes part of any path."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        die(f"bad collection name {name!r}")
    return name


def colls(cfg):
    """Collections this host serves: its name, any extra rooms (rooms hosts), then any app
    collections (hub hosts).  Every name is checked, since each one becomes a path."""
    return [safe_coll(c) for c in
            (cfg.get("collections") or [cfg["name"]]) + list(cfg.get("app_colls") or [])]


def check_user(user):
    if not isinstance(user, str) or not USER_RE.fullmatch(user):
        die(f"--user {user!r}: a plain user name (lowercase letters, digits, _ and -, max 32)")
    if user == "root":
        die("--user root: the host must not run as root; leave --user out to use "
            f"{SERVICE_USER}")
    return user


def check_prefix(prefix):
    """An absolute path made of safe characters only (it goes into the systemd unit)."""
    p = os.path.abspath(prefix)
    if not PREFIX_RE.fullmatch(p) or any(part in ("..", ".") for part in p.split("/")) or p == "/":
        die(f"--prefix {prefix!r}: an absolute path of letters, digits, '.', '_', '-' and '/'")
    return p


def coll_kind(cfg, coll):
    """The kind of one of this host's collections."""
    if cfg["type"] == "board":
        return "board"
    return "app" if coll in (cfg.get("app_colls") or []) else TYPES[cfg["type"]][1]


def link_kind(cfg):
    return "board" if cfg["type"] == "board" else TYPES[cfg["type"]][1]


def unit_name(cfg):
    return f"satchel-host-{cfg['name']}"


def is_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0


def have_systemd():
    return os.path.isdir("/run/systemd/system") and shutil.which("systemctl")


def sh(*cmd, check=True):
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


# ---- request / delegation ----------------------------------------------------------------
def make_request(cfg):
    return {"kind": REQ_KIND, "v": 1, "type": cfg["type"], "name": cfg["name"],
            "collections": colls(cfg), "host": F.pub_hex(host_key(cfg)),
            "created": int(time.time()), "pack": VERSION}


def write_request(cfg):
    p = os.path.join(cfg["prefix"], "request.json")
    write_private(p, jdump(make_request(cfg)), 0o644)
    return p


def sign_request(root_key, req: dict, days: int, now=None) -> dict:
    if req.get("kind") != REQ_KIND:
        die("not a satchel-host delegation request")
    if not (1 <= days <= MAX_DAYS):
        die(f"--days must be 1..{MAX_DAYS}")
    F.load_pub(req["host"])
    now = int(time.time()) if now is None else now
    out = {"kind": "srnet-host-delegation/v1", "root": F.pub_hex(root_key), "host": req["host"],
           "delegations": {}}
    for coll in req["collections"]:
        if not NAME_RE.fullmatch(coll):
            die(f"bad collection name {coll!r}")
        d = {"v": 1, "board": coll, "root": F.pub_hex(root_key), "host": req["host"],
             "issued": now, "expires": now + days * 86400, "policy": ""}
        out["delegations"][coll] = {"delegation": d,
                                    "sig": root_key.sign(F.canonical(F.DELEGATION_DOMAIN, d)).hex()}
    return out


def check_bundle(cfg, bundle: dict, allow_root_change=False) -> str:
    hk = F.pub_hex(host_key(cfg))
    root = bundle.get("root", "")
    if cfg.get("root_pin") and root != cfg["root_pin"] and not allow_root_change:
        die("delegation is signed by a DIFFERENT root than before; refusing "
            "(use --allow-root-change only if you really rotated the root)")
    for coll in colls(cfg):
        env = bundle.get("delegations", {}).get(coll)
        if not env:
            die(f"bundle has no delegation for {coll}")
        if F.verify_delegation(env, root, coll) != hk:
            die("delegation is for a different host key")
    return root


# ---- systemd unit ------------------------------------------------------------------------
def exec_cmd(cfg):
    pre = cfg["prefix"]
    py = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable
    if cfg["type"] == "board":
        # same soft PoW as the older board hosts (16 / 18 for new keys) unless --pow-bits
        pb = int(cfg.get("pow_bits") or 16)
        return [py, "-m", "mbhost.host", "--data", f"{pre}/data", "--board", safe_coll(cfg["name"]),
                "--bind", "127.0.0.1", "--port", str(cfg["port"]),
                "--delegation", f"{pre}/delegation/{cfg['name']}.json", "--tick", "30",
                "--pow-bits", str(pb), "--new-key-pow-bits", str(pb + 2)]
    if cfg["type"] == "mail":  # sealed store-and-forward; never sees content or senders
        return [py, "-m", "typed.mailhost", "--data", f"{pre}/data", "--bind", "127.0.0.1",
                "--port", str(cfg["port"]), "--delegation", f"{pre}/delegation/{cfg['name']}.json",
                "--max-inboxes", str(cfg.get("max_inboxes", 1000))] + (["--closed"] if cfg.get("closed") else [])
    kind = TYPES[cfg["type"]][1]
    cmd = [py, "-m", "typed.hub", "--data", f"{pre}/data", "--bind", "127.0.0.1",
           "--port", str(cfg["port"]), "--delegation-dir", f"{pre}/delegation"]
    for c in colls(cfg):
        cmd += ["--coll", f"{coll_kind(cfg, c)}:{c}"]
    if kind == "room" and cfg.get("voice"):
        cmd += ["--voice"]
    if kind == "drop":
        cmd += ["--max-file-mb", str(cfg.get("max_file_mb", 25)),
                "--quota-mb", str(cfg.get("quota_mb", 0)),
                "--total-mb", str(cfg.get("total_mb", DEFAULT_TOTAL_MB))]
    if cfg.get("pow_bits") is not None:
        cmd += ["--pow-bits", str(int(cfg["pow_bits"]))]
    for n, w in cfg.get("rate") or []:
        cmd += ["--rate", f"{int(n)}/{int(w)}"]
    for a in cfg.get("admins", []):
        cmd += ["--admin", a]
    return cmd


def sd_quote(arg: str) -> str:
    """One ExecStart argument, quoted the way systemd reads it: plain safe tokens as they are,
    anything else in double quotes with \\, \" escaped, and % and $ doubled (no expansion)."""
    arg = str(arg)
    if re.fullmatch(r"[A-Za-z0-9_@+=:,./-]+", arg):
        return arg
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in arg):
        die(f"argument with a control character: {arg!r}")
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def unit_text(cfg):
    pre = check_prefix(cfg["prefix"])
    check_user(cfg["user"])
    return f"""# generated by satchel-host {VERSION}; do not edit by hand (re-run satchel-host)
[Unit]
Description=Satchel {cfg['type']} host '{cfg['name']}' (loopback only)
After=network.target

[Service]
User={cfg['user']}
WorkingDirectory={pre}/app
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={' '.join(sd_quote(x) for x in exec_cmd(cfg))}
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
MemoryDenyWriteExecute=true
SystemCallArchitectures=native
CapabilityBoundingSet=
AmbientCapabilities=
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
IPAddressDeny=any
IPAddressAllow=localhost
ReadWritePaths={pre}/data
ReadOnlyPaths={pre}/delegation {pre}/app

[Install]
WantedBy=multi-user.target
"""


# ---- commands ----------------------------------------------------------------------------
def cmd_init(a):
    if a.type not in TYPES:
        die(f"--type must be one of {', '.join(TYPES)}")
    if not NAME_RE.fullmatch(a.name) or not a.name[0].isalpha():
        die("--name: starts with a letter; lowercase letters, digits, dashes; max 32")
    extra = [r for r in (a.room or []) if r != a.name]
    if extra and a.type != "rooms":
        die("--room is for rooms hosts")
    for r in extra:
        if not NAME_RE.fullmatch(r) or not r[0].isalpha():
            die(f"--room {r!r}: starts with a letter; lowercase letters, digits, dashes; max 32")
    app_colls = sorted(set(a.apps or []))
    if app_colls and TYPES[a.type][0] != "hub":
        die("--apps is for hub hosts (rooms, wiki, files, people, directory, apps)")
    for r in app_colls:
        if not NAME_RE.fullmatch(r) or not r[0].isalpha() or r == a.name or r in extra:
            die(f"--apps {r!r}: a new collection name (starts with a letter; lowercase "
                "letters, digits, dashes; max 32)")
    if a.voice and a.type != "rooms":
        die("--voice is for rooms hosts")
    prefix = check_prefix(a.prefix or f"/opt/satchel-host/{a.name}")
    user = check_user(a.user or (SERVICE_USER if is_root() else pwd.getpwuid(os.getuid()).pw_name))
    if a.user and is_root() and a.user != SERVICE_USER:
        try:
            pwd.getpwnam(a.user)
        except KeyError:
            die(f"--user {a.user}: no such user (create it first, or leave --user out)")
    if a.pow_bits is not None and not 8 <= a.pow_bits <= 28:
        die("--pow-bits must be between 8 and 28")
    if a.rate and TYPES[a.type][0] != "hub":
        die("--rate is for hub hosts (rooms, wiki, files, people, directory, apps)")
    if a.pow_bits is not None and a.type == "mail":
        die("--pow-bits is not used by mail hosts")
    if os.path.exists(os.path.join(prefix, "host.json")):
        die(f"{prefix} already has a host (use status / renew / uninstall)")
    port = a.port or DEFAULT_PORT[a.type]
    if not (1024 <= port <= 65535):
        die("--port must be 1024..65535")
    import socket
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            die(f"127.0.0.1:{port} is already in use; pick another --port")
    for d in ("app", "data", "delegation", "backups"):
        os.makedirs(os.path.join(prefix, d), exist_ok=True)
    for d in APP_DIRS:
        dst = os.path.join(prefix, "app", d)
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(os.path.join(PACK_ROOT, d), dst,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    cfg = {"v": 1, "type": a.type, "name": a.name, "prefix": prefix, "port": port,
           "user": user, "root_pin": "", "admins": a.admin,
           "installed": int(time.time()), "pack": VERSION, "systemd": False}
    if extra:
        cfg["collections"] = [a.name] + sorted(set(extra))
    if app_colls:
        cfg["app_colls"] = app_colls
    if a.voice:
        cfg["voice"] = True
    if a.type == "mail":
        cfg["closed"], cfg["max_inboxes"] = bool(a.closed), a.max_inboxes
    if a.type == "files":
        cfg["max_file_mb"], cfg["quota_mb"], cfg["total_mb"] = check_mb(
            a.max_file_mb, a.quota_mb, a.total_mb)
    if a.pow_bits is not None:
        cfg["pow_bits"] = a.pow_bits
    if a.rate:
        cfg["rate"] = a.rate
    os.chmod(os.path.join(prefix, "data"), 0o700)
    from typed.host import load_host_key
    load_host_key(os.path.join(prefix, "data"))
    save_cfg(cfg)
    if is_root():
        ensure_service_user(user)
        chown_tree(os.path.join(prefix, "data"), user)
    mode, path = choose_sesh_mode(a)
    sname = sesh_setup(cfg, mode, path)
    save_cfg(cfg)
    req = write_request(cfg)
    print(f"Host '{a.name}' ({a.type}) prepared in {prefix}")
    if len(colls(cfg)) > 1:
        print(f"  collections: {', '.join(colls(cfg))}")
    if sname:
        print(f"  .sesh    : {sname} (key stored mode 600; switch it on with sesh/ENABLE.txt)")
    print(f"  host key : {F.pub_hex(host_key(cfg))}")
    print(f"  listens  : 127.0.0.1:{port} (loopback only)")
    print(f"\nNext: copy {req} to your OFFLINE root machine and run:")
    print(f"  satchel-host root sign {os.path.basename(req)} --days 90")
    print("then bring delegation.json back and run:")
    print(f"  satchel-host install-delegation delegation.json --prefix {prefix}")


def ensure_service_user(user):
    """As root: make the service user (and a group of the same name) if it doesn't exist."""
    try:
        pwd.getpwnam(user)
        return
    except KeyError:
        pass
    if user != SERVICE_USER:
        die(f"user {user} doesn't exist")
    try:
        grp.getgrnam(user)
        group = ["--gid", user]  # the group is already there: join it
    except KeyError:
        group = ["--user-group"]  # create it with the user
    sh("useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", *group, user)


def chown_tree(path, user):
    """Give path and everything under it to user and the user's PRIMARY group (no group of
    the user's name is assumed)."""
    pw = pwd.getpwnam(user)
    for root_, dirs, files in os.walk(path):
        os.chown(root_, pw.pw_uid, pw.pw_gid)
        for n in files:
            os.chown(os.path.join(root_, n), pw.pw_uid, pw.pw_gid, follow_symlinks=False)


def cmd_root_init(a):
    os.makedirs(a.dir, exist_ok=True)
    p = os.path.join(a.dir, "root.key")
    if os.path.exists(p):
        die(f"{p} exists; refusing to overwrite a root key")
    seed = os.urandom(32)
    write_private(p, seed)
    pin = F.pub_hex(F.key_from_seed(seed))
    write_private(os.path.join(a.dir, "root.pin"), (pin + "\n").encode(), 0o644)
    print(f"Root key created: {p} (keep this machine offline; back the key up on paper/USB)")
    print(f"Root pin (public, give to users): {pin}")


def cmd_root_sign(a):
    with open(a.root_key, "rb") as f:
        root = F.key_from_seed(f.read())
    with open(a.request) as f:
        req = json.load(f)
    print(f"Request: type={req.get('type')} collections={req.get('collections')}")
    print(f"         host key={req.get('host')}")
    if not a.yes:
        if input(f"Sign for {a.days} days? Check the host key with the host operator. [y/N] ") \
                .strip().lower() != "y":
            die("not signed")
    bundle = sign_request(root, req, a.days)
    write_private(a.out, jdump(bundle), 0o644)
    print(f"Wrote {a.out} (valid {a.days} days). Copy it back to the host.")


def install_unit(cfg):
    up = f"/etc/systemd/system/{unit_name(cfg)}.service"
    write_private(up, unit_text(cfg).encode(), 0o644)
    sh("systemctl", "daemon-reload")
    sh("systemctl", "enable", "--now", unit_name(cfg))
    sh("systemctl", "restart", unit_name(cfg))


def cmd_install_delegation(a):
    cfg = load_cfg(a.prefix)
    with open(a.bundle) as f:
        bundle = json.load(f)
    root = check_bundle(cfg, bundle, a.allow_root_change)
    # only this host's own collections are written; any other key in the bundle is ignored
    # (names come from host.json and are checked, never from the bundle)
    for coll in colls(cfg):
        write_private(os.path.join(cfg["prefix"], "delegation", f"{coll}.json"),
                      jdump(bundle["delegations"][coll]), 0o644)
    extra = sorted(str(k) for k in (bundle.get("delegations") or {}) if k not in colls(cfg))
    if extra:
        print(f"note: ignored delegations for collections this host doesn't serve: "
              f"{', '.join(repr(k) for k in extra)}")
    cfg["root_pin"] = root
    write_private(os.path.join(cfg["prefix"], "unit.service"), unit_text(cfg).encode(), 0o644)
    if a.no_systemd or not (is_root() and have_systemd()):
        cfg["systemd"] = False
        save_cfg(cfg)
        print(f"Delegation installed. systemd not used; start in foreground with:\n"
              f"  satchel-host run --prefix {cfg['prefix']}")
    else:
        cfg["systemd"] = True
        save_cfg(cfg)
        install_unit(cfg)
        print(f"Delegation installed; service {unit_name(cfg)} started.")
    print_connect(cfg)


def links(cfg, host=None):
    """srnet:// invite links (collections) or mail host URL, for the .sesh name if set."""
    sn = (cfg.get("sesh") or {}).get("name")
    host = host or sn or f"127.0.0.1:{cfg['port']}"
    if cfg["type"] == "mail":
        # the srnet:// link is what directories list ("Add mail host" verifies the root-signed
        # delegation in /v1/mail/info); the URL is for clients that take a plain mail host address
        url = f"http://{host}"
        return ([f"srnet://{host}/mail/{cfg['name']}#root={cfg['root_pin']}", url]
                if cfg.get("root_pin") else [url])
    if not cfg.get("root_pin"):
        return []
    return [f"srnet://{host}/{coll_kind(cfg, c)}/{c}#root={cfg['root_pin']}" for c in colls(cfg)]


def print_connect(cfg):
    sn = (cfg.get("sesh") or {}).get("name")
    print("\nConnect info:")
    print(f"  type     : {cfg['type']}   collections: {', '.join(colls(cfg))}")
    print(f"  root pin : {cfg['root_pin']}")
    print(f"  local    : http://127.0.0.1:{cfg['port']} (loopback)")
    print(f"  .sesh    : {sn if sn else 'none yet (satchel-host sesh --prefix ... --sesh new)'}")
    for ln in links(cfg):
        label = "mail host URL" if ln.startswith("http") else "invite link"
        print(f"  {label}: {ln}")
    if not sn:
        print("  (links use the loopback address until the host has a .sesh name)")


def cmd_link(a):
    cfg = load_cfg(a.prefix)
    for ln in links(cfg):
        print(ln)


def cmd_mail(a):
    cfg = load_cfg(a.prefix)
    if cfg["type"] != "mail":
        die("not a mail host")
    if a.closed or a.open:
        cfg["closed"] = bool(a.closed)
    if a.max_inboxes is not None:
        if a.max_inboxes < 0:
            die("--max-inboxes must be >= 0")
        cfg["max_inboxes"] = a.max_inboxes
    save_cfg(cfg)
    write_private(os.path.join(cfg["prefix"], "unit.service"), unit_text(cfg).encode(), 0o644)
    if cfg.get("systemd") and is_root() and have_systemd():
        install_unit(cfg)
    print(f"mail host '{cfg['name']}': sign-up {'CLOSED' if cfg.get('closed') else 'open'}, "
          f"cap {cfg.get('max_inboxes', 1000)} inboxes"
          + ("" if cfg.get("systemd") else " (restart `satchel-host run` to apply)"))


DEFAULT_QUOTA_MB = 250
DEFAULT_TOTAL_MB = 2048


def check_mb(max_file_mb, quota_mb, total_mb=DEFAULT_TOTAL_MB):
    if not (0 < max_file_mb <= 256):
        die("--max-file-mb must be more than 0 and at most 256")
    if quota_mb < 0:
        die("--quota-mb must be >= 0 (0 = no per-uploader quota)")
    if not total_mb > 0:
        die("--total-mb must be more than 0 (the total cap is always on)")
    return max_file_mb, quota_mb, total_mb


def cmd_files(a):
    cfg = load_cfg(a.prefix)
    if cfg["type"] != "files":
        die("not a files host")
    cfg["max_file_mb"], cfg["quota_mb"], cfg["total_mb"] = check_mb(
        cfg.get("max_file_mb", 25) if a.max_file_mb is None else a.max_file_mb,
        cfg.get("quota_mb", 0) if a.quota_mb is None else a.quota_mb,
        cfg.get("total_mb", DEFAULT_TOTAL_MB) if a.total_mb is None else a.total_mb)
    save_cfg(cfg)
    write_private(os.path.join(cfg["prefix"], "unit.service"), unit_text(cfg).encode(), 0o644)
    if cfg.get("systemd") and is_root() and have_systemd():
        install_unit(cfg)
    print(f"files host '{cfg['name']}': max file {cfg['max_file_mb']} MiB, quota "
          f"{cfg['quota_mb'] or 'none'}{' MiB per uploader' if cfg['quota_mb'] else ''}, "
          f"{cfg['total_mb']} MiB in all"
          + ("" if cfg.get("systemd") else " (restart `satchel-host run` to apply)"))


def _coll_host(cfg, coll):
    """The Host object for one of this host's collections (signs with the host key)."""
    from typed.host import Host
    if cfg["type"] in ("mail",):
        die("mail hosts hold only sealed mail; there is nothing to moderate")
    names = [cfg["name"]] if cfg["type"] == "board" else colls(cfg)
    coll = coll or cfg["name"]
    if coll not in names:
        die(f"--coll must be one of {', '.join(names)}")
    d = os.path.join(cfg["prefix"], "data") if cfg["type"] == "board" \
        else os.path.join(cfg["prefix"], "data", coll)
    if not os.path.exists(os.path.join(d, "log.sqlite3")):
        die(f"no data for {coll} yet (start the host first)")
    h = Host(d, coll, host_key(cfg), {})
    if coll_kind(cfg, coll) == "app":
        from typed.apps import BundleStore
        h.blobs = BundleStore(os.path.join(d, "blobs"), coll)
    elif os.path.isdir(os.path.join(d, "blobs")):
        from typed.hub import BlobStore
        # only used to delete a removed file; uploads in progress belong to the running host
        h.blobs = BlobStore(os.path.join(d, "blobs"), coll=coll, manage_uploads=False)
    return h, d


def _fix_owner(cfg, d):
    """Run as root: give files we created back to the service user."""
    if is_root() and cfg.get("user") and cfg["user"] != "root":
        try:
            chown_tree(d, cfg["user"])
        except (LookupError, OSError):
            pass


def cmd_moderate(a):
    cfg = load_cfg(a.prefix)
    if a.action in ("remove", "pin", "unpin") and a.target_type != "post":
        die(f"{a.action} takes a post id")
    if a.action == "remove" and not a.yes:
        print(f"This removes post {a.target} (and its file, if any) from this host's database "
              "and storage now; mirrors and Satchel drop their copies when they next sync. "
              "It can't be undone.")
        try:
            ok = input("Type REMOVE to continue: ").strip() == "REMOVE"
        except EOFError:
            ok = False
        if not ok:
            die("cancelled (use --yes to skip the prompt)")
    h, d = _coll_host(cfg, a.coll)
    import mbfmt as F2
    try:
        env = h.moderate(a.action, a.target_type, a.target, a.reason or "")
    except F2.FormatError as e:
        die(str(e))
    _fix_owner(cfg, d)
    print(json.dumps(env))


def cmd_header(a):
    cfg = load_cfg(a.prefix)
    if cfg["type"] not in ("rooms", "board"):
        die("headers are for rooms and board hosts")
    if a.description is None and a.rules is None:
        die("give --description and/or --rules")
    h, d = _coll_host(cfg, a.coll)
    import mbfmt as F2
    try:
        out = h.set_header(a.description, a.rules)
    except F2.FormatError as e:
        die(str(e))
    _fix_owner(cfg, d)
    print(json.dumps(out))


def drop_privileges(cfg):
    """Started as root: switch to the service user (its uid, primary gid and groups) before
    the host runs.  Refuses to go on as root."""
    if not is_root():
        return
    user = check_user(cfg.get("user") or "")
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        die(f"service user {user} doesn't exist (run init as root, or create it)")
    if pw.pw_uid == 0:
        die(f"service user {user} has uid 0; refusing to run the host as root")
    os.initgroups(user, pw.pw_gid)
    os.setgid(pw.pw_gid)
    os.setuid(pw.pw_uid)
    if os.geteuid() == 0 or os.getuid() == 0:
        die("could not drop root privileges")
    os.environ.update(HOME=pw.pw_dir or "/", USER=user, LOGNAME=user)


def cmd_run(a):
    cfg = load_cfg(a.prefix)
    if not cfg.get("root_pin"):
        die("no delegation installed yet")
    cmd = exec_cmd(cfg)
    drop_privileges(cfg)
    os.chdir(os.path.join(cfg["prefix"], "app"))
    os.execv(cmd[0], cmd)


def delegation_left(cfg):
    left = []
    for c in colls(cfg):
        p = os.path.join(cfg["prefix"], "delegation", f"{c}.json")
        if not os.path.exists(p):
            return None
        with open(p) as f:
            left.append(json.load(f)["delegation"]["expires"] - time.time())
    return min(left)


def cmd_status(a):
    cfg = load_cfg(a.prefix)
    out = {"name": cfg["name"], "type": cfg["type"], "collections": colls(cfg),
           "prefix": cfg["prefix"], "pack": cfg.get("pack"),
           "listen": f"127.0.0.1:{cfg['port']}", "root_pin": cfg["root_pin"] or None}
    if cfg["type"] == "mail":
        out["mail_signup"] = "closed" if cfg.get("closed") else "open"
        out["max_inboxes"] = cfg.get("max_inboxes", 1000)
    left = delegation_left(cfg)
    out["sesh"] = (cfg.get("sesh") or {}).get("name")
    out["links"] = links(cfg)
    out["delegation_days_left"] = None if left is None else round(left / 86400, 1)
    if cfg.get("systemd") and have_systemd():
        out["service"] = sh("systemctl", "is-active", unit_name(cfg), check=False).stdout.strip()
    path = {"board": "/v1/info", "mail": "/v1/mail/info"}.get(cfg["type"], f"/c/{cfg['name']}/v1/info")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{cfg['port']}{path}", timeout=3) as r:
            out["http"] = "ok" if r.status == 200 else r.status
    except Exception as e:  # noqa: BLE001
        out["http"] = f"down ({e.__class__.__name__})"
    warn = []
    if left is not None and left < 14 * 86400:
        warn.append("delegation expires within 14 days: run `satchel-host renew`")
    if os.path.exists(os.path.join(cfg["prefix"], "data", "root.key")):
        warn.append("a root.key is on this host; move it offline")
    out["warnings"] = warn
    print(json.dumps(out, indent=2))
    return 0 if out["http"] == "ok" else 1


def cmd_renew(a):
    cfg = load_cfg(a.prefix)
    req = write_request(cfg)
    print(f"Renewal request written: {req}\nSign it on the root machine "
          f"(satchel-host root sign request.json --days 90), then run install-delegation.")


def cmd_backup(a):
    cfg = load_cfg(a.prefix)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = a.out or os.path.join(cfg["prefix"], "backups", f"{cfg['name']}-{ts}.tar.gz")
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fo, tarfile.open(fileobj=fo, mode="w:gz") as t:
        for n in ("host.json", "data", "delegation", "sesh"):
            if os.path.exists(os.path.join(cfg["prefix"], n)):
                t.add(os.path.join(cfg["prefix"], n), arcname=n)
    print(f"Backup: {out} (contains the HOST key and any .sesh key; store it privately). No root key is included.")


def cmd_uninstall(a):
    cfg = load_cfg(a.prefix)
    if cfg.get("systemd") and have_systemd():
        sh("systemctl", "disable", "--now", unit_name(cfg), check=False)
        try:
            os.remove(f"/etc/systemd/system/{unit_name(cfg)}.service")
        except FileNotFoundError:
            pass
        sh("systemctl", "daemon-reload", check=False)
    if a.purge:
        if not a.yes:
            die("--purge deletes keys and data; add --yes to confirm (back up first)")
        shutil.rmtree(cfg["prefix"])
        print(f"Removed service and {cfg['prefix']}")
    else:
        cfg["systemd"] = False
        save_cfg(cfg)
        print(f"Service removed; data kept in {cfg['prefix']} (use --purge --yes to delete)")


# ---- dedicated .sesh identity (stored + configured, never enabled) -----------------------
def sesh_name(pub: bytes) -> str:
    return F.zb32(pub) + ".sesh"


def parse_sesh_key(raw: bytes):
    """Accept a 32-byte seed or 64-byte seed||pub as hex text or raw bytes, or a bencoded
    `d1:s64:<seed||pub>e` identity.  Returns the Ed25519 key; raises ValueError otherwise."""
    from cryptography.hazmat.primitives import serialization as ser
    seed = pub = None
    txt = raw.strip()
    if txt.startswith(b"d1:s64:") and txt.endswith(b"e") and len(txt) == 72:
        seed, pub = txt[7:39], txt[39:71]
    elif re.fullmatch(rb"[0-9a-fA-F]{64}|[0-9a-fA-F]{128}", txt):
        b = bytes.fromhex(txt.decode())
        seed, pub = b[:32], (b[32:] or None)
    elif len(raw) in (32, 64):
        seed, pub = raw[:32], (raw[32:] or None)
    else:
        raise ValueError("unrecognised .sesh key file (want 32/64-byte hex, raw, or d1:s64:...e)")
    key = F.key_from_seed(seed)
    real = key.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)
    if pub is not None and pub != real:
        raise ValueError("key file is inconsistent: stored public half does not match seed")
    return key, real


def sesh_setup(cfg, mode: str, import_path: str | None = None):
    """mode: new | import | skip.  Writes <prefix>/sesh/{identity.key,router.ini,ENABLE.txt}."""
    if mode == "skip":
        cfg["sesh"] = None
        return None
    d = os.path.join(cfg["prefix"], "sesh")
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    kp = os.path.join(d, "identity.key")
    if os.path.exists(kp):
        die(f"{kp} already exists; refusing to overwrite a .sesh key")
    if mode == "import":
        with open(import_path, "rb") as f:
            raw = f.read()
        try:
            _, pub = parse_sesh_key(raw)
        except ValueError as e:
            die(str(e))
        write_private(kp, raw)  # copied byte-for-byte, mode 600
    else:
        seed = os.urandom(32)
        _, pub = parse_sesh_key(seed)
        write_private(kp, (seed + pub).hex().encode() + b"\n")
    name = sesh_name(pub)
    ini = f"""; Dedicated Session Router instance for Satchel host '{cfg['name']}' (start it yourself when ready).
; Template generated by satchel-host {VERSION}; adjust to your Session Router version.
[router]
data-dir={d}/router-data
[network]
keyfile={kp}
[bind]
; keep the instance's own private TUN range; do not reuse another instance's
[service]
; map the .sesh name to the loopback-only host
80=127.0.0.1:{cfg['port']}
"""
    write_private(os.path.join(d, "router.ini"), ini.encode(), 0o600)
    enable = f"""To expose '{cfg['name']}' as {name} (manual, deliberate step):
  1. Install Session Router if it is not installed.
  2. Start a SEPARATE instance with this config (do not edit an existing instance):
       session-router {d}/router.ini
     or create a systemd unit 'satchel-sesh-{cfg['name']}' running that command.
  3. Check the instance reports the name {name}.
  4. Share the links from `satchel-host link --prefix {cfg['prefix']}` (http://{name} serves the host).
To undo: stop that instance. The host itself never listens beyond 127.0.0.1.
"""
    write_private(os.path.join(d, "ENABLE.txt"), enable.encode(), 0o644)
    cfg["sesh"] = {"name": name, "keyfile": kp, "config": os.path.join(d, "router.ini"),
                   "source": mode, "enabled": False}
    return name


def choose_sesh_mode(a):
    if a.sesh_import:
        return "import", a.sesh_import
    if a.sesh:
        return a.sesh, None
    if sys.stdin.isatty():
        while True:
            ans = input(".sesh name for this host: [n]ew key, [i]mport key file, [s]kip (default s): ") \
                .strip().lower() or "s"
            if ans in ("n", "new"):
                return "new", None
            if ans in ("s", "skip"):
                return "skip", None
            if ans in ("i", "import"):
                return "import", input("Path to .sesh key file: ").strip()
    return "skip", None


def cmd_sesh(a):
    cfg = load_cfg(a.prefix)
    mode, path = choose_sesh_mode(a)
    name = sesh_setup(cfg, mode, path)
    save_cfg(cfg)
    if name:
        print(f".sesh name: {name}  (stored; switch it on with {cfg['prefix']}/sesh/ENABLE.txt)")


SESH_HELP = """Opening a host over Session Router (.sesh): a deliberate step you take yourself.

Your host only listens on 127.0.0.1. To let others reach it:
 1. Run a SEPARATE Session Router instance with its own key, so this host gets its own
    .sesh name (do not reuse a name that already serves other things).
 2. Point that instance's port 80 at 127.0.0.1:<port> of this host (the generated
    <prefix>/sesh/router.ini does this). The host itself never listens beyond loopback.
 3. Share the links from `satchel-host link --prefix ...`: each carries the .sesh name, the
    collection and your root pin.
Anyone who knows the name can then reach the host, so review your rules and rate limits first.
"""


def _ver(v):
    try:
        return tuple(int(x) for x in str(v).split(".")[:3])
    except ValueError:
        return (0,)


def cmd_upgrade(a):
    """Swap in the app code from THIS (newer) pack. Keys, data, delegations and .sesh stay."""
    cfg = load_cfg(a.prefix)
    old = cfg.get("pack", "0")
    if _ver(VERSION) < _ver(old) and not a.force:
        die(f"this pack is {VERSION}, the host runs {old}: refusing to downgrade (--force)")
    app = os.path.join(cfg["prefix"], "app")
    new = app + ".new"
    shutil.rmtree(new, ignore_errors=True)
    os.makedirs(new)
    for d in APP_DIRS:
        shutil.copytree(os.path.join(PACK_ROOT, d), os.path.join(new, d),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    prev = app + ".prev"
    shutil.rmtree(prev, ignore_errors=True)
    os.replace(app, prev)
    os.replace(new, app)
    cfg["pack"] = VERSION
    save_cfg(cfg)
    write_private(os.path.join(cfg["prefix"], "unit.service"), unit_text(cfg).encode(), 0o644)
    if cfg.get("systemd") and is_root() and have_systemd():
        install_unit(cfg)
        print(f"Upgraded {cfg['name']} {old} -> {VERSION}; service restarted.")
    else:
        print(f"Upgraded {cfg['name']} {old} -> {VERSION}. Restart `satchel-host run --prefix "
              f"{cfg['prefix']}` to use it.")
    print(f"Previous code kept in {prev}. Keys, data and delegations are unchanged.")


def cmd_root_directory_entry(a):
    """Offline: sign a directory listing for one of YOUR collections with its root key."""
    from typed import joins
    from typed.record import KIND_TYPES, make_record
    with open(a.root_key, "rb") as f:
        key = F.key_from_seed(f.read()[:32])
    try:
        ln = joins.parse(a.link)
    except joins.LinkError as e:
        die(f"--link: {e}")
    if not ln["kind"] or ln["root"] != F.pub_hex(key):
        die("the link must name a collection and carry #root= of THIS root key")
    if not NAME_RE.fullmatch(a.directory_name):
        die("--directory-name: the directory collection's name")
    env = make_record(key, KIND_TYPES["directory"], a.directory_name, "directory.entry",
                      {"link": a.link, "title": a.title[:80], "desc": a.desc}, [],
                      pow_bits=a.pow_bits)
    write_private(a.out, jdump(env), 0o644)
    print(f"Wrote {a.out}: '{a.title}' for directory '{a.directory_name}'. Carry it to any "
          "online machine and run `satchel-host directory-submit`.")


def _directory_titles(to):
    """link -> newest title of the root-signed entries a directory serves (best effort)."""
    from typed.sync import http_json
    from typed import dirboard as DB
    out, since = {}, 0
    for _ in range(200):
        code, r = http_json(f"{to}/v1/log?since={since}&limit=500")
        rows = (r or {}).get("entries") or [] if code == 200 else []
        for e in rows:
            rec = (e.get("env") or {}).get("rec") or {}
            b = rec.get("body") or {}
            p = DB.parse_link(b.get("link"))
            if rec.get("t") == "directory.entry" and p and p[3] == rec.get("author"):
                out[b["link"]] = b.get("title")
            since = max(since, e.get("seq") or 0)
        if len(rows) < 500:
            break
    return out


def cmd_directory_submit(a):
    from typed.sync import http_json
    with open(a.entry) as f:
        env = json.load(f)
    to = a.to.rstrip("/")
    body = ((env.get("rec") or {}).get("body") or {}) if isinstance(env, dict) else {}
    link, title = body.get("link"), body.get("title")
    try:
        titles = _directory_titles(to)
    except (OSError, ValueError) as e:
        titles = None
        print(f"note: couldn't read the directory's listings to check titles ({e})")
    if titles is not None:
        old = titles.get(link)
        if old is not None and old != title and not a.replace:
            die(f"this directory already lists {link} as '{old}'. This entry would rename it to "
                f"'{title}'. Add --replace if that is what you want.")
        same = sorted(l for l, t in titles.items() if l != link and (t or "").lower() == (title or "").lower())
        if same:
            print(f"warning: another listing already uses the title '{title}': {', '.join(same)}")
    code, r = http_json(to + "/v1/submit", env)
    if code == 202 and r.get("pending"):
        print(f"Received; waiting for approval (id {r.get('id')}). New listings show once the "
              "directory's admin approves them. After that, title and description changes "
              "from the same root key go straight in.")
        return
    if not (code == 200 and r.get("ok")):
        die(f"directory refused the entry ({code}): {r.get('error')}")
    print(f"Listed: {r}")


def _deleg_dir(cfg):
    return os.path.join(cfg["prefix"], "delegation")


def refresh_unit_name(cfg):
    return f"satchel-dirrefresh-{cfg['name']}"


def refresh_unit_text(cfg):
    """Hourly refresh: a oneshot service plus its timer (needs the network, unlike the host)."""
    pre = check_prefix(cfg["prefix"])
    check_user(cfg["user"])
    r = cfg["directory_refresh"]
    py = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable
    cmd = [py, "-m", "typed.dirboard", "--data", f"{pre}/data", "refresh", "--to", r["to"],
           "--deleg-dir", f"{pre}/delegation"]
    for ln in r["links"]:
        cmd += ["--link", ln]
    env = f"Environment=http_proxy={sd_quote(r['proxy'])}\n" if r.get("proxy") else ""
    svc = f"""# generated by satchel-host {VERSION}; do not edit by hand (re-run satchel-host)
[Unit]
Description=Satchel directory refresh for '{cfg['name']}'
After=network-online.target

[Service]
Type=oneshot
User={cfg['user']}
WorkingDirectory={pre}/app
Environment=PYTHONDONTWRITEBYTECODE=1
{env}ExecStart={' '.join(sd_quote(x) for x in cmd)}
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ReadOnlyPaths={pre}
"""
    timer = f"""# generated by satchel-host {VERSION}; do not edit by hand (re-run satchel-host)
[Unit]
Description=Hourly Satchel directory refresh for '{cfg['name']}'

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
RandomizedDelaySec=5min
Persistent=true

[Install]
WantedBy=timers.target
"""
    return svc, timer


def cmd_directory_refresh(a):
    """Tell a directory this host's listed collections are still running (signed with the
    host key; the delegation in it chains to the root pin in the link)."""
    cfg = load_cfg(a.prefix)
    from typed import dirboard as DB
    if a.timer == "off":
        cfg.pop("directory_refresh", None)
        save_cfg(cfg)
        if is_root() and have_systemd():
            sh("systemctl", "disable", "--now", refresh_unit_name(cfg) + ".timer", check=False)
            for ext in (".service", ".timer"):
                try:
                    os.remove(f"/etc/systemd/system/{refresh_unit_name(cfg)}{ext}")
                except FileNotFoundError:
                    pass
            sh("systemctl", "daemon-reload", check=False)
        print("Hourly refresh off. The listing shows as inactive 24 hours after its last refresh.")
        return
    saved = cfg.get("directory_refresh") or {}
    to = (a.to or saved.get("to") or "").rstrip("/")
    if not to:
        die("--to: the directory URL, e.g. http://<directory>.sesh/c/directory")
    lks = a.link or saved.get("links") or [l for l in links(cfg) if l.startswith("srnet://")]
    if not lks:
        die("no links to refresh (install a delegation first, or give --link)")
    mine = set(l for l in links(cfg))
    for l in lks:
        p = DB.parse_link(l)
        if not p or p[3] != cfg.get("root_pin") or p[2] not in colls(cfg):
            die(f"--link {l}: not one of this host's collections (see `satchel-host link`)")
        if l not in mine:
            print(f"note: {l} uses a different address than `satchel-host link` prints")
    if a.timer == "on":
        cfg["directory_refresh"] = {"to": to, "links": lks, "proxy": a.proxy or saved.get("proxy") or ""}
        save_cfg(cfg)
        svc, timer = refresh_unit_text(cfg)
        write_private(os.path.join(cfg["prefix"], "dirrefresh.service"), svc.encode(), 0o644)
        write_private(os.path.join(cfg["prefix"], "dirrefresh.timer"), timer.encode(), 0o644)
        if is_root() and have_systemd():
            write_private(f"/etc/systemd/system/{refresh_unit_name(cfg)}.service", svc.encode(), 0o644)
            write_private(f"/etc/systemd/system/{refresh_unit_name(cfg)}.timer", timer.encode(), 0o644)
            sh("systemctl", "daemon-reload")
            sh("systemctl", "enable", "--now", refresh_unit_name(cfg) + ".timer")
            print(f"Hourly refresh on ({refresh_unit_name(cfg)}.timer).")
        else:
            print(f"Wrote {cfg['prefix']}/dirrefresh.service and .timer. Without systemd, run "
                  f"this command from cron once an hour instead.")
    with open(os.path.join(cfg["prefix"], "data", "host.key"), "rb") as f:
        key = F.key_from_seed(f.read()[:32])
    if a.proxy:
        os.environ["http_proxy"] = a.proxy
    res = DB.refresh(key, _deleg_dir(cfg), lks, to)
    for link, ok, msg in res:
        print(f"{'ok' if ok else 'no'} {link}: {msg}")
    if not all(ok for _, ok, _ in res) and a.timer != "on":
        sys.exit(1)


def cmd_directory_review(a):
    """The review queue of this host's directory collection."""
    cfg = load_cfg(a.prefix)
    if cfg["type"] != "directory":
        die("this host isn't a directory (init --type directory)")
    from typed import dirboard as DB
    from typed.host import Host, load_host_key
    coll = a.coll or cfg["name"]
    if coll not in colls(cfg):
        die(f"--coll must be one of {', '.join(colls(cfg))}")
    d = os.path.join(cfg["prefix"], "data", coll)
    argv = ["--data", os.path.join(cfg["prefix"], "data"), "--coll", coll, a.dc]
    if a.target:
        argv.append(a.target)
    if a.reason:
        argv += ["--reason", a.reason]
    if a.dc == "approve" and not a.yes:
        print(f"Approving makes {a.target} public in this directory; later entries for the same "
              "link from the same root key go in without review.")
        try:
            ok = input("Type APPROVE to continue: ").strip() == "APPROVE"
        except EOFError:
            ok = False
        if not ok:
            die("cancelled (use --yes to skip the prompt)")
    DB.main(argv, host_cls=Host, load_key=load_host_key)
    _fix_owner(cfg, d)


# ---- apps (docs/APP-API.md) -------------------------------------------------------------
BUNDLE_KIND = "satchel-app-bundle/v1"


def cmd_root_app_bundle(a):
    """Offline: sign an app.bundle record for a JS app with the root key."""
    from typed import apps as AP
    from typed.record import KIND_TYPES, make_record
    with open(a.root_key, "rb") as f:
        key = F.key_from_seed(f.read()[:32])
    with open(a.source, "rb") as f:
        src = f.read()
    name = a.name or os.path.splitext(os.path.basename(a.source))[0]
    try:
        AP.check_source(src)
        if not AP.NAME_RE.fullmatch(name):
            raise F.FormatError("--name: starts with a letter; lowercase letters, digits, dashes; max 32")
        if not NAME_RE.fullmatch(a.coll):
            raise F.FormatError("--coll: the app collection's name")
        body = AP.bundle_body(name, a.title, a.ver, src, a.caps, a.desc)
        env = make_record(key, KIND_TYPES["app"], a.coll, "app.bundle", body, [],
                          pow_bits=a.pow_bits)
    except F.FormatError as e:
        die(str(e))
    out = a.out or f"{name}-{a.ver}.bundle.json"
    write_private(out, jdump({"kind": BUNDLE_KIND, "coll": a.coll, "record": env,
                              "source": src.decode("utf-8")}), 0o644)
    print(f"Wrote {out}: {name} {a.ver} for app collection '{a.coll}' "
          f"(sha256 {body['sha256']}, {len(src)} bytes).")
    print("Carry it to the host and run `satchel-host app publish` within 10 minutes "
          "(hosts refuse records with an old timestamp; sign again if it is late).")


def _app_coll(cfg, coll):
    names = [c for c in colls(cfg) if coll_kind(cfg, c) == "app"]
    if not names:
        die("this host has no app collection (init --type apps, or --apps NAME)")
    coll = coll or names[0]
    if coll not in names:
        die(f"--coll must be one of {', '.join(names)}")
    return coll


def _post_json(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except ValueError:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except OSError as e:
        return {"ok": False, "error": f"host not reachable ({e.__class__.__name__}); is it running?"}


def cmd_app_publish(a):
    """Host: add the app's source to the app collection, then post its signed record."""
    import hashlib
    from typed import apps as AP  # noqa: F401  (registers the app kind)
    from typed.record import codec_for
    cfg = load_cfg(a.prefix)
    if not cfg.get("root_pin"):
        die("no delegation installed yet")
    with open(a.bundle) as f:
        b = json.load(f)
    if b.get("kind") != BUNDLE_KIND:
        die("not a bundle file from `satchel-host root app-bundle`")
    coll = _app_coll(cfg, a.coll or b.get("coll"))
    env, src = b["record"], b["source"].encode("utf-8")
    try:
        meta = codec_for("app").verify(env, coll)
    except (F.FormatError, KeyError, TypeError) as e:
        die(f"the bundle record doesn't verify for collection '{coll}': {e}")
    if meta.t != "app.bundle":
        die("not an app.bundle record")
    if meta.author != cfg["root_pin"]:
        die("the bundle is signed by a different key than this host's root")
    if hashlib.sha256(src).hexdigest() != meta.body["sha256"] or len(src) != meta.body["size"]:
        die("the source doesn't match the hash and size in the record")
    h, d = _coll_host(cfg, coll)
    h.blobs.add(src)
    _fix_owner(cfg, d)
    r = _post_json(f"http://127.0.0.1:{cfg['port']}/c/{coll}/v1/submit", env)
    if not r.get("ok"):
        die(f"the host refused the record: {r.get('error')}")
    print(f"Published {meta.body['name']} {meta.body['ver']} in '{coll}' "
          f"(sha256 {meta.body['sha256']}, record {r.get('id')}).")
    print(f"Point a collection at it with: satchel-host app use --prefix {cfg['prefix']} "
          f"--coll <collection> --app {coll}:{meta.body['name']}")


def app_bundles(cfg, coll):
    """Latest root-signed bundle per app name in one of this host's app collections."""
    import sqlite3
    p = os.path.join(cfg["prefix"], "data", coll, "log.sqlite3")
    if not os.path.exists(p):
        return {}
    db = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT seq, id, env FROM log WHERE t='app.bundle' AND author=? "
                          "AND tomb IS NULL ORDER BY seq", (cfg["root_pin"],)).fetchall()
    finally:
        db.close()
    out = {}
    for seq, rid, env in rows:
        body = json.loads(env)["rec"]["body"]
        out[body["name"]] = {**body, "id": rid, "seq": seq}
    return out


def cmd_app_list(a):
    cfg = load_cfg(a.prefix)
    out = {}
    for c in colls(cfg):
        if coll_kind(cfg, c) == "app":
            out[c] = {n: {k: b[k] for k in ("title", "ver", "sha256", "size", "caps")}
                      for n, b in app_bundles(cfg, c).items()}
    pol = {}
    for c in colls(cfg):
        p = os.path.join(cfg["prefix"], "data", c, "apps.json")
        if os.path.exists(p):
            with open(p) as f:
                pol[c] = json.load(f)
    print(json.dumps({"bundles": out, "pointers": pol}, indent=2))


def cmd_app_use(a):
    """Host: name the apps a collection opens with (signed into its policy)."""
    cfg = load_cfg(a.prefix)
    if cfg["type"] in ("board", "mail"):
        die("apps are for hub hosts")
    if not cfg.get("root_pin"):
        die("no delegation installed yet")
    target = a.coll or cfg["name"]
    if target not in colls(cfg):
        die(f"--coll must be one of {', '.join(colls(cfg))}")
    pointers = []
    if not a.clear:
        for spec in a.app:
            m = re.fullmatch(r"([a-z0-9][a-z0-9-]{0,31}):([a-z][a-z0-9-]{0,31})(?:@([0-9a-f]{64}))?", spec)
            if not m:
                die(f"--app {spec!r}: want COLLECTION:NAME or COLLECTION:NAME@SHA256")
            ac, name, sha = m.groups()
            ac = _app_coll(cfg, ac)
            if not sha:
                b = app_bundles(cfg, ac).get(name)
                if not b:
                    die(f"no published bundle '{name}' in '{ac}' (run `satchel-host app publish`)")
                sha = b["sha256"]
            pointers.append({"coll": ac, "name": name, "sha256": sha})
    names = {p["name"] for p in pointers}
    if a.default and not a.clear:
        own = set(app_bundles(cfg, target)) if coll_kind(cfg, target) == "app" else set()
        if a.default not in names | own:
            die(f"--default {a.default!r} isn't one of the apps named with --app"
                + (" or published in this collection" if own else ""))
    h, d = _coll_host(cfg, target)
    try:
        out = h.set_apps(pointers, None if a.clear else a.default)
    except F.FormatError as e:
        die(str(e))
    _fix_owner(cfg, d)
    print(json.dumps(out))
    print("The running host signs this into its next checkpoint; "
          "Satchel shows the apps after its next sync.")


def cmd_app_add_collection(a):
    """Host: add an app collection to an existing hub host (needs a new delegation)."""
    cfg = load_cfg(a.prefix)
    if TYPES[cfg["type"]][0] != "hub":
        die("app collections go on hub hosts")
    if not NAME_RE.fullmatch(a.name) or not a.name[0].isalpha() or a.name in colls(cfg):
        die(f"--name {a.name!r}: a new collection name")
    cfg["app_colls"] = sorted(set(cfg.get("app_colls") or []) | {a.name})
    save_cfg(cfg)
    write_private(os.path.join(cfg["prefix"], "unit.service"), unit_text(cfg).encode(), 0o644)
    req = write_request(cfg)
    print(f"Added app collection '{a.name}'. It needs a delegation too: sign {req} on the root "
          "machine (satchel-host root sign request.json), then run install-delegation.")


def parse_rate(v):
    from typed.hub import parse_rate as pr
    return pr(v)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="satchel-host", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=VERSION)
    sp = ap.add_subparsers(dest="cmd", required=True)

    def pre(p):
        p.add_argument("--prefix", required=True, help="install dir, e.g. /opt/satchel-host/<name>")

    def sesh_args(p):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--sesh", choices=["new", "skip"], help="make a new .sesh key, or skip")
        g.add_argument("--sesh-import", metavar="FILE", help="import an existing .sesh key file")

    p = sp.add_parser("init", help="prepare a host and write a delegation request")
    p.add_argument("--type", required=True, choices=list(TYPES))
    p.add_argument("--name", required=True)
    p.add_argument("--prefix")
    p.add_argument("--port", type=int)
    p.add_argument("--user", help=f"service user (default {SERVICE_USER} when root)")
    p.add_argument("--admin", action="append", default=[], help="admin public key (hub types)")
    p.add_argument("--apps", action="append", default=[], metavar="NAME",
                   help="hub types: also serve an app collection NAME (repeatable)")
    p.add_argument("--voice", action="store_true", help="rooms: take voice clips (kept 7 days)")
    p.add_argument("--closed", action="store_true", help="mail: no new inboxes (default: open)")
    p.add_argument("--max-inboxes", type=int, default=1000, help="mail: inbox cap (default 1000)")
    p.add_argument("--room", action="append", default=[], help="rooms: another room on this host (repeatable)")
    p.add_argument("--max-file-mb", type=float, default=25, help="files: largest file in MiB (default 25)")
    p.add_argument("--quota-mb", type=float, default=DEFAULT_QUOTA_MB,
                   help=f"files: MiB each uploader may store (default {DEFAULT_QUOTA_MB}, 0 = no per-uploader quota)")
    p.add_argument("--total-mb", type=float, default=DEFAULT_TOTAL_MB,
                   help=f"files: MiB all files may take together (default {DEFAULT_TOTAL_MB}; always on)")
    p.add_argument("--pow-bits", type=int,
                   help="proof of work per post (new keys need 2 bits more); default per type")
    p.add_argument("--rate", action="append", type=parse_rate, default=[], metavar="N/SECONDS",
                   help="hub types: writes per collection from all keys together, e.g. 600/3600 "
                        "(repeatable; default per type)")
    sesh_args(p)
    p.set_defaults(fn=cmd_init)
    p = sp.add_parser("mail", help="mail host: open or close new-inbox sign-up, change the cap")
    pre(p)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--closed", action="store_true", help="stop new inboxes (existing ones keep working)")
    g.add_argument("--open", action="store_true", help="allow new inboxes again")
    p.add_argument("--max-inboxes", type=int)
    p.set_defaults(fn=cmd_mail)
    p = sp.add_parser("files", help="files host: change the file size limit or uploader quota")
    pre(p)
    p.add_argument("--max-file-mb", type=float)
    p.add_argument("--quota-mb", type=float)
    p.add_argument("--total-mb", type=float)
    p.set_defaults(fn=cmd_files)
    p = sp.add_parser("moderate", help="hide, unhide, remove, pin or unpin (signed, public)")
    pre(p)
    p.add_argument("action", choices=["hide", "unhide", "remove", "pin", "unpin"])
    p.add_argument("target_type", choices=["post", "author"])
    p.add_argument("target", help="post id or author key")
    p.add_argument("--reason", default="")
    p.add_argument("--coll", help="collection (default: the host's name; e.g. another room)")
    p.add_argument("--yes", action="store_true", help="remove: skip the confirmation")
    p.set_defaults(fn=cmd_moderate)
    p = sp.add_parser("header", help="set the room description and rules shown at the top")
    pre(p)
    p.add_argument("--description")
    p.add_argument("--rules")
    p.add_argument("--coll")
    p.set_defaults(fn=cmd_header)
    p = sp.add_parser("sesh", help="make or import a dedicated .sesh key later")
    pre(p)
    sesh_args(p)
    p.set_defaults(fn=cmd_sesh)

    r = sp.add_parser("root", help="offline root machine commands").add_subparsers(dest="rc",
                                                                                   required=True)
    p = r.add_parser("init", help="create an offline root key")
    p.add_argument("--dir", default="./satchel-root")
    p.set_defaults(fn=cmd_root_init)
    p = r.add_parser("sign", help="sign a host's request.json")
    p.add_argument("request")
    p.add_argument("--root-key", default="./satchel-root/root.key")
    p.add_argument("--days", type=int, default=MAX_DAYS)
    p.add_argument("--out", default="delegation.json")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_root_sign)
    p = r.add_parser("directory-entry", help="sign a directory listing for your collection")
    p.add_argument("--link", required=True, help="srnet:// link of YOUR collection (from `satchel-host link`)")
    p.add_argument("--title", required=True)
    p.add_argument("--desc")
    p.add_argument("--directory-name", default="directory", help="name of the directory collection")
    p.add_argument("--root-key", default="./satchel-root/root.key")
    p.add_argument("--pow-bits", type=int, default=16, help="proof-of-work bits (default 16)")
    p.add_argument("--out", default="directory-entry.json")
    p.set_defaults(fn=cmd_root_directory_entry)
    p = r.add_parser("app-bundle", help="sign an app (a .js file) for an app collection")
    p.add_argument("source", help="the app's JavaScript file")
    p.add_argument("--name", help="app name (default: the file name)")
    p.add_argument("--title", required=True, help="what Satchel shows, up to 60 characters")
    p.add_argument("--ver", required=True, help="version, e.g. 1.0")
    p.add_argument("--caps", default="read", choices=["read", "read,draft"],
                   help="read, or read,draft if the app proposes posts (default read)")
    p.add_argument("--desc", help="one or two sentences, up to 300 characters")
    p.add_argument("--coll", default="apps", help="the app collection's name (default apps)")
    p.add_argument("--root-key", default="./satchel-root/root.key")
    p.add_argument("--pow-bits", type=int, default=16, help="proof-of-work bits (default 16)")
    p.add_argument("--out", help="default: <name>-<ver>.bundle.json")
    p.set_defaults(fn=cmd_root_app_bundle)
    ap_ = sp.add_parser("app", help="publish apps and choose which apps a collection opens with") \
        .add_subparsers(dest="ac", required=True)
    p = ap_.add_parser("publish", help="publish a signed bundle file on this host")
    p.add_argument("bundle")
    pre(p)
    p.add_argument("--coll", help="app collection (default: the one in the bundle file)")
    p.set_defaults(fn=cmd_app_publish)
    p = ap_.add_parser("use", help="name the apps a collection opens with")
    pre(p)
    p.add_argument("--coll", help="the collection that gets the apps (default: the host's name)")
    p.add_argument("--app", action="append", default=[], metavar="COLL:NAME[@SHA256]",
                   help="an app to name (repeatable, at most 8; default: latest published)")
    p.add_argument("--default", help="the app Satchel opens first")
    p.add_argument("--clear", action="store_true", help="remove the apps and the default")
    p.set_defaults(fn=cmd_app_use)
    p = ap_.add_parser("list", help="published bundles and each collection's apps")
    pre(p)
    p.set_defaults(fn=cmd_app_list)
    p = ap_.add_parser("add-collection", help="add an app collection to this host")
    pre(p)
    p.add_argument("--name", required=True)
    p.set_defaults(fn=cmd_app_add_collection)
    p = sp.add_parser("directory-submit", help="post a signed directory entry to a directory")
    p.add_argument("entry")
    p.add_argument("--to", required=True, help="directory URL, e.g. http://<name>.sesh/c/directory")
    p.add_argument("--replace", action="store_true", help="allow renaming a link already listed")
    p.set_defaults(fn=cmd_directory_submit)
    p = sp.add_parser("directory-refresh", help="tell a directory this host's listings are running")
    pre(p)
    p.add_argument("--to", help="directory URL (saved by --timer on)")
    p.add_argument("--link", action="append", default=[], help="a listed link (default: all of this host's)")
    p.add_argument("--timer", choices=["on", "off"], help="install or remove the hourly refresh timer")
    p.add_argument("--proxy", help="HTTP proxy that reaches .sesh names, e.g. http://127.0.0.1:8080")
    p.set_defaults(fn=cmd_directory_refresh)
    dr = sp.add_parser("directory", help="review queue of a directory host") \
        .add_subparsers(dest="dc", required=True)
    for n, h in (("pending", "listings waiting for approval (only you see them)"),
                 ("approved", "links that no longer need review"),
                 ("approve", "make a pending listing public (id or link)"),
                 ("reject", "drop a pending listing (id or link)"),
                 ("unapprove", "a link's next entry waits for review again")):
        p = dr.add_parser(n, help=h)
        pre(p)
        p.add_argument("--coll")
        if n in ("approve", "reject", "unapprove"):
            p.add_argument("target")
        else:
            p.set_defaults(target=None)
        p.add_argument("--reason", default="")
        p.add_argument("--yes", action="store_true")
        p.set_defaults(fn=cmd_directory_review)
    p = sp.add_parser("upgrade", help="move a host to this pack's code (keeps keys and data)")
    pre(p)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_upgrade)
    p = sp.add_parser("link", help="print invite links (or the mail host URL)")
    pre(p)
    p.set_defaults(fn=cmd_link)

    p = sp.add_parser("install-delegation", help="install a signed delegation and start")
    p.add_argument("bundle")
    pre(p)
    p.add_argument("--no-systemd", action="store_true")
    p.add_argument("--allow-root-change", action="store_true")
    p.set_defaults(fn=cmd_install_delegation)
    for n, fn, h in (("status", cmd_status, "show health"), ("renew", cmd_renew, "new request"),
                     ("run", cmd_run, "run in foreground (no systemd)")):
        p = sp.add_parser(n, help=h)
        pre(p)
        p.set_defaults(fn=fn)
    p = sp.add_parser("backup", help="tar.gz of data + delegation")
    pre(p)
    p.add_argument("--out")
    p.set_defaults(fn=cmd_backup)
    p = sp.add_parser("uninstall", help="remove service (keeps data unless --purge)")
    pre(p)
    p.add_argument("--purge", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_uninstall)
    p = sp.add_parser("sesh-help", help="how to expose over Session Router (manual)")
    p.set_defaults(fn=lambda a: print(SESH_HELP))
    a = ap.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
