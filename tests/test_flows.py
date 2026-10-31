"""Every host flow the guide describes, end to end in a temp sandbox (no systemd).

init for every type (rooms with extra rooms, mail, directory), .sesh make / import / skip,
root sign, install-delegation, run, status, link, directory-entry + directory-submit,
mail --closed / --open, upgrade, apps (root app-bundle, app publish, app use).  With PY_CLIENT
pointing at a checkout of the Python reference client (it has snapps/typed/core.py), that
client also joins, reads the directory and uses mail."""
import json, os, signal, socket, subprocess, sys, time, unittest, urllib.error, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.dirname(HERE)
sys.path[:0] = [PACK, HERE, os.path.join(PACK, "bin")]
from helpers import tmpdir  # noqa: E402
import mbfmt as F  # noqa: E402
from typed import joins as J  # noqa: E402
from typed.record import KIND_TYPES, make_record  # noqa: E402
import satchel_host as S  # noqa: E402
from typed.sync import submit  # noqa: E402

CLI = os.path.join(PACK, "bin", "satchel_host.py")
CLIENT = os.environ.get("PY_CLIENT") or ""
HAVE_CLIENT = bool(CLIENT) and os.path.isfile(os.path.join(CLIENT, "snapps", "typed", "core.py"))


def run(*a, ok=True):
    r = subprocess.run([sys.executable, CLI, *a], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    if ok and r.returncode:
        raise AssertionError(" ".join(a) + "\n" + r.stderr + r.stdout)
    return r


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_up(port, path, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    raise AssertionError(f"port {port}{path} never came up")


class Flows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = tmpdir()
        run("root", "init", "--dir", f"{cls.t}/root")
        cls.pin = open(f"{cls.t}/root/root.pin").read().strip()
        cls.procs, cls.hosts = [], {}
        from cryptography.hazmat.primitives import serialization as ser
        k = F.new_key()  # a vanity-style key file to import: 64-byte seed||pub hex
        seed = k.private_bytes(ser.Encoding.Raw, ser.PrivateFormat.Raw, ser.NoEncryption())
        pub = k.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)
        open(f"{cls.t}/vanity.key", "w").write((seed + pub).hex())
        cls.vanity = F.zb32(pub) + ".sesh"
        plan = [("board", "news", ["--sesh", "new"]),
                ("apps", "apps", ["--sesh-import", f"{cls.t}/vanity.key"]),
                ("wiki", "wiki", ["--sesh", "skip"]),
                ("rooms", "lobby", ["--room", "hikers", "--room", "maps", "--apps", "lobby-apps", "--voice"]),
                ("files", "files", []), ("people", "people", []),
                ("directory", "directory", []), ("mail", "post", [])]
        for typ, name, extra in plan:
            cls.hosts[typ] = cls.setup_host(typ, name, extra)

    @classmethod
    def setup_host(cls, typ, name, extra):
        pre, port = f"{cls.t}/{name}", free_port()
        run("init", "--type", typ, "--name", name, "--prefix", pre, "--port", str(port), *extra)
        run("root", "sign", f"{pre}/request.json", "--root-key", f"{cls.t}/root/root.key",
            "--days", "90", "--yes", "--out", f"{pre}.delegation.json")
        r = run("install-delegation", f"{pre}.delegation.json", "--prefix", pre, "--no-systemd")
        h = {"pre": pre, "port": port, "name": name, "typ": typ, "install_out": r.stdout}
        cls.start(h)
        return h

    @classmethod
    def start(cls, h):
        p = subprocess.Popen([sys.executable, CLI, "run", "--prefix", h["pre"]],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        h["proc"] = p
        cls.procs.append(p)
        wait_up(h["port"], {"board": "/v1/info", "mail": "/v1/mail/info"}.get(h["typ"], f"/c/{h['name']}/v1/info"))

    @classmethod
    def stop(cls, h):
        os.killpg(h["proc"].pid, signal.SIGTERM)
        h["proc"].wait(10)

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass

    def status(self, typ):
        r = run("status", "--prefix", self.hosts[typ]["pre"], ok=False)
        return r.returncode, json.loads(r.stdout)

    def test_01_status_every_role(self):
        for typ in self.hosts:
            code, st = self.status(typ)
            self.assertEqual((code, st["http"]), (0, "ok"), (typ, st))
            self.assertEqual(st["root_pin"], self.pin)
            self.assertGreater(st["delegation_days_left"], 89)
            self.assertEqual(st["pack"], S.VERSION)
        self.assertEqual(self.status("rooms")[1]["collections"], ["lobby", "hikers", "maps", "lobby-apps"])
        self.assertEqual(self.status("mail")[1]["mail_signup"], "open")

    def test_02_sesh_make_import_skip(self):
        st = self.status("board")[1]
        self.assertTrue(st["sesh"].endswith(".sesh"))
        self.assertEqual(st["links"], [f"srnet://{st['sesh']}/board/news#root={self.pin}"])
        self.assertEqual(self.status("apps")[1]["sesh"], self.vanity)
        self.assertIsNone(self.status("wiki")[1]["sesh"])
        ini = open(f"{self.hosts['board']['pre']}/sesh/router.ini").read()
        self.assertIn(f"80=127.0.0.1:{self.hosts['board']['port']}", ini)
        self.assertIn("invite link: srnet://", self.hosts["board"]["install_out"])
        for ln in st["links"] + self.status("apps")[1]["links"]:
            J.parse(ln)  # the client accepts these links

    def test_03_every_link_probes_and_verifies(self):
        for typ, h in self.hosts.items():
            if typ == "mail":
                continue
            local = f"127.0.0.1:{h['port']}"
            out = run("link", "--prefix", h["pre"]).stdout.split()
            self.assertTrue(out)
            for ln in out:
                l = J.parse(ln)
                self.assertEqual(l["root"], self.pin)
                pr = J.probe(local, l["kind"], l["name"])  # what the client's join sheet does
                self.assertEqual(pr["root"], self.pin, (typ, ln))
        names = sorted(c["name"] for c in J.offered(f"127.0.0.1:{self.hosts['rooms']['port']}"))
        self.assertEqual(names, ["hikers", "lobby", "lobby-apps", "maps"])

    def test_04_directory_entry_and_submit(self):
        d = self.hosts["directory"]
        hikers = J.make(f"127.0.0.1:{self.hosts['rooms']['port']}", "room", "hikers", self.pin)
        out = f"{self.t}/entry.json"
        run("root", "directory-entry", "--link", hikers, "--title", "Hikers", "--desc", "Weekend walks",
            "--root-key", f"{self.t}/root/root.key", "--out", out)
        to = f"http://127.0.0.1:{d['port']}/c/directory"
        r = run("directory-submit", out, "--to", to)
        self.assertIn("Listed", r.stdout)  # signed by the directory's own root: listed at once
        logs = lambda: json.load(urllib.request.urlopen(to + "/v1/log"))["entries"]  # noqa: E731
        titles = lambda: [e["env"]["rec"]["body"]["title"] for e in logs()]  # noqa: E731
        self.assertEqual(titles(), ["Hikers"])
        # someone else's link (another root key) waits for the admin
        if not os.path.exists(f"{self.t}/root2/root.key"):
            run("root", "init", "--dir", f"{self.t}/root2")
        pin2 = open(f"{self.t}/root2/root.pin").read().strip()
        walk = J.make(f"127.0.0.1:{self.hosts['rooms']['port']}", "room", "walkers", pin2)
        out3 = f"{self.t}/entry3.json"
        run("root", "directory-entry", "--link", walk, "--title", "Walkers", "--root-key",
            f"{self.t}/root2/root.key", "--out", out3)
        self.assertIn("waiting for approval", run("directory-submit", out3, "--to", to).stdout)
        self.assertEqual(titles(), ["Hikers"])
        pend = json.loads(run("directory", "pending", "--prefix", d["pre"]).stdout)["pending"]
        self.assertEqual([p["title"] for p in pend], ["Walkers"])
        r = run("directory", "approve", "--prefix", d["pre"], walk, ok=False)  # asks first
        self.assertNotEqual(r.returncode, 0)
        run("directory", "approve", "--prefix", d["pre"], walk, "--yes")
        self.assertEqual(titles(), ["Hikers", "Walkers"])
        self.assertIn("Listed", run("directory-submit", out, "--to", to).stdout)  # replay: same entry
        # a new title for an approved link goes straight in, but only with --replace
        out2 = f"{self.t}/entry2.json"
        run("root", "directory-entry", "--link", hikers, "--title", "Hikers and walkers", "--root-key",
            f"{self.t}/root/root.key", "--out", out2)
        r = run("directory-submit", out2, "--to", to, ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--replace", r.stderr + r.stdout)
        self.assertIn("Listed", run("directory-submit", out2, "--to", to, "--replace").stdout)
        # the rooms host tells the directory it is still running (host key + delegation)
        rooms = self.hosts["rooms"]
        r = run("directory-refresh", "--prefix", rooms["pre"], "--to", to, "--link", hikers)
        self.assertIn("ok ", r.stdout)
        alive = json.load(urllib.request.urlopen(to + "/v1/alive"))["alive"]
        self.assertEqual([a["rec"]["body"]["link"] for a in alive], [hikers])
        # the lobby isn't listed: refused, nonzero exit
        lobby = J.make(f"127.0.0.1:{rooms['port']}", "room", "lobby", self.pin)
        r = run("directory-refresh", "--prefix", rooms["pre"], "--to", to, "--link", lobby, ok=False)
        self.assertNotEqual(r.returncode, 0)
        # --timer on writes the units (systemd isn't used in tests) and saves the settings
        run("directory-refresh", "--prefix", rooms["pre"], "--to", to, "--link", hikers, "--timer", "on")
        svc = open(f"{rooms['pre']}/dirrefresh.service").read()
        self.assertIn("typed.dirboard", svc)
        self.assertIn(hikers.split("#")[0], svc)
        self.assertIn("OnUnitActiveSec=1h", open(f"{rooms['pre']}/dirrefresh.timer").read())
        self.assertIn("ok ", run("directory-refresh", "--prefix", rooms["pre"]).stdout)
        run("directory-refresh", "--prefix", rooms["pre"], "--timer", "off")
        Flows._listed = True
        other = J.make(f"127.0.0.1:{self.hosts['rooms']['port']}", "room", "hikers", "cd" * 32)
        r = run("root", "directory-entry", "--link", other, "--title", "x", "--root-key",
                f"{self.t}/root/root.key", "--out", f"{self.t}/bad.json", ok=False)
        self.assertNotEqual(r.returncode, 0)

    def test_05_mail_closed_open(self):
        h = self.hosts["mail"]
        lk, url = run("link", "--prefix", h["pre"]).stdout.split()
        self.assertEqual(url, f"http://127.0.0.1:{h['port']}")
        self.assertEqual(lk, f"srnet://127.0.0.1:{h['port']}/mail/post#root={self.pin}")
        pr = J.probe(f"127.0.0.1:{h['port']}", "mail", "post")  # the client's "Add mail host" check
        self.assertEqual((pr["root"], pr["url"]), (self.pin, url))
        e = f"{self.t}/mail-entry.json"  # mail hosts can be listed in a directory
        run("root", "directory-entry", "--link", lk, "--title", "Free mail", "--root-key",
            f"{self.t}/root/root.key", "--out", e)
        d = self.hosts["directory"]
        to = f"http://127.0.0.1:{d['port']}/c/directory"
        self.assertIn("Listed", run("directory-submit", e, "--to", to).stdout)  # own root: at once
        self.assertIn("Listed", run("directory-submit", e, "--to", to).stdout)
        info = lambda: json.dumps(json.load(urllib.request.urlopen(f"http://127.0.0.1:{h['port']}/v1/mail/info")))
        self.assertIn('"open_registration": true', info())
        run("mail", "--prefix", h["pre"], "--closed")
        self.stop(h); self.start(h)
        self.assertIn('"open_registration": false', info())
        self.assertEqual(self.status("mail")[1]["mail_signup"], "closed")
        run("mail", "--prefix", h["pre"], "--open")
        self.stop(h); self.start(h)
        self.assertIn('"open_registration": true', info())
        self.assertEqual(self.status("mail")[1]["mail_signup"], "open")

    def test_06_upgrade_keeps_keys_and_data(self):
        h = self.hosts["wiki"]
        base = f"http://127.0.0.1:{h['port']}/c/wiki"
        env = make_record(F.new_key(), KIND_TYPES["wiki"], "wiki", "wiki.page",
                          {"slug": "hello", "title": "Hello"}, [], pow_bits=16)
        self.assertTrue(submit([base], env).get("ok"))
        hk = open(f"{h['pre']}/data/host.key", "rb").read()
        cfgp = f"{h['pre']}/host.json"
        cfg = json.load(open(cfgp)); cfg["pack"] = "0.2.0"; open(cfgp, "w").write(json.dumps(cfg))
        r = run("upgrade", "--prefix", h["pre"])
        self.assertIn(f"0.2.0 -> {S.VERSION}", r.stdout)
        self.assertTrue(os.path.isdir(f"{h['pre']}/app.prev"))
        self.stop(h); self.start(h)
        self.assertEqual(open(f"{h['pre']}/data/host.key", "rb").read(), hk)
        code, st = self.status("wiki")
        self.assertEqual((code, st["pack"], st["root_pin"]), (0, S.VERSION, self.pin))
        self.assertIn(env["sig"], urllib.request.urlopen(base + "/v1/log?since=0").read().decode())
        cfg = json.load(open(cfgp)); cfg["pack"] = "9.0.0"; open(cfgp, "w").write(json.dumps(cfg))
        self.assertNotEqual(run("upgrade", "--prefix", h["pre"], ok=False).returncode, 0)
        cfg["pack"] = "0.3.0"; open(cfgp, "w").write(json.dumps(cfg))

    def test_08_apps_publish_and_point(self):
        import hashlib
        from typed import apps as A
        src_path = os.path.join(PACK, "apps", "starter", "hello.js")
        src = open(src_path, "rb").read()
        sha = hashlib.sha256(src).hexdigest()
        key = f"{self.t}/root/root.key"
        # a bundle for the standalone app host, and one for the rooms host's app collection
        for typ, coll in (("apps", "apps"), ("rooms", "lobby-apps")):
            h = self.hosts[typ]
            out = f"{self.t}/hello-{coll}.json"
            run("root", "app-bundle", src_path, "--name", "hello", "--title", "Guestbook", "--ver", "1",
                "--caps", "read,draft", "--coll", coll, "--root-key", key, "--out", out)
            run("app", "publish", out, "--prefix", h["pre"], "--coll", coll)
            base = f"http://127.0.0.1:{h['port']}/c/{coll}"
            self.assertEqual(urllib.request.urlopen(f"{base}/v1/blob/{sha}").read(), src)
            log = json.load(urllib.request.urlopen(base + "/v1/log?since=0"))["entries"]
            b = [e["env"] for e in log if "env" in e and e["env"]["rec"]["t"] == "app.bundle"]
            self.assertEqual(len(b), 1)
            self.assertEqual(b[0]["rec"]["author"], self.pin)
            self.assertEqual((b[0]["rec"]["body"]["sha256"], b[0]["rec"]["body"]["size"]), (sha, len(src)))
            # nobody can upload over HTTP to an app collection
            req = urllib.request.Request(base + "/v1/blob", data=b"x", method="POST",
                                         headers={"Content-Type": "application/octet-stream"})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
            self.assertIn(cm.exception.code, (403, 413))
            # a bundle signed by anyone but the root key is refused
            body = A.bundle_body("evil", "Evil", "1", src, "read", "")
            env = make_record(F.new_key(), KIND_TYPES["app"], coll, "app.bundle", body, [], pow_bits=16)
            self.assertFalse(submit([base], env).get("ok"))
        # the room opens with the app by default; the app host defaults to its own bundle
        h = self.hosts["rooms"]
        r = run("app", "use", "--prefix", h["pre"], "--coll", "hikers", "--app", "lobby-apps:hello",
                "--default", "hello")
        self.assertIn('"default_app": "hello"', r.stdout)
        self.assertNotEqual(run("app", "use", "--prefix", h["pre"], "--coll", "hikers", "--app",
                                "lobby-apps:missing", ok=False).returncode, 0)
        pol = json.load(urllib.request.urlopen(f"http://127.0.0.1:{h['port']}/c/hikers/v1/policy"))
        self.assertEqual(pol["apps"], [{"coll": "lobby-apps", "name": "hello", "sha256": sha}])
        self.assertEqual(pol["default_app"], "hello")
        cp = json.load(urllib.request.urlopen(f"http://127.0.0.1:{h['port']}/c/hikers/v1/checkpoint"))
        self.assertEqual(cp["checkpoint"]["policy"], F.policy_hash(pol))  # the pointers are signed
        ha = self.hosts["apps"]
        run("app", "use", "--prefix", ha["pre"], "--coll", "apps", "--default", "hello")
        pol = json.load(urllib.request.urlopen(f"http://127.0.0.1:{ha['port']}/c/apps/v1/policy"))
        self.assertEqual(pol.get("default_app"), "hello")
        lst = json.loads(run("app", "list", "--prefix", h["pre"]).stdout)
        self.assertEqual(lst["bundles"]["lobby-apps"]["hello"]["sha256"], sha)
        ln = J.make(f"127.0.0.1:{h['port']}", "room", "hikers", self.pin, "hello")
        self.assertEqual(J.parse(ln)["app"], "hello")
        run("app", "use", "--prefix", h["pre"], "--coll", "hikers", "--clear")
        pol = json.load(urllib.request.urlopen(f"http://127.0.0.1:{h['port']}/c/hikers/v1/policy"))
        self.assertNotIn("apps", pol)

    @unittest.skipUnless(HAVE_CLIENT, "set PY_CLIENT to a reference client checkout")
    def test_07_real_client_join_directory_mail(self):
        if not getattr(Flows, "_listed", False):
            self.test_04_directory_entry_and_submit()
        code = r'''
import json, sys, os
sys.path[:0] = [os.path.join(sys.argv[1], "snapps")]
from typed.core import Core
a = json.loads(sys.argv[2])
c = Core(a["dir"], {"mail": {"hosts": [a["mail"]]}}, autoconfirm=True)
out = {}
pv = c.join_preview(a["rooms"]); out["preview"] = [pv["name"], pv["root"], pv["first_seen"]]
out["join"] = c.join(a["rooms"])["id"]
c.join(a["dirlink"])
out["entries"] = [e["title"] for e in c.view("directory")["view"]["entries"]]
out["sidebar"] = sorted(i["id"] for g in c.sidebar()["groups"] for i in g["items"])
out["share_root"] = c.share(out["join"])["link"].split("#root=")[1]
c.mail.register()
out["mail_link"] = c.mail.link().startswith("srnet://mail/")
b = Core(a["dir"] + "-b", {"mail": {"hosts": [a["mail"]]}}, autoconfirm=True); b.mail.register()
b.mail.send(c.mail.link(), "hi", "hello from b")
out["fetched"] = c.mail.fetch()["new"]
out["requests"] = len(c.mail.folder("requests"))
print(json.dumps(out))
'''
        rooms = f"127.0.0.1:{self.hosts['rooms']['port']}"
        args = {"dir": f"{self.t}/client", "mail": f"http://127.0.0.1:{self.hosts['mail']['port']}",
                "rooms": J.make(rooms, "room", "hikers", self.pin),
                "dirlink": J.make(f"127.0.0.1:{self.hosts['directory']['port']}", "directory", "directory", self.pin)}
        r = subprocess.run([sys.executable, "-c", code, CLIENT, json.dumps(args)], capture_output=True,
                           text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["preview"], ["hikers", self.pin, True])
        self.assertIn("Hikers and walkers", out["entries"])  # a 0.3.0 client is fine with refreshes
        self.assertIn("hikers", out["sidebar"])
        self.assertEqual(out["share_root"], self.pin)
        self.assertTrue(out["mail_link"])
        self.assertEqual((out["fetched"], out["requests"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
