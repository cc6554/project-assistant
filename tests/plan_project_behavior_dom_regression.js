const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

// Reuse the existing lightweight browser shim.  The source file runs its own
// queue scenarios after this prefix, so evaluate only the shared DOM classes.
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
  `${shimSource.slice(0, prefixEnd)}\nglobalThis.__projectShim = { Document, storage, response };`,
  shimContext,
  { filename: shimPath },
);
const { Document, storage, response } = shimContext.__projectShim;
Document.prototype.createDocumentFragment = function createDocumentFragment() {
  return this.createElement("fragment");
};
const elementPrototype = Object.getPrototypeOf(new Document().createElement("div"));
Object.defineProperty(elementPrototype, "childNodes", {
  configurable: true,
  get() { return this.children; },
});
elementPrototype.contains = function contains(node) {
  let current = node;
  while (current) {
    if (current === this) return true;
    current = current.parentNode;
  }
  return false;
};
elementPrototype.closest = function closest(selector) {
  if (selector === "[data-session-id]" && this.dataset && this.dataset.sessionId) return this;
  if (selector === "[data-session-delete]" && this.dataset && this.dataset.sessionDelete) return this;
  if (selector === "[data-action]" && this.dataset && this.dataset.action) return this;
  return this.parentNode ? this.parentNode.closest(selector) : null;
};
Document.prototype.contains = function contains(node) {
  return this.body.contains(node) || this.documentElement.contains(node);
};

const root = path.resolve(__dirname, "..");
const appSource = fs.readFileSync(path.join(root, "src/agent_coach/web_static/app.js"), "utf8");
const htmlSource = fs.readFileSync(path.join(root, "src/agent_coach/web_static/index.html"), "utf8");

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function installIds(document) {
  for (const match of htmlSource.matchAll(/id="([^"]+)"/g)) document.getElementById(match[1]);
  const steps = ["jd", "profile", "plan", "practice"].map((id) => {
    const button = document.createElement("button");
    button.dataset.step = id;
    button.id = `project-step-${id}`;
    document.getElementById("workflowSteps").appendChild(button);
    return button;
  });
  const originalQueryAll = document.querySelectorAll.bind(document);
  document.querySelectorAll = (selector) => selector === "[data-step]" ? steps : originalQueryAll(selector);

  const chatHome = document.getElementById("chatHome");
  const chatHost = document.getElementById("practiceChatHost");
  const chatColumn = document.createElement("main");
  chatColumn.className = "chat-column";
  chatHome.appendChild(chatColumn);
  ["chatScroll", "messageList", "approvalRegion", "composerForm", "composerInput"].forEach((id) => chatColumn.appendChild(document.getElementById(id)));
  const originalQuery = document.querySelector.bind(document);
  document.querySelector = (selector) => selector === ".chat-column" ? chatColumn : originalQuery(selector);
  return { steps, chatHost };
}

function makeSession(id, repos = []) {
  return {
    id,
    title: `测试会话 ${id}`,
    messages: [],
    events: [],
    pending: null,
    queued_calls: 0,
    busy: false,
    execution: null,
    active_skills: [],
    skill_state: {
      "job-radar": {
        jd: null,
        profile: null,
        plan: null,
        repos,
        selected_project: null,
        project_history: [],
      },
    },
  };
}

function readBody(call) {
  return JSON.parse(call.options.body || "{}");
}

function makeFileReader() {
  return class StubFileReader {
    readAsDataURL() {
      this.result = "data:text/plain;base64,cmVwcm8=";
      setTimeout(() => { if (this.onload) this.onload(); }, 0);
    }
  };
}

async function boot({ repos = [], includeSecondSession = false, deferredImport = false } = {}) {
  const document = new Document();
  const installed = installIds(document);
  const sessions = { A: makeSession("A", repos) };
  if (includeSecondSession) sessions.B = makeSession("B");
  const calls = [];
  const intervals = [];
  let pendingImportResolve = null;

  const summaries = () => Object.values(sessions).map((session) => ({
    id: session.id,
    title: session.title,
    pending: Boolean(session.pending),
    queued_calls: session.queued_calls,
    busy: Boolean(session.busy),
    message_count: session.messages.length,
    active_skills: [],
    has_profile: false,
  }));
  const snapshot = (id) => clone(sessions[id]);

  async function fetchStub(url, options = {}) {
    const requestPath = String(url).replace(/^\/api/, "");
    calls.push({ path: requestPath, options });
    if (requestPath === "/info") return response({ mode: "demo", workspace: "project-test", skills: [] });
    if (requestPath === "/sessions" && options.method === "POST") {
      const id = "created";
      sessions[id] = makeSession(id);
      return response(sessions[id]);
    }
    if (requestPath === "/sessions") return response({ sessions: summaries() });
    const sessionMatch = requestPath.match(/^\/sessions\/([^/]+)$/);
    if (sessionMatch) return response(snapshot(sessionMatch[1]));
    const messageMatch = requestPath.match(/^\/sessions\/([^/]+)\/message$/);
    if (messageMatch && options.method === "POST") {
      const id = messageMatch[1];
      const body = readBody({ options });
      sessions[id].messages.push({ role: "user", content: body.text });
      return response({});
    }
    if (requestPath === "/uploads" && options.method === "POST") return response({ path: "uploads/reproduction/README.md" });
    if (requestPath === "/imports/local" && options.method === "POST") {
      const payload = { imported: [{ name: "README.md", path: "imports/reproduction/README.md", source_path: "C:/local/reproduction/README.md", media_type: "text/markdown" }], skipped: [] };
      if (deferredImport) return await new Promise((resolve) => { pendingImportResolve = () => resolve(response(payload)); });
      return response(payload);
    }
    if (requestPath.startsWith("/files")) return response({ path: ".", entries: [] });
    if (requestPath === "/settings/ui") return response({ theme: "dark" });
    if (requestPath === "/settings/models" || requestPath === "/settings/models/options") return response({ profiles: [], saved_profiles: [] });
    throw new Error(`unexpected request ${requestPath}`);
  }

  const FileReader = makeFileReader();
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
    FileReader,
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
    File: class File {},
    FileReader,
    setTimeout,
    clearTimeout,
    setInterval: window.setInterval,
    clearInterval: window.clearInterval,
    requestAnimationFrame: window.requestAnimationFrame,
    getComputedStyle: window.getComputedStyle,
    navigator: { language: "zh-CN", userAgent: "project-test", mediaDevices: {} },
    crypto: global.crypto,
    TextEncoder,
    TextDecoder,
  };
  window.fetch = fetchStub;
  window.navigator = context.navigator;
  vm.runInNewContext(appSource, context, { filename: path.join(root, "src/agent_coach/web_static/app.js") });

  const flush = async () => { for (let index = 0; index < 24; index += 1) await new Promise((resolve) => setTimeout(resolve, 0)); };
  await flush();
  const walk = (node, result = []) => {
    if (!node || !node.children) return result;
    for (const child of node.children) { result.push(child); walk(child, result); }
    return result;
  };
  const all = () => walk(document.body);
  return {
    document,
    sessions,
    calls,
    intervals,
    steps: installed.steps,
    all,
    flush,
    resolveImport: () => { if (pendingImportResolve) { const resolve = pendingImportResolve; pendingImportResolve = null; resolve(); return true; } return false; },
  };
}

function lastCall(scenario, pathValue) {
  const calls = scenario.calls.filter((call) => call.path === pathValue);
  assert.ok(calls.length, `expected ${pathValue}`);
  return calls[calls.length - 1];
}

async function openPlan(scenario) {
  scenario.steps.find((button) => button.dataset.step === "plan").click();
  await scenario.flush();
}

async function registerUrlScenario() {
  const scenario = await boot();
  await openPlan(scenario);
  scenario.document.getElementById("composerInput").value = "保留主输入草稿";
  scenario.document.getElementById("planProjectUrl").value = "https://user:secret@example.com/reproduction?token=private";
  scenario.document.getElementById("planProjectRegister").click();
  await scenario.flush();
  assert.match(scenario.document.getElementById("planPageStatus").textContent, /无凭据/);
  assert.strictEqual(scenario.calls.filter((call) => call.path === "/sessions/A/message").length, 0, "credential-bearing project URL must not reach the model");
  scenario.document.getElementById("planProjectUrl").value = "https://github.com/example/reproduction";
  scenario.document.getElementById("planProjectRegister").click();
  await scenario.flush();
  const body = readBody(lastCall(scenario, "/sessions/A/message"));
  assert.match(body.text, /register_project/);
  assert.match(body.text, /https:\/\/github\.com\/example\/reproduction/);
  assert.doesNotMatch(body.text, /search_projects/);
  assert.strictEqual(scenario.document.getElementById("composerInput").value, "保留主输入草稿", "page action must not overwrite composer draft");
  console.log("project URL registration: current session message sent without external search");
}

async function uploadScenario() {
  const scenario = await boot();
  await openPlan(scenario);
  const input = scenario.document.getElementById("planProjectFileInput");
  input.files = [{ name: "README.md", size: 32, type: "text/markdown" }];
  input.dispatchEvent({ type: "change" });
  await scenario.flush();
  assert.ok(scenario.calls.some((call) => call.path === "/uploads"), "plan upload should use the existing upload endpoint");
  scenario.document.getElementById("planProjectRegister").click();
  await scenario.flush();
  const body = readBody(lastCall(scenario, "/sessions/A/message"));
  assert.match(body.text, /uploads\/reproduction\/README\.md/);
  assert.ok(!scenario.document.getElementById("composerInput").value, "plan upload must not become a composer attachment");
  console.log("project upload: uploaded workspace path routed through project registration");
}

async function localImportScenario() {
  const scenario = await boot();
  await openPlan(scenario);
  scenario.document.getElementById("planProjectLocalImport").click();
  assert.match(scenario.document.getElementById("localImportHint").textContent, /添加到复现项目材料，点击评估后读取/);
  scenario.document.getElementById("localImportPath").value = "C:/local/reproduction";
  scenario.document.getElementById("localImportForm").dispatchEvent({ type: "submit" });
  await scenario.flush();
  assert.ok(scenario.calls.some((call) => call.path === "/imports/local"), "plan local import should use the existing import endpoint");
  scenario.document.getElementById("planProjectRegister").click();
  await scenario.flush();
  const body = readBody(lastCall(scenario, "/sessions/A/message"));
  assert.match(body.text, /imports\/reproduction\/README\.md/);
  assert.match(body.text, /C:\/local\/reproduction\/README\.md/);
  console.log("local import: workspace and source paths routed through project registration");
}

async function switchGuardScenario() {
  const scenario = await boot({ includeSecondSession: true, deferredImport: true });
  await openPlan(scenario);
  scenario.document.getElementById("planProjectLocalImport").click();
  scenario.document.getElementById("localImportPath").value = "C:/local/reproduction";
  scenario.document.getElementById("localImportForm").dispatchEvent({ type: "submit" });
  await scenario.flush();
  assert.ok(scenario.calls.some((call) => call.path === "/imports/local"), "deferred import should be in flight");
  const sessionB = scenario.all().find((node) => node.dataset && node.dataset.sessionId === "B");
  assert.ok(sessionB, "second session should be rendered for the switch guard");
  sessionB.click();
  await scenario.flush();
  assert.ok(scenario.calls.some((call) => call.path === "/sessions/B"), "session switch should load B before the import resolves");
  assert.strictEqual(scenario.resolveImport(), true, "deferred import should expose a resolver");
  await scenario.flush();
  assert.match(scenario.document.getElementById("localImportStatus").textContent, /会话已切换/);
  assert.strictEqual(scenario.calls.filter((call) => call.path === "/sessions/A/message").length, 0, "late import must not send to the old session");
  console.log("session switch guard: late local import was not attached to the new session");
}

async function candidateSelectionScenario() {
  const repos = [
    { project_key: "repo:demo", full_name: "example/demo", html_url: "https://github.com/example/demo", language: "Python", stargazers_count: 17 },
    { project_key: "user:1234567890abcdef", name: "我的本地复现", source_kind: "local_import", source: { kind: "local_import", paths: ["imports/reproduction/README.md"], evidence: ["用户导入的 README"] }, language: "", stargazers_count: 0, user_provided: true },
  ];
  const scenario = await boot({ repos });
  await openPlan(scenario);
  assert.ok(scenario.all().some((node) => node.textContent === "我的本地复现"), "local candidate should use its display name");
  assert.ok(scenario.all().some((node) => node.textContent === "本地导入"), "local candidate should show its source kind");
  assert.strictEqual(scenario.all().filter((node) => node.textContent === "查看公开项目链接 ↗").length, 0, "local candidate must not render an empty URL link");
  assert.strictEqual(scenario.all().filter((node) => node.textContent.includes("0 星标")).length, 0, "local candidate must not render fake stars");
  assert.strictEqual(scenario.all().filter((node) => node.textContent.includes("匹配分：0%")).length, 0, "local candidate must not render the default zero score");
  const selectButton = scenario.all().find((node) => node.textContent === "选用并开始实践");
  assert.ok(selectButton, "candidate card should expose selection action");
  selectButton.click();
  await scenario.flush();
  const body = readBody(lastCall(scenario, "/sessions/A/message"));
  assert.strictEqual(body.workflow_action.project_key, "repo:demo", "candidate selection must carry project_key");
  assert.match(body.text, /project_key=repo:demo/);
  console.log("candidate selection: project_key carried with no local fake star/link");
}

(async () => {
  await registerUrlScenario();
  await uploadScenario();
  await localImportScenario();
  await switchGuardScenario();
  await candidateSelectionScenario();
  console.log("plan/project behavioral DOM scenarios passed");
})().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });
