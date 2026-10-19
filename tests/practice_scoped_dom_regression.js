const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

// Use the existing browser shim's DOM implementation, while keeping this
// test's service double focused on durable scoped transcripts.
const shimPath = path.join(__dirname, "web_static_dom_regression.js");
const shimSource = fs.readFileSync(shimPath, "utf8");
const marker = "\n(async () => {";
const prefixEnd = shimSource.indexOf(marker);
assert.ok(prefixEnd > 0, "existing DOM shim shape changed");
const shimContext = { console, setTimeout, clearTimeout, TextEncoder, TextDecoder, require, __dirname: path.dirname(shimPath), __filename: shimPath };
shimContext.global = shimContext;
vm.runInNewContext(`${shimSource.slice(0, prefixEnd)}\nglobalThis.__practiceShim = { Document, storage, response };`, shimContext, { filename: shimPath });
const { Document, storage, response } = shimContext.__practiceShim;
Document.prototype.createDocumentFragment = function createDocumentFragment() { return this.createElement("fragment"); };
const elementPrototype = Object.getPrototypeOf(new Document().createElement("div"));
Object.defineProperty(elementPrototype, "childNodes", { configurable: true, get() { return this.children; } });
elementPrototype.contains = function contains(node) { let current = node; while (current) { if (current === this) return true; current = current.parentNode; } return false; };
elementPrototype.closest = function closest(selector) {
  if (selector === "[data-session-id]" && this.dataset && this.dataset.sessionId) return this;
  if (selector === "[data-session-delete]" && this.dataset && this.dataset.sessionDelete) return this;
  if (selector === "[data-action]" && this.dataset && this.dataset.action) return this;
  return this.parentNode ? this.parentNode.closest(selector) : null;
};
Document.prototype.contains = function contains(node) { return this.body.contains(node) || this.documentElement.contains(node); };

const root = path.resolve(__dirname, "..");
const appSource = fs.readFileSync(path.join(root, "src/agent_coach/web_static/app.js"), "utf8");
const htmlSource = fs.readFileSync(path.join(root, "src/agent_coach/web_static/index.html"), "utf8");
const clone = (value) => JSON.parse(JSON.stringify(value));

function installIds(document) {
  for (const match of htmlSource.matchAll(/id="([^"]+)"/g)) document.getElementById(match[1]);
  const steps = ["jd", "profile", "plan", "practice"].map((id) => {
    const button = document.createElement("button"); button.dataset.step = id; button.id = `practice-step-${id}`; document.getElementById("workflowSteps").appendChild(button); return button;
  });
  const originalQueryAll = document.querySelectorAll.bind(document);
  document.querySelectorAll = (selector) => selector === "[data-step]" ? steps : originalQueryAll(selector);
  const chatHome = document.getElementById("chatHome");
  const chatHost = document.getElementById("practiceChatHost");
  const chatColumn = document.createElement("main"); chatColumn.className = "chat-column"; chatHome.appendChild(chatColumn);
  ["chatScroll", "messageList", "approvalRegion", "composerForm", "composerInput", "activityPanel", "busyBar", "attachmentStrip"].forEach((id) => chatColumn.appendChild(document.getElementById(id)));
  const originalQuery = document.querySelector.bind(document);
  document.querySelector = (selector) => selector === ".chat-column" ? chatColumn : originalQuery(selector);
  return { steps };
}

function session(id, selectedKey = "repo:a") {
  return {
    id, title: `会话 ${id}`, messages: [
      { role: "user", content: "主对话上下文" },
      { role: "user", content: "A 项目练习记录", metadata: { scope: "practice", project_key: "repo:a", practice_thread_id: "thread-a" } },
      { role: "assistant", content: "A 项目教练回复", metadata: { scope: "practice", project_key: "repo:a", practice_thread_id: "thread-a" } },
      { role: "user", content: "B 项目旧记录不应出现", metadata: { scope: "practice", project_key: "repo:b", practice_thread_id: "thread-b" } },
      { role: "user", content: "未标记项目的练习记录不应出现", metadata: { scope: "practice", practice_thread_id: "thread-a" } },
    ], events: [
      { type: "completed", message: "主对话事件" },
      { type: "completed", message: "A 项目事件", scope: "practice", project_key: "repo:a", practice_thread_id: "thread-a" },
      { type: "completed", message: "B 项目事件", scope: "practice", project_key: "repo:b", practice_thread_id: "thread-b" },
      { type: "completed", message: "未标记项目的练习事件不应出现", metadata: { scope: "practice", practice_thread_id: "thread-a" } },
    ], pending: null, queued_calls: 0, busy: false, execution: null, active_skills: [],
    skill_state: { "job-radar": {
      jd: { job_title: "算法工程师" }, profile: { skills: [{ name: "Python", evidence: "项目" }] },
      plan: { gaps: [{ requirement: "服务部署", matched_skill: "Python", status: "missing", note: "需要实践" }], phases: [] },
      repos: [], selected_project: { project_key: selectedKey, full_name: selectedKey === "repo:b" ? "example/b" : "example/a" },
      project_history: [
        { project: { project_key: "repo:a", full_name: "example/a" }, progress: [] },
        { project: { project_key: "repo:b", full_name: "example/b" }, progress: [] },
      ], project_filter: { other: "已有服务部署限制", paper_projects: true, rerank: true },
    } },
    practice_threads: [
      { id: "thread-a", project_key: "repo:a", project: { project_key: "repo:a", full_name: "example/a" } },
      { id: "thread-b", project_key: "repo:b", project: { project_key: "repo:b", full_name: "example/b" } },
    ],
    practice_filter: { scope: "main" },
  };
}

async function boot(initial = session("A")) {
  const document = new Document();
  const installed = installIds(document);
  const sessions = { A: clone(initial) };
  const calls = [];
  const summaries = () => Object.values(sessions).map((item) => ({ id: item.id, title: item.title, pending: false, queued_calls: 0, busy: false, message_count: item.messages.length, active_skills: [], has_profile: true }));
  const fetchStub = async (url, options = {}) => {
    const requestPath = String(url).replace(/^\/api/, ""); calls.push({ path: requestPath, options });
    if (requestPath === "/info") return response({ mode: "demo", workspace: "practice-test", skills: [] });
    if (requestPath === "/sessions") return response(options.method === "POST" ? sessions.A : { sessions: summaries() });
    const snapshot = requestPath.match(/^\/sessions\/([^/]+)$/);
    if (snapshot) return response(clone(sessions[snapshot[1]]));
    const message = requestPath.match(/^\/sessions\/([^/]+)\/message$/);
    if (message && options.method === "POST") {
      const id = message[1]; const body = JSON.parse(options.body || "{}");
      sessions[id].messages.push({ role: "user", content: body.text });
      if (body.workflow_action && ["repo:a", "repo:b"].includes(body.workflow_action.project_key)) sessions[id].skill_state["job-radar"].selected_project = { project_key: body.workflow_action.project_key, full_name: body.workflow_action.project_key === "repo:b" ? "example/b" : "example/a" };
      if (body.workflow_action && body.workflow_action.project_filter) sessions[id].skill_state["job-radar"].project_filter = clone(body.workflow_action.project_filter);
      return response({});
    }
    const openPractice = requestPath.match(/^\/sessions\/([^/]+)\/practice$/);
    if (openPractice && options.method === "POST") {
      const id = openPractice[1]; const body = JSON.parse(options.body || "{}"); const key = body.project_key || "repo:a";
      const thread = sessions[id].practice_threads.find((item) => item.project_key === key) || { id: `thread-${key.slice(-1)}`, project_key: key, project: { project_key: key, full_name: key === "repo:b" ? "example/b" : "example/a" } };
      if (!sessions[id].practice_threads.some((item) => item.id === thread.id)) sessions[id].practice_threads.push(thread);
      return response({ practice_thread_id: thread.id, project_key: thread.project_key, project: thread.project });
    }
    const practiceSnapshot = requestPath.match(/^\/sessions\/([^/]+)\/practice\/([^/]+)$/);
    if (practiceSnapshot && options.method !== "POST") {
      const id = practiceSnapshot[1]; const threadId = practiceSnapshot[2]; const messages = sessions[id].messages.filter((message) => message.metadata && message.metadata.practice_thread_id === threadId);
      const events = sessions[id].events.filter((event) => (event.metadata && event.metadata.practice_thread_id === threadId) || event.practice_thread_id === threadId);
      return response({ practice_thread_id: threadId, messages, events, pending: null, execution: {}, queued_calls: Number(sessions[id].queued_calls || 0), busy: Boolean(sessions[id].busy), practice_busy: Boolean(sessions[id].practice_busy), practice_filter: clone(sessions[id].practice_filter || {}) });
    }
    if (requestPath === "/settings/ui") return response({ theme: "dark" });
    if (requestPath === "/settings/models" || requestPath === "/settings/models/options") return response({ profiles: [], saved_profiles: [] });
    if (requestPath.startsWith("/files")) return response({ path: ".", entries: [] });
    throw new Error(`unexpected request ${requestPath}`);
  };
  const window = {
    document, localStorage: storage(), sessionStorage: storage(), location: { hash: "", pathname: "/", search: "" }, history: { replaceState() {} }, innerWidth: 1280,
    confirm: () => true, addEventListener() {}, removeEventListener() {}, matchMedia: () => ({ matches: false, addEventListener() {} }), setTimeout, clearTimeout,
    setInterval: () => 1, clearInterval() {}, requestAnimationFrame: (fn) => { fn(); return 1; }, scrollTo() {}, navigator: { language: "zh-CN", userAgent: "practice-test", mediaDevices: {} },
  };
  const context = { window, document, console, fetch: fetchStub, Headers, FormData, URL, Blob, File: class File {}, setTimeout, clearTimeout, setInterval: window.setInterval, clearInterval: window.clearInterval, requestAnimationFrame: window.requestAnimationFrame, getComputedStyle: () => ({ getPropertyValue: () => "" }), navigator: window.navigator, crypto: global.crypto, TextEncoder, TextDecoder };
  window.fetch = fetchStub;
  vm.runInNewContext(appSource, context, { filename: path.join(root, "src/agent_coach/web_static/app.js") });
  const flush = async () => { for (let index = 0; index < 28; index += 1) await new Promise((resolve) => setTimeout(resolve, 0)); };
  await flush();
  const walk = (node, result = []) => { if (!node || !node.children) return result; for (const child of node.children) { result.push(child); walk(child, result); } return result; };
  return { document, sessions, calls, steps: installed.steps, flush, all: () => walk(document.body) };
}

