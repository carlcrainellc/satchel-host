"""Voice clips for rooms (Satchel's Walkie Talkie): the room.voice record type and the
clip store a room host keeps.  Opt-in: `python3 -m typed.hub --voice` (or `satchel-host init
--type rooms --voice`) registers room.voice and gives every room a ClipStore.

What a room host keeps: clip files are deleted 7 days after upload, each signing key may
store 4 MiB of clips per room and send 60 clips in any 24 hours, and one room holds at most
256 MiB of clips.  Uploads must be signed.
"""
import os, threading, time

from . import record as R, hub as H

MAX_MS = 30000
MAX_CLIP = 35 * 1024
MAX_BLOB = 8 + 65536 + 2 * 16
ROOM_VOICE = R.TypeSpec("room.voice", 1, {
    "sha256": R.hexhash(required=True), "size": R.integer(1, MAX_BLOB),
    "dur_ms": R.integer(1, MAX_MS), "key": R.pattern(r"[0-9a-f]{64}", what="key")}, 0)


def register():
    R.KIND_TYPES["room"].specs[ROOM_VOICE.key] = ROOM_VOICE


# What a room host keeps for the Walkie Talkie.
KEEP_S = 7 * 86400            # clip files are deleted 7 days after upload
QUOTA_BYTES = 4 << 20         # per person (signing key) per room: 4 MiB of stored clip files
PER_DAY = 60                  # per person per room: 60 clips in any 24 hours
TOTAL_BYTES = 256 << 20       # all clips in one room together (stored and uploading)
SWEEP_S = 600                 # how often the background sweep runs

FULL = ("Your voice clip space in this room is full ({mb} MB). Clips are deleted after "
        "{days} days, which frees space.")
DAY = "You've sent {n} voice clips to this room in the last 24 hours. That's the limit; try again later."


class ClipStore(H.BlobStore):
    """BlobStore for a room's voice clips: files expire after keep_s, each signing key has a
    byte quota and a clip-per-day cap, and the room has a total cap.  Uploads must be signed
    (/v1/upload); clips still uploading count against the quota, the total and the day cap."""

    def __init__(self, d, keep_s=KEEP_S, quota_bytes=QUOTA_BYTES, per_day=PER_DAY, clock=time.time,
                 coll="", total_bytes=TOTAL_BYTES, upload_ttl_s=H.UPLOAD_TTL_S):
        super().__init__(d, MAX_CLIP + 1024, quota_bytes, coll, total_bytes=total_bytes,
                         clock=clock, upload_ttl_s=upload_ttl_s)
        self.keep_s, self.per_day = int(keep_s), int(per_day)
        stamp = [v for v in self._acct.values() if "ts" not in v]
        for v in stamp:   # files from before expiry was on: their 7 days start now
            v["ts"] = int(self.clock())
        if stamp:
            self._save()
        self.sweep()

    def sweep(self, now=None):
        """Delete clip files older than keep_s, and stale uploads. Returns how many clips went."""
        now = self.clock() if now is None else now
        with self._lock:
            old = [h for h, v in list(self._acct.items()) if now - v.get("ts", now) >= self.keep_s]
            for h in old:
                self.delete(h)
        self.sweep_uploads(now)
        return len(old)

    def _admit(self, who, sha, now):
        day = sum(1 for v in self._acct.values() if v["author"] == who and now - v.get("ts", 0) < 86400)
        day += sum(1 for m in self._up.values() if m["author"] == who)
        if day >= self.per_day:
            return 429, {"ok": False, "error": DAY.format(n=self.per_day), "limit": "clips_per_day",
                         "per_day": self.per_day}
        return None

    def start(self, env, now):
        self.sweep()
        code, r = super().start(env, now)
        if code == 403 and r.get("error") == "upload quota exceeded":
            r = {**r, "error": FULL.format(mb=f"{self.quota_bytes / (1 << 20):.2g}", days=f"{self.keep_s / 86400:.2g}"), "limit": "quota"}
        return code, r

    def status(self, sha):
        self.sweep()
        return super().status(sha)

    def get(self, h):
        self.sweep()
        return super().get(h)


def room_blobs(hub, name, d, **kw):
    """Give a room a ClipStore. Test overrides come from SATCHEL_VOICE_KEEP_S, _QUOTA, _PER_DAY
    and _TOTAL."""
    env = os.environ
    kw.setdefault("keep_s", int(env.get("SATCHEL_VOICE_KEEP_S", KEEP_S)))
    kw.setdefault("quota_bytes", int(env.get("SATCHEL_VOICE_QUOTA", QUOTA_BYTES)))
    kw.setdefault("per_day", int(env.get("SATCHEL_VOICE_PER_DAY", PER_DAY)))
    kw.setdefault("total_bytes", int(env.get("SATCHEL_VOICE_TOTAL", TOTAL_BYTES)))
    st = hub.nodes[name].blobs = ClipStore(str(d), coll=name, **kw)

    def loop():
        while True:
            time.sleep(min(SWEEP_S, max(1, st.keep_s // 4)))
            try: st.sweep()
            except Exception: pass   # keep sweeping
    threading.Thread(target=loop, daemon=True).start()
    return st
