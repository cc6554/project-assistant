const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

// Reuse the existing lightweight DOM classes without changing that regression
// harness.  The original file executes its queue scenarios at the end, so load
// only the shared definitions before its async IIFE.
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
  `${shimSource.slice(0, prefixEnd)}\nglobalThis.__reviewShim = { Document, storage, response };`,
  shimContext,
  { filename: shimPath },
);
const { Document, storage, response } = shimContext.__reviewShim;
// The shared shim predates the markdown renderer's fragment API.  A regular
// shim Element has the appendChild contract needed by this behavior test.
Document.prototype.createDocumentFragment = function createDocumentFragment() {
  return this.createElement("fragment");
};
const shimElementPrototype = Object.getPrototypeOf(new Document().createElement("div"));
Object.defineProperty(shimElementPrototype, "childNodes", {
  configurable: true,
  get() { return this.children; },
});
shimElementPrototype.contains = function contains(node) {
  let current = node;
  while (current) {
    if (current === this) return true;
    current = current.parentNode;
  }
  return false;
};
Document.prototype.contains = function contains(node) {
  return this.body.contains(node) || this.documentElement.contains(node);
};

const root = path.resolve(__dirname, "..");
const appSource = fs.readFileSync(path.join(root, "src/agent_coach/web_static/app.js"), "utf8");
const htmlSource = fs.readFileSync(path.join(root, "src/agent_coach/web_static/index.html"), "utf8");
const FileCtor = global.File || class File {};

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function makeSession(id, question, initial = {}) {
  return {
    id,
    title: id,
    messages: [],
    events: [],
    pending: initial.pending || null,
    queued_calls: Number(initial.queued_calls || 0),
    busy: false,
    execution: null,
    active_skills: [],
    skill_state: {
      "job-radar": {
        profile: { summary: "测试档案", skills: [], open_questions: [question] },
        clarification: { status: "active", open_questions: [question], answered: [] },
      },
    },
  };
}

function installIds(document) {
  for (const match of htmlSource.matchAll(/id="([^"]+)"/g)) document.getElementById(match[1]);
  // The HTML parser is intentionally absent from the existing shim.  Add the
  // workflow step controls needed to open the profile page in this scenario.
  const steps = ["jd", "profile", "plan", "practice"].map((id) => {
    const button = document.createElement("button");
    button.dataset.step = id;
    button.id = `review-step-${id}`;
    document.getElementById("workflowSteps").appendChild(button);
    return button;
  });
  const originalQuery = document.querySelectorAll.bind(document);
  document.querySelectorAll = (selector) => {
    if (selector === "[data-step]") return steps;
    return originalQuery(selector);
  };
  const chatHome = document.getElementById("chatHome");
  const chatHost = document.getElementById("practiceChatHost");
  const chatColumn = document.createElement("main");
  chatColumn.className = "chat-column";
  chatHome.appendChild(chatColumn);
  ["chatScroll", "messageList", "approvalRegion", "composerForm", "composerInput"].forEach((id) => chatColumn.appendChild(document.getElementById(id)));
  const originalQueryOne = document.querySelector.bind(document);
  document.querySelector = (selector) => selector === ".chat-column" ? chatColumn : originalQueryOne(selector);
  return { steps, chatColumn, chatHost };
}

