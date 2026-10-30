import json, os, socket, subprocess, sys, tarfile, time, unittest, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
from helpers import tmpdir
import mbfmt as F
import satchel_host as S

CLI = os.path.join(os.path.dirname(S.__file__), "satchel_host.py")


def run(*a, ok=True):
    r = subprocess.run([sys.executable, CLI, *a], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if ok and r.returncode:
        raise AssertionError(r.stderr + r.stdout)
    return r


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class HostPack(unittest.TestCase):
    def setUp(self):
        self.t = tmpdir()
        run("root", "init", "--dir", f"{self.t}/root")
        self.pin = open(f"{self.t}/root/root.pin").read().strip()

    def init(self, typ, name, **kw):
        pre = f"{self.t}/{name}"
        args = ["init", "--type", typ, "--name", name, "--prefix", pre, "--port", str(free_port())]
        for k, v in kw.items():
            args += [f"--{k}"] + ([] if v is None else [v])
        run(*args)
        return pre

    def sign(self, pre, days=90, root=None, ok=True):
        return run("root", "sign", f"{pre}/request.json", "--root-key",
                   root or f"{self.t}/root/root.key", "--days", str(days), "--yes",
                   "--out", f"{pre}/../d.json", ok=ok)

    def test_init_never_creates_root_key(self):
        pre = self.init("board", "b1")
        self.assertTrue(os.path.exists(f"{pre}/data/host.key"))
        self.assertFalse(any("root" in f for f in os.listdir(f"{pre}/data")))
        req = json.load(open(f"{pre}/request.json"))
        self.assertEqual(req["collections"], ["b1"])
        self.assertEqual(oct(os.stat(f"{pre}/data/host.key").st_mode & 0o777), "0o600")

    def test_root_sign_caps_90_days(self):
        pre = self.init("wiki", "w1")
        self.assertNotEqual(self.sign(pre, days=91, ok=False).returncode, 0)
        self.sign(pre, days=90)
        b = json.load(open(f"{self.t}/d.json"))
        d = b["delegations"]["w1"]
        self.assertEqual(F.verify_delegation(d, self.pin, "w1"), b["host"])

    def test_install_refuses_wrong_host_and_root_change(self):
        pa, pb = self.init("board", "ba"), self.init("board", "bb")
        self.sign(pa)
        r = run("install-delegation", f"{self.t}/d.json", "--prefix", pb, "--no-systemd", ok=False)
        self.assertNotEqual(r.returncode, 0)
        run("install-delegation", f"{self.t}/d.json", "--prefix", pa, "--no-systemd")
        run("root", "init", "--dir", f"{self.t}/root2")
        self.sign(pa, root=f"{self.t}/root2/root.key")
        r = run("install-delegation", f"{self.t}/d.json", "--prefix", pa, "--no-systemd", ok=False)
        self.assertIn("DIFFERENT root", r.stderr)

    def test_unit_is_loopback_and_hardened(self):
        pre = self.init("rooms", "hall1", admin="ab" * 32, apps="apps1", voice=None)
        cfg = S.load_cfg(pre)
        u = S.unit_text(cfg)
        for needle in ("--bind 127.0.0.1", "IPAddressDeny=any", "NoNewPrivileges=true",
                       "ProtectSystem=strict", "CapabilityBoundingSet=", "--delegation-dir",
                       "--coll room:hall1", "--coll app:apps1", "--voice", "--admin " + "ab" * 32):
            self.assertIn(needle, u)
        self.assertNotIn("root.key", u)

    def test_app_pointer_and_source_checks(self):
        from typed import apps as A
        ok = [{"coll": "apps", "name": "chat", "sha256": "a" * 64}]
        A.check_pointers(ok, "chat")
        for bad in ([{"coll": "apps", "name": "Chat", "sha256": "a" * 64}],
                    [{"coll": "apps", "name": "chat", "sha256": "A" * 64}],
                    [{"coll": "apps", "name": "chat", "sha256": "a" * 64, "x": 1}],
                    ok * 2, [dict(ok[0], name=f"a{i}") for i in range(9)], "nope"):
            with self.assertRaises(F.FormatError):
                A.check_pointers(bad)
        A.check_source(b"panel.render({t: 'text', v: 'hi'});")
        for bad in (b"", b"\xff\xfe", b"x" * (A.APP_MAX_BYTES + 1)):
            with self.assertRaises(F.FormatError):
                A.check_source(bad)
        st = A.BundleStore(f"{self.t}/bs", "apps")
        h = st.add(b"log('x');")
        self.assertEqual(st.get(h), b"log('x');")

    def test_app_bundle_needs_root_key_and_good_source(self):
        src = f"{self.t}/a.js"
        open(src, "w").write("log('hi');")
        r = run("root", "app-bundle", src, "--name", "Bad Name", "--title", "x", "--ver", "1",
                "--root-key", f"{self.t}/root/root.key", "--out", f"{self.t}/b.json", ok=False)
        self.assertNotEqual(r.returncode, 0)
        run("root", "app-bundle", src, "--name", "hi", "--title", "Hi", "--ver", "1.0",
            "--root-key", f"{self.t}/root/root.key", "--out", f"{self.t}/b.json")
        b = json.load(open(f"{self.t}/b.json"))
        self.assertEqual((b["coll"], b["record"]["rec"]["author"], b["source"]), ("apps", self.pin, "log('hi');"))
        self.assertNotIn("root.key", json.dumps(b))

    def test_hub_runs_with_offline_delegation_and_backup(self):
        pre = self.init("rooms", "lobby1")
        self.sign(pre)
        run("install-delegation", f"{self.t}/d.json", "--prefix", pre, "--no-systemd")
        p = subprocess.Popen([sys.executable, CLI, "run", "--prefix", pre],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            info = json.loads(p.stdout.readline())
            self.assertEqual(info["root_pin"], self.pin)
            port = S.load_cfg(pre)["port"]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/c/lobby1/v1/delegation") as r:
                d = json.load(r)
            self.assertEqual(F.verify_delegation(d, self.pin, "lobby1"),
                             F.pub_hex(S.host_key(S.load_cfg(pre))))
            st = run("status", "--prefix", pre)
            self.assertEqual(json.loads(st.stdout)["http"], "ok")
        finally:
            p.terminate(); p.wait()
        run("backup", "--prefix", pre, "--out", f"{self.t}/bk.tar.gz")
        names = tarfile.open(f"{self.t}/bk.tar.gz").getnames()
        self.assertIn("data/host.key", names)
        self.assertFalse(any("root.key" in n for n in names))
        r = run("uninstall", "--prefix", pre, "--purge", ok=False)
        self.assertNotEqual(r.returncode, 0)  # needs --yes
        run("uninstall", "--prefix", pre, "--purge", "--yes")
        self.assertFalse(os.path.exists(pre))

    def _start(self, pre):
        p = subprocess.Popen([sys.executable, CLI, "run", "--prefix", pre],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        p.stdout.readline()
        return p, S.load_cfg(pre)["port"]

    def test_moderate_remove_header_pin(self):
        from typed.record import KIND_TYPES, make_record
        pre = self.init("rooms", "hall")
        self.sign(pre)
        run("install-delegation", f"{self.t}/d.json", "--prefix", pre, "--no-systemd")
        p, port = self._start(pre)
        base = f"http://127.0.0.1:{port}/c/hall"
        try:
            k = F.new_key()
            ids = []
            for txt in ("REMOVE-THIS-TEXT", "keep me"):
                env = make_record(k, KIND_TYPES["room"], "hall", "room.msg", {"text": txt}, [], pow_bits=16)
                req = urllib.request.Request(base + "/v1/submit", data=json.dumps(env).encode(),
                                             headers={"Content-Type": "application/json"})
                ids.append(json.load(urllib.request.urlopen(req))["id"])
            r = run("moderate", "--prefix", pre, "remove", "post", ids[0], ok=False)
            self.assertNotEqual(r.returncode, 0)  # no confirmation, nothing removed
            run("moderate", "--prefix", pre, "remove", "post", ids[0], "--reason", "illegal", "--yes")
            for root, _, files in os.walk(f"{pre}/data"):
                for n in files:
                    self.assertNotIn(b"REMOVE-THIS-TEXT", open(os.path.join(root, n), "rb").read(), n)
            log = json.load(urllib.request.urlopen(base + "/v1/log?since=0"))["entries"]
            self.assertEqual(log[0]["tomb"]["by"], "remove")
            self.assertEqual(log[0]["tomb"]["record"]["mod"]["reason"], "illegal")
            self.assertEqual(run("moderate", "--prefix", pre, "remove", "author", "ab" * 32,
                                 "--yes", ok=False).returncode != 0, True)
            run("moderate", "--prefix", pre, "pin", "post", ids[1])
            recs = json.load(urllib.request.urlopen(base + "/v1/records?since=0"))["records"]
            self.assertEqual([r["rec"]["mod"]["action"] for r in recs], ["remove", "pin"])
            run("header", "--prefix", pre, "--description", "Main hall", "--rules", "Be kind")
            pol = json.load(urllib.request.urlopen(base + "/v1/policy"))
            self.assertEqual((pol["description"], pol["rules"]), ("Main hall", "Be kind"))
            cp = json.load(urllib.request.urlopen(base + "/v1/checkpoint"))["checkpoint"]
            self.assertEqual(cp["policy"], F.policy_hash(pol))  # header is covered by the signature
        finally:
            p.terminate(); p.wait()

    def test_files_limits_flag(self):
        pre = self.init("files", "drop1", **{"max-file-mb": "2", "quota-mb": "3"})
        cmd = S.exec_cmd(S.load_cfg(pre))
        self.assertIn("--max-file-mb", cmd)
        self.assertEqual(float(cmd[cmd.index("--max-file-mb") + 1]), 2)
        self.sign(pre)
        run("install-delegation", f"{self.t}/d.json", "--prefix", pre, "--no-systemd")
        p, port = self._start(pre)
        try:
            info = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/c/drop1/v1/info"))
            self.assertEqual((info["max_file_bytes"], info["quota_bytes"]), (2 << 20, 3 << 20))
        finally:
            p.terminate(); p.wait()
        run("files", "--prefix", pre, "--max-file-mb", "50")
        self.assertEqual(S.load_cfg(pre)["max_file_mb"], 50)
        self.assertNotEqual(run("files", "--prefix", pre, "--max-file-mb", "999", ok=False).returncode, 0)

    def test_mail_host_role(self):
        pre = self.init("mail", "post1")
        cfg = S.load_cfg(pre)
        u = S.unit_text(cfg)
        for needle in ("-m typed.mailhost", "--bind 127.0.0.1", "IPAddressDeny=any",
                       "ProtectSystem=strict", f"--delegation {pre}/delegation/post1.json"):
            self.assertIn(needle, u)
        self.sign(pre)
        run("install-delegation", f"{self.t}/d.json", "--prefix", pre, "--no-systemd")
        p = subprocess.Popen([sys.executable, CLI, "run", "--prefix", pre],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        base = f"http://127.0.0.1:{cfg['port']}"
        try:
            for _ in range(100):
                try:
                    with urllib.request.urlopen(base + "/v1/mail/info", timeout=1) as r:
                        info = json.load(r)
                    break
                except OSError:
                    time.sleep(0.1)
            hk = F.pub_hex(S.host_key(cfg))
            self.assertEqual(info["host"], hk)
            self.assertEqual(F.verify_delegation(info["delegation"], self.pin, "post1"), hk)
            self.assertEqual(json.loads(run("status", "--prefix", pre).stdout)["http"], "ok")
            # a sealed message through the installed host; nothing readable lands on disk
            from typed import filecrypt as FC, mail as M
            seed = os.urandom(32)
            bk, bx = F.key_from_seed(seed), FC.x25519_from_seed(seed)

            def post(path, obj):
                req = urllib.request.Request(base + path, json.dumps(obj).encode(),
                                             {"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req) as r:
                        return r.status, json.load(r)
                except urllib.error.HTTPError as e:
                    return e.code, json.load(e)

            def auth(op, args=None):
                _, c = post("/v1/mail/challenge", {"to": F.pub_hex(bk)})
                return post("/v1/mail/auth", M.sign_auth(bk, hk, op, c["nonce"], args or {}))

            self.assertEqual(auth("register")[0], 200)
            card = M.make_card(bk, bx, [base])["card"]
            ak = F.new_key()
            m = M.make_message(ak, FC.x25519_from_seed(os.urandom(32)), [base], card, "s", "HOSTPACK-SECRET")
            self.assertEqual(post("/v1/mail/put", {"to": card["ed"], "msg_id": m["msg_id"],
                                                   "blob": M.b64e(M.seal(m, card["x"]))})[0], 200)
            for root_, _, files in os.walk(pre):
                for n in files:
                    with open(os.path.join(root_, n), "rb") as f:
                        self.assertNotIn(b"HOSTPACK-SECRET", f.read())
            got = auth("fetch", {"ids": [m["msg_id"]]})[1]["msgs"][0]
            self.assertEqual(len(auth("list")[1]["msgs"]), 1)  # kept until ack
            self.assertEqual(auth("ack", {"ids": [m["msg_id"]]})[1]["deleted"], 1)
            self.assertEqual(M.open_sealed(M.b64d(got["blob"]), bx, m["msg_id"], card["ed"])["body"],
                             "HOSTPACK-SECRET")
        finally:
            p.terminate(); p.wait()
            p.stdout.close(); p.stderr.close()
        self.assertNotIn("--closed", S.unit_text(S.load_cfg(pre)))  # open by default
        self.assertIn("CLOSED", run("mail", "--prefix", pre, "--closed").stdout)
        self.assertIn("--closed", S.unit_text(S.load_cfg(pre)))
        run("mail", "--prefix", pre, "--open", "--max-inboxes", "50")
        u = S.unit_text(S.load_cfg(pre))
        self.assertNotIn("--closed", u)
        self.assertIn("--max-inboxes 50", u)

    def test_renew_writes_new_request(self):
        pre = self.init("files", "drop1")
        t0 = json.load(open(f"{pre}/request.json"))["created"]
        time.sleep(1.1)
        run("renew", "--prefix", pre)
        self.assertGreater(json.load(open(f"{pre}/request.json"))["created"], t0)


class Sesh(unittest.TestCase):
    """Throwaway test keys only; never real Session Router keys."""

    def setUp(self):
        self.t = tmpdir()

    def init(self, name, *extra):
        pre = f"{self.t}/{name}"
        r = run("init", "--type", "wiki", "--name", name, "--prefix", pre,
                "--port", str(free_port()), *extra)
        return pre, r

    def test_default_noninteractive_is_skip(self):
        pre, r = self.init("w0")
        self.assertIsNone(S.load_cfg(pre).get("sesh"))
        self.assertFalse(os.path.exists(f"{pre}/sesh"))

    def test_new_key(self):
        pre, r = self.init("w1", "--sesh", "new")
        c = S.load_cfg(pre)["sesh"]
        self.assertTrue(c["name"].endswith(".sesh") and len(c["name"]) == 57)
        self.assertFalse(c["enabled"])
        self.assertIn(c["name"], r.stdout)
        key = open(c["keyfile"]).read().strip()
        self.assertNotIn(key[:64], r.stdout)  # secret never printed
        self.assertEqual(oct(os.stat(c["keyfile"]).st_mode & 0o777), "0o600")
        self.assertIn(f"127.0.0.1:{S.load_cfg(pre)['port']}", open(c["config"]).read())
        self.assertIn(c["name"], open(f"{pre}/sesh/ENABLE.txt").read())
        self.assertNotIn("sesh", S.unit_text(S.load_cfg(pre)))  # host unit unchanged

    def test_import_formats_and_name(self):
        k = F.new_key()
        seed = k.private_bytes_raw()
        pub = bytes.fromhex(F.pub_hex(k))
        want = F.zb32(pub) + ".sesh"
        for i, blob in enumerate([seed.hex().encode(), (seed + pub).hex().encode() + b"\n",
                                  seed + pub, b"d1:s64:" + seed + pub + b"e"]):
            kf = f"{self.t}/k{i}"
            open(kf, "wb").write(blob)
            pre, r = self.init(f"imp{i}", "--sesh-import", kf)
            c = S.load_cfg(pre)["sesh"]
            self.assertEqual(c["name"], want)
            self.assertEqual(open(c["keyfile"], "rb").read(), blob)
            self.assertEqual(oct(os.stat(c["keyfile"]).st_mode & 0o777), "0o600")
            self.assertNotIn(seed.hex(), r.stdout + r.stderr)

    def test_import_rejects_bad_and_inconsistent(self):
        bad = f"{self.t}/bad"
        open(bad, "w").write("not a key")
        pre = f"{self.t}/x"
        r = run("init", "--type", "wiki", "--name", "x", "--prefix", pre,
                "--port", str(free_port()), "--sesh-import", bad, ok=False)
        self.assertNotEqual(r.returncode, 0)
        seed = F.new_key().private_bytes_raw()
        other = bytes.fromhex(F.pub_hex(F.new_key()))
        with self.assertRaises(ValueError):
            S.parse_sesh_key((seed + other).hex().encode())

    def test_later_sesh_subcommand_and_no_overwrite(self):
        pre, _ = self.init("w2")
        run("sesh", "--prefix", pre, "--sesh", "new")
        self.assertTrue(S.load_cfg(pre)["sesh"]["name"])
        r = run("sesh", "--prefix", pre, "--sesh", "new", ok=False)
        self.assertIn("refusing to overwrite", r.stderr)


if __name__ == "__main__":
    unittest.main()