function bodyOf(call) { return JSON.parse(call.options.body || "{}"); }
function nodeText(node) {
  if (!node) return "";
  const own = String(node.textContent || "");
  return own + (Array.isArray(node.children) ? node.children.map(nodeText).join("") : "");
}

(async () => {
  const first = await boot();
  first.steps.find((step) => step.dataset.step === "practice").click(); await first.flush();
  first.document.getElementById("composerInput").value = "主对话未发送草稿";
  first.document.getElementById("practiceStarter").click(); await first.flush();
  assert.notStrictEqual(first.document.getElementById("composerInput").value, "主对话未发送草稿", "practice composer must not inherit the main draft");
  first.document.getElementById("composerInput").value = "当前项目练习草稿";
  first.document.getElementById("workflowChatClose").click(); await first.flush();
  assert.strictEqual(first.document.getElementById("composerInput").value, "主对话未发送草稿", "closing practice must restore the main draft");
  first.document.getElementById("practiceStarter").click(); await first.flush();
  assert.strictEqual(first.document.getElementById("composerInput").value, "当前项目练习草稿", "reopening practice must restore its own draft");
  const practiceText = nodeText(first.document.getElementById("messageList"));
  assert.match(practiceText, /A 项目练习记录/);
  assert.doesNotMatch(practiceText, /主对话上下文|B 项目旧记录|未标记项目/);
  assert.match(nodeText(first.document.getElementById("activityList")), /A 项目事件/);
  assert.doesNotMatch(nodeText(first.document.getElementById("activityList")), /主对话事件|B 项目事件|未标记项目/);
  assert.ok(first.calls.some((call) => call.path === "/sessions/A/practice/thread-a"), "practice history must come from the scoped endpoint");
  assert.strictEqual(first.calls.filter((call) => call.path === "/sessions/A/practice/thread-a/message").length, 0, "reopening a durable practice thread must not auto-start a new turn");
  assert.strictEqual(first.calls.filter((call) => call.path === "/sessions/A/message").length, 0, "reopening an existing practice history must not duplicate the start turn");

  // A fresh app instance reading the same durable session keeps the scoped
  // transcript after reload; the browser never reconstructs it from a main
  // chat baseline.
  const reloaded = await boot(first.sessions.A);
  reloaded.steps.find((step) => step.dataset.step === "practice").click(); await reloaded.flush();
  reloaded.document.getElementById("practiceStarter").click(); await reloaded.flush();
  assert.match(nodeText(reloaded.document.getElementById("messageList")), /A 项目练习记录/);
  assert.doesNotMatch(nodeText(reloaded.document.getElementById("messageList")), /主对话上下文|B 项目旧记录|未标记项目/);
  reloaded.document.getElementById("composerInput").value = "A 项目未发送草稿";
  reloaded.document.getElementById("composerInput").dispatchEvent({ type: "input" });

  // Switching projects changes the exact key used by the renderer, so A's
  // durable turns cannot leak into B's dedicated practice window.
  const switchButton = reloaded.all().find((node) => node.textContent === "切换并继续");
  assert.ok(switchButton, "project history should expose a switch action"); switchButton.click(); await reloaded.flush();
  reloaded.steps.find((step) => step.dataset.step === "practice").click(); await reloaded.flush();
  reloaded.document.getElementById("practiceStarter").click(); await reloaded.flush();
  assert.doesNotMatch(nodeText(reloaded.document.getElementById("messageList")), /A 项目练习记录/);
  assert.match(nodeText(reloaded.document.getElementById("messageList")), /B 项目旧记录/);

  // The practice queue belongs to the exact current thread when the canonical
  // session filter says so; a main queue must remain an explicit main notice.
  reloaded.sessions.A.queued_calls = 1;
  reloaded.sessions.A.practice_filter = { scope: "practice", project_key: "repo:b", practice_thread_id: "thread-b" };
  reloaded.document.getElementById("refreshButton").click(); await reloaded.flush();
  assert.match(nodeText(reloaded.document.getElementById("approvalRegion")), /继续执行工具队列/);
  assert.doesNotMatch(nodeText(reloaded.document.getElementById("approvalRegion")), /打开主对话处理/);
  assert.match(reloaded.document.getElementById("composerInput").placeholder, /当前项目工具队列/);
  reloaded.sessions.A.practice_filter = { scope: "main" };
  reloaded.document.getElementById("refreshButton").click(); await reloaded.flush();
  assert.match(nodeText(reloaded.document.getElementById("approvalRegion")), /打开主对话处理/);
  assert.match(reloaded.document.getElementById("composerInput").placeholder, /主对话工具队列/);
  reloaded.sessions.A.queued_calls = 0;

  // A practice draft is keyed by project as well as session. Switching to B
  // starts with a clean composer, and returning to A restores its unsent text.
  reloaded.sessions.A.practice_filter = { scope: "practice", project_key: "repo:b", practice_thread_id: "thread-b" };
  reloaded.document.getElementById("refreshButton").click(); await reloaded.flush();
  reloaded.document.getElementById("composerInput").value = "B 项目未发送草稿";
  reloaded.document.getElementById("composerInput").dispatchEvent({ type: "input" });
  const switchToA = reloaded.all().find((node) => node.textContent === "切换并继续");
  assert.ok(switchToA, "project history should expose a switch back action"); switchToA.click(); await reloaded.flush();
  reloaded.document.getElementById("practiceStarter").click(); await reloaded.flush();
  assert.strictEqual(reloaded.document.getElementById("composerInput").value, "A 项目未发送草稿", "returning to A should restore only A's draft");
  const switchToB = reloaded.all().find((node) => node.textContent === "切换并继续");
  assert.ok(switchToB, "project history should expose the other project switch"); switchToB.click(); await reloaded.flush();
  reloaded.document.getElementById("practiceStarter").click(); await reloaded.flush();
  assert.strictEqual(reloaded.document.getElementById("composerInput").value, "B 项目未发送草稿", "returning to B should restore only B's draft");

  const plan = await boot(); plan.steps.find((step) => step.dataset.step === "plan").click(); await plan.flush();
  const other = plan.all().find((node) => node.dataset && node.dataset.jobFilter === "other");
  assert.ok(other, "plan filter should expose an Other freeform field"); other.value = "必须支持 Windows 本地复现"; other.dispatchEvent({ type: "input" });
  const discuss = plan.all().find((node) => node.classList && node.classList.contains("project-filter-discuss")); assert.ok(discuss, "results card should retain its discussion action"); discuss.click(); await plan.flush();
  const discussion = plan.calls.filter((call) => call.path === "/sessions/A/message").pop(); const payload = bodyOf(discussion);
  assert.match(payload.text, /必须支持 Windows 本地复现/);
  assert.strictEqual(payload.workflow_action.project_filter.other, "必须支持 Windows 本地复现");
  await plan.flush();
  assert.strictEqual(plan.document.getElementById("planOther").value, "必须支持 Windows 本地复现", "the confirmed discussion refinement should replace an older plan textarea");
  const primaryOther = plan.document.getElementById("planOther");
  primaryOther.value = "主页面必须保留可复现说明";
  primaryOther.dispatchEvent({ type: "input" });
  const primaryDiscuss = plan.document.getElementById("planOtherDiscuss");
  assert.ok(primaryDiscuss, "main plan page should expose the discussion action"); primaryDiscuss.click(); await plan.flush();
  const primaryCall = plan.calls.filter((call) => call.path === "/sessions/A/message").pop();
  assert.match(bodyOf(primaryCall).text, /主页面必须保留可复现说明/);
  assert.strictEqual(bodyOf(primaryCall).workflow_action.project_filter.other, "主页面必须保留可复现说明");
  await plan.flush();
  assert.strictEqual(plan.document.getElementById("planOther").value, "主页面必须保留可复现说明", "a primary-page confirmation should refresh its own Other field");
  plan.document.getElementById("planPageForm").dispatchEvent({ type: "submit", preventDefault() {} }); await plan.flush();
  const laterPlan = plan.calls.filter((call) => call.path === "/sessions/A/message").pop();
  assert.match(bodyOf(laterPlan).text, /主页面必须保留可复现说明/, "later plan prompts should carry the latest confirmed Other refinement");
  console.log("practice scoped history/reload/project-switch and other-filter behavior passed");
})().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });
