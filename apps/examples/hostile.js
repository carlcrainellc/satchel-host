// Test app that misbehaves on purpose. The sandbox must contain all of it.
const probes = ["fetch", "XMLHttpRequest", "WebSocket", "importScripts", "postMessage", "self", "window", "document", "navigator", "records"];
log("probe " + probes.filter((n) => { try { return typeof globalThis[n] !== "undefined"; } catch { return false; } }).join(",") + ";");
try { panel.render({t: "script", v: "alert(1)"}); } catch (e) { log("render refused: " + e.message); }
panel.render({t: "col", c: [
  {t: "heading", v: "Hostile test app"},
  {t: "para", v: "Each button does something an app must not get away with."},
  {t: "row", c: [{t: "button", id: "spin", v: "Endless loop"}, {t: "button", id: "bomb", v: "Memory bomb"}]},
]});
onEvent((e) => {
  if (e.id === "spin") { for (;;) {} }
  if (e.id === "bomb") { const a = []; for (;;) a.push(new Array(1e5).fill(7)); }
});
