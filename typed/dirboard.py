"""Community board for a directory collection: a review queue for new listings, and refreshes
that show a listed host is still running.

Review queue
  A directory.entry for a link this directory hasn't approved yet is checked like any post
  (signature, time, proof of work, rate limits), then held in a private queue instead of the
  public log. Nothing held is served to anyone. An admin approves it (it is appended to the log
  as signed, and the link counts as approved) or rejects it (it is dropped). Later entries for
  an approved link (a new title or description, signed by the same root key) go straight in.
  A new link always waits for approval again. Links listed before the queue existed count as
  approved, and so do links signed by the directory's own root key (the operator listing
  their own collections).

Refreshes (directory.alive)
  A listed host signs {link, deleg} with its online host key, where deleg is the delegation
  the root key in the link signed for that host key and that collection. Anyone can check the
  chain: record signature -> host key -> delegation signed by the link's root. Refreshes are
  not log entries: the directory keeps only the newest one per link and serves them at
  GET /v1/alive. Older Satchel builds never see them, so their sync is unchanged. A listing
  with no entry or refresh in the last 24 hours shows as inactive; its next refresh brings it
  back. Hosts post one an hour (satchel-host directory-refresh, run by a timer).

Admin (on the directory's machine; works while the hub runs, the queue is in its database):
  python3 -m typed.dirboard --data DIR --coll directory pending
  python3 -m typed.dirboard --data DIR --coll directory approve <id or link>
  python3 -m typed.dirboard --data DIR --coll directory reject <id or link> --reason "..."
  python3 -m typed.dirboard --data DIR --coll directory unapprove <link>
  python3 -m typed.dirboard --data DIR --coll directory approved
(satchel-host has the same as `satchel-host directory pending|approve|reject|...`.)

This file is self-contained (mbfmt and typed.record only) so other hosts can vendor it.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import threading
import time

import mbfmt as F
from typed import record as R

ALIVE_TYPE = "directory.alive"
ENTRY_TYPE = "directory.entry"
ACTIVE_S = 86400            # a listing with nothing newer than this shows as inactive
REFRESH_S = 3600            # how often hosts refresh
ALIVE_RATE = [(12, 3600)]   # refreshes per listed link
MAX_PENDING = 200           # whole queue
MAX_PENDING_PER_KEY = 5     # per signing key
MAX_ALIVE_SERVED = 2000
LINK_PAT = r"srnet://[a-z0-9.:\[\]]{1,80}/[a-z]{2,12}/[a-z][a-z0-9_-]{0,31}#root=[0-9a-f]{64}"
LINK_RE = re.compile(r"^srnet://([a-z0-9.:\[\]]{1,80})/([a-z]{2,12})/([a-z][a-z0-9_-]{0,31})#root=([0-9a-f]{64})$")
DELEG_KEYS = {"v", "board", "root", "host", "issued", "expires", "policy"}


def _deleg_shape():
    def chk(v):
        if not isinstance(v, dict) or set(v) != {"delegation", "sig"} or not isinstance(v["sig"], str) \
                or not isinstance(v["delegation"], dict) or not set(v["delegation"]) <= DELEG_KEYS:
            raise F.FormatError("deleg: a delegation envelope {delegation, sig}")
    return chk


DIRECTORY_ALIVE = R.TypeSpec("directory.alive", 1, {"link": R.pattern(LINK_PAT, what="link"),
                                                    "deleg": _deleg_shape()}, 0)
ALIVE_TYPES = R.TypeSet(DIRECTORY_ALIVE)
ALIVE_CODEC = R.TypedCodec(ALIVE_TYPES)


def parse_link(link: str):
    """srnet://host/kind/name#root=hex -> (host, kind, name, root), or None."""
    m = LINK_RE.match(link or "") if isinstance(link, str) else None
    return m.groups() if m else None


def _link_problem(link):
    """What a client's link parser says about link (clients drop listings it refuses)."""
    try:
        from typed import joins
    except ImportError:
        return None
    try:
        joins.parse(link)
    except joins.LinkError as e:
        return str(e)
    return None


def _rec(env):
    r = env.get("rec") if isinstance(env, dict) else None
    return r if isinstance(r, dict) else {}


def make_alive(host_key, dir_coll: str, link: str, deleg: dict, ts: int | None = None) -> dict:
    """A refresh for `link`, signed by the listed collection's host key."""
    return R.make_record(host_key, ALIVE_TYPES, dir_coll, ALIVE_TYPE, {"link": link, "deleg": deleg}, ts=ts)


def check_alive(env, dir_coll: str, now: float | None = None, check_time: bool = False):
    """Verify a refresh: record signature, then the delegation from the link's root to the
    signing key, for the link's collection, valid when the refresh was signed.
    -> (link, ts, host_pub). Raises F.FormatError."""
    meta = ALIVE_CODEC.verify(env, dir_coll, now=now, check_time=check_time)
    p = parse_link(meta.body["link"])
    if not p:
        raise F.FormatError("link")
    _host, _kind, name, root = p
    host_pub = F.verify_delegation(meta.body["deleg"], root, name, now=meta.ts)
    if host_pub != meta.author:
        raise F.FormatError("refresh not signed by the host key the link's root delegated")
    return meta.body["link"], meta.ts, host_pub


def liveness(entries_ts: dict, alive_ts: dict, now: float) -> dict:
    """link -> (active_ts, inactive) from the newest entry ts and the newest refresh ts."""
    out = {}
    for link, ts in entries_ts.items():
        a = max(ts, alive_ts.get(link, 0))
        out[link] = (a, now - a > ACTIVE_S)
    return out


class Board:
    def __init__(self, host):
        self.h = host
        self.db = host.store.db
        self.lock = host.store.lock
        self.rl_alive = _Rate(ALIVE_RATE)
        self.db.execute("CREATE TABLE IF NOT EXISTS dir_meta(k TEXT PRIMARY KEY, v TEXT)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS dir_pending(id TEXT PRIMARY KEY, link TEXT NOT NULL,
            author TEXT NOT NULL, got INTEGER NOT NULL, env TEXT NOT NULL)""")
        self.db.execute("CREATE TABLE IF NOT EXISTS dir_approved(link TEXT PRIMARY KEY, ts INTEGER NOT NULL, how TEXT)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS dir_rejected(id TEXT PRIMARY KEY, link TEXT NOT NULL,
            author TEXT NOT NULL, ts INTEGER NOT NULL, reason TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS dir_alive(link TEXT PRIMARY KEY, author TEXT NOT NULL,
            ts INTEGER NOT NULL, env TEXT NOT NULL)""")
        self._seed_approved()

    def _now(self):
        return int(self.h.clock())

    def _seed_approved(self):
        """Links already listed (root-signed, live) when the queue first runs count as approved."""
        with self.lock:
            if self.db.execute("SELECT 1 FROM dir_meta WHERE k='seeded'").fetchone():
                return
            for author, env in self._live_entries():
                link = _rec(env).get("body", {}).get("link")
                p = parse_link(link)
                if p and p[3] == author:
                    self.db.execute("INSERT OR IGNORE INTO dir_approved VALUES(?,?,?)", (link, self._now(), "listed before review"))
            self.db.execute("INSERT OR REPLACE INTO dir_meta VALUES('seeded', ?)", (str(self._now()),))

    def own_root(self):
        """The root key that delegated this directory (its operator), or None."""
        d = self.h.delegation if isinstance(self.h.delegation, dict) else {}
        r = (d.get("delegation") or {}).get("root") if isinstance(d.get("delegation"), dict) else None
        return r if isinstance(r, str) and len(r) == 64 else None

    # ---- queue ----
    def approved(self, link: str) -> bool:
        return self.db.execute("SELECT 1 FROM dir_approved WHERE link=?", (link,)).fetchone() is not None

    def listed(self, link: str) -> bool:
        """A live root-signed entry for link is in the public log."""
        root = (parse_link(link) or (None,) * 4)[3]
        return any(a == root and _rec(e).get("body", {}).get("link") == link for a, e in self._live_entries())

    def _live_entries(self):
        """(author, envelope) of live directory entries in the public log, oldest first."""
        return [(a, json.loads(e)) for a, e in self.db.execute(
            "SELECT author, env FROM log WHERE t=? AND tomb IS NULL ORDER BY seq", (ENTRY_TYPE,))]

    def submit(self, env, orig):
        """Host.submit for a directory: refreshes, held new links, everything else as before."""
        t = _rec(env).get("t")
        if t == ALIVE_TYPE:
            return self.submit_alive(env)
        if t != ENTRY_TYPE:
            return orig(env)
        h = self.h
        try:
            author = h.codec.verify(env, h.board).author
            meta = h.codec.verify(env, h.board, now=h.clock(), pow_bits=h.pow_for(author), check_time=True)
            link = meta.body["link"]
            p = parse_link(link)
            if not p or p[3] != meta.author:
                return 400, {"ok": False, "error": "a listing must be signed by the root key in its link"}
            bad = _link_problem(link)
            if bad:
                return 400, {"ok": False, "error": f"link: {bad}"}
            if h.rules:
                err = h.rules(h, meta)
                if err:
                    return 400, {"ok": False, "error": err}
        except (F.FormatError, KeyError, TypeError, AttributeError) as e:
            return 400, {"ok": False, "error": str(e)}
        if not self.approved(link) and meta.author == self.own_root():
            with self.lock:
                self.db.execute("INSERT OR IGNORE INTO dir_approved VALUES(?,?,?)",
                                (link, self._now(), "the directory's own root key"))
        if self.approved(link):
            return orig(env)
        with self.lock:
            if self.db.execute("SELECT 1 FROM dir_pending WHERE id=?", (meta.id,)).fetchone():
                return 202, {"ok": True, "id": meta.id, "pending": True, "dup": True}
            n_all = self.db.execute("SELECT COUNT(*) FROM dir_pending").fetchone()[0]
            n_key = self.db.execute("SELECT COUNT(*) FROM dir_pending WHERE author=?", (meta.author,)).fetchone()[0]
            if n_all >= MAX_PENDING or n_key >= MAX_PENDING_PER_KEY:
                return 429, {"ok": False, "error": "the review queue is full; try again later"}
            rl_all = getattr(h, "rl_all", None)
            if not h.rl.allow(meta.author) or (rl_all is not None and not rl_all.allow()):
                return 429, {"ok": False, "error": "rate limited"}
            self.db.execute("INSERT INTO dir_pending VALUES(?,?,?,?,?)",
                            (meta.id, link, meta.author, self._now(), json.dumps(h.codec.normalize(env))))
        return 202, {"ok": True, "id": meta.id, "pending": True,
                     "note": "new listing: it shows once this directory's admin approves it"}

    def pending(self) -> list:
        out = []
        for i, link, author, got, env in self.db.execute(
                "SELECT id, link, author, got, env FROM dir_pending ORDER BY got"):
            b = _rec(json.loads(env)).get("body", {})
            out.append({"id": i, "link": link, "title": b.get("title"), "desc": b.get("desc") or "",
                        "author": author, "received": got})
        return out

    def _pick(self, which: str) -> list:
        rows = self.db.execute("SELECT id, link, author, got, env FROM dir_pending WHERE id=? OR link=? ORDER BY got",
                               (which, which)).fetchall()
        if not rows:
            raise F.FormatError(f"nothing pending for {which!r}")
        return rows

    def approve(self, which: str) -> dict:
        """Append the held entry (all held entries, for a link) to the public log; approve the link."""
        h, seqs = self.h, []
        with self.lock:
            rows = self._pick(which)
            for i, link, _author, _got, env_s in rows:
                env = json.loads(env_s)
                meta = h.codec.verify(env, h.board)  # signature and shape again; time was checked on arrival
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    if not h.store.has(meta.id):
                        seqs.append(h.store.append(env, meta))
                    self.db.execute("INSERT OR REPLACE INTO dir_approved VALUES(?,?,?)", (link, self._now(), "approved"))
                    self.db.execute("DELETE FROM dir_pending WHERE id=?", (i,))
                    self.db.execute("COMMIT")
                except BaseException:
                    self.db.execute("ROLLBACK")
                    raise
        kick = getattr(h, "_kick", None)
        if kick is not None:
            kick.set()
        return {"ok": True, "approved": sorted({r[1] for r in rows}), "seq": seqs}

    def reject(self, which: str, reason: str = "") -> dict:
        with self.lock:
            rows = self._pick(which)
            for i, link, author, _got, _env in rows:
                self.db.execute("INSERT OR REPLACE INTO dir_rejected VALUES(?,?,?,?,?)",
                                (i, link, author, self._now(), reason[:200]))
                self.db.execute("DELETE FROM dir_pending WHERE id=?", (i,))
        return {"ok": True, "rejected": [r[0] for r in rows]}

    def unapprove(self, link: str) -> dict:
        """Later entries for link wait for review again, and its refreshes are refused. The
        public entry stays until an admin removes it (typed.admin remove post <id>)."""
        with self.lock:
            n = self.db.execute("DELETE FROM dir_approved WHERE link=?", (link,)).rowcount
            self.db.execute("DELETE FROM dir_alive WHERE link=?", (link,))
        return {"ok": True, "unapproved": bool(n)}

    def approved_links(self) -> list:
        return [{"link": l, "since": ts, "how": how} for l, ts, how in
                self.db.execute("SELECT link, ts, how FROM dir_approved ORDER BY ts")]

    # ---- refreshes ----
    def submit_alive(self, env):
        try:
            link, ts, host_pub = check_alive(env, self.h.board, now=self.h.clock(), check_time=True)
        except (F.FormatError, KeyError, TypeError, AttributeError) as e:
            return 400, {"ok": False, "error": str(e)}
        if not self.approved(link):
            return 403, {"ok": False, "error": "this listing is waiting for the directory's approval (or isn't listed here)"}
        if not self.listed(link):
            return 404, {"ok": False, "error": "no listing for this link here"}
        if not self.rl_alive.allow(link):
            return 429, {"ok": False, "error": "rate limited"}
        with self.lock:
            row = self.db.execute("SELECT ts FROM dir_alive WHERE link=?", (link,)).fetchone()
            if row and row[0] >= ts:
                return 200, {"ok": True, "ts": row[0], "dup": True}
            self.db.execute("INSERT OR REPLACE INTO dir_alive VALUES(?,?,?,?)",
                            (link, host_pub, ts, json.dumps(ALIVE_CODEC.normalize(env))))
        return 200, {"ok": True, "ts": ts}

    def alive_list(self) -> dict:
        envs = [json.loads(e) for (e,) in self.db.execute(
            "SELECT env FROM dir_alive ORDER BY ts DESC LIMIT ?", (MAX_ALIVE_SERVED,))]
        return {"alive": envs, "active_s": ACTIVE_S, "refresh_s": REFRESH_S}

    def alive_ts(self) -> dict:
        return {l: ts for l, ts in self.db.execute("SELECT link, ts FROM dir_alive")}


class _Rate:
    def __init__(self, rules):
        self.rules, self.hits, self.lock = rules, {}, threading.Lock()

    def allow(self, key):
        now = time.monotonic()
        with self.lock:
            q = self.hits.setdefault(key, collections.deque())
            horizon = max(w for _, w in self.rules)
            while q and q[0] <= now - horizon:
                q.popleft()
            if any(sum(1 for t in q if t > now - w) >= n for n, w in self.rules):
                return False
            q.append(now)
            return True


def attach(host) -> Board:
    """Give a directory collection's Host the review queue and refreshes (idempotent)."""
    if getattr(host, "dirboard", None):
        return host.dirboard
    b = Board(host)
    orig = host.submit
    host.submit = lambda env: b.submit(env, orig)
    host.alive_list = b.alive_list
    host.dirboard = b
    return b


def dir_coll_of(url: str) -> str:
    """Directory URL http://<host>/c/<name> -> <name> (a bare host serves 'directory')."""
    from urllib.parse import urlparse
    parts = [x for x in urlparse(url).path.split("/") if x]
    return parts[1] if len(parts) >= 2 and parts[0] == "c" else "directory"


def refresh(host_key, deleg_dir, links: list, to: str, post=None, ts=None) -> list:
    """Sign and post one refresh per link. deleg_dir: a directory of <collection>.json
    delegations, or a function collection name -> delegation. -> [(link, ok, message)]."""
    if post is None:
        from typed.sync import http_json as post
    to, coll, out = to.rstrip("/"), dir_coll_of(to), []
    for link in links:
        p = parse_link(link)
        if not p:
            out.append((link, False, "not a collection link"))
            continue
        try:
            if callable(deleg_dir):
                deleg = deleg_dir(p[2])
            else:
                with open(os.path.join(deleg_dir, p[2] + ".json")) as f:
                    deleg = json.load(f)
            env = make_alive(host_key, coll, link, deleg, ts=ts)
            code, r = post(to + "/v1/submit", env)
        except (OSError, ValueError, KeyError, F.FormatError) as e:
            out.append((link, False, str(e)))
            continue
        if code == 200 and r.get("ok"):
            out.append((link, True, "refreshed"))
        elif code == 403:
            out.append((link, False, "waiting for the directory's approval"))
        elif code == 404:
            out.append((link, False, "not listed in this directory (post an entry first)"))
        else:
            out.append((link, False, f"refused ({code}): {r.get('error')}"))
    return out


