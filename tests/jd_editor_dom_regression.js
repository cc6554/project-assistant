const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

// Reuse the app's existing DOM harness, then add just enough selector and
// tree behavior to exercise the editable JD rows through their real events.
const shimPath = path.join(__dirname, "web_static_dom_regression.js");
const shimSource = fs.readFileSync(shimPath, "utf8");
const marker = "\n(async () => {";
const prefixEnd = shimSource.indexOf(marker);
assert.ok(prefixEnd > 0, "existing DOM shim shape changed");
const shimContext = {
  console,
  setTimeout,
  clearTimeout,
  TextEncoder,
  TextDecoder,
  require,
  __dirname: path.dirname(shimPath),
  __filename: shimPath,
};
shimContext.global = shimContext;
shimContext.File = class File {};
vm.runInNewContext(
  `${shimSource.slice(0, prefixEnd)}\nglobalThis.__jdShim = { Document, Element, storage, response };`,
  shimContext,
  { filename: shimPath },
);
const { Document, Element, storage, response } = shimContext.__jdShim;
const root = path.resolve(__dirname, "..");
const appPath = path.join(root, "src/agent_coach/web_static/app.js");
const htmlPath = path.join(root, "src/agent_coach/web_static/index.html");
const appSource = fs.readFileSync(appPath, "utf8");
const htmlSource = fs.readFileSync(htmlPath, "utf8");
const FileCtor = global.File || class File {};

function camelData(name) {
  return name.slice(5).replace(/-([a-z])/g, (_m, value) => value.toUpperCase());
}

function attrValue(node, name) {
  if (!node) return undefined;
  if (name === "class") return node.className;
  if (name === "id") return node.id;
  if (name === "disabled") return node.disabled ? "" : undefined;
  if (name.startsWith("data-")) return node.dataset[camelData(name)];
  return Object.prototype.hasOwnProperty.call(node.attributes || {}, name) ? node.attributes[name] : undefined;
}

function matchesSimple(node, selector) {
  if (!node || !node.tagName || !selector) return false;
  let candidate = selector.trim();
  const negated = [];
  candidate = candidate.replace(/:not\(([^)]+)\)/g, (_m, inner) => { negated.push(inner); return ""; });
  if (negated.some((item) => matchesSimple(node, item))) return false;
  const attributes = [];
  candidate = candidate.replace(/\[([^\]=]+)(?:\s*=\s*["']?([^\]"']+)["']?)?\]/g, (_m, name, value) => {
    attributes.push({ name, value });
    return "";
  });
  const id = /#([A-Za-z0-9_-]+)/.exec(candidate);
  if (id && node.id !== id[1]) return false;
  const classes = [...candidate.matchAll(/\.([A-Za-z0-9_-]+)/g)].map((item) => item[1]);
  if (classes.some((name) => !node.classList.contains(name))) return false;
  const tag = candidate.replace(/[#.][A-Za-z0-9_-]+/g, "").trim();
  if (tag && tag !== "*" && node.tagName.toLowerCase() !== tag.toLowerCase()) return false;
  return attributes.every(({ name, value }) => {
    const actual = attrValue(node, name);
    if (actual === undefined) return false;
    return value === undefined || String(actual) === String(value);
  });
}

function matchesSelector(node, selector) {
  const parts = String(selector || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return false;
  if (parts.length === 1) return matchesSimple(node, parts[0]);
  if (!matchesSimple(node, parts[parts.length - 1])) return false;
  let parent = node.parentNode;
  for (let index = parts.length - 2; index >= 0; index -= 1) {
    while (parent && !matchesSimple(parent, parts[index])) parent = parent.parentNode;
    if (!parent) return false;
    parent = parent.parentNode;
  }
  return true;
}

function descendants(rootNode) {
  const result = [];
  const visit = (node) => {
    if (!node || !node.children) return;
    node.children.forEach((child) => {
      if (child && child.tagName) {
        result.push(child);
        visit(child);
      }
    });
  };
  visit(rootNode);
  return result;
}

const elementPrototype = Element.prototype;
const classListPrototype = Object.getPrototypeOf(new Document().createElement("div").classList);
classListPrototype.forEach = function forEach(callback) { this.values.forEach(callback); };
elementPrototype.querySelectorAll = function querySelectorAll(selector) {
  const selectors = String(selector || "").split(",").map((item) => item.trim()).filter(Boolean);
  return descendants(this).filter((node) => selectors.some((item) => matchesSelector(node, item)));
};
elementPrototype.querySelector = function querySelector(selector) {
  if (selector === ".activity-pulse") {
    if (!this._activityPulse) this._activityPulse = this.ownerDocument.createElement("span");
    return this._activityPulse;
  }
  return this.querySelectorAll(selector)[0] || new Element(this.ownerDocument, "div");
};
elementPrototype.closest = function closest(selector) {
  let node = this;
  while (node) {
    if (matchesSelector(node, selector)) return node;
    node = node.parentNode;
  }
  return null;
};
elementPrototype.insertBefore = function insertBefore(child, reference) {
  const index = this.children.indexOf(reference);
  if (index < 0) return this.appendChild(child);
  if (child.parentNode) child.parentNode.children = child.parentNode.children.filter((item) => item !== child);
  child.parentNode = this;
  this.children.splice(index, 0, child);
  return child;
};
elementPrototype.getAttribute = function getAttribute(name) { return attrValue(this, name); };
Object.defineProperty(elementPrototype, "parentElement", { get() { return this.parentNode && this.parentNode.tagName ? this.parentNode : null; } });
Object.defineProperty(elementPrototype, "childElementCount", { get() { return this.children.filter((child) => child && child.tagName).length; } });
elementPrototype.contains = function contains(node) {
  let current = node;
  while (current) {
    if (current === this) return true;
    current = current.parentNode;
  }
  return false;
};
Document.prototype.createDocumentFragment = function createDocumentFragment() { return this.createElement("fragment"); };
Document.prototype.querySelectorAll = function querySelectorAll(selector) { return this.body.querySelectorAll(selector); };
Document.prototype.querySelector = function querySelector(selector) { return this.body.querySelector(selector); };
Document.prototype.contains = function contains(node) { return this.body.contains(node) || this.documentElement.contains(node); };
Object.defineProperty(elementPrototype, "childNodes", { configurable: true, get() { return this.children; } });

function clone(value) { return JSON.parse(JSON.stringify(value)); }

function makeJd(index) {
  return {
    job_title: index ? "B 岗位" : "算法工程师",
    company: index ? "B 公司" : "示例科技",
    salary: index ? "20-30K" : null,
    location: index ? "上海" : "深圳",
    team: index ? "B 团队" : "机器人研发组",
    hard_requirements: ["第一条硬性要求", "第二条硬性要求"],
    skill_tags: Array.from({ length: 15 }, (_item, itemIndex) => `技能${itemIndex + 1}`),
    responsibilities: ["负责算法开发与验证。", "记录实验结果并完成联调。"],
    nice_to_haves: ["有真实部署经验。"],
    uncertain_fields: ["salary"],
    raw_summary: "岗位概况与工作方向。",
  };
}

function makeSession(id, index = 0) {
  return {
    id,
    title: id,
    messages: [],
    events: [],
    pending: null,
    queued_calls: 0,
    busy: false,
    execution: null,
    active_skills: [],
    skill_state: { "job-radar": { jd: makeJd(index) } },
  };
}

function installIds(document) {
  for (const match of htmlSource.matchAll(/id="([^"]+)"/g)) document.getElementById(match[1]);
  const steps = ["jd", "profile", "plan", "practice"].map((id) => {
    const button = document.createElement("button");
    button.dataset.step = id;
    button.setAttribute("data-step", id);
    button.id = `jd-test-step-${id}`;
    const status = document.createElement("small");
    status.className = "step-status";
    button.appendChild(status);
    document.getElementById("workflowSteps").appendChild(button);
    return button;
  });
  return steps;
}

async function boot({ saveMode = "success", includeSecondSession = false } = {}) {
  const document = new Document();
  const steps = installIds(document);
  const sessions = { A: makeSession("A", 0) };
  if (includeSecondSession) sessions.B = makeSession("B", 1);
  const calls = [];
  const pending = { value: null };
  const summaries = () => Object.values(sessions).map((session) => ({
    id: session.id,
    title: session.title,
    job_title: session.skill_state["job-radar"].jd.job_title,
    company: session.skill_state["job-radar"].jd.company,
    pending: false,
    queued_calls: 0,
    busy: Boolean(session.busy),
    message_count: session.messages.length,
    active_skills: [],
  }));
  const snapshot = (id) => ({ ...clone(sessions[id]), skill_state: clone(sessions[id].skill_state) });
  async function fetchStub(url, options = {}) {
    const requestPath = String(url).replace(/^\/api/, "");
    calls.push({ path: requestPath, options });
    if (requestPath === "/info") return response({ mode: "demo", workspace: "jd-test", skills: [] });
    if (requestPath === "/sessions") return response({ sessions: summaries() });
    const sessionMatch = requestPath.match(/^\/sessions\/([^/]+)$/);
    if (sessionMatch) return response(snapshot(sessionMatch[1]));
    if (requestPath.startsWith("/files")) return response({ path: ".", entries: [] });
    if (requestPath === "/settings/ui") return response({ theme: "dark" });
    if (requestPath === "/settings/models" || requestPath === "/settings/models/options") return response({ profiles: [], saved_profiles: [] });
    const saveMatch = requestPath.match(/^\/sessions\/([^/]+)\/jd$/);
    if (saveMatch && options.method === "POST") {
      const id = saveMatch[1];
      const body = JSON.parse(options.body || "{}");
      assert.deepStrictEqual(Object.keys(body).sort(), ["confirmed", "jd"]);
      assert.strictEqual(body.confirmed, true);
      assert.ok(Array.isArray(body.jd.hard_requirements));
      if (saveMode === "failure") return response({ detail: "保存失败测试" }, 500);
      const apply = () => {
        sessions[id].skill_state["job-radar"].jd = clone(body.jd);
        sessions[id].title = body.jd.job_title || id;
        return response(snapshot(id));
      };
      if (saveMode === "delayed" && !pending.value) return new Promise((resolve) => { pending.value = { id, resolve, apply }; });
      return apply();
    }
    throw new Error(`unexpected request ${requestPath}`);
  }
  const intervals = [];
  const window = {
    document,
    localStorage: storage(),
    sessionStorage: storage(),
    location: { hash: "", pathname: "/", search: "" },
    history: { replaceState() {} },
    innerWidth: 1280,
    confirm: () => true,
    addEventListener() {},
    removeEventListener() {},
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
    setTimeout,
    clearTimeout,
    setInterval: (fn) => { const id = intervals.length + 1; intervals.push({ id, fn }); return id; },
    clearInterval: (id) => { const index = intervals.findIndex((item) => item.id === id); if (index >= 0) intervals.splice(index, 1); },
    requestAnimationFrame: (fn) => { fn(); return 1; },
    scrollTo() {},
  };
  const context = {
    window,
    document,
    console,
    fetch: fetchStub,
    Headers,
    FormData,
    URL,
    Blob,
    File: FileCtor,
    setTimeout,
    clearTimeout,
    setInterval: window.setInterval,
    clearInterval: window.clearInterval,
    requestAnimationFrame: window.requestAnimationFrame,
    getComputedStyle: window.getComputedStyle,
    navigator: { language: "zh-CN", userAgent: "jd-test", mediaDevices: {} },
    crypto: global.crypto,
    TextEncoder,
    TextDecoder,
  };
  window.fetch = fetchStub;
  window.navigator = context.navigator;
  vm.runInNewContext(appSource, context, { filename: appPath });
  const flush = async () => { for (let index = 0; index < 20; index += 1) await new Promise((resolve) => setTimeout(resolve, 0)); };
  await flush();
  const all = () => descendants(document.body);
  const find = (predicate) => all().find(predicate);
  const jdStep = steps.find((button) => button.dataset.step === "jd");
  jdStep.click();
  await flush();
  return { document, sessions, calls, pending, flush, all, find, steps };
}

function childrenMatching(rootNode, predicate) {
  const result = [];
  const visit = (node) => {
    if (!node || !node.children) return;
    node.children.forEach((child) => {
      if (!child || !child.tagName) return;
      if (predicate(child)) result.push(child);
      visit(child);
    });
  };
  visit(rootNode);
  return result;
}

function textNodes(rootNode, value) {
  return childrenMatching(rootNode, (node) => node.textContent === value);
}

function editorRows(test, field) {
  return test.all().filter((node) => node.dataset && node.dataset.jobEditListRow === field);
}

function editorInput(test, row, tagName) {
  return childrenMatching(row, (node) => node.tagName === tagName)[0];
}

async function openEditor(test) {
  const card = test.find((node) => node.classList && node.classList.contains("jd-card"));
  assert.ok(card, "JD card should render");
  const edit = textNodes(card, "编辑")[0];
  assert.ok(edit, "JD card should expose an edit button");
  edit.click();
  await test.flush();
  assert.strictEqual(test.document.getElementById("jobEditBackdrop").classList.contains("hidden"), false);
}

async function saveEditor(test) {
  test.document.getElementById("jobEditForm").dispatchEvent({ type: "submit" });
  await test.flush();
}

async function run() {
  const basic = await boot();
  const card = basic.find((node) => node.classList && node.classList.contains("jd-card"));
  assert.ok(card, "JD card should render");
  assert.ok(textNodes(card, "岗位卡片（可编辑）").length, "JD card title should match the editable card");
  assert.strictEqual(childrenMatching(card, (node) => node.classList.contains("job-chip")).length, 15, "JD skill tags must keep all entries beyond twelve");
  assert.strictEqual(textNodes(card, "null").length, 0, "null fields must not leak into the card");

  await openEditor(basic);
  assert.strictEqual(editorRows(basic, "hard_requirements").length, 2, "hard requirements must have independent rows");
  assert.strictEqual(editorRows(basic, "responsibilities")[0] && editorInput(basic, editorRows(basic, "responsibilities")[0], "TEXTAREA").tagName, "TEXTAREA", "responsibilities should use independent multiline controls");
  const hardWrapper = basic.find((node) => node.dataset && node.dataset.jobEditList === "hard_requirements");
  const hardRowsBefore = editorRows(basic, "hard_requirements");
  editorInput(basic, hardRowsBefore[1], "TEXTAREA").value = "第二条已修改";
  const add = textNodes(hardWrapper, "添加一项")[0];
  add.click();
  await basic.flush();
  const hardRowsAfterAdd = editorRows(basic, "hard_requirements");
  assert.strictEqual(hardRowsAfterAdd.length, 3, "adding a JD row should append one independent row");
  editorInput(basic, hardRowsAfterAdd[2], "TEXTAREA").value = "新增第三条";
  textNodes(hardRowsAfterAdd[0], "×")[0].click();
  await basic.flush();
  assert.strictEqual(editorRows(basic, "hard_requirements").length, 2, "removing a JD row should remove only that row");
  await saveEditor(basic);
  const saved = basic.sessions.A.skill_state["job-radar"].jd;
  assert.deepStrictEqual(saved.hard_requirements, ["第二条已修改", "新增第三条"]);
  assert.strictEqual(basic.document.getElementById("jobEditBackdrop").classList.contains("hidden"), true, "successful save should close the editor");

  const cancelled = await boot();
  await openEditor(cancelled);
  const titleInput = cancelled.find((node) => node.dataset && node.dataset.jobEditField === "job_title");
  titleInput.value = "取消后的临时标题";
  editorInput(cancelled, editorRows(cancelled, "hard_requirements")[0], "TEXTAREA").value = "取消后的临时要求";
  cancelled.document.getElementById("jobEditCancel").click();
  await cancelled.flush();
  assert.strictEqual(cancelled.sessions.A.skill_state["job-radar"].jd.job_title, "算法工程师", "cancel must leave stored JD untouched");
  await openEditor(cancelled);
  assert.strictEqual(cancelled.find((node) => node.dataset && node.dataset.jobEditField === "job_title").value, "算法工程师", "cancel must discard the editor draft");

  const failed = await boot({ saveMode: "failure" });
  await openEditor(failed);
  failed.find((node) => node.dataset && node.dataset.jobEditField === "job_title").value = "失败不应保存";
  await saveEditor(failed);
  assert.strictEqual(failed.document.getElementById("jobEditBackdrop").classList.contains("hidden"), false, "failed save should keep the editor open");
  assert.ok(failed.document.getElementById("jobEditStatus").textContent.includes("保存失败"), "failed save should show a status message");
  assert.strictEqual(failed.sessions.A.skill_state["job-radar"].jd.job_title, "算法工程师", "failed save must leave stored JD untouched");

  const isolated = await boot({ saveMode: "delayed", includeSecondSession: true });
  await openEditor(isolated);
  isolated.find((node) => node.dataset && node.dataset.jobEditField === "job_title").value = "A 晚到保存";
  await saveEditor(isolated);
  assert.ok(isolated.pending.value, "delayed save should remain pending");
  const bButton = isolated.find((node) => node.dataset && node.dataset.sessionId === "B");
  assert.ok(bButton, "second session should be selectable");
  bButton.click();
  await isolated.flush();
  await openEditor(isolated);
  const bTitle = isolated.find((node) => node.dataset && node.dataset.jobEditField === "job_title");
  assert.strictEqual(bTitle.value, "B 岗位", "switching sessions should open the target draft");
  isolated.pending.value.resolve(isolated.pending.value.apply());
  await isolated.flush();
  assert.strictEqual(isolated.document.getElementById("jobEditBackdrop").classList.contains("hidden"), false, "late A save must not close B's editor");
  assert.strictEqual(isolated.find((node) => node.dataset && node.dataset.jobEditField === "job_title").value, "B 岗位", "late A save must not overwrite B's editor");
  assert.strictEqual(isolated.sessions.B.skill_state["job-radar"].jd.job_title, "B 岗位", "late A save must not mutate B's stored JD");
  console.log("JD editor DOM scenarios passed");
}

run().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });
