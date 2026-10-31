// Executes the REAL static/app.js against a minimal DOM stub and reports what renderMarkdown produced for hostile input.
const fs = require("fs");
class Node_ { constructor(t) { this.nodeType = t; } }
class Text extends Node_ { constructor(s) { super(3); this.data = String(s); } get textContent() { return this.data; } }
class El extends Node_ {
  constructor(tag) { super(1); this.tagName = String(tag).toUpperCase(); this.attrs = {}; this.children = []; this.className = ""; }
  append(...ns) { for (const n of ns) { if (n && n.tagName === "#FRAGMENT") this.children.push(...n.children); else this.children.push(typeof n === "string" ? new Text(n) : n); } }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener() {}
  removeChild(c) { this.children = this.children.filter((x) => x !== c); }
  get firstChild() { return this.children[0] || null; }
  get textContent() { return this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.children = [new Text(v)]; }
}
const document = { createElement: (t) => new El(t), createTextNode: (s) => new Text(s), createDocumentFragment: () => new El("#fragment"), querySelector: () => null, getElementById: () => null, addEventListener() {}, querySelectorAll: () => [] };
const window = {};
const fetchStub = () => new Promise(() => {});                   // boot() waits forever; nothing else runs
new Function("document", "window", "fetch", "confirm", "TextDecoder", fs.readFileSync(process.argv[2], "utf8"))(document, window, fetchStub, () => true, TextDecoder);
const { renderMarkdown } = window.__campus;
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const walk = (n, out) => { if (n.nodeType === 1 && n.tagName === "#FRAGMENT") { n.children.forEach((c) => walk(c, out)); } else if (n.nodeType === 1) { out.elements.push({ tag: n.tagName, attrs: Object.keys(n.attrs).concat(n.className ? ["class"] : []), ref: n.attrs["data-ref"] || null }); n.children.forEach((c) => walk(c, out)); } else out.text.push(n.data); return out; };
console.log(JSON.stringify(input.map((p) => { const o = walk(renderMarkdown(p), { elements: [], text: [] }); return { payload: p, elements: o.elements, text: o.text.join("") }; })));