def main(argv=None, host_cls=None, load_key=None):
    ap = argparse.ArgumentParser(description="Review queue of a directory collection, and refreshes.")
    ap.add_argument("--data", required=True, help="the hub's --data directory (refresh: the listed host's)")
    ap.add_argument("--coll", default="directory")
    ap.add_argument("action", choices=["pending", "approve", "reject", "unapprove", "approved", "refresh"])
    ap.add_argument("target", nargs="?", help="a pending id, or a link")
    ap.add_argument("--reason", default="")
    ap.add_argument("--to", help="refresh: the directory URL, e.g. http://<name>.sesh/c/directory")
    ap.add_argument("--link", action="append", default=[], help="refresh: a listed link (repeatable)")
    ap.add_argument("--deleg-dir", help="refresh: where <collection>.json delegations are")
    a = ap.parse_args(argv)
    if a.action == "refresh":
        if not (a.to and a.link and a.deleg_dir):
            ap.error("refresh needs --to, --link and --deleg-dir")
        kp = os.path.join(a.data, "host.key")
        if not os.path.exists(kp):
            raise SystemExit(f"no host key at {kp}")
        with open(kp, "rb") as f:
            key = F.key_from_seed(f.read()[:32])
        res = refresh(key, a.deleg_dir, a.link, a.to)
        for link, ok, msg in res:
            print(f"{'ok' if ok else 'no'} {link}: {msg}")
        return 0 if all(ok for _, ok, _ in res) else 1
    if a.action in ("approve", "reject", "unapprove") and not a.target:
        ap.error(f"{a.action} needs a pending id or a link")
    if host_cls is None or load_key is None:
        from typed.host import Host as host_cls, load_host_key as load_key  # noqa: N813
    d = os.path.join(a.data, a.coll)
    if not os.path.exists(os.path.join(d, "log.sqlite3")):
        raise SystemExit(f"no collection {a.coll!r} under {a.data}")
    h = host_cls(d, a.coll, load_key(a.data), {}, codec=R.codec_for("directory"))
    b = attach(h)
    try:
        r = {"pending": lambda: {"pending": b.pending()},
             "approved": lambda: {"approved": b.approved_links()},
             "approve": lambda: b.approve(a.target),
             "reject": lambda: b.reject(a.target, a.reason),
             "unapprove": lambda: b.unapprove(a.target)}[a.action]()
    except F.FormatError as e:
        raise SystemExit(str(e))
    print(json.dumps(r, indent=1))


if __name__ == "__main__":
    sys.exit(main())
