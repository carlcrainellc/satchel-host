// End-to-end check against a Satchel checkout: a local satchel-host serves a room and an app
// collection, the starter app is published and pointed at, and Satchel's own core and
// sandbox load it the way the browser would. A second host serves a file drop: Satchel
// uploads (signed, chunked), opens and deletes files there, and deletes its own room post.
//
//   cd satchel && npm install          (once)
//   SATCHEL=/path/to/satchel node tests/e2e_satchel.mjs
//
// Nothing leaves 127.0.0.1. Temp files go in the system temp dir.
import assert from "node:assert/strict";
import {execFileSync, spawn} from "node:child_process";
import {createHash} from "node:crypto";
import {existsSync, mkdtempSync, readFileSync, readdirSync} from "node:fs";
import {createRequire} from "node:module";
import {createServer} from "node:net";
import {tmpdir} from "node:os";
import {dirname, join} from "node:path";
import {fileURLToPath, pathToFileURL} from "node:url";

const PACK = dirname(dirname(fileURLToPath(import.meta.url)));
const SAT = process.env.SATCHEL;
if (!SAT) { console.error("set SATCHEL to a Satchel checkout (with npm install done)"); process.exit(2); }
const sat = (p) => import(pathToFileURL(join(SAT, p)).href);
const req = createRequire(join(SAT, "package.json"));

const {Core} = await sat("ext/lib/core.js");
const {Net} = await sat("ext/lib/net.js");
const {MemKv} = await sat("ext/lib/kv.js");
const {Vault} = await sat("ext/lib/vault.js");
const J = await sat("ext/lib/joins.js");
const {createGuest} = await sat("ext/sandbox/guest.js");
const {CAPS, checkTree, checkDraft} = await sat("ext/sandbox/limits.js");
const {newQuickJSWASMModuleFromVariant} = req("quickjs-emscripten-core");
const variant = req("@jitl/quickjs-wasmfile-release-sync").default || req("@jitl/quickjs-wasmfile-release-sync");

const CLI = join(PACK, "bin", "satchel_host.py");
const cli = (...a) => execFileSync("python3", [CLI, ...a], {encoding: "utf8"});
const freePort = () => new Promise((ok) => { const s = createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => ok(p)); }); });
const sha = (b) => createHash("sha256").update(b).digest("hex");

const t = mkdtempSync(join(tmpdir(), "satchel-host-e2e-"));
const port = await freePort();
const pre = join(t, "lobby");
cli("root", "init", "--dir", join(t, "root"));
const pin = readFileSync(join(t, "root", "root.pin"), "utf8").trim();
cli("init", "--type", "rooms", "--name", "lobby", "--prefix", pre, "--port", String(port), "--apps", "apps", "--sesh", "skip");
cli("root", "sign", join(pre, "request.json"), "--root-key", join(t, "root", "root.key"), "--days", "30", "--yes", "--out", join(t, "d.json"));
cli("install-delegation", join(t, "d.json"), "--prefix", pre, "--no-systemd");
const fport = await freePort();
const fpre = join(t, "files");
cli("init", "--type", "files", "--name", "files", "--prefix", fpre, "--port", String(fport), "--sesh", "skip");
cli("root", "sign", join(fpre, "request.json"), "--root-key", join(t, "root", "root.key"), "--days", "30", "--yes", "--out", join(t, "f.json"));
cli("install-delegation", join(t, "f.json"), "--prefix", fpre, "--no-systemd");
const started = (p) => new Promise((res, rej) => { p.stdout.once("data", res); p.once("exit", (c) => rej(new Error("host exited " + c))); });
const host = spawn("python3", [CLI, "run", "--prefix", pre], {stdio: ["ignore", "pipe", "inherit"]});
const fhost = spawn("python3", [CLI, "run", "--prefix", fpre], {stdio: ["ignore", "pipe", "inherit"]});
// every byte the host's database holds: the file plus its write-ahead log
const dbText = (p, coll) => readdirSync(join(p, "data", coll)).filter((f) => f.startsWith("log.sqlite3"))
  .map((f) => readFileSync(join(p, "data", coll, f)).toString("latin1")).join("");
let ok = false;
try {
  await started(host);
  await started(fhost);

  const srcPath = join(PACK, "apps", "starter", "hello.js");
  const src = readFileSync(srcPath, "utf8");
  cli("root", "app-bundle", srcPath, "--name", "hello", "--title", "Guestbook", "--ver", "1", "--caps", "read,draft",
      "--root-key", join(t, "root", "root.key"), "--out", join(t, "hello.json"));
  cli("app", "publish", join(t, "hello.json"), "--prefix", pre);
  cli("app", "use", "--prefix", pre, "--coll", "lobby", "--app", "apps:hello", "--default", "hello");

  // Satchel reaches hosts by .sesh name; map a made-up one to the local port.
  const name = "y".repeat(52) + ".sesh";
  const fname = "z".repeat(52) + ".sesh";
  const ports = {[name]: port, [fname]: fport};
  const fetchFn = (url, opt = {}) => { const u = new URL(url); return fetch(ports[u.host] ? `http://127.0.0.1:${ports[u.host]}${u.pathname}${u.search}` : url, opt); };
  const config = {collections: [
    {name: "lobby", kind: "room", root_pin: pin, hosts: [`http://${name}/c/lobby`]},
    {name: "apps", kind: "app", root_pin: pin, hosts: [`http://${name}/c/apps`]},
    {name: "files", kind: "drop", root_pin: pin, hosts: [`http://${fname}/c/files`]},
  ]};
  const vault = new Vault(new MemKv(), {iter: 100000}); await vault.load(); await vault.create("correct horse battery");
  const c = await Core.open({kv: new MemKv(), net: new Net({relay: false, fetchFn}), vault, config});
  await c.sync();

  // The link format is unchanged, &app= included.
  const link = J.make(name, "room", "lobby", pin, "hello");
  assert.deepEqual(J.parse(link), {host: name, kind: "room", name: "lobby", root: pin, app: "hello"});

  // The room's signed policy points at the bundle; Satchel lists it as the default view.
  const viaRoom = await c.appBundles("lobby");
  assert.deepEqual(viaRoom.map((b) => [b.name, b.via, !!b.default]), [["hello", "apps", true]]);
  assert.deepEqual(await c.appRefused("lobby"), []);
  const own = await c.appBundles("apps");
  assert.deepEqual(own.map((b) => [b.name, b.author === pin || b.author === undefined]), [["hello", true]]);
  const got = await c.appSource("apps", "hello");
  assert.equal(got.src, src);
  assert.equal(got.bundle.sha256, sha(Buffer.from(src)));
  assert.equal((await c.appSource("lobby", "hello")).src, src);

  // Run it in the same QuickJS sandbox the browser uses.
  const QJS = await newQuickJSWASMModuleFromVariant(variant);
  const msgs = [];
  const g = createGuest(QJS, {caps: {read: true, draft: true}, limits: CAPS, post: (m) => msgs.push(m), setTimer: () => 1, clearTimer: () => {}});
  const recs = await c.appRecords("apps");
  g.start(got.src, recs);
  g.view(recs);
  const renders = msgs.filter((m) => m.type === "render");
  assert.ok(renders.length, "the app drew its panel");
  checkTree(renders.at(-1).tree);
  assert.equal(msgs.filter((m) => m.type === "stopped").length, 0);
  g.ui({kind: "click", id: "sign", fields: {text: "  hello from the e2e test  "}});
  const drafts = msgs.filter((m) => m.type === "draft");
  assert.deepEqual(drafts.map(({t, body}) => ({t, body})), [{t: "app.data", body: {kind: "hello", text: "hello from the e2e test"}}]);
  checkDraft(drafts[0]);

  // Satchel signs the draft and the host takes it; the app sees it on the next view.
  const d = await c.draft("apps", drafts[0].t, drafts[0].body, drafts[0].refs || [], {app: true});
  assert.equal((await c.confirm(d.token)).ok, true);
  await c.sync();
  const after = await c.appRecords("apps");
  assert.ok(after.some((r) => r.body.kind === "hello" && r.body.text === "hello from the e2e test" && r.mine));
  msgs.length = 0;
  g.view(after);
  const last = msgs.filter((m) => m.type === "render").at(-1).tree;
  checkTree(last);
  assert.ok(JSON.stringify(last).includes("hello from the e2e test"));

  // Deleting your own room post: the host drops the text from its database at once.
  const marker = "e2e-delete-me-" + sha(Buffer.from(String(Date.now()))).slice(0, 12);
  const pd = await c.draft("lobby", "room.msg", {text: marker}, []);
  const posted = await c.confirm(pd.token);
  assert.equal(posted.ok, true, JSON.stringify(posted));
  await c.sync();
  assert.ok(dbText(pre, "lobby").includes(marker));
  const del = await c.delete("lobby", posted.id);
  assert.equal(del.ok, true, JSON.stringify(del));
  assert.ok(!dbText(pre, "lobby").includes(marker), "deleted text is gone from the host's database files");
  // the log keeps the post's id as a placeholder, so the history still verifies
  const again = await (await fetch(`http://127.0.0.1:${port}/c/lobby/v1/log?after=0`)).json();
  assert.ok(JSON.stringify(again).includes(posted.id));

  // Files: Satchel's signed chunked upload (no unsigned /v1/blob), open, then delete.
  const blobs = join(fpre, "data", "files", "blobs");
  const fileData = new Uint8Array(1500000).map((_, i) => (i * 31) & 255);   // two chunks
  const up = await c.drops.prepareUpload("files", fileData, "e2e.bin", "application/octet-stream", {mode: "gated"});
  assert.ok(up.token, JSON.stringify(up));
  const stored = await c.confirm(up.token);
  assert.equal(stored.ok, true, JSON.stringify(stored));
  await c.sync();
  c.drops.cache = new (c.drops.cache.constructor)();   // fetch it back from the host
  const opened = await c.drops.openFile("files", stored.id);
  assert.equal(opened.name, "e2e.bin");
  assert.equal(Buffer.compare(Buffer.from(opened.data), Buffer.from(fileData)), 0);
  const onDisk = readdirSync(blobs).filter((f) => /^[0-9a-f]{64}$/.test(f));
  assert.equal(onDisk.length, 1);
  const unsigned = await fetch(`http://127.0.0.1:${fport}/c/files/v1/blob`, {method: "POST", body: new Uint8Array(10)});
  assert.equal(unsigned.status, 403);
  const fdel = await c.delete("files", stored.id);
  assert.equal(fdel.ok, true, JSON.stringify(fdel));
  assert.ok(!existsSync(join(blobs, onDisk[0])), "the file's bytes are deleted with the post");
  assert.equal((await fetch(`http://127.0.0.1:${fport}/c/files/v1/blob/${onDisk[0]}`)).status, 404);

  ok = true;
  console.log("e2e ok: host served the room and the app collection; Satchel listed, fetched, checked and ran the starter app; " +
              "deleted its own post; uploaded, opened and deleted a file");
} finally {
  host.kill();
  fhost.kill();
  if (!ok) process.exitCode = 1;
}
