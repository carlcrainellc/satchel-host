// End-to-end check against a Satchel checkout: a local satchel-host serves a room and an app
// collection, the starter app is published and pointed at, and Satchel's own core and
// sandbox load it the way the browser would.
//
//   cd satchel && npm install          (once)
//   SATCHEL=/path/to/satchel node tests/e2e_satchel.mjs
//
// Nothing leaves 127.0.0.1. Temp files go in the system temp dir.
import assert from "node:assert/strict";
import {execFileSync, spawn} from "node:child_process";
import {createHash} from "node:crypto";
import {mkdtempSync, readFileSync} from "node:fs";
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
const host = spawn("python3", [CLI, "run", "--prefix", pre], {stdio: ["ignore", "pipe", "inherit"]});
let ok = false;
try {
  await new Promise((res, rej) => { host.stdout.once("data", res); host.once("exit", (c) => rej(new Error("host exited " + c))); });

  const srcPath = join(PACK, "apps", "starter", "hello.js");
  const src = readFileSync(srcPath, "utf8");
  cli("root", "app-bundle", srcPath, "--name", "hello", "--title", "Guestbook", "--ver", "1", "--caps", "read,draft",
      "--root-key", join(t, "root", "root.key"), "--out", join(t, "hello.json"));
  cli("app", "publish", join(t, "hello.json"), "--prefix", pre);
  cli("app", "use", "--prefix", pre, "--coll", "lobby", "--app", "apps:hello", "--default", "hello");

  // Satchel reaches hosts by .sesh name; map a made-up one to the local port.
  const name = "y".repeat(52) + ".sesh";
  const fetchFn = (url, opt = {}) => { const u = new URL(url); return fetch(u.host === name ? `http://127.0.0.1:${port}${u.pathname}${u.search}` : url, opt); };
  const config = {collections: [
    {name: "lobby", kind: "room", root_pin: pin, hosts: [`http://${name}/c/lobby`]},
    {name: "apps", kind: "app", root_pin: pin, hosts: [`http://${name}/c/apps`]},
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
  ok = true;
  console.log("e2e ok: host served the room and the app collection; Satchel listed, fetched, checked and ran the starter app");
} finally {
  host.kill();
  if (!ok) process.exitCode = 1;
}
