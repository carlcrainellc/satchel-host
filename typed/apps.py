"""App collections for Satchel: the `app` kind, its record types and the app pointers a
collection's signed policy may carry.  Same TypeSpecs and limits as Satchel itself
(docs/APP-API.md); importing this module registers the kind.

  app.bundle@1  one app: name, title, ver, sha256 + size of the JS source, caps, desc.
                Only the collection's root key may publish one; the source is served as a
                blob at /c/<coll>/v1/blob/<sha256>.
  app.data@1    small posts an app makes: {kind, text}.

Pointers live in the policy (and so in every signed checkpoint):
  "apps": [{"coll": "apps", "name": "chat", "sha256": "<hex>"}, ...]   at most 8
  "default_app": "chat"                                                optional
"""
from __future__ import annotations

import hashlib
import re

import mbfmt as F
from . import record as R

APP_MAX_BYTES = 256 * 1024
MAX_POINTERS = 8
NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,31}")
COLL_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
HASH_RE = re.compile(r"[0-9a-f]{64}")
CAPS = ("read", "read,draft")

APP_BUNDLE = R.TypeSpec("app.bundle", 1, {
    "name": R.pattern(NAME_RE.pattern, what="app name"), "title": R.text(60, multiline=False),
    "ver": R.pattern(r"[0-9]{1,6}(\.[0-9]{1,6}){0,2}", what="version"),
    "sha256": R.hexhash(required=True), "size": R.integer(1, APP_MAX_BYTES),
    "entry": R.pattern(r"main", what="entry"), "caps": R.pattern(r"read|read,draft", what="caps"),
    "desc": R.text(300, required=False)}, 1)
APP_DATA = R.TypeSpec("app.data", 1, {
    "kind": R.pattern(r"[a-z][a-z0-9_]{0,23}", what="record kind"), "text": R.text(2000)}, 1)

POLICY = {"rate": [[20, 3600]], "pow_bits": 12, "new_key_pow_bits": 14}

R.KIND_TYPES["app"] = R.TypeSet(APP_BUNDLE, APP_DATA, *R.PROFILE_TYPES)


def bundle_body(name: str, title: str, ver: str, src: bytes, caps: str = "read,draft",
                desc: str | None = None) -> dict:
    b = {"name": name, "title": title, "ver": ver, "sha256": hashlib.sha256(src).hexdigest(),
         "size": len(src), "entry": "main", "caps": caps}
    if desc:
        b["desc"] = desc
    return b


def check_source(src: bytes) -> None:
    """What Satchel checks before it runs a bundle: size and UTF-8."""
    if not 0 < len(src) <= APP_MAX_BYTES:
        raise F.FormatError(f"app source must be 1..{APP_MAX_BYTES} bytes")
    try:
        src.decode("utf-8", errors="strict")
    except UnicodeDecodeError as e:
        raise F.FormatError(f"app source is not valid UTF-8: {e}") from None


def check_pointers(apps, default_app=None) -> None:
    """Validate the policy's app pointers; raises FormatError."""
    if not isinstance(apps, list) or len(apps) > MAX_POINTERS:
        raise F.FormatError(f"apps: a list of at most {MAX_POINTERS} pointers")
    seen = set()
    for p in apps:
        if not isinstance(p, dict) or set(p) != {"coll", "name", "sha256"}:
            raise F.FormatError("each app pointer is {coll, name, sha256}")
        if not (isinstance(p["coll"], str) and COLL_RE.fullmatch(p["coll"])):
            raise F.FormatError(f"bad app collection {p['coll']!r}")
        if not (isinstance(p["name"], str) and NAME_RE.fullmatch(p["name"])):
            raise F.FormatError(f"bad app name {p['name']!r}")
        if not (isinstance(p["sha256"], str) and HASH_RE.fullmatch(p["sha256"])):
            raise F.FormatError("app sha256 must be 64 lowercase hex")
        if p["name"] in seen:
            raise F.FormatError(f"app {p['name']!r} is named twice")
        seen.add(p["name"])
    if default_app is not None and not (isinstance(default_app, str) and NAME_RE.fullmatch(default_app)):
        raise F.FormatError(f"bad default_app {default_app!r}")


def bundle_rule(host, meta) -> str | None:
    """Host-side: only the root key publishes bundles, and the source must already be here."""
    if meta.t != "app.bundle":
        return None
    d = host.delegation or {}
    root = (d.get("delegation") or {}).get("root") if isinstance(d, dict) else None
    if not root or meta.author != root:
        return "only the collection's root key may publish app bundles"
    blobs = getattr(host, "blobs", None)
    src = blobs.get(meta.body["sha256"]) if blobs else None
    if src is None or len(src) != meta.body["size"]:
        return "publish the app source to this host before its bundle record"
    return None


class BundleStore:
    """Read-only blob store for an app collection: serves sources by SHA-256.  Nobody can
    upload over HTTP; the host operator adds sources with `satchel-host app publish`."""

    def __init__(self, d, coll=""):
        from .hub import BlobStore
        self._b = BlobStore(d, APP_MAX_BYTES, 0, coll)
        self.d, self.coll = d, coll
        self.max_bytes, self.max_file_bytes, self.quota_bytes = 0, APP_MAX_BYTES, 0

    def _no(self, *a, **k):
        return 403, {"ok": False, "error": "app sources are added by the host operator"}

    put = start = chunk = _no

    def status(self, sha):
        return self._b.status(sha)

    def get(self, h):
        return self._b.get(h)

    def delete(self, h) -> bool:
        return self._b.delete(h)

    def add(self, src: bytes) -> str:
        check_source(src)
        return self._b._commit(src)
