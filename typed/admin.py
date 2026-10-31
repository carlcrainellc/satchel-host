"""Hub moderation CLI (typed collections).  Writes a signed mod record with the hub's host key.
  python3 -m typed.admin --data DIR --coll lobby hide post <id> --reason spam
  python3 -m typed.admin --data DIR --coll lobby unhide author <hexkey>
  python3 -m typed.admin --data DIR --coll files remove post <id> --reason "illegal content"
  python3 -m typed.admin --data DIR --coll lobby pin post <id>        (max 3 per room)
  python3 -m typed.admin --data DIR --coll lobby header --description "..." --rules "..."
Directory review queue (new listings wait here; nothing pending is public):
  python3 -m typed.admin --data DIR --coll directory pending
  python3 -m typed.admin --data DIR --coll directory approve <pending id or link>
  python3 -m typed.admin --data DIR --coll directory reject <pending id or link> --reason "..."
  python3 -m typed.admin --data DIR --coll directory unapprove <link>   (its next entry waits again)
  python3 -m typed.admin --data DIR --coll directory approved
hide/unhide: public signed record; the text stays and clients may choose to show it.
remove: public signed record AND the post text (or a file's blob bytes) is deleted from this
host at once; mirrors and clients delete their copies when they sync.  Not reversible.
The running hub serves the new record on its next request (mirrors get it on the next push)."""
import argparse
import json
import os

from .host import Host, load_host_key
from .hub import BlobStore


REVIEW = ("pending", "approved", "approve", "reject", "unapprove")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="the hub's --data directory")
    ap.add_argument("--coll", required=True, help="collection name, e.g. lobby or files")
    ap.add_argument("action", choices=["hide", "unhide", "remove", "pin", "unpin", "header",
                                       "pending", "approved", "approve", "reject", "unapprove"])
    ap.add_argument("target_type", nargs="?", help="post or author (approve/reject/unapprove: the id or link)")
    ap.add_argument("target", nargs="?")
    ap.add_argument("--reason", default="")
    ap.add_argument("--description", help="header: room description (500 chars max)")
    ap.add_argument("--rules", help="header: room rules (1000 chars max)")
    a = ap.parse_args(argv)
    if a.action in REVIEW:
        from . import dirboard
        return dirboard.main(["--data", a.data, "--coll", a.coll, a.action]
                             + ([a.target_type] if a.target_type else []) + ["--reason", a.reason])
    if a.target_type not in (None, "post", "author"):
        ap.error("target type is post or author")
    if a.action != "header" and not (a.target_type and a.target):
        ap.error("give a target type and target")
    if a.action in ("remove", "pin", "unpin") and a.target_type != "post":
        ap.error(f"{a.action} takes a post id")
    d = os.path.join(a.data, a.coll)
    if not os.path.exists(os.path.join(d, "log.sqlite3")):
        raise SystemExit(f"no collection {a.coll!r} under {a.data}")
    h = Host(d, a.coll, load_host_key(a.data), {})
    if os.path.isdir(os.path.join(d, "blobs")):
        h.blobs = BlobStore(os.path.join(d, "blobs"), coll=a.coll, manage_uploads=False)
    if a.action == "header":
        print(json.dumps(h.set_header(a.description, a.rules)))
        return
    print(json.dumps(h.moderate(a.action, a.target_type, a.target, a.reason)))


if __name__ == "__main__":
    main()
