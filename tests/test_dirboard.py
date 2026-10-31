"""Community board: review queue for new directory listings, refreshes (directory.alive) and
the active / inactive view."""
import json, os, subprocess, sys, unittest, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import tmpdir  # noqa: E402
import mbfmt as F  # noqa: E402
from typed import dirboard as DB, node as N, views as V  # noqa: E402
from typed.host import Host, load_host_key  # noqa: E402
from typed.node import serve, url_of  # noqa: E402
from typed.record import KIND_TYPES, codec_for, make_record  # noqa: E402
from test_review_fixes import Clock, make_hub  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Listed:
    """A community somewhere else: its root key, host key, delegation and link."""
    def __init__(self, name="hikers", kind="room", host=None, clock=None):
        host = host or F.zb32(os.urandom(32)) + ".sesh"
        self.root, self.hk = F.new_key(), F.new_key()
        now = int(clock() if clock else 0) or None
        import time
        now = now or int(time.time())
        self.deleg = F.make_delegation(self.root, name, F.pub_hex(self.hk), now + 60 * 86400, issued=now - 10)
        self.link = f"srnet://{host}/{kind}/{name}#root={F.pub_hex(self.root)}"

    def entry(self, title, clock, desc=None, key=None):
        return make_record(key or self.root, KIND_TYPES["directory"], "directory", "directory.entry",
                           {"link": self.link, "title": title, "desc": desc}, [], ts=int(clock()),
                           pow_bits=8)

    def alive(self, clock, key=None, deleg=None, coll="directory"):
        return DB.make_alive(key or self.hk, coll, self.link, deleg or self.deleg, ts=int(clock()))


def log_titles(h):
    return [e["env"]["rec"]["body"]["title"] for e in h.store.since(0, 500) if "env" in e]


def view(h, now):
    from typed.views import items_from_store
    items = items_from_store(h.store, h.codec)
    return V.directory_view(items, h.policy, alive=h.dirboard.alive_ts(), now=now)["entries"]


class Board(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.hub = make_hub([("directory", "directory")], clock=self.clock)
        self.h = self.hub.nodes["directory"]
        self.b = self.h.dirboard

    def test_new_link_waits_and_is_not_public(self):
        a = Listed(clock=self.clock)
        code, r = self.h.submit(a.entry("Hikers", self.clock))
        self.assertEqual((code, r["pending"]), (202, True))
        self.assertEqual(log_titles(self.h), [])  # not in the public log, so no client sees it
        self.assertEqual(N._get(self.h, "/v1/log", {})[1]["entries"], [])
        self.assertEqual([p["title"] for p in self.b.pending()], ["Hikers"])
        # the same envelope again is not queued twice
        self.assertEqual(self.h.submit(a.entry("Hikers", self.clock))[0], 202)

    def test_only_the_links_root_can_list_it(self):
        a, other = Listed(clock=self.clock), F.new_key()
        code, r = self.h.submit(a.entry("Hikers", self.clock, key=other))
        self.assertEqual(code, 400)
        self.assertIn("root key", r["error"])
        self.assertEqual(self.b.pending(), [])

    def test_approve_then_edits_go_straight_in_and_new_links_wait(self):
        a = Listed(clock=self.clock)
        self.h.submit(a.entry("Hikers", self.clock))
        r = self.b.approve(a.link)
        self.assertEqual(r["approved"], [a.link])
        self.assertEqual(log_titles(self.h), ["Hikers"])
        self.assertEqual(self.b.pending(), [])
        self.clock.t += 5
        code, r = self.h.submit(a.entry("Hikers and walkers", self.clock, desc="Weekend walks"))
        self.assertEqual((code, r.get("pending")), (200, None))
        self.assertEqual(log_titles(self.h), ["Hikers", "Hikers and walkers"])
        self.assertEqual([e["title"] for e in view(self.h, self.clock())], ["Hikers and walkers"])
        # another link of the same root key is a new listing: it waits again
        b2 = Listed(name="maps", clock=self.clock)
        b2.root, b2.link = a.root, a.link.replace("/room/hikers#", "/room/maps#")
        self.assertEqual(self.h.submit(b2.entry("Maps", self.clock))[0], 202)
        self.assertEqual(len(log_titles(self.h)), 2)

    def test_reject_drops_it(self):
        a = Listed(clock=self.clock)
        _, r = self.h.submit(a.entry("Spam", self.clock))
        self.assertEqual(self.b.reject(r["id"], "spam")["rejected"], [r["id"]])
        self.assertEqual(self.b.pending(), [])
        self.assertEqual(log_titles(self.h), [])
        with self.assertRaises(F.FormatError):
            self.b.approve(r["id"])

    def test_unapprove_makes_the_next_entry_wait(self):
        a = Listed(clock=self.clock)
        self.h.submit(a.entry("Hikers", self.clock))
        self.b.approve(a.link)
        self.b.unapprove(a.link)
        self.clock.t += 5
        self.assertEqual(self.h.submit(a.entry("Renamed", self.clock))[0], 202)
        self.assertEqual(log_titles(self.h), ["Hikers"])
        self.assertEqual(self.h.submit(a.alive(self.clock))[0], 403)

    def test_queue_has_a_cap_per_key(self):
        a = Listed(clock=self.clock)
        for i in range(DB.MAX_PENDING_PER_KEY):
            self.clock.t += 1
            self.assertEqual(self.h.submit(a.entry(f"T{i}", self.clock))[0], 202)
        self.clock.t += 1
        self.assertEqual(self.h.submit(a.entry("one more", self.clock))[0], 429)

    def test_listings_from_before_the_queue_count_as_approved(self):
        clock = Clock()
        root, hk = F.new_key(), F.new_key()
        d = tmpdir()
        deleg = F.make_delegation(root, "directory", F.pub_hex(hk), int(clock()) + 86400, issued=int(clock()))
        pol = {"pow_bits": 0, "new_key_pow_bits": 0, "new_key_rate": [[100, 3600]]}
        h0 = Host(os.path.join(d, "directory"), "directory", hk, deleg, pol, clock=clock,
                  codec=codec_for("directory"))
        a = Listed(clock=clock)
        self.assertEqual(h0.submit(a.entry("Old listing", clock))[0], 200)  # no queue then
        h1 = Host(os.path.join(d, "directory"), "directory", hk, deleg, pol, clock=clock,
                  codec=codec_for("directory"))
        b = DB.attach(h1)
        self.assertTrue(b.approved(a.link))
        clock.t += 5
        self.assertEqual(h1.submit(a.entry("Old listing, new title", clock))[0], 200)

    def test_refresh_chain_and_view(self):
        a = Listed(clock=self.clock)
        self.h.submit(a.entry("Hikers", self.clock))
        # not approved yet: refused
        self.assertEqual(self.h.submit(a.alive(self.clock))[0], 403)
        self.b.approve(a.link)
        t0 = self.clock()
        self.assertEqual(self.h.submit(a.alive(self.clock))[0], 200)
        # signed by a key the root didn't delegate
        code, r = self.h.submit(a.alive(self.clock, key=F.new_key()))
        self.assertEqual(code, 400)
        # a delegation for another collection of the same root
        other = F.make_delegation(a.root, "maps", F.pub_hex(a.hk), int(t0) + 86400, issued=int(t0) - 5)
        self.assertEqual(self.h.submit(a.alive(self.clock, deleg=other))[0], 400)
        # a delegation from some other root
        stranger = F.new_key()
        fake = F.make_delegation(stranger, "hikers", F.pub_hex(a.hk), int(t0) + 86400, issued=int(t0) - 5)
        self.assertEqual(self.h.submit(a.alive(self.clock, deleg=fake))[0], 400)
        # signed for another directory
        self.assertEqual(self.h.submit(a.alive(self.clock, coll="elsewhere"))[0], 400)
        # served, and only the newest per link is kept; refreshes never enter the log
        got = N._get(self.h, "/v1/alive", {})[1]
        self.assertEqual(len(got["alive"]), 1)
        self.assertEqual(DB.check_alive(got["alive"][0], "directory")[0], a.link)
        self.assertEqual(log_titles(self.h), ["Hikers"])
        self.assertTrue(all(e["env"]["rec"]["t"] == "directory.entry" for e in self.h.store.since(0, 500)))
        # active now; 25 h without a refresh: inactive; next refresh: back
        e = view(self.h, self.clock())[0]
        self.assertEqual((e["inactive"], e["ago_h"]), (False, 0))
        self.clock.t += 25 * 3600
        e = view(self.h, self.clock())[0]
        self.assertEqual((e["inactive"], e["ago_h"]), (True, 25))
        self.assertEqual(self.h.submit(a.alive(self.clock))[0], 200)
        e = view(self.h, self.clock())[0]
        self.assertEqual((e["inactive"], e["ago_h"]), (False, 0))
        # an old refresh doesn't replace a newer one
        old = a.alive(Clock(self.clock() - 60))
        self.assertTrue(self.h.submit(old)[1].get("dup"))

    def test_refresh_for_an_unlisted_link(self):
        a = Listed(clock=self.clock)
        with self.b.lock:
            self.b.db.execute("INSERT INTO dir_approved VALUES(?,?,?)", (a.link, 0, "test"))
        self.assertEqual(self.h.submit(a.alive(self.clock))[0], 404)

    def test_view_sorts_by_latest_sign_of_life(self):
        a, b = Listed(clock=self.clock), Listed(name="maps", clock=self.clock)
        for x, t in ((a, "Alpha"), (b, "Beta")):
            self.h.submit(x.entry(t, self.clock))
            self.b.approve(x.link)
        self.clock.t += 600
        self.h.submit(a.alive(self.clock))
        self.assertEqual([e["title"] for e in view(self.h, self.clock())], ["Alpha", "Beta"])
        self.clock.t += 60
        self.h.submit(b.alive(self.clock))
        self.assertEqual([e["title"] for e in view(self.h, self.clock())], ["Beta", "Alpha"])

    def test_http_alive_and_202(self):
        srv = serve(self.h)
        url = url_of(srv)
        try:
            a = Listed(clock=self.clock)
            from typed.sync import http_json
            code, r = http_json(url + "/v1/submit", a.entry("Hikers", self.clock))
            self.assertEqual((code, r["pending"]), (202, True))
            self.b.approve(a.link)
            code, r = http_json(url + "/v1/submit", a.alive(self.clock))
            self.assertEqual(code, 200, r)
            got = json.load(urllib.request.urlopen(url + "/v1/alive"))
            self.assertEqual(len(got["alive"]), 1)
            self.assertEqual(got["active_s"], 86400)
        finally:
            srv.shutdown()

    def test_admin_cli(self):
        a = Listed(clock=self.clock)
        _, r = self.h.submit(a.entry("Hikers", self.clock))
        data = os.path.dirname(os.path.dirname(self.h._header_path))
        with open(os.path.join(data, "host.key"), "wb") as f:
            f.write(os.urandom(32))  # approving signs nothing; the CLI only needs a key file
        cmd = [sys.executable, "-m", "typed.admin", "--data", data, "--coll", "directory"]
        out = subprocess.run(cmd + ["pending"], capture_output=True, text=True, cwd=ROOT, check=True).stdout
        self.assertEqual(json.loads(out)["pending"][0]["id"], r["id"])
        out = subprocess.run(cmd + ["approve", r["id"]], capture_output=True, text=True, cwd=ROOT, check=True).stdout
        self.assertEqual(json.loads(out)["approved"], [a.link])
        self.assertTrue(self.b.approved(a.link))  # same database the running host uses
        self.assertEqual(log_titles(self.h), ["Hikers"])

    def test_refresh_helper(self):
        a = Listed(clock=self.clock)
        self.h.submit(a.entry("Hikers", self.clock))
        self.b.approve(a.link)
        dd = tmpdir()
        with open(os.path.join(dd, "hikers.json"), "w") as f:
            json.dump(a.deleg, f)
        res = DB.refresh(a.hk, dd, [a.link], "http://x/c/directory",
                         post=lambda url, env: self.h.submit(env))
        self.assertEqual(res, [(a.link, True, "refreshed")])
        res = DB.refresh(a.hk, dd, [a.link.replace("hikers", "nope")], "http://x/c/directory",
                         post=lambda url, env: self.h.submit(env))
        self.assertFalse(res[0][1])
        self.assertEqual(DB.dir_coll_of("http://abc.sesh/c/listings"), "listings")


if __name__ == "__main__":
    unittest.main()
