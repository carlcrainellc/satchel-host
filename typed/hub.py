"""Hub: one service hosting many typed collections (rooms, wiki, drop, profiles, apps),
each with its own log, checkpoints, delegation (signed by the hub's collection root key)
and policy.  Routes /c/<collection>/v1/... ; GET /v1/collections lists them.

Run:  python3 -m typed.hub --data DIR --port 48810 \\
          --coll room:lobby --coll room:dev --coll wiki:wiki \\
          --coll drop:files --coll id:people
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

import mbfmt as F
from .host import Host, load_policy
from .node import serve, url_of
from .record import codec_for
from .views import host_rules

NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")  # always fullmatch
UPLOAD_TTL_S = 3600  # a started upload that gets no chunk for this long is thrown away
DEFAULT_MAX_FILE_MB = 25
DEFAULT_QUOTA_MB = 250  # per uploader key, per drop
DEFAULT_TOTAL_MB = 2048  # all files in one drop together, finished and in progress
KIND_POLICY = {  # lighter limits for rooms; heavier for wiki
    # rate: per signing key.  global_rate: the whole collection, every key together (keys are
    # free to make, so this is what caps a flood).  New keys pay 16 bits of proof of work.
    "room": {"rate": [[60, 600], [600, 86400]], "global_rate": [[600, 600], [6000, 86400]],
             "pow_bits": 12, "new_key_pow_bits": 16},
    # wiki pages and app bundles are kept: retention would cut page histories and apps
    "wiki": {"rate": [[20, 3600]], "global_rate": [[200, 3600], [1000, 86400]],
             "pow_bits": 14, "new_key_pow_bits": 16, "retention_days": 0},
    "drop": {"rate": [[10, 3600]], "global_rate": [[100, 3600], [500, 86400]],
             "pow_bits": 14, "new_key_pow_bits": 16,
             "max_file_bytes": DEFAULT_MAX_FILE_MB << 20, "quota_bytes": DEFAULT_QUOTA_MB << 20,
             "total_bytes": DEFAULT_TOTAL_MB << 20},
    "id": {"rate": [[10, 3600]], "global_rate": [[200, 3600], [1000, 86400]],
           "pow_bits": 12, "new_key_pow_bits": 16},
    "directory": {"rate": [[20, 3600]], "global_rate": [[100, 3600], [500, 86400]],
                  "pow_bits": 12, "new_key_pow_bits": 16},
}
from . import apps as APPS  # noqa: E402  (registers the "app" kind)
from . import dirboard as DIRBOARD  # noqa: E402
KIND_POLICY["app"] = dict(APPS.POLICY, retention_days=0)


UPLOAD_DOMAIN = "srnet-typed/upload/v1"
UP_CHUNK = 1 << 20  # largest chunk per request
HEX64 = re.compile(r"[0-9a-f]{64}")


class BlobStore:
    """Content-addressed blobs for file drops: stored and served by SHA-256 only.

    Limits come from the drop's (checkpoint-signed) policy: max_file_bytes caps one file,
    quota_bytes (0 = none) caps the bytes each uploader key may store, and total_bytes caps
    the whole store (always on; 0 or None means the default, DEFAULT_TOTAL_MB).

    Every upload is signed (/v1/upload) and opens a session for that file: the announced size
    is reserved against the key's quota and the total cap at once, so uploads in progress
    count too.  Chunks (/v1/upload/<sha>?off=N[&s=<session>]) are taken only while the
    session is live; a session that gets no chunk for upload_ttl_s is dropped with its
    partial file.  The session token comes back from /v1/upload; when a chunk carries it, it
    must match.  Satchel 0.3.0 doesn't send it yet, so it is optional unless
    require_session is set."""

    def __init__(self, d, max_file_bytes=None, quota_bytes=0, coll="", total_bytes=None,
                 clock=time.time, upload_ttl_s=UPLOAD_TTL_S, require_session=False,
                 manage_uploads=True):
        from .filecrypt import DEFAULT_MAX_FILE, max_cipher_len
        self.d, self.coll, self.clock = d, coll, clock
        self.max_file_bytes = int(max_file_bytes or DEFAULT_MAX_FILE)
        self.max_bytes = max_cipher_len(self.max_file_bytes)
        self.quota_bytes = int(quota_bytes or 0)
        self.total_bytes = int(total_bytes or DEFAULT_TOTAL_MB * 1024 * 1024)
        self.upload_ttl_s, self.require_session = upload_ttl_s, require_session
        self.up = os.path.join(d, ".up")
        os.makedirs(self.up, exist_ok=True)
        self._lock = threading.RLock()
        self._acct_path = os.path.join(d, ".owners.json")
        self._acct = {}
        if os.path.exists(self._acct_path):
            with open(self._acct_path) as f:
                self._acct = json.load(f)
        self._up = {}  # sha -> {"author", "size", "token", "ts"}: uploads in progress
        self._swept = 0.0
        # manage_uploads=False: a second process (the CLI deleting a removed file) leaves .up
        # alone; the running host owns the uploads in progress
        self.manage_uploads = manage_uploads
        if manage_uploads:
            self._load_uploads()

    def _save(self):
        with open(self._acct_path + ".tmp", "w") as f:
            json.dump(self._acct, f)
        os.replace(self._acct_path + ".tmp", self._acct_path)

    # ---- uploads in progress ----
    def _paths(self, sha):
        return os.path.join(self.up, sha + ".json"), os.path.join(self.up, sha + ".part")

    def _load_uploads(self):
        """Pick up sessions left by a restart; anything else in .up is removed."""
        for fn in os.listdir(self.up):
            sha, _, ext = fn.partition(".")
            if ext != "json" or not HEX64.fullmatch(sha):
                continue
            mp, pp = self._paths(sha)
            try:
                with open(mp) as f:
                    m = json.load(f)
                if isinstance(m.get("author"), str) and isinstance(m.get("size"), int) \
                        and os.path.exists(pp):
                    m["token"] = str(m.get("token") or secrets.token_hex(16))
                    m["ts"] = os.path.getmtime(pp)
                    self._up[sha] = m
            except (OSError, ValueError, AttributeError):
                pass
        for fn in os.listdir(self.up):
            if fn.partition(".")[0] not in self._up:
                self._drop_upload(None, path=os.path.join(self.up, fn))
        self.sweep_uploads()

    def _drop_upload(self, sha, path=None):
        self._up.pop(sha, None)
        for p in ([path] if path else self._paths(sha)):
            try:
                os.remove(p)
            except OSError:
                pass

    def sweep_uploads(self, now=None) -> int:
        """Drop uploads that got no chunk for upload_ttl_s.  Returns how many went."""
        now = self.clock() if now is None else now
        with self._lock:
            old = [h for h, m in self._up.items() if now - m["ts"] > self.upload_ttl_s]
            for h in old:
                self._drop_upload(h)
            self._swept = now
            # files removed by another process (satchel-host moderate) stop counting
            gone = [h for h in self._acct if not os.path.exists(os.path.join(self.d, h))]
            for h in gone:
                del self._acct[h]
            if gone:
                self._save()
            return len(old)

    def _maybe_sweep(self, now):
        if now - self._swept > min(60, self.upload_ttl_s):
            self.sweep_uploads(now)

    # ---- accounting: finished files plus reserved space for uploads in progress ----
    def used(self, author, skip=None):
        return (sum(v["size"] for v in self._acct.values() if v["author"] == author)
                + sum(m["size"] for h, m in self._up.items() if m["author"] == author and h != skip))

    def total_used(self, skip=None):
        return (sum(v["size"] for v in self._acct.values())
                + sum(m["size"] for h, m in self._up.items() if h != skip))

    def _room_for(self, who, size, skip=None):
        """None if who may hold size more bytes, else (code, error reply)."""
        if self.quota_bytes and self.used(who, skip) + size > self.quota_bytes:
            return 403, {"ok": False, "error": "upload quota exceeded",
                         "quota_bytes": self.quota_bytes, "used": self.used(who, skip)}
        if self.total_used(skip) + size > self.total_bytes:
            return 507, {"ok": False, "error": "this host's file space is full; try later",
                         "total_bytes": self.total_bytes}
        return None

    def _admit(self, who, sha, now):
        """Extra per-store checks before a new upload opens (None = fine).  Under the lock."""
        return None

    def _commit(self, b: bytes):
        h = hashlib.sha256(b).hexdigest()
        p = os.path.join(self.d, h)
        if not os.path.exists(p):
            with open(p + ".tmp", "wb") as f:
                f.write(b)
            os.replace(p + ".tmp", p)
        return h

    def put(self, b: bytes):
        """The old unsigned single-shot upload.  Always refused: nothing ties the bytes to a
        key, so no quota could hold.  Satchel uses the signed /v1/upload."""
        return 403, {"ok": False, "error": "use the signed /v1/upload"}

    # ---- chunked, resumable ----
    def start(self, env: dict, now: float):
        try:
            r, sig = env["upload"], env["sig"]
            if set(r) != {"v", "coll", "author", "sha256", "size", "ts"} or r["coll"] != self.coll:
                raise ValueError("fields")
            if not HEX64.fullmatch(r["sha256"]) or not isinstance(r["size"], int) \
                    or isinstance(r["size"], bool):
                raise ValueError("sha256/size")
            if not F._verify(r["author"], sig, F.canonical(UPLOAD_DOMAIN, r)):
                raise ValueError("bad signature")
            if abs(now - r["ts"]) > F.CLOCK_SKEW_S:
                raise ValueError("stale timestamp")
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            return 400, {"ok": False, "error": f"bad upload request: {e}"}
        sha, size, who = r["sha256"], r["size"], r["author"]
        if size <= 0 or size > self.max_bytes:
            return 413, {"ok": False, "error": f"file too large for this host (max "
                         f"{self.max_file_bytes} bytes)", "max_file_bytes": self.max_file_bytes}
        with self._lock:
            t = self.clock()
            self._maybe_sweep(t)
            if os.path.exists(os.path.join(self.d, sha)):
                return 200, {"ok": True, "sha256": sha, "have": size, "done": True}
            mp, pp = self._paths(sha)
            m = self._up.get(sha)
            if m and (m["author"] != who or m["size"] != size):
                return 409, {"ok": False, "error": "another upload of this file is in progress"}
            if not m:
                bad = self._room_for(who, size) or self._admit(who, sha, t)
                if bad:
                    return bad
                m = {"author": who, "size": size, "token": secrets.token_hex(16), "ts": t}
                with open(mp + ".tmp", "w") as f:
                    json.dump({k: m[k] for k in ("author", "size", "token")}, f)
                os.replace(mp + ".tmp", mp)
                open(pp, "wb").close()
                self._up[sha] = m
            m["ts"] = t
            have = os.path.getsize(pp)
        return 200, {"ok": True, "sha256": sha, "have": have, "done": False, "chunk": UP_CHUNK,
                     "session": m["token"]}

    def status(self, sha):
        if not HEX64.fullmatch(sha or ""):
            return 400, {"error": "bad hash"}
        if os.path.exists(os.path.join(self.d, sha)):
            return 200, {"sha256": sha, "done": True}
        with self._lock:
            self._maybe_sweep(self.clock())
            if sha not in self._up:
                return 404, {"error": "no such upload"}
            return 200, {"sha256": sha, "have": os.path.getsize(self._paths(sha)[1]),
                         "done": False}

    def _committed(self, sha, m, now):
        self._acct[sha] = {"author": m["author"], "size": m["size"], "ts": int(now)}

    def chunk(self, sha, off: int, data: bytes, session: str | None = None):
        if not HEX64.fullmatch(sha or "") or len(data) > UP_CHUNK:
            return 400, {"ok": False, "error": "bad chunk"}
        with self._lock:
            now = self.clock()
            self._maybe_sweep(now)
            m = self._up.get(sha)
            if m is not None and now - m["ts"] > self.upload_ttl_s:
                self._drop_upload(sha)
                m = None
            if m is None:
                return 404, {"ok": False, "error": "start the upload first"}
            if session is None and self.require_session:
                return 403, {"ok": False, "error": "chunk needs the upload session"}
            if session is not None and not hmac.compare_digest(str(session), m["token"]):
                return 403, {"ok": False, "error": "not this upload's session"}
            mp, pp = self._paths(sha)
            have = os.path.getsize(pp)
            if off != have:  # resume point mismatch: tell the client where to continue
                return 409, {"ok": False, "error": "offset", "have": have}
            if have + len(data) > m["size"]:
                return 413, {"ok": False, "error": "more bytes than announced"}
            bad = self._room_for(m["author"], m["size"], skip=sha)  # limits may have changed
            if bad:
                self._drop_upload(sha)
                return bad
            with open(pp, "ab") as f:
                f.write(data)
            have += len(data)
            m["ts"] = now
            if have < m["size"]:
                return 200, {"ok": True, "have": have, "done": False}
            with open(pp, "rb") as f:
                b = f.read()
            self._drop_upload(sha)
            if hashlib.sha256(b).hexdigest() != sha:
                return 400, {"ok": False, "error": "hash mismatch; upload discarded"}
            self._commit(b)
            self._committed(sha, m, now)
            self._save()
            return 200, {"ok": True, "have": have, "done": True, "sha256": sha}

    def delete(self, h) -> bool:
        if not HEX64.fullmatch(h or ""):
            return False
        p = os.path.join(self.d, h)
        with self._lock:
            if self._acct.pop(h, None):
                self._save()
        if os.path.exists(p):
            os.remove(p)
            return True
        return False

    def get(self, h):
        if not HEX64.fullmatch(h or ""):
            return None
        p = os.path.join(self.d, h)
        if not os.path.exists(p):
            return None
        with open(p, "rb") as f:
            return f.read()


def sign_upload(key, coll, sha, size, ts=None) -> dict:
    r = {"v": 1, "coll": coll, "author": F.pub_hex(key), "sha256": sha, "size": size,
         "ts": int(time.time()) if ts is None else int(ts)}
    return {"upload": r, "sig": key.sign(F.canonical(UPLOAD_DOMAIN, r)).hex()}


class Hub:
    def __init__(self, data_dir, root_key, host_key, colls: list[tuple[str, str]],
                 policy_extra: dict | None = None, clock=time.time, pol_override=None,
                 delegations: dict | None = None, voice: bool = False):
        self.nodes, self.kinds = {}, {}
        if voice:
            from . import voice as V
            V.register()
        for kind, name in colls:
            if not NAME_RE.fullmatch(name) or kind not in KIND_POLICY:
                raise SystemExit(f"bad collection {kind}:{name}")
            if delegations is not None:
                d = delegations[name]
            else:
                d = F.make_delegation(root_key, name, F.pub_hex(host_key),
                                      int(clock()) + F.MAX_DELEGATION_S, issued=int(clock()))
            pol = dict(KIND_POLICY[kind], kind=kind, **(policy_extra or {}))
            pol.update((pol_override or {}).get(kind, {}))
            h = Host(os.path.join(data_dir, name), name, host_key, d, load_policy(None, name, pol),
                     clock=clock, codec=codec_for(kind), rules=host_rules)
            if kind == "drop":
                h.blobs = BlobStore(os.path.join(data_dir, name, "blobs"),
                                    h.policy.get("max_file_bytes"), h.policy.get("quota_bytes"), name,
                                    total_bytes=h.policy.get("total_bytes"))
            elif kind == "directory":  # review queue for new listings, and refreshes
                DIRBOARD.attach(h)
            elif kind == "app":
                h.blobs = APPS.BundleStore(os.path.join(data_dir, name, "blobs"), name)
            self.nodes[name], self.kinds[name] = h, kind
            if kind == "room" and voice:  # Walkie Talkie clips (typed/voice.py)
                V.room_blobs(self, name, os.path.join(data_dir, name, "blobs"))

    def __call__(self, path):
        parts = path.split("/", 3)
        if len(parts) >= 4 and parts[1] == "c" and parts[2] in self.nodes:
            return self.nodes[parts[2]], "/" + parts[3]
        return None, None

    def collections(self):
        return {"collections": [{"name": n, "kind": self.kinds[n]} for n in self.nodes]}


def parse_rate(v: str) -> list[int]:
    """"N/SECONDS" -> [N, SECONDS] (both positive integers)."""
    m = re.fullmatch(r"([0-9]{1,7})/([0-9]{1,8})", v or "")
    if not m or int(m[1]) <= 0 or int(m[2]) <= 0:
        raise argparse.ArgumentTypeError(f"rate must look like 600/3600 (writes/seconds): {v!r}")
    return [int(m[1]), int(m[2])]


def write_overrides(pow_bits=None, rate=None) -> dict:
    """Policy keys that --pow-bits / --rate set on every collection of a hub.
    pow_bits: proof of work for keys that have posted before; new keys need 2 bits more.
    rate: the collection-wide write budget (every key together)."""
    out = {}
    if pow_bits is not None:
        if not 8 <= pow_bits <= 28:
            raise SystemExit("--pow-bits must be between 8 and 28")
        out.update(pow_bits=pow_bits, new_key_pow_bits=pow_bits + 2)
    if rate:
        out["global_rate"] = [list(r) for r in rate]
    return out


def main():
    from .host import load_or_make_keys
    ap = argparse.ArgumentParser(description="typed collection hub")
    ap.add_argument("--data", required=True)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=48810)
    ap.add_argument("--coll", action="append", required=True, help="kind:name")
    ap.add_argument("--admin", action="append", default=[], help="admin key hex")
    ap.add_argument("--max-file-mb", type=float, default=DEFAULT_MAX_FILE_MB,
                    help=f"largest file a drop accepts (MiB, default {DEFAULT_MAX_FILE_MB})")
    ap.add_argument("--quota-mb", type=float, default=DEFAULT_QUOTA_MB,
                    help=f"bytes each uploader key may store per drop (MiB, default "
                         f"{DEFAULT_QUOTA_MB}, 0 = no per-key quota)")
    ap.add_argument("--total-mb", type=float, default=DEFAULT_TOTAL_MB,
                    help=f"all files in one drop together (MiB, default {DEFAULT_TOTAL_MB}; "
                         f"always on)")
    ap.add_argument("--pow-bits", type=int,
                    help="proof of work per post (new keys need 2 bits more); default per kind")
    ap.add_argument("--rate", action="append", type=parse_rate, default=[],
                    help="collection-wide write budget N/SECONDS (all keys together); "
                         "repeatable; default per kind")
    ap.add_argument("--voice", action="store_true",
                    help="rooms take voice clips (room.voice), kept 7 days")
    ap.add_argument("--delegation-dir",
                    help="dir of <collection>.json delegations signed by an offline root")
    ap.add_argument("--tick", type=float, default=30.0,
                    help="seconds between retention runs")
    a = ap.parse_args()
    colls = []
    for c in a.coll:
        kind, _, name = c.partition(":")
        if kind not in KIND_POLICY or not NAME_RE.fullmatch(name):
            raise SystemExit(f"bad collection {c!r}")
        colls.append((kind, name))
    if a.total_mb <= 0:
        raise SystemExit("--total-mb must be more than 0")
    if a.delegation_dir:
        from .host import load_host_key, load_signed_delegation
        hk, root = load_host_key(a.data), None
        dels = {n: load_signed_delegation(os.path.join(a.delegation_dir, n + ".json"), n, hk)
                for _, n in colls}
        pins = {e["delegation"]["root"] for e in dels.values()}
        if len(pins) != 1:
            raise SystemExit("all collections must share one root")
        root_pin = pins.pop()
    else:
        root, hk, _ = load_or_make_keys(a.data, "hub")
        dels, root_pin = None, F.pub_hex(root)
    drop_pol = {"max_file_bytes": int(a.max_file_mb * 1024 * 1024),
                "quota_bytes": int(a.quota_mb * 1024 * 1024),
                "total_bytes": int(a.total_mb * 1024 * 1024)}
    extra = {"admins": a.admin, **write_overrides(a.pow_bits, a.rate)}
    hub = Hub(a.data, root, hk, colls, extra,
              delegations=dels, pol_override={"drop": drop_pol}, voice=a.voice)
    srv = serve(hub, a.bind, a.port)
    for h in hub.nodes.values():  # retention (policy retention_days) and the daily VACUUM
        h.run_ticker(a.tick)
    print(json.dumps({"url": url_of(srv), "root_pin": root_pin,
                      "collections": hub.collections()["collections"]}), flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    main()
