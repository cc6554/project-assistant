const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const projectRoot = path.resolve(__dirname, "..");
const appPath = path.join(projectRoot, "src/agent_coach/web_static/app.js");
const htmlPath = path.join(projectRoot, "src/agent_coach/web_static/index.html");
const FileCtor = global.File || class File {};

class ClassList {
  constructor(owner) { this.owner = owner; this.values = new Set(); }
  sync(value) { this.values = new Set(String(value || "").split(/\s+/).filter(Boolean)); }
  add(...values) { values.forEach((value) => this.values.add(String(value))); this.owner._className = [...this.values].join(" "); }
  remove(...values) { values.forEach((value) => this.values.delete(String(value))); this.owner._className = [...this.values].join(" "); }
  contains(value) { return this.values.has(String(value)); }
  toggle(value, force) {
    const present = this.contains(value);
    const next = force === undefined ? !present : Boolean(force);
    if (next) this.add(value); else this.remove(value);
    return next;
  }
}

class TextNode {
  constructor(text) { this.textContent = String(text); this.parentNode = null; }
}

class Element {
  constructor(document, tagName = "div") {
    this.ownerDocument = document;
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this._className = "";
    this.classList = new ClassList(this);
    this.textContent = "";
    this.innerHTML = "";
    this.value = "";
    this.type = "text";
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.files = [];
    this.scrollHeight = 0;
    this.scrollTop = 0;
    this.listeners = new Map();
  }
  get id() { return this._id || ""; }
  set id(value) { this._id = String(value); if (this.ownerDocument) this.ownerDocument.elements.set(this._id, this); }
  get className() { return this._className; }
  set className(value) { this._className = String(value || ""); this.classList.sync(this._className); }
  appendChild(child) {
    if (child == null) return child;
    if (typeof child === "string") child = this.ownerDocument.createTextNode(child);
    child.parentNode = this; this.children.push(child); return child;
  }
  append(...children) { children.forEach((child) => this.appendChild(child)); }
  prepend(...children) { children.reverse().forEach((child) => { if (child == null) return; if (typeof child === "string") child = this.ownerDocument.createTextNode(child); child.parentNode = this; this.children.unshift(child); }); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((child) => child !== this); this.parentNode = null; }
  addEventListener(name, handler) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(handler); }
  removeEventListener(name, handler) { this.listeners.set(name, (this.listeners.get(name) || []).filter((item) => item !== handler)); }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "class") this.className = value;
    if (name === "id") this.id = value;
    if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-([a-z])/g, (_m, c) => c.toUpperCase())] = String(value);
  }
  toggleAttribute(name, force) {
    const next = force === undefined ? !Object.prototype.hasOwnProperty.call(this.attributes, name) : Boolean(force);
    if (next) this.setAttribute(name, ""); else this.removeAttribute(name);
    return next;
  }
  removeAttribute(name) { delete this.attributes[name]; }
  querySelector(selector) {
    if (selector === ".activity-pulse") {
      if (!this._activityPulse) this._activityPulse = new Element(this.ownerDocument, "span");
      return this._activityPulse;
    }
    return new Element(this.ownerDocument, "div");
  }
  querySelectorAll() { return []; }
  closest(selector) { return selector === "[data-action]" && this.dataset.action ? this : this.parentNode ? this.parentNode.closest(selector) : null; }
  focus() { this.ownerDocument.activeElement = this; }
  blur() {}
  setSelectionRange() {}
  scrollIntoView() {}
  getBoundingClientRect() { return { width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0 }; }
  dispatchEvent(event) {
    const current = { ...event, target: event.target || this, currentTarget: this, preventDefault: event.preventDefault || (() => {}), stopPropagation: event.stopPropagation || (() => {}) };
    for (const handler of this.listeners.get(event.type) || []) handler(current);
    if (this.parentNode) this.parentNode.dispatchEvent(current);
    return true;
  }
  click() { this.dispatchEvent({ type: "click", target: this }); }
}

class Document {
  constructor() {
    this.elements = new Map();
    this.documentElement = new Element(this, "html");
    this.body = new Element(this, "body");
    this.activeElement = this.body;
    this.visibilityState = "visible";
    this.listeners = new Map();
  }
  createElement(tagName) { return new Element(this, tagName); }
  createTextNode(text) { return new TextNode(text); }
  getElementById(id) {
    if (!this.elements.has(id)) { const element = new Element(this); element.id = id; this.body.appendChild(element); }
    return this.elements.get(id);
  }
  querySelectorAll() { return []; }
  querySelector() { return new Element(this, "div"); }
  addEventListener(name, handler) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(handler); }
  removeEventListener(name, handler) { this.listeners.set(name, (this.listeners.get(name) || []).filter((item) => item !== handler)); }
  dispatchEvent(event) { for (const handler of this.listeners.get(event.type) || []) handler({ ...event, target: this, currentTarget: this }); }
}

function storage() {
  const values = new Map();
  return { getItem: (key) => values.has(key) ? values.get(key) : null, setItem: (key, value) => values.set(key, String(value)), removeItem: (key) => values.delete(key) };
}

function response(payload, status = 200) {
  return { ok: status >= 200 && status < 300, status, headers: { get: () => "application/json" }, json: async () => payload, text: async () => JSON.stringify(payload) };
}

async function runScenario(name, { pending = null, queue = 0, busy = false }) {
  const document = new Document();
  const idSource = fs.readFileSync(htmlPath, "utf8").matchAll(/id="([^"]+)"/g);
  for (const match of idSource) document.getElementById(match[1]);
  const intervals = [];
  const session = {
    id: "session-1", title: "测试会话", messages: [], events: [], pending, busy,
    queued_calls: queue, active_skills: [], skill_state: { "job-radar": {} }, execution: null,
  };
  const jobs = new Map();
  let nextJob = 1;
  const summary = { id: session.id, title: session.title, pending: Boolean(pending), queued_calls: queue, busy, message_count: 0, active_skills: [] };
  const window = {
    document, localStorage: storage(), sessionStorage: storage(), location: { hash: "", pathname: "/", search: "" },
    history: { replaceState() {} }, innerWidth: 1280, confirm: () => true,
    listeners: new Map(),
    addEventListener(name, handler) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(handler); },
    removeEventListener(name, handler) { this.listeners.set(name, (this.listeners.get(name) || []).filter((item) => item !== handler)); },
    dispatchEvent(event) { for (const handler of this.listeners.get(event.type) || []) handler({ ...event, target: this, currentTarget: this }); },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
    setTimeout, clearTimeout,
    setInterval: (fn) => { const id = intervals.length + 1; intervals.push({ id, fn }); return id; },
    clearInterval: (id) => { const index = intervals.findIndex((item) => item.id === id); if (index >= 0) intervals.splice(index, 1); },
    requestAnimationFrame: (fn) => { fn(); return 1; },
    scrollTo() {},
  };
  const calls = [];
  const snapshot = () => ({ ...session, pending: session.pending ? { ...session.pending } : null, messages: [...session.messages], events: [...session.events], active_skills: [...session.active_skills], skill_state: { ...session.skill_state } });
  const refreshSummary = () => { summary.pending = Boolean(session.pending); summary.queued_calls = session.queued_calls; summary.busy = Boolean(session.busy); };
  async function fetchStub(url, options = {}) {
    const path = String(url).replace(/^\/api/, "");
    calls.push({ path, options });
    if (path === "/info") return response({ mode: "demo", workspace: "test", skills: [] });
    if (path === "/sessions") { refreshSummary(); return response({ sessions: [{ ...summary }] }); }
    if (path === "/sessions/session-1") { refreshSummary(); return response(snapshot()); }
    if (path.startsWith("/jobs/")) return response(jobs.get(path.slice("/jobs/".length)) || { status: "failed", error: "missing fake job" });
    if (path.startsWith("/files")) return response({ path: ".", entries: [] });
    if (path === "/settings/ui") return response({ theme: "dark" });
    if (path === "/settings/models") return response({ profiles: [] });
    if (path === "/settings/models/options") return response({ saved_profiles: [] });
    if (options.method === "POST" && (path === "/sessions/session-1/continue" || path === "/sessions/session-1/approve")) {
      const jobId = `job-${nextJob++}`;
      if (path.endsWith("/approve")) {
        const body = JSON.parse(options.body || "{}");
        if (!session.pending || String(body.id) !== String(session.pending.id)) return response({ detail: "approval mismatch" }, 409);
        session.pending = null;
      }
      session.queued_calls = 0;
      session.busy = false;
      refreshSummary();
      jobs.set(jobId, { status: "completed", result: { status: "completed", message: `${path.endsWith("/continue") ? "continue" : "approve"} fake completed` } });
      return response({ job_id: jobId });
    }
    throw new Error(`unexpected request ${path}`);
  }
  const context = {
    window, document, console, fetch: fetchStub, Headers, FormData, URL, Blob, File: FileCtor,
    setTimeout, clearTimeout, setInterval: window.setInterval, clearInterval: window.clearInterval,
    requestAnimationFrame: window.requestAnimationFrame, getComputedStyle: window.getComputedStyle,
    navigator: { language: "zh-CN", userAgent: "test", mediaDevices: {} },
    crypto: global.crypto, TextEncoder, TextDecoder,
  };
  window.fetch = fetchStub; window.navigator = context.navigator;
  vm.runInNewContext(fs.readFileSync(appPath, "utf8"), context, { filename: appPath });
  const flush = async () => { for (let i = 0; i < 12; i++) await new Promise((resolve) => setTimeout(resolve, 0)); };
  await flush();
  const region = document.getElementById("approvalRegion");
  const actions = [];
  const visit = (node) => { if (!node || !node.children) return; for (const child of node.children) { if (child.dataset && child.dataset.action) actions.push(child); visit(child); } };
  visit(region);
  assert.ok(document.getElementById("appShell"));
  const result = {
    name, actions, activityStatus: document.getElementById("activityStatus").textContent,
    activityHidden: document.getElementById("activityPanel").classList.contains("hidden"),
    busyHidden: document.getElementById("busyBar").classList.contains("hidden"),
    intervals: intervals.length, calls,
  };
  result.refocusMainWindow = async () => {
    const input = document.getElementById("composerInput");
    input.value = "parent draft must remain";
    const before = calls.length;
    window.dispatchEvent({ type: "focus" });
    await flush();
    assert.ok(calls.slice(before).some((call) => call.path === "/sessions/session-1"), "main focus should refresh the current session");
    assert.strictEqual(input.value, "parent draft must remain", "main focus refresh must preserve the composer draft");
  };
  result.click = async (actionName) => {
    const action = actions.find((item) => item.dataset.action === actionName);
    assert.ok(action, `${name} should have ${actionName} action`);
    action.click();
    await flush();
  };
  result.queue = () => session.queued_calls;
  return result;
}

(async () => {
  const pending = await runScenario("pending+queue", { pending: { id: "approval-1", tool_name: "write", summary: "等待审批", preview: "{}", arguments: {} }, queue: 1 });
  const approve = pending.actions.find((item) => item.dataset.action === "approve");
  const reject = pending.actions.find((item) => item.dataset.action === "reject");
  assert.ok(approve && reject, "pending approval actions should render");
  assert.strictEqual(approve.disabled, false, "queued tail must not disable approve");
  assert.strictEqual(reject.disabled, false, "queued tail must not disable reject");
  assert.strictEqual(pending.actions.some((item) => item.dataset.action === "continue"), false, "pending approval owns the recovery surface");
  await pending.click("approve");
  assert.ok(pending.calls.some((call) => call.path === "/sessions/session-1/approve" && JSON.parse(call.options.body).id === "approval-1"), "approve button must POST the exact approval id");
  assert.strictEqual(pending.queue(), 0, "approved queue tail should be drained");
  console.log("pending+queue: approve POST matched approval-1; queued_calls=0");

  const queued = await runScenario("idle-queue", { queue: 1 });
  const resume = queued.actions.find((item) => item.dataset.action === "continue");
  assert.ok(resume, "idle queue should render a continue action");
  assert.strictEqual(resume.disabled, false, "idle queue continue must be enabled");
  assert.strictEqual(queued.activityStatus, "待继续");
  assert.strictEqual(queued.activityHidden, false);
  assert.strictEqual(queued.busyHidden, true);
  assert.strictEqual(queued.intervals, 0, "idle queue must not start polling");
  await queued.click("continue");
  assert.ok(queued.calls.some((call) => call.path === "/sessions/session-1/continue"), "continue button must POST the continue action");
  assert.strictEqual(queued.queue(), 0, "continue should drain the persisted queue");
  console.log("idle queue: continue POST matched; queued_calls=0; no polling before click");

  const live = await runScenario("live-queue", { queue: 1, busy: true });
  assert.strictEqual(live.actions.some((item) => item.dataset.action === "continue"), false, "live queue must not show an interruption card");
  assert.strictEqual(live.activityStatus, "处理中");
  assert.ok(live.intervals >= 1, "live execution may poll");
  console.log("live queue: recovery action absent; activity=处理中; polling allowed");
  await live.refocusMainWindow();
  console.log("main focus: current session refreshed; composer draft preserved");
  console.log("web static DOM queue regression scenarios passed");
})().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });
