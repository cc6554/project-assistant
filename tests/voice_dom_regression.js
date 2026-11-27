"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const projectRoot = path.resolve(__dirname, "..");
const appPath = path.join(projectRoot, "src/agent_coach/web_static/app.js");

class FakeElement {
  constructor(document, tagName = "div") {
    this.ownerDocument = document;
    this.nodeType = 1;
    this.tagName = String(tagName).toUpperCase();
    this.id = "";
    this.type = this.tagName === "TEXTAREA" ? "textarea" : "text";
    this.inputMode = "";
    this.disabled = false;
    this.readOnly = false;
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.className = "";
    const classNames = new Set();
    this.classList = {
      add(name) { classNames.add(name); },
      remove(name) { classNames.delete(name); },
      toggle(name, force) {
        const next = force === undefined ? !classNames.has(name) : Boolean(force);
        if (next) classNames.add(name); else classNames.delete(name);
        return next;
      },
      contains(name) { return classNames.has(name); },
      forEach(callback) { classNames.forEach(callback); },
      [Symbol.iterator]() { return classNames[Symbol.iterator](); }
    };
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this.children = [];
    this.listeners = new Map();
    this.connected = true;
    this.parentNode = null;
  }
  appendChild(child) {
    if (child) {
      if (typeof child === "object") child.parentNode = this;
      this.children.push(child);
    }
    return child;
  }
  append(...children) { children.forEach((child) => this.appendChild(child)); }
  addEventListener(name, handler) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(handler);
  }
  removeEventListener(name, handler) {
    this.listeners.set(name, (this.listeners.get(name) || []).filter((item) => item !== handler));
  }
  dispatchEvent(event) {
    const current = { ...event, target: event.target || this, currentTarget: this };
    for (const handler of this.listeners.get(event.type) || []) handler(current);
    return true;
  }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  closest(selector) {
    const selectors = String(selector || "").split(",").map((item) => item.trim());
    return selectors.includes(this.closestMatch || "") ? {} : null;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  remove() { this.connected = false; }
}

class FakeDocument {
  constructor() {
    this.documentElement = new FakeElement(this, "html");
    this.body = new FakeElement(this, "body");
    this.activeElement = this.body;
    this.visibilityState = "visible";
    this.toastRegion = new FakeElement(this, "div");
    this.toastRegion.id = "toastRegion";
    this.elements = [this.documentElement, this.body, this.toastRegion];
  }
  createElement(tagName) {
    const element = new FakeElement(this, tagName);
    this.elements.push(element);
    return element;
  }
  createTextNode(text) { return { textContent: String(text), nodeType: 3, parentNode: null }; }
  getElementById(id) { return this.elements.find((element) => element.id === id) || null; }
  querySelector() { return null; }
  querySelectorAll(selector) {
    if (selector === "[data-voice-input-button]") return this.elements.filter((element) => element.dataset.voiceInputButton === "true");
    if (selector === "[data-voice-level]") return this.elements.filter((element) => element.dataset.voiceLevel === "true");
    if (selector === "[data-voice-status]") return this.elements.filter((element) => element.dataset.voiceStatus === "true");
    return [];
  }
  addEventListener() {}
  contains(node) { return Boolean(node && node.connected !== false); }
}

class FakeTrack {
  constructor() { this.stopCalls = 0; this.stopped = false; }
  stop() { this.stopCalls += 1; this.stopped = true; }
}

class FakeStream {
  constructor() { this.tracks = [new FakeTrack()]; }
  getTracks() { return this.tracks; }
}

class FakeMediaRecorder {
  constructor(stream, options = {}) {
    this.stream = stream;
    this.mimeType = options.mimeType || "audio/webm";
    this.state = "inactive";
    this.startCalls = 0;
    this.stopCalls = 0;
    FakeMediaRecorder.instances.push(this);
  }
  static isTypeSupported(value) { return value === "audio/webm;codecs=opus"; }
  start() { this.startCalls += 1; this.state = "recording"; }
  stop() { this.stopCalls += 1; this.state = "inactive"; }
  emitData(value) {
    if (typeof this.ondataavailable === "function") this.ondataavailable({ data: new Blob([value], { type: this.mimeType }) });
  }
  emitStop() { if (typeof this.onstop === "function") this.onstop(); }
}
FakeMediaRecorder.instances = [];

class FakeHeaders {
  constructor(values = {}) { this.values = new Map(Object.entries(values)); }
  set(name, value) { this.values.set(String(name).toLowerCase(), String(value)); }
  get(name) { return this.values.get(String(name).toLowerCase()) || null; }
}

class FakeAbortController {
  constructor() {
    const listeners = new Set();
    this.signal = {
      aborted: false,
      addEventListener(name, handler) { if (name === "abort") listeners.add(handler); },
      removeEventListener(name, handler) { if (name === "abort") listeners.delete(handler); }
    };
    this.abortCalls = 0;
    this._listeners = listeners;
    FakeAbortController.instances.push(this);
  }
  abort() {
    this.abortCalls += 1;
    if (this.signal.aborted) return;
    this.signal.aborted = true;
    for (const listener of this._listeners) listener();
  }
}
FakeAbortController.instances = [];

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function createTimerQueue() {
  let nextId = 1;
  const timers = new Map();
  return {
    timers,
    setTimeout(callback, delay) {
      const id = nextId++;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
    run(delay) {
      const entry = [...timers.entries()].find(([, value]) => value.delay === delay);
      assert.ok(entry, "expected timer " + delay);
      timers.delete(entry[0]);
      entry[1].callback();
    }
  };
}

function response(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new FakeHeaders({ "content-type": "application/json" }),
    async json() { return payload; },
    async text() { return JSON.stringify(payload); }
  };
}

async function run() {
  const document = new FakeDocument();
  const timerQueue = createTimerQueue();
  const test = {
    timerQueue,
    getUserMediaCalls: 0,
    streams: [],
    permissionQueue: [],
    statusResponses: [],
    transcribeRequests: [],
    fetchCalls: [],
    toastRegion: document.toastRegion,
    deferPermission() {
      const item = deferred();
      this.permissionQueue.push(item);
      return item;
    },
    deferStatus({ ignoreAbort = false } = {}) {
      const item = deferred();
      item.ignoreAbort = ignoreAbort;
      this.statusResponses.push(item);
      return item;
    }
  };
  const window = {
    MediaRecorder: FakeMediaRecorder,
    AbortController: FakeAbortController,
    btoa: (value) => Buffer.from(value, "binary").toString("base64"),
    setTimeout: timerQueue.setTimeout,
    clearTimeout: timerQueue.clearTimeout,
    requestAnimationFrame() { return 1; },
    cancelAnimationFrame() {},
    addEventListener() {},
    removeEventListener() {},
    localStorage: { getItem() { return null; }, setItem() {} },
    sessionStorage: { getItem() { return null; }, setItem() {} },
    location: { hash: "", pathname: "/", search: "" },
    __voiceTest: test
  };
  const navigator = {
    language: "zh-CN",
    userAgent: "offline-voice-dom-regression",
    mediaDevices: {
      getUserMedia: async () => {
        test.getUserMediaCalls += 1;
        if (test.permissionQueue.length) {
          const item = test.permissionQueue.shift();
          const stream = new FakeStream();
          item.stream = stream;
          return item.promise.then(() => {
            test.streams.push(stream);
            return stream;
          });
        }
        const stream = new FakeStream();
        test.streams.push(stream);
        return stream;
      }
    }
  };
  const fetch = async (url, options = {}) => {
    test.fetchCalls.push({ url, options });
    if (url === "/api/speech/status") {
      if (test.statusResponses.length) {
        const item = test.statusResponses.shift();
        return item.ignoreAbort ? item.promise : abortable(item.promise, options.signal);
      }
      return response({ available: true, mode: "offline", message: "本机离线语音已就绪。", max_duration_seconds: 120 });
    }
    if (url === "/api/speech/transcribe") {
      const item = deferred();
      item.url = url;
      item.options = options;
      item.body = JSON.parse(options.body);
      test.transcribeRequests.push(item);
      return abortable(item.promise, options.signal);
    }
    return response({});
  };
  function abortable(promise, signal) {
    if (!signal) return promise;
    if (signal.aborted) return Promise.reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
    return new Promise((resolve, reject) => {
      const onAbort = () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
      signal.addEventListener("abort", onAbort);
      promise.then(
        (value) => { signal.removeEventListener("abort", onAbort); resolve(value); },
        (error) => { signal.removeEventListener("abort", onAbort); reject(error); }
      );
    });
  }
  const context = {
    window,
    document,
    navigator,
    assert,
    console,
    fetch,
    Headers: FakeHeaders,
    Event: class Event {
      constructor(type, options = {}) { this.type = type; this.bubbles = Boolean(options.bubbles); }
    },
    setTimeout: timerQueue.setTimeout,
    clearTimeout: timerQueue.clearTimeout,
    URL,
    URLSearchParams,
    FormData: global.FormData || class FormData {},
    Blob,
    File: global.File || class File {},
    TextEncoder: global.TextEncoder,
    TextDecoder: global.TextDecoder,
    Uint8Array,
    AbortController: FakeAbortController,
    btoa: window.btoa,
    crypto: global.crypto,
    FakeMediaRecorder,
    FakeAbortController,
    response
  };

  const appSource = fs.readFileSync(appPath, "utf8");
  const htmlSource = fs.readFileSync(path.join(projectRoot, "src/agent_coach/web_static/index.html"), "utf8");
  const controlMarkup = (id) => htmlSource.match(new RegExp(`<(?:(?:input)|(?:textarea))\\b[^>]*\\bid="${id}"[^>]*>`))?.[0] || "";
  for (const id of ["profileLocalPath", "localImportPath", "workspaceSwitchPath", "jdSourceUrl"]) {
    assert.match(controlMarkup(id), /data-voice-exclude="true"/, `${id} should be explicitly excluded from automatic voice input`);
  }
  const testCode = String.raw`
    return (async () => {
      const test = window.__voiceTest;
      const flush = async () => { for (let index = 0; index < 40; index += 1) await Promise.resolve(); };
      const makeInput = (id, value = "") => {
        const input = document.createElement("textarea");
        input.id = id;
        input.value = value;
        return input;
      };
      const voiceFields = {
        obsidianPath: makeInput("profileLocalPath"),
        readonlyNaturalText: makeInput("read-only", "existing"),
        disabledNaturalText: makeInput("disabled", "existing"),
        workspacePath: makeInput("workspaceSwitchPath"),
        localImportPath: makeInput("localImportPath"),
        jdSourceUrl: makeInput("jdSourceUrl"),
        jdQuery: makeInput("jdQuery"),
        skillName: makeInput("skill-name"),
        planInclude: makeInput("planInclude"),
        clarification: makeInput("clarificationAnswer"),
        modelName: makeInput("modelName"),
        githubName: makeInput("githubProfileName"),
        composer: makeInput("composerInput")
      };
      voiceFields.obsidianPath.type = "text";
      voiceFields.obsidianPath.readOnly = true;
      voiceFields.readonlyNaturalText.readOnly = true;
      voiceFields.disabledNaturalText.disabled = true;
      voiceFields.workspacePath.type = "text";
      voiceFields.workspacePath.dataset.voiceExclude = "true";
      voiceFields.localImportPath.type = "text";
      voiceFields.localImportPath.dataset.voiceExclude = "true";
      voiceFields.jdSourceUrl.inputMode = "url";
      voiceFields.modelName.closestMatch = "#modelSettingsForm";
      voiceFields.githubName.closestMatch = "#githubProfileForm";
      voiceFields.composer.tagName = "TEXTAREA";
      assert.strictEqual(voiceInputAllowed(voiceFields.obsidianPath), false, "read-only Obsidian path must not receive voice input");
      assert.strictEqual(voiceInputAllowed(voiceFields.readonlyNaturalText), false, "all read-only fields must be excluded");
      assert.strictEqual(voiceInputAllowed(voiceFields.disabledNaturalText), false, "all disabled fields must be excluded");
      assert.strictEqual(voiceInputAllowed(voiceFields.workspacePath), false, "workspace path must remain excluded");
      assert.strictEqual(voiceInputAllowed(voiceFields.localImportPath), false, "local import path must remain excluded");
      assert.strictEqual(voiceInputAllowed(voiceFields.jdSourceUrl), false, "URL-mode textareas must not receive voice input");
      assert.strictEqual(voiceInputAllowed(voiceFields.modelName), false, "model configuration fields must be excluded");
      assert.strictEqual(voiceInputAllowed(voiceFields.githubName), false, "GitHub credential configuration fields must be excluded");
      assert.strictEqual(voiceInputAllowed(voiceFields.jdQuery), true, "target job title should retain voice input");
      assert.strictEqual(voiceInputAllowed(voiceFields.skillName), true, "skill name should retain voice input");
      assert.strictEqual(voiceInputAllowed(voiceFields.planInclude), true, "project search keywords should retain voice input");
      assert.strictEqual(voiceInputAllowed(voiceFields.clarification), true, "clarification answers should retain voice input");
      assert.strictEqual(voiceInputEligible(voiceFields.composer), true, "main/interview/practice composer should remain voice eligible");
      const latestRecorder = () => FakeMediaRecorder.instances[FakeMediaRecorder.instances.length - 1];
      const latestStream = () => test.streams[test.streams.length - 1];
      const finish = async (input, text, prefix) => {
        const recorder = latestRecorder();
        recorder.emitData("audio-" + input.id);
        toggleVoiceInput(input);
        assert.strictEqual(recorder.stopCalls, 1, "stop should call MediaRecorder.stop once");
        assert.strictEqual(latestStream().tracks[0].stopped, true, "stop should stop the recorder stream");
        recorder.emitStop();
        await flush();
        const pending = test.transcribeRequests[test.transcribeRequests.length - 1];
        assert.ok(pending && pending.body.audio_base64, "stop should post base64 audio");
        assert.ok(pending.body.mime_type, "stop should post MIME type");
        pending.resolve(response({ text }));
        await flush();
        assert.strictEqual(input.value, prefix + text, "transcription should append to the original prefix");
      };

      state.currentSessionId = "session-a";
      const disabled = makeInput("disabled-click");
      disabled.disabled = true;
      toggleVoiceInput(disabled);
      await flush();
      assert.strictEqual(test.getUserMediaCalls, 0, "disabled target must not request microphone access");
      assert.strictEqual(state.voice.starting, false, "disabled target must not leave a pending voice state");

      const canceledStatus = test.deferStatus({ ignoreAbort: true });
      const cancelStatusTarget = makeInput("cancel-status");
      state.voice.status = { available: null, mode: "offline", message: "检查中", maxDurationSeconds: 120 };
      toggleVoiceInput(cancelStatusTarget);
      await flush();
      const statusController = FakeAbortController.instances[FakeAbortController.instances.length - 1];
      toggleVoiceInput(cancelStatusTarget);
      await flush();
      assert.strictEqual(state.voice.starting, false, "status check should cancel when the same voice control is toggled again");
      assert.strictEqual(statusController.signal.aborted, true, "canceling status wait should abort the pending request");
      const replacementStatus = test.deferStatus();
      const replacementTarget = makeInput("replacement-status");
      toggleVoiceInput(replacementTarget);
      await flush();
      replacementTarget.value = "用户手工修改";
      replacementTarget.dispatchEvent(new Event("input", { bubbles: true }));
      replacementStatus.resolve(response({ available: true, mode: "offline", max_duration_seconds: 120 }));
      await flush();
      assert.strictEqual(state.voice.status.available, true, "new status response should be accepted");
      assert.strictEqual(test.getUserMediaCalls, 0, "manual edit while replacement status loads must still prevent microphone access");
      canceledStatus.resolve(response({ available: false, mode: "offline", message: "过期的不可用状态" }));
      await flush();
      assert.strictEqual(state.voice.status.available, true, "a late stale unavailable status must not overwrite a newer ready status");
      assert.strictEqual(test.getUserMediaCalls, 0, "late status result after cancellation must not request the microphone");

      const hungStatus = test.deferStatus();
      const statusTimeoutTarget = makeInput("status-timeout");
      state.voice.status = { available: null, mode: "offline", message: "检查中", maxDurationSeconds: 120 };
      toggleVoiceInput(statusTimeoutTarget);
      await flush();
      assert.strictEqual(state.voice.starting, true, "a pending status check should remain cancelable");
      test.timerQueue.run(10000);
      await flush();
      assert.strictEqual(state.voice.starting, false, "status timeout should release the pending start");
      assert.strictEqual(state.voice.status.available, false, "status timeout should produce a retryable unavailable state");
      hungStatus.resolve(response({ available: true, mode: "offline", max_duration_seconds: 120 }));
      await flush();
      assert.strictEqual(state.voice.status.available, false, "late status response must not restore an expired status check");
      assert.strictEqual(test.getUserMediaCalls, 0, "status timeout must not request microphone access");

      const editedBeforePermission = makeInput("edited-before-permission", "原文");
      const pendingStatus = test.deferStatus();
      state.voice.status = { available: null, mode: "offline", message: "检查中", maxDurationSeconds: 120 };
      toggleVoiceInput(editedBeforePermission);
      await flush();
      editedBeforePermission.value = "手工修改";
      editedBeforePermission.dispatchEvent(new Event("input", { bubbles: true }));
      pendingStatus.resolve(response({ available: true, mode: "offline", max_duration_seconds: 120 }));
      await flush();
      assert.strictEqual(editedBeforePermission.value, "手工修改", "editing while speech status is loading must be preserved");
      assert.strictEqual(test.getUserMediaCalls, 0, "manual edit before microphone permission must cancel pending start");

      const primary = makeInput("primary", "已有");
      const statusChecksBeforeReadyStart = test.fetchCalls.filter((item) => item.url === "/api/speech/status").length;
      toggleVoiceInput(primary);
      await flush();
      assert.ok(statusChecksBeforeReadyStart >= 2, "status should be checked before first recording");
      assert.strictEqual(test.fetchCalls.filter((item) => item.url === "/api/speech/status").length, statusChecksBeforeReadyStart, "ready status should be reused");
      assert.strictEqual(test.getUserMediaCalls, 1, "one microphone stream per recording");
      assert.strictEqual(FakeMediaRecorder.instances.length, 1, "one MediaRecorder should be created");
      await finish(primary, "你好", "已有");
      assert.strictEqual([...test.timerQueue.timers.values()].filter((item) => item.delay === 120000).length, 0, "manual stop should clear the old 120 second timer");

      const activeInput = makeInput("active-then-disabled");
      const disabledWhileActive = makeInput("disabled-while-active");
      toggleVoiceInput(activeInput);
      await flush();
      const activeRecorder = latestRecorder();
      const activeStream = latestStream();
      const meterButton = document.createElement("button");
      meterButton.id = "testVoiceButton";
      meterButton.dataset.voiceInputButton = "true";
      meterButton.dataset.voiceTarget = activeInput.id;
      const meterLevel = document.createElement("div");
      meterLevel.dataset.voiceLevel = "true";
      meterLevel.dataset.voiceTarget = activeInput.id;
      meterLevel.classList.add("hidden");
      updateVoiceUi();
      assert.strictEqual(meterLevel.classList.contains("hidden"), true, "volume display must stay hidden without an audio analyser");
      assert.strictEqual(meterLevel.attributes["aria-hidden"], "true", "hidden volume display must remain hidden from assistive technology");
      disabledWhileActive.disabled = true;
      toggleVoiceInput(disabledWhileActive);
      assert.strictEqual(state.voice.active, true, "clicking an ineligible field must not cancel another valid recording");
      assert.strictEqual(activeRecorder.stopCalls, 0, "ineligible click must leave the active recorder untouched");
      activeInput.disabled = true;
      updateVoiceUi();
      assert.strictEqual(state.voice.active, false, "disabling the active target should cancel recording");
      assert.strictEqual(activeStream.tracks[0].stopped, true, "disabling the active target should release the microphone");
      assert.strictEqual(meterButton.disabled, true, "voice button should disable with its target");

      const detachedInput = makeInput("detached-target", "旧目标");
      toggleVoiceInput(detachedInput);
      await flush();
      const detachedRecorder = latestRecorder();
      const detachedStream = latestStream();
      const requestsBeforeDetach = test.transcribeRequests.length;
      detachedInput.connected = false;
      enhanceVoiceInputs(document);
      detachedRecorder.emitData("detached-audio");
      detachedRecorder.emitStop();
      await flush();
      assert.strictEqual(detachedStream.tracks[0].stopped, true, "removing a recording target should release its stream");
      assert.strictEqual(test.transcribeRequests.length, requestsBeforeDetach, "removed target must not start transcription");

      const stopFallback = makeInput("stop-fallback", "前缀");
      toggleVoiceInput(stopFallback);
      await flush();
      const fallbackRecorder = latestRecorder();
      fallbackRecorder.emitData("fallback-audio");
      toggleVoiceInput(stopFallback);
      test.timerQueue.run(1200);
      await flush();
      const requestsAfterFallback = test.transcribeRequests.length;
      assert.strictEqual(requestsAfterFallback, requestsBeforeDetach + 1, "stop fallback should finish transcription when MediaRecorder omits stop");
      fallbackRecorder.emitStop();
      await flush();
      assert.strictEqual(test.transcribeRequests.length, requestsAfterFallback, "a late stop event must not transcribe twice");
      test.transcribeRequests[test.transcribeRequests.length - 1].resolve(response({ text: "补全" }));
      await flush();
      assert.strictEqual(stopFallback.value, "前缀补全", "stop fallback should still append the recovered transcription");

      const empty = makeInput("empty", "保留");
      toggleVoiceInput(empty);
      await flush();
      const emptyRecorder = latestRecorder();
      assert.strictEqual(emptyRecorder.stopCalls, 0, "a new recording should remain active after the prior manual stop");
      assert.strictEqual([...test.timerQueue.timers.values()].filter((item) => item.delay === 120000).length, 1, "the new recording should own exactly one 120 second timer");
      emptyRecorder.emitData("audio-empty");
      toggleVoiceInput(empty);
      emptyRecorder.emitStop();
      await flush();
      test.transcribeRequests[test.transcribeRequests.length - 1].resolve(response({ text: "" }));
      await flush();
      assert.strictEqual(empty.value, "保留", "empty transcription must not alter the prefix");
      const emptyToast = test.toastRegion.children.map((item) => (item.children || []).map((child) => child.textContent || "").join("")).join(" ");
      assert.ok(emptyToast.includes("没有识别到有效语音"), "empty transcription should explain that the user can retry");

      const manual = makeInput("manual", "初始");
      toggleVoiceInput(manual);
      await flush();
      const manualRecorder = latestRecorder();
      const manualStream = latestStream();
      manual.value = "手工编辑";
      manual.dispatchEvent(new Event("input", { bubbles: true }));
      assert.strictEqual(manualStream.tracks[0].stopped, true, "manual edit should stop its stream");
      assert.strictEqual(manualRecorder.stopCalls, 1, "manual edit should stop the recorder during cancellation");
      manualRecorder.emitData("不应覆盖");
      manualRecorder.emitStop();
      await flush();
      assert.strictEqual(manual.value, "手工编辑", "manual edit must prevent stale transcription");

      const targetA = makeInput("target-a", "A");
      const targetB = makeInput("target-b", "B");
      toggleVoiceInput(targetA);
      await flush();
      const oldRecorder = latestRecorder();
      const oldStream = latestStream();
      toggleVoiceInput(targetB);
      await flush();
      assert.strictEqual(oldStream.tracks[0].stopped, true, "switching target should stop old stream");
      oldRecorder.emitData("old");
      oldRecorder.emitStop();
      await flush();
      assert.strictEqual(targetA.value, "A", "old target must ignore stale recorder events");
      const newRecorder = latestRecorder();
      newRecorder.emitData("new");
      toggleVoiceInput(targetB);
      newRecorder.emitStop();
      await flush();
      const targetRequest = test.transcribeRequests[test.transcribeRequests.length - 1];
      targetRequest.resolve(response({ text: " 新结果" }));
      await flush();
      assert.strictEqual(targetB.value, "B 新结果", "new target should receive its result");

      const switched = makeInput("session-switch", "旧");
      toggleVoiceInput(switched);
      await flush();
      const switchedRecorder = latestRecorder();
      const switchedStream = latestStream();
      stopVoiceInput({ cancel: true });
      state.currentSessionId = "session-b";
      switchedRecorder.emitData("old-session");
      switchedRecorder.emitStop();
      await flush();
      assert.strictEqual(switchedStream.tracks[0].stopped, true, "session cancel should stop its stream");
      assert.strictEqual(switched.value, "旧", "session cancel must ignore stale data");

      const latePermission = makeInput("late-permission", "保留");
      const permission = test.deferPermission();
      toggleVoiceInput(latePermission);
      await flush();
      stopVoiceInput({ cancel: true });
      const lateStream = permission.stream;
      permission.resolve();
      await flush();
      assert.ok(lateStream, "late permission should resolve a stream");
      assert.strictEqual(lateStream.tracks[0].stopped, true, "late permission stream must be stopped");
      assert.strictEqual(FakeMediaRecorder.instances.length, 9, "late permission must not create a recorder");

      const lateHttp = makeInput("late-http", "前缀");
      toggleVoiceInput(lateHttp);
      await flush();
      const lateHttpRecorder = latestRecorder();
      lateHttpRecorder.emitData("late");
      toggleVoiceInput(lateHttp);
      lateHttpRecorder.emitStop();
      await flush();
      const lateHttpRequest = test.transcribeRequests[test.transcribeRequests.length - 1];
      stopVoiceInput({ cancel: true });
      const lateController = FakeAbortController.instances[FakeAbortController.instances.length - 1];
      assert.strictEqual(lateController.signal.aborted, true, "cancel should abort the HTTP signal");
      lateHttpRequest.resolve(response({ text: "stale" }));
      await flush();
      assert.strictEqual(lateHttp.value, "前缀", "late HTTP must not overwrite cancelled input");

      const timed = makeInput("timeout", "前");
      toggleVoiceInput(timed);
      await flush();
      const timedRecorder = latestRecorder();
      const timedStream = latestStream();
      timedRecorder.emitData("timeout");
      test.timerQueue.run(120000);
      assert.strictEqual(timedRecorder.stopCalls, 1, "120 second max duration should stop");
      assert.strictEqual(timedStream.tracks[0].stopped, true, "timeout should stop the stream");
      timedRecorder.emitStop();
      await flush();
      const timedRequest = test.transcribeRequests[test.transcribeRequests.length - 1];
      timedRequest.resolve(response({ text: "后" }));
      await flush();
      assert.strictEqual(timed.value, "前后", "timeout should transcribe once");

      const conflict = makeInput("conflict", "原文");
      toggleVoiceInput(conflict);
      await flush();
      const conflictRecorder = latestRecorder();
      conflictRecorder.emitData("conflict");
      toggleVoiceInput(conflict);
      conflictRecorder.emitStop();
      await flush();
      const conflictRequest = test.transcribeRequests[test.transcribeRequests.length - 1];
      conflictRequest.resolve(response({ detail: "已有任务" }, 409));
      await flush();
      assert.strictEqual(conflict.value, "原文", "409 must not alter input");
      const toastText = test.toastRegion.children.map((item) => (item.children || []).map((child) => child.textContent || "").join("")).join(" ");
      assert.ok(toastText.includes("稍后重试"), "409 should explain retry");

      const inferenceFailureTarget = makeInput("inference-retry", "原文");
      toggleVoiceInput(inferenceFailureTarget);
      await flush();
      const inferenceFailureRecorder = latestRecorder();
      inferenceFailureRecorder.emitData("failed-inference");
      toggleVoiceInput(inferenceFailureTarget);
      inferenceFailureRecorder.emitStop();
      await flush();
      test.transcribeRequests[test.transcribeRequests.length - 1].resolve(response({ detail: "识别失败" }, 500));
      await flush();
      assert.strictEqual(inferenceFailureTarget.value, "原文", "failed inference must preserve existing text");
      toggleVoiceInput(inferenceFailureTarget);
      await flush();
      const recoveryRecorder = latestRecorder();
      recoveryRecorder.emitData("successful-retry");
      toggleVoiceInput(inferenceFailureTarget);
      recoveryRecorder.emitStop();
      await flush();
      test.transcribeRequests[test.transcribeRequests.length - 1].resolve(response({ text: "恢复成功" }));
      await flush();
      assert.strictEqual(inferenceFailureTarget.value, "原文恢复成功", "a later successful transcription should recover after inference failure");

      const transcriptionTimeout = makeInput("transcription-timeout", "保持");
      toggleVoiceInput(transcriptionTimeout);
      await flush();
      const timeoutRecorder = latestRecorder();
      timeoutRecorder.emitData("timeout-audio");
      toggleVoiceInput(transcriptionTimeout);
      timeoutRecorder.emitStop();
      await flush();
      const timeoutRequest = test.transcribeRequests[test.transcribeRequests.length - 1];
      test.timerQueue.run(300000);
      await flush();
      assert.strictEqual(FakeAbortController.instances[FakeAbortController.instances.length - 1].signal.aborted, true, "transcription timeout should abort its request");
      assert.strictEqual(transcriptionTimeout.value, "保持", "a timed out transcription must leave the input unchanged");
      const timeoutToast = test.toastRegion.children.map((item) => (item.children || []).map((child) => child.textContent || "").join("")).join(" ");
      assert.ok(timeoutToast.includes("语音识别超时"), "transcription timeout should explain how to retry");
      timeoutRequest.resolve(response({ text: "过期结果" }));
      await flush();
      assert.strictEqual(transcriptionTimeout.value, "保持", "late timeout response must not overwrite the input");

      state.voice.status = { available: null, mode: "offline", message: "检查中", maxDurationSeconds: 120 };
      const temporaryStatusFailure = test.deferStatus();
      const failedStatusTarget = makeInput("status-failure");
      toggleVoiceInput(failedStatusTarget);
      await flush();
      temporaryStatusFailure.reject(new Error("temporary status failure"));
      await flush();
      assert.strictEqual(state.voice.status.available, false, "temporary status failure should set an unavailable state");
      assert.strictEqual(test.getUserMediaCalls, 16, "failed status check must not request the microphone");
      const retryTarget = makeInput("status-retry");
      const statusCallsBeforeRetry = test.fetchCalls.filter((item) => item.url === "/api/speech/status").length;
      toggleVoiceInput(retryTarget);
      await flush();
      assert.strictEqual(test.fetchCalls.filter((item) => item.url === "/api/speech/status").length, statusCallsBeforeRetry + 1, "cached unavailable status should be checked again on a user retry");
      assert.strictEqual(state.voice.active, true, "status retry should recover when offline speech becomes available");
      stopVoiceInput({ cancel: true });

      assert.strictEqual(test.getUserMediaCalls, 17, "one microphone stream should be opened per valid recording attempt");
      assert.strictEqual(FakeMediaRecorder.instances.length, 16, "late permission must not create a recorder");
      console.log("offline voice DOM regression: field eligibility, hidden unavailable volume meter, status cancel/timeout/retry/stale response, inference recovery, MediaRecorder, prefix/empty, manual edit, target/session cancel, late permission/HTTP, recording/transcription timeout, 409 passed");
    })();
  `;
  const executable = appSource.replace("  bootstrap();", testCode);
  const result = vm.runInNewContext(executable, context, { filename: appPath });
  await result;
}

run().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
