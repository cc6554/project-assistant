const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const projectRoot = path.resolve(__dirname, "..");
const appPath = path.join(projectRoot, "src/agent_coach/web_static/app.js");
const htmlPath = path.join(projectRoot, "src/agent_coach/web_static/index.html");
const cssPath = path.join(projectRoot, "src/agent_coach/web_static/styles.css");
const staticHtml = fs.readFileSync(htmlPath, "utf8");
const staticApp = fs.readFileSync(appPath, "utf8");
const staticCss = fs.readFileSync(cssPath, "utf8");
assert.doesNotMatch(staticHtml, /class="breadcrumbs"/, "topbar breadcrumbs should stay removed");
assert.match(staticHtml, /class="sr-only"[^>]*id="globalModelStatus"/, "global model status should not render as a visible marker");
assert.match(staticHtml, /class="icon-button inspector-toggle"[^>]*id="inspectorToggle"/, "inspector control should live in the fixed topbar slot");
assert.match(staticHtml, /id="workspacePickerButton"/, "workspace switching should expose the native folder picker button");
assert.match(staticHtml, /id="workspaceSwitchPath"[^>]*data-voice-exclude="true"/, "workspace paths should be excluded from voice autofill");
assert.match(staticHtml, /id="workflowChatTitle">简历项目助手对话<\/h2>/, "workflow chat should use the unified product name");
assert.match(staticHtml, /class="brand-name">简历项目助手<\/div>/, "sidebar should use the unified product name");
assert.match(staticApp, /pick_workspace_folder/, "workspace switching should use the native pick_workspace_folder bridge when available");
assert.doesNotMatch(staticApp, /nativeApi\.workspace_picker/, "workspace switching should not call the stale workspace_picker bridge name");
assert.match(staticApp, /pickerRequestSerial/, "workspace picker should invalidate late native responses after modal lifecycle changes");
assert.match(staticApp, /function syncInspectorToggle\([\s\S]*?hiddenByWorkflowPage[\s\S]*?workflow-inspector-overlay/, "inspector toggle state should follow the workflow overlay visibility");
assert.match(staticApp, /const expanded = !pane\.classList\.contains\("collapsed"\) && !hiddenByWorkflowPage;/, "inspector aria-expanded should reflect actual workflow panel visibility");
assert.match(staticApp, /if \(!state\.pageView\) \{\s*syncInspectorToggle\(\);\s*return;\s*\}/, "returning to the main chat should resync inspector toggle state");
assert.match(staticApp, /简历项目助手对话/, "native workflow chat fallback should use the unified product name");
assert.doesNotMatch(staticCss, /@media\s*\(hover:\s*none\)/, "desktop pywebview must not force session actions visible from hover capability detection");
assert.match(staticCss, /\.session-row:hover[^\n]*\.session-action/, "session actions should appear for the whole row on hover");
assert.match(staticCss, /\.session-row:has\(:focus-visible\)/, "session actions should appear for keyboard focus");
assert.match(staticCss, /\.inspector-toggle\s*\{\s*position:\s*absolute/, "inspector control should stay anchored in the topbar");
assert.match(staticCss, /\.pane-close\s*\{\s*display:\s*none\s*!important/, "the inspector header should not add a second close-control position");
assert.match(staticCss, /\.model-settings-modal[^\n]*background:\s*var\(--panel\)/, "connection settings should use the active panel color");
assert.match(staticCss, /html\[data-theme="dark"\] \.model-settings-modal/, "dark connection settings theme should remain explicitly readable");
assert.match(staticCss, /html\[data-theme="light"\] \.model-settings-modal/, "light connection settings theme should remain explicitly readable");
assert.match(staticHtml, /id="sessionRenameBackdrop"/, "session rename dialog should be present");
assert.match(staticHtml, /id="modelProfileChoice"/, "API settings manager should expose an existing-profile selector");
assert.match(staticHtml, /id="githubProfileChoice"/, "GitHub settings manager should expose an existing-profile selector");
assert.match(staticHtml, /id="githubProfileForm"/, "GitHub token creation should use an independent dialog form");
assert.match(staticHtml, /id="modelDeleteProfileButton"/, "API settings manager should expose a local removal control");
assert.match(staticHtml, /id="githubEditProfileButton"/, "GitHub settings manager should expose an edit control");
assert.match(staticHtml, /id="githubDeleteProfileButton"/, "GitHub settings manager should expose a local removal control");
assert.match(staticHtml, /id="jdBackgroundStatus"/, "JD import should expose an inline background result region");
assert.match(staticHtml, /<textarea id="jdSourceUrl"/, "JD import should accept multiple source URLs in a textarea");
assert.match(staticHtml, /id="jdImageSummary"/, "JD image import should expose count and size limits");
assert.match(staticHtml, /<label for="jdQuery">目标岗位名称<\/label><input id="jdQuery"[^>]*type="text"/, "JD search should accept one target job title");
assert.doesNotMatch(staticHtml, /岗位名称或方向（可填多个）|可填写多个岗位名称或方向/, "JD search should not suggest multiple target roles");
assert.match(staticHtml, /搜索 3-5 份同类招聘 JD，汇总多来源共性要求并标出来源和差异/, "JD search copy should explain multi-source common-requirement aggregation");
assert.match(staticApp, /source_urls:\s*jdSourceUrls\(fields\.jdSourceUrl\)/, "JD import should submit the complete source URL array");
assert.match(staticApp, /jdQuery[\s\S]*event\.ctrlKey \|\| event\.metaKey/, "JD query should use Ctrl/Cmd+Enter to submit the single target role");
assert.match(staticHtml, /只填写一个目标岗位名称/, "JD query hint should require one target role");
assert.match(staticApp, /sample_count/, "JD aggregation status should use the structured sample count");
assert.match(staticApp, /comparison_status/, "JD aggregation status should use the structured comparison status");
assert.match(staticApp, /jd-import-result-card/, "JD import results should reuse the structured JD card instead of raw JSON");
assert.match(staticApp, /MAX_SOURCE_URLS/, "JD import should enforce the backend source URL count limit");
assert.match(staticApp, /MAX_IMAGE_TOTAL_BYTES/, "JD image import should enforce the backend total-size limit");
assert.match(staticApp, /jdImportContextMatches/, "JD import should guard late async results after a session switch");
assert.match(staticHtml, /搜索公开网页（企业招聘官网 \/ 招聘网站）/, "JD search should clearly describe the generic public-web scope");
assert.doesNotMatch(staticHtml, /id="jdPlatform"|BOSS|猎聘|智联招聘/, "JD search should not expose a platform selector or single-platform branding");
assert.match(staticApp, /JD_PUBLIC_WEB_SEARCH_TARGET/, "empty JD search recovery should use one fixed generic public-web target");
assert.match(staticApp, /encodeURIComponent\(text\)/, "empty JD search recovery should encode the current query");
assert.match(staticApp, /openJdWebSearch/, "empty JD search recovery should offer an in-context public-web search action");
assert.match(staticApp, /sessionId !== String\(state\.currentSessionId/, "public-web search recovery should reject stale session context");
assert.match(staticHtml, /id="profileInterviewOpen"/, "profile interview should expose a dedicated conversation launcher");
assert.doesNotMatch(staticHtml, /id="profileAnswer(Label)?"/, "interview tab should not retain the old inline answer form");
assert.match(staticHtml, /id="profileImportActions"/, "profile import actions should have a scoped container to hide during interview");
assert.match(staticHtml, /data-profile-tab="self_report"[^>]*>技能自述/, "profile tabs should expose the skill self-report tab");
assert.match(staticHtml, /data-profile-tab="resume"[^>]*>简历/, "profile tabs should expose the resume tab");
assert.match(staticHtml, /data-profile-tab="local_log"[^>]*>本地日志文件/, "profile tabs should expose the local log tab");
assert.doesNotMatch(staticHtml, /id="profileSource"/, "profile tabs should no longer expose a material-source selector");
assert.match(staticHtml, /id="profileInputMode"/, "skill self-report should expose text/document input mode");
assert.match(staticHtml, /id="profileResumeDocuments"/, "resume tab should expose an isolated resume picker");
assert.match(staticHtml, /id="profileLocalFilePicker"/, "local log tab should expose browser file fallback");
assert.match(staticHtml, /id="profileLocalFolderPicker"[^>]*webkitdirectory/, "local log tab should expose browser directory upload fallback");
assert.doesNotMatch(staticHtml, /profileLocalNative(File|Folder)Button/, "local tab should not expose implementation-specific picker buttons");
assert.match(staticHtml, /for="profileLocalPath">Obsidian 库 \/ 本地笔记/, "local tab should label the readonly path as the Obsidian/local notes location");
assert.match(staticHtml, /profile-local-path-caption[^>]*>（已选位置）/, "local tab should identify the readonly value as the selected location");
assert.match(staticHtml, /这里只读取日志和笔记材料，不包含简历/, "local log copy should exclude resumes");
assert.match(staticApp, /self_report: "self_report", resume: "resume", local_log: "work_log"/, "profile source should map each tab to its dedicated backend source");
assert.match(staticApp, /pick_local_file/, "local file picker should use the actual file bridge method");
assert.match(staticApp, /pick_local_folder/, "local folder picker should use the actual folder bridge method");
assert.doesNotMatch(staticApp, /pick_profile_path/, "local picker should not use the removed generic bridge method");
assert.match(staticApp, /openProfileLocalBrowserPicker\(kind\)/, "local picker should fall back to browser file selection when the bridge is unavailable");
assert.match(staticApp, /profileLocalPickerSerial/, "local log picker should invalidate late native results");
assert.match(staticApp, /PROFILE_LOCAL_MAX_FILES = 1000/, "browser local folder fallback should cap the file count");
assert.match(staticApp, /PROFILE_LOCAL_MAX_FILE_BYTES = 2 \* 1024 \* 1024/, "browser local folder fallback should cap each file at 2 MiB");
assert.match(staticApp, /PROFILE_LOCAL_MAX_TOTAL_BYTES = 20 \* 1024 \* 1024/, "browser local folder fallback should cap the total at 20 MiB");
assert.match(staticApp, /profileLocalBrowserPathBlocked/, "browser local folder fallback should reject hidden and sensitive paths");
assert.match(staticApp, /已跳过 \$\{selection\.skipped\.length\} 个/, "browser local folder fallback should show a skipped count");
assert.match(staticApp, /profileResumeFiles/, "resume files should stay separate from skill self-report files");
assert.match(staticApp, /\/sessions\/\$\{encodeURIComponent\(id\)\}\/jd\/import/, "JD import should use the scoped background route");
assert.match(staticApp, /\/sessions\/\$\{encodeURIComponent\(sessionId\)\}\/interview.*body: {}/s, "interview should use the idempotent scoped-open route for the first question");
assert.match(staticApp, /metadata\.scope === "practice"|\["practice", "interview", "jd-import"\]/, "transcript filtering should recognize interview and JD background scopes");
assert.match(staticApp, /if \(state\.interview\.launchPending\) return true;/, "interview launch should be idempotent while the native child is opening");
assert.doesNotMatch(staticHtml, /id="githubToken"/, "saved GitHub tokens should not remain as a manager field");
assert.match(staticApp, /data-session-rename/, "sidebar rows should expose rename controls");
assert.match(staticApp, /data-session-pin/, "sidebar rows should expose pin controls");
assert.match(staticApp, /method: "PATCH", body: \{ title: title \|\| null \}/, "renaming should persist through the session metadata endpoint");
assert.match(staticApp, /Number\(Boolean\(b\.pinned\)\) - Number\(Boolean\(a\.pinned\)\)/, "pinned sessions should sort first");
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
    this.clientHeight = 0;
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
  closest(selector) {
    const match = String(selector || "").match(/^\[data-([a-z0-9-]+)\]$/i);
    if (match) {
      const key = match[1].replace(/-([a-z])/g, (_m, c) => c.toUpperCase());
      if (this.dataset && this.dataset[key] !== undefined) return this;
    }
    return this.parentNode ? this.parentNode.closest(selector) : null;
  }
  contains(node) { return node === this || this.children.some((child) => child === node || (typeof child.contains === "function" && child.contains(node))); }
  focus() { this.ownerDocument.activeElement = this; }
  blur() {}
  setSelectionRange() {}
  scrollTo({ top }) { this.scrollTop = Math.max(0, Math.min(top, this.scrollHeight - this.clientHeight)); }
  scrollIntoView() {}
  reset() { this.value = ""; this.checked = false; }
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
  contains(node) { return node === this || this.body.contains(node) || this.documentElement.contains(node); }
  querySelectorAll() { return []; }
  querySelector() { return new Element(this, "div"); }
  addEventListener(name, handler) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(handler); }
  removeEventListener(name, handler) { this.listeners.set(name, (this.listeners.get(name) || []).filter((item) => item !== handler)); }
  dispatchEvent(event) { for (const handler of this.listeners.get(event.type) || []) handler({ ...event, target: this, currentTarget: this }); }
}

function storage({ fail = false } = {}) {
  const values = new Map();
  if (fail) return {
    getItem: () => { throw new Error("storage unavailable"); },
    setItem: () => { throw new Error("storage unavailable"); },
    removeItem: () => { throw new Error("storage unavailable"); },
  };
  return { getItem: (key) => values.has(key) ? values.get(key) : null, setItem: (key, value) => values.set(key, String(value)), removeItem: (key) => values.delete(key) };
}

function response(payload, status = 200) {
  return { ok: status >= 200 && status < 300, status, headers: { get: () => "application/json" }, json: async () => payload, text: async () => JSON.stringify(payload) };
}

async function runScenario(name, { pending = null, queue = 0, busy = false, workspaceBlocked = "", storageFailure = false, workspacePickerResult = null, profilePickerKind = null, profilePickerResult = null, alternateSession = false, locationSearch = "", jdImportPayload = null, jdJobPayload = null, interviewSnapshotPayload = null, jobRadarState = null } = {}) {
  const document = new Document();
  const idSource = fs.readFileSync(htmlPath, "utf8").matchAll(/id="([^"]+)"/g);
  for (const match of idSource) document.getElementById(match[1]);
  const syntheticProfileTab = new Element(document, "button"); syntheticProfileTab.dataset.profileTab = "interview";
  const syntheticLocalProfileTab = new Element(document, "button"); syntheticLocalProfileTab.dataset.profileTab = "local_log";
  const syntheticStep = new Element(document, "button"); syntheticStep.dataset.step = "jd";
  const syntheticJdModes = ["source", "search", "image"].map((mode) => { const button = new Element(document, "button"); button.dataset.jdMode = mode; return button; });
  document.body.appendChild(syntheticProfileTab); document.body.appendChild(syntheticLocalProfileTab); document.body.appendChild(syntheticStep); syntheticJdModes.forEach((button) => document.body.appendChild(button));
  const originalQuerySelectorAll = document.querySelectorAll.bind(document);
  document.querySelectorAll = (selector) => {
    if (selector === "[data-profile-tab]") return [syntheticProfileTab, syntheticLocalProfileTab];
    if (selector === "[data-step]") return [syntheticStep];
    if (selector === "[data-jd-mode]") return syntheticJdModes;
    return originalQuerySelectorAll(selector);
  };
  const intervals = [];
  const session = {
    id: "session-1", title: "测试会话", messages: [], events: [], pending, busy,
    queued_calls: queue, active_skills: [], skill_state: { "job-radar": jobRadarState && typeof jobRadarState === "object" ? jobRadarState : {} }, execution: null,
  };
  const jobs = new Map();
  let nextJob = 1;
  const summary = { id: session.id, title: session.title, pending: Boolean(pending), queued_calls: queue, busy, message_count: 0, active_skills: [] };
  let workspacePath = "test";
  let reloadCount = 0;
  const workspacePickerCalls = [];
  const profilePickerCalls = [];
  const openedWindows = [];
  const window = {
    document, localStorage: storage(), sessionStorage: storage({ fail: storageFailure }), location: { href: "http://test.local/", hash: "", pathname: "/", search: locationSearch, reload: () => { reloadCount += 1; } },
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
    open: (url, name, features) => { const child = { url, name, features, closed: false, focus() {} }; openedWindows.push(child); return child; },
  };
  const bridgeApi = {};
  if (workspacePickerResult !== null) bridgeApi.pick_workspace_folder = async (initialPath) => {
    workspacePickerCalls.push(String(initialPath || ""));
    return typeof workspacePickerResult === "function" ? workspacePickerResult(initialPath) : workspacePickerResult;
  };
  if (profilePickerKind && profilePickerResult !== null) bridgeApi[`pick_local_${profilePickerKind}`] = async (initialPath) => {
    profilePickerCalls.push({ kind: profilePickerKind, initialPath: String(initialPath || "") });
    return typeof profilePickerResult === "function" ? profilePickerResult(initialPath) : profilePickerResult;
  };
  if (Object.keys(bridgeApi).length) window.pywebview = { api: bridgeApi };
  const calls = [];
  let selectedModelProfileId = "model-main";
  let modelProfiles = [
    { profile_id: "model-main", display_name: "主 API", api_mode: "chat_completions", base_url: "https://api.example.test/v1", model: "main-model", has_api_key: true },
    { profile_id: "model-alt", display_name: "备用 API", api_mode: "chat_completions", base_url: "https://api.example.test/v1", model: "alt-model", has_api_key: true }
  ];
  let githubProfiles = [{ profile_id: "github-default", display_name: "默认 GitHub", has_token: true }];
  let activeGithubProfileId = "github-default";
  const snapshot = () => ({ ...session, pending: session.pending ? { ...session.pending } : null, messages: [...session.messages], events: [...session.events], active_skills: [...session.active_skills], skill_state: { ...session.skill_state } });
  const alternateSessionData = {
    id: "session-2", title: "第二测试会话", messages: [], events: [], pending: null, busy: false,
    queued_calls: 0, active_skills: [], skill_state: { "job-radar": {} }, execution: null,
  };
  const alternateSummary = { id: alternateSessionData.id, title: alternateSessionData.title, pending: false, queued_calls: 0, busy: false, message_count: 0, active_skills: [] };
  const sessionSummaries = () => alternateSession ? [{ ...summary }, { ...alternateSummary }] : [{ ...summary }];
  const refreshSummary = () => { summary.pending = Boolean(session.pending); summary.queued_calls = session.queued_calls; summary.busy = Boolean(session.busy); };
  async function fetchStub(url, options = {}) {
    const path = String(url).replace(/^\/api/, "");
    calls.push({ path, options });
    if (path === "/info") return response({ mode: "demo", workspace: workspacePath, skills: [] });
    if (path === "/workspace") {
      if (options.method === "POST") {
        const body = JSON.parse(options.body || "{}");
        workspacePath = String(body.path || "");
        return response({ ok: true, workspace: workspacePath, requires_reload: true, token: "rotated-token" });
      }
      return response({ workspace: workspacePath, session_count: 1, busy: false, can_switch: !workspaceBlocked, blocked_reason: workspaceBlocked || null });
    }
    if (path === "/sessions") { refreshSummary(); return response({ sessions: sessionSummaries() }); }
    if (path === "/sessions/session-1") { refreshSummary(); return response(snapshot()); }
    if (path === "/sessions/session-2") return response({ ...alternateSessionData, messages: [], events: [] });
    if (path.startsWith("/jobs/")) return response(jdJobPayload || jobs.get(path.slice("/jobs/".length)) || { status: "failed", error: "missing fake job" });
    if (path.startsWith("/files")) return response({ path: ".", entries: [] });
    if (path === "/imports/local" && options.method === "POST") {
      const body = JSON.parse(options.body || "{}");
      assert.ok(body.path, "local path import should receive the selected path");
      return response({ imported: [{ name: "note.md", path: "notes/note.md", source_path: body.path }], skipped: [] });
    }
    if (path === "/settings/ui") return response({ theme: "dark" });
    if (path === "/settings/models") {
      if (options.method === "POST") {
        const body = JSON.parse(options.body || "{}");
        if (body.confirm && body.profile_id) {
          modelProfiles = modelProfiles.filter((item) => item.profile_id !== body.profile_id);
          if (selectedModelProfileId === body.profile_id) selectedModelProfileId = modelProfiles[0]?.profile_id || "";
          return response({ current_profile_id: selectedModelProfileId, saved_profiles: modelProfiles });
        }
        if (body.profile_id && modelProfiles.some((item) => item.profile_id === body.profile_id)) {
          selectedModelProfileId = body.profile_id;
          modelProfiles = modelProfiles.map((item) => item.profile_id === body.profile_id
            ? { ...item, display_name: body.display_name || item.display_name, api_mode: body.api_mode || item.api_mode, base_url: body.base_url || item.base_url, model: body.model || item.model }
            : item);
          const updated = modelProfiles.find((item) => item.profile_id === selectedModelProfileId) || {};
          return response({ current_profile_id: selectedModelProfileId, api_mode: updated.api_mode, base_url: updated.base_url, model: updated.model, has_api_key: true });
        }
        if (body.profile_id) selectedModelProfileId = body.profile_id;
        if (body.display_name) {
          selectedModelProfileId = "model-new";
          modelProfiles = [{ profile_id: selectedModelProfileId, display_name: body.display_name, api_mode: body.api_mode, base_url: body.base_url, model: body.model, has_api_key: Boolean(body.api_key) }, ...modelProfiles];
        }
        return response({ current_profile_id: selectedModelProfileId, api_mode: "chat_completions", base_url: "https://api.example.test/v1", model: selectedModelProfileId === "model-alt" ? "alt-model" : "main-model", has_api_key: true });
      }
      return response({ current_profile_id: selectedModelProfileId, api_mode: "chat_completions", base_url: "https://api.example.test/v1", model: selectedModelProfileId === "model-alt" ? "alt-model" : "main-model", has_api_key: true });
    }
    if (path === "/settings/models/select" && options.method === "POST") {
      const body = JSON.parse(options.body || "{}");
      selectedModelProfileId = body.profile_id;
      return response({ current_profile_id: selectedModelProfileId, saved_profiles: modelProfiles });
    }
    if (path === "/settings/models/options") return response({ current_profile_id: selectedModelProfileId, saved_profiles: modelProfiles });
    if (path === "/settings/models/delete" && options.method === "POST") {
      const body = JSON.parse(options.body || "{}");
      assert.strictEqual(body.confirm, true, "API removal must include explicit confirmation");
      modelProfiles = modelProfiles.filter((item) => item.profile_id !== body.profile_id);
      if (selectedModelProfileId === body.profile_id) selectedModelProfileId = modelProfiles[0]?.profile_id || "";
      return response({ current_profile_id: selectedModelProfileId, saved_profiles: modelProfiles });
    }
    if (path === "/settings/github-profiles") {
      if (options.method === "POST") {
        const body = JSON.parse(options.body || "{}");
        assert.deepStrictEqual(Object.keys(body).sort(), ["display_name", "token"], "GitHub create payload should contain only name and token");
        assert.strictEqual(body.token, "gh-secret", "GitHub token should be sent only on create");
        githubProfiles = [{ profile_id: "github-new", display_name: body.display_name, has_token: true }, ...githubProfiles];
        activeGithubProfileId = "github-new";
        return response({ profiles: githubProfiles, active_profile_id: activeGithubProfileId, has_token: true });
      }
      if (options.method === "PATCH") {
        const body = JSON.parse(options.body || "{}");
        assert.deepStrictEqual(Object.keys(body).sort(), ["display_name", "profile_id", "token"], "GitHub edit payload should retain stable identity");
        assert.strictEqual(body.token, "", "GitHub edit may leave the token blank to preserve it");
        githubProfiles = githubProfiles.map((item) => item.profile_id === body.profile_id ? { ...item, display_name: body.display_name } : item);
        activeGithubProfileId = body.profile_id;
        return response({ profiles: githubProfiles, active_profile_id: activeGithubProfileId, has_token: true });
      }
      return response({ profiles: githubProfiles, active_profile_id: activeGithubProfileId, has_token: true });
    }
    if (path.startsWith("/settings/github-profiles/") && options.method === "DELETE") {
      const profileId = path.slice("/settings/github-profiles/".length);
      githubProfiles = githubProfiles.filter((item) => item.profile_id !== profileId);
      activeGithubProfileId = githubProfiles[0]?.profile_id || "";
      return response({ profiles: githubProfiles, active_profile_id: activeGithubProfileId, has_token: githubProfiles.length > 0 });
    }
    if (path === "/settings/github-profiles/select" && options.method === "POST") {
      const body = JSON.parse(options.body || "{}");
      activeGithubProfileId = body.profile_id;
      return response({ profiles: githubProfiles, active_profile_id: activeGithubProfileId, has_token: true });
    }
    if (path === "/sessions/session-1/jd/import" && options.method === "POST") {
      const body = JSON.parse(options.body || "{}");
      assert.ok(["search", "source", "image"].includes(body.mode), "JD background route should receive a supported import mode");
      assert.strictEqual(Object.prototype.hasOwnProperty.call(body, "text"), false, "JD background submit must not persist a user prompt");
      assert.ok(Array.isArray(body.source_urls), "JD background submit should include source_urls");
      if (body.source_urls.length) assert.strictEqual(body.source_url, body.source_urls[0], "legacy source_url should mirror the first source URL");
      if (jdImportPayload) return response(jdImportPayload);
      const jobId = `jd-job-${nextJob++}`;
      jobs.set(jobId, { status: "completed", result: { jd: { title: "后端工程师", company: "Synthetic" }, message: "已读取真实输入" } });
      return response({ job_id: jobId });
    }
    if (path === "/sessions/session-1/approve" && options.method === "POST" && jdImportPayload?.result?.pending) {
      const body = JSON.parse(options.body || "{}");
      assert.strictEqual(body.id, jdImportPayload.result.pending.id, "JD approval should carry the scoped pending id");
      return response({ status: "completed", result: { status: "completed", message: "Synthetic JD approval completed" } });
    }
    if (path === "/sessions/session-1/interview" && options.method === "POST") return response({ interview_thread_id: "interview-1" });
    if (path === "/sessions/session-1/interview/interview-1") return response(interviewSnapshotPayload || { messages: [], events: [], busy: false });
    if (path === "/sessions/session-1/interview/interview-1/start" && options.method === "POST") return response({ job_id: "interview-job-1" });
    if (path === "/sessions/session-1/interview/interview-1/message" && options.method === "POST") return response({ job_id: "interview-job-2" });
    if (path === "/sessions/session-1/approve" && options.method === "POST" && interviewSnapshotPayload?.pending) {
      const body = JSON.parse(options.body || "{}");
      assert.strictEqual(body.id, interviewSnapshotPayload.pending.id, "interview approval should carry the selected thread pending id");
      jobs.set("interview-approve-job", { status: "completed", result: { status: "completed", message: "Synthetic interview approval completed" } });
      return response({ job_id: "interview-approve-job" });
    }
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
    window, document, console, fetch: fetchStub, Headers, FormData, URL, URLSearchParams, Blob, File: FileCtor,
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
  result.flush = flush;
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
  result.reloadCount = () => reloadCount;
  result.locationHash = () => window.location.hash;
  result.workspaceCalls = () => calls.filter((call) => call.path === "/workspace");
  result.workspacePickerCalls = () => [...workspacePickerCalls];
  result.profilePickerCalls = () => profilePickerCalls.map((call) => ({ ...call }));
  result.openedWindows = () => [...openedWindows];
  result.openInterview = async () => { syntheticProfileTab.click(); await flush(); };
  result.openProfileLocal = async (kind = "file") => {
    syntheticLocalProfileTab.click();
    await flush();
    document.getElementById(kind === "folder" ? "profileLocalFolderButton" : "profileLocalFileButton").click();
    await flush();
  };
  result.selectProfileLocalBrowserFiles = async (kind, files) => {
    const picker = document.getElementById(kind === "folder" ? "profileLocalFolderPicker" : "profileLocalFilePicker");
    picker.files = files;
    picker.dispatchEvent({ type: "change", target: picker });
    await flush();
  };
  result.switchProfileSession = async (id = "session-2") => {
    const nodes = [];
    const visitSessionNodes = (node) => {
      if (!node || !node.children) return;
      for (const child of node.children) { nodes.push(child); visitSessionNodes(child); }
    };
    visitSessionNodes(document.body);
    const button = nodes.find((node) => String(node.dataset?.sessionId || "") === String(id));
    assert.ok(button, `${name} should expose a session switch target`);
    button.click();
    await flush();
  };
  result.openJd = async () => { syntheticStep.click(); await flush(); };
  result.submitJdSource = async (text = "Synthetic JD body", sourceUrls = "") => {
    document.getElementById("jdSourceUrl").value = sourceUrls;
    document.getElementById("jdSourceText").value = text;
    document.getElementById("jdImportSubmit").click();
    await flush();
  };
  result.keydownJdQuery = async (value, { ctrlKey = false, metaKey = false, shiftKey = false } = {}) => {
    const input = document.getElementById("jdQuery");
    input.value = value;
    let prevented = false;
    input.dispatchEvent({
      type: "keydown", key: "Enter", ctrlKey, metaKey, shiftKey, isComposing: false,
      preventDefault() { prevented = true; },
    });
    await flush();
    return { prevented, value: input.value };
  };
  result.selectJdImages = async (files) => {
    const picker = document.getElementById("jdImagePicker");
    picker.files = files;
    picker.dispatchEvent({ type: "change", target: picker });
    await flush();
  };
  result.pasteJdImages = async (files) => {
    const backdrop = document.getElementById("jdImportBackdrop");
    backdrop.dispatchEvent({ type: "paste", target: backdrop, clipboardData: { files } });
    await flush();
  };
  result.workspaceStatus = () => document.getElementById("workspaceSwitchStatus").textContent;
  result.openSettings = async () => {
    document.getElementById("modelSettingsButton").click();
    await flush();
  };
  result.openNewApi = async () => {
    document.getElementById("modelNewProfileButton").click();
    await flush();
  };
  result.cancelApi = async () => {
    document.getElementById("modelEditorCancel").click();
    await flush();
  };
  result.saveApi = async (name) => {
    document.getElementById("modelProfileName").value = name;
    document.getElementById("modelApiMode").value = "chat_completions";
    document.getElementById("modelBaseUrl").value = "https://api.example.test/v1";
    document.getElementById("modelName").value = "new-model";
    document.getElementById("modelApiKey").value = "api-secret";
    document.getElementById("modelSettingsForm").dispatchEvent({ type: "submit", preventDefault() {} });
    await flush();
  };
  result.openNewGithub = async () => {
    document.getElementById("githubNewProfileButton").click();
    await flush();
  };
  result.cancelGithub = async () => {
    document.getElementById("githubProfileCancel").click();
    await flush();
  };
  result.saveGithub = async (name, token) => {
    document.getElementById("githubProfileName").value = name;
    document.getElementById("githubProfileToken").value = token;
    document.getElementById("githubProfileForm").dispatchEvent({ type: "submit", preventDefault() {} });
    await flush();
  };
  result.settingsDocument = () => document;
  result.switchWorkspace = async (target) => {
    document.getElementById("localImportBackdrop").classList.add("hidden");
    document.getElementById("workspaceSwitchButton").click();
    await flush();
    document.getElementById("workspaceSwitchPath").value = target;
    document.getElementById("workspaceSwitchForm").dispatchEvent({ type: "submit" });
    await flush();
  };
  result.openWorkspaceSwitch = async () => {
    document.getElementById("localImportBackdrop").classList.add("hidden");
    document.getElementById("workspaceSwitchButton").click();
    await flush();
  };
  result.pickWorkspace = async () => {
    document.getElementById("workspacePickerButton").click();
    await flush();
  };
  result.closeWorkspaceSwitch = async () => {
    document.getElementById("workspaceSwitchClose").click();
    await flush();
  };
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

  const workspace = await runScenario("workspace-switch", {});
  await workspace.switchWorkspace("next-workspace");
  const workspacePost = workspace.workspaceCalls().find((call) => call.options.method === "POST");
  assert.ok(workspacePost, "workspace switch should POST to the workspace endpoint");
  assert.strictEqual(JSON.parse(workspacePost.options.body).path, "next-workspace");
  assert.strictEqual(workspace.reloadCount(), 1, "successful workspace switch must reload the complete client state");
  console.log("workspace switch: POST path matched; rotated token accepted; full reload requested");

  const storageFallback = await runScenario("workspace-switch-storage-fallback", { storageFailure: true });
  await storageFallback.switchWorkspace("hash-target");
  assert.strictEqual(storageFallback.locationHash(), "#token=rotated-token", "workspace switch should carry the rotated token in the URL when browser storage is unavailable");
  assert.strictEqual(storageFallback.reloadCount(), 1, "URL token fallback should still reload the complete client state");
  console.log("workspace switch: URL token fallback preserved the rotated token");

  const blockedWorkspace = await runScenario("workspace-switch-blocked", { workspaceBlocked: "会话有待处理的审批" });
  await blockedWorkspace.switchWorkspace("blocked-target");
  assert.strictEqual(blockedWorkspace.workspaceCalls().filter((call) => call.options.method === "POST").length, 0, "blocked workspace must not submit a switch");
  assert.ok(blockedWorkspace.workspaceStatus().includes("会话有待处理的审批"), "blocked workspace must explain why switching is unavailable");
  console.log("workspace switch: blocker explained and POST suppressed");

  const nativePicker = await runScenario("workspace-native-picker", { workspacePickerResult: { selected: true, path: "picked-workspace" } });
  await nativePicker.openWorkspaceSwitch();
  await nativePicker.pickWorkspace();
  assert.strictEqual(nativePicker.settingsDocument().getElementById("workspaceSwitchPath").value, "picked-workspace", "native workspace picker should populate the fallback path field");
  assert.deepStrictEqual(nativePicker.workspacePickerCalls(), ["test"], "native workspace picker should receive the current path as its initial directory");
  assert.strictEqual(nativePicker.workspaceCalls().filter((call) => call.options.method === "POST").length, 0, "choosing a workspace folder must wait for explicit switch confirmation");
  console.log("workspace picker: pick_workspace_folder received initial path; result populated path; switch remained explicit");

  const canceledPicker = await runScenario("workspace-picker-canceled", { workspacePickerResult: { ok: true, selected: false, canceled: true } });
  await canceledPicker.openWorkspaceSwitch();
  await canceledPicker.pickWorkspace();
  assert.ok(canceledPicker.workspaceStatus().includes("已取消选择"), "native workspace picker cancellation should be reported separately");
  console.log("workspace picker: cancellation reported without treating it as an error");

  const failedPicker = await runScenario("workspace-picker-error", { workspacePickerResult: { ok: false, error: "系统文件夹选择失败", selected: false } });
  await failedPicker.openWorkspaceSwitch();
  await failedPicker.pickWorkspace();
  assert.ok(failedPicker.workspaceStatus().includes("系统文件夹选择失败"), "native workspace picker errors should surface the backend error");
  assert.strictEqual(failedPicker.workspaceStatus().includes("已取消选择"), false, "native workspace picker errors must not be reported as cancellation");
  console.log("workspace picker: backend error surfaced distinctly from cancellation");

  let resolveLatePicker;
  const latePicker = new Promise((resolve) => { resolveLatePicker = resolve; });
  const closedPicker = await runScenario("workspace-picker-close-guard", { workspacePickerResult: () => latePicker });
  await closedPicker.openWorkspaceSwitch();
  await closedPicker.pickWorkspace();
  await closedPicker.closeWorkspaceSwitch();
  resolveLatePicker({ ok: true, selected: true, path: "late-workspace" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.strictEqual(closedPicker.settingsDocument().getElementById("workspaceSwitchPath").value, "test", "late picker results must not repopulate a closed workspace modal");
  assert.strictEqual(closedPicker.workspaceStatus().includes("已选择文件夹"), false, "late picker results must not update closed modal status");
  console.log("workspace picker: closing the modal invalidated the pending native result");

  const settings = await runScenario("settings-manager", {});
  await settings.openSettings();
  const settingsDocument = settings.settingsDocument();
  assert.strictEqual(settingsDocument.getElementById("modelSettingsManagerView").classList.contains("hidden"), false, "settings manager should open as the top-level view");
  assert.strictEqual(settingsDocument.getElementById("modelProfileChoice").children.length, 2, "API manager should list saved API names");
  assert.strictEqual(settingsDocument.getElementById("githubProfileChoice").children.length, 1, "GitHub manager should list saved token names");
  settingsDocument.getElementById("modelProfileChoice").value = "model-alt";
  settingsDocument.getElementById("modelProfileChoice").dispatchEvent({ type: "change" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(settings.calls.some((call) => call.path === "/settings/models/select" && JSON.parse(call.options.body).profile_id === "model-alt"), "API dropdown should apply the selected profile");
  settingsDocument.getElementById("modelEditProfileButton").click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  settingsDocument.getElementById("modelBaseUrl").value = "https://api.changed.example/v2";
  settingsDocument.getElementById("modelName").value = "changed-model";
  settingsDocument.getElementById("modelSettingsForm").dispatchEvent({ type: "submit", preventDefault() {} });
  await new Promise((resolve) => setTimeout(resolve, 0));
  const editedApiCall = settings.calls.find((call) => call.path === "/settings/models" && call.options.method === "POST" && JSON.parse(call.options.body).model === "changed-model");
  assert.ok(editedApiCall, "editing an existing API should submit the changed endpoint/model");
  assert.strictEqual(JSON.parse(editedApiCall.options.body).profile_id, "model-alt", "editing an existing API must retain its stable profile identity");
  await settings.openNewApi();
  assert.strictEqual(settingsDocument.getElementById("modelEditorBackdrop").classList.contains("hidden"), false, "new API should open a child editor dialog");
  assert.strictEqual(settingsDocument.getElementById("modelApiKey").value, "", "new API editor must start with an empty secret");
  await settings.cancelApi();
  assert.strictEqual(settingsDocument.getElementById("modelEditorBackdrop").classList.contains("hidden"), true, "cancel should close the API child dialog");
  await settings.openNewApi();
  await settings.saveApi("新 API");
  assert.strictEqual(settingsDocument.getElementById("modelEditorBackdrop").classList.contains("hidden"), true, "API save should close the child editor");
  assert.strictEqual(settingsDocument.getElementById("modelSettingsManagerView").classList.contains("hidden"), false, "API save should return to the manager");
  assert.strictEqual(settingsDocument.getElementById("modelProfileChoice").value, "model-new", "saved API should become the active manager selection");
  await settings.openNewGithub();
  assert.strictEqual(settingsDocument.getElementById("githubProfileEditorBackdrop").classList.contains("hidden"), false, "new GitHub token should open a child editor dialog");
  const githubCreatesBeforeCancel = settings.calls.filter((call) => call.path === "/settings/github-profiles" && call.options.method === "POST").length;
  await settings.cancelGithub();
  assert.strictEqual(settings.calls.filter((call) => call.path === "/settings/github-profiles" && call.options.method === "POST").length, githubCreatesBeforeCancel, "canceling a new GitHub token must not submit a request");
  await settings.openNewGithub();
  await settings.saveGithub("个人 GitHub", "gh-secret");
  assert.strictEqual(settingsDocument.getElementById("githubProfileToken").value, "", "GitHub token input must clear after save");
  assert.strictEqual(settingsDocument.getElementById("githubProfileEditorBackdrop").classList.contains("hidden"), true, "GitHub save should return to the manager");
  assert.strictEqual(settingsDocument.getElementById("githubProfileChoice").value, "github-new", "saved GitHub profile should become active");
  settingsDocument.getElementById("githubProfileChoice").value = "github-default";
  settingsDocument.getElementById("githubProfileChoice").dispatchEvent({ type: "change" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(settings.calls.some((call) => call.path === "/settings/github-profiles/select" && JSON.parse(call.options.body).profile_id === "github-default"), "GitHub dropdown should apply the selected profile");
  settingsDocument.getElementById("githubEditProfileButton").click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  settingsDocument.getElementById("githubProfileName").value = "默认 GitHub 改名";
  settingsDocument.getElementById("githubProfileToken").value = "";
  settingsDocument.getElementById("githubProfileForm").dispatchEvent({ type: "submit", preventDefault() {} });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(settings.calls.some((call) => call.path === "/settings/github-profiles" && call.options.method === "PATCH" && JSON.parse(call.options.body).profile_id === "github-default" && JSON.parse(call.options.body).token === ""), "GitHub edit should preserve stable identity and allow a blank token");
  settingsDocument.getElementById("githubDeleteProfileButton").click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(settings.calls.some((call) => call.path === "/settings/github-profiles/github-default" && call.options.method === "DELETE"), "GitHub removal should require explicit local confirmation");
  settingsDocument.getElementById("modelProfileChoice").value = "model-alt";
  settingsDocument.getElementById("modelProfileChoice").dispatchEvent({ type: "change" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  settingsDocument.getElementById("modelDeleteProfileButton").click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(settings.calls.some((call) => call.path === "/settings/models/delete" && JSON.parse(call.options.body).confirm === true), "API removal should require explicit local confirmation");
  console.log("settings manager: API list/select/edit/remove and GitHub create/edit/remove passed");

  const jdBackground = await runScenario("jd-background", {});
  await jdBackground.openJd();
  await jdBackground.submitJdSource("Synthetic JD body", "https://careers.example.com/jobs/one\nhttps://jobs.example.org/postings/two");
  assert.ok(jdBackground.calls.some((call) => call.path === "/sessions/session-1/jd/import" && call.options.method === "POST"), "JD import should submit to its background route");
  const jdSourceCall = jdBackground.calls.find((call) => call.path === "/sessions/session-1/jd/import" && call.options.method === "POST");
  const jdSourceBody = JSON.parse(jdSourceCall.options.body);
  assert.deepStrictEqual(jdSourceBody.source_urls, ["https://careers.example.com/jobs/one", "https://jobs.example.org/postings/two"], "JD source textarea should preserve one URL per line for generic public sources");
  assert.strictEqual(jdSourceBody.source_url, jdSourceBody.source_urls[0], "JD source payload should retain the legacy first URL");
  assert.strictEqual(jdBackground.calls.some((call) => call.path === "/sessions/session-1/message"), false, "JD import should not create a main user prompt");
  assert.strictEqual(jdBackground.settingsDocument().getElementById("jdBackgroundStatus").classList.contains("hidden"), false, "JD completion should remain visible on the current page");
  console.log("JD background import: structured route, no main prompt, inline terminal status passed");

  const jdQueryInput = await runScenario("jd-query-enter", {});
  await jdQueryInput.openJd();
  const queryText = "具身智能算法工程师";
  const ordinaryEnter = await jdQueryInput.keydownJdQuery(queryText);
  assert.strictEqual(ordinaryEnter.prevented, false, "ordinary Enter should remain available without submitting a target role");
  assert.strictEqual(jdQueryInput.calls.some((call) => call.path === "/sessions/session-1/jd/import"), false, "ordinary Enter should not submit a JD query");
  assert.strictEqual(ordinaryEnter.value, queryText, "ordinary Enter should preserve the target role value");
  const shortcutEnter = await jdQueryInput.keydownJdQuery(queryText, { ctrlKey: true });
  assert.strictEqual(shortcutEnter.prevented, true, "Ctrl+Enter should submit a JD query");
  const jdQueryCall = jdQueryInput.calls.find((call) => call.path === "/sessions/session-1/jd/import" && call.options.method === "POST");
  assert.ok(jdQueryCall, "Ctrl+Enter should use the background JD import route");
  assert.strictEqual(JSON.parse(jdQueryCall.options.body).query, queryText, "Ctrl+Enter should submit the one target role");
  console.log("JD query editor: one target role is preserved; Ctrl+Enter submits it");

  const jdMultipleQuery = await runScenario("jd-multiple-query-rejected", {});
  await jdMultipleQuery.openJd();
  const rejected = await jdMultipleQuery.keydownJdQuery("具身智能算法工程师\n机器人算法工程师", { ctrlKey: true });
  assert.strictEqual(rejected.prevented, true, "Ctrl+Enter should handle an accidental multiline target input");
  assert.strictEqual(jdMultipleQuery.calls.some((call) => call.path === "/sessions/session-1/jd/import"), false, "multiple target roles should be rejected before request");
  assert.ok(jdMultipleQuery.settingsDocument().getElementById("jdImportStatus").textContent.includes("一个目标岗位"), "multiple target roles should explain the single-role input rule");
  console.log("JD query editor: multiple target roles are rejected before search");

  const jdAggregate = await runScenario("jd-aggregate-result", {
    jdImportPayload: {
      status: "completed",
      result: {
        status: "completed",
        jd: {
          job_title: "具身智能算法工程师",
          company: "多家公司（共性汇总）",
          hard_requirements: ["Python 服务开发", "具备真实项目经验"],
          skill_tags: ["Python", "PyTorch"],
          responsibilities: ["负责算法方案落地"],
          raw_summary: "共性要求由 3 份同类 JD 共同支持；差异：部分公司额外要求具备量产经验。",
          uncertain_fields: [],
          sample_target: 5,
          sample_count: 3,
          comparison_status: "compared",
        },
        source: {
          kind: "public_source",
          source_urls: [
              "https://careers.example.com/jobs/one",
              "https://jobs.example.org/postings/two",
              "https://company.example.net/careers/three",
          ],
          evidence: ["来源 1 已读取", "来源 2 已读取", "来源 3 已读取"],
        },
        message: "已完成共性汇总，等待核对。",
      },
    },
  });
  await jdAggregate.openJd();
  await jdAggregate.keydownJdQuery("具身智能算法工程师", { ctrlKey: true });
  const aggregateStatus = jdAggregate.settingsDocument().getElementById("jdBackgroundStatus");
  const aggregateText = (node) => [String(node.textContent || ""), ...Array.from(node.children || []).map(aggregateText)].join(" ");
  const findJdNode = (node, predicate) => {
    if (!node || !node.children) return null;
    for (const child of node.children) {
      if (predicate(child)) return child;
      const nested = findJdNode(child, predicate);
      if (nested) return nested;
    }
    return null;
  };
  const aggregateRenderedText = aggregateText(aggregateStatus);
  assert.ok(aggregateRenderedText.includes("共性硬性要求"), "structured JD result should show a common-requirement section");
  assert.ok(aggregateRenderedText.includes("来源与核验"), "structured JD result should show source verification details");
  assert.ok(aggregateRenderedText.includes("已核实 3 份同类 JD"), "structured JD result should show the verified sample count");
  assert.strictEqual(aggregateRenderedText.includes("job_title"), false, "structured JD result should not expose raw JSON field names");
  assert.strictEqual(aggregateRenderedText.includes("[object Object]"), false, "structured JD result should not expose object stringification");
  const aggregateCard = findJdNode(aggregateStatus, (node) => String(node.className || "").split(/\s+/).includes("jd-import-result-card"));
  assert.ok(aggregateCard, "completed search should render one structured editable JD card");
  assert.strictEqual(aggregateCard.dataset.jdCard, "structured", "search result should use the shared structured JD card renderer");
  assert.ok(findJdNode(aggregateCard, (node) => node.tagName === "BUTTON" && String(node.textContent || "") === "编辑"), "completed JD card should retain the edit action");
  const aggregateAnalysis = findJdNode(aggregateCard, (node) => String(node.className || "").split(/\s+/).includes("jd-analysis-details"));
  assert.ok(aggregateAnalysis, "long JD analysis should be placed in a collapsible details region");
  assert.strictEqual(Boolean(aggregateAnalysis.open), false, "JD analysis details should be collapsed by default");

  const persistedCompared = await runScenario("jd-persisted-search-compared", {
    jobRadarState: {
      jd: {
        job_title: "具身智能算法工程师",
        hard_requirements: ["Python 服务开发", "真实项目经验"],
        skill_tags: ["Python", "PyTorch"],
        responsibilities: ["负责算法方案落地"],
        nice_to_haves: ["具备量产经验"],
        raw_summary: "刷新后仍保留 3 份同类 JD 的共性与来源差异。",
        uncertain_fields: [],
        sample_target: 5,
        sample_count: 3,
        comparison_status: "compared",
      },
      jd_source: {
        kind: "public_source",
        source_urls: ["https://careers.example.com/jobs/persisted-one", "https://jobs.example.org/postings/persisted-two"],
        evidence: ["刷新后来源证据仍可核验"],
      },
      jd_comparison: { mode: "search", status: "compared", sample_target: 5, sample_count: 3, readable_samples: 3, minimum_samples: 3, evidence_complete: true },
    },
  });
  await persistedCompared.openJd();
  const persistedComparedPane = persistedCompared.settingsDocument().getElementById("jdPageResults");
  const persistedComparedCard = findJdNode(persistedComparedPane, (node) => String(node.className || "").split(/\s+/).includes("jd-import-result-card"));
  assert.ok(persistedComparedCard, "persisted compared search should use the shared structured JD card after refresh");
  const persistedComparedText = aggregateText(persistedComparedCard);
  assert.ok(persistedComparedText.includes("共性硬性要求"), "persisted compared search should retain common-requirement labels");
  assert.ok(persistedComparedText.includes("来源与核验"), "persisted compared search should retain source disclosure");
  assert.ok(findJdNode(persistedComparedCard, (node) => String(node.className || "").split(/\s+/).includes("jd-analysis-details")), "persisted compared search should retain folded analysis");
  console.log("JD persisted compared: refreshed state keeps common labels, folded analysis, and source disclosure");

  const persistedInsufficient = await runScenario("jd-persisted-search-insufficient", {
    jobRadarState: {
      jd: {
        job_title: "具身智能算法工程师",
        hard_requirements: [],
        skill_tags: [],
        responsibilities: [],
        nice_to_haves: [],
        raw_summary: "仅核实 2 份同类 JD，样本不足以形成共性。",
        uncertain_fields: ["共同要求：可比样本不足 3 个（当前 2 份，目标 5 份）"],
        sample_target: 5,
        sample_count: 2,
        comparison_status: "insufficient_samples",
      },
      jd_source: {
        kind: "public_source",
        source_urls: ["https://careers.example.com/jobs/persisted-insufficient-one", "https://jobs.example.org/postings/persisted-insufficient-two"],
        evidence: ["两份来源均已读取，但无法形成共性"],
      },
      jd_comparison: { mode: "search", status: "insufficient_samples", sample_target: 5, sample_count: 2, readable_samples: 2, minimum_samples: 3, evidence_complete: false },
    },
  });
  await persistedInsufficient.openJd();
  const persistedInsufficientPane = persistedInsufficient.settingsDocument().getElementById("jdPageResults");
  const persistedInsufficientCard = findJdNode(persistedInsufficientPane, (node) => String(node.className || "").split(/\s+/).includes("jd-import-result-card"));
  assert.ok(persistedInsufficientCard, "persisted insufficient search should still render a truthful structured card after refresh");
  const persistedInsufficientText = aggregateText(persistedInsufficientCard);
  assert.ok(persistedInsufficientText.includes("仅核实 2 份同类 JD"), "persisted insufficient search should retain its limited-evidence notice");
  assert.ok(persistedInsufficientText.includes("来源与核验"), "persisted insufficient search should retain source evidence disclosure");
  assert.strictEqual(persistedInsufficientText.includes("共性硬性要求"), false, "persisted insufficient search must not invent common-requirement labels");
  const persistedInsufficientEvidence = findJdNode(persistedInsufficientCard, (node) => String(node.className || "").split(/\s+/).includes("jd-evidence-details"));
  assert.ok(persistedInsufficientEvidence && !persistedInsufficientEvidence.open, "persisted insufficient source evidence should stay folded by default");
  console.log("JD persisted insufficient: refreshed state keeps limited evidence explicit without inventing common requirements");

  const queuedNoSearchRecords = await runScenario("jd-search-queued-no-records", {
    jdImportPayload: {
      status: "queued",
      job_id: "jd-queued-no-records",
      result: { status: "queued", mode: "search", search: { results: [] } },
    },
    jdJobPayload: {
      status: "running",
      result: { status: "running", mode: "search", search: { results: [] }, progress: { phase: "search", candidate_count: 0, readable_count: 0, fetched_count: 0, failed_count: 0 } },
    },
  });
  await queuedNoSearchRecords.openJd();
  await queuedNoSearchRecords.keydownJdQuery("具身智能算法工程师", { ctrlKey: true });
  const queuedStatus = queuedNoSearchRecords.settingsDocument().getElementById("jdBackgroundStatus");
  const queuedText = aggregateText(queuedStatus);
  assert.ok(queuedText.includes("正在搜索中"), "queued search should announce that discovery is still running");
  assert.strictEqual(queuedText.includes("公开网页搜索没有返回可核实"), false, "queued search must not show the terminal empty-search notice");
  assert.strictEqual(findJdNode(queuedStatus, (node) => String(node.className || "").split(/\s+/).includes("jd-search-empty")), null, "queued search must not render an empty result card");
  console.log("JD search queued: no search records stays in progress without a premature empty state");

  const sourceStillWorking = await runScenario("jd-source-working-tool-output", {
    jdImportPayload: {
      status: "running",
      result: { status: "running", mode: "source", message: "fetch_url tool output is still being processed", jd: { job_title: "旧岗位", hard_requirements: ["旧字段"] } },
    },
  });
  await sourceStillWorking.openJd();
  await sourceStillWorking.submitJdSource("Synthetic JD body");
  const sourceWorkingStatus = sourceStillWorking.settingsDocument().getElementById("jdBackgroundStatus");
  const sourceWorkingText = aggregateText(sourceWorkingStatus);
  assert.ok(sourceWorkingText.includes("正在整理中"), "source import should announce整理中 while AI/tool work is active");
  assert.strictEqual(findJdNode(sourceWorkingStatus, (node) => String(node.className || "").split(/\s+/).includes("jd-import-result-card")), null, "working source output must not render a partial JD card");
  console.log("JD source working: tool output stays behind the整理中 state until completion");
  console.log("JD aggregate result: common requirements, source evidence, and differences render as a structured card");

  const jdProgress = await runScenario("jd-search-progress", {
    jdImportPayload: {
      status: "completed",
      result: {
        status: "completed",
        mode: "search",
        metadata: { mode: "search", platform: "web", query: "灵巧手算法工程师" },
        progress: {
          phase: "completed",
          candidate_count: 20,
          readable_count: 3,
          fetched_count: 3,
          failed_count: 12,
          search_rounds: 2,
          max_search_rounds: 3,
          message: `ValueError: model comparison leaked into progress\n${"long raw model reply ".repeat(100)}`,
        },
        comparison: { candidate_count: 25, readable_fetches: 13, failed_count: 0 },
        jd: {
          job_title: "灵巧手算法工程师",
          hard_requirements: ["Python"],
          raw_summary: "3 份可读 JD 形成共性要求。",
          sample_target: 5,
          sample_count: 3,
          comparison_status: "compared",
        },
        source: {
          kind: "public_source",
          source_urls: [
            "https://careers.example.com/jobs/one",
            "https://jobs.example.org/postings/two",
          ],
          evidence: [
            { source_kind: "search_lead", lead_only: true, text: "搜索摘要不应显示为证据" },
            { kind: "public_source", readable: true, text: "岗位原文来源 1" },
          ],
        },
      },
    },
  });
  await jdProgress.openJd();
  await jdProgress.keydownJdQuery("灵巧手算法工程师", { ctrlKey: true });
  const progressStatus = jdProgress.settingsDocument().getElementById("jdBackgroundStatus");
  const progressText = aggregateText(progressStatus);
  assert.ok(progressText.includes("候选公开来源 20"), "search progress should expose the observed candidate-source count");
  assert.ok(progressText.includes("已读取页面 3"), "search progress should keep fetched-page count separate");
  assert.ok(progressText.includes("已读取独立 JD 3"), "search progress should expose readable JD count");
  assert.ok(progressText.includes("读取失败 12"), "search progress should expose observed failed-source count");
  assert.ok(progressText.includes("检索 2/3"), "search progress should expose the bounded retrieval round count");
  assert.strictEqual(progressText.includes("候选公开来源 25"), false, "model comparison counts must not inflate the observed candidate count");
  assert.strictEqual(progressText.includes("已读取页面 13"), false, "model comparison counts must not inflate the observed fetched-page count");
  assert.strictEqual(progressText.includes("ValueError"), false, "model replies must never be shown as progress messages");
  assert.strictEqual(progressText.includes("long raw model reply"), false, "long model replies must never enter the progress card");
  assert.ok(progressText.includes("岗位原文来源 1"), "verified source evidence should remain visible");
  assert.strictEqual(progressText.includes("搜索摘要不应显示为证据"), false, "search leads and snippets must not render as evidence");
  console.log("JD search progress: candidate, readable, failed counts stay distinct and snippets stay out of evidence");

  const jdMixedDirection = await runScenario("jd-direction-mismatch", {
    jdImportPayload: {
      status: "completed",
      result: {
        status: "completed",
        mode: "search",
        comparison: { direction_mismatch: true },
        jd: {
          job_title: "灵巧手算法工程师",
          sample_target: 5,
          sample_count: 4,
          comparison_status: "compared",
        },
      },
    },
  });
  await jdMixedDirection.openJd();
  await jdMixedDirection.keydownJdQuery("灵巧手算法工程师", { ctrlKey: true });
  const mixedDirectionStatus = jdMixedDirection.settingsDocument().getElementById("jdBackgroundStatus");
  const mixedDirectionText = aggregateText(mixedDirectionStatus);
  assert.strictEqual(mixedDirectionStatus.classList.contains("success"), false, "an explicit direction mismatch must not be reported as a completed comparison");
  assert.ok(mixedDirectionText.includes("不同技术方向"), "an explicit direction mismatch should explain why common requirements remain unconfirmed");
  console.log("JD comparison guard: explicit direction mismatch stays unconfirmed");

  const jdEmptySearch = await runScenario("jd-empty-public-search", {
    jdImportPayload: {
      status: "completed",
      result: {
        status: "completed",
        mode: "search",
        metadata: { mode: "search", platform: "web", query: "灵巧手算法工程师" },
        jd: {
          job_title: "灵巧手算法工程师",
          company: "company",
          hard_requirements: ["hard_requirements"],
          uncertain_fields: ["company", "hard_requirements"],
          sample_target: 5,
          sample_count: 0,
          comparison_status: "insufficient_samples",
        },
        source: { kind: "public_source", source_urls: ["https://careers.example.com/jobs/unreadable"], evidence: ["来源已尝试但未能读取"] },
        search: { ok: false, results: [], count: 0 },
      },
    },
  });
  await jdEmptySearch.openJd();
  await jdEmptySearch.keydownJdQuery("灵巧手算法工程师", { ctrlKey: true });
  const emptySearchStatus = jdEmptySearch.settingsDocument().getElementById("jdBackgroundStatus");
  const emptySearchText = aggregateText(emptySearchStatus);
  assert.ok(emptySearchText.includes("公开网页搜索没有返回可核实"), "empty public search should explain the public-web failure");
  assert.ok(emptySearchText.includes("这不表示没有符合条件的岗位"), "empty public search should not imply that no matching job exists");
  assert.ok(emptySearchText.includes("多个岗位链接、正文或截图"), "empty public search should explain the next user-provided evidence step");
  const emptySearchNodes = [];
  const visitEmptySearch = (node) => { if (!node || !node.children) return; for (const child of node.children) { emptySearchNodes.push(child); visitEmptySearch(child); } };
  visitEmptySearch(emptySearchStatus);
  const hasEmptyJdCard = emptySearchNodes.some((node) => String(node.className || "").split(/\s+/).includes("jd-import-result-card"));
  assert.strictEqual(hasEmptyJdCard, false, "zero valid search results must not render an empty JD card");
  assert.strictEqual(emptySearchText.includes("hard_requirements"), false, "zero valid search results must not expose raw English field names");
  const openPublicSearch = emptySearchNodes.find((node) => node.tagName === "BUTTON" && String(node.textContent || "").includes("打开公开网页搜索"));
  assert.ok(openPublicSearch, "empty public search should expose the generic public-web search action");
  openPublicSearch.click();
  await jdEmptySearch.flush();
  const openedPublicSearch = jdEmptySearch.openedWindows().find((item) => item.url.includes("www.bing.com/search?q="));
  assert.ok(openedPublicSearch, "site-search action should open the fixed public-web search destination");
  assert.ok(openedPublicSearch.url.includes(encodeURIComponent("灵巧手算法工程师")), "site-search action should encode the current job query");
  console.log("JD empty public search: readable failure, no blank card, and fixed encoded public-web recovery passed");

  const jdInsufficient = await runScenario("jd-insufficient-result", {
    jdImportPayload: {
      status: "completed",
      result: {
        status: "completed",
        jd: {
          job_title: "具身智能算法工程师",
          hard_requirements: ["Python"],
          raw_summary: "仅核实 1 份来源，暂不足形成共性。",
          uncertain_fields: ["可比样本不足"],
          sample_target: 5,
          sample_count: 1,
          comparison_status: "insufficient_samples",
        },
        source: { kind: "public_source", source_urls: ["https://careers.example.com/jobs/one"], evidence: ["来源 1 已读取"] },
      },
    },
  });
  await jdInsufficient.openJd();
  await jdInsufficient.keydownJdQuery("具身智能算法工程师", { ctrlKey: true });
  const insufficientStatus = jdInsufficient.settingsDocument().getElementById("jdBackgroundStatus");
  const insufficientText = aggregateText(insufficientStatus);
  assert.strictEqual(insufficientStatus.classList.contains("success"), false, "insufficient search coverage should not look complete");
  assert.ok(insufficientText.includes("尚未完成共性汇总"), "insufficient search coverage should explain why aggregation is incomplete");
  assert.ok(insufficientText.includes("尚未形成共性"), "insufficient search card should avoid labeling a single sample as common");
  console.log("JD aggregate result: one-source output remains explicitly incomplete");

  const jdInsufficientTwo = await runScenario("jd-insufficient-two-folded-evidence", {
    jdImportPayload: {
      status: "completed",
      result: {
        status: "completed",
        mode: "search",
        metadata: { mode: "search", query: "具身智能算法工程师" },
        jd: {
          job_title: "具身智能算法工程师",
          hard_requirements: ["Python 服务开发"],
          skill_tags: ["Python", "PyTorch"],
          responsibilities: ["负责算法方案落地"],
          nice_to_haves: ["有量产经验"],
          raw_summary: "样本分析很长：来源一支持 Python 与 PyTorch；来源二补充了算法落地和部署经验。两份来源方向相近但仍不足以形成可靠共性，单样本差异需要继续核对。",
          uncertain_fields: ["可比样本不足"],
          sample_target: 5,
          sample_count: 2,
          comparison_status: "insufficient_samples",
        },
        source: {
          kind: "public_source",
          source_urls: ["https://careers.example.com/jobs/one", "https://jobs.example.org/postings/two"],
          evidence: ["来源 1 已读取", "来源 2 已读取"],
        },
      },
    },
  });
  await jdInsufficientTwo.openJd();
  await jdInsufficientTwo.keydownJdQuery("具身智能算法工程师", { ctrlKey: true });
  const insufficientTwoStatus = jdInsufficientTwo.settingsDocument().getElementById("jdBackgroundStatus");
  const insufficientTwoText = aggregateText(insufficientTwoStatus);
  const insufficientTwoCard = findJdNode(insufficientTwoStatus, (node) => String(node.className || "").split(/\s+/).includes("jd-import-result-card"));
  assert.ok(insufficientTwoCard, "two readable samples should still render the limited structured JD card");
  assert.ok(insufficientTwoText.includes("仅核实 2 份同类 JD"), "two-sample search should state the limited evidence count");
  assert.ok(insufficientTwoText.includes("尚未形成共性"), "two-sample search must preserve the incomplete comparison notice");
  assert.strictEqual(insufficientTwoText.includes("共性硬性要求"), false, "two-sample search must not label sample requirements as common");
  const insufficientAnalysis = findJdNode(insufficientTwoCard, (node) => String(node.className || "").split(/\s+/).includes("jd-analysis-details"));
  const insufficientEvidence = findJdNode(insufficientTwoCard, (node) => String(node.className || "").split(/\s+/).includes("jd-evidence-details"));
  assert.ok(insufficientAnalysis && insufficientEvidence, "sample analysis and source evidence should each be collapsible");
  assert.strictEqual(Boolean(insufficientAnalysis.open), false, "insufficient sample analysis should not dominate the primary card");
  assert.strictEqual(Boolean(insufficientEvidence.open), false, "source evidence should be collapsed by default");
  console.log("JD insufficient samples: two-source evidence stays explicit and folded without inventing common requirements");

  const jdEnoughButDisagreeing = await runScenario("jd-enough-but-disagreeing", {
    jdImportPayload: {
      status: "completed",
      result: {
        status: "completed",
        mode: "search",
        jd: {
          job_title: "具身智能算法工程师",
          raw_summary: "已读取 3 份来源，但技术侧重点存在差异，暂不形成可靠共性。",
          sample_target: 5,
          sample_count: 3,
          comparison_status: "insufficient_samples",
        },
        source: { kind: "public_source", source_urls: ["https://careers.example.com/jobs/one", "https://jobs.example.org/postings/two", "https://company.example.net/careers/three"], evidence: ["来源 1 已读取", "来源 2 已读取", "来源 3 已读取"] },
      },
    },
  });
  await jdEnoughButDisagreeing.openJd();
  await jdEnoughButDisagreeing.keydownJdQuery("具身智能算法工程师", { ctrlKey: true });
  const enoughButDisagreeingStatus = jdEnoughButDisagreeing.settingsDocument().getElementById("jdBackgroundStatus");
  const enoughButDisagreeingText = aggregateText(enoughButDisagreeingStatus);
  assert.ok(enoughButDisagreeingText.includes("已核实 3 份同类 JD"), "three readable samples should be named explicitly");
  assert.ok(enoughButDisagreeingText.includes("尚未形成可靠共性"), "three disagreeing samples should explain the comparison failure");
  assert.strictEqual(enoughButDisagreeingText.includes("少于目标 3-5 份"), false, "three readable samples must not be described as a sample-count shortage");
  console.log("JD comparison wording: three readable but disagreeing samples are distinguished from insufficient volume");

  const jdSingleSource = await runScenario("jd-single-source-card", {
    jdImportPayload: {
      status: "completed",
      result: {
        status: "completed",
        jd: {
          job_title: "后端工程师",
          hard_requirements: ["Python"],
          raw_summary: "已读取单条岗位来源。",
          sample_target: 1,
          sample_count: 1,
          comparison_status: "single_source",
        },
        source: { kind: "public_source", source_urls: ["https://careers.example.com/jobs/one"], evidence: ["来源已读取"] },
      },
    },
  });
  await jdSingleSource.openJd();
  await jdSingleSource.submitJdSource("Synthetic single-source JD", "https://careers.example.com/jobs/one");
  const singleSourceStatus = jdSingleSource.settingsDocument().getElementById("jdBackgroundStatus");
  const singleSourceText = aggregateText(singleSourceStatus);
  assert.ok(singleSourceText.includes("JD 已整理并同步到本页"), "ordinary single-source JD should keep the normal completion message");
  assert.ok(singleSourceText.includes("硬性要求"), "ordinary single-source JD should use the normal requirement label");
  assert.strictEqual(singleSourceText.includes("尚未形成共性"), false, "ordinary single-source JD should not be treated as an incomplete comparison");
  console.log("JD single-source result: sample_target=1 stays a normal source result");

  const jdImageInput = await runScenario("jd-image-input", {});
  await jdImageInput.openJd();
  // The harness exposes the real mode buttons through the document tree; the
  // synthetic query shim is intentionally minimal, so locate the button by
  // its data attribute through the same traversal used by the scenario.
  const allImageNodes = [];
  const visitImageNodes = (node) => { if (!node || !node.children) return; for (const child of node.children) { allImageNodes.push(child); visitImageNodes(child); } };
  visitImageNodes(jdImageInput.settingsDocument().body);
  const imageTab = allImageNodes.find((node) => node.dataset && node.dataset.jdMode === "image");
  assert.ok(imageTab, "JD import should expose the image mode tab");
  imageTab.click();
  await jdImageInput.flush();
  const firstBatch = [
    { name: "jd-one.png", type: "image/png", size: 1024 },
    { name: "jd-two.jpg", type: "image/jpeg", size: 2048 },
  ];
  await jdImageInput.selectJdImages(firstBatch);
  await jdImageInput.pasteJdImages([{ name: "jd-three.webp", type: "image/webp", size: 4096 }]);
  const imageList = jdImageInput.settingsDocument().getElementById("jdImageList");
  assert.strictEqual(imageList.children.length, 3, "multiple selections and consecutive pastes should accumulate JD images");
  assert.ok(jdImageInput.settingsDocument().getElementById("jdImageSummary").textContent.includes("3/10"), "JD image summary should show the selected count limit");
  imageList.children[0].children[1].click();
  await jdImageInput.flush();
  assert.strictEqual(imageList.children.length, 2, "each JD image should be removable independently");
  await jdImageInput.selectJdImages([{ name: "too-large.png", type: "image/png", size: 21 * 1024 * 1024 }]);
  assert.strictEqual(imageList.children.length, 2, "oversized JD images should be rejected before upload");
  assert.ok(jdImageInput.settingsDocument().getElementById("jdImportStatus").textContent.includes("超过 10 MiB"), "oversized JD images should explain the per-file limit");
  console.log("JD image input: multiple selection, consecutive paste, per-image removal, and limits passed");

  const jdFailure = await runScenario("jd-background-failure", { jdImportPayload: { status: "failed", result: { status: "failed", message: "Synthetic model failure", jd: { job_title: "不应显示的旧岗位", hard_requirements: ["stale"] } } } });
  await jdFailure.openJd();
  await jdFailure.submitJdSource("Synthetic JD failure");
  const jdFailureStatus = jdFailure.settingsDocument().getElementById("jdBackgroundStatus");
  const jdFailureText = (node) => [String(node.textContent || ""), ...Array.from(node.children || []).map(jdFailureText)].join(" ");
  assert.strictEqual(jdFailureStatus.classList.contains("error"), true, "JD terminal failures should be marked on the current page");
  const jdFailureBody = jdFailureStatus.children.find((node) => String(node.className || "").split(/\s+/).includes("jd-status-body"));
  assert.ok(jdFailureBody && jdFailureBody.children[1].textContent.includes("JD 整理未能完成"), "JD terminal failures should show a user-readable summary");
  const jdFailureDiagnostic = findJdNode(jdFailureStatus, (node) => String(node.className || "").split(/\s+/).includes("jd-status-diagnostics"));
  assert.ok(jdFailureDiagnostic && !jdFailureDiagnostic.open, "JD failure diagnostics should be collapsed by default");
  assert.ok(jdFailureText(jdFailureDiagnostic).includes("Synthetic model failure"), "the raw failure detail should remain available in the collapsed diagnostic");
  assert.strictEqual(findJdNode(jdFailureStatus, (node) => String(node.className || "").split(/\s+/).includes("jd-import-result-card")), null, "terminal JD failures must not render a stale structured card");
  assert.strictEqual(jdFailure.calls.some((call) => call.path === "/sessions/session-1/message"), false, "JD failures must not create a main user prompt");
  console.log("JD background failure: readable summary and collapsed diagnostic stay on the current page");

  const jdSearchFailure = await runScenario("jd-search-background-failure", {
    jdImportPayload: {
      status: "failed",
      diagnostic: "ValueError: private tool detail\nraw internal traceback",
      result: {
        status: "failed",
        mode: "search",
        message: `ValueError: model final text\n${"raw model reply ".repeat(80)}`,
        progress: {
          phase: "failed",
          status: "failed",
          candidate_count: 0,
          readable_count: 0,
          fetched_count: 0,
          failed_count: 0,
          message: `ValueError: ${"bad progress text ".repeat(80)}`,
        },
      },
    },
  });
  await jdSearchFailure.openJd();
  await jdSearchFailure.keydownJdQuery("灵巧手算法工程师", { ctrlKey: true });
  const jdSearchFailureStatus = jdSearchFailure.settingsDocument().getElementById("jdBackgroundStatus");
  const jdSearchFailureText = aggregateText(jdSearchFailureStatus);
  assert.ok(jdSearchFailureText.includes("岗位搜索与核验未能完成"), "search failures should display a user-readable summary");
  assert.strictEqual(jdSearchFailureText.includes("raw model reply"), false, "raw model final text must not appear in the search progress card");
  assert.strictEqual(jdSearchFailureText.includes("bad progress text"), false, "raw model text must not be used as the progress message");
  const jdSearchDiagnostic = findJdNode(jdSearchFailureStatus, (node) => String(node.className || "").split(/\s+/).includes("jd-status-diagnostics"));
  assert.ok(jdSearchDiagnostic && !jdSearchDiagnostic.open, "search failure diagnostics should stay folded");
  assert.ok(jdFailureText(jdSearchDiagnostic).includes("ValueError"), "bounded diagnostic context should remain available when expanded");
  console.log("JD search failure: model text stays out of progress and diagnostics remain folded");

  const jdRunningNestedFailure = await runScenario("jd-running-nested-failure", {
    jdImportPayload: { status: "queued", job_id: "jd-running-inner-failure", result: { status: "queued", mode: "source" } },
    jdJobPayload: { status: "running", result: { status: "failed", message: "Synthetic inner tool failure", jd: { job_title: "不应提前展示" } } },
  });
  await jdRunningNestedFailure.openJd();
  await jdRunningNestedFailure.submitJdSource("Synthetic running nested failure");
  const jdRunningNestedStatus = jdRunningNestedFailure.settingsDocument().getElementById("jdBackgroundStatus");
  assert.strictEqual(jdRunningNestedStatus.classList.contains("loading"), true, "a running outer job must stay loading when its nested RunResult reports failure");
  assert.strictEqual(jdRunningNestedStatus.classList.contains("error"), false, "a nested tool failure must not mark the active outer job as terminal error");
  assert.strictEqual(findJdNode(jdRunningNestedStatus, (node) => String(node.className || "").split(/\s+/).includes("jd-import-result-card")), null, "an active outer job must not render a nested partial JD");
  console.log("JD nested lifecycle: running outer job remains loading despite inner failure");

  const jdNestedFailure = await runScenario("jd-background-nested-failure", { jdImportPayload: { status: "completed", result: { status: "failed", message: "Synthetic nested failure" } } });
  await jdNestedFailure.openJd();
  await jdNestedFailure.submitJdSource("Synthetic nested failure");
  const jdNestedFailureStatus = jdNestedFailure.settingsDocument().getElementById("jdBackgroundStatus");
  assert.strictEqual(jdNestedFailureStatus.classList.contains("error"), true, "a terminal completed outer job should preserve a nested terminal RunResult failure");
  const jdNestedFailureDiagnostic = findJdNode(jdNestedFailureStatus, (node) => String(node.className || "").split(/\s+/).includes("jd-status-diagnostics"));
  assert.ok(jdNestedFailureDiagnostic && !jdNestedFailureDiagnostic.open, "nested failure diagnostics should remain folded by default");
  assert.ok(jdFailureText(jdNestedFailureDiagnostic).includes("Synthetic nested failure"), "the completed job may retain its inner diagnostic message");

  const jdQueuedImmediate = await runScenario("jd-background-nested-queued", { jdImportPayload: { status: "queued", result: { status: "queued", message: "Synthetic queued" } } });
  await jdQueuedImmediate.openJd();
  await jdQueuedImmediate.submitJdSource("Synthetic nested queued");
  const jdQueuedStatus = jdQueuedImmediate.settingsDocument().getElementById("jdBackgroundStatus");
  assert.strictEqual(jdQueuedStatus.classList.contains("loading"), true, "outer queued jobs must remain in progress");
  assert.strictEqual(jdQueuedStatus.classList.contains("success"), false, "nested queued results must not be reported as success");
  console.log("JD background nested status: queued and failed results preserve terminal state semantics passed");

  const jdApproval = await runScenario("jd-background-approval", { jdImportPayload: {
    status: "completed",
    result: { status: "pending_approval", pending: { id: "jd-approval-1", tool_name: "search_jobs", summary: "需确认岗位搜索" } },
  } });
  await jdApproval.openJd();
  await jdApproval.submitJdSource("Synthetic JD approval");
  const jdApprovalStatus = jdApproval.settingsDocument().getElementById("jdBackgroundStatus");
  const jdApprovalNodes = [];
  const visitJdApproval = (node) => { if (!node || !node.children) return; for (const child of node.children) { jdApprovalNodes.push(child); visitJdApproval(child); } };
  visitJdApproval(jdApprovalStatus);
  const approveJd = jdApprovalNodes.find((node) => node.dataset && node.dataset.jdAction === "approve");
  assert.ok(approveJd, "JD approval should render an inline current-page action");
  approveJd.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(jdApproval.calls.some((call) => call.path === "/sessions/session-1/approve" && JSON.parse(call.options.body).id === "jd-approval-1"), "JD approval should remain on the current page");
  assert.strictEqual(jdApproval.calls.some((call) => call.path === "/sessions/session-1/message"), false, "JD approval must not open or submit a main prompt");
  assert.ok((jdApproval.settingsDocument().getElementById("jdBackgroundStatus").textContent || "").includes("当前页") || !jdApproval.settingsDocument().getElementById("jdBackgroundStatus").classList.contains("hidden"), "JD approval result should remain visible on the current page");
  console.log("JD background approval: current-page action and terminal result passed");

  const profileNative = await runScenario("profile-local-native", {
    profilePickerKind: "file",
    profilePickerResult: { selected: true, path: "C:/notes" },
  });
  await profileNative.openProfileLocal("file");
  assert.deepStrictEqual(profileNative.profilePickerCalls(), [{ kind: "file", initialPath: "" }], "local file button should call pick_local_file with the current path");
  assert.ok(profileNative.calls.some((call) => call.path === "/imports/local" && JSON.parse(call.options.body).path === "C:/notes"), "native local selection should import the selected path");
  assert.ok(profileNative.settingsDocument().getElementById("profileLocalStatus").textContent.includes("已加入本地日志材料"), "native local import should report the imported material");
  console.log("profile local picker: pick_local_file received initial path and imported the selected location");

  const profileCanceled = await runScenario("profile-local-canceled", {
    profilePickerKind: "folder",
    profilePickerResult: { ok: true, selected: false, canceled: true },
  });
  await profileCanceled.openProfileLocal("folder");
  assert.deepStrictEqual(profileCanceled.profilePickerCalls(), [{ kind: "folder", initialPath: "" }], "local folder button should call pick_local_folder with the current path");
  assert.strictEqual(profileCanceled.settingsDocument().getElementById("profileLocalStatus").textContent, "已取消选择。", "local picker cancellation should remain a neutral state");
  assert.strictEqual(profileCanceled.calls.some((call) => call.path === "/imports/local"), false, "canceled local picker must not import a path");
  console.log("profile local picker: file/folder cancellation remains distinct from errors");

  const profileFallback = await runScenario("profile-local-browser-fallback", {});
  await profileFallback.openProfileLocal("file");
  assert.strictEqual(profileFallback.settingsDocument().getElementById("profileLocalStatus").textContent.includes("系统"), false, "browser fallback should not expose implementation details");
  await profileFallback.selectProfileLocalBrowserFiles("file", [{ name: "note.md", size: 128, webkitRelativePath: "Vault/note.md" }]);
  assert.strictEqual(profileFallback.settingsDocument().getElementById("profileLocalFileList").children.length, 1, "browser fallback should retain a selected note");
  console.log("profile local picker: missing bridge fell back to browser file selection");

  const profileFolderFallback = await runScenario("profile-local-folder-filter", {});
  await profileFolderFallback.openProfileLocal("folder");
  await profileFolderFallback.selectProfileLocalBrowserFiles("folder", [
    { name: "good.md", size: 128, webkitRelativePath: "Vault/notes/good.md" },
    { name: "secret.md", size: 128, webkitRelativePath: "Vault/.obsidian/secret.md" },
    { name: ".env", size: 128, webkitRelativePath: "Vault/.env" },
    { name: "cache.md", size: 128, webkitRelativePath: "Vault/cache/cache.md" },
    { name: "run.exe", size: 128, webkitRelativePath: "Vault/run.exe" },
    { name: "large.md", size: 2 * 1024 * 1024 + 1, webkitRelativePath: "Vault/large.md" },
  ]);
  const profileFolderStatus = profileFolderFallback.settingsDocument().getElementById("profileLocalStatus").textContent;
  assert.strictEqual(profileFolderFallback.settingsDocument().getElementById("profileLocalFileList").children.length, 1, "folder fallback should keep only supported safe documents");
  assert.ok(profileFolderStatus.includes("已跳过 5 个"), "folder fallback should show the skipped file count");
  console.log("profile local folder fallback: hidden, sensitive, unsupported, and oversized files were filtered");

  let resolveLateProfilePicker;
  const lateProfilePicker = new Promise((resolve) => { resolveLateProfilePicker = resolve; });
  const profileLate = await runScenario("profile-local-session-guard", {
    profilePickerKind: "file",
    profilePickerResult: () => lateProfilePicker,
    alternateSession: true,
  });
  await profileLate.openProfileLocal("file");
  await profileLate.switchProfileSession("session-2");
  resolveLateProfilePicker({ selected: true, path: "late-notes" });
  await profileLate.flush();
  assert.strictEqual(profileLate.settingsDocument().getElementById("profileLocalPath").value, "", "late picker results must not repopulate a different session");
  assert.strictEqual(profileLate.calls.some((call) => call.path === "/imports/local"), false, "late picker results must not import into a different session");
  console.log("profile local picker: late native result was ignored after switching sessions");

  const interview = await runScenario("interview-window", {});
  await interview.openInterview();
  assert.ok(interview.openedWindows().some((item) => item.url.includes("view=interview") && item.url.includes("session_id=session-1")), "访谈 tab should open a session-scoped dedicated window");
  assert.strictEqual(interview.settingsDocument().getElementById("workflowChatBackdrop").attributes["aria-hidden"], "true", "opening interview should not open the main coach dialog");
  assert.strictEqual(interview.calls.some((call) => call.path === "/sessions/session-1/message"), false, "opening interview should not persist a main prompt");
  console.log("interview tab: dedicated session window opened without main chat pollution passed");

  const interviewError = await runScenario("interview-last-result-error", {
    locationSearch: "?view=interview&session_id=session-1&start=1",
    interviewSnapshotPayload: {
      messages: [], events: [], busy: false, interview_busy: false,
      opening_started: false, opening_completed: false,
      last_result: { status: "error", error: "Synthetic interview opening failure", metadata: { scope: "interview", interview_thread_id: "interview-1" } }
    }
  });
  await interviewError.flush();
  const interviewErrorDocument = interviewError.settingsDocument();
  const interviewText = (node) => [String(node.textContent || ""), ...Array.from(node.children || []).map(interviewText)].join(" ");
  const errorText = interviewText(interviewErrorDocument.getElementById("messageList"));
  assert.ok(errorText.includes("Synthetic interview opening failure"), "interview last_result errors should be visible inside the scoped thread");
  const findNode = (node, predicate) => {
    if (!node || !node.children) return null;
    for (const child of node.children) { if (predicate(child)) return child; const found = findNode(child, predicate); if (found) return found; }
    return null;
  };
  const retryInterview = findNode(interviewErrorDocument.getElementById("messageList"), (node) => node.dataset && node.dataset.interviewRetry === "start");
  assert.ok(retryInterview, "interview startup errors should offer a scoped retry");
  retryInterview.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(interviewError.calls.some((call) => call.path === "/sessions/session-1/interview" && JSON.parse(call.options.body).interview_thread_id === "interview-1"), "interview retry should re-open the same durable thread");

  const interviewApproval = await runScenario("interview-thread-approval", {
    locationSearch: "?view=interview&session_id=session-1&start=1",
    interviewSnapshotPayload: {
      messages: [], events: [], busy: false, interview_busy: false,
      pending: { id: "interview-approval-1", scope: "interview", interview_thread_id: "interview-1", tool_name: "write_file", summary: "访谈更新技能档案", preview: "{}" },
      execution: {}, queued_calls: 0
    }
  });
  const interviewApprovalDocument = interviewApproval.settingsDocument();
  const approveInterview = findNode(interviewApprovalDocument.getElementById("approvalRegion"), (node) => node.dataset && node.dataset.action === "approve");
  assert.ok(approveInterview, "interview pending actions should render in the dedicated thread");
  approveInterview.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(interviewApproval.calls.some((call) => call.path === "/sessions/session-1/approve" && JSON.parse(call.options.body).id === "interview-approval-1"), "interview approval should use the same scoped session recovery endpoint");
  assert.strictEqual(interviewApproval.calls.some((call) => call.path === "/sessions/session-1/message"), false, "interview approval must not route through the main conversation");

  const interviewQueue = await runScenario("interview-thread-queue", {
    locationSearch: "?view=interview&session_id=session-1&start=1",
    interviewSnapshotPayload: {
      messages: [], events: [], busy: false, interview_busy: false,
      pending: null,
      execution: { scope: "interview", interview_thread_id: "interview-1", status: "paused" }, queued_calls: 2
    }
  });
  const continueInterview = findNode(interviewQueue.settingsDocument().getElementById("approvalRegion"), (node) => node.dataset && node.dataset.action === "continue");
  assert.ok(continueInterview, "interview queued calls should render a continue action in the dedicated thread");
  continueInterview.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(interviewQueue.calls.some((call) => call.path === "/sessions/session-1/continue"), "interview queue recovery should stay on the current session thread");
  console.log("interview thread: last-result retry, approval, and queue recovery stay scoped passed");
  console.log("web static DOM queue regression scenarios passed");
})().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });
