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
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.className = "";
    this.classList = { toggle() {}, add() {}, remove() {} };
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this.children = [];
    this.listeners = new Map();
    this.connected = true;
  }

  appendChild(child) {
    if (child) this.children.push(child);
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
  }

  createElement(tagName) { return new FakeElement(this, tagName); }
  createTextNode(text) { return { textContent: String(text), nodeType: 3 }; }
  getElementById(id) { return id === "toastRegion" ? this.toastRegion : null; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  addEventListener() {}
  contains(node) { return Boolean(node && node.connected !== false); }
}

class FakeRecognition {
  constructor() {
    this.stopCalls = 0;
    this.abortCalls = 0;
    this.started = false;
    FakeRecognition.instances.push(this);
  }

  start() { this.started = true; }
  stop() { this.stopCalls += 1; }
  abort() { this.abortCalls += 1; }
  emitResult(resultIndex, results) { if (typeof this.onresult === "function") this.onresult({ resultIndex, results }); }
  emitEnd() { if (typeof this.onend === "function") this.onend(); }
  emitError(error) { if (typeof this.onerror === "function") this.onerror({ error }); }
}

FakeRecognition.instances = [];

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
      assert.ok(entry, `expected a ${delay}ms timer`);
      timers.delete(entry[0]);
      entry[1].callback();
    }
  };
}

function speechResult(transcript, isFinal) {
  const result = [];
  result[0] = { transcript };
  result.isFinal = isFinal;
  return result;
}

async function run() {
  const document = new FakeDocument();
  const timerQueue = createTimerQueue();
  const window = {
    SpeechRecognition: FakeRecognition,
    setTimeout: timerQueue.setTimeout,
    clearTimeout: timerQueue.clearTimeout,
    requestAnimationFrame() { return 1; },
    cancelAnimationFrame() {},
    addEventListener() {},
    removeEventListener() {},
    localStorage: { getItem() { return null; }, setItem() {} },
    sessionStorage: { getItem() { return null; }, setItem() {} },
    location: { hash: "", pathname: "/", search: "" },
    __voiceTest: { instances: FakeRecognition.instances, timerQueue, getUserMediaCalls: 0 }
  };
  const context = {
    window,
    document,
    navigator: {
      language: "zh-CN",
      userAgent: "voice-dom-regression",
      // No microphone API is installed in this harness. The meter path must
      // degrade to its existing explanatory toast without opening a device.
      mediaDevices: {}
    },
    assert,
    speechResult,
    Event: class Event {
      constructor(type, options = {}) { this.type = type; this.bubbles = Boolean(options.bubbles); }
    },
    console,
    fetch: async () => ({ ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({}) }),
    setTimeout: timerQueue.setTimeout,
    clearTimeout: timerQueue.clearTimeout,
    URL,
    URLSearchParams,
    FormData,
    Blob,
    File: global.File || class File {},
    TextEncoder,
    TextDecoder,
    Uint8Array,
    crypto: global.crypto
  };
  const appSource = fs.readFileSync(appPath, "utf8");
  const testCode = `
    return (async () => {
      const test = window.__voiceTest;
      const instances = test.instances;
      const makeInput = (id, value = "") => {
        const input = document.createElement("textarea");
        input.id = id;
        input.value = value;
        return input;
      };

      state.currentSessionId = "session-a";
      const input = makeInput("voice-input", "已有");
      toggleVoiceInput(input);
      assert.ok(instances[0], "recognition should be constructed");
      assert.strictEqual(instances[0].interimResults, true, "interim results should be enabled");
      assert.strictEqual(instances[0].continuous, true, "recognition should remain continuous");
      instances[0].emitResult(0, [speechResult("你好", false)]);
      assert.strictEqual(input.value, "已有你好", "interim text should render immediately");
      instances[0].emitResult(0, [speechResult("你好", true)]);
      instances[0].emitResult(0, [speechResult("你好", true)]);
      assert.strictEqual(input.value, "已有你好", "re-emitted final text must not duplicate");
      instances[0].emitResult(1, [speechResult("你好", true), speechResult("世界", true)]);
      assert.strictEqual(input.value, "已有你好世界", "later final result should append once");

      toggleVoiceInput(input);
      assert.strictEqual(instances[0].stopCalls, 1, "explicit mic stop should request graceful stop");
      assert.strictEqual(instances[0].abortCalls, 0, "explicit mic stop must not abort immediately");
      instances[0].emitResult(2, [speechResult("你好", true), speechResult("世界", true), speechResult("终稿", true)]);
      assert.strictEqual(input.value, "已有你好世界终稿", "late final result during graceful stop must survive");
      instances[0].emitEnd();

      const multi = makeInput("multi-input");
      toggleVoiceInput(multi);
      const multiRecognition = instances[1];
      multiRecognition.emitResult(0, [speechResult("一", false), speechResult("二", false)]);
      assert.strictEqual(multi.value, "一二", "multiple interim results should render in index order");
      multiRecognition.emitResult(0, [speechResult("一", false)]);
      assert.strictEqual(multi.value, "一", "removed interim result must be pruned when results shrink");
      multiRecognition.emitResult(0, [speechResult("你", false), speechResult("好", false)]);
      assert.strictEqual(multi.value, "你好", "replacement interim results should not retain stale words");
      multiRecognition.emitResult(0, [speechResult("你", true), speechResult("好", false)]);
      assert.strictEqual(multi.value, "你好", "an interim result becoming final must keep one copy");
      multiRecognition.emitResult(1, [speechResult("你", true), speechResult("好", true)]);
      assert.strictEqual(multi.value, "你好", "multi interim-to-final transition must not duplicate text");
      multiRecognition.emitEnd();

      const manual = makeInput("manual-input", "初始");
      toggleVoiceInput(manual);
      const manualRecognition = instances[2];
      manual.value = "手工编辑";
      manual.dispatchEvent(new Event("input", { bubbles: true }));
      assert.strictEqual(manualRecognition.abortCalls, 1, "manual edit should cancel recognition");
      manualRecognition.emitResult(0, [speechResult("不应覆盖", true)]);
      assert.strictEqual(manual.value, "手工编辑", "recognition must not clobber a manual edit");

      const cleared = makeInput("cleared-input", "发送中的文字");
      toggleVoiceInput(cleared);
      const clearedRecognition = instances[3];
      // sendMessage clears the composer synchronously without dispatching an
      // input event; the result callback must still detect that value change.
      cleared.value = "";
      clearedRecognition.emitResult(0, [speechResult("发送后不应回填", true)]);
      assert.strictEqual(clearedRecognition.abortCalls, 1, "a synchronous composer clear should cancel recognition");
      assert.strictEqual(cleared.value, "", "a cleared composer must stay cleared");

      const targetA = makeInput("target-a");
      const targetB = makeInput("target-b");
      toggleVoiceInput(targetA);
      const targetRecognition = instances[4];
      toggleVoiceInput(targetB);
      assert.strictEqual(targetRecognition.abortCalls, 1, "switching targets should cancel the old recognition");
      targetRecognition.emitResult(0, [speechResult("旧目标", true)]);
      assert.strictEqual(targetA.value, "", "cancelled target must ignore stale results");
      const currentRecognition = instances[5];
      currentRecognition.emitResult(0, [speechResult("新目标", true)]);
      assert.strictEqual(targetB.value, "新目标", "new target should receive its own results");
      currentRecognition.emitEnd();

      state.currentSessionId = "session-a";
      const switched = makeInput("session-switch");
      toggleVoiceInput(switched);
      const switchedRecognition = instances[6];
      stopVoiceInput({ cancel: true });
      state.currentSessionId = "session-b";
      switchedRecognition.emitResult(0, [speechResult("旧会话", true)]);
      assert.strictEqual(switched.value, "", "session cancellation must ignore stale results");
      assert.strictEqual(switchedRecognition.abortCalls, 1, "session cancellation should abort recognition");

      state.currentSessionId = "session-a";
      const timed = makeInput("timeout");
      toggleVoiceInput(timed);
      const timedRecognition = instances[7];
      toggleVoiceInput(timed);
      assert.strictEqual(timedRecognition.stopCalls, 1, "timeout scenario should begin with graceful stop");
      assert.strictEqual(timedRecognition.abortCalls, 0, "timeout should not abort before its bound");
      test.timerQueue.run(1200);
      assert.strictEqual(timedRecognition.abortCalls, 1, "bounded cleanup should abort when onend never arrives");
      timedRecognition.emitResult(0, [speechResult("超时后", true)]);
      assert.strictEqual(timed.value, "", "late result after bounded cleanup must be ignored");

      assert.strictEqual(test.getUserMediaCalls, 0, "voice regression must not access a real microphone");
      console.log("voice DOM regression: interim/final dedup, shrink pruning, graceful tail, manual edit, target/session cancellation, timeout passed");
    })();
  `;
  // The app is intentionally loaded without bootstrap so the test controls
  // only the voice functions and does not make network or microphone calls.
  const executable = appSource.replace("  bootstrap();", testCode);
  const result = vm.runInNewContext(executable, context, { filename: appPath });
  await result;
}

run().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
