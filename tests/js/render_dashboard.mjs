// Runs docs/app.js with a minimal DOM against a data.json given on the command line, and prints every string the page assigned to innerHTML.
// A page that escapes what it renders has no tag or attribute of the data in that HTML.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const [appPath, dataPath] = process.argv.slice(2);
const data = JSON.parse(readFileSync(dataPath, "utf8"));
const html = {};
const element = (id) => {
  const store = { id, hidden: false, dataset: {}, checked: false, disabled: false, style: {}, children: [] };
  return new Proxy(store, {
    get(t, k) {
      if (k in t) return t[k];
      if (k === "innerHTML") return html[id] || "";
      if (k === "querySelectorAll") return () => [];
      if (k === "querySelector") return () => null;
      if (k === "closest") return () => null;
      if (k === "addEventListener" || k === "setAttribute" || k === "append" || k === "appendChild") return () => {};
      return undefined;
    },
    set(t, k, v) { if (k === "innerHTML") html[id] = String(v); else t[k] = v; return true; },
  });
};
const els = {};
const document = {
  getElementById: (id) => (els[id] ||= element(id)),
  querySelector: (sel) => (els[sel] ||= element(sel)),
  querySelectorAll: () => [],
  createElement: () => element("created"),
};
const sandbox = {
  document, console,
  location: { search: "", hash: "", pathname: "/index.html", href: "" },
  history: { replaceState() {} },
  URLSearchParams,
  fetch: async () => ({ json: async () => data }),
  setTimeout, Promise,
};
vm.createContext(sandbox);
// the page swallows a failure to draw into a "could not be loaded" card; here it must show, or a broken stub would look like a safe page
const source = readFileSync(appPath, "utf8").replace(".catch(function () {", ".catch(function (e) { console.error(e && e.stack);");
vm.runInContext(source, sandbox);
await new Promise((r) => setTimeout(r, 50));
console.log(JSON.stringify(html));