async function boot({ terminal = "confirmed", includeSecondSession = false, withApproval = false } = {}) {
  const document = new Document();
  const installed = installIds(document);
  const steps = installed.steps;
  const question = "是否有线上证据？";
  const sessions = {
    A: makeSession("A", question, withApproval ? {
      pending: { id: "review-approval", tool_name: "write_file", summary: "等待确认", preview: "{}", arguments: {} },
      queued_calls: 1,
    } : {})
  };
  if (includeSecondSession) sessions.B = makeSession("B", "B 会话问题？");
  const summaries = () => Object.values(sessions).map((session) => ({
    id: session.id,
    title: session.title,
    pending: Boolean(session.pending),
    queued_calls: session.queued_calls,
    busy: Boolean(session.busy),
    message_count: session.messages.length,
    active_skills: [],
    has_profile: true,
  }));
  const jobs = new Map();
  const calls = [];
  const intervals = [];
  let jobNumber = 0;
  let currentId = "A";

  const snapshot = (id) => {
    const session = sessions[id];
    return {
      ...clone(session),
      skill_state: clone(session.skill_state),
      messages: [...session.messages],
      events: [...session.events],
    };
  };

  function terminalPayload(id, job) {
    if (job.payload) {
      if (!job.applied && job.payload.result?.status === "completed" && job.payload.result?.metadata?.confirmed === true) {
        const profile = sessions[id].skill_state["job-radar"].profile;
        profile.open_questions = profile.open_questions.filter((item) => item !== job.question);
        sessions[id].skill_state["job-radar"].clarification.open_questions = profile.open_questions.slice();
        sessions[id].skill_state["job-radar"].clarification.status = profile.open_questions.length ? "active" : "completed";
        job.applied = true;
      }
      return job.payload;
    }
    if (job.polls++ === 0) return { status: "running", result: null };
    const questionForJob = job.question;
    const result = terminal === "error"
      ? { status: "error", message: "模型失败" }
      : {
        status: "completed",
        message: "档案已更新",
        metadata: { confirmed: true, question_remaining: false, question: questionForJob },
      };
    if (terminal !== "error") {
      const profile = sessions[id].skill_state["job-radar"].profile;
      profile.open_questions = profile.open_questions.filter((item) => item !== questionForJob);
      sessions[id].skill_state["job-radar"].clarification.open_questions = profile.open_questions.slice();
      sessions[id].skill_state["job-radar"].clarification.status = profile.open_questions.length ? "active" : "completed";
    }
    sessions[id].busy = false;
    job.payload = { status: "completed", result };
    return job.payload;
  }

  async function fetchStub(url, options = {}) {
    const requestPath = String(url).replace(/^\/api/, "");
    calls.push({ path: requestPath, options });
    if (requestPath === "/info") return response({ mode: "demo", workspace: "review", skills: [] });
    if (requestPath === "/sessions") return response({ sessions: summaries() });
    const sessionMatch = requestPath.match(/^\/sessions\/([^/]+)$/);
    if (sessionMatch) return response(snapshot(sessionMatch[1]));
    const jobMatch = requestPath.match(/^\/jobs\/([^/]+)$/);
    if (jobMatch) {
      const job = jobs.get(jobMatch[1]);
      assert.ok(job, `unknown job ${jobMatch[1]}`);
      return response(terminalPayload(job.sessionId, job));
    }
    if (requestPath.startsWith("/files")) return response({ path: ".", entries: [] });
    if (requestPath === "/settings/ui") return response({ theme: "dark" });
    if (requestPath === "/settings/models" || requestPath === "/settings/models/options") return response({ profiles: [], saved_profiles: [] });
    const clarificationMatch = requestPath.match(/^\/sessions\/([^/]+)\/clarification$/);
    if (clarificationMatch && options.method === "POST") {
      const id = clarificationMatch[1];
      const body = JSON.parse(options.body || "{}");
      assert.deepStrictEqual(Object.keys(body).sort(), ["answer", "question"]);
      assert.strictEqual(body.question, sessions[id].skill_state["job-radar"].profile.open_questions[0]);
      const idValue = `review-job-${++jobNumber}`;
      sessions[id].busy = true;
      jobs.set(idValue, { sessionId: id, question: body.question, polls: 0, payload: null });
      return response({ job_id: idValue, session_id: id, action: { kind: "clarification_answer" } }, 202);
    }
    throw new Error(`unexpected request ${requestPath}`);
  }

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
    navigator: { language: "zh-CN", userAgent: "review", mediaDevices: {} },
    crypto: global.crypto,
    TextEncoder,
    TextDecoder,
  };
  window.fetch = fetchStub;
  window.navigator = context.navigator;
  vm.runInNewContext(appSource, context, { filename: path.join(root, "src/agent_coach/web_static/app.js") });

  const flush = async () => { for (let i = 0; i < 18; i += 1) await new Promise((resolve) => setTimeout(resolve, 0)); };
  await flush();
  const walk = (node, result = []) => {
    if (!node || !node.children) return result;
    for (const child of node.children) { result.push(child); walk(child, result); }
    return result;
  };
  const all = () => walk(document.body);
  const findText = (text) => all().find((node) => node.textContent === text);
  const composer = document.getElementById("composerInput");
  composer.value = "保留我的主输入草稿";
  const profileStep = steps.find((button) => button.dataset.step === "profile");
  profileStep.click();
  await flush();
  const answerButton = findText("现在回答");
  assert.ok(answerButton, "profile card should expose clarification answer button");
  answerButton.click();
  await flush();
  return { document, sessions, jobs, calls, intervals, currentId, flush, all, findText, composer, answerButton, chatColumn: installed.chatColumn, chatHost: installed.chatHost, steps };
}

async function run() {
  const review = await boot({ terminal: "error" });
  const modal = review.document.getElementById("clarificationBackdrop");
  assert.strictEqual(modal.classList.contains("hidden"), false, "answer button should open modal");
  const answer = review.document.getElementById("clarificationAnswer");
  const form = review.document.getElementById("clarificationForm");
  answer.value = "部署日志和监控截图";
  answer.dispatchEvent({ type: "input" });
  assert.strictEqual(review.document.getElementById("clarificationSend").disabled, false);
  form.dispatchEvent({ type: "submit" });
  await review.flush();
  assert.ok(review.calls.some((call) => call.path === "/sessions/A/clarification"));
  assert.strictEqual(review.composer.value, "保留我的主输入草稿", "modal submit must not overwrite composer draft");
  review.document.getElementById("clarificationClose").click();
  await review.flush();
  assert.strictEqual(modal.classList.contains("hidden"), true, "closing modal should be immediate");
  assert.ok(review.intervals.length, "background job should keep a polling handle after close");
  await review.intervals[0].fn();
  await review.flush();
  assert.strictEqual(review.sessions.A.skill_state["job-radar"].profile.open_questions.length, 1, "error result must not complete question");
  const reopened = review.findText("现在回答");
  assert.ok(reopened, "failed question remains answerable after modal close");
  reopened.click();
  await review.flush();
  assert.strictEqual(review.document.getElementById("clarificationAnswer").value, "部署日志和监控截图", "failed answer must remain in per-session draft");

  // A second session's late result must not update the first session's modal.
  const isolated = await boot({ terminal: "confirmed", includeSecondSession: true });
  const firstAnswer = isolated.findText("现在回答");
  firstAnswer.click();
  await isolated.flush();
  const isolatedAnswer = isolated.document.getElementById("clarificationAnswer");
  isolatedAnswer.value = "A answer";
  isolatedAnswer.dispatchEvent({ type: "input" });
  isolated.document.getElementById("clarificationForm").dispatchEvent({ type: "submit" });
  await isolated.flush();
  assert.ok(isolated.calls.some((call) => call.path === "/sessions/A/clarification"));
  // Switch away through the session list. The late job remains associated with A.
  const sessionButton = isolated.all().find((node) => node.dataset && node.dataset.sessionId === "B");
  assert.ok(sessionButton, "session list should expose B");
  const jobsBeforeSwitch = isolated.calls.filter((call) => call.path.startsWith("/jobs/")).length;
  sessionButton.click();
  await isolated.flush();
  const beforeLate = isolated.calls.length;
  const job = [...isolated.jobs.values()][0];
  assert.ok(job, "A job should survive switching sessions");
  job.polls = 1;
  job.payload = { status: "completed", result: { status: "completed", metadata: { confirmed: true, question_remaining: false }, message: "A done" } };
  const jobsWhileBSelected = isolated.calls.filter((call) => call.path.startsWith("/jobs/")).length;
  assert.strictEqual(jobsWhileBSelected, jobsBeforeSwitch, "background polling must stop while B is selected");
  const backButton = isolated.all().find((node) => node.dataset && node.dataset.sessionId === "A");
  assert.ok(backButton, "session list should retain A");
  backButton.click();
  await isolated.flush();
  await Promise.all(isolated.intervals.map((item) => item.fn()));
  await isolated.flush();
  const latePolls = isolated.calls.slice(beforeLate).filter((call) => call.path.startsWith("/jobs/"));
  assert.ok(latePolls.length, "returning to A must resume polling an outstanding job");
  assert.strictEqual(isolated.sessions.A.skill_state["job-radar"].profile.open_questions.length, 0, "A result should update A only");
  assert.strictEqual(isolated.sessions.B.skill_state["job-radar"].profile.open_questions.length, 1, "A result must not update B");
  const lingeringConfirmedQuestions = isolated.all().filter((node) => node.className === "clarification-question" && node.textContent === "是否有线上证据？");
  assert.strictEqual(lingeringConfirmedQuestions.length, 0, "confirmed clarification row must be removed after completion");

  // Completion while the answer modal remains open must update both the
  // modal controls and the underlying workflow list.
  const completedModal = await boot({ terminal: "confirmed" });
  const completedAnswer = completedModal.document.getElementById("clarificationAnswer");
  completedAnswer.value = "部署日志和监控截图";
  completedAnswer.dispatchEvent({ type: "input" });
  completedModal.document.getElementById("clarificationForm").dispatchEvent({ type: "submit" });
  await completedModal.flush();
  const completedJob = [...completedModal.jobs.values()][0];
  assert.ok(completedJob, "completed modal scenario should have a job");
  completedJob.payload = { status: "completed", result: { status: "completed", metadata: { confirmed: true, question_remaining: false }, message: "档案已更新" } };
  await Promise.all(completedModal.intervals.map((item) => item.fn()));
  await completedModal.flush();
  assert.strictEqual(completedModal.document.getElementById("clarificationBackdrop").classList.contains("hidden"), false, "completion keeps modal open");
  assert.strictEqual(completedModal.document.getElementById("clarificationSend").disabled, false, "completed modal keeps close action clickable");
  assert.strictEqual(completedModal.document.getElementById("clarificationSend").textContent, "已完成", "completed modal shows completion state");
  const completedModalQuestions = completedModal.all().filter((node) => node.className === "clarification-question" && node.textContent === "是否有线上证据？");
  assert.strictEqual(completedModalQuestions.length, 0, "confirmed row is removed while modal remains open");
  const clarificationCallsBeforeClose = completedModal.calls.filter((call) => call.path === "/sessions/A/clarification").length;
  completedModal.document.getElementById("clarificationForm").dispatchEvent({ type: "submit" });
  await completedModal.flush();
  assert.strictEqual(completedModal.document.getElementById("clarificationBackdrop").classList.contains("hidden"), true, "completed action closes the modal");
  assert.strictEqual(completedModal.calls.filter((call) => call.path === "/sessions/A/clarification").length, clarificationCallsBeforeClose, "completed action must not resend the answer");

  // Workflow pages keep the one live chat column in a modal.  The same
  // composer draft, current session, and approval surface must move together.
  const workflow = await boot({ terminal: "error", withApproval: true });
  workflow.document.getElementById("clarificationClose").click();
  await workflow.flush();
  workflow.composer.value = "页面里的主输入草稿";
  workflow.document.getElementById("backToChat").click();
  await workflow.flush();
  assert.strictEqual(workflow.document.getElementById("workflowChatBackdrop").classList.contains("hidden"), false, "workflow page opens the chat dialog");
  assert.strictEqual(workflow.chatColumn.parentNode, workflow.chatHost, "the existing chat column is mounted in the workflow dialog");
  assert.strictEqual(workflow.document.getElementById("composerInput").value, "页面里的主输入草稿", "workflow dialog preserves the shared composer draft");
  assert.ok(workflow.all().some((node) => node.dataset && node.dataset.action === "approve"), "workflow dialog keeps the approval action visible");
  workflow.document.getElementById("workflowChatClose").click();
  await workflow.flush();
  assert.strictEqual(workflow.document.getElementById("workflowChatBackdrop").classList.contains("hidden"), true, "workflow dialog can close without leaving the page");
  assert.strictEqual(workflow.chatColumn.parentNode, workflow.document.getElementById("chatHome"), "closing returns the same chat column to chat home");

  // The JD page owns an embedded import backdrop. Opening the shared chat
  // temporarily hides it; closing the chat must restore that form and its
  // draft instead of leaving the page without an input surface.
  workflow.steps.find((button) => button.dataset.step === "jd").click();
  await workflow.flush();
  const jdBackdrop = workflow.document.getElementById("jdImportBackdrop");
  const jdSourceText = workflow.document.getElementById("jdSourceText");
  jdSourceText.value = "保留 JD 草稿";
  jdSourceText.dispatchEvent({ type: "input" });
  assert.strictEqual(jdBackdrop.classList.contains("hidden"), false, "JD page keeps its import form visible");
  workflow.document.getElementById("backToChat").click();
  await workflow.flush();
  assert.strictEqual(jdBackdrop.classList.contains("hidden"), true, "shared chat hides the embedded JD form temporarily");
  workflow.document.getElementById("workflowChatClose").click();
  await workflow.flush();
  assert.strictEqual(jdBackdrop.classList.contains("hidden"), false, "closing chat restores the JD form");
  assert.strictEqual(jdSourceText.value, "保留 JD 草稿", "closing chat preserves the JD draft");
  console.log("clarification review DOM scenarios passed");
}

run().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });
