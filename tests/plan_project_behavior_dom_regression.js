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
  document.getElementById("workflowChatBackdrop").classList.add("hidden");
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

async function boot({ repos = [], includeSecondSession = false, deferredImport = false, generatedRepos = null } = {}) {
  const document = new Document();
  const installed = installIds(document);
  const sessions = { A: makeSession("A", repos) };
  if (includeSecondSession) sessions.B = makeSession("B");
  const calls = [];
  const intervals = [];
  let pendingImportResolve = null;
  let deferSessionA = false;
  let pendingSessionResolve = null;
  let generationFinished = false;

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
    if (sessionMatch) {
      if (sessionMatch[1] === "A" && deferSessionA) {
        deferSessionA = false;
        return await new Promise((resolve) => { pendingSessionResolve = () => resolve(response(snapshot(sessionMatch[1]))); });
      }
      return response(snapshot(sessionMatch[1]));
    }
    const messageMatch = requestPath.match(/^\/sessions\/([^/]+)\/message$/);
    if (messageMatch && options.method === "POST") {
      const id = messageMatch[1];
      const body = readBody({ options });
      sessions[id].messages.push({ role: "user", content: body.text });
      if (generatedRepos && body.workflow_action?.step === "plan") {
        sessions[id].busy = true;
        return response({ job_id: "plan-job" });
      }
      return response({});
    }
    if (requestPath === "/jobs/plan-job") return response({ status: generationFinished ? "completed" : "running" });
    const selectMatch = requestPath.match(/^\/sessions\/([^/]+)\/practice\/select$/);
    if (selectMatch && options.method === "POST") {
      const session = sessions[selectMatch[1]];
      const job = session.skill_state["job-radar"];
      const key = readBody({ options }).project_key;
      job.selected_project = [...job.repos, ...(job.user_projects || [])].find((repo) => repo.project_key === key);
      return response(clone(session));
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
    deferSessionRefresh: () => { deferSessionA = true; return true; },
    resolveSessionRefresh: () => { if (pendingSessionResolve) { const resolve = pendingSessionResolve; pendingSessionResolve = null; resolve(); return true; } return false; },
    finishGeneration() {
      generationFinished = true;
      sessions.A.busy = false;
      const job = sessions.A.skill_state["job-radar"];
      job.plan = { phases: [{ phase: "第一阶段" }] };
      job.repos = generatedRepos;
      for (const timer of [...intervals]) timer.fn();
    },
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

async function sessionComposerSwitchScenario() {
  const scenario = await boot({ includeSecondSession: true });
  await openPlan(scenario);
  const input = scenario.document.getElementById("composerInput");
  input.value = "A 未发送草稿";
  input.dispatchEvent({ type: "input" });
  const projectUrl = scenario.document.getElementById("planProjectUrl");
  projectUrl.value = "https://github.com/example/a";
  const sessionB = scenario.all().find((node) => node.dataset && node.dataset.sessionId === "B");
  assert.ok(sessionB, "second session should be rendered for the composer guard");
  sessionB.click();

  // The B snapshot is still in flight here. Its keyed draft must replace A's
  // visible draft immediately, and the shared composer must reject submits
  // until B has an authoritative session snapshot.
  assert.strictEqual(input.value, "", "switching sessions must restore B's draft before its snapshot resolves");
  assert.strictEqual(projectUrl.value, "", "switching sessions must restore B's project material draft before its snapshot resolves");
  assert.strictEqual(input.disabled, true, "composer must stay disabled while the target session loads");
  input.value = "加载期间不应发送";
  scenario.document.getElementById("composerForm").dispatchEvent({ type: "submit", preventDefault() {} });
  await scenario.flush();
  assert.strictEqual(scenario.calls.filter((call) => call.path === "/sessions/B/message").length, 0, "a submit during session loading must not reach B");
  assert.strictEqual(input.disabled, false, "composer should re-enable after B loads");
  assert.strictEqual(input.value, "", "B's empty keyed draft must remain after its snapshot resolves");

  projectUrl.value = "https://github.com/example/b";
  const sessionA = scenario.all().find((node) => node.dataset && node.dataset.sessionId === "A");
  assert.ok(sessionA, "original session should remain available after the switch");
  sessionA.click();
  await scenario.flush();
  assert.strictEqual(projectUrl.value, "https://github.com/example/a", "A's project material draft must return with A");
  const sessionBAgain = scenario.all().find((node) => node.dataset && node.dataset.sessionId === "B");
  assert.ok(sessionBAgain, "B should remain available after restoring A");
  sessionBAgain.click();
  await scenario.flush();
  assert.strictEqual(projectUrl.value, "https://github.com/example/b", "B must restore only its own project material draft");
  console.log("session composer guard: old draft was cleared and loading submit was rejected");
}

async function crossSessionProjectHistoryScenario() {
  const scenario = await boot({ includeSecondSession: true });
  const jobState = scenario.sessions.A.skill_state["job-radar"];
  jobState.selected_project = { project_key: "repo:a", full_name: "example/a", language: "Python" };
  jobState.project_history = [
    { project: jobState.selected_project, progress: [{ status: "in_progress", note: "A 当前记录" }] },
    { project: { project_key: "repo:old", full_name: "example/old" }, progress: [] },
  ];
  scenario.document.getElementById("refreshButton").click();
  await scenario.flush();
  await openPlan(scenario);
  assert.ok(scenario.all().some((node) => node.textContent === "example/old"), "A should render its project history");
  assert.ok(scenario.all().some((node) => node.textContent === "切换并继续"), "A should expose a switch action for its history entry");

  // A refresh is left in flight while the user moves to B. The delayed A
  // response must not repopulate B's project history or action buttons.
  scenario.deferSessionRefresh();
  scenario.document.getElementById("refreshButton").click();
  const sessionB = scenario.all().find((node) => node.dataset && node.dataset.sessionId === "B");
  assert.ok(sessionB, "B should be available for the cross-session switch");
  sessionB.click();
  await scenario.flush();
  assert.ok(scenario.calls.some((call) => call.path === "/sessions/B"), "B snapshot should load while A refresh is pending");
  assert.strictEqual(scenario.all().some((node) => node.textContent === "example/old"), false, "B must not render A's project history");
  assert.strictEqual(scenario.all().some((node) => node.textContent === "切换并继续"), false, "B must not expose A's project switch action");

  assert.strictEqual(scenario.resolveSessionRefresh(), true, "the delayed A refresh should be resolvable");
  await scenario.flush();
  assert.strictEqual(scenario.all().some((node) => node.textContent === "example/old"), false, "late A refresh must not repopulate B");
  assert.strictEqual(scenario.calls.filter((call) => call.path === "/sessions/B/message").length, 0, "B must not send an A project switch action");

  const sessionA = scenario.all().find((node) => node.dataset && node.dataset.sessionId === "A");
  assert.ok(sessionA, "A should remain available after the delayed refresh");
  sessionA.click();
  await scenario.flush();
  assert.ok(scenario.all().some((node) => node.textContent === "example/old"), "returning to A should restore A's project history");
  assert.ok(scenario.all().some((node) => node.textContent === "切换并继续"), "returning to A should restore its project switch action");
  console.log("cross-session project history: delayed A refresh stayed out of B");
}

async function candidateSelectionScenario() {
  const repos = [
    { project_key: "repo:demo", full_name: "example/demo", html_url: "https://github.com/example/demo", language: "Python", stargazers_count: 17 },
    { project_key: "user:1234567890abcdef", name: "我的本地复现", source_kind: "local_import", source: { kind: "local_import", paths: ["imports/reproduction/README.md"], evidence: ["用户导入的 README"] }, language: "", stargazers_count: 0, user_provided: true },
  ];
  const scenario = await boot({ repos });
  scenario.steps.find((button) => button.dataset.step === "practice").click();
  await scenario.flush();
  assert.strictEqual(scenario.document.getElementById("practiceStarter").disabled, true, "practice must wait for an explicit selection");
  assert.ok(scenario.all().some((node) => node.textContent === "我的本地复现"), "local candidate should use its display name");
  assert.ok(scenario.all().some((node) => node.textContent === "本地导入"), "local candidate should show its source kind");
  assert.strictEqual(scenario.all().filter((node) => node.textContent === "查看公开项目链接 ↗").length, 0, "local candidate must not render an empty URL link");
  assert.strictEqual(scenario.all().filter((node) => node.textContent.includes("0 星标")).length, 0, "local candidate must not render fake stars");
  assert.strictEqual(scenario.all().filter((node) => node.textContent.includes("匹配分：0%")).length, 0, "local candidate must not render the default zero score");
  const candidates = scenario.document.getElementById("practicePageResults");
  const selectButton = scenario.all().find((node) => node.textContent === "选择项目" && candidates.contains(node));
  assert.ok(selectButton, "candidate card should expose selection action");
  selectButton.click();
  await scenario.flush();
  const body = readBody(lastCall(scenario, "/sessions/A/practice/select"));
  assert.deepStrictEqual(body, { project_key: "repo:demo" }, "candidate selection must send only the selected key");
  assert.ok(!scenario.calls.some((call) => /\/message$|\/practice$/.test(call.path)), "selection must stay out of the chat and wait for the practice start click");
  assert.ok(scenario.document.getElementById("workflowChatBackdrop").classList.contains("hidden"));
  assert.strictEqual(scenario.document.getElementById("practiceStarter").disabled, false);
  console.log("candidate selection: practice shows candidates and selects directly without a chat turn");
}

async function planHandoffScenario({ navigateAway = false } = {}) {
  const scenario = await boot({ generatedRepos: [{ project_key: "repo:generated", full_name: "example/generated" }] });
  const job = scenario.sessions.A.skill_state["job-radar"];
  job.jd = { job_title: "测试岗位" };
  job.profile = { summary: "测试档案" };
  scenario.document.getElementById("refreshButton").click();
  await scenario.flush();
  await openPlan(scenario);
  scenario.document.getElementById("planPageForm").dispatchEvent({ type: "submit", preventDefault() {} });
  await scenario.flush();
  assert.match(readBody(lastCall(scenario, "/sessions/A/message")).text, /“Agent 实操”页面点击选择/);
  assert.ok(scenario.document.getElementById("workflowChatBackdrop").classList.contains("hidden"), "generation must stay outside the main chat dialog");
  if (navigateAway) scenario.steps.find((button) => button.dataset.step === "profile").click();
  scenario.finishGeneration();
  await scenario.flush();
  const title = scenario.document.getElementById("workflowPageTitle");
  if (navigateAway) {
    assert.match(title.textContent, /技能档案/, "a result must not change the user's new page");
    scenario.steps.find((button) => button.dataset.step === "practice").click();
  }
  assert.match(title.textContent, /Agent 实操/);
  const results = scenario.document.getElementById("practicePageResults");
  assert.ok(scenario.all().some((node) => node.textContent === "example/generated" && results.contains(node)));
  assert.ok(!scenario.calls.some((call) => call.path.includes("/practice/")), "generation must wait for a candidate click");
  console.log(`plan handoff: generated candidates in practice (${navigateAway ? "manual navigation preserved" : "automatic navigation"})`);
}

(async () => {
  await registerUrlScenario();
  await uploadScenario();
  await localImportScenario();
  await switchGuardScenario();
  await sessionComposerSwitchScenario();
  await crossSessionProjectHistoryScenario();
  await candidateSelectionScenario();
  await planHandoffScenario();
  await planHandoffScenario({ navigateAway: true });
  console.log("plan/project behavioral DOM scenarios passed");
})().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });
