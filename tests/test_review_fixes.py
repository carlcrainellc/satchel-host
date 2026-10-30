"""Regression tests for the review of 0.5.0 (one class per finding, F1..F11)."""
import hashlib, http.client, json, os, pwd, socket, sys, threading, time, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.dirname(HERE)
sys.path[:0] = [PACK, HERE, os.path.join(PACK, "bin")]
from helpers import tmpdir  # noqa: E402
import mbfmt as F  # noqa: E402
from typed import hub as HUB, voice as V, mailhost as MH, ratelimit as RL  # noqa: E402
from typed.host import Host  # noqa: E402
from typed.node import serve  # noqa: E402
from typed.record import KIND_TYPES, make_record  # noqa: E402
import satchel_host as S  # noqa: E402
from test_flows import run, free_port  # noqa: E402

LOW = {"pow_bits": 8, "new_key_pow_bits": 8, "new_key_rate": [[1000, 3600]],
       "rate": [[1000, 3600]], "global_rate": [[100000, 3600]]}


class Clock:
    def __init__(self, t=None):
        self.t = float(t or time.time())

    def __call__(self):
        return self.t


def make_hub(kinds, clock=time.time, admins=(), **per_kind):
    root, hk = F.new_key(), F.new_key()
    over = {k: dict(LOW, **per_kind.get(k, {})) for k, _ in kinds}
    return HUB.Hub(tmpdir(), root, hk, kinds, {"admins": list(admins)}, clock=clock,
                   pol_override=over)


def rec(key, kind, coll, t, body, refs=(), clock=None):
    return make_record(key, KIND_TYPES[kind], coll, t, body, list(refs), pow_bits=8,
                       ts=None if clock is None else int(clock()))


def db_bytes(h):
    """Everything SQLite has on disk for a host: the database file plus WAL/journal."""
    d = os.path.dirname(h._header_path)
    out = b""
    for fn in os.listdir(d):
        if fn.startswith("log.sqlite3"):
            with open(os.path.join(d, fn), "rb") as f:
                out += f.read()
    return out


def blob(n=100):
    b = os.urandom(n)
    return b, hashlib.sha256(b).hexdigest()


def upload(store, key, b, sha, clock, session=True):
    code, r = store.start(HUB.sign_upload(key, store.coll, sha, len(b), ts=clock()), clock())
    if code != 200:
        return code, r
    return store.chunk(sha, 0, b, session=r["session"] if session else None)


# ---- F1: install-delegation writes only this host's own collections -----------------------
class F1InstallDelegation(unittest.TestCase):
    def test_extra_bundle_keys_are_not_written(self):
        t = tmpdir()
        run("root", "init", "--dir", f"{t}/root")
        pre = f"{t}/h"
        run("init", "--type", "rooms", "--name", "lobby", "--prefix", pre,
            "--port", str(free_port()), "--sesh", "skip")
        run("root", "sign", f"{pre}/request.json", "--root-key", f"{t}/root/root.key",
            "--yes", "--out", f"{t}/d.json")
        with open(f"{t}/d.json") as f:
            b = json.load(f)
        env = b["delegations"]["lobby"]
        b["delegations"].update({"../../evil": env, "../evil2": env, "/tmp/abs": env, "Bad": env})
        with open(f"{t}/d2.json", "w") as f:
            json.dump(b, f)
        r = run("install-delegation", f"{t}/d2.json", "--prefix", pre, "--no-systemd")
        self.assertIn("ignored", r.stdout)
        self.assertEqual(sorted(os.listdir(f"{pre}/delegation")), ["lobby.json"])
        self.assertFalse(os.path.exists(f"{t}/evil.json") or os.path.exists(f"{pre}/evil2.json"))
        self.assertFalse(os.path.exists("/tmp/abs.json"))

    def test_bad_names_in_host_json_are_refused(self):
        for bad in ("../x", "x/y", "a\n", "", "A", "x" * 33):
            with self.assertRaises(SystemExit):
                S.colls({"name": "ok", "collections": ["ok", bad]})
            with self.assertRaises(SystemExit):
                S.safe_coll(bad)
        self.assertEqual(S.colls({"name": "ok", "app_colls": ["a-1"]}), ["ok", "a-1"])
        with self.assertRaises(SystemExit):
            HUB.Hub(tmpdir(), F.new_key(), F.new_key(), [("room", "../x")])


# ---- F2: file drops can't fill the disk ---------------------------------------------------
class F2Uploads(unittest.TestCase):
    def store(self, clock, quota=150, total=10 ** 6, **kw):
        return HUB.BlobStore(tmpdir(), 1000, quota, "files", total_bytes=total, clock=clock, **kw)

    def test_unsigned_blob_refused(self):
        hub = make_hub([("drop", "files")])
        self.assertEqual(hub.nodes["files"].blobs.put(b"x")[0], 403)
        srv = serve(hub)
        try:
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
            c.request("POST", "/c/files/v1/blob", body=b"x" * 1000)
            r = c.getresponse()
            self.assertEqual(r.status, 403)
            self.assertIn("signed", json.loads(r.read())["error"])
        finally:
            srv.shutdown()

    def test_quota_reserved_at_start(self):
        clk = Clock()
        st, k = self.store(clk), F.new_key()
        b1, s1 = blob()
        b2, s2 = blob()
        self.assertEqual(st.start(HUB.sign_upload(k, "files", s1, 100, ts=clk()), clk())[0], 200)
        # nothing uploaded yet, but the first 100 bytes are reserved: a second file is over
        code, r = st.start(HUB.sign_upload(k, "files", s2, 100, ts=clk()), clk())
        self.assertEqual(code, 403)
        self.assertEqual(r["used"], 100)

    def test_parallel_starts_cannot_overbook(self):
        clk = Clock()
        st, k = self.store(clk), F.new_key()
        envs = [HUB.sign_upload(k, "files", blob()[1], 100, ts=clk()) for _ in range(12)]
        codes = []
        ths = [threading.Thread(target=lambda e=e: codes.append(st.start(e, clk())[0])) for e in envs]
        [t.start() for t in ths]
        [t.join() for t in ths]
        self.assertEqual(codes.count(200), 1)
        self.assertEqual(st.used(F.pub_hex(k)), 100)

    def test_total_cap(self):
        clk = Clock()
        st = self.store(clk, quota=0, total=250)
        for i, want in enumerate((200, 200, 507)):
            b, sha = blob()
            self.assertEqual(upload(st, F.new_key(), b, sha, clk)[0], want, i)
        self.assertLessEqual(st.total_used(), 250)
        self.assertEqual(HUB.BlobStore(tmpdir(), 1000, 0, "x").total_bytes,
                         HUB.DEFAULT_TOTAL_MB * 1024 * 1024)  # 0/None is the default, not unlimited

    def test_chunks_bound_to_session(self):
        clk = Clock()
        st, k = self.store(clk), F.new_key()
        b, sha = blob()
        code, r = st.start(HUB.sign_upload(k, "files", sha, 100, ts=clk()), clk())
        self.assertEqual(st.chunk(sha, 0, b[:10], session="0" * 32)[0], 403)
        self.assertEqual(st.chunk(sha, 0, b[:10], session=r["session"])[0], 200)
        self.assertEqual(st.chunk(sha, 10, b[10:20])[0], 200)  # Satchel 0.3.0: no token
        self.assertEqual(st.chunk(sha, 20, b[20:], session=r["session"])[1]["done"], True)
        # a chunk with no started upload is refused
        self.assertEqual(st.chunk(blob()[1], 0, b"x")[0], 404)
        strict = self.store(clk, require_session=True)
        code, r = strict.start(HUB.sign_upload(k, "files", sha, 100, ts=clk()), clk())
        self.assertEqual(strict.chunk(sha, 0, b)[0], 403)
        self.assertEqual(strict.chunk(sha, 0, b, session=r["session"])[0], 200)

    def test_stale_uploads_swept_and_released(self):
        clk = Clock()
        st, k = self.store(clk), F.new_key()
        b, sha = blob()
        _, r = st.start(HUB.sign_upload(k, "files", sha, 100, ts=clk()), clk())
        st.chunk(sha, 0, b[:50], session=r["session"])
        self.assertEqual(len(os.listdir(st.up)), 2)
        # a restart picks the session up again, still reserved
        st2 = HUB.BlobStore(st.d, 1000, 150, "files", total_bytes=10 ** 6, clock=clk)
        self.assertEqual(st2.used(F.pub_hex(k)), 100)
        clk.t += HUB.UPLOAD_TTL_S + 1
        self.assertEqual(st2.chunk(sha, 50, b[50:])[0], 404)
        st2.sweep_uploads()
        self.assertEqual(os.listdir(st2.up), [])
        self.assertEqual(st2.used(F.pub_hex(k)), 0)
        b2, s2 = blob()
        self.assertEqual(upload(st2, k, b2, s2, clk)[0], 200)

    def test_quota_rechecked_per_chunk(self):
        clk = Clock()
        st, k = self.store(clk), F.new_key()
        b, sha = blob()
        _, r = st.start(HUB.sign_upload(k, "files", sha, 100, ts=clk()), clk())
        st.quota_bytes = 50  # policy lowered while the upload runs
        self.assertEqual(st.chunk(sha, 0, b, session=r["session"])[0], 403)
        self.assertNotIn(sha, st._up)

    def test_other_key_cannot_hijack_upload(self):
        clk = Clock()
        st = self.store(clk)
        b, sha = blob()
        st.start(HUB.sign_upload(F.new_key(), "files", sha, 100, ts=clk()), clk())
        self.assertEqual(st.start(HUB.sign_upload(F.new_key(), "files", sha, 100, ts=clk()), clk())[0], 409)

    def test_cli_moderation_store_leaves_uploads_alone(self):
        clk = Clock()
        st, k = self.store(clk), F.new_key()
        _, sha = blob()
        st.start(HUB.sign_upload(k, "files", sha, 100, ts=clk()), clk())
        HUB.BlobStore(st.d, coll="files", manage_uploads=False)
        self.assertEqual(len(os.listdir(st.up)), 2)

    def test_voice_day_cap_counts_pending(self):
        clk = Clock()
        cs = V.ClipStore(tmpdir(), per_day=2, clock=clk, coll="lobby")
        k = F.new_key()
        codes = [cs.start(HUB.sign_upload(k, "lobby", blob()[1], 100, ts=clk()), clk())[0]
                 for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])

    def test_voice_total_cap(self):
        clk = Clock()
        cs = V.ClipStore(tmpdir(), clock=clk, coll="lobby", total_bytes=150)
        b, sha = blob()
        self.assertEqual(upload(cs, F.new_key(), b, sha, clk)[0], 200)
        self.assertEqual(cs.start(HUB.sign_upload(F.new_key(), "lobby", blob()[1], 100, ts=clk()),
                                  clk())[0], 507)
        self.assertGreater(V.TOTAL_BYTES, 0)

    def test_cli_defaults_have_caps(self):
        self.assertEqual(S.check_mb(25, 250), (25, 250, S.DEFAULT_TOTAL_MB))
        with self.assertRaises(SystemExit):
            S.check_mb(25, 250, 0)
        cfg = {"type": "files", "name": "files", "prefix": "/opt/x", "port": 48913}
        cmd = S.exec_cmd(cfg)
        self.assertEqual(cmd[cmd.index("--total-mb") + 1], str(S.DEFAULT_TOTAL_MB))


# ---- F3: author deletes only hit the author's own live posts -------------------------------
class F3Delete(unittest.TestCase):
    def setUp(self):
        self.hub = make_hub([("room", "lobby")], room={"delete_rate": [[3, 3600]]})
        self.h = self.hub.nodes["lobby"]
        self.a, self.b = F.new_key(), F.new_key()

    def post(self, k, text="hi"):
        code, r = self.h.submit(rec(k, "room", "lobby", "room.msg", {"text": text}))
        self.assertEqual(code, 200, r)
        return r["id"]

    def test_only_author_and_only_live(self):
        pid = self.post(self.a)
        self.assertEqual(self.h.delete(F.make_delete(self.b, "lobby", [pid]))[0], 400)
        env = F.make_delete(self.a, "lobby", [pid])
        self.assertEqual(self.h.delete(env)[0], 200)
        self.assertTrue(self.h.delete(env)[1]["dup"])  # replay is idempotent
        # the tombstone has no author: nobody may "delete" it again, b included
        code, r = self.h.delete(F.make_delete(self.b, "lobby", [pid], ts=int(time.time()) + 1))
        self.assertEqual(code, 400)
        self.assertIn("gone", r["error"])
        self.assertEqual(self.h.delete(F.make_delete(self.a, "lobby", [pid], ts=int(time.time()) + 2))[0], 400)

    def test_removed_post_cannot_be_redeleted(self):
        pid = self.post(self.a)
        self.h.moderate("remove", "post", pid, "")
        n = self.h.store.rhead()
        self.assertEqual(self.h.delete(F.make_delete(self.b, "lobby", [pid]))[0], 400)
        self.assertEqual(self.h.store.rhead(), n)  # no record added

    def test_delete_rate_limited(self):
        pids = [self.post(self.a, f"m{i}") for i in range(5)]
        codes = [self.h.delete(F.make_delete(self.a, "lobby", [p]))[0] for p in pids]
        self.assertEqual(codes, [200, 200, 200, 429, 429])


# ---- F4: the hub runs retention -----------------------------------------------------------
class F4Retention(unittest.TestCase):
    def test_hub_main_starts_ticker(self):
        argv = ["hub", "--data", tmpdir(), "--coll", "room:lobby", "--coll", "wiki:w", "--tick", "7"]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(Host, "run_ticker") as rt, \
                mock.patch.object(HUB, "serve") as sv, mock.patch.object(HUB, "url_of", return_value="x"), \
                mock.patch("threading.Event.wait"):
            sv.return_value = None
            HUB.main()
        self.assertEqual(rt.call_count, 2)
        self.assertEqual(rt.call_args.args, (7.0,))

    def test_maintain_applies_retention(self):
        clk = Clock()
        hub = make_hub([("room", "lobby")], clock=clk, room={"retention_days": 1})
        h = hub.nodes["lobby"]
        code, r = h.submit(rec(F.new_key(), "room", "lobby", "room.msg", {"text": "old-text-x1y2"}, clock=clk))
        self.assertEqual(code, 200)
        clk.t += 2 * 86400
        h.maintain()
        self.assertNotIn("env", h.store.get(r["id"]))
        self.assertNotIn(b"old-text-x1y2", db_bytes(h))

    def test_wiki_and_apps_keep_history(self):
        self.assertEqual(HUB.KIND_POLICY["wiki"]["retention_days"], 0)
        self.assertEqual(HUB.KIND_POLICY["app"]["retention_days"], 0)


# ---- F5: a collection-wide write budget -----------------------------------------------------
class F5GlobalBudget(unittest.TestCase):
    def test_token_bucket(self):
        clk = Clock()
        tb = RL.TokenBucket([(3, 300)], clock=clk)
        self.assertEqual([tb.allow() for _ in range(4)], [True, True, True, False])
        clk.t += 100  # refills 1 token
        self.assertEqual([tb.allow(), tb.allow()], [True, False])
        self.assertTrue(RL.TokenBucket([], clock=clk).allow())

    def test_ratelimiter_evicts_idle_keys(self):
        clk = Clock()
        rl = RL.RateLimiter([(5, 60)], clock=clk)
        for i in range(RL.GC_EVERY - 1):
            rl.allow(f"k{i}")
        clk.t += 61
        rl.allow("last")
        self.assertLessEqual(len(rl.hits), 1)

    def test_many_keys_hit_the_global_budget(self):
        hub = make_hub([("room", "lobby")], room={"global_rate": [[3, 3600]]})
        h = hub.nodes["lobby"]
        codes = [h.submit(rec(F.new_key(), "room", "lobby", "room.msg", {"text": f"s{i}"}))[0]
                 for i in range(5)]
        self.assertEqual(codes, [200, 200, 200, 429, 429])

    def test_parallel_submits_respect_limit(self):
        hub = make_hub([("room", "lobby")], room={"rate": [[2, 3600]]})
        h, k = hub.nodes["lobby"], F.new_key()
        envs = [rec(k, "room", "lobby", "room.msg", {"text": f"p{i}"}) for i in range(10)]
        codes = []
        ths = [threading.Thread(target=lambda e=e: codes.append(h.submit(e)[0])) for e in envs]
        [t.start() for t in ths]
        [t.join() for t in ths]
        self.assertEqual(codes.count(200), 2)

    def test_defaults_and_cli_flags(self):
        for kind, pol in HUB.KIND_POLICY.items():
            self.assertTrue(pol.get("global_rate"), kind)
        self.assertEqual(HUB.write_overrides(18, [[100, 3600]]),
                         {"pow_bits": 18, "new_key_pow_bits": 20, "global_rate": [[100, 3600]]})
        t = tmpdir()
        pre = f"{t}/r"
        run("init", "--type", "rooms", "--name", "lobby", "--prefix", pre, "--port", str(free_port()),
            "--sesh", "skip", "--pow-bits", "18", "--rate", "100/3600", "--rate", "1000/86400")
        cfg = S.load_cfg(pre)
        self.assertEqual((cfg["pow_bits"], cfg["rate"]), (18, [[100, 3600], [1000, 86400]]))
        cmd = S.exec_cmd(cfg)
        self.assertIn("18", cmd[cmd.index("--pow-bits") + 1])
        self.assertEqual([cmd[i + 1] for i, x in enumerate(cmd) if x == "--rate"], ["100/3600", "1000/86400"])
        self.assertNotEqual(run("init", "--type", "mail", "--name", "m", "--prefix", f"{t}/m",
                                "--rate", "1/1", "--sesh", "skip", ok=False).returncode, 0)
        self.assertNotEqual(run("init", "--type", "rooms", "--name", "x", "--prefix", f"{t}/x",
                                "--rate", "nope", "--sesh", "skip", ok=False).returncode, 0)


# ---- F6: `run` as root drops to the service user --------------------------------------------
class F6DropPrivileges(unittest.TestCase):
    PW = pwd.struct_passwd(("svc", "x", 4321, 4322, "", "/nonexistent", "/usr/sbin/nologin"))

    def test_drops_to_service_user(self):
        calls = []
        with mock.patch.object(S, "is_root", return_value=True), \
                mock.patch.object(S.pwd, "getpwnam", return_value=self.PW), \
                mock.patch.object(S.os, "initgroups", lambda u, g: calls.append(("groups", u, g))), \
                mock.patch.object(S.os, "setgid", lambda g: calls.append(("gid", g))), \
                mock.patch.object(S.os, "setuid", lambda u: calls.append(("uid", u))), \
                mock.patch.object(S.os, "geteuid", return_value=4321), \
                mock.patch.object(S.os, "getuid", return_value=4321), \
                mock.patch.dict(os.environ, {}):
            S.drop_privileges({"user": "svc"})
            self.assertEqual(os.environ["HOME"], "/nonexistent")
        self.assertEqual(calls, [("groups", "svc", 4322), ("gid", 4322), ("uid", 4321)])

    def test_refuses_root_targets(self):
        root_pw = pwd.struct_passwd(("toor", "x", 0, 0, "", "/", "/bin/sh"))
        with mock.patch.object(S, "is_root", return_value=True):
            for cfg in ({"user": "root"}, {"user": ""}, {"user": "a b"}):
                with self.assertRaises(SystemExit):
                    S.drop_privileges(cfg)
            with mock.patch.object(S.pwd, "getpwnam", return_value=root_pw), \
                    mock.patch.object(S.os, "setuid") as su, self.assertRaises(SystemExit):
                S.drop_privileges({"user": "toor"})
            su.assert_not_called()
            with mock.patch.object(S.pwd, "getpwnam", side_effect=KeyError), self.assertRaises(SystemExit):
                S.drop_privileges({"user": "ghost"})

    def test_run_drops_before_exec(self):
        order = []
        cfg = {"type": "rooms", "name": "lobby", "prefix": "/opt/x", "port": 48912,
               "user": "svc", "root_pin": "ab"}
        with mock.patch.object(S, "load_cfg", return_value=cfg), \
                mock.patch.object(S, "drop_privileges", lambda c: order.append("drop")), \
                mock.patch.object(S.os, "chdir", lambda p: order.append("chdir")), \
                mock.patch.object(S.os, "execv", lambda *a: order.append("exec")):
            S.cmd_run(mock.Mock(prefix="/opt/x"))
        self.assertEqual(order, ["drop", "chdir", "exec"])

    def test_noop_when_not_root(self):
        with mock.patch.object(S, "is_root", return_value=False), \
                mock.patch.object(S.os, "setuid") as su:
            S.drop_privileges({"user": "root"})
        su.assert_not_called()


# ---- F7: removal really removes ----------------------------------------------------------
class F7Removal(unittest.TestCase):
    def test_author_delete_and_remove_leave_no_text(self):
        hub = make_hub([("room", "lobby")])
        h, k = hub.nodes["lobby"], F.new_key()
        _, r1 = h.submit(rec(k, "room", "lobby", "room.msg", {"text": "secret-alpha-31"}))
        _, r2 = h.submit(rec(k, "room", "lobby", "room.msg", {"text": "secret-beta-47"}))
        self.assertIn(b"secret-alpha-31", db_bytes(h))
        self.assertEqual(h.delete(F.make_delete(k, "lobby", [r1["id"]]))[0], 200)
        h.moderate("remove", "post", r2["id"], "")
        data = db_bytes(h)
        self.assertNotIn(b"secret-alpha-31", data)
        self.assertNotIn(b"secret-beta-47", data)
        h.store.vacuum()
        self.assertNotIn(b"secret-alpha-31", db_bytes(h))

    def test_deleted_file_bytes_go(self):
        clk = Clock()
        hub = make_hub([("drop", "files")], clock=clk,
                       drop={"max_file_bytes": 10000, "quota_bytes": 0, "total_bytes": 10 ** 6})
        h, a, b = hub.nodes["files"], F.new_key(), F.new_key()
        data, sha = blob(300)
        self.assertEqual(upload(h.blobs, a, data, sha, clk)[0], 200)
        body = {"sha256": sha, "size": 300, "name": "f.bin"}
        _, ra = h.submit(rec(a, "drop", "files", "drop.item", body, clock=clk))
        _, rb = h.submit(rec(b, "drop", "files", "drop.item", body, clock=clk))
        h.delete(F.make_delete(a, "files", [ra["id"]], ts=int(clk())))
        self.assertIsNotNone(h.blobs.get(sha))  # b's item still uses the bytes
        h.delete(F.make_delete(b, "files", [rb["id"]], ts=int(clk())))
        self.assertIsNone(h.blobs.get(sha))
        self.assertFalse(os.path.exists(os.path.join(h.blobs.d, sha)))

    def test_wording(self):
        for p in ("docs/HOST-GUIDE.md", "README.md"):
            with open(os.path.join(PACK, p)) as f:
                txt = f.read()
            for bad in ("deletes it from its disk at once", "deleted from disk"):
                self.assertNotIn(bad, txt, p)


# ---- F8: delegation dates ----------------------------------------------------------------
class F8Delegation(unittest.TestCase):
    def test_issued_and_length(self):
        root, hk = F.new_key(), F.new_key()
        now = int(time.time())
        ok = F.make_delegation(root, "b", F.pub_hex(hk), now + F.MAX_DELEGATION_S, issued=now)
        self.assertEqual(F.verify_delegation(ok, F.pub_hex(root), "b"), F.pub_hex(hk))
        bad = [F.make_delegation(root, "b", F.pub_hex(hk), now + 400 * 86400, issued=now + 300 * 86400),
               F.make_delegation(root, "b", F.pub_hex(hk), now + 86400, issued=now - 200 * 86400),
               F.make_delegation(root, "b", F.pub_hex(hk), now + F.MAX_DELEGATION_S + 3600, issued=now),
               F.make_delegation(root, "b", F.pub_hex(hk), now + 86400, issued=now + 2 * 86400)]
        for env in bad:
            with self.assertRaises(F.FormatError):
                F.verify_delegation(env, F.pub_hex(root), "b")
        self.assertIn("future", F.verify_delegation.__doc__)


# ---- F9: protected wiki pages -----------------------------------------------------------
class F9Protected(unittest.TestCase):
    def test_non_admin_edits_refused(self):
        adm, usr = F.new_key(), F.new_key()
        hub = make_hub([("wiki", "w")], admins=[F.pub_hex(adm)])
        h = hub.nodes["w"]
        _, r0 = h.submit(rec(adm, "wiki", "w", "wiki.rev", {"slug": "home", "text": "v1"}))
        self.assertEqual(h.submit(rec(adm, "wiki", "w", "wiki.protect", {"slug": "home", "on": True}))[0], 200)
        for t, body, refs in (("wiki.rev", {"slug": "home", "text": "spam"}, [r0["id"]]),
                              ("wiki.revert", {"slug": "home"}, [r0["id"]]),
                              ("wiki.page", {"slug": "home", "title": "X"}, [])):
            code, r = h.submit(rec(usr, "wiki", "w", t, body, refs))
            self.assertEqual(code, 400, t)
            self.assertIn("protected", r["error"])
        self.assertEqual(h.submit(rec(usr, "wiki", "w", "wiki.rev", {"slug": "other", "text": "ok"}))[0], 200)
        self.assertEqual(h.submit(rec(adm, "wiki", "w", "wiki.rev", {"slug": "home", "text": "v2"},
                                      [r0["id"]]))[0], 200)
        time.sleep(1.1)  # a later record id for the unprotect
        h.submit(rec(adm, "wiki", "w", "wiki.protect", {"slug": "home", "on": False}))
        self.assertEqual(h.submit(rec(usr, "wiki", "w", "wiki.rev", {"slug": "home", "text": "v3"}))[0], 200)


# ---- F10: mail host bind and Content-Length -----------------------------------------------
class F10Mail(unittest.TestCase):
    def test_bind_rule(self):
        h = MH.Host(tmpdir())
        for bad in ("0.0.0.0", "::", "2001:db8::1", "example.org"):
            with self.assertRaises(SystemExit):
                MH.serve(h, bad, 0, purge=False)

    def raw(self, port, headers):
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(b"POST /v1/mail/challenge HTTP/1.1\r\nHost: x\r\n" + headers + b"\r\n")
            return s.recv(4096).split(b" ", 2)[1]

    def test_content_length(self):
        srv = MH.serve(MH.Host(tmpdir()), "127.0.0.1", 0, purge=False)
        try:
            port = srv.server_address[1]
            self.assertEqual(self.raw(port, b""), b"413")
            self.assertEqual(self.raw(port, b"Content-Length: 0\r\n"), b"413")
            self.assertEqual(self.raw(port, b"Content-Length: -5\r\n"), b"413")
            self.assertEqual(self.raw(port, b"Content-Length: abc\r\n"), b"413")
            self.assertEqual(self.raw(port, b"Content-Length: 999999999\r\n"), b"413")
        finally:
            srv.shutdown()


# ---- F11: installer input ------------------------------------------------------------------
class F11Installer(unittest.TestCase):
    def test_user_and_prefix_tokens(self):
        for bad in ("root", "a b", "x;rm", "-x", "", "Ab", "a" * 33):
            with self.assertRaises(SystemExit):
                S.check_user(bad)
        self.assertEqual(S.check_user("satchelhost"), "satchelhost")
        for bad in ("/opt/a b", "/opt/x;y", "/opt/$(id)", "/opt/a%i", "/", "/opt/a\nb"):
            with self.assertRaises(SystemExit):
                S.check_prefix(bad)
        self.assertEqual(S.check_prefix("/opt/satchel-host/lobby"), "/opt/satchel-host/lobby")
        t = tmpdir()
        r = run("init", "--type", "rooms", "--name", "lobby", "--prefix", f"{t}/a b", "--sesh", "skip", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(f"{t}/a b"))
        r = run("init", "--type", "rooms", "--name", "lobby", "--prefix", f"{t}/ok", "--user", "x;y",
                "--sesh", "skip", ok=False)
        self.assertNotEqual(r.returncode, 0)

    def test_execstart_quoting(self):
        self.assertEqual(S.sd_quote("/opt/x/data"), "/opt/x/data")
        self.assertEqual(S.sd_quote('a b"c\\%$'), '"a b\\"c\\\\%%$$"')
        cfg = {"type": "rooms", "name": "lobby", "prefix": "/opt/x", "port": 48912, "user": "svc",
               "admins": ["a b"]}
        line = [ln for ln in S.unit_text(cfg).splitlines() if ln.startswith("ExecStart=")][0]
        self.assertIn('"a b"', line)
        cfg["user"] = "bad user"
        with self.assertRaises(SystemExit):
            S.unit_text(cfg)

    def test_service_user_group(self):
        calls = []
        with mock.patch.object(S.pwd, "getpwnam", side_effect=KeyError), \
                mock.patch.object(S.grp, "getgrnam", side_effect=KeyError), \
                mock.patch.object(S, "sh", lambda *a, **k: calls.append(a)):
            S.ensure_service_user(S.SERVICE_USER)
        self.assertIn("--user-group", calls[0])
        calls.clear()
        with mock.patch.object(S.pwd, "getpwnam", side_effect=KeyError), \
                mock.patch.object(S.grp, "getgrnam", return_value=object()), \
                mock.patch.object(S, "sh", lambda *a, **k: calls.append(a)):
            S.ensure_service_user(S.SERVICE_USER)
        self.assertEqual(calls[0][-3:], ("--gid", S.SERVICE_USER, S.SERVICE_USER))

    def test_root_init_chowns_with_primary_gid(self):
        """A root install, mocked: no satchelhost user or group needs to exist on the box."""
        t, owned = tmpdir(), []
        pw = pwd.struct_passwd((S.SERVICE_USER, "x", 4321, 9876, "", "/", "/usr/sbin/nologin"))
        with mock.patch.object(S, "is_root", return_value=True), \
                mock.patch.object(S.pwd, "getpwnam", return_value=pw), \
                mock.patch.object(S.os, "chown", lambda p, u, g, **k: owned.append((p, u, g))), \
                mock.patch.object(S, "sh") as sh:
            S.main(["init", "--type", "rooms", "--name", "lobby", "--prefix", f"{t}/h",
                    "--port", str(free_port()), "--sesh", "skip"])
        sh.assert_not_called()  # user exists (mocked): nothing to create
        self.assertTrue(owned)
        self.assertTrue(all(u == 4321 and g == 9876 for _, u, g in owned))
        self.assertTrue(any(p.endswith("/data") for p, _, _ in owned))
        self.assertEqual(S.load_cfg(f"{t}/h")["user"], S.SERVICE_USER)


if __name__ == "__main__":
    unittest.main()
