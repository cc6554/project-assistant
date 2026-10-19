(() => {
  "use strict";

  const TOKEN_STORAGE_KEY = "agent-coach:api-token";
  const SESSION_STORAGE_KEY = "agent-coach:last-session";
  const THEME_STORAGE_KEY = "jr-theme";
  const DEFAULT_THEME = "dark";
  const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
  const IMAGE_MIME_TYPES = new Set(["image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp"]);
  const IMAGE_EXTENSIONS = /\.(?:jpe?g|png|gif|webp)$/i;
  const UNSUPPORTED_IMAGE_EXTENSIONS = /\.(?:avif|bmp|tiff?|heic|heif|jxl|svg)$/i;
  const EMPTY_ATTACHMENT_PROMPT = "请阅读这些求职材料，并告诉我下一步需要补充什么。";
  const API_ROOT = "/api";
  const MODEL_SETTINGS_PATH = "/settings/models";
  const UI_SETTINGS_PATH = "/settings/ui";
  const PRACTICE_WINDOW_VIEW = "practice";
  const PRACTICE_WINDOW_NAME = "agent-coach-practice";
  const PRACTICE_WINDOW_FEATURES = "popup=yes,resizable=yes,scrollbars=yes,width=1160,height=860,left=80,top=60";

  const ICONS = {
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>',
    menu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 0 0-14.6-4.4L4 8"/><path d="M4 4v4h4M4 13a8 8 0 0 0 14.6 4.4L20 16"/><path d="M20 20v-4h-4"/></svg>',
    panel: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="4" width="17" height="16" rx="2"/><path d="M15 4v16"/></svg>',
    "panel-close": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="4" width="17" height="16" rx="2"/><path d="M15 4v16M11 9l-3 3 3 3"/></svg>',
    key: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="8.5" cy="15.5" r="4.5"/><path d="m12 12 8-8m-3 3 2 2m-5-1 2 2"/></svg>',
    code: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m8 8-4 4 4 4M16 8l4 4-4 4M14 5l-4 14"/></svg>',
    folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 6.5a2 2 0 0 1 2-2h5l2 2h6a2 2 0 0 1 2 2v8.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/><path d="M3.8 9h16.4"/></svg>',
    "folder-open": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 7a2 2 0 0 1 2-2h4l2 2h6.8a2 2 0 0 1 1.9 2.6l-2 7a2 2 0 0 1-1.9 1.4H5.4a2 2 0 0 1-1.9-2.6z"/><path d="M4.4 10h14.2"/></svg>',
    spark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3 1.2 5.8L19 10l-5.8 1.2L12 17l-1.2-5.8L5 10l5.8-1.2z"/><path d="m19 16 .5 2.5L22 19l-2.5.5L19 22l-.5-2.5L16 19l2.5-.5z"/></svg>',
    briefcase: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="7" width="17" height="12" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3.5 12h17M10 12v2h4v-2"/></svg>',
    "arrow-up-right": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17 17 7M8 7h9v9"/></svg>',
    "arrow-right": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    "arrow-up": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M6 11l6-6 6 6"/></svg>',
    paperclip: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m20 11.5-7.8 7.8a5 5 0 0 1-7.1-7.1l8.3-8.3a3.5 3.5 0 0 1 5 5L10 17.3a2 2 0 1 1-2.8-2.8l7.9-7.9"/></svg>',
    shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5 19 6v5.2c0 4.3-2.8 7.5-7 9.3-4.2-1.8-7-5-7-9.3V6z"/><path d="m9 12 2 2 4-4"/></svg>',
    settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8.2a3.8 3.8 0 1 0 0 7.6 3.8 3.8 0 0 0 0-7.6Z"/><path d="m19.4 15 .1.1a1.9 1.9 0 0 1-2.7 2.7l-.1-.1a1.9 1.9 0 0 0-3.2 1.3v.2a1.9 1.9 0 0 1-3.8 0V19a1.9 1.9 0 0 0-3.2-1.3l-.1.1a1.9 1.9 0 0 1-2.7-2.7l.1-.1A1.9 1.9 0 0 0 2.5 12a1.9 1.9 0 0 1 1.9-1.9h.2a1.9 1.9 0 0 0 1.3-3.2l-.1-.1a1.9 1.9 0 0 1 2.7-2.7l.1.1a1.9 1.9 0 0 0 3.2-1.3v-.2a1.9 1.9 0 0 1 3.8 0v.2a1.9 1.9 0 0 0 3.2 1.3l.1-.1a1.9 1.9 0 0 1 2.7 2.7l-.1.1a1.9 1.9 0 0 0 1.3 3.2h.2a1.9 1.9 0 0 1 0 3.8H21a1.9 1.9 0 0 0-1.6 1.1Z"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="m6 6 12 12M18 6 6 18"/></svg>',
    file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4M9 12h6M9 16h6"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 4 4L19 6"/></svg>',
    warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="m12 4 9 16H3z"/><path d="M12 9v5M12 17.5v.1"/></svg>',
    bot: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="7" width="16" height="13" rx="3"/><path d="M12 3v4M8 13h.01M16 13h.01M8 16h8"/></svg>',
    user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/></svg>',
    mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="3" width="8" height="12" rx="4"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"/></svg>'
  };

  const state = {
    token: "",
    info: null,
    sessions: [],
    currentSessionId: null,
    currentSession: null,
    selectedTab: "job",
    selectedStep: "jd",
    sidebarCollapsed: false,
    sidebarOpen: false,
    inspectorCollapsed: false,
    filePath: ".",
    fileEntries: [],
    fileLoading: false,
    fileError: "",
    jobs: new Map(),
    followUpJobs: new Map(),
    commandRuntime: {
      sessionId: "",
      scopeKey: "",
      items: [],
      error: "",
      refreshTimer: null,
      refreshInFlight: false,
      stopping: new Set(),
      completionJobs: new Map(),
      settled: new Set()
    },
    pollTimer: null,
    pollTarget: null,
    pollInFlight: null,
    optimisticMessage: null,
    uploadedAttachment: null,
    uploadedAttachments: [],
    theme: DEFAULT_THEME,
    themeSyncSerial: 0,
    voice: {
      active: false,
      recognition: null,
      meter: null,
      generation: 0,
      latestText: "",
      target: null
    },
    jobEditor: {
      open: false,
      kind: "",
      value: null,
      sessionId: "",
      busy: false,
      serial: 0,
      previousFocus: null
    },
    jdImport: { open: false, opening: false, busy: false, sessionId: "", drafts: new Map(), previousFocus: null },
    clarification: {
      open: false,
      busy: false,
      sessionId: "",
      question: "",
      questionKey: "",
      answer: "",
      completed: false,
      status: "",
      statusKind: "",
      feedback: "",
      previousFocus: null,
      serial: 0,
      drafts: new Map(),
      jobs: new Map()
    },
    workflowChat: {
      open: false,
      // The workflow modal reuses the chat DOM so approvals/recovery keep the
      // same session semantics.  ``scope`` controls which durable transcript
      // is rendered while the modal is open; the home chat always uses main.
      scope: "main",
      practiceProjectKey: "",
      // Composer drafts are keyed by session, scope, and (for practice) the
      // exact project.  A shared chat DOM must not make an unsent A turn appear
      // in B's practice window after a project or session switch.
      drafts: new Map(),
      draftAttachments: new Map(),
      previousFocus: null
    },
    practice: {
      optimisticMessage: null,
      sessionId: "",
      projectKey: "",
      projectTitle: "",
      threadId: "",
      messages: [],
      events: [],
      loaded: false,
      loading: false,
      error: "",
      startPending: false,
      viewSerial: 0
    },
    practiceWindow: {
      standalone: false,
      sessionId: "",
      projectKey: "",
      startRequested: false,
      startConsumed: false,
      child: null,
      children: new Map(),
      launchPending: false,
      stale: false,
      error: "",
      refreshTimer: null,
      refreshInFlight: false
    },
    pendingWorkflowAction: null,
    newSession: {
      open: false,
      busy: false,
      previousFocus: null
    },
    localImportTarget: "composer",
    localImportSessionId: "new",
    github: {
      hasToken: false,
      busy: false
    },
    renderSerial: 0,
    lastResultSeen: new Map(),
    dismissTokenNotice: false,
    loadingSession: false,
    mainReturnRefreshInFlight: false,
    sessionRequestSerial: 0,
    modelSettings: {
      open: false,
      busyAction: "",
      hasApiKey: false,
      currentProfileId: "",
      currentProfileName: "",
      profileOptions: [],
      loadedIdentity: null,
      newProfile: false,
      previousFocus: null,
      requestSerial: 0
    }
  };

  const $ = (id) => document.getElementById(id);
  const qs = (selector, root = document) => root.querySelector(selector);
  const qsa = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function readTheme() {
    try {
      return window.localStorage.getItem(THEME_STORAGE_KEY) === "light" ? "light" : DEFAULT_THEME;
    } catch (_error) {
      return DEFAULT_THEME;
    }
  }

  function applyTheme(theme, { persist = true } = {}) {
    const next = theme === "light" ? "light" : DEFAULT_THEME;
    state.theme = next;
    document.documentElement.dataset.theme = next;
    qsa("[data-theme-option]").forEach((button) => {
      const active = button.dataset.themeOption === next;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    if (persist) {
      try { window.localStorage.setItem(THEME_STORAGE_KEY, next); } catch (_error) { /* optional */ }
    }
  }

  async function persistTheme(theme, serial) {
    try {
      await request(UI_SETTINGS_PATH, { method: "POST", body: { theme } });
    } catch (error) {
      if (serial === state.themeSyncSerial && error.status !== 404 && error.status !== 405) {
        toast("主题已保留在本机，但服务端同步失败。", "", 3600);
      }
    }
  }

  async function loadUiSettings() {
    const serial = ++state.themeSyncSerial;
    try {
      const payload = await request(UI_SETTINGS_PATH);
      if (serial !== state.themeSyncSerial) return;
      const theme = payload && payload.theme;
      if (theme === "light" || theme === "dark") applyTheme(theme);
    } catch (_error) {
      // localStorage is the first-paint fallback; older services may not have
      // the optional server-side UI settings endpoint yet.
    }
  }

  function toggleTheme(theme) {
    applyTheme(theme);
    const serial = ++state.themeSyncSerial;
    void persistTheme(theme, serial);
    toast(theme === "light" ? "已切换为浅色主题" : "已切换为深色主题", "success", 2200);
  }

  class ApiError extends Error {
    constructor(message, status = 0, payload = null) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.payload = payload;
    }
  }

  function localizeApiMessage(value, status = 0) {
    const message = String(value || "").trim();
    const labels = {
      "not found": "未找到请求资源。",
      "session not found": "未找到会话。",
      "request failed": "请求失败，请稍后重试。",
      "method not allowed": "当前操作不受支持。",
      unauthorized: "当前页面未通过授权。",
      forbidden: "当前操作没有权限。",
      "invalid request": "请求参数无效。"
    };
    return labels[message.toLowerCase()] || (message || (status ? `请求失败（${status}）` : "请求失败"));
  }

  function hydrateIcons(root = document) {
    qsa("[data-icon]", root).forEach((node) => {
      const name = node.getAttribute("data-icon");
      if (ICONS[name] && !node.firstElementChild) node.innerHTML = ICONS[name];
    });
  }

  function icon(name, className = "") {
    const span = document.createElement("span");
    span.setAttribute("aria-hidden", "true");
    if (className) span.className = className;
    span.innerHTML = ICONS[name] || "";
    return span;
  }

  function attachmentMediaType(attachment) {
    return String(attachment && (attachment.mediaType || attachment.media_type) || "").trim().toLowerCase();
  }

  function isSupportedImageAttachment(attachment) {
    const path = String(attachment && (attachment.path || attachment.name) || "");
    const mediaType = attachmentMediaType(attachment);
    if (IMAGE_MIME_TYPES.has(mediaType)) return true;
    if (mediaType.startsWith("image/")) return false;
    return IMAGE_EXTENSIONS.test(path);
  }

  function isUnsupportedImageAttachment(attachment) {
    const path = String(attachment && (attachment.path || attachment.name) || "");
    const mediaType = attachmentMediaType(attachment);
    return (mediaType.startsWith("image/") && !IMAGE_MIME_TYPES.has(mediaType)) || UNSUPPORTED_IMAGE_EXTENSIONS.test(path);
  }

  function tokenFromHash() {
    try {
      const fragment = window.location.hash.replace(/^#/, "");
      return fragment ? new URLSearchParams(fragment).get("token") || "" : "";
    } catch (_error) {
      return "";
    }
  }

  function captureToken() {
    let token = "";
    try {
      token = tokenFromHash();
      const remembered = window.sessionStorage.getItem(TOKEN_STORAGE_KEY) || "";
      if (token) {
        window.sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
        window.history.replaceState(null, document.title, window.location.pathname + window.location.search);
      } else {
        token = remembered;
      }
    } catch (_error) {
      // Storage can be disabled by a browser policy. The page remains usable
      // when the server does not require a token.
    }
    return token;
  }

  function handleTokenHashChange() {
    const nextToken = tokenFromHash();
    if (!nextToken || nextToken === state.token) {
      if (nextToken === state.token && nextToken) window.history.replaceState(null, document.title, window.location.pathname + window.location.search);
      return;
    }
    // A launcher restart can reuse this tab and only change the URL fragment.
    // Persist the new token before reloading so no request is sent with the
    // previous server's credential.
    state.token = nextToken;
    let persisted = false;
    try {
      window.sessionStorage.setItem(TOKEN_STORAGE_KEY, nextToken);
      persisted = window.sessionStorage.getItem(TOKEN_STORAGE_KEY) === nextToken;
    } catch (_error) {
      // captureToken will read the fragment again after reload when storage is
      // unavailable, so the token never needs to be copied into a log.
    }
    if (persisted) window.history.replaceState(null, document.title, window.location.pathname + window.location.search);
    clearPolling();
    window.location.reload();
  }

  function readPracticeWindowParams() {
    let params = null;
    try { params = new URLSearchParams(window.location.search || ""); } catch (_error) { params = null; }
    const view = String(params?.get("view") || "").trim().toLowerCase();
    if (view !== PRACTICE_WINDOW_VIEW) return { standalone: false, sessionId: "", projectKey: "", start: false };
    return {
      standalone: true,
      sessionId: String(params?.get("session_id") || "").trim(),
      projectKey: String(params?.get("project_key") || "").trim(),
      start: ["1", "true", "yes"].includes(String(params?.get("start") || "").trim().toLowerCase())
    };
  }

  function applyPracticeWindowChrome() {
    if (!state.practiceWindow.standalone) return;
    document.documentElement.dataset.windowView = PRACTICE_WINDOW_VIEW;
    document.body.classList.add("practice-window");
    setText($("modeBreadcrumb"), "Agent 实操");
    setText($("currentTitle"), state.practiceWindow.projectKey ? `实操 · ${state.practiceWindow.projectKey}` : "Agent 实操");
  }

  function practiceWindowUrl(sessionId, projectKey, start = false) {
    const id = String(sessionId || "").trim();
    const key = String(projectKey || "").trim();
    if (!id || !key) return "";
    try {
      const target = new URL(window.location.href);
      // Preserve the server path but scrub any legacy credential query keys
      // before constructing a child URL. New credentials always stay in the
      // fragment below.
      for (const keyName of Array.from(target.searchParams.keys())) {
        if (/^(?:token|api[_-]?key|access[_-]?token|authorization|secret|password)$/i.test(keyName)) target.searchParams.delete(keyName);
      }
      target.searchParams.set("view", PRACTICE_WINDOW_VIEW);
      target.searchParams.set("session_id", id);
      target.searchParams.set("project_key", key);
      if (start) target.searchParams.set("start", "1");
      else target.searchParams.delete("start");
      // API authorization remains in the fragment. Never put it in a query
      // string, where browser history, referrers, and native window logs can
      // expose it.
      target.hash = state.token ? `#token=${encodeURIComponent(state.token)}` : "";
      return target.href;
    } catch (_error) {
      return "";
    }
  }

  function focusPracticeWindow(child) {
    try { if (child && typeof child.focus === "function") child.focus(); } catch (_error) { /* optional */ }
  }

  function practiceBrowserWindowKey(sessionId, projectKey) {
    return String(sessionId || "").trim() + "\u0000" + String(projectKey || "").trim();
  }

  function practiceBrowserWindowName(sessionId, projectKey) {
    // A bounded deterministic name lets the browser reuse one popup per
    // session/project without navigating an existing popup and losing its
    // independent draft.
    const value = practiceBrowserWindowKey(sessionId, projectKey);
    let hash = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return PRACTICE_WINDOW_NAME + "-" + (hash >>> 0).toString(36);
  }

  function openPracticeWindow(sessionId, projectKey, start = false) {
    if (state.practiceWindow.standalone) return false;
    const id = String(sessionId || "").trim();
    const key = String(projectKey || "").trim();
    const url = practiceWindowUrl(id, key, start);
    if (!url) return false;
    const browserKey = practiceBrowserWindowKey(id, key);
    const existing = state.practiceWindow.children.get(browserKey);
    if (existing && !existing.closed) {
      state.practiceWindow.child = existing;
      focusPracticeWindow(existing);
      return true;
    }
    if (existing && existing.closed) state.practiceWindow.children.delete(browserKey);

    // pywebview exposes a small native bridge from packaged_launcher.py. It
    // creates a real sibling window with native minimize/maximize controls,
    // while keeping this page's WebView and backend session untouched.
    const nativeApi = window.pywebview && window.pywebview.api;
    const nativeOpen = nativeApi && nativeApi.open_practice_window;
    if (typeof nativeOpen === "function") {
      try {
        const result = nativeOpen.call(nativeApi, url);
        if (result && typeof result.then === "function") {
          state.practiceWindow.launchPending = true;
          result.then((payload) => {
            state.practiceWindow.launchPending = false;
            if (payload && payload.opened === false) openPracticeBrowserWindow(url, id, key);
          }).catch(() => {
            state.practiceWindow.launchPending = false;
            openPracticeBrowserWindow(url, id, key);
          });
          return true;
        }
        state.practiceWindow.launchPending = false;
        if (result !== false) return true;
      } catch (_error) {
        // A browser-backed dev run may expose a stale pywebview object. Use the
        // ordinary same-origin popup path below instead of trapping the user.
      }
    }
    return openPracticeBrowserWindow(url, id, key);
  }

  function openPracticeBrowserWindow(url, sessionId, projectKey) {
    if (!url || typeof window.open !== "function") return false;
    try {
      const key = practiceBrowserWindowKey(sessionId, projectKey);
      const existing = state.practiceWindow.children.get(key);
      if (existing && !existing.closed) {
        state.practiceWindow.child = existing;
        focusPracticeWindow(existing);
        return true;
      }
      const child = window.open(url, practiceBrowserWindowName(sessionId, projectKey), PRACTICE_WINDOW_FEATURES);
      if (!child) return false;
      state.practiceWindow.child = child;
      state.practiceWindow.children.set(key, child);
      focusPracticeWindow(child);
      return true;
    } catch (_error) {
      return false;
    }
  }

  function requestPracticeWindowClose() {
    const nativeApi = window.pywebview && window.pywebview.api;
    const nativeClose = nativeApi && nativeApi.close_practice_window;
    if (typeof nativeClose === "function") {
      try { nativeClose.call(nativeApi, state.practiceWindow.sessionId, state.practiceWindow.projectKey); } catch (_error) { /* browser fallback below */ }
    }
    try {
      if (window.opener || String(window.name || "").startsWith(PRACTICE_WINDOW_NAME + "-")) window.close();
    } catch (_error) { /* optional */ }
  }

  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
    const init = { ...options, headers, credentials: "same-origin" };
    if (options.body !== undefined && options.body !== null && typeof options.body !== "string" && !(options.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(options.body);
    }
    let response;
    try {
      response = await fetch(`${API_ROOT}${path}`, init);
    } catch (_error) {
      throw new ApiError("无法连接到 校招雷达服务");
    }
    let payload = null;
    const contentType = response.headers.get("content-type") || "";
    try {
      payload = contentType.includes("application/json") ? await response.json() : await response.text();
    } catch (_error) {
      payload = null;
    }
    if (!response.ok) {
      const detail = payload && typeof payload === "object" ? (payload.detail || payload.message || payload.error) : "";
      throw new ApiError(localizeApiMessage(detail, response.status), response.status, payload);
    }
    return payload;
  }

  function unwrapSession(payload) {
    if (!payload) return null;
    if (payload.session && typeof payload.session === "object") return payload.session;
    if (payload.snapshot && typeof payload.snapshot === "object") return payload.snapshot;
    if (payload.state && typeof payload.state === "object" && payload.state.id) return payload.state;
    return payload.id ? payload : null;
  }

  function sessionRows(payload) {
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray(payload.sessions)) return payload.sessions;
    return [];
  }

  function sessionBusy(session) {
    if (!session) return false;
    // ``busy`` is the server's live worker signal. An execution claim can
    // survive a process restart; that state must stay actionable as an
    // unknown result instead of disabling the whole session forever.
    return Boolean(session.busy || state.jobs.has(String(session.id)));
  }

  function sessionHasQueue(session) {
    if (!session) return false;
    return Number(session.queued_calls || 0) > 0;
  }

  function sessionTurnBlocked(session) {
    return Boolean(sessionBusy(session) || sessionHasQueue(session));
  }

  function currentJob() {
    return state.currentSessionId ? state.jobs.get(String(state.currentSessionId)) || null : null;
  }

  function persistCurrentSession() {
    // A child window is pinned by its URL. Do not overwrite the parent's
    // shared last-session preference while it opens that pinned session.
    if (!state.currentSessionId || state.practiceWindow.standalone) return;
    try { window.localStorage.setItem(SESSION_STORAGE_KEY, String(state.currentSessionId)); } catch (_error) { /* optional */ }
  }

  function readPersistedSession() {
    try { return window.localStorage.getItem(SESSION_STORAGE_KEY) || ""; } catch (_error) { return ""; }
  }

  function setText(node, value) {
    if (node) node.textContent = value == null ? "" : String(value);
  }

  function sidebarIsMobile() {
    return Boolean(window.matchMedia && window.matchMedia("(max-width: 680px)").matches);
  }

  let sidebarLastMobile = null;

  function focusSidebarToggle() {
    const toggle = $("sidebarToggle");
    if (toggle && typeof toggle.focus === "function") toggle.focus({ preventScroll: true });
  }

  function restoreSidebarAwareFocus(previousFocus) {
    if (!previousFocus || typeof previousFocus.focus !== "function" || !document.contains(previousFocus)) return;
    const sidebar = $("sidebar");
    const insideSidebar = sidebar && sidebar.contains(previousFocus);
    const sidebarHidden = insideSidebar && (sidebarIsMobile() ? !state.sidebarOpen : state.sidebarCollapsed);
    (sidebarHidden ? $("sidebarToggle") : previousFocus)?.focus({ preventScroll: true });
  }

  function syncSidebarState() {
    const sidebar = $("sidebar");
    const toggle = $("sidebarToggle");
    if (!sidebar || !toggle) return;
    const mobile = sidebarIsMobile();
    const drawerOpen = mobile && Boolean(state.sidebarOpen);
    if (mobile) {
      sidebar.classList.remove("collapsed");
      sidebar.classList.toggle("open", Boolean(state.sidebarOpen));
      toggle.setAttribute("aria-expanded", state.sidebarOpen ? "true" : "false");
      toggle.setAttribute("aria-label", state.sidebarOpen ? "收起会话导航" : "打开会话导航");
      toggle.title = state.sidebarOpen ? "收起会话导航" : "打开会话导航";
    } else {
      sidebar.classList.remove("open");
      sidebar.classList.toggle("collapsed", Boolean(state.sidebarCollapsed));
      toggle.setAttribute("aria-expanded", state.sidebarCollapsed ? "false" : "true");
      toggle.setAttribute("aria-label", state.sidebarCollapsed ? "展开会话导航" : "收起会话导航");
      toggle.title = state.sidebarCollapsed ? "展开会话导航" : "收起会话导航";
    }
    const sidebarHidden = mobile ? !drawerOpen : Boolean(state.sidebarCollapsed);
    sidebar.toggleAttribute("inert", sidebarHidden);
    sidebar.setAttribute("aria-hidden", sidebarHidden ? "true" : "false");
    const scrim = $("sidebarScrim");
    if (scrim) {
      scrim.classList.toggle("hidden", !drawerOpen);
      scrim.setAttribute("aria-hidden", drawerOpen ? "false" : "true");
      scrim.tabIndex = drawerOpen ? 0 : -1;
    }
  }

  function toggleSidebar() {
    if (sidebarIsMobile()) {
      state.sidebarOpen = !state.sidebarOpen;
      syncSidebarState();
      if (state.sidebarOpen) $("sidebarClose")?.focus({ preventScroll: true });
      return;
    } else {
      state.sidebarCollapsed = !state.sidebarCollapsed;
      state.sidebarOpen = !state.sidebarCollapsed;
    }
    syncSidebarState();
  }

  function closeSidebarDrawer() {
    if (!sidebarIsMobile()) return;
    const sidebar = $("sidebar");
    const active = document.activeElement;
    if ((sidebar && active && sidebar.contains(active)) || active === $("sidebarScrim")) focusSidebarToggle();
    state.sidebarOpen = false;
    syncSidebarState();
  }

  function handleSidebarResize() {
    const mobile = sidebarIsMobile();
    if (sidebarLastMobile === false && mobile) state.sidebarOpen = false;
    const sidebar = $("sidebar");
    const active = document.activeElement;
    const willHideSidebar = sidebar && active && sidebar.contains(active) && (mobile ? !state.sidebarOpen : state.sidebarCollapsed);
    if (willHideSidebar) focusSidebarToggle();
    sidebarLastMobile = mobile;
    syncSidebarState();
  }

  function safeString(value) {
    if (value == null) return "";
    if (typeof value === "string") return value;
    try { return JSON.stringify(value, null, 2); } catch (_error) { return String(value); }
  }

  function redactDisplay(value) {
    return String(value || "")
      .replace(/(Bearer\s+)[A-Za-z0-9._-]+/gi, "$1[已隐藏]")
      .replace(/\bsk[-_][A-Za-z0-9_-]{8,}\b/gi, "[已隐藏凭据]");
  }

  function contentText(content) {
    if (Array.isArray(content)) {
      return content.map((block) => {
        if (block && typeof block === "object" && block.type === "text") return block.text || "";
        if (block && typeof block === "object" && block.type === "image") return "[图片内容已保留给模型，界面不展开]";
        return safeString(block);
      }).join("\n");
    }
    return String(content == null ? "" : content);
  }

  function appendInline(parent, value) {
    const text = String(value || "");
    const tokenRe = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|~~[^~]+~~|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\))/g;
    let cursor = 0;
    let match;
    while ((match = tokenRe.exec(text)) !== null) {
      if (match.index > cursor) parent.appendChild(document.createTextNode(text.slice(cursor, match.index)));
      const token = match[0];
      if (token.startsWith("**") || token.startsWith("__")) {
        const strong = document.createElement("strong");
        strong.textContent = token.slice(2, -2);
        parent.appendChild(strong);
      } else if (token.startsWith("`")) {
        const code = document.createElement("code");
        code.className = "inline-code";
        code.textContent = token.slice(1, -1);
        parent.appendChild(code);
      } else if (token.startsWith("~~")) {
        const del = document.createElement("del");
        del.textContent = token.slice(2, -2);
        parent.appendChild(del);
      } else {
        const anchor = document.createElement("a");
        anchor.textContent = match[2];
        anchor.href = match[3];
        anchor.target = "_blank";
        anchor.rel = "noreferrer noopener";
        parent.appendChild(anchor);
      }
      cursor = match.index + token.length;
    }
    if (cursor < text.length) parent.appendChild(document.createTextNode(text.slice(cursor)));
  }

  function renderSafeMarkdown(value) {
    const fragment = document.createDocumentFragment();
    const lines = String(value || "").replace(/\r\n?/g, "\n").split("\n");
    let inCode = false;
    let codeLines = [];
    let codeLanguage = "";
    let list = null;
    let listType = "";
    const closeList = () => { list = null; listType = ""; };
    const addParagraph = (line) => {
      const paragraph = document.createElement("p");
      appendInline(paragraph, line);
      fragment.appendChild(paragraph);
    };
    const addListItem = (line, ordered) => {
      if (!list || listType !== (ordered ? "ol" : "ul")) {
        closeList();
        list = document.createElement(ordered ? "ol" : "ul");
        listType = ordered ? "ol" : "ul";
        fragment.appendChild(list);
      }
      const item = document.createElement("li");
      appendInline(item, line);
      list.appendChild(item);
    };
    lines.forEach((line) => {
      if (line.trim().startsWith("```")) {
        if (!inCode) {
          inCode = true;
          codeLines = [];
          codeLanguage = line.trim().slice(3).trim();
        } else {
          const pre = document.createElement("pre");
          pre.dataset.language = codeLanguage;
          pre.textContent = codeLines.join("\n");
          fragment.appendChild(pre);
          inCode = false;
          codeLanguage = "";
        }
        closeList();
        return;
      }
      if (inCode) { codeLines.push(line); return; }
      const trimmed = line.trim();
      if (!trimmed) { closeList(); return; }
      const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed);
      if (heading) {
        closeList();
        const headingNode = document.createElement(heading[1].length <= 2 ? "h3" : "h4");
        appendInline(headingNode, heading[2]);
        fragment.appendChild(headingNode);
        return;
      }
      const unordered = /^[-*+]\s+(.+)$/.exec(trimmed);
      if (unordered) { addListItem(unordered[1], false); return; }
      const ordered = /^\d+[.)]\s+(.+)$/.exec(trimmed);
      if (ordered) { addListItem(ordered[1], true); return; }
      if (trimmed.startsWith(">")) {
        closeList();
        const quote = document.createElement("blockquote");
        appendInline(quote, trimmed.replace(/^>\s?/, ""));
        fragment.appendChild(quote);
        return;
      }
      closeList();
      addParagraph(line);
    });
    if (inCode) {
      const pre = document.createElement("pre");
      pre.textContent = codeLines.join("\n");
      fragment.appendChild(pre);
    }
    return fragment;
  }

  function formatTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function formatBytes(value) {
    const size = Number(value);
    if (!Number.isFinite(size) || size < 0) return "";
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }

  function maybeShowLastResult(id, session) {
    const result = session && session.last_result;
    if (!result || typeof result !== "object") return;
    const signature = safeString(result);
    if (state.lastResultSeen.get(String(id)) === signature) return;
    state.lastResultSeen.set(String(id), signature);
    const message = String(result.message || result.error || "").trim();
    if (!message) return;
    // Successful model replies are rendered in the transcript. Showing the
    // whole reply again in a green toast makes long answers cover the page.
    // Keep the toast for failures because the failed state can be discovered
    // from a later session refresh without a visible transcript entry.
    if (["failed", "error"].includes(String(result.status || "").toLowerCase())) toast(message, "error");
  }

  function sessionTitle(session) {
    if (session && session.title) return String(session.title);
    const workflow = session && session.skill_state && session.skill_state["job-radar"];
    const jd = (session && session.jd) || (workflow && workflow.jd) || (session && session.job_state && session.job_state.jd) || {};
    const jobTitle = String(session && session.job_title || jd && jd.job_title || "").trim();
    const company = String(session && session.company || jd && jd.company || "").trim();
    if (jobTitle && company) return `${jobTitle} · ${company}`;
    if (jobTitle || company) return jobTitle || company;
    const messages = (session && session.messages) || [];
    const firstUser = messages.find((message) => message && message.role === "user");
    const text = firstUser ? contentText(firstUser.content).replace(/\s+/g, " ").trim() : "新会话";
    return text ? text.slice(0, 42) : "新会话";
  }

  function visibleMessages(session) {
    return ((session && session.messages) || []).filter((message) => message && message.role !== "system");
  }

  // Practice turns are persisted by the service with an explicit metadata
  // envelope.  Main and legacy messages have no practice metadata and must
  // never be inferred into the project window after a reload or switch.
  function messageScope(message) {
    if (!message || typeof message !== "object") return "main";
    const metadata = message.metadata && typeof message.metadata === "object" ? message.metadata : {};
    return metadata.scope === "practice" ? "practice" : "main";
  }

  function messageProjectKey(message) {
    if (!message || typeof message !== "object") return "";
    const metadata = message.metadata && typeof message.metadata === "object" ? message.metadata : {};
    return String(metadata.project_key || "").trim();
  }

  function messagePracticeThreadId(message) {
    if (!message || typeof message !== "object") return "";
    const metadata = message.metadata && typeof message.metadata === "object" ? message.metadata : {};
    return String(metadata.practice_thread_id || "").trim();
  }

  // Events are also projected with their canonical scope fields at the top
  // level. Accept the nested envelope emitted by the current service and the
  // exact projection used by session snapshots, while keeping message
  // attribution metadata-only and fail-closed.
  function eventScope(event) {
    if (!event || typeof event !== "object") return "main";
    const metadata = event.metadata && typeof event.metadata === "object" ? event.metadata : {};
    return metadata.scope === "practice" || event.scope === "practice" ? "practice" : "main";
  }

  function eventProjectKey(event) {
    if (!event || typeof event !== "object") return "";
    const metadata = event.metadata && typeof event.metadata === "object" ? event.metadata : {};
    return String(metadata.project_key || event.project_key || "").trim();
  }

  function eventPracticeThreadId(event) {
    if (!event || typeof event !== "object") return "";
    const metadata = event.metadata && typeof event.metadata === "object" ? event.metadata : {};
    return String(metadata.practice_thread_id || event.practice_thread_id || "").trim();
  }

  function selectedProjectKey(data = workflowData()) {
    return projectIdentity(data && data.selected_project);
  }

  function practiceWindowSelectionMatches() {
    if (!state.practiceWindow.standalone) return true;
    const expected = String(state.practiceWindow.projectKey || "").trim();
    const actual = selectedProjectKey();
    return Boolean(expected && actual && expected === actual);
  }

  function markPracticeWindowStale() {
    if (!state.practiceWindow.standalone || !state.practiceWindow.projectKey) return false;
    const stale = !practiceWindowSelectionMatches();
    if (state.practiceWindow.stale !== stale) {
      state.practiceWindow.stale = stale;
      if (!stale) state.practiceWindow.error = "";
      if (state.workflowChat.open && state.workflowChat.scope === "practice") renderAll();
    }
    return stale;
  }

  function practiceMessages(session, projectKey = "", threadId = state.practice.threadId) {
    const key = String(projectKey || "").trim();
    const thread = String(threadId || "").trim();
    // A practice window is intentionally fail-closed.  There is no safe way
    // to attribute an unkeyed legacy turn to the current project or thread.
    if (!key || !thread) return [];
    const currentId = String(session && session.id || "");
    const source = state.practice.loaded && state.practice.sessionId === currentId && state.practice.threadId === thread
      ? state.practice.messages
      : visibleMessages(session);
    return source.filter((message) => {
      if (!message || typeof message !== "object") return false;
      return messageScope(message) === "practice"
        && messageProjectKey(message) === key
        && messagePracticeThreadId(message) === thread;
    });
  }

  function chatMessages(session) {
    if (state.workflowChat.open && state.workflowChat.scope === "practice") {
      return practiceMessages(session, state.workflowChat.practiceProjectKey || selectedProjectKey(), state.practice.threadId);
    }
    return visibleMessages(session).filter((message) => messageScope(message) !== "practice");
  }

  function practiceHasMessages(session = state.currentSession, projectKey = state.workflowChat.practiceProjectKey || selectedProjectKey(), threadId = state.practice.threadId) {
    return practiceMessages(session, projectKey, threadId).length > 0;
  }

  function practiceThreadEntries(session = state.currentSession) {
    const raw = session && session.practice_threads;
    return Array.isArray(raw) ? raw.filter((item) => item && typeof item === "object") : [];
  }

  function practiceThreadFor(session, projectKey) {
    const key = String(projectKey || "").trim();
    if (!key) return null;
    if (state.practice.sessionId === String(session && session.id || "") && state.practice.projectKey === key && state.practice.threadId) {
      return { id: state.practice.threadId, project_key: key };
    }
    const filter = session && session.practice_filter && typeof session.practice_filter === "object" ? session.practice_filter : {};
    const filteredKey = String(filter.project_key || "").trim();
    const filteredId = String(filter.practice_thread_id || "").trim();
    if (filteredKey === key && filteredId) return { id: filteredId, project_key: key, project: filter.project || null };
    return practiceThreadEntries(session).find((item) => String(item.project_key || "").trim() === key && String(item.id || "").trim()) || null;
  }

  function practiceThreadMessages(payload) {
    if (payload && Array.isArray(payload.messages)) return payload.messages;
    return [];
  }

  function practiceThreadEvents(payload) {
    return payload && Array.isArray(payload.events) ? payload.events : [];
  }

  function scopedPracticeEvent(event, projectKey, threadId) {
    return event && typeof event === "object"
      && eventScope(event) === "practice"
      && eventProjectKey(event) === String(projectKey || "").trim()
      && eventPracticeThreadId(event) === String(threadId || "").trim();
  }

  function practiceScopeChangedError() {
    const error = new Error("实操窗口已切换");
    error.practiceScopeChanged = true;
    return error;
  }

  function practiceContextMatches(sessionId, projectKey, threadId = "", viewSerial = null) {
    const id = String(sessionId || "");
    const key = String(projectKey || "").trim();
    const thread = String(threadId || "").trim();
    if (!id || String(state.currentSessionId || "") !== id || String(state.practice.sessionId || "") !== id || state.practice.projectKey !== key) return false;
    if (thread && state.practice.threadId !== thread) return false;
    if (viewSerial !== null && (state.practice.viewSerial !== viewSerial || !state.workflowChat.open || state.workflowChat.scope !== "practice" || state.workflowChat.practiceProjectKey !== key)) return false;
    return true;
  }

  async function ensurePracticeThread(sessionId, projectKey, viewSerial = null) {
    const id = String(sessionId || "");
    const key = String(projectKey || "").trim();
    if (!id || !key) throw new ApiError("当前会话没有选定练习项目。");
    if (viewSerial !== null && !practiceContextMatches(id, key, "", viewSerial)) throw practiceScopeChangedError();
    const existing = practiceThreadFor(state.currentSession, key);
    if (existing) {
      if (viewSerial !== null && !practiceContextMatches(id, key, "", viewSerial)) throw practiceScopeChangedError();
      state.practice.threadId = String(existing.id || "").trim();
      state.practice.projectKey = key;
      state.practice.loaded = false;
      state.practice.messages = [];
      state.practice.events = [];
      return state.practice.threadId;
    }
    const payload = await request(`/sessions/${encodeURIComponent(id)}/practice`, { method: "POST", body: { project_key: key } });
    const threadId = String(payload && payload.practice_thread_id || "").trim();
    const returnedKey = String(payload && payload.project_key || "").trim();
    if (!threadId || returnedKey !== key) throw new ApiError("服务没有返回当前项目的实操窗口。");
    if (viewSerial !== null && !practiceContextMatches(id, key, "", viewSerial)) throw practiceScopeChangedError();
    state.practice.threadId = threadId;
    state.practice.projectKey = returnedKey;
    state.practice.loaded = false;
    state.practice.messages = [];
    state.practice.events = [];
    return threadId;
  }

  async function refreshPracticeThread(sessionId = state.currentSessionId, threadId = state.practice.threadId, viewSerial = null) {
    const id = String(sessionId || "");
    const thread = String(threadId || "").trim();
    const key = String(state.practice.projectKey || state.workflowChat.practiceProjectKey || selectedProjectKey()).trim();
    if (!id || !thread || !key) return [];
    if (viewSerial !== null && !practiceContextMatches(id, key, thread, viewSerial)) throw practiceScopeChangedError();
    state.practice.loading = true;
    state.practice.error = "";
    try {
      const payload = await request(`/sessions/${encodeURIComponent(id)}/practice/${encodeURIComponent(thread)}`);
      if (!practiceContextMatches(id, key, thread, viewSerial)) return [];
      state.practice.messages = practiceThreadMessages(payload).filter((message) => messageScope(message) === "practice" && messageProjectKey(message) === key && messagePracticeThreadId(message) === thread);
      state.practice.events = practiceThreadEvents(payload).filter((event) => scopedPracticeEvent(event, key, thread));
      state.practice.loaded = true;
      return state.practice.messages;
    } catch (error) {
      if (practiceContextMatches(id, key, thread, viewSerial)) {
        state.practice.loaded = true;
        state.practice.error = error.message || "读取实操记录失败";
        state.practice.messages = [];
        state.practice.events = [];
      }
      throw error;
    } finally {
      if (practiceContextMatches(id, key, thread, viewSerial)) {
        state.practice.loading = false;
        renderAll();
      }
    }
  }

  function visibleUserCount(messages) {
    return messages.filter((message) => message && message.role === "user").length;
  }

  const ACTIVITY_LABELS = {
    running: "处理中",
    started: "已开始",
    queued: "已排队",
    waiting_approval: "等待确认",
    tool_call: "正在调用工具",
    tool_result: "工具已返回",
    completed: "已完成",
    failed: "执行失败",
    uncertain: "等待核实"
  };

  function activityLabel(value) {
    const raw = String(value || "").trim();
    if (!raw) return "状态已更新";
    const key = raw.toLowerCase().replace(/[\s-]+/g, "_");
    return ACTIVITY_LABELS[key] || raw;
  }

  function sidebarMessageCount(summary) {
    const id = String(summary && summary.id || "");
    if (id === String(state.currentSessionId || "") && state.currentSession) return visibleMessages(state.currentSession).length;
    if (summary && Array.isArray(summary.messages)) return visibleMessages(summary).length;
    const raw = Number(summary && summary.message_count);
    if (!Number.isFinite(raw)) return null;
    // The API summary includes the single engine system prompt while the
    // message view deliberately hides system entries. Keep both views on the
    // same visible-message count until a full snapshot is available.
    const systemCount = Number(summary && summary.system_message_count);
    if (Number.isFinite(systemCount)) return Math.max(0, raw - systemCount);
    return Math.max(0, raw - (raw > 0 ? 1 : 0));
  }

  function serverHasOptimisticUser(session, optimistic) {
    if (!session || !optimistic) return false;
    const messages = visibleMessages(session);
    const baseUsers = Number(optimistic.baseUserCount);
    const baseMessages = Number(optimistic.baseMessageCount);
    if (!Number.isFinite(baseUsers) || !Number.isFinite(baseMessages)) return false;
    return visibleUserCount(messages) > baseUsers && messages.length > baseMessages;
  }

  function updateMode() {
    const info = state.info || {};
    const mode = String(info.mode || "unconfigured");
    const badge = $("modeBadge");
    const label = $("modeLabel");
    badge.classList.remove("demo", "unconfigured", "error");
    if (mode === "demo") { badge.classList.add("demo"); setText(label, "演示模式 · 脚本模型"); }
    else if (mode === "configured") { setText(label, "模型已配置"); }
    else if (mode === "error") { badge.classList.add("error"); setText(label, "服务异常"); }
    else { badge.classList.add("unconfigured"); setText(label, "未配置模型"); }
    setText($("modeBreadcrumb"), state.practiceWindow.standalone ? "Agent 实操" : mode === "demo" ? "岗位雷达 · 演示模式" : "岗位雷达");
    setText($("workspacePath"), info.workspace || "等待服务返回工作区");
    setText($("workspaceMeta"), mode === "configured" ? "内容将发送到已配置的模型" : mode === "demo" ? "离线脚本模型 · 不代表真实模型" : "本地服务已连接");
    $("workspaceStatusDot").classList.toggle("offline", mode === "error");
    const onboarding = $("modelOnboarding");
    if (onboarding) {
      const show = mode === "unconfigured" || mode === "error";
      onboarding.classList.toggle("hidden", !show);
      if (mode === "error") {
        setText($("onboardingTitle"), "暂时无法连接 校招雷达服务");
        setText($("onboardingText"), "请确认 校招雷达服务已启动并刷新页面；模型设置入口在侧栏底部。");
        setText($("onboardingOpenModelSettings"), "打开模型设置");
      } else {
        setText($("onboardingTitle"), "还没有配置模型");
        setText($("onboardingText"), "点击侧栏底部“管理 API 配置”填写协议、API 地址、模型和密钥；保存后建议新建会话。");
        setText($("onboardingOpenModelSettings"), "打开模型设置");
      }
    }
  }

  function renderSessions() {
    const list = $("sessionList");
    const empty = $("emptySessions");
    qsa(".session-row, .session-item", list).forEach((node) => {
      if (node.classList.contains("session-row")) node.remove();
      else if (!node.parentElement || !node.parentElement.classList.contains("session-row")) node.remove();
    });
    const rows = [...state.sessions].sort((a, b) => String(b.updated_at || b.created_at || "").localeCompare(String(a.updated_at || a.created_at || "")));
    setText($("sessionCount"), rows.length);
    empty.classList.toggle("hidden", rows.length > 0);
    rows.forEach((summary) => {
      const id = String(summary.id || "");
      if (!id) return;
      const row = document.createElement("div");
      row.className = "session-row";
      const button = document.createElement("button");
      button.type = "button";
      button.className = `session-item${id === String(state.currentSessionId || "") ? " active" : ""}`;
      button.dataset.sessionId = id;
      button.setAttribute("aria-current", id === String(state.currentSessionId || "") ? "true" : "false");
      button.appendChild(icon("bot", "session-icon"));
      const body = document.createElement("span");
      body.className = "session-item-body";
      const title = document.createElement("span");
      title.className = "session-title";
      title.textContent = sessionTitle(summary);
      body.appendChild(title);
      const meta = document.createElement("span");
      meta.className = "session-meta";
      const dot = document.createElement("span");
      dot.className = "session-state-dot";
      const busy = Boolean(summary.busy || state.jobs.has(id));
      const pending = Boolean(summary.pending);
      const queued = Number(summary.queued_calls || 0) > 0;
      if (busy) dot.classList.add("busy"); else if (pending || queued) dot.classList.add("pending");
      meta.appendChild(dot);
      const count = sidebarMessageCount(summary);
      if (Number.isFinite(count)) meta.appendChild(document.createTextNode(`${count} 条消息`));
      body.appendChild(meta);
      button.appendChild(body);
      row.appendChild(button);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "session-delete";
      remove.dataset.sessionDelete = id;
      remove.setAttribute("aria-label", `删除会话 ${sessionTitle(summary)}`);
      remove.textContent = "×";
      row.appendChild(remove);
      list.appendChild(row);
    });
  }

  function renderHeader() {
    const session = state.currentSession;
    setText($("currentTitle"), state.practiceWindow.standalone
      ? `实操 · ${state.practiceWindow.projectKey || "当前项目"}`
      : session ? sessionTitle(session) : "新会话");
    renderSessions();
    updateMode();
  }

  function createMessageElement(message, toolMap) {
    const role = message.role === "user" || message.role === "assistant" || message.role === "tool" ? message.role : "assistant";
    const article = document.createElement("article");
    article.className = `message ${role}`;
    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.appendChild(icon(role === "user" ? "user" : role === "tool" ? "code" : "bot"));
    article.appendChild(avatar);
    const body = document.createElement("div");
    body.className = "message-body";
    const label = document.createElement("div");
    label.className = "message-label";
    const labelText = document.createElement("span");
    labelText.textContent = role === "user" ? "你" : role === "tool" ? `工具${message.name ? ` · ${message.name}` : ""}` : "求职教练";
    label.appendChild(labelText);
    if (message.at || message.created_at) {
      const time = document.createElement("span");
      time.className = "message-time";
      time.textContent = formatTime(message.at || message.created_at);
      label.appendChild(time);
    }
    body.appendChild(label);
    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    const text = redactDisplay(contentText(message.content));
    if (text.trim()) bubble.appendChild(renderSafeMarkdown(text));
    if (message.tool_calls && Array.isArray(message.tool_calls) && message.tool_calls.length) {
      const calls = document.createElement("div");
      calls.className = "tool-calls";
      message.tool_calls.forEach((call) => calls.appendChild(createToolCard(call, toolMap.get(String(call.id || "")))));
      bubble.appendChild(calls);
    }
    if (!bubble.childNodes.length) bubble.textContent = role === "assistant" ? "求职教练正在准备下一步…" : "";
    body.appendChild(bubble);
    article.appendChild(body);
    return article;
  }

  function createToolCard(call, result) {
    const details = document.createElement("details");
    details.className = "tool-card";
    const summary = document.createElement("summary");
    summary.appendChild(icon("code"));
    const name = document.createElement("span");
    name.className = "tool-name";
    name.textContent = String(call && call.name || "工具调用");
    summary.appendChild(name);
    if (call && call.id) {
      const id = document.createElement("span");
      id.className = "tool-id";
      id.textContent = String(call.id);
      summary.appendChild(id);
    }
    details.appendChild(summary);
    const detailBody = document.createElement("div");
    detailBody.className = "tool-card-body";
    const argLabel = document.createElement("div");
    argLabel.className = "tool-card-label";
    argLabel.textContent = "参数";
    detailBody.appendChild(argLabel);
    const args = document.createElement("pre");
    args.textContent = redactDisplay(safeString(call && call.arguments || {}));
    detailBody.appendChild(args);
    if (result) {
      const outLabel = document.createElement("div");
      outLabel.className = "tool-card-label";
      outLabel.textContent = "输出";
      detailBody.appendChild(outLabel);
      const output = document.createElement("pre");
      output.className = "tool-output";
      output.textContent = redactDisplay(contentText(result.content));
      detailBody.appendChild(output);
    }
    details.appendChild(detailBody);
    return details;
  }

  function renderMessages(shouldScroll = false) {
    const list = $("messageList");
    list.replaceChildren();
    const practiceScope = state.workflowChat.open && state.workflowChat.scope === "practice";
    const messages = chatMessages(state.currentSession);
    const toolMap = new Map(messages.filter((message) => message.role === "tool" && message.tool_call_id).map((message) => [String(message.tool_call_id), message]));
    messages.forEach((message) => {
      if (message.role === "tool" && message.tool_call_id && toolMap.has(String(message.tool_call_id))) {
        const hasCall = messages.some((candidate) => candidate.tool_calls && candidate.tool_calls.some((call) => String(call.id) === String(message.tool_call_id)));
        if (hasCall) return;
      }
      list.appendChild(createMessageElement(message, toolMap));
    });
    if (practiceScope && state.practiceWindow.standalone && state.practiceWindow.stale) {
      const stale = document.createElement("div");
      stale.className = "practice-stale-state";
      stale.textContent = state.practiceWindow.error || "主窗口已切换到另一个练习项目。为避免把消息写入错误项目，请关闭此窗口并从当前项目重新打开实操。";
      list.prepend(stale);
    }
    if (practiceScope && !messages.length && !state.practice.optimisticMessage) {
      const empty = document.createElement("div");
      empty.className = "practice-empty-state";
      empty.textContent = state.practice.loading
        ? "正在加载当前项目的实操记录…"
        : state.practice.error
          ? `暂时无法读取实操记录：${state.practice.error}`
          : "这是当前项目的独立实操记录。发送第一条消息后，教练会沿用同一求职会话的完整背景。";
      list.appendChild(empty);
    }
    const optimisticState = practiceScope ? state.practice.optimisticMessage : state.optimisticMessage;
    const optimisticMatches = optimisticState && optimisticState.sessionId === String(state.currentSessionId || "")
      && (!practiceScope || !optimisticState.projectKey || optimisticState.projectKey === (state.workflowChat.practiceProjectKey || selectedProjectKey()));
    const serverHasPracticeOptimistic = practiceScope && optimisticState
      ? messages.some((message) => message.role === "user" && messageScope(message) === "practice" && (!optimisticState.projectKey || messageProjectKey(message) === optimisticState.projectKey) && messagePracticeThreadId(message) === state.practice.threadId && contentText(message.content).includes(String(optimisticState.text || "").slice(0, 80)))
      : false;
    const serverHasCurrentOptimistic = practiceScope ? serverHasPracticeOptimistic : serverHasOptimisticUser(state.currentSession, optimisticState);
    if (optimisticMatches && !serverHasCurrentOptimistic) {
      const optimistic = createMessageElement({ role: "user", content: optimisticState.text, scope: practiceScope ? "practice" : "main", project_key: optimisticState.projectKey }, new Map());
      optimistic.classList.add("pending-message");
      list.appendChild(optimistic);
    }
    const hasMessages = list.childElementCount > 0;
    $("welcomePanel").classList.toggle("hidden", hasMessages);
    if (shouldScroll) requestAnimationFrame(() => { $("chatScroll").scrollTop = $("chatScroll").scrollHeight; });
  }

  function commandScopeEnvelope() {
    const practiceScope = state.workflowChat.open && state.workflowChat.scope === "practice";
    if (!practiceScope) return { scope: "main" };
    return {
      scope: "practice",
      project_key: String(state.workflowChat.practiceProjectKey || state.practice.projectKey || "").trim(),
      practice_thread_id: String(state.practice.threadId || "").trim()
    };
  }

  function commandScopeKey(scope = commandScopeEnvelope()) {
    try { return JSON.stringify(scope); } catch (_error) { return String(scope.scope || "main"); }
  }

  function commandStatusLabel(status) {
    const value = String(status || "").trim().toLowerCase();
    return ({
      starting: "启动中",
      running: "运行中",
      completed: "已完成",
      stopped: "已停止",
      timed_out: "已超时",
      uncertain: "结果不确定，需人工确认",
      failed: "失败"
    })[value] || (value || "未知状态");
  }

  function commandIsRunning(item) {
    return ["starting", "running"].includes(String(item && item.status || "").trim().toLowerCase());
  }

  function commandOutput(item) {
    const chunks = [];
    const stdout = String(item && item.stdout || "");
    const stderr = String(item && item.stderr || "");
    if (stdout) chunks.push(`标准输出\n${stdout}`);
    if (stderr) chunks.push(`标准错误\n${stderr}`);
    return chunks.join("\n\n");
  }

  function commandId(item) {
    return String(item && (item.execution_id || item.command_session_id || item.session_id) || "").trim();
  }

  function commandIsTerminal(item) {
    return ["completed", "failed", "timed_out", "stopped"].includes(String(item && item.status || "").trim().toLowerCase());
  }

  function renderCommandRuntime() {
    const panel = $("commandRuntime");
    const list = $("commandRuntimeList");
    if (!panel || !list) return;
    const runtime = state.commandRuntime;
    const sameScope = runtime.sessionId === String(state.currentSessionId || "")
      && runtime.scopeKey === commandScopeKey();
    const items = sameScope && Array.isArray(runtime.items) ? runtime.items : [];
    panel.classList.toggle("hidden", !items.length && !runtime.error);
    list.replaceChildren();
    if (runtime.error && !items.length) {
      const error = document.createElement("div");
      error.className = "command-runtime-error";
      error.textContent = runtime.error;
      list.appendChild(error);
    }
    items.slice(-6).reverse().forEach((item) => {
      const row = document.createElement("article");
      row.className = "command-runtime-item";
      const head = document.createElement("div");
      head.className = "command-runtime-item-head";
      const command = document.createElement("code");
      command.className = "command-runtime-command";
      command.textContent = String(item.command || "命令");
      command.title = String(item.command || "");
      head.appendChild(command);
      const status = document.createElement("span");
      status.className = "command-runtime-state";
      status.textContent = commandStatusLabel(item.status);
      head.appendChild(status);
      const executionId = commandId(item);
      if (commandIsRunning(item) && executionId) {
        const stop = document.createElement("button");
        stop.type = "button";
        stop.className = "command-runtime-stop";
        stop.dataset.commandStop = executionId;
        const stopping = runtime.stopping.has(executionId);
        const completing = runtime.completionJobs.has(executionId);
        stop.disabled = stopping || completing;
        stop.textContent = stopping ? "停止中…" : completing ? "同步中…" : "停止命令";
        stop.setAttribute("aria-label", `停止命令 ${executionId}`);
        head.appendChild(stop);
      }
      row.appendChild(head);
      if (item.purpose) {
        const purpose = document.createElement("p");
        purpose.className = "command-runtime-purpose";
        purpose.textContent = String(item.purpose);
        row.appendChild(purpose);
      }
      const output = commandOutput(item);
      if (output || commandIsRunning(item)) {
        const pre = document.createElement("pre");
        pre.className = "command-runtime-output";
        pre.textContent = output || "正在等待命令输出…";
        row.appendChild(pre);
      }
      list.appendChild(row);
    });
    const running = items.some(commandIsRunning);
    setText($("commandRuntimeStatus"), runtime.error && !items.length ? "读取失败" : running ? "运行中" : items.length ? "已同步" : "等待命令");
  }

  function clearCommandRuntime() {
    state.commandRuntime.sessionId = "";
    state.commandRuntime.scopeKey = "";
    state.commandRuntime.items = [];
    state.commandRuntime.error = "";
    state.commandRuntime.stopping.clear();
    state.commandRuntime.completionJobs.clear();
    state.commandRuntime.settled.clear();
    renderCommandRuntime();
  }

  async function refreshCommandRuntime() {
    const id = String(state.currentSessionId || "");
    if (!id || document.visibilityState === "hidden" || state.commandRuntime.refreshInFlight) return;
    const scope = commandScopeEnvelope();
    const scopeKey = commandScopeKey(scope);
    if (state.commandRuntime.sessionId !== id || state.commandRuntime.scopeKey !== scopeKey) {
      state.commandRuntime.sessionId = id;
      state.commandRuntime.scopeKey = scopeKey;
      state.commandRuntime.items = [];
      state.commandRuntime.error = "";
      state.commandRuntime.stopping.clear();
      state.commandRuntime.completionJobs.clear();
      state.commandRuntime.settled.clear();
    }
    state.commandRuntime.refreshInFlight = true;
    try {
      const query = new URLSearchParams(scope).toString();
      const payload = await request(`/sessions/${encodeURIComponent(id)}/commands?${query}`);
      if (String(state.currentSessionId || "") !== id || commandScopeKey() !== scopeKey) return;
      state.commandRuntime.items = Array.isArray(payload && payload.commands) ? payload.commands : [];
      state.commandRuntime.error = "";
      renderActivity();
      const execution = state.currentSession && state.currentSession.execution;
      const liveCommandId = execution && execution.status === "running"
        ? commandId(execution)
        : "";
      for (const item of state.commandRuntime.items) {
        if (!commandIsTerminal(item)) continue;
        const executionId = commandId(item);
        // The backend keeps the original submit job alive and normally
        // resumes the model without browser help.  A POST fallback is only
        // valid for the one durable command claim still marked running;
        // historical terminal rows must remain display-only.
        if (!executionId || executionId !== liveCommandId) continue;
        if (!executionId || state.commandRuntime.settled.has(executionId) || state.commandRuntime.completionJobs.has(executionId)) continue;
        // GET only observes the process. Commit one terminal result and
        // continue the saved model turn through an async engine job.
        state.commandRuntime.completionJobs.set(executionId, { pending: true, kind: "command-poll" });
        try {
          const completion = await request("/sessions/" + encodeURIComponent(id) + "/commands/" + encodeURIComponent(executionId) + "/poll", {
            method: "POST",
            body: { ...scope, wait_seconds: 0 }
          });
          const jobId = String(completion && completion.job_id || "").trim();
          if (!jobId) throw new Error("服务没有返回命令结果任务");
          state.commandRuntime.completionJobs.set(executionId, { jobId, kind: "command-poll" });
          state.jobs.set(id, { id: jobId, commandId: executionId, kind: "command-poll" });
          startPolling(id);
        } catch (error) {
          state.commandRuntime.completionJobs.delete(executionId);
          if (String(state.currentSessionId || "") === id) state.commandRuntime.error = error.message || "同步命令结果失败";
        }
      }
      renderCommandRuntime();
      const needsRefresh = state.practiceWindow.standalone
        || sessionBusy(state.currentSession)
        || state.commandRuntime.items.some(commandIsRunning);
      if (needsRefresh && !state.commandRuntime.refreshTimer && document.visibilityState !== "hidden") {
        state.commandRuntime.refreshTimer = window.setInterval(() => { void refreshCommandRuntime(); }, 1000);
      } else if (!needsRefresh) {
        clearCommandRuntimeRefresh();
      }
    } catch (error) {
      if (String(state.currentSessionId || "") !== id || commandScopeKey() !== scopeKey) return;
      state.commandRuntime.error = error.message || "暂时无法读取命令状态";
      state.commandRuntime.items = [];
      renderActivity();
    } finally {
      state.commandRuntime.refreshInFlight = false;
    }
  }

  function clearCommandRuntimeRefresh() {
    if (state.commandRuntime.refreshTimer) {
      window.clearInterval(state.commandRuntime.refreshTimer);
      state.commandRuntime.refreshTimer = null;
    }
  }

  function startCommandRuntimeRefresh() {
    if (document.visibilityState === "hidden") return;
    clearCommandRuntimeRefresh();
    void refreshCommandRuntime();
    if (!state.practiceWindow.standalone && !sessionBusy(state.currentSession) && !state.commandRuntime.items.some(commandIsRunning)) return;
    state.commandRuntime.refreshTimer = window.setInterval(() => { void refreshCommandRuntime(); }, 1000);
  }

  async function stopCommandRuntime(executionId) {
    const id = String(state.currentSessionId || "");
    const commandId = String(executionId || "").trim();
    if (!id || !commandId || state.commandRuntime.stopping.has(commandId) || state.commandRuntime.completionJobs.has(commandId)) return;
    const scope = commandScopeEnvelope();
    state.commandRuntime.stopping.add(commandId);
    renderCommandRuntime();
    try {
      const completion = await request("/sessions/" + encodeURIComponent(id) + "/commands/" + encodeURIComponent(commandId) + "/stop", {
        method: "POST",
        body: scope
      });
      const jobId = String(completion && completion.job_id || "").trim();
      if (!jobId) throw new Error("服务没有返回停止任务");
      state.commandRuntime.completionJobs.set(commandId, { jobId, kind: "command-stop" });
      state.jobs.set(id, { id: jobId, commandId, kind: "command-stop" });
      startPolling(id);
      await refreshCommandRuntime();
    } catch (error) {
      state.commandRuntime.error = error.message || "停止命令失败";
      renderCommandRuntime();
    } finally {
      state.commandRuntime.stopping.delete(commandId);
      renderCommandRuntime();
    }
  }

  function renderActivity() {
    const panel = $("activityPanel");
    const practiceScope = state.workflowChat.open && state.workflowChat.scope === "practice";
    const practiceKey = state.workflowChat.practiceProjectKey || selectedProjectKey();
    const scopedSnapshotLoaded = practiceScope
      && state.practice.loaded
      && state.practice.sessionId === String(state.currentSessionId || "")
      && state.practice.projectKey === String(practiceKey || "").trim()
      && Boolean(state.practice.threadId);
    const allEvents = scopedSnapshotLoaded
      ? state.practice.events
      : (Array.isArray(state.currentSession && state.currentSession.events) ? state.currentSession.events : []);
    const events = allEvents.filter((event) => {
      if (!practiceScope) return !event || eventScope(event) !== "practice";
      if (eventScope(event) !== "practice") return false;
      const eventKey = eventProjectKey(event);
      const eventThread = eventPracticeThreadId(event);
      return Boolean(practiceKey && eventKey && eventKey === practiceKey && state.practice.threadId && eventThread === state.practice.threadId);
    }).slice(-5);
    const busy = sessionBusy(state.currentSession);
    const queued = sessionHasQueue(state.currentSession);
    const queuedForScope = practiceScope ? practiceQueueMatches(state.currentSession) : queued;
    const commandScope = commandScopeEnvelope();
    const commandScopeMatches = state.commandRuntime.sessionId === String(state.currentSessionId || "")
      && state.commandRuntime.scopeKey === commandScopeKey(commandScope);
    const commands = commandScopeMatches ? state.commandRuntime.items : [];
    const commandBusy = commands.some(commandIsRunning);
    const scopeBusy = practiceScope ? Boolean(state.currentSession && state.currentSession.practice_busy) || Boolean(state.practice.optimisticMessage) && busy : busy;
    panel.classList.toggle("hidden", events.length === 0 && !scopeBusy && !queuedForScope && !commands.length && !state.commandRuntime.error);
    const list = $("activityList");
    list.replaceChildren();
    [...events].reverse().forEach((event) => {
      const row = document.createElement("div");
      row.className = "activity-item";
      const time = document.createElement("span");
      time.className = "activity-time";
      time.textContent = formatTime(event.at);
      row.appendChild(time);
      const message = document.createElement("span");
      message.textContent = activityLabel(event.message || event.type);
      row.appendChild(message);
      list.appendChild(row);
    });
    setText($("activityStatus"), scopeBusy ? "处理中" : commandBusy ? "命令运行中" : queuedForScope ? "待继续" : events.length ? "已记录" : "等待操作");
    $("activityPanel").querySelector(".activity-pulse").style.animationPlayState = scopeBusy || commandBusy ? "running" : "paused";
    renderCommandRuntime();
  }

  function approvalIdFor(session) {
    if (!session) return "";
    const pending = session.pending;
    if (pending && pending.id) return String(pending.id);
    const execution = session.execution || {};
    return String(execution.approval_id || execution.call_id || "");
  }

  function practiceApprovalMatches(session) {
    if (!session) return false;
    const practiceKey = state.workflowChat.practiceProjectKey || selectedProjectKey();
    const thread = String(state.practice.threadId || "").trim();
    return [session.pending, session.execution].some((record) => scopedTurnMatches(record, practiceKey, thread));
  }

  function scopedTurnEnvelope(record) {
    if (!record || typeof record !== "object") return { scope: "main" };
    const metadata = record.metadata && typeof record.metadata === "object" ? record.metadata : {};
    const scope = String(record.scope || metadata.scope || "main").trim().toLowerCase();
    if (scope !== "practice") return { scope: "main" };
    return {
      scope: "practice",
      project_key: String(record.project_key || metadata.project_key || "").trim(),
      practice_thread_id: String(record.practice_thread_id || metadata.practice_thread_id || "").trim()
    };
  }

  function scopedTurnMatches(record, projectKey, threadId) {
    const envelope = scopedTurnEnvelope(record);
    return envelope.scope === "practice"
      && Boolean(String(projectKey || "").trim())
      && envelope.project_key === String(projectKey || "").trim()
      && Boolean(String(threadId || "").trim())
      && envelope.practice_thread_id === String(threadId || "").trim();
  }

  function practiceQueueMatches(session) {
    if (!session || Number(session.queued_calls || 0) <= 0) return false;
    const execution = session.execution && typeof session.execution === "object" && Object.keys(session.execution).length ? session.execution : null;
    const active = session.pending || execution || session.practice_filter;
    const practiceKey = state.workflowChat.practiceProjectKey || selectedProjectKey();
    return scopedTurnMatches(active, practiceKey, state.practice.threadId);
  }

  function renderApproval() {
    const region = $("approvalRegion");
    region.replaceChildren();
    const session = state.currentSession;
    if (!session) return;
    const pending = session.pending;
    const execution = session.execution || {};
    const liveWorker = Boolean(session.busy || state.jobs.has(String(session.id)));
    const practiceScope = state.workflowChat.open && state.workflowChat.scope === "practice";
    if (practiceScope && pending && !practiceApprovalMatches(session)) {
      const notice = document.createElement("article");
      notice.className = "approval-card practice-main-approval-notice";
      notice.innerHTML = `<div class="approval-head"><span class="approval-icon" data-icon="warning"></span><div><div class="approval-kicker">主对话需要处理</div><h3>当前会话有一项待确认操作</h3></div></div>`;
      const body = document.createElement("div"); body.className = "approval-body";
      const summary = document.createElement("p"); summary.className = "approval-summary";
      summary.textContent = "这项操作属于主教练对话，练习窗口不会替它批准。打开主对话后核对完整预览。";
      body.appendChild(summary);
      const actions = document.createElement("div"); actions.className = "approval-actions";
      const open = document.createElement("button"); open.type = "button"; open.className = "action-button resolve"; open.textContent = "打开主对话处理";
      open.addEventListener("click", () => { closeWorkflowChatDialog(); openWorkflowChatDialog(); });
      actions.appendChild(open); body.appendChild(actions); notice.appendChild(body); region.appendChild(notice); hydrateIcons(notice);
      return;
    }
    const isUnknown = !liveWorker && ["uncertain", "running"].includes(String(execution.status || "")) && approvalIdFor(session);
    if (practiceScope && isUnknown && !practiceApprovalMatches(session)) {
      const notice = document.createElement("article");
      notice.className = "approval-card practice-main-approval-notice";
      notice.innerHTML = `<div class="approval-head"><span class="approval-icon" data-icon="warning"></span><div><div class="approval-kicker">主对话需要处理</div><h3>主对话的执行结果待核实</h3></div></div>`;
      const body = document.createElement("div"); body.className = "approval-body";
      const summary = document.createElement("p"); summary.className = "approval-summary";
      summary.textContent = "这项结果没有当前练习项目的精确标记，练习窗口不会替主对话核实。打开主对话后记录核实结果。";
      body.appendChild(summary);
      const actions = document.createElement("div"); actions.className = "approval-actions";
      const open = document.createElement("button"); open.type = "button"; open.className = "action-button resolve"; open.textContent = "打开主对话处理";
      open.addEventListener("click", () => { closeWorkflowChatDialog(); openWorkflowChatDialog({ scrollApproval: true }); });
      actions.appendChild(open); body.appendChild(actions); notice.appendChild(body); region.appendChild(notice); hydrateIcons(notice);
      return;
    }
    if (pending && !isUnknown) {
      const card = document.createElement("article");
      card.className = "approval-card";
      card.innerHTML = `<div class="approval-head"><span class="approval-icon" data-icon="warning"></span><div><div class="approval-kicker">需要确认的操作</div><h3>请确认求职教练的下一步操作</h3></div></div>`;
      const body = document.createElement("div");
      body.className = "approval-body";
      const summary = document.createElement("p");
      summary.className = "approval-summary";
      summary.textContent = String(pending.summary || `工具 ${pending.tool_name || "操作"} 请求执行`);
      body.appendChild(summary);
      const idRow = document.createElement("div");
      idRow.className = "approval-id-row";
      idRow.appendChild(document.createTextNode("审批 ID"));
      const id = document.createElement("code");
      id.className = "approval-id";
      id.textContent = String(pending.id || "");
      idRow.appendChild(id);
      body.appendChild(idRow);
      const previewLabel = document.createElement("div");
      previewLabel.className = "approval-preview-label";
       previewLabel.textContent = "完整预览 / 差异";
      body.appendChild(previewLabel);
      const preview = document.createElement("pre");
      preview.className = "approval-preview";
      preview.textContent = redactDisplay(String(pending.preview || safeString(pending.arguments || {})));
      body.appendChild(preview);
      const actions = document.createElement("div");
      actions.className = "approval-actions";
      const approve = document.createElement("button");
      approve.type = "button"; approve.className = "action-button approve"; approve.dataset.action = "approve";
      approve.appendChild(icon("check")); approve.appendChild(document.createTextNode("批准并继续"));
      const reject = document.createElement("button");
      reject.type = "button"; reject.className = "action-button reject"; reject.dataset.action = "reject";
      reject.appendChild(icon("close")); reject.appendChild(document.createTextNode("拒绝"));
      if (sessionBusy(session)) { approve.disabled = true; reject.disabled = true; }
      actions.append(approve, reject);
      body.appendChild(actions);
      const foot = document.createElement("p");
      foot.className = "approval-footnote";
      foot.textContent = "请核对完整 ID 与预览内容。只有当前会话的精确 ID 才会被接受。";
      body.appendChild(foot);
      card.appendChild(body);
      region.appendChild(card);
      hydrateIcons(card);
      return;
    }
    const queuedCalls = Number(session.queued_calls || 0);
    if (practiceScope && !practiceQueueMatches(session) && !pending && !liveWorker && !isUnknown && Number.isFinite(queuedCalls) && queuedCalls > 0) {
      const notice = document.createElement("article");
      notice.className = "approval-card practice-main-approval-notice";
      notice.innerHTML = `<div class="approval-head"><span class="approval-icon" data-icon="warning"></span><div><div class="approval-kicker">主对话需要处理</div><h3>主对话工具队列待恢复</h3></div></div>`;
      const body = document.createElement("div"); body.className = "approval-body";
      const summary = document.createElement("p"); summary.className = "approval-summary";
      summary.textContent = `主对话还有 ${queuedCalls} 个工具调用等待恢复；练习窗口不会替它继续。打开主对话后按原顺序恢复。`;
      body.appendChild(summary);
      const actions = document.createElement("div"); actions.className = "approval-actions";
      const open = document.createElement("button"); open.type = "button"; open.className = "action-button resolve"; open.textContent = "打开主对话处理";
      open.addEventListener("click", () => { closeWorkflowChatDialog(); openWorkflowChatDialog({ scrollApproval: true }); });
      actions.appendChild(open); body.appendChild(actions); notice.appendChild(body); region.appendChild(notice); hydrateIcons(notice);
      return;
    }
    if (!pending && !liveWorker && !isUnknown && Number.isFinite(queuedCalls) && queuedCalls > 0) {
      const card = document.createElement("article");
      card.className = "approval-card queue-recovery-card";
      card.innerHTML = `<div class="approval-head"><span class="approval-icon" data-icon="warning"></span><div><div class="approval-kicker">工具队列待恢复</div><h3>上一轮工具调用尚未完成</h3></div></div>`;
      const body = document.createElement("div");
      body.className = "approval-body";
      const summary = document.createElement("p");
      summary.className = "approval-summary";
      summary.textContent = `还有 ${queuedCalls} 个工具调用等待按原顺序处理。继续会恢复这一轮对话，不会新增用户消息。`;
      body.appendChild(summary);
      const actions = document.createElement("div");
      actions.className = "approval-actions";
      const resume = document.createElement("button");
      resume.type = "button";
      resume.className = "action-button resolve";
      resume.dataset.action = "continue";
      resume.appendChild(icon("arrow-right"));
      resume.appendChild(document.createTextNode("继续执行工具队列"));
      // A live job may still be draining the queue. The button becomes
      // enabled after the next snapshot reports the persisted queue as idle.
      const liveJob = Boolean(session.busy || state.jobs.has(String(session.id)));
      resume.disabled = liveJob;
      actions.appendChild(resume);
      body.appendChild(actions);
      const foot = document.createElement("p");
      foot.className = "approval-footnote";
      foot.textContent = "恢复前不会重放已获批的副作用操作；需要人工确认的操作仍会单独显示。";
      body.appendChild(foot);
      card.appendChild(body);
      region.appendChild(card);
      hydrateIcons(card);
      return;
    }
    if (isUnknown) {
      const card = document.createElement("article");
      card.className = "approval-card unknown-card";
      card.innerHTML = `<div class="approval-head"><span class="approval-icon" data-icon="warning"></span><div><div class="approval-kicker">执行结果待确认</div><h3>上次操作的结果需要人工确认</h3></div></div>`;
      const body = document.createElement("div"); body.className = "approval-body";
      const summary = document.createElement("p"); summary.className = "approval-summary";
      summary.textContent = "不会自动重放这次操作。请核实外部状态，记录你的结论后再继续。";
      body.appendChild(summary);
      const idRow = document.createElement("div"); idRow.className = "approval-id-row";
      idRow.appendChild(document.createTextNode("执行 ID"));
      const id = document.createElement("code"); id.className = "approval-id"; id.textContent = approvalIdFor(session); idRow.appendChild(id); body.appendChild(idRow);
      const note = document.createElement("textarea"); note.className = "resolve-note"; note.id = "resolveNote"; note.placeholder = "写下核实结果，例如：已在目标文件确认写入完成。"; note.setAttribute("aria-label", "未知执行结果的核实备注"); body.appendChild(note);
      const actions = document.createElement("div"); actions.className = "approval-actions";
      const resolve = document.createElement("button"); resolve.type = "button"; resolve.className = "action-button resolve"; resolve.dataset.action = "resolve"; resolve.appendChild(icon("check")); resolve.appendChild(document.createTextNode("记录结果并继续")); actions.appendChild(resolve); body.appendChild(actions);
      const foot = document.createElement("p"); foot.className = "approval-footnote"; foot.textContent = "记录只会解除自动重试保护，不会再次执行原操作；继续由你发起。"; body.appendChild(foot);
      card.appendChild(body); region.appendChild(card); hydrateIcons(card);
    }
  }

  function renderBusy() {
    const busy = sessionBusy(state.currentSession);
    const pending = Boolean(state.currentSession && state.currentSession.pending);
    const queued = sessionHasQueue(state.currentSession);
    const practiceScope = state.workflowChat.open && state.workflowChat.scope === "practice";
    const practicePending = practiceScope && pending && practiceApprovalMatches(state.currentSession);
    const practiceQueue = practiceScope && practiceQueueMatches(state.currentSession);
    const stalePractice = practiceScope && state.practiceWindow.standalone && state.practiceWindow.stale;
    const blocked = busy || pending || queued || stalePractice;
    $("busyBar").classList.toggle("hidden", !busy);
    $("sendButton").disabled = blocked;
    $("composerInput").disabled = blocked;
    $("sendButton").setAttribute("aria-busy", busy ? "true" : "false");
    $("composerInput").placeholder = practiceScope
      ? (stalePractice
        ? "主窗口已切换练习项目，请从当前项目重新打开实操…"
        : pending
        ? (practicePending ? "请先处理练习操作的审批，再发送新的消息…" : "主对话有待确认操作，请先打开主对话处理…")
        : queued
         ? (practiceScope && practiceQueue ? "当前项目工具队列待继续，请在练习窗口恢复…" : "主对话工具队列待继续，请先打开主对话恢复…")
          : busy
            ? "练习教练正在处理当前项目…"
            : "描述当前项目进展、遇到的错误或下一步问题…")
      : pending
      ? "请先处理上方审批，再发送新的消息…"
      : queued
        ? "上一轮工具队列待继续，请先点击下方“继续执行工具队列”…"
        : busy
          ? "求职教练正在处理当前会话…"
          : "粘贴 JD、说明已有技能，或上传相关材料…";
    const job = currentJob();
    setText(
      $("busyText"),
      job
          ? "求职教练正在运行，状态每秒更新…"
          : "会话正在恢复，状态会自动更新…",
    );
  }

  function renderAll({ scroll = false } = {}) {
    state.renderSerial += 1;
    if (state.jobEditor.open && state.jobEditor.sessionId !== String(state.currentSessionId || "")) closeJobEditor();
    renderHeader();
    renderMessages(scroll);
    renderActivity();
    renderApproval();
    renderBusy();
    renderInspector();
    renderWorkflowPage();
    if (state.clarification.open && state.clarification.sessionId !== String(state.currentSessionId || "")) closeClarificationModal({ restore: false });
    else syncClarificationModal();
    const attachment = $("attachmentStrip");
    const attachments = Array.isArray(state.uploadedAttachments)
      ? state.uploadedAttachments
      : (state.uploadedAttachment ? [state.uploadedAttachment] : []);
    attachment.classList.toggle("hidden", attachments.length === 0);
    attachment.replaceChildren();
    attachments.forEach((uploaded, index) => {
      const chip = document.createElement("div"); chip.className = "attachment-chip";
      chip.appendChild(icon("paperclip"));
      const label = document.createElement("span"); label.textContent = `${uploaded.path || uploaded.name} · 已上传`; chip.appendChild(label);
      const remove = document.createElement("button"); remove.type = "button"; remove.setAttribute("aria-label", `移除附件 ${uploaded.name || index + 1}`); remove.appendChild(icon("close"));
      remove.addEventListener("click", () => {
        state.uploadedAttachments = attachments.filter((_item, itemIndex) => itemIndex !== index);
        state.uploadedAttachment = state.uploadedAttachments[0] || null;
        captureComposerDraft();
        renderAll();
      });
      chip.appendChild(remove);
      attachment.appendChild(chip); hydrateIcons(chip);
    });
    enhanceVoiceInputs();
    hydrateIcons();
  }

  const WORKFLOW_STEPS = [
    { id: "jd", title: "① 岗位 JD", hint: "粘贴目标岗位 JD，或上传岗位截图。你也可以手动填写；未知信息请留空。", prompt: "请整理我接下来提供的目标岗位 JD，保留不确定字段，保存到当前求职会话。" },
    { id: "profile", title: "② 技能档案", hint: "补充已有技能、熟练程度和事实证据，上传简历或项目材料。待澄清问题会保留在这里。", prompt: "请根据我提供的简历和技能事实整理当前会话的技能档案，缺少证据的内容请向我澄清。" },
    { id: "plan", title: "③ 计划 & 项目", hint: "基于岗位和技能档案分析差距、制定计划。每次选择一个练习项目，可逐个完成多个项目。", prompt: "请根据当前会话的岗位 JD 和技能档案分析差距，生成学习计划并搜索适合我的候选练习项目。" },
    { id: "practice", title: "④ Agent 实操", hint: "围绕选定项目继续实操，记录真实结果和证据。文件修改、命令执行仍由你确认。", prompt: "请围绕当前已选练习项目给出今天的实操任务，逐步指导我执行，并根据实际结果记录进度与证据。" }
  ];

  function workflowData() {
    const session = state.currentSession || {};
    return (session.skill_state && session.skill_state["job-radar"]) || session.job_state || session.job_radar || session.job || {};
  }

  function renderWorkflow() {
    const data = workflowData();
    const saved = (value) => Boolean(value && typeof value === "object" && Object.values(value).some((item) => Array.isArray(item) ? item.length : item && String(item).trim()));
    const questions = [...(Array.isArray(data.profile?.open_questions) ? data.profile.open_questions : []), ...(Array.isArray(data.clarification?.open_questions) ? data.clarification.open_questions : [])];
    const statuses = {
      jd: saved(data.jd) ? "已保存" : "待填写",
      profile: saved(data.profile) ? (questions.length ? "待澄清" : "已保存") : "待填写",
      plan: saved(data.plan) ? (saved(data.selected_project) ? "已选项目" : "已有计划") : "待生成",
      practice: Array.isArray(data.progress) && data.progress.length ? (data.progress.some(progressIsCompleted) ? "已记录完成" : "已有进度") : "待开始"
    };
    qsa("[data-step]").forEach((button) => {
      const id = button.dataset.step;
      const active = id === state.selectedStep;
      button.classList.toggle("active", active);
      button.classList.toggle("has-data", !["待填写", "待生成", "待开始"].includes(statuses[id]));
      if (active) button.setAttribute("aria-current", "step"); else button.removeAttribute("aria-current");
      qs(".step-status", button).textContent = statuses[id];
    });
  }

  function selectWorkflowStep(id) {
    if (!WORKFLOW_STEPS.some((step) => step.id === id)) return;
    if (state.voice.active || state.voice.stopping) stopVoiceInput({ cancel: true });
    if (state.jdImport.open) saveJdDraft();
    state.selectedStep = id;
    state.pageView = true;
    state.selectedTab = "job";
    renderInspector();
    renderWorkflowPage();
  }

  function mountSharedChat(host) {
    const chat = qs(".chat-column");
    if (!chat || !host) return null;
    if (chat.parentNode !== host) host.appendChild(chat);
    return chat;
  }

  function composerDraftKey(scope = state.workflowChat.scope, sessionId = state.currentSessionId, projectKey = state.workflowChat.practiceProjectKey || state.practice.projectKey) {
    const normalizedScope = scope === "practice" ? "practice" : "main";
    const normalizedSession = String(sessionId || "new");
    const normalizedProject = normalizedScope === "practice" ? String(projectKey || "") : "";
    return JSON.stringify([normalizedSession, normalizedScope, normalizedProject]);
  }

  function captureComposerDraft(scope = state.workflowChat.scope, sessionId = state.currentSessionId, projectKey = state.workflowChat.practiceProjectKey || state.practice.projectKey) {
    const key = composerDraftKey(scope, sessionId, projectKey);
    const input = $("composerInput");
    state.workflowChat.drafts.set(key, String(input?.value || ""));
    const attachments = Array.isArray(state.uploadedAttachments)
      ? state.uploadedAttachments
      : (state.uploadedAttachment ? [state.uploadedAttachment] : []);
    state.workflowChat.draftAttachments.set(key, attachments.map((item) => ({ ...item })));
  }

  function storeComposerDraft(scope, sessionId, projectKey, text, attachments = []) {
    const key = composerDraftKey(scope, sessionId, projectKey);
    state.workflowChat.drafts.set(key, String(text || ""));
    state.workflowChat.draftAttachments.set(key, (Array.isArray(attachments) ? attachments : []).map((item) => ({ ...item })));
  }

  function clearComposerDraft(scope = state.workflowChat.scope, sessionId = state.currentSessionId, projectKey = state.workflowChat.practiceProjectKey || state.practice.projectKey) {
    const key = composerDraftKey(scope, sessionId, projectKey);
    state.workflowChat.drafts.delete(key);
    state.workflowChat.draftAttachments.delete(key);
  }

  function restoreComposerDraft(scope = state.workflowChat.scope, sessionId = state.currentSessionId, projectKey = state.workflowChat.practiceProjectKey || state.practice.projectKey) {
    const key = composerDraftKey(scope, sessionId, projectKey);
    const input = $("composerInput");
    if (input) input.value = state.workflowChat.drafts.get(key) || "";
    const attachments = (state.workflowChat.draftAttachments.get(key) || []).map((item) => ({ ...item }));
    state.uploadedAttachments = attachments;
    state.uploadedAttachment = attachments[0] || null;
    resizeComposer();
  }

  function openWorkflowChatDialog({ focus = true, scrollApproval = false } = {}) {
    if (!state.pageView) return false;
    if (state.jdImport.open) {
      saveJdDraft();
      $("jdImportBackdrop")?.classList.add("hidden");
      state.jdImport.open = false;
    }
    if ((state.voice.active || state.voice.stopping) && state.workflowChat.scope !== "main") stopVoiceInput({ cancel: true });
    const wasPractice = state.workflowChat.scope === "practice";
    captureComposerDraft(state.workflowChat.scope);
    if (wasPractice) state.practice.viewSerial += 1;
    const host = $("practiceChatHost");
    const backdrop = $("workflowChatBackdrop");
    const chat = mountSharedChat(host);
    if (!host || !backdrop || !chat) return false;
    if (!state.workflowChat.open) {
      state.workflowChat.open = true;
      const active = document.activeElement;
      const activeInAnotherModal = qsa(".modal-backdrop").some((item) => item !== backdrop && item.contains(active));
      state.workflowChat.previousFocus = activeInAnotherModal ? $("backToChat") : active;
    }
    state.workflowChat.scope = "main";
    state.workflowChat.practiceProjectKey = "";
    restoreComposerDraft("main");
    const step = WORKFLOW_STEPS.find((item) => item.id === state.selectedStep);
    setText($("workflowChatTitle"), step ? `${step.title} · 当前会话` : "求职辅导对话");
    backdrop.classList.remove("hidden");
    backdrop.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    if (focus) window.requestAnimationFrame(() => {
      if (!state.workflowChat.open) return;
      if (scrollApproval) {
        const region = $("approvalRegion");
        if (region && typeof region.scrollIntoView === "function") region.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      const input = $("composerInput");
      if (input && !input.disabled) input.focus();
      else $("workflowChatClose")?.focus();
    });
    renderAll();
    return true;
  }

  function showStandalonePracticeStale(sessionId, projectKey, message = "") {
    const host = $("practiceChatHost");
    const backdrop = $("workflowChatBackdrop");
    if (!host || !backdrop || !mountSharedChat(host)) return false;
    state.practiceWindow.stale = true;
    state.practiceWindow.error = String(message || "").trim();
    state.workflowChat.open = true;
    state.workflowChat.scope = "practice";
    state.workflowChat.practiceProjectKey = String(projectKey || state.practiceWindow.projectKey || "").trim();
    state.practice.sessionId = String(sessionId || state.currentSessionId || "");
    state.practice.projectKey = state.workflowChat.practiceProjectKey;
    state.practice.projectTitle = "当前项目";
    state.practice.threadId = "";
    state.practice.loaded = true;
    state.practice.loading = false;
    state.practice.error = "";
    restoreComposerDraft("practice");
    backdrop.classList.remove("hidden");
    backdrop.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    renderAll();
    startPracticeWindowRefresh();
    return true;
  }

  async function openPracticeChatDialog({ focus = true, start = false } = {}) {
    if (!state.pageView || !state.currentSessionId) return false;
    if (state.practiceWindow.standalone && start) {
      start = !state.practiceWindow.startConsumed;
      state.practiceWindow.startConsumed = true;
    }
    const data = workflowData();
    const selected = data && data.selected_project;
    const projectKey = selectedProjectKey(data);
    const sessionId = String(state.currentSessionId || "");
    if (!selected || !projectKey) {
      if (state.practiceWindow.standalone) {
        showStandalonePracticeStale(sessionId, state.practiceWindow.projectKey, "当前练习项目已不可用。为避免把消息写入错误项目，请关闭此窗口并从当前项目重新打开实操。");
        return false;
      }
      toast("请先在“计划 & 项目”中选定练习项目。", "");
      return false;
    }
    if (state.practiceWindow.standalone && state.practiceWindow.projectKey && state.practiceWindow.projectKey !== projectKey) {
      showStandalonePracticeStale(sessionId, state.practiceWindow.projectKey, "主窗口已切换到另一个练习项目。为避免把消息写入错误项目，请关闭此窗口并从当前项目重新打开实操。");
      return false;
    }
    state.practiceWindow.stale = false;
    state.practiceWindow.error = "";
    if (!state.practiceWindow.standalone && openPracticeWindow(sessionId, projectKey, start)) {
      // The native/browser child owns its own chat DOM and transcript view.
      // Keep the parent page on the workflow page so its drafts, inspector,
      // and main conversation remain usable while practice runs.
      return true;
    }
    if (start
      && state.practice.startPending
      && state.practice.sessionId === String(state.currentSessionId)
      && state.practice.projectKey === projectKey) return true;
    if (start) state.practice.startPending = true;
    if (state.jdImport.open) {
      saveJdDraft();
      $("jdImportBackdrop")?.classList.add("hidden");
      state.jdImport.open = false;
    }
    if (state.voice.active || state.voice.stopping) stopVoiceInput({ cancel: true });
    captureComposerDraft(state.workflowChat.scope);
    const host = $("practiceChatHost");
    const backdrop = $("workflowChatBackdrop");
    const chat = mountSharedChat(host);
    if (!host || !backdrop || !chat) {
      state.practice.startPending = false;
      return false;
    }
    const active = document.activeElement;
    if (!state.workflowChat.open) {
      state.workflowChat.open = true;
      const activeInAnotherModal = qsa(".modal-backdrop").some((item) => item !== backdrop && item.contains(active));
      state.workflowChat.previousFocus = activeInAnotherModal ? $("backToChat") : active;
    }
    state.workflowChat.scope = "practice";
    state.workflowChat.practiceProjectKey = projectKey;
    const viewSerial = ++state.practice.viewSerial;
    const sameThread = state.practice.sessionId === sessionId && state.practice.projectKey === projectKey && Boolean(state.practice.threadId);
    state.practice.sessionId = sessionId;
    state.practice.projectKey = projectKey;
    state.practice.projectTitle = projectDisplayTitle(selected);
    if (!sameThread) {
      state.practice.threadId = "";
      state.practice.messages = [];
      state.practice.events = [];
      state.practice.loaded = false;
      if (!start) state.practice.startPending = false;
    }
    state.practice.loading = true;
    state.practice.error = "";
    restoreComposerDraft("practice");
    const kicker = qs(".workflow-chat-header .inspect-kicker");
    if (kicker) kicker.textContent = "当前练习项目 · 独立记录";
    backdrop.classList.remove("hidden");
    backdrop.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    renderAll();
    try {
      await ensurePracticeThread(sessionId, projectKey, viewSerial);
      if (!practiceContextMatches(sessionId, projectKey, "", viewSerial)) return false;
      const threadId = state.practice.threadId;
      await refreshPracticeThread(sessionId, threadId, viewSerial);
      if (!practiceContextMatches(sessionId, projectKey, threadId, viewSerial)) return false;
      // Reopening an existing thread only reads its durable transcript. The
      // first explicit click may seed a new thread once, after the server has
      // confirmed that no practice turn exists; busy/pending main turns never
      // start a second run here.
      const canStart = start
        && state.workflowChat.open
        && state.workflowChat.scope === "practice"
        && state.currentSessionId === sessionId
        && state.practice.viewSerial === viewSerial
        && !practiceHasMessages(state.currentSession, projectKey, threadId)
        && !state.currentSession?.pending && !sessionTurnBlocked(state.currentSession);
      if (canStart) {
        state.practice.startPending = false;
        void sendPracticeMessage(
          "请围绕当前练习项目给出首个可执行步骤，等待我完成后记录真实结果与证据。",
          { summary: "开始今天的实操", projectKey, viewSerial }
        );
      } else state.practice.startPending = false;
    } catch (error) {
      if (error && error.practiceScopeChanged) return false;
      state.practice.startPending = false;
      if (practiceContextMatches(sessionId, projectKey, "", viewSerial)) {
        state.practice.loading = false;
        state.practice.error = error.message || "无法打开实操记录";
        renderAll();
        toast(state.practice.error, "error");
      }
    }
    if (state.practiceWindow.standalone) startPracticeWindowRefresh();
    if (focus) window.requestAnimationFrame(() => {
      if (!state.workflowChat.open || state.workflowChat.scope !== "practice") return;
      const input = $("composerInput");
      if (input && !input.disabled) input.focus();
      else $("workflowChatClose")?.focus();
    });
    return true;
  }

  function closeWorkflowChatDialog({ restore = true } = {}) {
    if (!state.workflowChat.open) return;
    if ((state.voice.active || state.voice.stopping) && state.voice.target && $("workflowChatBackdrop").contains(state.voice.target)) stopVoiceInput({ cancel: true });
    const wasPractice = state.workflowChat.scope === "practice";
    captureComposerDraft(state.workflowChat.scope);
    if (wasPractice && state.practiceWindow.standalone) {
      // Closing a child window only detaches this view. The backend job is
      // deliberately left running so another window can continue observing it.
      state.practice.viewSerial += 1;
      clearPracticeWindowRefresh();
      state.workflowChat.open = false;
      state.workflowChat.scope = "main";
      state.workflowChat.practiceProjectKey = "";
      state.practice.optimisticMessage = null;
      state.practice.startPending = false;
      $("workflowChatBackdrop").classList.add("hidden");
      $("workflowChatBackdrop").setAttribute("aria-hidden", "true");
      document.body.classList.remove("modal-open");
      requestPracticeWindowClose();
      return;
    }
    if (wasPractice) state.practice.viewSerial += 1;
    if (state.workflowChat.scope === "practice") restoreComposerDraft("main");
    const chat = qs(".chat-column");
    if (chat && chat.parentNode !== $("chatHome")) $("chatHome").prepend(chat);
    state.workflowChat.open = false;
    state.workflowChat.scope = "main";
    state.workflowChat.practiceProjectKey = "";
    state.practice.optimisticMessage = null;
    state.practice.startPending = false;
    $("workflowChatBackdrop").classList.add("hidden");
    $("workflowChatBackdrop").setAttribute("aria-hidden", "true");
    if (restore && state.pageView && state.selectedStep === "jd" && !state.jdImport.busy) {
      $("jdImportBackdrop")?.classList.remove("hidden");
      state.jdImport.open = true;
    }
    if (!state.modelSettings.open && !state.jobEditor.open && !state.newSession.open && !state.clarification.open && !state.jdImport.open) document.body.classList.remove("modal-open");
    const previous = state.workflowChat.previousFocus;
    state.workflowChat.previousFocus = null;
    if (restore) restoreSidebarAwareFocus(previous);
    renderAll();
  }

  function showSharedChat() {
    if (state.voice.active || state.voice.stopping) stopVoiceInput({ cancel: true });
    if (state.workflowChat.open) closeWorkflowChatDialog({ restore: false });
    if (state.jdImport.open) saveJdDraft();
    restoreInspectorPaneHost();
    state.pageView = false;
    const chat = qs(".chat-column");
    if (chat && chat.parentNode !== $("chatHome")) $("chatHome").prepend(chat);
    $("workflowPage").classList.add("hidden");
    $("chatHome").classList.remove("hidden");
    $("composerInput").focus();
  }

  function restoreInspectorPaneHost() {
    const pane = $("inspectPane");
    const home = $("chatHome");
    if (!pane || !home || pane.parentNode === home) return;
    pane.classList.remove("workflow-inspector-overlay");
    home.appendChild(pane);
  }

  const WORKFLOW_ACTION_SUMMARIES = {
    jd: "处理目标岗位 JD，保留不确定字段并等待核对",
    profile: "整理技能档案，保留事实证据与待澄清问题",
    plan: "基于已确认 JD 与技能档案生成差距、计划和候选项目",
    practice: "围绕当前练习项目开始实操并记录真实证据"
  };

  function workflowActionSummary(stepId) {
    return WORKFLOW_ACTION_SUMMARIES[String(stepId || "")] || "继续当前求职步骤";
  }

  function setPendingWorkflowAction(stepId) {
    const summary = workflowActionSummary(stepId);
    state.pendingWorkflowAction = {
      sessionId: String(state.currentSessionId || ""),
      stepId: String(stepId || state.selectedStep || ""),
      summary
    };
    openWorkflowChatDialog();
    toast(`已记录动作：${summary}。请在对话窗口补充材料后发送。`, "success", 4200);
    $("composerInput").focus();
  }

  function sendTrustedWorkflowAction(text, stepId = state.selectedStep, summary = "", actionContext = {}, { openChat = true } = {}) {
    const actionText = String(text || "").trim();
    // A workflow button may be used while the user has an unrelated draft in
    // the composer.  Keep that draft intact and let the normal session guards
    // (approval/queue/running) decide whether the trusted action can run.
    if (!actionText || !canPrepareJobAction()) return false;
    const id = String(state.currentSessionId || "");
    if (!id) {
      toast("请先创建岗位会话，再执行此操作。", "");
      return false;
    }
    const actionSummary = String(summary || actionText).trim();
    const context = actionContext && typeof actionContext === "object" && !Array.isArray(actionContext) ? actionContext : {};
    const body = {
      text: actionText,
      workflow_action: { step: String(stepId || ""), summary: actionSummary, ...context }
    };
    state.pendingWorkflowAction = null;
    const accepted = enqueueAction("message", body, id, { optimisticText: actionSummary });
    if (openChat) openWorkflowChatDialog();
    return accepted;
  }

  function practiceRequestBody(text) {
    return { text: String(text || "").trim() };
  }

  async function sendPracticeMessage(text, { summary = "", projectKey = state.workflowChat.practiceProjectKey || state.practice.projectKey || selectedProjectKey(), attachments = [], viewSerial = state.practice.viewSerial } = {}) {
    const id = String(state.currentSessionId || "");
    const key = String(projectKey || "").trim();
    const message = String(text || "").trim();
    if (!id || !key || !message) return false;
    if (state.practiceWindow.standalone) {
      // Refresh immediately before a child send. This closes the small race
      // where the parent has just submitted a project switch but the child's
      // one-second observer has not received that session snapshot yet.
      const latest = await refreshCurrentSession(id, { scroll: false, clearOptimistic: false });
      if (!latest) return false;
      markPracticeWindowStale();
    }
    if (state.practiceWindow.standalone && (state.practiceWindow.stale || state.practiceWindow.projectKey !== key || !practiceWindowSelectionMatches())) {
      state.practiceWindow.stale = true;
      renderAll();
      toast("主窗口已切换练习项目；请关闭此窗口后从当前项目重新打开实操。", "error", 5200);
      return false;
    }
    if (!canPrepareJobAction()) return false;
    if (!practiceContextMatches(id, key, "", viewSerial)) return false;
    if (!state.practice.threadId || state.practice.projectKey !== key) {
      try {
        await ensurePracticeThread(id, key, viewSerial);
        if (!practiceContextMatches(id, key, "", viewSerial)) return false;
        await refreshPracticeThread(id, state.practice.threadId, viewSerial);
      } catch (error) {
        if (!error || !error.practiceScopeChanged) toast(error.message || "无法打开实操窗口", "error");
        return false;
      }
    }
    if (!practiceContextMatches(id, key, state.practice.threadId, viewSerial)) return false;
    const previousScope = state.workflowChat.scope;
    const previousKey = state.workflowChat.practiceProjectKey;
    state.workflowChat.scope = "practice";
    state.workflowChat.practiceProjectKey = key;
    const body = practiceRequestBody(message);
    const imagePaths = attachments.filter(isSupportedImageAttachment).map((attachment) => attachment.path || attachment.name).filter(Boolean);
    const textPaths = attachments.filter((attachment) => !isSupportedImageAttachment(attachment)).map((attachment) => attachment.path || attachment.name).filter(Boolean);
    if (textPaths.length) body.text = `${body.text}\n\n已上传工作区附件，请使用相对路径读取：${textPaths.join("、")}`;
    if (imagePaths.length) body.attachments = imagePaths;
    const thread = String(state.practice.threadId || "").trim();
    const accepted = await enqueueAction(`practice/${encodeURIComponent(thread)}/message`, body, id, {
      optimisticText: message,
      scope: "practice",
      projectKey: key
    });
    // Keep the practice view selected while its job is running.  If a caller
    // was only preparing a turn, restoring these fields avoids leaking scope
    // into the home chat's next render.
    if (!state.workflowChat.open) {
      state.workflowChat.scope = previousScope;
      state.workflowChat.practiceProjectKey = previousKey;
    }
    return accepted;
  }

  const pageDrafts = new Map();
  const projectFilterDrafts = new Map();
  const projectFilterServerSnapshots = new Map();
  let pageSession = null;
  let profileTab = "text";
  let profileFiles = [];
  let planProjectFiles = [];
  let pageSubmitting = false;

  function projectFilterSource(data = workflowData()) {
    const raw = data && data.project_filter;
    return raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  }

  function dataProjectFilterForSession(sessionId = state.currentSessionId) {
    const id = String(sessionId || "new");
    const source = projectFilterSource(id === String(state.currentSessionId || "") ? workflowData() : {});
    const draft = projectFilterDrafts.get(id) || {};
    const value = (key, fallback = "") => draft[key] !== undefined ? draft[key] : source[key] !== undefined ? source[key] : fallback;
    const minValue = value("min_stars", undefined);
    const minStars = minValue === undefined || minValue === null || String(minValue).trim() === ""
      ? "50"
      : String(minValue).trim();
    const includeValue = value("include", []);
    const excludeValue = value("exclude", []);
    const asText = (raw) => Array.isArray(raw) ? raw.join("、") : String(raw || "");
    const asBool = (raw, fallback) => {
      if (raw === undefined || raw === null || raw === "") return fallback;
      if (typeof raw === "string") return !["false", "0", "no", "off"].includes(raw.trim().toLowerCase());
      return Boolean(raw);
    };
    return {
      minStars,
      include: asText(includeValue),
      exclude: asText(excludeValue),
      other: String(value("other", "") || ""),
      paperProjects: asBool(value("paper_projects", undefined), true),
      rerank: asBool(value("rerank", undefined), true)
    };
  }

  function rememberProjectFilter(sessionId, values = {}) {
    const id = String(sessionId || state.currentSessionId || "new");
    const current = dataProjectFilterForSession(id);
    projectFilterDrafts.set(id, {
      min_stars: String(values.minStars ?? current.minStars ?? "50"),
      include: String(values.include ?? current.include ?? ""),
      exclude: String(values.exclude ?? current.exclude ?? ""),
      other: String(values.other ?? current.other ?? ""),
      paper_projects: values.paperProjects == null ? current.paperProjects : Boolean(values.paperProjects),
      rerank: values.rerank == null ? current.rerank : Boolean(values.rerank)
    });
  }

  function projectFilterActionContext(values = dataProjectFilterForSession()) {
    const rawMinStars = String(values.minStars ?? "").trim();
    const minStars = rawMinStars === "" ? 50 : Number(rawMinStars);
    return {
      min_stars: Number.isFinite(minStars) && minStars >= 0 ? Math.trunc(minStars) : 50,
      include: commaList(values.include),
      exclude: commaList(values.exclude),
      other: String(values.other || "").trim(),
      paper_projects: Boolean(values.paperProjects),
      rerank: Boolean(values.rerank)
    };
  }
  const PAGE_FIELDS = ["profileSource", "profileText", "profilePath", "profileAnswer", "planInclude", "planExclude", "planMinStars", "planOther", "planRerank", "planPaperProjects", "planProjectUrl"];
  function syncPageDrafts() {
    const id = String(state.currentSessionId || "new");
    if (pageSession === id) return;
    if (pageSession !== null) {
      const values = {};
      PAGE_FIELDS.forEach((name) => { values[name] = $(name).type === "checkbox" ? $(name).checked : $(name).value; });
      pageDrafts.set(pageSession, { values, tab: profileTab, files: profileFiles, projectFiles: planProjectFiles.map((item) => ({ ...item })) });
    }
    pageSession = id;
    const draft = pageDrafts.get(id);
    const savedFilter = dataProjectFilterForSession(id);
    PAGE_FIELDS.forEach((name) => {
      const el = $(name);
      const filterValue = {
        planInclude: savedFilter.include,
        planExclude: savedFilter.exclude,
        planMinStars: savedFilter.minStars,
        planOther: savedFilter.other,
        planRerank: savedFilter.rerank,
        planPaperProjects: savedFilter.paperProjects
      }[name];
      const value = draft?.values[name] ?? (filterValue !== undefined ? filterValue : name === "planMinStars" ? "50" : name === "profileSource" ? "self_report" : ["planRerank", "planPaperProjects"].includes(name) ? true : "");
      if (el.type === "checkbox") el.checked = value;
      else el.value = value;
    });
    profileFiles = draft?.files || [];
    planProjectFiles = (draft?.projectFiles || []).map((item) => ({ ...item }));
    $("profileDocuments").value = "";
    setProfileTab(draft?.tab || "text");
    renderProfileFiles();
    renderPlanProjectResources();
    setText($("profileImportStatus"), ""); setText($("planPageStatus"), "");
  }

  function syncPlanFilterControlsFromSession() {
    const id = String(state.currentSessionId || "");
    if (!id || projectFilterDrafts.has(id)) return;
    const saved = dataProjectFilterForSession(id);
    const controls = {
      planInclude: saved.include,
      planExclude: saved.exclude,
      planMinStars: saved.minStars,
      planOther: saved.other,
      planRerank: saved.rerank,
      planPaperProjects: saved.paperProjects
    };
    Object.entries(controls).forEach(([name, value]) => {
      const el = $(name);
      if (!el) return;
      if (el.type === "checkbox") el.checked = Boolean(value);
      else el.value = String(value ?? "");
    });
  }

  function syncProjectFilterServerSnapshot() {
    const id = String(state.currentSessionId || "");
    if (!id || state.loadingSession || !state.currentSession || String(state.currentSession.id || "") !== id) return;
    const signature = JSON.stringify(projectFilterSource(workflowData()));
    const previous = projectFilterServerSnapshots.get(id);
    if (previous !== undefined && previous !== signature && projectFilterDrafts.has(id)) {
      // A main coach discussion or a completed filter action has produced a
      // newer persisted value. Keep that confirmed session value ahead of an
      // old local textarea draft.
      projectFilterDrafts.delete(id);
    }
    projectFilterServerSnapshots.set(id, signature);
  }
  function setProfileTab(tab) {
    profileTab = tab;
    qsa("[data-profile-tab]").forEach((el) => el.setAttribute("aria-selected", String(el.dataset.profileTab === tab)));
    qsa("[data-profile-panel]").forEach((el) => el.classList.toggle("hidden", el.dataset.profilePanel !== tab));
  }
  function renderProfileFiles() { $("profileDocumentList").textContent = profileFiles.map((file) => file.name).join("、"); }
  function projectResourcePath(resource) {
    return String(resource && (resource.path || resource.name || resource.workspace_path || resource.workspacePath) || "").trim();
  }
  function projectResourceSourcePath(resource) {
    return String(resource && (resource.source_path || resource.sourcePath || resource.original_path || resource.originalPath) || "").trim();
  }
  function projectResourceLabel(resource) {
    const path = projectResourcePath(resource);
    const sourcePath = projectResourceSourcePath(resource);
    if (!path) return "未命名材料";
    return sourcePath && sourcePath !== path ? `${path}（来源：${sourcePath}）` : path;
  }
  function validateProjectUrl(value) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    let parsed;
    try { parsed = new URL(raw); } catch (_) { throw new Error("项目 URL 必须是有效的 HTTPS 项目链接。"); }
    if (parsed.protocol !== "https:" || !parsed.hostname || parsed.username || parsed.password || parsed.hash || (parsed.port && parsed.port !== "443")) throw new Error("项目 URL 必须是无凭据的 HTTPS 项目链接。");
    if (parsed.hostname.includes(":")) throw new Error("项目 URL 暂不支持 IPv6 字面量。");
    const credentialKeys = new Set(["token", "access_token", "api_key", "apikey", "key", "auth", "authorization"]);
    for (const key of parsed.searchParams.keys()) {
      if (credentialKeys.has(String(key).trim().toLowerCase().replace(/-/g, "_"))) throw new Error("项目 URL 查询参数不能携带凭据。");
    }
    return parsed.href;
  }
  function renderPlanProjectResources() {
    const list = $("planProjectResourceList");
    if (!list) return;
    list.replaceChildren();
    planProjectFiles.forEach((resource, index) => {
      const row = document.createElement("div");
      row.className = "plan-project-resource";
      const label = document.createElement("span");
      label.textContent = projectResourceLabel(resource);
      row.appendChild(label);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "plan-project-resource-remove";
      remove.textContent = "移除";
      remove.setAttribute("aria-label", `移除复现项目材料 ${projectResourcePath(resource) || index + 1}`);
      remove.addEventListener("click", () => {
        planProjectFiles = planProjectFiles.filter((_item, itemIndex) => itemIndex !== index);
        renderPlanProjectResources();
      });
      row.appendChild(remove);
      list.appendChild(row);
    });
    const url = String($("planProjectUrl")?.value || "").trim();
    if (!planProjectFiles.length && !url) {
      const empty = document.createElement("span");
      empty.className = "plan-project-resource-empty";
      empty.textContent = "尚未添加项目链接或本地材料。";
      list.appendChild(empty);
    }
  }
  function renderWorkflowPage() {
    syncProjectFilterServerSnapshot();
    syncPageDrafts();
    if (!state.pageView) return;
    syncPlanFilterControlsFromSession();
    const step = WORKFLOW_STEPS.find((item) => item.id === state.selectedStep);
    $("workflowPage").classList.remove("hidden"); $("chatHome").classList.add("hidden");
    $("workflowPageTitle").textContent = step.title;
    $("workflowPageHint").textContent = step.hint;
    qsa(".step-page").forEach((page) => page.classList.toggle("hidden", page.id !== `${step.id}Page`));
    const busy = sessionBusy(state.currentSession);
    const queued = sessionHasQueue(state.currentSession);
    $("workflowPageStatus").textContent = state.currentSession?.pending
      ? "当前操作等待确认；请在已打开的对话窗口中核对并批准或拒绝。"
      : queued
        ? `上一轮还有 ${Number(state.currentSession?.queued_calls || 0)} 个工具调用等待恢复；请在对话窗口点击“继续执行工具队列”。`
        : busy
          ? "正在处理；回复、运行记录和待确认操作会显示在对话窗口。"
          : "";
    $("workflowPageStatus").classList.toggle("hidden", !state.currentSession?.pending && !busy && !queued);
    ["profileImportSubmit", "planGenerate", "planProjectRegister", "planProjectUpload", "planProjectLocalImport", "planProjectUrl", "planOther", "planOtherDiscuss", "planPaperProjects", "planRerank"].forEach((id) => { const control = $(id); if (control) control.disabled = Boolean(pageSubmitting || busy || queued || state.currentSession?.pending || state.loadingSession); });
    const practiceOpen = $("practiceStarter");
    // Opening practice is always a read/view action.  A pending main turn or
    // a running queue blocks starting another turn, but must not hide durable
    // practice history or its scoped status window.
    if (practiceOpen) practiceOpen.disabled = Boolean(pageSubmitting || state.loadingSession);
    const results = $(`${step.id}PageResults`);
    results.replaceChildren(); renderJobPane(results);
    qs(".workflow-intro", results)?.remove();
    qs(".project-filter-card", results)?.remove();
    const data = workflowData();
    const missingPlan = !data.jd || !data.profile;
    const missingPractice = !data.plan || !data.selected_project;
    $("planPrerequisite").classList.toggle("hidden", !missingPlan);
    $("planPrerequisite").textContent = "还需要岗位 JD 和技能档案。请先到前两步填写并确认，再生成学习计划与项目。";
    $("practicePrerequisite").classList.toggle("hidden", !missingPractice);
    $("practicePrerequisite").textContent = "还需要学习计划和选定的练习项目。请先到“计划 & 项目”准备好，再开始实操；下方对话仍可继续补充材料或提问。";
    $("planGenerate").disabled ||= missingPlan;
    $("practiceStarter").disabled ||= missingPractice;
    $("profileCopy").disabled = !data.profile || state.loadingSession || state.newSession.busy;
    $("profileManual").disabled = Boolean(state.loadingSession || state.newSession.busy || state.currentSession?.pending || busy || queued);
    if (step.id === "plan") qsa(".job-card", results).forEach((card) => { if (qs("h3", card)?.textContent === "下一步与差距") card.remove(); });
    if (step.id === "profile") qsa(".job-card", results).forEach((card) => { if (qs("h3", card)?.textContent === "已有技能") card.remove(); });
    if (step.id === "profile" && data.profile?.skills?.length) {
      if (data.profile.summary) { const summary = document.createElement("p"); summary.className = "profile-summary"; summary.textContent = data.profile.summary; results.prepend(summary); }
      results.prepend(pageTable("当前档案", ["技能", "熟练程度", "事实证据"], data.profile.skills.map((skill) => [typeof skill === "string" ? skill : skill.name || skill.skill, jobEnumLabel(skill.proficiency || skill.level || skill.status), skill.evidence || skill.proof]), { className: "profile-table", headerLines: { "熟练程度": ["熟练", "程度"] } }));
    }
    if (step.id === "plan" && data.plan) {
      results.prepend(pageTable("差距分析", ["岗位要求", "已有技能", "状态", "说明"], (data.plan.gaps || []).map((gap) => [typeof gap === "string" ? gap : gap.requirement || gap.name || gap.skill, typeof gap === "object" ? gap.matched_skill || gap.matchedSkill || gap.existing_skill || gap.existingSkill || gap.skill_match : "", jobEnumLabel(typeof gap === "object" ? gap.status || gap.priority : ""), typeof gap === "object" ? gap.note || gap.description || gap.reason : ""]), { className: "gap-analysis-table" }));
      const phases = document.createElement("section"); phases.className = "job-card";
      const title = document.createElement("h3"); title.textContent = "分阶段学习计划"; phases.appendChild(title);
      (data.plan.phases || []).forEach((phase) => { const item = document.createElement("div"); item.className = "plan-phase"; const h = document.createElement("h4"); h.textContent = typeof phase === "string" ? phase : `${phase.phase || phase.title || phase.name || "学习阶段"}${phase.duration_weeks ? ` · ${phase.duration_weeks} 周` : ""}`; item.appendChild(h); for (const key of ["goal", "topics", "suggested_practice", "tasks", "checkpoints"]) { if (phase[key]) { const p = document.createElement("p"); p.textContent = `${({goal:"目标",topics:"学习内容",suggested_practice:"建议练习",tasks:"任务",checkpoints:"检查点"})[key]}：${Array.isArray(phase[key]) ? phase[key].map((value) => typeof value === "string" ? value : jobItemText(value, ["title", "name", "description", "text"]) || safeString(value)).join("、") : typeof phase[key] === "string" ? phase[key] : safeString(phase[key])}`; item.appendChild(p); } } phases.appendChild(item); });
      if (data.plan.phases?.length) { const repos = qsa(".job-card", results).find((card) => qs("h3", card)?.textContent.startsWith("候选练习项目")); if (repos) results.insertBefore(phases, repos); else results.appendChild(phases); }
    }
    if (step.id === "jd") {
      if ($("jdImportBackdrop").parentNode !== $("jdPageImport")) $("jdPageImport").appendChild($("jdImportBackdrop"));
      if (!state.workflowChat.open) $("jdImportBackdrop").classList.remove("hidden");
      if (state.jdImport.sessionId !== String(state.currentSessionId || "")) {
        if (state.jdImport.open) saveJdDraft();
        state.jdImport.sessionId = String(state.currentSessionId || "");
        JD_FIELDS.forEach((id) => { $(id).value = jdDraft().fields[id] || (id === "jdPlatform" ? "boss" : ""); });
        setJdMode(jdDraft().mode); renderJdFiles();
      }
      if (!state.workflowChat.open) state.jdImport.open = true;
    }
    const practiceChatOpen = state.workflowChat.open && state.workflowChat.scope === "practice";
    const chatTitle = practiceChatOpen
      ? `Agent 实操 · ${state.practice.projectTitle || projectDisplayTitle(data.selected_project) || "当前项目"}`
      : (step ? `${step.title} · 当前会话` : "求职辅导对话");
    setText($("workflowChatTitle"), chatTitle);
    const chatKicker = qs(".workflow-chat-header .inspect-kicker");
    if (chatKicker) chatKicker.textContent = practiceChatOpen ? "当前练习项目 · 独立记录" : "当前会话";
    $("workflowChatBackdrop")?.setAttribute("aria-hidden", state.workflowChat.open ? "false" : "true");
    enhanceVoiceInputs($("workflowPage"));
  }
  function pageTable(title, headers, rows, options = {}) {
    const card = document.createElement("section"); card.className = `job-card page-table-card${options.className ? ` ${options.className}` : ""}`;
    const heading = document.createElement("h3"); heading.textContent = title; card.appendChild(heading);
    const table = document.createElement("table"); const head = document.createElement("thead"); const row = document.createElement("tr");
    headers.forEach((value) => {
      const cell = document.createElement("th");
      const lines = options.headerLines && options.headerLines[value];
      if (Array.isArray(lines) && lines.length) {
        cell.className = "table-header-lines";
        cell.setAttribute("aria-label", value);
        lines.forEach((line) => { const lineNode = document.createElement("span"); lineNode.textContent = line; cell.appendChild(lineNode); });
      } else cell.textContent = value;
      row.appendChild(cell);
    }); head.appendChild(row); table.appendChild(head);
    const body = document.createElement("tbody"); rows.forEach((values) => { const row = document.createElement("tr"); values.forEach((value) => { const cell = document.createElement("td"); cell.textContent = value == null ? "—" : typeof value === "object" ? safeString(value) : String(value); row.appendChild(cell); }); body.appendChild(row); }); table.appendChild(body); card.appendChild(table); return card;
  }
  async function sendPageAction(text, status, attachments = [], sessionId = state.currentSessionId, actionContext = null) {
    if (state.loadingSession || state.currentSession?.pending || sessionTurnBlocked(state.currentSession)) throw new Error("请先完成当前操作或点击“继续执行工具队列”。");
    if (sessionId && String(sessionId) !== String(state.currentSessionId)) throw new Error("会话已切换，输入已保留，请在原会话重试。");
    if (!state.currentSessionId) await createSession();
    const id = String(state.currentSessionId);
    const imagePaths = attachments.filter(isSupportedImageAttachment).map((item) => item.path || item.name).filter(Boolean);
    const textPaths = attachments.filter((item) => !isSupportedImageAttachment(item)).map((item) => item.path || item.name).filter(Boolean);
    const composed = textPaths.length ? `${text}\n\n已上传工作区附件，请使用 read_document 或文件读取工具读取相对路径：${textPaths.join("、")}` : text;
    const body = { text: composed }; if (imagePaths.length) body.attachments = imagePaths;
    if (actionContext && typeof actionContext === "object") body.workflow_action = actionContext;
    const accepted = await enqueueAction("message", body, id, { optimisticText: composed });
    if (!accepted) throw new Error("发送失败，输入已保留。");
    if (String(state.currentSessionId) !== id) throw new Error("会话已切换，输入已保留，请在原会话重试。");
    openWorkflowChatDialog();
    status.textContent = "已提交；对话窗口已打开，回复和待确认操作会同步到本页。";
  }
  async function submitProfilePage(event) {
    event.preventDefault(); if (pageSubmitting) return;
    let id = state.currentSessionId;
    const tab = profileTab, files = [...profileFiles];
    const source = $("profileSource").value;
    const materialText = $("profileText").value, materialPath = $("profilePath").value.trim(), materialAnswer = $("profileAnswer").value.trim();
    let text = "", attachments = [];
    const status = $("profileImportStatus");
    pageSubmitting = true; $("profileImportSubmit").disabled = true;
    try {
      if (state.loadingSession || sessionTurnBlocked(state.currentSession) || state.currentSession?.pending) throw new Error("请先完成当前操作或点击“继续执行工具队列”。");
      if (tab === "text" && !materialText.trim()) throw new Error("请填写技能材料。");
      if (tab === "documents" && !files.length) throw new Error("请先选择文档。");
      if (tab === "obsidian" && !materialPath) throw new Error("请填写本地路径。");
      if (!id) {
        id = await createSession();
        if (!id) throw new Error("无法创建岗位会话，请重试。");
        if (String(state.currentSessionId) !== String(id)) throw new Error("会话已切换，输入已保留，请在原会话重试。");
        $("profileText").value = materialText; $("profilePath").value = materialPath; $("profileAnswer").value = materialAnswer; $("profileSource").value = source; profileFiles = files; setProfileTab(tab); renderProfileFiles();
      }
      if (tab === "text") text = materialText;
      if (tab === "documents") { if (!files.length) throw new Error("请先选择文档。"); for (const file of files) attachments.push(await uploadOne(file)); text = "请读取本条消息附带的技能材料。"; }
      if (tab === "obsidian") {
        const path = materialPath; if (!path) throw new Error("请填写本地路径。");
        const payload = await request("/imports/local", { method: "POST", body: { path } });
        attachments = (payload.imported || []).map((item) => ({ path: typeof item === "string" ? item : item.path, name: typeof item === "string" ? item : item.path, mediaType: item.media_type || item.mediaType || "" })).filter((item) => item.path);
        if (!attachments.length) throw new Error("没有导入可读取的材料，请检查路径和文件类型。"); text = "请读取本条消息附带的本地笔记 / Obsidian 材料。";
      }
      if (tab === "interview") text = materialAnswer || "请开始技能访谈，每次只问一个问题，了解我的项目经历、技能熟练度和事实证据。";
      await sendPageAction(`本次只处理技能档案阶段。材料来源：${source}。请整理并保存技能、熟练程度和事实证据；缺乏证据的内容列入待澄清问题，不要推测，不要自动生成计划。\n${text}`, status, attachments, id);
    } catch (error) { status.textContent = error.message; }
    finally { pageSubmitting = false; renderWorkflowPage(); }
  }
  function discussPlanOther() {
    if (!canPrepareJobAction()) return;
    const id = String(state.currentSessionId || "");
    const values = {
      minStars: String($("planMinStars")?.value || "").trim(),
      include: String($("planInclude")?.value || "").trim(),
      exclude: String($("planExclude")?.value || "").trim(),
      other: String($("planOther")?.value || "").trim(),
      paperProjects: Boolean($("planPaperProjects")?.checked),
      rerank: Boolean($("planRerank")?.checked)
    };
    rememberProjectFilter(id, values);
    const prompt = values.other
      ? `我想和你讨论并细化项目筛选要求。当前“其他要求”是：${values.other}。请结合岗位差距和已有项目候选，指出需要澄清的取舍；确认后调用 set_project_filter，把最终筛选偏好保存到当前会话，后续搜索和计划都遵守。`
      : "我想和你讨论项目筛选要求。请结合岗位差距和已有项目候选，询问我最需要补充的限制或偏好；确认后调用 set_project_filter，把最终筛选偏好保存到当前会话，后续搜索和计划都遵守。";
    const summary = "讨论并确认项目筛选要求";
    Promise.resolve(sendTrustedWorkflowAction(prompt, "plan", summary, { project_filter: projectFilterActionContext(values) })).then((accepted) => {
      if (accepted && String(state.currentSessionId || "") === id) projectFilterDrafts.delete(id);
    });
  }
  async function submitPlanPage(event) {
    event.preventDefault(); if (pageSubmitting) return;
    const data = workflowData(); const status = $("planPageStatus");
    if (!data.jd || !data.profile) { status.textContent = "请先填写并确认岗位 JD 和技能档案。"; return; }
    const stars = Number($("planMinStars").value);
    if (!Number.isInteger(stars) || stars < 0) { status.textContent = "最低 star 数应为非负整数。"; return; }
    const includeKeywords = String($("planInclude").value || "").trim();
    const excludeKeywords = String($("planExclude").value || "").trim();
    const otherRequirements = String($("planOther").value || "").trim();
    const rerankProjects = Boolean($("planRerank").checked);
    const paperProjects = Boolean($("planPaperProjects").checked);
    let projectUrl = String($("planProjectUrl").value || "").trim();
    if (projectUrl) {
      try { projectUrl = validateProjectUrl(projectUrl); }
      catch (error) { status.textContent = error.message; return; }
    }
    const projectFiles = planProjectFiles.map((resource) => ({ ...resource }));
    const projectResources = projectFiles.map((resource) => projectResourcePath(resource)).filter(Boolean);
    const projectResourceLabels = projectFiles.map((resource) => projectResourceLabel(resource)).filter(Boolean);
    const projectSourceText = projectUrl || projectResources.length
      ? `另外，请评估我提供的自有复现项目。项目 URL：${projectUrl || "未提供"}；项目材料工作区路径：${projectResources.length ? projectResources.join("、") : "未提供"}；材料来源标记：${projectResourceLabels.length ? projectResourceLabels.join("、") : "未提供"}。请先读取 URL 或这些材料，再用 register_project 把它登记为候选，保留 source.kind（user_url、upload 或 local_import）、source.evidence、source.url 和 source.paths 等真实来源字段；不要自动 select_project 或执行代码。只能把 URL 或材料中明确可核验的内容作为事实；不要把我的项目影响力、结果或完成状态写成未经证明的事实。`
      : "没有自有复现项目材料；请只搜索并展示外部候选。";
    const paperSearchText = paperProjects
      ? "调用 search_projects 时启用 include_paper_implementations=true，优先寻找与当前岗位差距相关、同时有可核验论文和开源仓库证据的高影响力项目；先读取 README、引用文件和公开论文来源，再只使用真实论文/DOI、仓库链接以及可核验的公开引用或 star 等指标，并把论文依据保留为 paper_evidence。无法核验时明确标注未知，绝不编造影响力、引用数、star 数或项目结果。"
      : "调用 search_projects 时使用 include_paper_implementations=false；不额外优先论文或高影响力开源项目，但所有候选仍必须保留真实仓库链接并明确可核验依据，不能编造影响力或项目结果。";
    pageSubmitting = true;
    try {
      let id = String(state.currentSessionId || "");
      if (!id) {
        id = await createSession();
        if (!id) throw new Error("无法创建岗位会话，请重试。");
        if (String(state.currentSessionId) !== id) throw new Error("会话已切换，输入已保留，请在原会话重试。");
        planProjectFiles = projectFiles;
        $("planInclude").value = includeKeywords;
        $("planExclude").value = excludeKeywords;
        $("planMinStars").value = String(stars);
        $("planOther").value = otherRequirements;
        $("planRerank").checked = rerankProjects;
        $("planPaperProjects").checked = paperProjects;
        $("planProjectUrl").value = projectUrl;
        renderPlanProjectResources();
      }
      rememberProjectFilter(id, { minStars: String(stars), include: includeKeywords, exclude: excludeKeywords, other: otherRequirements, paperProjects, rerank: rerankProjects });
      await sendPageAction(`请基于当前已确认 JD 与技能档案生成差距分析、分阶段学习计划（含任务、检查点），并搜索真实 GitHub 复现项目。包含关键词：${includeKeywords || "不限"}；排除关键词：${excludeKeywords || "无"}；最低 star 数：${stars}；其他要求：${otherRequirements || "无"}。${rerankProjects ? "由你比较候选项目与我的差距并说明排序理由。" : "按搜索匹配度展示候选项目。"}${paperSearchText}\n${projectSourceText}\n保存本次项目筛选偏好（尤其是其他要求），后续重新搜索和计划调整继续遵守；等待我选择项目，不要自动开始实操。`, status, projectFiles, id, { step: "plan", summary: "生成差距分析、学习计划和项目候选", project_filter: projectFilterActionContext({ minStars: String(stars), include: includeKeywords, exclude: excludeKeywords, other: otherRequirements, paperProjects, rerank: rerankProjects }) });
      projectFilterDrafts.delete(id);
    }
    catch (error) { status.textContent = error.message; }
    finally { pageSubmitting = false; renderWorkflowPage(); }
  }

  async function submitOwnProjectPage() {
    if (pageSubmitting) return;
    const status = $("planPageStatus");
    let projectUrl = String($("planProjectUrl").value || "").trim();
    const projectFiles = planProjectFiles.map((resource) => ({ ...resource }));
    const projectResources = projectFiles.map((resource) => projectResourcePath(resource)).filter(Boolean);
    if (!projectUrl && !projectResources.length) {
      status.textContent = "请先提供项目 URL、上传文件或导入本地材料。";
      return;
    }
    if (projectUrl) {
      try { projectUrl = validateProjectUrl(projectUrl); }
      catch (error) { status.textContent = error.message; return; }
    }
    const sourceKind = projectUrl ? "使用 user_url；若同时有文件，source.paths 也必须保留这些材料" : "按每条材料的真实来源使用 upload 或 local_import";
    const sourcePaths = projectResources.length ? projectFiles.map((resource) => projectResourceLabel(resource)).filter(Boolean).join("、") : "未提供";
    pageSubmitting = true;
    try {
      if (state.loadingSession || sessionTurnBlocked(state.currentSession) || state.currentSession?.pending) throw new Error("请先完成当前操作或点击“继续执行工具队列”。");
      let id = String(state.currentSessionId || "");
      if (!id) {
        id = await createSession();
        if (!id) throw new Error("无法创建岗位会话，请重试。");
        if (String(state.currentSessionId) !== id) throw new Error("会话已切换，项目材料已保留，请在目标会话重试。");
        // createSession renders a fresh draft. Restore the captured source so
        // a successful registration never clears the user's project entry.
        planProjectFiles = projectFiles;
        $("planProjectUrl").value = projectUrl;
        renderPlanProjectResources();
      }
      await sendPageAction(`请只评估我提供的自有复现项目并把它加入当前候选项目。项目 URL：${projectUrl || "未提供"}；项目材料工作区路径：${sourcePaths}。请先读取 URL 或这些材料，再调用 register_project 登记候选；source.kind 请按真实来源使用 ${sourceKind}，并保留 source.evidence、source.url、source.paths。若 URL 与材料同时存在，source.evidence 要分别说明，不能丢失任一来源。若岗位 JD、技能档案或学习计划尚未准备好，只说明暂缺比较依据，仍先读取并登记候选；已有计划和外部候选保持原样，不要触发外部项目检索或生成、重算学习计划。不要 select_project，不要执行代码。只能使用 URL 或材料中可核验的项目名称、代码、论文和结果；影响力、星标、论文关联或完成状态无法核验时标为待核实，不要补写。`, status, projectFiles, id);
    } catch (error) {
      status.textContent = error.message;
    } finally {
      pageSubmitting = false;
      renderWorkflowPage();
    }
  }

  function draftWorkflowPrompt(text) {
    // This button is a material-entry affordance. Keep the user's composer
    // untouched and carry only a short, structured action context into the
    // next message they explicitly send.
    if (!canPrepareJobAction()) return;
    setPendingWorkflowAction(state.selectedStep);
  }

  function renderStepIntro(root, data) {
    const step = WORKFLOW_STEPS.find((item) => item.id === state.selectedStep);
    const card = document.createElement("section"); card.className = "workflow-intro";
    const h = document.createElement("h3"); h.textContent = step.title;
    const hint = document.createElement("p"); hint.textContent = step.hint;
    card.append(h, hint);
    let missing = "";
    if (step.id === "plan" && (!data.jd || !data.profile)) missing = "还需要岗位 JD 和技能档案。可以先切回前两步补充，再生成计划。";
    if (step.id === "practice" && (!data.plan || !data.selected_project)) missing = "还需要学习计划和选定的练习项目。请在“计划 & 项目”中准备好，再开始实操。";
    if (missing) { const note = document.createElement("p"); note.className = "workflow-prerequisite"; note.textContent = missing; card.appendChild(note); }
    const actions = document.createElement("div"); actions.className = "workflow-actions";
    if (["jd", "profile"].includes(step.id)) {
      const edit = document.createElement("button"); edit.type = "button"; edit.className = "action-button secondary";
      edit.textContent = step.id === "jd" ? "填写 / 编辑 JD" : "填写 / 编辑档案";
      edit.addEventListener("click", async () => {
        if (state.loadingSession || state.newSession.busy) return;
        if (!state.currentSessionId) { edit.disabled = true; await createSession(); }
        if (state.currentSessionId) openJobEditor(step.id, data[step.id] || {});
      }); actions.appendChild(edit);
    }
    const draft = document.createElement("button"); draft.type = "button"; draft.className = "action-button secondary";
    draft.textContent = "准备对话引导"; draft.disabled = Boolean(missing); draft.addEventListener("click", () => draftWorkflowPrompt(step.prompt)); actions.appendChild(draft);
    const index = WORKFLOW_STEPS.indexOf(step);
    if (index < WORKFLOW_STEPS.length - 1) {
      const next = document.createElement("button"); next.type = "button"; next.className = "job-card-action";
      next.textContent = `下一步：${WORKFLOW_STEPS[index + 1].title.slice(2)}`;
      next.addEventListener("click", () => selectWorkflowStep(WORKFLOW_STEPS[index + 1].id)); actions.appendChild(next);
    }
    if (step.id === "jd") { const imports = document.createElement("button"); imports.type = "button"; imports.className = "action-button primary"; imports.textContent = "导入 / 搜索岗位 JD"; imports.addEventListener("click", openJdImport); actions.prepend(imports); }
    card.appendChild(actions);
    const note = document.createElement("small"); note.textContent = "准备对话引导会记录动作上下文；请在消息框补充材料后发送。"; card.appendChild(note);
    root.appendChild(card);
  }

  function renderInspector() {
    renderWorkflow();
    qsa(".inspect-tab").forEach((button) => {
      const active = button.dataset.tab === state.selectedTab;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
    const content = $("inspectContent");
    content.replaceChildren();
    if (state.selectedTab === "files") renderFilesPane(content);
    else renderJobPane(content);
  }

  function renderFilesPane(root) {
    const title = document.createElement("div"); title.className = "inspect-section-title";
    const titleText = document.createElement("span"); titleText.textContent = "工作区文件"; title.appendChild(titleText);
    const importButton = document.createElement("button"); importButton.type = "button"; importButton.className = "inspect-inline-action"; importButton.textContent = "导入本地路径"; importButton.addEventListener("click", openLocalImport); title.appendChild(importButton);
    root.appendChild(title);
    const crumb = document.createElement("div"); crumb.className = "path-crumb";
    const pathButton = document.createElement("button"); pathButton.type = "button"; pathButton.textContent = state.filePath || "."; pathButton.title = state.filePath || "."; pathButton.addEventListener("click", () => loadFiles(".")); crumb.appendChild(pathButton); root.appendChild(crumb);
    if (state.fileLoading) { const loading = document.createElement("div"); loading.className = "file-loading"; const spin = document.createElement("span"); spin.className = "spinner"; loading.append(spin, document.createTextNode("读取目录…")); root.appendChild(loading); return; }
    if (state.fileError) { const error = document.createElement("div"); error.className = "file-error"; error.textContent = state.fileError; root.appendChild(error); return; }
    if (!state.fileEntries.length) { const empty = document.createElement("div"); empty.className = "inspect-empty"; empty.textContent = "这个目录暂时没有可展示的文件。"; root.appendChild(empty); return; }
    const list = document.createElement("div"); list.className = "file-list";
    const entries = [...state.fileEntries].sort((a, b) => (a.type === b.type ? String(a.name).localeCompare(String(b.name), "zh") : a.type === "directory" ? -1 : 1));
    if (state.filePath && state.filePath !== ".") {
      const parent = document.createElement("button"); parent.type = "button"; parent.className = "file-entry directory"; parent.appendChild(icon("folder-open", "file-entry-icon")); const info = document.createElement("span"); info.className = "file-entry-info"; const name = document.createElement("span"); name.className = "file-entry-name"; name.textContent = ".. 返回上级"; info.appendChild(name); parent.appendChild(info); parent.addEventListener("click", () => loadFiles(parentPath(state.filePath))); list.appendChild(parent);
    }
    entries.forEach((entry) => {
      const button = document.createElement("button"); button.type = "button"; button.className = `file-entry ${entry.type === "directory" ? "directory" : "file"}`;
      button.appendChild(icon(entry.type === "directory" ? "folder" : "file", "file-entry-icon"));
      const info = document.createElement("span"); info.className = "file-entry-info";
      const name = document.createElement("span"); name.className = "file-entry-name"; name.textContent = String(entry.name || entry.path || "未命名"); info.appendChild(name);
      const meta = document.createElement("span"); meta.className = "file-entry-meta"; meta.textContent = entry.type === "directory" ? "目录" : formatBytes(entry.size); info.appendChild(meta); button.appendChild(info);
      button.addEventListener("click", () => entry.type === "directory" ? loadFiles(String(entry.path || joinPath(state.filePath, entry.name))) : openFile(String(entry.path || joinPath(state.filePath, entry.name)), String(entry.name || "文件")));
      list.appendChild(button);
    });
    root.appendChild(list); hydrateIcons(root);
  }

  function projectIdentity(project) {
    if (!project || typeof project !== "object") return "";
    return String(project.project_key || project.projectKey || project.id || project.full_name || project.fullName || project.repository || project.name || project.html_url || project.url || "").trim();
  }

  function projectSourceMeta(project) {
    if (!project || typeof project !== "object") return {};
    const source = project.source || project.source_meta || project.sourceMeta;
    return source && typeof source === "object" && !Array.isArray(source) ? source : {};
  }

  function projectSourceKind(project) {
    const source = projectSourceMeta(project);
    return String(project && (project.source_kind || project.sourceKind) || source.kind || "").trim().toLowerCase().replace(/[\s-]+/g, "_");
  }

  function projectUrl(project) {
    if (!project || typeof project !== "object") return "";
    const source = projectSourceMeta(project);
    const values = [project.html_url, project.repository_url, project.repo_url, project.url, source.url, project.source_url];
    for (const raw of values) {
      const value = String(raw || "").trim();
      if (!value) continue;
      try {
        const parsed = new URL(value);
        if (["https:", "http:"].includes(parsed.protocol) && parsed.hostname) return parsed.href;
      } catch (_) { /* candidate URLs are untrusted display data */ }
    }
    return "";
  }

  function projectPathHints(project) {
    const source = projectSourceMeta(project);
    const values = [];
    for (const raw of [project.source_paths, project.sourcePaths, project.paths, source.paths, project.workspace_paths, project.workspacePaths]) {
      if (Array.isArray(raw)) values.push(...raw);
      else if (raw) values.push(raw);
    }
    return values.map((value) => String(value || "").trim()).filter(Boolean);
  }

  function stableProjectLabel(value) {
    const text = String(value || "").trim();
    return text && !/^(?:user|local):[a-z0-9_-]{8,}$/i.test(text) ? text : "";
  }

  function projectDisplayTitle(project) {
    if (!project || typeof project !== "object") return "未命名项目";
    for (const raw of [project.display_name, project.displayName, project.title, project.full_name, project.fullName, project.name, project.repository]) {
      const value = stableProjectLabel(raw);
      if (value) return value;
    }
    const url = projectUrl(project);
    if (url) {
      try {
        const parsed = new URL(url);
        const parts = parsed.pathname.split("/").filter(Boolean);
        if (parts.length) return `自有复现项目 · ${parts[parts.length - 1]}`;
      } catch (_) { /* fall through to source paths */ }
    }
    const paths = projectPathHints(project);
    if (paths.length) {
      const raw = paths[0].replace(/[\\/]+$/, "");
      const parts = raw.split(/[\\/]/).filter(Boolean);
      if (parts.length) return `自有复现项目 · ${parts[parts.length - 1]}`;
    }
    return projectSourceKind(project) ? "我的复现项目" : "未命名项目";
  }

  function projectIsLocalOnly(project) {
    const kind = projectSourceKind(project);
    if (["upload", "local_import", "local_path"].includes(kind)) return true;
    return !projectUrl(project) && projectPathHints(project).length > 0;
  }

  function projectSourceLabel(project) {
    const kind = projectSourceKind(project);
    if (["local_import", "local_path", "local"].includes(kind)) return "本地导入";
    if (["upload", "imported_material"].includes(kind)) return "上传材料";
    if (["user_url", "url", "github", "paper_implementation"].includes(kind)) return "项目链接";
    return "";
  }

  function projectStars(project) {
    if (projectIsLocalOnly(project) || project && project.user_provided === true) return null;
    const raw = project && (project.stargazers_count ?? project.stars ?? project.star_count);
    if (raw === null || raw === undefined || String(raw).trim() === "") return null;
    const value = Number(raw);
    return Number.isFinite(value) && value >= 0 ? value : null;
  }

  function projectHistoryEntries(data) {
    const current = data && data.selected_project && typeof data.selected_project === "object" ? data.selected_project : null;
    const currentId = projectIdentity(current);
    const raw = data && (data.project_history || data.projectHistory || data.projects_history);
    const entries = [];
    if (Array.isArray(raw)) {
      raw.forEach((item) => { if (item && typeof item === "object") entries.push(item); });
    } else if (raw && typeof raw === "object") {
      Object.entries(raw).forEach(([key, value]) => {
        if (!value || typeof value !== "object") return;
        entries.push({ ...value, project: value.project || value.repository || { full_name: key } });
      });
    }
    if (current && currentId && !entries.some((item) => projectIdentity(item.project || item.selected_project || item.repo || item) === currentId)) {
      entries.unshift({ project: current, progress: Array.isArray(data.progress) ? data.progress : [], is_current: true });
    }
    return entries.map((item) => {
      const project = item.project || item.selected_project || item.repo || item.repository || item;
      const progressFromEntry = Array.isArray(item.progress)
        ? item.progress
        : Array.isArray(item.progress_history) ? item.progress_history : [];
      const identity = projectIdentity(project);
      const currentByShape = item.is_current === true || item.current === true || (currentId && identity === currentId);
      const progress = currentByShape && !progressFromEntry.length && currentId === identity && Array.isArray(data.progress) ? data.progress : progressFromEntry;
      return { raw: item, project: project && typeof project === "object" ? project : {}, progress, identity, isCurrent: currentByShape, updatedAt: item.updated_at || item.updatedAt || item.at || "" };
    }).filter((item) => item.identity);
  }

  function progressHasEvidence(entry) {
    if (!entry || typeof entry !== "object") return false;
    const evidence = entry.evidence ?? entry.proof;
    if (Array.isArray(evidence)) return evidence.length > 0;
    if (evidence && typeof evidence === "object") return Object.keys(evidence).length > 0;
    return Boolean(String(evidence || "").trim());
  }

  function progressIsCompleted(entry) {
    const status = String(entry && entry.status || "").trim().toLowerCase().replace(/[\s-]+/g, "_");
    return ["completed", "complete", "done", "finished"].includes(status)
      && entry.user_confirmed === true
      && progressHasEvidence(entry);
  }

  function projectProgressStatus(entry) {
    const explicit = String(entry && (entry.status || entry.state) || "").trim().toLowerCase().replace(/[\s-]+/g, "_");
    if (progressIsCompleted(entry)) return "已完成";
    if (["blocked", "failed"].includes(explicit)) return jobEnumLabel(explicit);
    if (entry) return "进行中";
    return "待开始";
  }

  function projectHistoryStatus(item) {
    const rawStatus = String(item && item.raw && (item.raw.status || item.raw.state) || "").trim().toLowerCase().replace(/[\s-]+/g, "_");
    const latest = item && item.progress && item.progress.length ? item.progress[item.progress.length - 1] : null;
    if (item && item.progress.some(progressIsCompleted)) return "已完成";
    if (["blocked", "failed"].includes(rawStatus)) return jobEnumLabel(rawStatus);
    if (latest) return projectProgressStatus(latest);
    if (["not_started", "planned", "todo", "pending"].includes(rawStatus)) return "待开始";
    if (["in_progress", "active", "working"].includes(rawStatus)) return "进行中";
    return "待开始";
  }

  function projectProgressSummary(item) {
    if (!item || !item.progress.length) return "暂无实操记录";
    const latest = item.progress[item.progress.length - 1];
    const note = String(latest && (latest.note || latest.summary || latest.text) || "").trim();
    return `${item.progress.length} 条记录${note ? ` · ${note}` : ""}`;
  }

  function requestProjectSwitch(project) {
    const id = projectIdentity(project);
    if (!id || !state.currentSessionId) return;
    const name = projectDisplayTitle(project);
    const summary = `切换练习项目：${name}`;
    const text = `${summary}。候选 project_key=${id}。请调用 select_project，并使用这个 project_key 恢复已有进度，返回当前项目状态。`;
    selectWorkflowStep("practice");
    sendTrustedWorkflowAction(text, "practice", summary, { project_key: id }, { openChat: false });
  }

  function renderProjectHistory(root, data) {
    const entries = projectHistoryEntries(data);
    if (!entries.length) return;
    const card = document.createElement("section");
    card.className = "job-card project-history-card";
    const head = document.createElement("div");
    head.className = "job-card-head";
    const title = document.createElement("h3");
    title.textContent = "练习项目记录";
    head.appendChild(title);
    const hint = document.createElement("span");
    hint.className = "project-history-hint";
    hint.textContent = "每次选择一个练习项目，可逐个完成多个项目";
    head.appendChild(hint);
    card.appendChild(head);
    const list = document.createElement("div");
    list.className = "project-history-list";
    entries.forEach((item) => {
      const row = document.createElement("div");
      row.className = `project-history-row${item.isCurrent ? " current" : ""}`;
      const body = document.createElement("div");
      body.className = "project-history-body";
      const name = document.createElement("strong");
      name.className = "project-history-name";
      name.textContent = projectDisplayTitle(item.project);
      body.appendChild(name);
      const meta = document.createElement("span");
      meta.className = "project-history-meta";
      const language = item.project.language ? String(item.project.language) : projectSourceLabel(item.project);
      meta.textContent = [language, projectHistoryStatus(item), projectProgressSummary(item)].filter(Boolean).join(" · ");
      body.appendChild(meta);
      row.appendChild(body);
      const action = document.createElement("button");
      action.type = "button";
      action.className = item.isCurrent ? "project-history-current" : "job-card-action";
      action.textContent = item.isCurrent ? "当前" : "切换并继续";
      action.disabled = item.isCurrent;
       action.setAttribute("aria-label", item.isCurrent ? `当前项目 ${name.textContent}` : `切换到项目 ${name.textContent}`);
      if (!item.isCurrent) action.addEventListener("click", () => requestProjectSwitch(item.project));
      row.appendChild(action);
      list.appendChild(row);
    });
    card.appendChild(list);
    root.appendChild(card);
  }

  function jobEditField(label, field, value, { multiline = false, hint = "" } = {}) {
    const wrapper = document.createElement("div");
    wrapper.className = "job-edit-field";
    const labelNode = document.createElement("label");
    labelNode.textContent = label;
    labelNode.setAttribute("for", `jobEdit-${field}`);
    wrapper.appendChild(labelNode);
    const input = document.createElement(multiline ? "textarea" : "input");
    input.id = `jobEdit-${field}`;
    input.dataset.jobEditField = field;
    input.value = value == null ? "" : String(value);
    if (multiline) input.rows = field === "skills" ? 7 : 4;
    wrapper.appendChild(input);
    if (hint) {
      const hintNode = document.createElement("span");
      hintNode.className = "job-edit-field-hint";
      hintNode.textContent = hint;
      wrapper.appendChild(hintNode);
    }
    return wrapper;
  }

  function editableList(value) {
    return Array.isArray(value) ? value.map((item) => {
      if (typeof item === "string") return item;
      return jobItemText(item, ["requirement", "name", "skill", "tag", "text", "title", "description"]);
    }).filter(Boolean).join("\n") : "";
  }

  function editableListEditor(label, field, value, { hint = "每行一项", placeholder = "请输入内容", multiline = false } = {}) {
    const wrapper = document.createElement("div");
    wrapper.className = "job-edit-field job-edit-list-field";
    wrapper.dataset.jobEditList = field;
    wrapper.setAttribute("data-job-edit-list", field);
    const labelNode = document.createElement("label");
    labelNode.textContent = label;
    wrapper.appendChild(labelNode);

    const list = document.createElement("div");
    list.className = "job-edit-list";
    const values = Array.isArray(value) ? value.map((item) => {
      if (typeof item === "string" || typeof item === "number") return String(item);
      return jobItemText(item, ["requirement", "name", "skill", "tag", "text", "title", "description", "responsibility"]);
    }).map((item) => String(item || "").trim()).filter(Boolean) : [];
    const appendRow = (item = "", focus = false) => {
      const row = document.createElement("div");
      row.className = "job-edit-list-row";
      row.dataset.jobEditListRow = field;
      row.setAttribute("data-job-edit-list-row", field);
      const input = document.createElement(multiline ? "textarea" : "input");
      if (!multiline) input.type = "text";
      input.className = "job-edit-list-input";
      input.dataset.jobEditListInput = field;
      input.setAttribute("data-job-edit-list-input", field);
      input.value = item == null ? "" : String(item);
      input.placeholder = placeholder;
      if (multiline) input.rows = 2;
      input.setAttribute("aria-label", `${label}条目`);
      row.appendChild(input);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "job-edit-list-remove";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `移除${label}这一行`);
      remove.addEventListener("click", () => row.remove());
      row.appendChild(remove);
      list.appendChild(row);
      if (focus && typeof input.focus === "function") input.focus();
      return row;
    };
    values.forEach((item) => appendRow(item));
    if (!values.length) appendRow();
    wrapper.appendChild(list);

    const add = document.createElement("button");
    add.type = "button";
    add.className = "inspect-inline-action job-edit-list-add";
    add.textContent = "添加一项";
    add.addEventListener("click", () => appendRow("", true));
    wrapper.appendChild(add);
    if (hint) {
      const hintNode = document.createElement("span");
      hintNode.className = "job-edit-field-hint";
      hintNode.textContent = hint;
      wrapper.appendChild(hintNode);
    }
    return wrapper;
  }

  function readEditableList(field) {
    const fields = $("jobEditFields");
    return qsa(`[data-job-edit-list-row="${field}"]`, fields).map((row) => {
      const input = qs(`[data-job-edit-list-input="${field}"]`, row);
      return String(input && input.value || "").trim();
    }).filter(Boolean);
  }

  const PROFILE_PROFICIENCIES = [
    ["aware", "了解"],
    ["working", "能上手"],
    ["proficient", "熟练"],
    ["expert", "精通"]
  ];
  const PROFILE_EVIDENCE_SOURCES = new Set(["resume", "self_report", "chat", "work_log", "obsidian", "local_path"]);

  function normalizeProfileSkill(item) {
    if (typeof item === "string") return { name: item, proficiency: "working", confidence: 0.5, sources: [], evidence: "" };
    const value = item && typeof item === "object" ? item : {};
    const proficiency = PROFILE_PROFICIENCIES.some(([key]) => key === value.proficiency) ? value.proficiency : "working";
    const confidence = Number(value.confidence);
    return {
      ...value,
      name: String(value.name || value.skill || value.title || ""),
      proficiency,
      confidence: Number.isFinite(confidence) ? confidence : 0.5,
      sources: Array.isArray(value.sources) ? value.sources.slice() : [],
      evidence: String(value.evidence || value.proof || "")
    };
  }

  function profileSkillRow(skill, index = null) {
    const value = normalizeProfileSkill(skill);
    const row = document.createElement("div");
    row.className = "profile-skill-row";
    row.dataset.profileSkillRow = "true";
    if (index != null) row.dataset.skillIndex = String(index);

    const name = document.createElement("input");
    name.type = "text";
    name.className = "profile-skill-name";
    name.value = value.name;
    name.placeholder = "技能名称";
    name.setAttribute("aria-label", "技能名称");
    row.appendChild(name);

    const level = document.createElement("select");
    level.className = "profile-skill-proficiency";
    level.setAttribute("aria-label", "技能程度");
    PROFILE_PROFICIENCIES.forEach(([key, label]) => {
      const option = document.createElement("option");
      option.value = key;
      option.textContent = label;
      option.selected = value.proficiency === key;
      level.appendChild(option);
    });
    row.appendChild(level);

    const evidence = document.createElement("textarea");
    evidence.className = "profile-skill-evidence";
    evidence.rows = 2;
    evidence.value = value.evidence;
    evidence.placeholder = "事实证据：项目、日志、材料路径或具体结果";
    evidence.setAttribute("aria-label", "事实证据");
    row.appendChild(evidence);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "profile-skill-remove";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `移除技能 ${value.name || "这一行"}`);
    remove.addEventListener("click", () => row.remove());
    row.appendChild(remove);
    return row;
  }

  function profileSkillsEditor(skills) {
    const wrapper = document.createElement("div");
    wrapper.className = "job-edit-field profile-skills-field";
    const label = document.createElement("label");
    label.textContent = "技能与事实证据";
    wrapper.appendChild(label);
    const header = document.createElement("div");
    header.className = "profile-skill-columns";
    ["技能名称", "程度", "事实证据", ""].forEach((text) => {
      const cell = document.createElement("span");
      cell.textContent = text;
      header.appendChild(cell);
    });
    wrapper.appendChild(header);
    const list = document.createElement("div");
    list.className = "profile-skill-list";
    const values = Array.isArray(skills) ? skills : [];
    values.forEach((skill, index) => list.appendChild(profileSkillRow(skill, index)));
    if (!values.length) list.appendChild(profileSkillRow(null));
    wrapper.appendChild(list);
    const add = document.createElement("button");
    add.type = "button";
    add.className = "inspect-inline-action profile-skill-add";
    add.textContent = "添加技能";
    add.addEventListener("click", () => {
      const row = profileSkillRow(null);
      list.appendChild(row);
      row.querySelector("input.profile-skill-name").focus();
    });
    wrapper.appendChild(add);
    const hint = document.createElement("span");
    hint.className = "job-edit-field-hint";
    hint.textContent = "程度只使用四种标准值；缺少事实证据的技能会转入待澄清问题，不会伪造证据。";
    wrapper.appendChild(hint);
    return wrapper;
  }

  function openJobEditor(kind, value) {
    if (!kind || !value || !state.currentSessionId) return;
    // A pending save belongs to the editor instance that started it. Opening a
    // fresh editor invalidates that instance so a late response cannot close or
    // unlock the new editor.
    state.jobEditor.serial += 1;
    state.jobEditor.open = true;
    state.jobEditor.kind = kind;
    state.jobEditor.value = JSON.parse(JSON.stringify(value));
    state.jobEditor.sessionId = String(state.currentSessionId);
    state.jobEditor.busy = false;
    state.jobEditor.previousFocus = document.activeElement;
    const fields = $("jobEditFields");
    fields.replaceChildren();
    if (kind === "jd") {
      const draft = state.jobEditor.value;
      $("jobEditTitle").textContent = "编辑目标 JD";
      fields.append(
        jobEditField("岗位名称", "job_title", draft.job_title),
        jobEditField("公司", "company", draft.company),
        jobEditField("薪资", "salary", draft.salary),
        jobEditField("地点", "location", draft.location),
        jobEditField("团队", "team", draft.team),
        jobEditField("概况", "raw_summary", draft.raw_summary, { multiline: true }),
        editableListEditor("硬性要求", "hard_requirements", draft.hard_requirements, { multiline: true, placeholder: "例如：熟悉 Python" }),
        editableListEditor("技能标签", "skill_tags", draft.skill_tags, { placeholder: "例如：Python" }),
        editableListEditor("工作职责", "responsibilities", draft.responsibilities, { multiline: true, placeholder: "例如：负责模型训练与部署" }),
        editableListEditor("加分项", "nice_to_haves", draft.nice_to_haves, { multiline: true, placeholder: "例如：有竞赛或开源经验" }),
        editableListEditor("不确定字段", "uncertain_fields", draft.uncertain_fields, { hint: "每行一个字段名，例如 salary、location", placeholder: "例如：salary" })
      );
    } else {
      const draft = state.jobEditor.value;
      $("jobEditTitle").textContent = "编辑技能档案";
      fields.append(
        jobEditField("档案概况", "summary", draft.summary, { multiline: true }),
        jobEditField("目标方向", "target_direction", draft.target_direction),
        profileSkillsEditor(draft.skills),
        jobEditField("待澄清问题", "open_questions", editableList(draft.open_questions), { multiline: true, hint: "每行一项" })
      );
    }
    enhanceVoiceInputs(fields);
    setText($("jobEditStatus"), "");
    $("jobEditBackdrop").classList.remove("hidden");
    document.body.classList.add("modal-open");
    $("jobEditClose").focus();
  }

  function closeJobEditor() {
    if (!state.jobEditor.open) return;
    if ((state.voice.active || state.voice.stopping) && state.voice.target && $("jobEditBackdrop").contains(state.voice.target)) stopVoiceInput({ cancel: true });
    state.jobEditor.serial += 1;
    state.jobEditor.open = false;
    state.jobEditor.value = null;
    state.jobEditor.sessionId = "";
    state.jobEditor.busy = false;
    $("jobEditBackdrop").classList.add("hidden");
    if (!state.modelSettings.open && !state.newSession.open && !state.clarification.open && !state.workflowChat.open) document.body.classList.remove("modal-open");
    const previous = state.jobEditor.previousFocus;
    state.jobEditor.previousFocus = null;
    if (previous && typeof previous.focus === "function" && document.contains(previous)) previous.focus();
  }

  function linesFromField(field) {
    return String(field && field.value || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  }

  function readProfileSkillsEditor(originalSkills, existingQuestions) {
    const skills = [];
    const missingEvidence = [];
    qsa("[data-profile-skill-row]", $("jobEditFields")).forEach((row) => {
      const name = String((qs("input.profile-skill-name", row) || {}).value || "").trim();
      const proficiency = String((qs(".profile-skill-proficiency", row) || {}).value || "working");
      const evidence = String((qs("textarea.profile-skill-evidence", row) || {}).value || "").trim();
      if (!name && !evidence) return;
      if (!name) return;
      if (!evidence) {
        missingEvidence.push(name);
        return;
      }
      const index = Number(row.dataset.skillIndex);
      const original = Number.isInteger(index) && index >= 0 && Array.isArray(originalSkills) ? originalSkills[index] : null;
      const old = original && typeof original === "object" ? original : {};
      const oldName = String(old.name || old.skill || old.title || "").trim();
      const oldEvidence = String(old.evidence || old.proof || "").trim();
      const changedByUser = !original || typeof original !== "object" || oldName !== name || oldEvidence !== evidence;
      const sources = Array.isArray(old.sources) ? old.sources.filter((source) => PROFILE_EVIDENCE_SOURCES.has(source)) : [];
      const item = { ...old, name, proficiency: PROFILE_PROFICIENCIES.some(([key]) => key === proficiency) ? proficiency : "working", evidence };
      const confidence = Number(item.confidence);
      item.confidence = Number.isFinite(confidence) ? Math.min(1, Math.max(0, confidence)) : 0.5;
      // A newly entered skill or explicitly changed fact is a user statement.
      // Unchanged valid provenance is retained verbatim.
      item.sources = changedByUser || !sources.length ? ["self_report"] : sources;
      skills.push(item);
    });
    const questions = [...(Array.isArray(existingQuestions) ? existingQuestions : [])];
    missingEvidence.forEach((name) => {
      const question = `请补充技能“${name}”的事实证据（项目、日志、材料路径或具体结果）。`;
      if (!questions.includes(question)) questions.push(question);
    });
    return { skills, openQuestions: questions, missingEvidence };
  }

  async function saveJobEditor(event) {
    event.preventDefault();
    if (!state.jobEditor.open || !state.jobEditor.value || state.jobEditor.busy) return;
    const sessionId = String(state.jobEditor.sessionId || "");
    if (!sessionId || String(state.currentSessionId || "") !== sessionId) return;
    const saveToken = ++state.jobEditor.serial;
    const saveIsCurrent = () => state.jobEditor.open
      && state.jobEditor.busy
      && state.jobEditor.serial === saveToken
      && state.jobEditor.sessionId === sessionId
      && String(state.currentSessionId || "") === sessionId;
    const draft = JSON.parse(JSON.stringify(state.jobEditor.value));
    let missingEvidence = [];
    qsa("[data-job-edit-field]", $("jobEditFields")).forEach((field) => {
      const key = field.dataset.jobEditField;
      // JD collection fields use one control per item. Profile's existing
      // open_questions textarea remains intentionally unchanged.
      if (key === "open_questions") draft[key] = linesFromField(field);
      else draft[key] = String(field.value || "").trim();
    });
    const kind = state.jobEditor.kind;
    if (kind === "jd") {
      ["hard_requirements", "skill_tags", "responsibilities", "nice_to_haves", "uncertain_fields"].forEach((key) => {
        draft[key] = readEditableList(key);
      });
    }
    if (kind === "profile") {
      const profileSkills = readProfileSkillsEditor(draft.skills, draft.open_questions);
      draft.skills = profileSkills.skills;
      draft.open_questions = profileSkills.openQuestions;
      missingEvidence = profileSkills.missingEvidence;
    }
    const kindLabel = kind === "jd" ? "岗位 JD" : "技能档案";
    const button = $("jobEditSave");
    state.jobEditor.busy = true;
    button.disabled = true;
    qsa("input, textarea, select, button", $("jobEditFields")).forEach((control) => { control.disabled = true; });
    setText($("jobEditStatus"), `正在保存${kindLabel}…`);
    try {
      const payload = await request(`/sessions/${encodeURIComponent(sessionId)}/${kind}`, { method: "POST", body: { [kind]: draft, confirmed: true } });
      if (!saveIsCurrent()) return;
      const session = unwrapSession(payload);
      if (session && session.id) state.currentSession = session;
      else await refreshCurrentSession(sessionId, { scroll: false });
      if (!saveIsCurrent()) return;
      // JD edits also change the sidebar title. Refresh the summaries before
      // rendering so both the row and the top title use the new JD immediately.
      await refreshSessions();
      if (!saveIsCurrent()) return;
      const summary = state.sessions.find((row) => String(row.id) === sessionId);
      if (summary && state.currentSession) state.currentSession = { ...state.currentSession, title: summary.title, job_title: summary.job_title, company: summary.company };
      if (!saveIsCurrent()) return;
      state.jobEditor.busy = false;
      closeJobEditor();
      renderAll();
      toast(missingEvidence.length ? `${kindLabel}已确认并保存；${missingEvidence.length} 项缺少事实证据，已转入待澄清问题。` : `${kindLabel}已确认并保存。`, "success");
    } catch (error) {
      if (saveIsCurrent()) setText($("jobEditStatus"), `保存失败：${error.message || "无法保存资料"}`);
    } finally {
      if (saveIsCurrent()) {
        state.jobEditor.busy = false;
        button.disabled = false;
        qsa("input, textarea, select, button", $("jobEditFields")).forEach((control) => { control.disabled = false; });
      }
    }
  }

  function commaList(value) {
    return String(value || "").split(/[，,、]/).map((item) => item.trim()).filter(Boolean);
  }

  function canPrepareJobAction() {
    if (state.currentSession?.pending) { toast("请先批准或拒绝当前操作。", ""); return false; }
    if (sessionTurnBlocked(state.currentSession)) {
      toast(sessionHasQueue(state.currentSession) ? "上一轮工具队列待恢复，请先点击“继续执行工具队列”。" : "当前会话仍在运行，请等待完成。", "");
      return false;
    }
    return true;
  }

  function currentProjectFilterCard() {
    return qs(".project-filter-card", $("inspectContent"))
      || qs(".project-filter-card", $("planPageResults"));
  }

  function projectFilterControl(card, key) {
    return card && card._projectFilterControls && card._projectFilterControls[key]
      ? card._projectFilterControls[key]
      : qs(`[data-job-filter="${key}"]`, card);
  }

  function submitProjectFilter(sourceCard = null) {
    if (!canPrepareJobAction()) return;
    const card = sourceCard || currentProjectFilterCard();
    if (!card) return;
    const minStars = String((projectFilterControl(card, "minStars") || {}).value || "").trim();
    const include = commaList((projectFilterControl(card, "include") || {}).value);
    const exclude = commaList((projectFilterControl(card, "exclude") || {}).value);
    const other = String((projectFilterControl(card, "other") || {}).value || "").trim();
    const paperProjects = Boolean((projectFilterControl(card, "paperProjects") || {}).checked);
    const rerank = Boolean((projectFilterControl(card, "rerank") || {}).checked);
    if (!minStars && !include.length && !exclude.length && !other && !paperProjects && !rerank) { toast("请先填写至少一项筛选偏好。", ""); return; }
    const filterValues = { minStars: minStars, include: include.join("、"), exclude: exclude.join("、"), other, paperProjects, rerank };
    rememberProjectFilter(state.currentSessionId, filterValues);
    const summaryParts = ["重新筛选练习项目"];
    if (minStars) summaryParts.push(`最低 star ${minStars}`);
    if (include.length) summaryParts.push(`包含 ${include.join("、")}`);
    if (exclude.length) summaryParts.push(`排除 ${exclude.join("、")}`);
    if (other) summaryParts.push(`其他要求：${other}`);
    if (paperProjects) summaryParts.push("查找相关高影响力论文的开源实现（只接受可核验依据）");
    const summary = `${summaryParts.join("；")}。`;
    const text = `${summary}请搜索真实候选项目，并说明它们与当前岗位差距的对应关系。${other ? `请把这些额外要求作为筛选条件并记录到当前会话：${other}。` : ""}${paperProjects ? "查找相关高影响力论文的开源实现；核对论文来源、代码关联及影响力依据，不足时标为待核实，绝不编造。" : "仍请保留真实来源链接和可核验依据，不要编造项目影响力或结果。"}`;
    const id = String(state.currentSessionId || "");
    Promise.resolve(sendTrustedWorkflowAction(text, "plan", summary, { project_filter: projectFilterActionContext(filterValues) })).then((accepted) => {
      // Keep the per-session draft while the request is in flight (and after a
      // failure). Once the server has accepted the workflow action, its
      // persisted project_filter is authoritative and a later render can
      // absorb model-confirmed refinements without an old textarea winning.
      if (accepted && String(state.currentSessionId || "") === id) projectFilterDrafts.delete(id);
    });
  }

  function discussProjectFilter(sourceCard = null) {
    if (!canPrepareJobAction()) return;
    const card = sourceCard || currentProjectFilterCard();
    if (!card) return;
    const values = {
      minStars: String((projectFilterControl(card, "minStars") || {}).value || "").trim(),
      include: String((projectFilterControl(card, "include") || {}).value || "").trim(),
      exclude: String((projectFilterControl(card, "exclude") || {}).value || "").trim(),
      other: String((projectFilterControl(card, "other") || {}).value || "").trim(),
      paperProjects: Boolean((projectFilterControl(card, "paperProjects") || {}).checked),
      rerank: Boolean((projectFilterControl(card, "rerank") || {}).checked)
    };
    rememberProjectFilter(state.currentSessionId, values);
    const prompt = values.other
      ? `我想和你讨论并细化项目筛选要求。当前其他要求是：${values.other}。请结合岗位差距和已有项目候选，指出需要澄清的取舍；确认后把最终筛选偏好保存到当前会话，后续搜索和计划都遵守。`
      : "我想和你讨论项目筛选要求。请结合岗位差距和已有项目候选，询问我最需要补充的限制或偏好；确认后把最终筛选偏好保存到当前会话，后续搜索和计划都遵守。";
    const summary = "讨论并确认项目筛选要求";
    const id = String(state.currentSessionId || "");
    Promise.resolve(sendTrustedWorkflowAction(prompt, "plan", summary, { project_filter: projectFilterActionContext(values) })).then((accepted) => {
      if (accepted && String(state.currentSessionId || "") === id) projectFilterDrafts.delete(id);
    });
  }

  function startSelectedProject(repo) {
    const projectKey = projectIdentity(repo);
    if (!projectKey) return;
    const name = projectDisplayTitle(repo);
    if (!name) return;
    const summary = `选用练习项目：${name}`;
    const text = `${summary}。候选 project_key=${projectKey}。请调用 select_project 使用这个 project_key 记录当前项目，先给出今天的上手步骤，等待我按步骤实践。`;
    selectWorkflowStep("practice");
    sendTrustedWorkflowAction(text, "practice", summary, { project_key: projectKey }, { openChat: false });
  }

  function clarificationQuestionKey(question) {
    return String(question || "").trim();
  }

  function clarificationDraftKey(sessionId, question) {
    return `${String(sessionId || "")}:${clarificationQuestionKey(question)}`;
  }

  function clarificationJobFor(sessionId, questionKey = "") {
    const entry = state.clarification.jobs.get(String(sessionId || ""));
    return entry && (!questionKey || entry.questionKey === questionKey) ? entry : null;
  }

  function clarificationStatus(message, kind = "") {
    state.clarification.status = String(message || "");
    state.clarification.statusKind = String(kind || "");
    const node = $("clarificationStatus");
    if (!node) return;
    node.className = `clarification-status-message${kind ? ` ${kind}` : ""}`;
    node.textContent = state.clarification.status;
    node.setAttribute("aria-busy", kind === "loading" ? "true" : "false");
  }

  function clarificationFeedbackText(terminalPayload, confirmed = false) {
    // This must be the result of this clarification job.  Never search the
    // session transcript: a late result from another action could otherwise
    // make this modal display an unrelated old assistant message.
    const result = terminalPayload && terminalPayload.result;
    const resultMessage = result && typeof result === "object"
      ? String(result.message || result.assistant_text || "").trim()
      : "";
    if (resultMessage) return resultMessage.slice(0, 4000);
    return confirmed ? "档案已更新，待澄清问题列表已刷新。" : "模型已完成处理，但待澄清问题仍保留。";
  }

  function clarificationEntryBlocked(entry) {
    return Boolean(entry && entry.blocked);
  }

  function clarificationEntryActive(entry) {
    return Boolean(entry && !entry.blocked && entry.active !== false);
  }

  function clarificationSessionBlocked(session, result = null) {
    const resultStatus = String(result && result.status || "").trim().toLowerCase();
    const executionStatus = String(session && session.execution && session.execution.status || "").trim().toLowerCase();
    const blockedStatuses = new Set([
      "pending", "paused", "uncertain", "waiting_approval", "waiting-approval",
      "needs_approval", "needs-approval", "queued", "running"
    ]);
    return blockedStatuses.has(resultStatus)
      || Boolean(result && result.pending)
      || Boolean(session && session.pending)
      || sessionHasQueue(session)
      || blockedStatuses.has(executionStatus);
  }

  function clarificationSnapshotFor(session, sessionId) {
    const id = String(sessionId || "");
    if (!session || !id || String(session.id || "") !== id) return null;
    const root = session.skill_state;
    const data = root && typeof root === "object" ? root["job-radar"] : null;
    if (!data || typeof data !== "object") return null;
    const profile = data.profile;
    // A session shell can arrive before the job-radar profile is loaded.  A
    // missing open_questions array is therefore not evidence that a question
    // was answered.
    if (!profile || typeof profile !== "object" || !Array.isArray(profile.open_questions)) return null;
    const clarification = data.clarification && typeof data.clarification === "object" ? data.clarification : {};
    const questions = [...new Set([
      ...profile.open_questions,
      ...(Array.isArray(clarification.open_questions) ? clarification.open_questions : [])
    ].map((item) => String(item || "").trim()).filter(Boolean))];
    return { data, profile, clarification, questions };
  }

  function syncClarificationModal({ preserveAnswer = true } = {}) {
    const modal = $("clarificationBackdrop");
    if (!modal) return;
    modal.classList.toggle("hidden", !state.clarification.open);
    if (!state.clarification.open) return;
    setText($("clarificationQuestion"), state.clarification.question);
    const answer = $("clarificationAnswer");
    if (answer && (!preserveAnswer || document.activeElement !== answer)) answer.value = state.clarification.answer;
    if (answer) answer.disabled = Boolean(state.clarification.busy);
    const send = $("clarificationSend");
    if (send) {
      send.disabled = Boolean(state.clarification.busy || (!state.clarification.completed && !String(state.clarification.answer || "").trim()));
      send.textContent = state.clarification.completed ? "已完成" : state.clarification.busy ? "发送中…" : "发送回答";
    }
    const close = $("clarificationClose");
    if (close) close.disabled = false;
    const recovery = $("clarificationRecovery");
    const entry = clarificationJobFor(state.clarification.sessionId, state.clarification.questionKey);
    const blocked = Boolean(
      clarificationEntryBlocked(entry)
      || state.currentSession?.pending
      || sessionHasQueue(state.currentSession)
      || ["paused", "uncertain", "waiting_approval", "waiting-approval"].includes(String(state.currentSession?.execution?.status || "").toLowerCase())
    );
    if (recovery) {
      recovery.classList.toggle("hidden", !blocked);
      recovery.disabled = false;
    }
    const feedback = $("clarificationFeedback");
    if (feedback) {
      feedback.classList.toggle("hidden", !state.clarification.feedback);
      feedback.textContent = state.clarification.feedback;
    }
    clarificationStatus(state.clarification.status, state.clarification.statusKind);
  }

  function openClarificationModal(question) {
    const text = String(question || "").trim();
    const sessionId = String(state.currentSessionId || "");
    if (!text || !sessionId || state.loadingSession) return;
    const key = clarificationQuestionKey(text);
    const draftKey = clarificationDraftKey(sessionId, text);
    const running = clarificationJobFor(sessionId, key);
    state.clarification.open = true;
    state.clarification.sessionId = sessionId;
    state.clarification.question = text;
    state.clarification.questionKey = key;
    state.clarification.answer = running?.answer || state.clarification.drafts.get(draftKey) || "";
    // An awaitingConfirmation entry means the backend result was ambiguous;
    // it must remain retryable and must not be presented as a saved answer
    // when the modal is opened again.
    state.clarification.completed = false;
    state.clarification.busy = clarificationEntryActive(running);
    state.clarification.feedback = "";
    state.clarification.previousFocus = document.activeElement;
    state.clarification.serial += 1;
    if (clarificationEntryActive(running)) clarificationStatus("正在处理你的回答，关闭窗口不会取消后台任务。", "loading");
    else if (clarificationEntryBlocked(running)) clarificationStatus("请先打开对话处理当前操作；答案会保留，处理完成后继续更新。", "error");
    else if (running?.awaitingSnapshot || running?.awaitingConfirmation) clarificationStatus("暂时无法确认档案保存状态；答案已保留，可以稍后重试。", "error");
    else if (state.currentSession?.pending) clarificationStatus("当前会话等待审批，请先处理对话中的审批操作。", "error");
    else if (sessionHasQueue(state.currentSession)) clarificationStatus("当前会话有待恢复工具队列，请打开对话继续执行。", "error");
    else clarificationStatus("只填写与问题相关的事实、项目、日志或材料路径。回答与结果会保存到当前会话。", "");
    $("clarificationBackdrop").classList.remove("hidden");
    document.body.classList.add("modal-open");
    syncClarificationModal({ preserveAnswer: false });
    $("clarificationAnswer").focus();
  }

  function closeClarificationModal({ restore = true } = {}) {
    if (!state.clarification.open) return;
    if ((state.voice.active || state.voice.stopping) && state.voice.target && $("clarificationBackdrop").contains(state.voice.target)) stopVoiceInput({ cancel: true });
    state.clarification.open = false;
    $("clarificationBackdrop").classList.add("hidden");
    if (!state.modelSettings.open && !state.jobEditor.open && !state.newSession.open && !state.clarification.open && !state.workflowChat.open) document.body.classList.remove("modal-open");
    const previous = state.clarification.previousFocus;
    state.clarification.previousFocus = null;
    if (restore) restoreSidebarAwareFocus(previous);
  }

  async function submitClarification(event) {
    event.preventDefault();
    if (!state.clarification.open) return;
    if (state.clarification.completed) {
      closeClarificationModal();
      return;
    }
    if (state.clarification.busy) return;
    const sessionId = String(state.clarification.sessionId || "");
    const question = String(state.clarification.question || "").trim();
    const answer = String($("clarificationAnswer")?.value || "").trim();
    if (!sessionId || sessionId !== String(state.currentSessionId || "")) {
      clarificationStatus("会话已切换，请关闭窗口后在当前会话重新打开问题。", "error");
      return;
    }
    if (!answer) {
      clarificationStatus("请填写事实回答后再发送。", "error");
      $("clarificationAnswer")?.focus();
      return;
    }
    if (state.currentSession?.pending) {
      clarificationStatus("当前会话等待审批，请打开对话处理审批。", "error");
      syncClarificationModal();
      return;
    }
    if (sessionTurnBlocked(state.currentSession)) {
      clarificationStatus(sessionHasQueue(state.currentSession) ? "当前会话有待恢复工具队列，请打开对话继续执行。" : "当前会话正在处理，请稍候。", "error");
      syncClarificationModal();
      return;
    }
    const key = clarificationQuestionKey(question);
    const token = ++state.clarification.serial;
    const entry = {
      token,
      sessionId,
      question,
      questionKey: key,
      answer,
      baseMessageCount: visibleMessages(state.currentSession).length,
      jobId: ""
    };
    state.clarification.answer = answer;
    state.clarification.drafts.set(clarificationDraftKey(sessionId, question), answer);
    state.clarification.jobs.set(sessionId, entry);
    state.clarification.busy = true;
    state.clarification.feedback = "";
    clarificationStatus("正在处理你的回答，回答与结果会保存到当前会话…", "loading");
    syncClarificationModal();
    const summary = `回答待澄清问题：${question}`;
    // The server builds the fixed clarification action and validates the
    // question under the session lock. Do not send a client-authored prompt
    // through the ordinary message route or start a second model/session.
    const body = { question, answer };
    const accepted = await enqueueAction("clarification", body, sessionId, {
      optimisticText: `${summary}\n事实回答：${answer}`,
      clarificationContext: entry
    });
    if (!accepted) {
      const current = state.clarification.jobs.get(sessionId);
      if (current && current.token === token) {
        state.clarification.jobs.delete(sessionId);
        state.clarification.busy = false;
        clarificationStatus("发送失败，答案已保留；请修正后重试。", "error");
        syncClarificationModal();
      }
      return;
    }
    // Older services can complete a short message synchronously and omit a
    // job id. Treat that response as complete after enqueueAction refreshes the
    // authoritative session snapshot.
    const current = state.clarification.jobs.get(sessionId);
    if (current && current.token === token && !current.jobId && !state.jobs.has(sessionId)) {
      finishClarificationJob(sessionId, { status: "completed", result: {} }, state.currentSession);
    }
  }

  const JD_FIELDS = ["jdSourceUrl", "jdSourceText", "jdPlatform", "jdQuery", "jdImageNote"];

  function jdDraft() {
    const key = state.jdImport.sessionId;
    if (!state.jdImport.drafts.has(key)) state.jdImport.drafts.set(key, { fields: {}, files: [], mode: "source" });
    return state.jdImport.drafts.get(key);
  }

  function saveJdDraft() {
    if (!state.jdImport.open) return;
    JD_FIELDS.forEach((id) => { jdDraft().fields[id] = $(id).value; });
  }

  function setJdMode(mode) {
    saveJdDraft();
    jdDraft().mode = mode;
    qsa("[data-jd-mode]").forEach((button) => button.setAttribute("aria-selected", String(button.dataset.jdMode === mode)));
    $("jdSourcePanel").classList.toggle("hidden", mode !== "source");
    $("jdSearchPanel").classList.toggle("hidden", mode !== "search");
    $("jdImagePanel").classList.toggle("hidden", mode !== "image");
  }

  function renderJdFiles() {
    const list = $("jdImageList"); list.replaceChildren();
    jdDraft().files.forEach((file, index) => {
      const row = document.createElement("div"); row.className = "jd-file-row";
      const name = document.createElement("span"); name.textContent = file.name || "粘贴截图";
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "移除"; remove.disabled = state.jdImport.busy;
      remove.addEventListener("click", () => { jdDraft().files.splice(index, 1); renderJdFiles(); });
      row.append(name, remove); list.appendChild(row);
    });
  }

  async function openJdImport() {
    if (state.jdImport.busy || state.loadingSession || state.newSession.busy) return;
    if (!state.currentSessionId) await createSession();
    selectWorkflowStep("jd");
    $(jdDraft().mode === "source" ? "jdSourceUrl" : jdDraft().mode === "image" ? "jdImageDrop" : "jdQuery").focus();
  }
  function closeJdImport() {
    if (state.jdImport.busy) return;
    if (state.jdImport.open) saveJdDraft();
  }

  function addJdFiles(files) {
    if (state.jdImport.busy) return;
    const candidates = Array.from(files || []);
    const valid = candidates.filter((file) => isSupportedImageAttachment({ name: file.name, mediaType: file.type }) && file.size <= MAX_UPLOAD_BYTES);
    if (valid.length !== candidates.length) setText($("jdImportStatus"), "仅支持每张不超过 10 MiB 的 PNG、JPEG、GIF 或 WebP 图片。");
    const available = Math.max(0, 10 - jdDraft().files.length);
    jdDraft().files.push(...valid.slice(0, available));
    if (valid.length > available) setText($("jdImportStatus"), "一次最多发送 10 张岗位截图，多出的图片未加入。请分批发送或移除后重新选择。");
    renderJdFiles();
  }

  async function submitJdImport(kind) {
    if (state.jdImport.busy) return;
    if (!state.currentSessionId) {
      saveJdDraft();
      const pendingDraft = jdDraft();
      const captured = { fields: { ...pendingDraft.fields }, files: [...pendingDraft.files], mode: pendingDraft.mode };
      const createdId = await createSession();
      if (!createdId || String(state.currentSessionId) !== String(createdId)) { setText($("jdImportStatus"), "会话已变化，输入已保留，请在原会话重试。"); return; }
      state.jdImport.sessionId = String(createdId); state.jdImport.drafts.set(String(createdId), captured);
      JD_FIELDS.forEach((id) => { $(id).value = captured.fields[id] || (id === "jdPlatform" ? "boss" : ""); });
      setJdMode(captured.mode); renderJdFiles();
    }
    const id = state.jdImport.sessionId;
    if (state.loadingSession || String(state.currentSessionId) !== id) { setText($("jdImportStatus"), "会话已切换，请关闭后在目标会话重新打开。"); return; }
    if (state.currentSession?.pending || sessionTurnBlocked(state.currentSession)) { setText($("jdImportStatus"), "请先处理当前待确认操作、等待运行完成或点击“继续执行工具队列”。"); return; }
    saveJdDraft(); const draft = jdDraft(); const fields = draft.fields; let text = "";
    if (kind === "search") {
      if (!fields.jdQuery.trim()) { setText($("jdImportStatus"), "请填写岗位名称或方向。"); return; }
      text = `请在当前求职会话中调用 search_jobs 搜索真实招聘岗位，平台 platform=${fields.jdPlatform}，查询 query=${fields.jdQuery.trim()}。请展示真实来源链接，整理岗位 JD 供我核对，不要编造搜索结果或岗位要求。`;
    } else if (kind === "source") {
      const url = fields.jdSourceUrl.trim(), body = fields.jdSourceText.trim();
      if (!url && !body) { setText($("jdImportStatus"), "请提供来源链接或岗位正文。"); return; }
      if (url) { try { if (new URL(url).protocol !== "https:") throw new Error(); } catch (_) { setText($("jdImportStatus"), "请输入有效的 HTTPS 来源链接。"); return; } }
      text = `请在当前求职会话中解析下面的真实岗位来源，整理 JD 并展示摘要供我核对。可访问链接时读取真实内容；无法访问且正文不足时请说明需要补充，不要编造。\n来源链接：${url || "未提供"}\n岗位正文：\n${body || "未提供"}`;
    } else {
      if (!draft.files.length) { setText($("jdImportStatus"), "请先选择或粘贴岗位截图。"); return; }
      text = `请阅读本条消息附带的真实岗位截图，识别并整理岗位 JD 供我核对；看不清的字段请标为待确认，不要猜测。\n${fields.jdImageNote.trim()}`;
    }
    text += "\n\n本次只处理当前岗位 JD 阶段：用 set_jd 保存待核对的 JD 草稿，保留真实来源链接或用户提供的正文/截图出处；不要推测缺失字段。展示识别摘要和待确认项后等待我确认，不要自动进入技能档案、计划或项目阶段。";
    state.jdImport.busy = true;
    qsa("button, input, textarea, select", $("jdImportBackdrop")).forEach((el) => { el.disabled = true; });
    $("jdImportSubmit").textContent = "正在提交…";
    setText($("jdImportStatus"), kind === "image" ? "正在上传截图并发送…" : "正在提交…");
    try {
      const attachments = [];
      if (kind === "image") for (const file of draft.files) attachments.push(await uploadOne(file));
      if (String(state.currentSessionId) !== id || state.loadingSession || state.currentSession?.pending || sessionTurnBlocked(state.currentSession)) throw new Error("会话状态已变化，输入已保留，请稍后重试。");
      const body = { text }; if (attachments.length) body.attachments = attachments.map((item) => item.path);
      const accepted = await enqueueAction("message", body, id, { optimisticText: text });
      if (!accepted) throw new Error("发送失败，输入已保留，请重试。");
      if (kind === "search") $("jdQuery").value = "";
      else if (kind === "source") { $("jdSourceUrl").value = ""; $("jdSourceText").value = ""; }
      else { draft.files = []; $("jdImageNote").value = ""; }
       saveJdDraft(); state.jdImport.busy = false; openWorkflowChatDialog(); setText($("jdImportStatus"), "已提交；对话窗口已打开，结果会同步到本页。");
    } catch (error) { setText($("jdImportStatus"), error.message || "导入失败，输入已保留。"); }
    finally { state.jdImport.busy = false; qsa("button, input, textarea, select", $("jdImportBackdrop")).forEach((el) => { el.disabled = false; }); $("jdImportSubmit").textContent = "交给 AI 整理 JD"; renderJdFiles(); }
  }

  function renderJobPane(root) {
    const session = state.currentSession || {};
    const stateData = workflowData();
    const step = state.selectedStep;
    renderStepIntro(root, stateData);
    const title = document.createElement("div"); title.className = "inspect-section-title"; title.textContent = "求职资料与进度"; root.appendChild(title);
    if (!stateData) { const empty = document.createElement("div"); empty.className = "inspect-empty"; empty.textContent = "在对话中提供 JD、已有技能或简历材料后，相关资料会显示在这里。"; root.appendChild(empty); return; }
    if (["plan", "practice"].includes(step)) renderProjectHistory(root, stateData);
    if (step === "jd" && stateData.jd) {
      const jdCard = jdObjectCard(stateData.jd);
      appendJobList(jdCard, "硬性要求", stateData.jd.hard_requirements, (item) => jobItemText(item, ["requirement", "name", "text", "title"]), { listTag: "ul", listClass: "jd-requirement-list", limit: null });
      appendJobList(jdCard, "技能标签", stateData.jd.skill_tags, (item) => jobItemText(item, ["name", "skill", "tag", "text"]), { listClass: "job-chip-list", itemClass: "job-chip", limit: null });
      appendJobList(jdCard, "工作职责", stateData.jd.responsibilities, (item) => jobItemText(item, ["responsibility", "name", "text", "title"]), { listTag: "ul", listClass: "jd-detail-list", limit: null });
      appendJobList(jdCard, "加分项", stateData.jd.nice_to_haves, (item) => jobItemText(item, ["requirement", "name", "text", "title"]), { listTag: "ul", listClass: "jd-detail-list", limit: null });
      appendJobList(jdCard, "待确认字段", stateData.jd.uncertain_fields, (item) => jobItemText(item, ["field", "name", "key", "text", "title"]), { listTag: "ul", listClass: "jd-detail-list jd-uncertain-list", limit: null });
      appendJdSummary(jdCard, stateData.jd);
      root.appendChild(jdCard);
    }
    if (step === "profile" && stateData.profile) {
      const profileCard = jobObjectCard("已有技能", stateData.profile, ["summary"], "profile");
      appendJobList(profileCard, "技能与证据", stateData.profile.skills, (item) => {
        if (typeof item === "string") return item;
        const name = item && (item.name || item.skill || item.title);
        const level = item && (item.level || item.proficiency || item.status);
        const evidence = item && (item.evidence || item.proof);
        return [name, level && `水平：${jobEnumLabel(level)}`, evidence && `证据：${evidence}`].filter(Boolean).join(" · ");
      });
      root.appendChild(profileCard);
    }
    const clarification = stateData.clarification && typeof stateData.clarification === "object" ? stateData.clarification : {};
    const profileQuestions = stateData.profile && Array.isArray(stateData.profile.open_questions) ? stateData.profile.open_questions : [];
    const clarificationQuestions = Array.isArray(clarification.open_questions) ? clarification.open_questions : [];
    const pendingClarification = state.clarification.jobs.get(String(state.currentSessionId || ""));
    const questions = [...new Set([
      ...clarificationQuestions,
      ...profileQuestions,
      // Keep a question visible until this session's clarification job has
      // supplied confirmed=true.  A transient or unconfirmed snapshot must
      // never make a still-unverified question disappear from the UI.
      ...(pendingClarification?.question ? [pendingClarification.question] : [])
    ].map((item) => String(item || "").trim()).filter(Boolean))];
    if (step === "profile" && (questions.length || (stateData.profile && clarification.status))) {
      const card = document.createElement("section"); card.className = "job-card clarification-card";
      const head = document.createElement("div"); head.className = "job-card-head";
      const heading = document.createElement("h3"); heading.textContent = "待澄清问题"; head.appendChild(heading);
      const status = String(clarification.status || (questions.length ? "active" : "completed")).trim().toLowerCase();
      const statusText = status === "completed" || status === "complete" || !questions.length ? "已完成" : "待回答";
      const badge = document.createElement("span"); badge.className = `clarification-status ${statusText === "已完成" ? "done" : ""}`; badge.textContent = statusText; head.appendChild(badge);
      card.appendChild(head);
      if (!questions.length) {
        const done = document.createElement("p"); done.className = "clarification-done"; done.textContent = "待澄清内容已全部确认，技能档案更新完成。"; card.appendChild(done);
      } else {
        const hint = document.createElement("p"); hint.className = "job-edit-field-hint"; hint.textContent = "回答后更新档案，并从清单中移除已澄清问题。"; card.appendChild(hint);
        const list = document.createElement("div"); list.className = "clarification-list";
        questions.slice(0, 12).forEach((question) => {
          const row = document.createElement("div"); row.className = "clarification-row";
          const text = document.createElement("span"); text.className = "clarification-question"; text.textContent = question; row.appendChild(text);
          const answer = document.createElement("button"); answer.type = "button"; answer.className = "job-card-action"; answer.textContent = "现在回答"; answer.setAttribute("aria-label", `回答待澄清问题：${question}`); answer.addEventListener("click", () => openClarificationModal(question)); row.appendChild(answer);
          list.appendChild(row);
        });
        card.appendChild(list);
      }
      root.appendChild(card);
    }
    if (step === "plan" && stateData.plan) {
      const planCard = document.createElement("section"); planCard.className = "job-card";
      const h = document.createElement("h3"); h.textContent = "下一步与差距"; planCard.appendChild(h);
      appendJobList(planCard, "差距", stateData.plan.gaps, (item) => {
        if (typeof item === "string") return item;
        return [jobItemText(item, ["requirement", "name", "skill", "text", "title"]), item && jobEnumLabel(item.status || item.priority)].filter(Boolean).join(" · ");
      });
      appendJobList(planCard, "学习阶段", stateData.plan.phases, (item) => {
        if (typeof item === "string") return item;
        return [item && (item.phase || item.title || item.name), item && (item.goal || item.description)].filter(Boolean).join("：");
      });
      if (planCard.querySelector(".job-list")) root.appendChild(planCard);
      const filterCard = document.createElement("section"); filterCard.className = "job-card project-filter-card";
      const filterHead = document.createElement("div"); filterHead.className = "job-card-head";
      const filterTitle = document.createElement("h3"); filterTitle.textContent = "调整项目筛选"; filterHead.appendChild(filterTitle); filterCard.appendChild(filterHead);
      const filterHint = document.createElement("p"); filterHint.className = "job-edit-field-hint"; filterHint.textContent = "按明确偏好重新搜索。"; filterCard.appendChild(filterHint);
      const filterFields = document.createElement("div"); filterFields.className = "project-filter-fields";
      const filterValues = dataProjectFilterForSession(state.currentSessionId);
      const rememberField = (key, control) => control.addEventListener("input", () => rememberProjectFilter(state.currentSessionId, { [key]: control.value }));
      const minStars = document.createElement("input"); minStars.type = "number"; minStars.min = "0"; minStars.step = "1"; minStars.placeholder = "最低星标数"; minStars.value = filterValues.minStars; minStars.dataset.jobFilter = "minStars"; minStars.setAttribute("aria-label", "最低星标数"); rememberField("minStars", minStars);
      const include = document.createElement("input"); include.type = "text"; include.placeholder = "包含关键词（逗号分隔）"; include.value = filterValues.include; include.dataset.jobFilter = "include"; include.setAttribute("aria-label", "包含关键词"); rememberField("include", include);
      const exclude = document.createElement("input"); exclude.type = "text"; exclude.placeholder = "排除关键词（逗号分隔）"; exclude.value = filterValues.exclude; exclude.dataset.jobFilter = "exclude"; exclude.setAttribute("aria-label", "排除关键词"); rememberField("exclude", exclude);
      const other = document.createElement("textarea"); other.rows = 3; other.placeholder = "其他要求（例如：优先轻量、文档完整、能在 Windows 本地复现）"; other.value = filterValues.other; other.dataset.jobFilter = "other"; other.setAttribute("aria-label", "其他项目筛选要求"); rememberField("other", other);
      const paperLabel = document.createElement("label"); paperLabel.className = "project-filter-checkbox";
      const paper = document.createElement("input"); paper.type = "checkbox"; paper.checked = filterValues.paperProjects; paper.dataset.jobFilter = "paperProjects"; paper.setAttribute("aria-label", "查找相关高影响力论文的开源实现"); paper.addEventListener("change", () => rememberProjectFilter(state.currentSessionId, { paperProjects: paper.checked }));
      paperLabel.append(paper, document.createTextNode("查找相关高影响力论文的开源实现"));
      const rerankLabel = document.createElement("label"); rerankLabel.className = "project-filter-checkbox";
      const rerank = document.createElement("input"); rerank.type = "checkbox"; rerank.checked = filterValues.rerank; rerank.dataset.jobFilter = "rerank"; rerank.setAttribute("aria-label", "使用 AI 教练比较候选项目"); rerank.addEventListener("change", () => rememberProjectFilter(state.currentSessionId, { rerank: rerank.checked }));
      rerankLabel.append(rerank, document.createTextNode("使用 AI 教练比较候选项目"));
      filterCard._projectFilterControls = { minStars, include, exclude, other, paperProjects: paper, rerank };
      filterFields.append(minStars, include, exclude, other); filterCard.append(filterFields, paperLabel, rerankLabel);
      const discussButton = document.createElement("button"); discussButton.type = "button"; discussButton.className = "action-button secondary project-filter-discuss"; discussButton.textContent = "和教练讨论筛选要求"; discussButton.addEventListener("click", () => discussProjectFilter(filterCard)); filterCard.appendChild(discussButton);
      const filterButton = document.createElement("button"); filterButton.type = "button"; filterButton.className = "action-button secondary project-filter-submit"; filterButton.textContent = "重新搜索"; filterButton.addEventListener("click", () => submitProjectFilter(filterCard)); filterCard.appendChild(filterButton);
      root.appendChild(filterCard);
    }
    if (["plan", "practice"].includes(step) && stateData.selected_project) {
      const selectedProject = { ...stateData.selected_project, full_name: projectDisplayTitle(stateData.selected_project) };
      if (!selectedProject.language) selectedProject.language = projectSourceLabel(stateData.selected_project);
      const selectedUrl = projectUrl(stateData.selected_project);
      if (selectedUrl) selectedProject.html_url = selectedUrl;
      else delete selectedProject.html_url;
      const selectedStars = projectStars(stateData.selected_project);
      if (selectedStars == null) delete selectedProject.stargazers_count;
      else selectedProject.stargazers_count = selectedStars;
      root.appendChild(jobObjectCard("已选练习项目", selectedProject, ["full_name", "language", "stargazers_count", "html_url"]));
    }
    if (step === "plan" && Array.isArray(stateData.repos) && stateData.repos.length) {
      const card = document.createElement("section"); card.className = "job-card"; const h = document.createElement("h3"); h.textContent = `候选练习项目 · ${stateData.repos.length}`; card.appendChild(h);
      stateData.repos.slice(0, 8).forEach((repo) => { const row = document.createElement("div"); row.className = "job-repo"; const info = document.createElement("div"); const strong = document.createElement("strong"); strong.textContent = projectDisplayTitle(repo); info.appendChild(strong); const repoUrl = projectUrl(repo); if (repoUrl) { const link = document.createElement("a"); link.href = repoUrl; link.target = "_blank"; link.rel = "noopener noreferrer"; let repoHost = ""; try { repoHost = new URL(repoUrl).hostname.toLowerCase(); } catch (_) {} link.textContent = repoHost === "github.com" || repoHost.endsWith(".github.com") ? "查看 GitHub 仓库 ↗" : "查看公开项目链接 ↗"; info.appendChild(link); } const paperUrl = String(repo.paper_url || "").trim(); if (paperUrl) { try { const parsedPaper = new URL(paperUrl); if (["https:", "http:"].includes(parsedPaper.protocol) && parsedPaper.hostname) { const paperLink = document.createElement("a"); paperLink.href = parsedPaper.href; paperLink.target = "_blank"; paperLink.rel = "noopener noreferrer"; paperLink.textContent = "查看论文来源 ↗"; info.appendChild(paperLink); } } catch (_) {} } const stars = projectStars(repo); const span = document.createElement("span"); span.textContent = [repo.language || projectSourceLabel(repo), stars == null ? "" : `${stars} 星标`].filter(Boolean).join(" · "); if (span.textContent) info.appendChild(span); if (repo.paper_candidate) { const paper = document.createElement("p"); paper.className = "repo-detail"; paper.textContent = `论文线索：${repo.paper_verified ? "已提供可核验证据" : "待核验，影响力未知"}${repo.paper_title ? ` · ${repo.paper_title}` : ""}`; info.appendChild(paper); } for (const key of ["description", "reason", "match_score"]) { if (key === "match_score" && (projectIsLocalOnly(repo) || repo.user_provided === true)) continue; if (repo[key] != null && String(repo[key]).trim() !== "") { const detail = document.createElement("p"); detail.className = "repo-detail"; detail.textContent = `${key === "match_score" ? "匹配分：" : key === "reason" ? "推荐理由：" : ""}${key === "match_score" && Number.isFinite(Number(repo[key])) ? `${Math.round(Number(repo[key]) <= 1 ? Number(repo[key]) * 100 : Number(repo[key]))}%` : repo[key]}`; info.appendChild(detail); } } row.appendChild(info); const use = document.createElement("button"); use.type = "button"; use.className = "job-card-action"; use.textContent = "选用并开始实践"; use.addEventListener("click", () => startSelectedProject(repo)); row.appendChild(use); card.appendChild(row); }); root.appendChild(card);
    }
    if (step === "practice" && Array.isArray(stateData.progress) && stateData.progress.length) {
      const card = document.createElement("section"); card.className = "job-card"; const h = document.createElement("h3"); h.textContent = "练习进度"; card.appendChild(h);
      stateData.progress.slice(-8).forEach((entry) => {
        const row = document.createElement("div"); row.className = "progress-row";
        const normalizedStatus = String(entry.status || "in_progress").trim().toLowerCase().replace(/[\s-]+/g, "_");
        const dot = document.createElement("span"); dot.className = `progress-state${["completed", "complete", "done"].includes(normalizedStatus) ? " done" : ""}`; row.appendChild(dot);
        const text = document.createElement("span"); text.className = "progress-text"; text.textContent = `${jobEnumLabel(entry.status || "in_progress")} · ${entry.note || "已记录"}`; row.appendChild(text);
        if (entry.evidence != null) { const evidence = document.createElement("small"); evidence.className = "progress-evidence"; evidence.textContent = `证据：${typeof entry.evidence === "string" ? entry.evidence : safeString(entry.evidence)}`; text.appendChild(evidence); }
        card.appendChild(row);
      }); root.appendChild(card);
    }
    if (!stateData.jd && !stateData.profile && !stateData.plan && !stateData.selected_project && !stateData.repos?.length && !stateData.progress?.length) { const empty = document.createElement("div"); empty.className = "inspect-empty"; empty.textContent = "还没有求职资料，先在对话中提供 JD 或已有技能。"; root.appendChild(empty); }
  }

  const JOB_ENUM_LABELS = {
    working: "能实际使用", usable: "能实际使用", can_use: "能实际使用", able_to_use: "能实际使用",
    aware: "了解",
    missing: "待补齐", incomplete: "待补齐", needs_practice: "需练习", weak: "需加强",
    beginner: "入门", familiar: "了解", intermediate: "基础使用", advanced: "熟练", proficient: "熟练", expert: "精通",
    planned: "待开始", todo: "待开始", pending: "待确认", in_progress: "进行中", active: "进行中",
    completed: "已完成", complete: "已完成", done: "已完成", blocked: "受阻", failed: "失败",
    high: "高优先", medium: "中优先", low: "低优先", evidence_insufficient: "证据不足", insufficient_evidence: "证据不足", unknown: "待确认"
  };

  function jobEnumLabel(value) {
    const raw = String(value == null ? "" : value).trim();
    if (!raw) return "";
    const key = raw.toLowerCase().replace(/[\s-]+/g, "_");
    return JOB_ENUM_LABELS[key] || raw;
  }

  function jobItemText(item, fields = []) {
    if (item == null) return "";
    if (typeof item === "string" || typeof item === "number") return String(item);
    if (typeof item !== "object") return String(item);
    for (const field of fields) {
      const value = item[field];
      if (value != null && String(value).trim()) return String(value).trim();
    }
    return "";
  }

  function jobObjectCard(title, value, fields, editKind = "") {
    const labels = { job_title: "岗位", company: "公司", salary: "薪资", location: "地点", team: "团队", raw_summary: "概况", summary: "概况", uncertain_fields: "待确认字段", full_name: "项目", language: "语言", stargazers_count: "关注", html_url: "链接" };
    const card = document.createElement("section"); card.className = "job-card";
    const head = document.createElement("div"); head.className = "job-card-head";
    const h = document.createElement("h3"); h.textContent = title; head.appendChild(h);
    if (editKind) {
      const actions = document.createElement("div"); actions.className = "job-card-actions";
      const edit = document.createElement("button"); edit.type = "button"; edit.className = "job-card-action"; edit.textContent = "编辑"; edit.addEventListener("click", () => openJobEditor(editKind, value)); actions.appendChild(edit);
      if (editKind === "profile") {
        const copy = document.createElement("button"); copy.type = "button"; copy.className = "job-card-action"; copy.textContent = "复制到新会话"; copy.addEventListener("click", () => openNewSession(state.currentSessionId)); actions.appendChild(copy);
      }
      head.appendChild(actions);
    }
    card.appendChild(head);
    const dl = document.createElement("dl"); fields.forEach((field) => { if (value[field] == null || value[field] === "") return; const dt = document.createElement("dt"); dt.textContent = labels[field] || field; const dd = document.createElement("dd"); dd.textContent = Array.isArray(value[field]) ? value[field].slice(0, 4).join("、") : String(value[field]); dl.append(dt, dd); }); if (!dl.childElementCount) { const dd = document.createElement("dd"); dd.textContent = "已保存结构化资料"; dl.appendChild(dd); } card.appendChild(dl); return card;
  }

  function jdObjectCard(value) {
    const card = document.createElement("section");
    card.className = "job-card jd-card";
    const head = document.createElement("div");
    head.className = "job-card-head";
    const h = document.createElement("h3");
    h.textContent = "岗位卡片（可编辑）";
    head.appendChild(h);
    const actions = document.createElement("div");
    actions.className = "job-card-actions";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "job-card-action";
    edit.textContent = "编辑";
    edit.addEventListener("click", () => openJobEditor("jd", value));
    actions.appendChild(edit);
    head.appendChild(actions);
    card.appendChild(head);

    const title = String(value && value.job_title || "").trim();
    const company = String(value && value.company || "").trim();
    const identity = document.createElement("div");
    identity.className = "jd-card-identity";
    if (title) {
      const jobTitle = document.createElement("strong");
      jobTitle.className = "jd-job-title";
      jobTitle.textContent = title;
      identity.appendChild(jobTitle);
    }
    if (company) {
      const companyNode = document.createElement("span");
      companyNode.className = "jd-card-company";
      companyNode.textContent = company;
      identity.appendChild(companyNode);
    }
    if (identity.children.length) card.appendChild(identity);
    const meta = [value && value.salary, value && value.location]
      .map((item) => String(item == null ? "" : item).trim()).filter(Boolean);
    if (meta.length) {
      const metaNode = document.createElement("div");
      metaNode.className = "jd-card-meta";
      metaNode.textContent = meta.join(" · ");
      card.appendChild(metaNode);
    }
    const team = String(value && value.team || "").trim();
    if (team) {
      const teamNode = document.createElement("div");
      teamNode.className = "jd-card-team";
      teamNode.textContent = team;
      card.appendChild(teamNode);
    }
    return card;
  }

  function appendJdSummary(card, value) {
    const summary = String(value && (value.raw_summary || value.summary) || "").trim();
    if (summary) {
      const summaryTitle = document.createElement("div");
      summaryTitle.className = "job-subtitle";
      summaryTitle.textContent = "概况";
      card.appendChild(summaryTitle);
      const summaryNode = document.createElement("p");
      summaryNode.className = "jd-summary";
      summaryNode.textContent = summary;
      card.appendChild(summaryNode);
    }
  }

  function appendJobList(card, label, items, formatter, { listTag = "div", listClass = "", itemClass = "job-list-item", limit = 12 } = {}) {
    if (!Array.isArray(items) || !items.length) return;
    const heading = document.createElement("div"); heading.className = "job-subtitle"; heading.textContent = label; card.appendChild(heading);
    const list = document.createElement(listTag); list.className = `job-list${listClass ? ` ${listClass}` : ""}`;
    const visibleItems = limit == null ? items : items.slice(0, limit);
    visibleItems.forEach((item) => { const value = String(formatter(item) || "").trim(); if (!value) return; const row = document.createElement(listTag === "ul" ? "li" : "div"); row.className = itemClass; row.textContent = value; list.appendChild(row); });
    if (list.children.length) card.appendChild(list);
    else heading.remove();
  }

  function parentPath(path) {
    const normalized = String(path || ".").replace(/[\\/]+$/, "");
    if (!normalized || normalized === ".") return ".";
    const index = Math.max(normalized.lastIndexOf("/"), normalized.lastIndexOf("\\"));
    return index <= 0 ? "." : normalized.slice(0, index);
  }

  function joinPath(base, name) {
    const left = !base || base === "." ? "" : String(base).replace(/[\\/]+$/, "");
    return left ? `${left}/${name}` : String(name || ".");
  }

  function openLocalImport(target = "composer") {
    if (state.newSession.open || state.modelSettings.open) return;
    state.localImportTarget = target === "planProject" ? "planProject" : "composer";
    state.localImportSessionId = String(state.currentSessionId || "new");
    const hint = $("localImportHint");
    if (hint) hint.textContent = target === "planProject"
      ? "输入本机代码、说明材料或项目目录的完整路径。支持 PDF、Office、图片和文本等可读取资料；敏感路径、超限文件和不支持的二进制会跳过。上限：最多 1000 个文件、单文件 2 MiB、总计 20 MiB。导入后会添加到复现项目材料，点击评估后读取。"
      : "输入本机简历、笔记或资料目录的完整路径。支持 PDF、Office、图片和文本等可读取资料；敏感路径、超限文件和不支持的二进制会跳过。上限：最多 1000 个文件、单文件 2 MiB、总计 20 MiB。导入后会放入当前消息，发送后读取。";
    $("localImportPath").value = "";
    setText($("localImportStatus"), "");
    $("localImportBackdrop").classList.remove("hidden");
    document.body.classList.add("modal-open");
    $("localImportClose").focus();
  }

  function closeLocalImport() {
    $("localImportBackdrop").classList.add("hidden");
    state.localImportTarget = "composer";
    state.localImportSessionId = "new";
    if (!state.modelSettings.open && !state.jobEditor.open && !state.newSession.open && !state.clarification.open && !state.workflowChat.open) document.body.classList.remove("modal-open");
  }

  const LOCAL_IMPORT_REASON_LABELS = {
    sensitive_path: "敏感路径",
    unsupported_binary: "不支持的二进制文件",
    file_too_large: "单文件超过 2 MiB",
    max_files: "文件数量达到 1000 个上限",
    max_bytes: "总大小达到 20 MiB 上限",
    symlink: "符号链接或目录联接",
    directory: "目录项未直接导入",
    excluded_directory: "敏感目录已跳过",
    unsupported_binary_or_extension: "不支持的文件类型"
  };

  function localImportSummary(payload, importedCount, skipped) {
    const parts = [`实际导入 ${importedCount} 个文件`];
    const counts = new Map();
    skipped.forEach((item) => {
      const reason = String(item && item.reason || "其他原因");
      counts.set(reason, (counts.get(reason) || 0) + 1);
    });
    const manifest = payload && payload.manifest && typeof payload.manifest === "object" ? payload.manifest : {};
    const reportedSkipped = Number(manifest.skipped_count);
    const skippedCount = Number.isFinite(reportedSkipped) ? Math.max(skipped.length, reportedSkipped) : skipped.length;
    if (counts.size) {
      const detail = [...counts.entries()].map(([reason, count]) => `${LOCAL_IMPORT_REASON_LABELS[reason] || reason} ${count} 个`).join("、");
      parts.push(`跳过 ${skippedCount} 个：${detail}`);
    } else if (skippedCount) {
      parts.push(`跳过 ${skippedCount} 个文件（服务未返回逐项原因）`);
    }
    if (payload && payload.truncated) parts.push("已触发文件数量或总大小上限");
    parts.push("上限：最多 1000 个文件、单文件 2 MiB、总计 20 MiB");
    return parts.join("；");
  }

  async function submitLocalImport(event) {
    event.preventDefault();
    const path = String($("localImportPath").value || "").trim();
    if (!path) { setText($("localImportStatus"), "请填写本地路径。"); $("localImportStatus").className = "model-settings-status error"; return; }
    const button = $("localImportSubmit");
    button.disabled = true;
    setText($("localImportStatus"), "正在导入路径…");
    try {
      const payload = await request("/imports/local", { method: "POST", body: { path } });
      if (String(state.currentSessionId || "new") !== String(state.localImportSessionId || "new")) throw new Error("会话已切换，本次导入结果未加入新会话。请在目标会话重新导入。");
      // Only paths in the explicit imported list become message attachments.
      // The response envelope may contain the original source path and must
      // never be treated as an attachment when zero files were imported.
      const imported = Array.isArray(payload && payload.imported) ? payload.imported : [];
      const skipped = Array.isArray(payload && payload.skipped) ? payload.skipped : [];
      const attachments = imported.map((item) => ({
        name: String(typeof item === "string" ? item : item && (item.name || item.path) || ""),
        path: String(typeof item === "string" ? item : item && (item.path || item.workspace_path) || ""),
        sourcePath: String(item && (item.source_path || item.sourcePath || item.original_path || item.originalPath) || ""),
        mediaType: String(item && (item.media_type || item.mediaType) || "")
      })).filter((item) => item.path);
      const summary = localImportSummary(payload, attachments.length, skipped);
      if (attachments.length) {
        const target = state.localImportTarget;
        if (target === "planProject") {
          planProjectFiles = [...planProjectFiles, ...attachments];
          renderPlanProjectResources();
        } else {
          state.uploadedAttachments = [...(state.uploadedAttachments || []), ...attachments];
          state.uploadedAttachment = state.uploadedAttachments[0] || null;
        }
        closeLocalImport();
        if (target === "planProject") {
          renderWorkflowPage();
          toast(`${summary}；已加入自有复现项目材料。`, "success", 7200);
        } else {
          renderAll();
          toast(summary, "success", 7200);
        }
      } else {
        setText($("localImportStatus"), `${summary}。没有可附加文件。`);
        $("localImportStatus").className = "model-settings-status error";
        toast(`${summary}。没有可附加文件。`, "error", 7200);
      }
    } catch (error) {
      setText($("localImportStatus"), error.message || "导入本地路径失败");
      $("localImportStatus").className = "model-settings-status error";
    } finally {
      button.disabled = false;
    }
  }

  async function loadFiles(path = ".") {
    state.filePath = path || ".";
    state.fileLoading = true; state.fileError = ""; renderInspector();
    try {
      const query = `?path=${encodeURIComponent(state.filePath)}`;
      const payload = await request(`/files${query}`);
      state.fileEntries = Array.isArray(payload && payload.entries) ? payload.entries : [];
      state.filePath = String(payload && payload.path || state.filePath);
    } catch (error) {
      state.fileEntries = []; state.fileError = error instanceof ApiError ? error.message : "读取目录失败";
    } finally { state.fileLoading = false; renderInspector(); }
  }

  async function openFile(path, label) {
    $("fileModalBackdrop").classList.remove("hidden");
    setText($("fileModalTitle"), label || path);
    setText($("fileModalMeta"), path);
    setText($("filePreview"), "读取文件…");
    try {
      const payload = await request(`/file?path=${encodeURIComponent(path)}`);
      setText($("fileModalMeta"), `${payload && payload.path ? payload.path : path}${payload && payload.truncated ? " · 内容已截断" : ""}`);
      setText($("filePreview"), String(payload && payload.content || ""));
    } catch (error) { setText($("filePreview"), error instanceof ApiError ? error.message : "读取文件失败"); }
  }

  function closeFileModal() { $("fileModalBackdrop").classList.add("hidden"); }

  function toast(message, kind = "", duration = 4200) {
    const text = String(message || "").trim();
    if (!text) return;
    const compact = text.length > 640 ? `${text.slice(0, 640)}…` : text;
    const item = document.createElement("div"); item.className = `toast ${kind}`;
    item.appendChild(icon(kind === "error" ? "warning" : kind === "success" ? "check" : "spark", "toast-icon")); item.appendChild(document.createTextNode(compact));
    $("toastRegion").appendChild(item); hydrateIcons(item);
    window.setTimeout(() => item.remove(), duration);
  }

  function modelSettingsMessage(value, apiKey = "") {
    let message = redactDisplay(value || "");
    if (apiKey) message = message.split(apiKey).join("[已隐藏凭据]");
    return message;
  }

  function modelTestMessage(payload, apiKey = "") {
    const code = String(payload && (payload.code || payload.error || "") || "");
    const labels = {
      missing_api_key: "未提供 API Key；远程模型需要密钥。",
      authentication_failed: "认证失败，请检查 API Key。",
      balance_insufficient: "账户余额不足或额度已用尽，请检查服务商账单。",
      invalid_request: "请求参数或模型名称无效，请检查协议、地址和模型。",
      invalid_model: "模型名称无效或当前服务不提供该模型，请检查模型 ID。",
      endpoint_failed: "API 地址或模型端点不可用。",
      rate_limited: "请求受到限流，请稍后重试。",
      service_unavailable: "模型服务暂时不可用，请稍后重试。",
      timeout: "模型连接超时，请检查网络或稍后重试。",
      connection_failed: "连接失败，请检查地址和网络。",
      length_truncated: "模型响应因输出长度限制被截断。",
      tool_unsupported: "模型服务不支持工具调用，或未按要求返回探测工具。",
      no_response: "模型未返回可用的连接探测响应。",
      tool_call_failed: "模型已响应，但未完成连接探测。"
    };
    const fallback = "模型连接测试失败，请检查设置或稍后重试。";
    const mapped = labels[code] || fallback;
    // The backend only emits fixed messages.  Accept a longer server message
    // when it starts with the known fixed label (for example, the friendly
    // DeepSeek model-name hint); never display an unknown SDK response body.
    const serverMessage = payload && typeof payload.message === "string" ? payload.message : "";
    const safe = serverMessage && (serverMessage === mapped || serverMessage.startsWith(`${mapped} `)) ? serverMessage : mapped;
    return modelSettingsMessage(safe, apiKey);
  }

  function savedModelSettingsWarning(value) {
    const labels = {
      saved_key_unavailable: "已保存的 API Key 无法读取，请重新输入后保存。",
      saved_settings_invalid: "已保存的模型设置无效，请重新填写并保存。",
      saved_settings_unavailable: "已保存的模型设置暂时不可用，请重新填写并保存。"
    };
    return labels[String(value || "")] || (value ? "当前模型设置需要重新输入，请检查后保存。" : "");
  }

  function setModelSettingsStatus(message, kind = "") {
    const node = $("modelSettingsStatus");
    if (!node) return;
    node.className = `model-settings-status${kind ? ` ${kind}` : ""}`;
    setText(node, message);
    node.setAttribute("aria-busy", kind === "loading" ? "true" : "false");
  }

  function modelHasApiKey(value) {
    return value === true || value === 1 || value === "1" || String(value || "").toLowerCase() === "true";
  }

  function updateModelKeyControls(hasApiKey) {
    state.modelSettings.hasApiKey = Boolean(hasApiKey);
    const input = $("modelApiKey");
    const hint = $("modelApiKeyHint");
    const clearLabel = $("modelClearKeyLabel");
    const clear = $("modelClearKey");
    if (input) input.placeholder = hasApiKey ? "已保存 API Key；留空则保留，输入新密钥后替换" : "未保存；请输入 API Key（可留空）";
    if (hint) setText(hint, hasApiKey ? "检测到已保存密钥；留空会保留，勾选下方选项才会清除。" : "密钥只在提交时发送，不会保存到浏览器或显示在页面中。");
    if (clearLabel) clearLabel.classList.toggle("hidden", !hasApiKey);
    if (clear) clear.checked = false;
  }

  function resetModelSettingsForm() {
    const form = $("modelSettingsForm");
    if (form) form.reset();
    state.modelSettings.currentProfileId = "";
    state.modelSettings.currentProfileName = "";
    state.modelSettings.profileOptions = [];
    state.modelSettings.loadedIdentity = null;
    state.modelSettings.newProfile = false;
    if ($("modelProfileName")) $("modelProfileName").value = "";
    if ($("modelApiKey")) $("modelApiKey").value = "";
    if ($("modelApiMode")) $("modelApiMode").value = "chat_completions";
    if ($("modelBaseUrl")) $("modelBaseUrl").value = "";
    if ($("modelName")) $("modelName").value = "";
    if ($("modelChoice")) { $("modelChoice").replaceChildren(); $("modelChoiceField").classList.add("hidden"); }
    if ($("modelProfileList")) $("modelProfileList").replaceChildren();
    updateModelKeyControls(state.modelSettings.hasApiKey);
    setModelSettingsStatus("");
  }

  function normalizedModelBaseUrl(value) {
    return String(value || "").trim().replace(/\/+$/, "");
  }

  function sameModelIdentity(left, right) {
    return Boolean(left && right)
      && String(left.api_mode || "") === String(right.api_mode || "")
      && normalizedModelBaseUrl(left.base_url) === normalizedModelBaseUrl(right.base_url)
      && String(left.model || "").trim() === String(right.model || "").trim();
  }

  function modelSettingsDraft() {
    const apiKey = $("modelApiKey").value || "";
    const draft = {
      api_mode: String($("modelApiMode").value || "chat_completions"),
      base_url: String($("modelBaseUrl").value || "").trim(),
      model: String($("modelName").value || "").trim()
    };
    const displayName = String($("modelProfileName") && $("modelProfileName").value || "").trim();
    const identity = { api_mode: draft.api_mode, base_url: draft.base_url, model: draft.model };
    if (displayName) draft.display_name = displayName;
    // A profile id is safe only while the connection identity remains the
    // active profile's identity. Changing endpoint/model creates a new
    // profile so the old profile keeps its key and route.
    if (state.modelSettings.currentProfileId && !state.modelSettings.newProfile
      && sameModelIdentity(identity, state.modelSettings.loadedIdentity)) {
      draft.profile_id = state.modelSettings.currentProfileId;
    }
    if (apiKey.length) draft.api_key = apiKey;
    if ($("modelClearKey").checked || (state.modelSettings.newProfile && !apiKey.trim())) draft.clear_api_key = true;
    return draft;
  }

  function validateModelSettings(draft) {
    if (!["chat_completions", "anthropic_messages"].includes(draft.api_mode)) {
      setModelSettingsStatus("请选择支持的协议。", "error");
      $("modelApiMode").focus();
      return false;
    }
    if (!draft.base_url) {
      setModelSettingsStatus("请填写 API 地址。", "error");
      $("modelBaseUrl").focus();
      return false;
    }
    try {
      const parsed = new URL(draft.base_url);
      if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("protocol");
    } catch (_error) {
      setModelSettingsStatus("API 地址需是以 http:// 或 https:// 开头的有效地址。", "error");
      $("modelBaseUrl").focus();
      return false;
    }
    if (!draft.model) {
      setModelSettingsStatus("请填写模型名称。", "error");
      $("modelName").focus();
      return false;
    }
    if (draft.clear_api_key && draft.api_key) {
      setModelSettingsStatus("已勾选清除密钥，请清空 API Key 输入框，或取消勾选。", "error");
      $("modelApiKey").focus();
      return false;
    }
    return true;
  }

  function setModelSettingsBusy(action, serial = state.modelSettings.requestSerial) {
    if (serial !== state.modelSettings.requestSerial) return;
    state.modelSettings.busyAction = action || "";
    qsa("#modelSettingsForm input, #modelSettingsForm select, #modelSettingsForm button").forEach((control) => {
      control.disabled = control.id === "modelSettingsCancel" ? action === "saving" : Boolean(action);
    });
    const closeButton = $("modelSettingsClose");
    if (closeButton) closeButton.disabled = action === "saving";
    const testButton = $("modelTestButton");
    const saveButton = $("modelSaveButton");
    if (testButton) testButton.textContent = action === "testing" ? "测试中…" : action === "selecting" ? "切换中…" : "测试连接";
    if (saveButton) saveButton.textContent = action === "saving" ? "保存中…" : "保存并应用";
  }

  function modelSettingsPayload(payload) {
    if (payload && payload.settings && typeof payload.settings === "object") return payload.settings;
    return payload && typeof payload === "object" ? payload : {};
  }

  function setGitHubStatus(message, kind = "") {
    const node = $("githubTokenStatus");
    if (!node) return;
    node.className = `model-settings-status${kind ? ` ${kind}` : ""}`;
    setText(node, message);
  }

  function updateGitHubTokenControls(hasToken) {
    state.github.hasToken = Boolean(hasToken);
    const input = $("githubToken");
    const hint = $("githubTokenHint");
    const clearLabel = $("githubClearTokenLabel");
    const clear = $("githubClearToken");
    if (input) input.placeholder = hasToken ? "已保存 GitHub Token；留空则保留" : "未保存；请输入 GitHub Token（可留空）";
    if (hint) setText(hint, hasToken ? "检测到已保存 Token；用于读取你有权限的私有仓库并提高公开仓库搜索额度；不会发给模型。留空会保留，勾选下方选项才会清除。" : "用于读取你有权限的私有仓库并提高公开仓库搜索额度；不会发给模型。");
    if (clearLabel) clearLabel.classList.toggle("hidden", !hasToken);
    if (clear) clear.checked = false;
  }

  async function loadGitHubToken() {
    setGitHubStatus("正在读取 GitHub Token 状态…", "loading");
    try {
      const payload = await request("/settings/github-token");
      const value = payload && (payload.has_token ?? payload.hasToken);
      updateGitHubTokenControls(value === true || value === 1 || value === "1" || String(value || "").toLowerCase() === "true");
      setGitHubStatus("");
    } catch (error) {
      updateGitHubTokenControls(false);
      setGitHubStatus(error.status === 404 ? "当前服务暂不支持 GitHub Token 设置。" : "无法读取 Token 状态。", error.status === 404 ? "" : "error");
    }
  }

  async function saveGitHubToken() {
    if (state.github.busy) return;
    const input = $("githubToken");
    const clear = $("githubClearToken");
    const token = String(input && input.value || "").trim();
    if (clear && clear.checked && token) {
      setGitHubStatus("已勾选清除 Token，请清空输入框或取消勾选。", "error");
      return;
    }
    if (!token && !(clear && clear.checked)) {
      setGitHubStatus(state.github.hasToken ? "未输入新 Token，现有设置保持不变。" : "请输入 GitHub Token，或勾选清除 Token。", state.github.hasToken ? "" : "error");
      return;
    }
    state.github.busy = true;
    if (input) input.disabled = true;
    if (clear) clear.disabled = true;
    $("githubTokenSave").disabled = true;
    setGitHubStatus("正在保存 GitHub Token…", "loading");
    try {
      const payload = await request("/settings/github-token", {
        method: "POST",
        // The agreed browser contract names the flag clear_token; keep the
        // short clear alias while the service rollout is mixed so both
        // versions of the endpoint handle the same user action.
        body: {
          ...(token ? { token } : {}),
          ...(clear && clear.checked ? { clear_token: true, clear: true } : {})
        }
      });
      const hasToken = payload && (payload.has_token ?? payload.hasToken);
      updateGitHubTokenControls(hasToken === true || hasToken === 1 || hasToken === "1" || String(hasToken || "").toLowerCase() === "true");
      if (input) input.value = "";
      setGitHubStatus("GitHub Token 已保存。", "success");
      toast("GitHub Token 设置已更新。", "success");
    } catch (error) {
      setGitHubStatus(error.status === 404 ? "当前服务暂不支持 GitHub Token 设置。" : `保存失败：${error.message || "无法保存 Token"}`, "error");
    } finally {
      state.github.busy = false;
      if (input) input.disabled = false;
      if (clear) clear.disabled = false;
      $("githubTokenSave").disabled = false;
    }
  }

  function normalizeModelOption(option) {
    const object = option && typeof option === "object" ? option : {};
    const profileId = String(object.profile_id || object.profileId || "").trim();
    const provider = String(object.provider || "").trim();
    const model = String(object.model || (!profileId ? (object.id || object.name || option || "") : "")).trim();
    if (!model) return null;
    const displayName = String(object.display_name || object.label || (profileId ? object.name || "" : "")).trim();
    const text = profileId
      ? `${displayName || model}${provider ? ` · ${provider}` : ""} · ${model}`
      : provider ? `${provider} · ${model}` : model;
    const value = profileId
      ? JSON.stringify({ profile_id: profileId })
      : provider ? JSON.stringify({ provider, model }) : model;
    return {
      profileId,
      displayName,
      provider,
      model,
      text,
      value,
      apiMode: String(object.api_mode || ""),
      baseUrl: String(object.base_url || ""),
      hasApiKey: object.has_api_key == null ? null : modelHasApiKey(object.has_api_key)
    };
  }

  function renderModelProfileList(options, currentProfileId) {
    const list = $("modelProfileList");
    if (!list) return;
    list.replaceChildren();
    const profiles = options.filter((item) => item.profileId);
    list.classList.toggle("hidden", profiles.length === 0);
    profiles.forEach((profile) => {
      const row = document.createElement("div");
      row.className = `model-profile-row${profile.profileId === currentProfileId ? " current" : ""}`;
      const summary = document.createElement("div");
      summary.className = "model-profile-summary";
      const name = document.createElement("strong");
      name.className = "model-profile-name";
      name.textContent = profile.displayName || profile.model;
      const detail = document.createElement("span");
      detail.className = "model-profile-detail";
      detail.textContent = `${profile.model}${profile.baseUrl ? ` · ${profile.baseUrl}` : ""}`;
      summary.append(name, detail);
      row.appendChild(summary);
      if (profile.profileId === currentProfileId) {
        const current = document.createElement("span");
        current.className = "model-profile-current";
        current.textContent = "当前";
        row.appendChild(current);
      } else {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "action-button secondary model-profile-delete";
        remove.textContent = "删除";
        remove.setAttribute("aria-label", `删除模型配置 ${profile.displayName || profile.model}`);
        remove.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          void deleteModelProfile(profile.profileId, profile.displayName || profile.model);
        });
        row.appendChild(remove);
      }
      list.appendChild(row);
    });
  }

  async function loadModelSettings(serial) {
    setModelSettingsBusy("loading", serial);
    setModelSettingsStatus("正在读取当前模型设置…", "loading");
    try {
      const [rawSettings, rawOptions] = await Promise.all([
        request(MODEL_SETTINGS_PATH),
        request(`${MODEL_SETTINGS_PATH}/options`).catch(() => null)
      ]);
      const settings = modelSettingsPayload(rawSettings);
      const optionPayload = rawOptions && typeof rawOptions === "object" ? rawOptions : {};
      if (serial !== state.modelSettings.requestSerial || !state.modelSettings.open) return;
      const mode = String(settings.api_mode || "chat_completions");
      $("modelApiMode").value = ["chat_completions", "anthropic_messages"].includes(mode) ? mode : "chat_completions";
      $("modelBaseUrl").value = String(settings.base_url || "");
      $("modelName").value = String(settings.model || "");
      // The server intentionally returns only has_api_key. Never put a key in
      // the form, even if a proxy accidentally includes an api_key field.
      $("modelApiKey").value = "";
      updateModelKeyControls(modelHasApiKey(settings.has_api_key));
      const declaredOptions = Array.isArray(settings.options)
        ? settings.options
        : Array.isArray(settings.models)
          ? settings.models
          : Array.isArray(optionPayload.options) ? optionPayload.options : [];
      const savedOptions = Array.isArray(optionPayload.saved_profiles)
        ? optionPayload.saved_profiles
        : Array.isArray(optionPayload.profiles) ? optionPayload.profiles : [];
      const declared = declaredOptions.map(normalizeModelOption).filter(Boolean);
      const saved = savedOptions.map(normalizeModelOption).filter(Boolean);
      const savedIdentities = new Set(saved.map((item) => `${item.apiMode}|${normalizedModelBaseUrl(item.baseUrl)}|${item.model}`));
      // When no YAML router exists, the backend exposes the active GUI router
      // as a synthetic provider=gui option. A saved profile already represents
      // that same connection, so showing both would make switching ambiguous.
      const filteredDeclared = declared.filter((item) => (
        !(item.provider === "gui" && savedIdentities.has(`${item.apiMode}|${normalizedModelBaseUrl(item.baseUrl)}|${item.model}`))
      ));
      const seen = new Set();
      const options = [...filteredDeclared, ...saved]
        .filter((item) => {
          if (!item) return false;
          const key = item.profileId ? `profile:${item.profileId}` : `declared:${item.provider}:${item.model}`;
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
      state.modelSettings.profileOptions = options;
      const current = (settings.current && typeof settings.current === "object" ? settings.current : null)
        || (optionPayload.current && typeof optionPayload.current === "object" ? optionPayload.current : null)
        || {};
      const currentProfileId = String(optionPayload.current_profile_id || settings.current_profile_id || "").trim();
      const currentProfile = options.find((item) => item.profileId === currentProfileId) || null;
      const currentProvider = String(current.provider || settings.provider || "").trim();
      const currentModel = String(current.model || settings.model || "").trim();
      const selectedProfileId = currentProfile ? currentProfile.profileId : "";
      state.modelSettings.currentProfileId = selectedProfileId;
      state.modelSettings.currentProfileName = String((currentProfile && currentProfile.displayName) || settings.display_name || "");
      state.modelSettings.newProfile = false;
      state.modelSettings.loadedIdentity = {
        api_mode: $("modelApiMode").value,
        base_url: $("modelBaseUrl").value,
        model: $("modelName").value
      };
      if ($("modelProfileName")) $("modelProfileName").value = state.modelSettings.currentProfileName;
      const choiceField = $("modelChoiceField");
      const choice = $("modelChoice");
      if (choiceField && choice) {
        choice.replaceChildren();
        options.forEach((option) => {
          const item = document.createElement("option");
          item.value = option.value;
          item.textContent = option.text;
          if (option.profileId) item.dataset.profileId = option.profileId;
          if (option.provider) item.dataset.provider = option.provider;
          item.dataset.model = option.model;
          if (option.apiMode) item.dataset.apiMode = option.apiMode;
          if (option.baseUrl) item.dataset.baseUrl = option.baseUrl;
          if (option.hasApiKey != null) item.dataset.hasApiKey = option.hasApiKey ? "true" : "false";
          if (option.displayName) item.dataset.displayName = option.displayName;
          choice.appendChild(item);
        });
        choiceField.classList.toggle("hidden", choice.childElementCount === 0);
        const currentOption = selectedProfileId
          ? Array.from(choice.options).find((item) => item.dataset.profileId === selectedProfileId)
          : Array.from(choice.options).find((item) => currentProvider && item.dataset.provider === currentProvider && item.dataset.model === currentModel)
            || Array.from(choice.options).find((item) => item.dataset.model === currentModel || item.value === currentModel);
        if (currentOption) choice.value = currentOption.value;
      }
      renderModelProfileList(options, selectedProfileId);
      await loadGitHubToken();
      setModelSettingsStatus(savedModelSettingsWarning(state.info && state.info.settings_error), state.info && state.info.settings_error ? "error" : "");
    } catch (error) {
      if (serial === state.modelSettings.requestSerial && state.modelSettings.open) {
        setModelSettingsStatus(`无法读取当前模型设置：${modelSettingsMessage(error.message || "服务暂不可用")}`, "error");
      }
    } finally {
      setModelSettingsBusy("", serial);
      if (serial === state.modelSettings.requestSerial && state.modelSettings.open) $("modelApiMode").focus();
    }
  }

  async function selectConfiguredModel(option) {
    const profileId = String(option && option.dataset && option.dataset.profileId || "").trim();
    const provider = String(option && option.dataset && option.dataset.provider || "").trim();
    const model = String(option && option.dataset && option.dataset.model || option && option.value || "").trim();
    if (!profileId && (!provider || !model)) {
      $("modelName").value = model;
      if (option && option.dataset && ["chat_completions", "anthropic_messages"].includes(option.dataset.apiMode || "")) $("modelApiMode").value = option.dataset.apiMode;
      if (option && option.dataset && option.dataset.baseUrl) $("modelBaseUrl").value = option.dataset.baseUrl;
      setModelSettingsStatus("已填入模型连接字段；修改后点击“保存并应用”。");
      return;
    }
    if (state.modelSettings.busyAction) return;
    const serial = ++state.modelSettings.requestSerial;
    setModelSettingsBusy("selecting", serial);
    setModelSettingsStatus("正在切换已配置模型…", "loading");
    try {
      const payload = await request(`${MODEL_SETTINGS_PATH}/select`, {
        method: "POST",
        body: profileId ? { profile_id: profileId } : { provider, model }
      });
      if (serial !== state.modelSettings.requestSerial || !state.modelSettings.open) return;
      const current = payload && payload.current && typeof payload.current === "object" ? payload.current : {};
      const apiMode = String(option.dataset.apiMode || current.api_mode || "chat_completions");
      const baseUrl = String(option.dataset.baseUrl || current.base_url || $("modelBaseUrl").value || "");
      $("modelApiMode").value = ["chat_completions", "anthropic_messages"].includes(apiMode) ? apiMode : "chat_completions";
      $("modelBaseUrl").value = baseUrl;
      $("modelName").value = String(current.model || model);
      $("modelApiKey").value = "";
      const hasApiKey = current.has_api_key != null ? modelHasApiKey(current.has_api_key) : modelHasApiKey(option.dataset.hasApiKey);
      updateModelKeyControls(hasApiKey);
      state.modelSettings.currentProfileId = profileId;
      state.modelSettings.currentProfileName = String(option.dataset.displayName || "");
      state.modelSettings.newProfile = false;
      state.modelSettings.loadedIdentity = { api_mode: $("modelApiMode").value, base_url: baseUrl, model: $("modelName").value };
      if ($("modelProfileName")) $("modelProfileName").value = state.modelSettings.currentProfileName;
      renderModelProfileList(state.modelSettings.profileOptions, profileId);
      const label = profileId
        ? `配置“${state.modelSettings.currentProfileName || model}”`
        : `${provider} · ${model}`;
      setModelSettingsStatus(`已切换到 ${label}。${profileId ? "已恢复该配置的密钥和连接字段。" : "已切回配置文件中的 Provider 路由。"}`, "success");
      await refreshInfo();
    } catch (error) {
      if (serial === state.modelSettings.requestSerial && state.modelSettings.open) {
        const message = error.status === 404 || error.status === 405
          ? "当前服务暂不支持已配置模型即时切换，请编辑连接字段后保存。"
          : `切换失败：${error.message || "无法应用已配置模型"}`;
        setModelSettingsStatus(message, "error");
      }
    } finally {
      setModelSettingsBusy("", serial);
    }
  }

  function startNewModelProfile() {
    if (state.modelSettings.busyAction) return;
    state.modelSettings.currentProfileId = "";
    state.modelSettings.currentProfileName = "";
    state.modelSettings.loadedIdentity = null;
    state.modelSettings.newProfile = true;
    if ($("modelProfileName")) $("modelProfileName").value = "";
    if ($("modelApiMode")) $("modelApiMode").value = "chat_completions";
    if ($("modelBaseUrl")) $("modelBaseUrl").value = "";
    if ($("modelName")) $("modelName").value = "";
    if ($("modelApiKey")) $("modelApiKey").value = "";
    if ($("modelClearKey")) $("modelClearKey").checked = false;
    if ($("modelChoice")) $("modelChoice").selectedIndex = -1;
    updateModelKeyControls(false);
    setModelSettingsStatus("新建配置草稿已清空；已有配置不会被删除。填写连接字段后保存即可。", "success");
    $("modelProfileName")?.focus();
  }

  async function deleteModelProfile(profileId, displayName) {
    const id = String(profileId || "").trim();
    if (!id || id === state.modelSettings.currentProfileId) {
      toast("当前正在使用的配置不能删除。", "error");
      return;
    }
    if (state.modelSettings.busyAction) return;
    if (!window.confirm(`确定删除模型配置“${displayName || id}”吗？此操作不会影响当前正在使用的配置。`)) return;
    const serial = ++state.modelSettings.requestSerial;
    setModelSettingsBusy("deleting", serial);
    setModelSettingsStatus("正在删除模型配置…", "loading");
    try {
      await request(`${MODEL_SETTINGS_PATH}/delete`, {
        method: "POST",
        body: { profile_id: id, confirm: true }
      });
      if (serial !== state.modelSettings.requestSerial || !state.modelSettings.open) return;
      setModelSettingsStatus("模型配置已删除。", "success");
      await loadModelSettings(serial);
      await refreshGlobalModels();
      toast("模型配置已删除。", "success");
    } catch (error) {
      if (serial === state.modelSettings.requestSerial && state.modelSettings.open) setModelSettingsStatus(`删除失败：${error.message || "无法删除模型配置"}`, "error");
    } finally {
      setModelSettingsBusy("", serial);
    }
  }

  function openModelSettings() {
    if (state.modelSettings.open || state.modelSettings.busyAction) return;
    state.modelSettings.open = true;
    state.modelSettings.previousFocus = document.activeElement;
    resetModelSettingsForm();
    $("modelSettingsBackdrop").classList.remove("hidden");
    document.body.classList.add("modal-open");
    const serial = ++state.modelSettings.requestSerial;
    loadModelSettings(serial);
    $("modelSettingsClose").focus();
  }

  function closeModelSettings() {
    if (!state.modelSettings.open) return;
    if (state.modelSettings.busyAction === "saving") {
      setModelSettingsStatus("正在保存模型设置，请稍候…", "loading");
      return;
    }
    state.modelSettings.open = false;
    ++state.modelSettings.requestSerial;
    setModelSettingsBusy("");
    // Clear the secret as soon as the dialog closes. It is never put in
    // localStorage/sessionStorage and is not included in any diagnostic text.
    if ($("modelApiKey")) $("modelApiKey").value = "";
    if ($("modelClearKey")) $("modelClearKey").checked = false;
    $("modelSettingsBackdrop").classList.add("hidden");
    if (!state.workflowChat.open && !state.newSession.open && !state.jobEditor.open && !state.clarification.open) document.body.classList.remove("modal-open");
    const previousFocus = state.modelSettings.previousFocus;
    state.modelSettings.previousFocus = null;
    restoreSidebarAwareFocus(previousFocus);
  }

  function trapModelSettingsFocus(event) {
    if (event.key !== "Tab") return;
    const backdrop = state.clarification.open ? $("clarificationBackdrop") : state.newSession.open ? $("newSessionBackdrop") : state.modelSettings.open ? $("modelSettingsBackdrop") : state.workflowChat.open ? $("workflowChatBackdrop") : null;
    if (!backdrop) return;
    const dialog = backdrop.querySelector('[role="dialog"]');
    const focusable = qsa("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])", dialog).filter((node) => node.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  async function testModelSettings() {
    if (state.modelSettings.busyAction) return;
    const draft = modelSettingsDraft();
    if (!validateModelSettings(draft)) return;
    const serial = ++state.modelSettings.requestSerial;
    setModelSettingsBusy("testing", serial);
    setModelSettingsStatus("正在测试连接…", "loading");
    try {
      const payload = await request(`${MODEL_SETTINGS_PATH}/test`, { method: "POST", body: draft });
      if (serial !== state.modelSettings.requestSerial || !state.modelSettings.open) return;
      const message = modelTestMessage(payload, draft.api_key);
      if (payload && payload.ok === true) setModelSettingsStatus(message || "连接成功。", "success");
      else setModelSettingsStatus(`测试失败：${message || "服务未确认连接成功。"}`, "error");
    } catch (error) {
      if (serial === state.modelSettings.requestSerial && state.modelSettings.open) setModelSettingsStatus(`测试失败：${modelSettingsMessage(error.message || "无法连接服务", draft.api_key)}`, "error");
    } finally {
      setModelSettingsBusy("", serial);
    }
  }

  async function saveModelSettings(event) {
    event.preventDefault();
    if (state.modelSettings.busyAction) return;
    const draft = modelSettingsDraft();
    if (!validateModelSettings(draft)) return;
    const serial = ++state.modelSettings.requestSerial;
    setModelSettingsBusy("saving", serial);
    setModelSettingsStatus("正在保存并应用模型设置…", "loading");
    try {
      const payload = await request(MODEL_SETTINGS_PATH, { method: "POST", body: draft });
      if (serial !== state.modelSettings.requestSerial || !state.modelSettings.open) return;
      const keyWasSent = Boolean(draft.api_key);
      updateModelKeyControls(draft.clear_api_key ? false : (keyWasSent || state.modelSettings.hasApiKey));
      $("modelApiKey").value = "";
      // Re-read the catalog so a newly created profile gets its server
      // assigned id and the non-current delete controls stay accurate.
      await loadModelSettings(serial);
      setModelSettingsStatus("模型设置已保存；所有岗位会话的后续消息将使用当前配置。", "success");
      // /info is the source of truth for the header badge. Refresh it after
      // saving so demo/unconfigured/configured state cannot become stale.
      await refreshInfo();
      toast(modelSettingsMessage((payload && payload.message) || "模型设置已保存；所有岗位会话的后续消息将使用当前配置。", draft.api_key), "success");
    } catch (error) {
      if (serial === state.modelSettings.requestSerial && state.modelSettings.open) setModelSettingsStatus(`保存失败：${modelSettingsMessage(error.message || "无法保存模型设置", draft.api_key)}`, "error");
    } finally {
      setModelSettingsBusy("", serial);
    }
  }

  function showTokenNotice() {
    $("tokenNotice").classList.toggle("hidden", Boolean(state.token || state.dismissTokenNotice));
  }

  let globalModelSerial = 0;
  let globalModelBusy = false;
  let globalModelCurrent = "";
  async function refreshGlobalModels() {
    if (globalModelBusy) return;
    const serial = ++globalModelSerial;
    const select = $("globalModelChoice");
    select.disabled = true;
    setText($("globalModelStatus"), "正在加载…");
    try {
      const [raw, payload] = await Promise.all([request(MODEL_SETTINGS_PATH), request(`${MODEL_SETTINGS_PATH}/options`)]);
      if (serial !== globalModelSerial) return;
      const settings = modelSettingsPayload(raw);
      const optionPayload = payload && typeof payload === "object" ? payload : {};
      const profiles = (optionPayload.saved_profiles || optionPayload.profiles || []).map(normalizeModelOption).filter((item) => item && item.profileId);
      const current = String(optionPayload.current_profile_id || settings.current_profile_id || "");
      select.replaceChildren();
      globalModelCurrent = profiles.some((profile) => profile.profileId === current) ? current : "";
      if (profiles.length) {
        profiles.forEach((profile) => {
          const option = document.createElement("option"); option.value = profile.profileId;
          option.textContent = profile.displayName || profile.model; select.appendChild(option);
        });
        if (globalModelCurrent) select.value = globalModelCurrent;
        else select.selectedIndex = -1;
        // A YAML/provider route may be active without a saved GUI profile. Do
        // not invent a selected profile or silently select the first saved one.
        setText($("globalModelStatus"), globalModelCurrent ? "所有会话共用" : "当前使用未保存配置；可在设置中保存并命名");
        select.disabled = false;
      } else {
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "尚无已保存 API 配置 · 请打开设置";
        placeholder.disabled = true;
        placeholder.selected = true;
        select.appendChild(placeholder);
        select.disabled = true;
        setText($("globalModelStatus"), "请在设置中添加并命名 API");
      }
    } catch (error) {
      if (serial !== globalModelSerial) return;
      select.replaceChildren();
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "无法读取已保存 API 配置 · 请刷新";
      placeholder.disabled = true;
      placeholder.selected = true;
      select.appendChild(placeholder);
      select.disabled = true;
      setText($("globalModelStatus"), "读取配置失败，请刷新重试");
    }
  }

  async function switchGlobalModel() {
    const select = $("globalModelChoice");
    if (globalModelBusy || state.modelSettings.busyAction || !select.value) { select.value = globalModelCurrent; return; }
    const previous = globalModelCurrent;
    ++globalModelSerial;
    globalModelBusy = true; select.disabled = true;
    $("modelSettingsButton").disabled = true;
    setText($("globalModelStatus"), "正在切换所有会话的 API…");
    try {
      await request(`${MODEL_SETTINGS_PATH}/select`, { method: "POST", body: { profile_id: select.value } });
      state.info = await request("/info"); updateMode();
    } catch (error) { select.value = previous; toast(`切换失败：${error.message || "请重试"}`, "error"); }
    finally {
      globalModelBusy = false; $("modelSettingsButton").disabled = false;
      await refreshGlobalModels();
    }
  }

  async function refreshInfo() {
    try { state.info = await request("/info"); }
    catch (error) { state.info = { mode: "error", workspace: "服务暂不可用", skills: [] }; if (error.status === 401) state.dismissTokenNotice = false; }
    updateMode(); showTokenNotice(); await refreshGlobalModels();
  }

  async function refreshSessions() {
    try {
      const payload = await request("/sessions");
      state.sessions = sessionRows(payload);
      renderSessions();
    } catch (error) {
      if (error.status === 401 || error.status === 403) showTokenNotice();
      else toast(error.message || "读取会话失败", "error");
    }
  }

  function openNewSession(sourceId = "") {
    if (state.newSession.open || state.newSession.busy) return;
    closeSidebarDrawer();
    state.newSession.open = true;
    state.newSession.previousFocus = document.activeElement;
    const select = $("copySessionSource");
    select.replaceChildren();
    const blank = document.createElement("option"); blank.value = ""; blank.textContent = "不复制，空白档案"; select.appendChild(blank);
    state.sessions.filter((summary) => summary.has_profile === true).forEach((summary) => {
      const option = document.createElement("option");
      option.value = String(summary.id || "");
      option.textContent = sessionTitle(summary);
      if (option.value) select.appendChild(option);
    });
    select.value = sourceId && [...select.options].some((option) => option.value === String(sourceId)) ? String(sourceId) : "";
    $("newSessionCreate").textContent = select.value ? "复制档案并新建" : "新建岗位会话";
    setText($("newSessionStatus"), "");
    $("newSessionBackdrop").classList.remove("hidden");
    document.body.classList.add("modal-open");
    $("newSessionClose").focus();
  }

  function closeNewSession() {
    if (!state.newSession.open || state.newSession.busy) return;
    state.newSession.open = false;
    $("newSessionBackdrop").classList.add("hidden");
    if (!state.modelSettings.open && !state.jobEditor.open && !state.clarification.open && !state.workflowChat.open) document.body.classList.remove("modal-open");
    const previous = state.newSession.previousFocus;
    state.newSession.previousFocus = null;
    restoreSidebarAwareFocus(previous);
  }

  async function createSession(sourceId = "") {
    if (state.loadingSession || state.newSession.busy) return;
    state.newSession.busy = true;
    if (state.newSession.open) {
      setText($("newSessionStatus"), "正在创建会话…");
      $("newSessionCreate").disabled = true;
    }
    try {
      const body = sourceId ? { source_session_id: String(sourceId) } : {};
      const payload = await request("/sessions", { method: "POST", body });
      const session = unwrapSession(payload);
      if (!session || !session.id) throw new ApiError("服务没有返回新会话");
      captureComposerDraft(state.workflowChat.scope);
      clearPolling(); stopVoiceInput({ cancel: true }); closeWorkflowChatDialog({ restore: false });
      clearCommandRuntime();
      state.optimisticMessage = null; state.practice.optimisticMessage = null; state.practice.sessionId = String(session.id); state.practice.projectKey = ""; state.practice.projectTitle = ""; state.practice.threadId = ""; state.practice.messages = []; state.practice.events = []; state.practice.loaded = false; state.practice.error = ""; state.practice.startPending = false; state.uploadedAttachment = null; state.uploadedAttachments = [];
      restoreComposerDraft("main", session.id, "");
      state.currentSessionId = String(session.id); state.currentSession = session; persistCurrentSession();
      state.sessions = [{ ...session, has_profile: Boolean(session.skill_state?.["job-radar"]?.profile), message_count: visibleMessages(session).length }, ...state.sessions.filter((row) => String(row.id) !== String(session.id))];
      if (state.newSession.open) { state.newSession.busy = false; closeNewSession(); }
      renderAll({ scroll: true });
      startCommandRuntimeRefresh();
      await loadFiles(".");
      return String(session.id);

    } catch (error) {
      toast(error.message || "新建会话失败", "error");
      if (state.newSession.open) setText($("newSessionStatus"), error.message || "新建会话失败");
    } finally {
      state.newSession.busy = false;
      if ($("newSessionCreate")) $("newSessionCreate").disabled = false;
    }
  }

  function submitNewSession(event) {
    event.preventDefault();
    if (state.newSession.busy) return;
    const sourceId = String($("copySessionSource").value || "");
    void createSession(sourceId);
  }

  async function selectSession(id, { restore = false } = {}) {
    const target = String(id || ""); if (!target) return;
    if (state.jobEditor.open && state.jobEditor.sessionId !== target) closeJobEditor();
    captureComposerDraft(state.workflowChat.scope);
    closeClarificationModal({ restore: false });
    closeWorkflowChatDialog({ restore: false });
    clearPolling();
    clearCommandRuntime();
    stopVoiceInput({ cancel: true });
    state.currentSessionId = target; state.currentSession = null; state.loadingSession = true; state.optimisticMessage = null; state.practice.optimisticMessage = null; state.practice.sessionId = target; state.practice.projectKey = ""; state.practice.projectTitle = ""; state.practice.threadId = ""; state.practice.messages = []; state.practice.events = []; state.practice.loaded = false; state.practice.loading = false; state.practice.error = ""; state.practice.startPending = false; state.workflowChat.scope = "main"; state.workflowChat.practiceProjectKey = ""; state.uploadedAttachment = null; state.uploadedAttachments = []; persistCurrentSession();
    renderAll();
    const serial = ++state.sessionRequestSerial;
    try {
      const session = unwrapSession(await request(`/sessions/${encodeURIComponent(target)}`));
      if (serial !== state.sessionRequestSerial || String(state.currentSessionId) !== target) return;
      state.currentSession = session || { id: target, messages: [] };
      state.loadingSession = false;
      restoreComposerDraft("main", target, "");
      renderAll({ scroll: true });
      startCommandRuntimeRefresh();
      const live = sessionBusy(state.currentSession);
      const queued = sessionHasQueue(state.currentSession);
      if (restore && (state.currentSession.pending || live || queued)) {
        toast(
          state.currentSession.pending
            ? "已恢复待审批会话，请核对完整预览。"
            : queued && !live
              ? "已恢复待执行工具队列，请打开对话点击“继续执行工具队列”。"
              : "已恢复正在运行的会话，状态会自动更新。",
          "success",
        );
      }
      if (sessionBusy(state.currentSession)) startPolling(target);
      else maybeShowLastResult(target, state.currentSession);
      await loadFiles(".");
    } catch (error) {
      if (serial !== state.sessionRequestSerial || String(state.currentSessionId) !== target) return;
      state.loadingSession = false; state.currentSession = { id: target, messages: [], events: [] }; restoreComposerDraft("main", target, ""); renderAll(); toast(error.message || "读取会话失败", "error");
    }
  }

  async function deleteSession(id) {
    const target = String(id || "");
    if (!target) return;
    const summary = state.sessions.find((row) => String(row.id) === target);
    if (!window.confirm(`确定删除会话“${sessionTitle(summary || { title: target })}”吗？此操作会移除会话记录。`)) return;
    try {
      await request(`/sessions/${encodeURIComponent(target)}`, { method: "DELETE" });
      state.sessions = state.sessions.filter((row) => String(row.id) !== target);
      state.jobs.delete(target);
      state.clarification.jobs.delete(target);
      if (String(state.currentSessionId || "") === target) {
        state.currentSessionId = null;
        state.currentSession = null;
        state.optimisticMessage = null;
        persistCurrentSession();
        const next = state.sessions[0];
        if (next && next.id) await selectSession(next.id);
        else { renderAll(); await loadFiles("."); }
      } else renderAll();
      toast("会话已删除。", "success");
    } catch (error) {
      toast(error.message || "删除会话失败", "error");
    }
  }

  function sessionSignature(session) {
    if (!session) return "";
    try { return JSON.stringify(session); } catch (_error) { return safeString(session); }
  }

  async function refreshCurrentSession(target = state.currentSessionId, { scroll = false, clearOptimistic = false } = {}) {
    const id = String(target || ""); if (!id) return null;
    try {
      const session = unwrapSession(await request(`/sessions/${encodeURIComponent(id)}`));
      if (String(state.currentSessionId) !== id) return null;
      if (session) {
        const previousSignature = sessionSignature(state.currentSession);
        const job = state.jobs.get(id);
        session.busy = Boolean(session.busy || (job && job.id));
        const shouldClearOptimistic = clearOptimistic && !session.busy && state.optimisticMessage && state.optimisticMessage.sessionId === id;
        if (shouldClearOptimistic) state.optimisticMessage = null;
        state.currentSession = session;
        const index = state.sessions.findIndex((row) => String(row.id) === id);
        if (index >= 0) state.sessions[index] = { ...state.sessions[index], ...session, message_count: visibleMessages(session).length };
        const changed = previousSignature !== sessionSignature(session);
        if (changed || scroll || shouldClearOptimistic) renderAll({ scroll });
        if (!session.busy) maybeShowLastResult(id, session);
      }
      return session;
    } catch (error) {
      if (String(state.currentSessionId) === id && error.status !== 401 && error.status !== 403) toast(error.message || "刷新会话失败", "error");
      return null;
    }
  }

  function clearPolling() {
    if (state.pollTimer) { window.clearInterval(state.pollTimer); state.pollTimer = null; }
    state.pollTarget = null;
  }

  function startPolling(target) {
    const id = String(target || "");
    if (!id || document.visibilityState === "hidden") return;
    const hasJob = state.jobs.has(id);
    const liveSession = String(state.currentSessionId || "") === id && sessionBusy(state.currentSession);
    if (!hasJob && !liveSession) return;
    clearPolling(); state.pollTarget = id;
    pollOnce(id);
    state.pollTimer = window.setInterval(() => pollOnce(id), 1000);
  }

  function clearPracticeWindowRefresh() {
    if (state.practiceWindow.refreshTimer) {
      window.clearInterval(state.practiceWindow.refreshTimer);
      state.practiceWindow.refreshTimer = null;
    }
  }

  function startPracticeWindowRefresh() {
    if (!state.practiceWindow.standalone || !state.currentSessionId || document.visibilityState === "hidden") return;
    clearPracticeWindowRefresh();
    const refresh = async () => {
      const id = String(state.currentSessionId || "");
      if (!id || state.practiceWindow.refreshInFlight) return;
      state.practiceWindow.refreshInFlight = true;
      try {
        await refreshCurrentSession(id, { scroll: false, clearOptimistic: false });
        markPracticeWindowStale();
        if (state.workflowChat.open && state.workflowChat.scope === "practice" && state.practice.threadId && !state.practiceWindow.stale) {
          try { await refreshPracticeThread(id, state.practice.threadId); } catch (_error) { /* scoped panel reports its own read error */ }
        }
      } finally {
        state.practiceWindow.refreshInFlight = false;
      }
    };
    void refresh();
    // Keep a child window current even when another window initiated the job.
    // The service is local and the scoped endpoint is cheap; a one-second
    // cadence also makes completion visible without relying on opener events.
    state.practiceWindow.refreshTimer = window.setInterval(() => { void refresh(); }, 1000);
  }

  async function refreshMainWindowOnReturn() {
    // A practice child owns its own one-second refresh loop.  The main window
    // needs one immediate read when it becomes active again so a child that
    // completed a turn can update the main status and inspector before the
    // next user action.  refreshCurrentSession only replaces the durable
    // session object; the composer draft remains in its keyed local store and
    // is never restored or cleared by this read.
    if (state.practiceWindow.standalone || document.visibilityState === "hidden") return;
    const id = String(state.currentSessionId || "");
    if (!id || state.loadingSession || state.mainReturnRefreshInFlight) return;
    state.mainReturnRefreshInFlight = true;
    try {
      await refreshCurrentSession(id, { scroll: false, clearOptimistic: false });
    } finally {
      state.mainReturnRefreshInFlight = false;
    }
  }

  function finishClarificationJob(sessionId, terminalPayload = null, session = null) {
    const id = String(sessionId || "");
    const entry = state.clarification.jobs.get(id);
    if (!entry) return { kind: "none" };
    const result = terminalPayload && terminalPayload.result && typeof terminalPayload.result === "object"
      ? terminalPayload.result
      : {};
    const outerStatus = String(terminalPayload && terminalPayload.status || "").trim().toLowerCase();
    const resultStatus = String(result.status || outerStatus || "").trim().toLowerCase();
    const metadata = result.metadata && typeof result.metadata === "object"
      ? result.metadata
      : result.action && result.action.metadata && typeof result.action.metadata === "object"
        ? result.action.metadata
        : null;
    const failed = outerStatus === "failed" || ["failed", "error"].includes(resultStatus);
    const current = String(state.currentSessionId || "") === id;
    const modalMatches = current
      && state.clarification.open
      && state.clarification.sessionId === id
      && state.clarification.questionKey === entry.questionKey;
    const snapshot = clarificationSnapshotFor(session, id);
    const blocked = !failed && clarificationSessionBlocked(session, result);

    const preserveAnswer = () => {
      state.clarification.drafts.set(clarificationDraftKey(id, entry.question), entry.answer);
      if (modalMatches) state.clarification.answer = entry.answer;
    };
    const updateModal = (message, kind, feedback = "") => {
      if (!modalMatches) return;
      state.clarification.busy = false;
      state.clarification.completed = false;
      preserveAnswer();
      state.clarification.feedback = feedback;
      clarificationStatus(message, kind);
      syncClarificationModal();
    };

    if (failed) {
      state.clarification.jobs.delete(id);
      preserveAnswer();
      updateModal("处理失败，答案已保留；请检查后重试。", "error");
      return { kind: "failed", current, modalMatches };
    }

    // Approval, uncertain execution, and queued tool work are not a model
    // confirmation. Keep the question/job association so a later approve,
    // resolve, or continue job can finish this same modal operation.
    if (blocked) {
      preserveAnswer();
      state.clarification.jobs.set(id, {
        ...entry,
        active: false,
        blocked: true,
        needsRecovery: true,
        jobId: "",
        lastStatus: resultStatus
      });
      updateModal("请先打开对话处理当前操作；答案会保留，处理完成后继续更新。", "error");
      if (current && !modalMatches) toast("当前操作需要先在对话中处理，答案已保留。", "error", 5200);
      return { kind: "blocked", current, modalMatches };
    }

    // A result from a different session, or a shell without a loaded
    // job-radar profile, cannot prove that this question was saved.
    if (!snapshot) {
      preserveAnswer();
      state.clarification.jobs.set(id, {
        ...entry,
        active: false,
        blocked: false,
        needsRecovery: false,
        awaitingSnapshot: true,
        jobId: "",
        lastStatus: resultStatus
      });
      updateModal("暂时无法确认档案是否更新；答案已保留，请刷新后重试。", "error");
      if (current && !modalMatches) toast("暂时无法确认技能档案状态，答案已保留。", "error", 5200);
      return { kind: "uncertain", current, modalMatches };
    }

    const stillOpen = snapshot.questions.some((question) => clarificationQuestionKey(question) === entry.questionKey);

    // Generic approval/recovery endpoints may finish a worker without the
    // clarification metadata.  A changed snapshot alone is not enough to
    // prove that this particular answer was confirmed, so keep the local
    // association and allow a retry until the backend supplies the flag.
    if (!metadata || typeof metadata.confirmed !== "boolean") {
      preserveAnswer();
      state.clarification.jobs.set(id, {
        ...entry,
        active: false,
        blocked: false,
        needsRecovery: false,
        awaitingConfirmation: true,
        questionRemoved: !stillOpen,
        jobId: "",
        lastStatus: resultStatus
      });
      updateModal("已完成当前操作，但还无法确认档案保存状态；答案已保留，请稍后重试。", "error");
      if (current && !modalMatches) toast("还无法确认档案保存状态，答案已保留。", "error", 5200);
      return { kind: "uncertain", current, modalMatches };
    }

    const confirmed = metadata && metadata.confirmed === true;
    const answered = confirmed && !stillOpen && metadata.question_remaining !== true;
    if (!answered) {
      preserveAnswer();
      if (!stillOpen && metadata.confirmed !== true) {
        state.clarification.jobs.set(id, {
          ...entry,
          active: false,
          blocked: false,
          needsRecovery: false,
          awaitingConfirmation: true,
          questionRemoved: true,
          jobId: "",
          lastStatus: resultStatus
        });
        updateModal("当前作业尚未确认档案保存；答案已保留，请稍后重试。", "error", clarificationFeedbackText(terminalPayload, false));
        if (current && !modalMatches) toast("当前作业尚未确认档案保存，答案已保留。", "error", 5200);
        return { kind: "uncertain", current, modalMatches };
      }
      state.clarification.jobs.delete(id);
      updateModal("模型已完成处理，但待澄清问题仍保留；请核对回答后重试。", "error", clarificationFeedbackText(terminalPayload, false));
      if (current && !modalMatches) toast("待澄清问题仍保留，答案已保留以便重试。", "error", 5200);
      return { kind: "unconfirmed", current, modalMatches };
    }

    state.clarification.drafts.delete(clarificationDraftKey(id, entry.question));
    // The pending question is merged into the rendered list while this job
    // is awaiting confirmed metadata.  Remove that local association before
    // refreshing the workflow panes so a confirmed row cannot linger after
    // the modal completes.
    state.clarification.jobs.delete(id);
    if (modalMatches) {
      state.clarification.busy = false;
      state.clarification.completed = true;
      state.clarification.answer = entry.answer;
      state.clarification.feedback = clarificationFeedbackText(terminalPayload, true);
      clarificationStatus("回答已核对并保存，待澄清问题列表已刷新。", "success");
      renderInspector();
      renderWorkflowPage();
      syncClarificationModal();
    } else if (current) {
      renderInspector();
      renderWorkflowPage();
      toast("技能档案已更新，待澄清问题列表已刷新。", "success", 5200);
    }
    return { kind: "confirmed", current, modalMatches };
  }

  async function pollOnce(target) {
    const id = String(target || "");
    if (String(state.currentSessionId || "") !== id || document.visibilityState === "hidden") return;
    if (state.pollInFlight === id) return;
    state.pollInFlight = id;
    const job = state.jobs.get(id);
    const commandJobId = String(job && job.commandId || "").trim();
    try {
      let terminalPayload = null;
      if (job && job.id) {
        const payload = await request(`/jobs/${encodeURIComponent(job.id)}`);
        if (!state.jobs.has(id) || state.jobs.get(id).id !== job.id) return;
        const status = String(payload && payload.status || "running");
        if (status === "completed" || status === "failed") terminalPayload = payload;
      }
      if (terminalPayload) {
        state.jobs.delete(id);
        if (commandJobId) {
          state.commandRuntime.completionJobs.delete(commandJobId);
          state.commandRuntime.settled.add(commandJobId);
          if (terminalPayload.status === "failed") state.commandRuntime.error = String(terminalPayload.error || "命令结果同步失败");
        }
        // The worker has reached a terminal state, so the server snapshot is
        // authoritative. Clear the optimistic bubble before the refresh
        // renders it, including failed jobs where the result is still shown.
        if (String(state.currentSessionId) === id) {
          if (state.optimisticMessage && state.optimisticMessage.sessionId === id) state.optimisticMessage = null;
          if (state.practice.optimisticMessage && state.practice.optimisticMessage.sessionId === id) state.practice.optimisticMessage = null;
        }
      }
      const session = await refreshCurrentSession(id, { scroll: Boolean(terminalPayload), clearOptimistic: !job });
      await refreshCommandRuntime();
      if (terminalPayload && state.practice.sessionId === id && state.practice.threadId && state.workflowChat.scope === "practice") {
        try { await refreshPracticeThread(id, state.practice.threadId); } catch (_error) { /* the scoped panel reports its own read error */ }
      }
      if (terminalPayload) {
        const clarificationOutcome = finishClarificationJob(id, terminalPayload, session);
        state.optimisticMessage = null;
        state.practice.optimisticMessage = null;
        const status = String(terminalPayload.status || "");
        if (status === "failed") toast(String(terminalPayload.error || "AI 教练执行失败"), "error");
        await refreshSessions();
        await loadFiles(state.filePath);
        if (state.pollTarget === id) clearPolling();
        if (String(terminalPayload.status || "") === "completed" && state.followUpJobs.get(id) === "continue" && String(state.currentSessionId) === id) {
          state.followUpJobs.delete(id);
          await enqueueAction("continue", {}, id);
        } else if (terminalPayload) state.followUpJobs.delete(id);
      } else if (!job && session && !session.busy) {
        // After a page refresh the browser has no job id. Polling the session
        // itself still observes completion and avoids a permanently busy UI.
        maybeShowLastResult(id, session);
        await loadFiles(state.filePath || ".");
        if (state.pollTarget === id) clearPolling();
      }
    } catch (error) {
      if (String(state.currentSessionId) === id) toast(error.message || "读取执行状态失败", "error");
    } finally {
      if (state.pollInFlight === id) state.pollInFlight = null;
    }
  }

  async function enqueueAction(path, body, targetId = state.currentSessionId, { optimisticText = "", restoreText = "", restoreAttachment = null, restoreAttachments = null, clarificationContext = null, scope = "main", projectKey = "" } = {}) {
    const id = String(targetId || ""); if (!id || String(state.currentSessionId) !== id) return false;
    const recoveryPath = new Set(["approve", "reject", "continue", "resolve"]).has(String(path || ""));
    const recoveryEntry = !clarificationContext && recoveryPath ? clarificationJobFor(id) : null;
    const operationClarification = clarificationContext || recoveryEntry;
    if (optimisticText && scope === "practice") {
      const baseline = practiceMessages(state.currentSession, projectKey);
      state.practice.optimisticMessage = {
        sessionId: id,
        projectKey: String(projectKey || "").trim(),
        text: optimisticText,
        baseMessageCount: baseline.length
      };
    } else if (optimisticText) {
      const baseline = visibleMessages(state.currentSession);
      state.optimisticMessage = {
        sessionId: id,
        text: optimisticText,
        baseMessageCount: baseline.length,
        baseUserCount: visibleUserCount(baseline)
      };
    }
    if (state.currentSession) state.currentSession.busy = true;
    renderAll();
    try {
      const payload = await request(`/sessions/${encodeURIComponent(id)}/${path}`, { method: "POST", body });
      const jobId = payload && (payload.job_id || payload.jobId || (payload.job && payload.job.id));
      const practiceThreadId = payload && payload.practice_thread_id;
      if (scope === "practice" && practiceThreadId && String(state.currentSessionId) === id) state.practice.threadId = String(practiceThreadId);
      // The user can switch sessions while the POST is in flight. Preserve
      // the job association before checking the visible session so the
      // result can still be observed when they return.
      if (jobId) {
        state.jobs.set(id, { id: String(jobId) });
        if (operationClarification) {
          const entry = state.clarification.jobs.get(id);
          if (entry && (!operationClarification.token || entry.token === operationClarification.token)) {
            entry.jobId = String(jobId);
            entry.active = true;
            entry.blocked = false;
            entry.needsRecovery = false;
            entry.awaitingConfirmation = false;
          }
        }
      }
      if (String(state.currentSessionId) !== id) return true;
      const session = unwrapSession(payload);
      if (session && session.id) { state.currentSession = session; renderAll(); }
      if (jobId) {
        if (state.currentSession) state.currentSession.busy = true;
        renderAll(); startPolling(id);
      } else {
        if (scope === "practice") state.practice.optimisticMessage = null;
        else state.optimisticMessage = null;
        const latest = await refreshCurrentSession(id, { scroll: true });
        if (scope === "practice" && state.practice.threadId) {
          try { await refreshPracticeThread(id, state.practice.threadId); } catch (_error) { /* scoped panel keeps the error */ }
        }
        if (latest && latest.busy) startPolling(id);
        await refreshSessions();
        if (operationClarification && !jobId) finishClarificationJob(id, { status: "completed", result: {} }, latest);
      }
      return true;
    } catch (error) {
      state.jobs.delete(id);
      state.followUpJobs.delete(id);
      if (operationClarification) {
        const entry = state.clarification.jobs.get(id);
        if (entry && (!operationClarification.token || entry.token === operationClarification.token)) {
            const keepRecovery = Boolean(recoveryEntry || entry.blocked || entry.needsRecovery);
          if (keepRecovery) {
            state.clarification.jobs.set(id, { ...entry, active: false, blocked: true, needsRecovery: true, jobId: "" });
          } else state.clarification.jobs.delete(id);
          if (String(state.currentSessionId) === id && state.clarification.open && state.clarification.questionKey === entry.questionKey) {
            state.clarification.busy = false;
            state.clarification.answer = entry.answer;
            state.clarification.drafts.set(clarificationDraftKey(id, entry.question), entry.answer);
            state.clarification.feedback = "";
            clarificationStatus(keepRecovery ? "当前操作未完成，请打开对话重试；答案已保留。" : "发送失败，答案已保留；请修正后重试。", "error");
            syncClarificationModal();
          }
        }
      }
      if (String(state.currentSessionId) === id) {
        if (scope === "practice") state.practice.optimisticMessage = null;
        else state.optimisticMessage = null;
        if (state.currentSession) state.currentSession.busy = false;
        if (restoreText) { $("composerInput").value = restoreText; resizeComposer(); }
        if (restoreAttachments) {
          state.uploadedAttachments = restoreAttachments.map((item) => ({ ...item }));
          state.uploadedAttachment = state.uploadedAttachments[0] || null;
        } else if (restoreAttachment) {
          state.uploadedAttachments = [{ ...restoreAttachment }];
          state.uploadedAttachment = state.uploadedAttachments[0];
        }
        if (restoreText || restoreAttachments || restoreAttachment) captureComposerDraft(scope, id, projectKey);
        renderAll();
        toast(error.message || "操作失败", "error");
      }
      return false;
    }
  }

  async function sendMessage() {
    const input = $("composerInput");
    if ((state.voice.active || state.voice.stopping) && state.voice.target === input) stopVoiceInput({ cancel: true });
    if (state.workflowChat.open && state.workflowChat.scope === "practice") {
      if (!state.currentSessionId) return;
      if (state.currentSession && state.currentSession.pending && !practiceApprovalMatches(state.currentSession)) {
        toast("请先打开主对话处理待确认操作。", "");
        return;
      }
      const attachments = Array.isArray(state.uploadedAttachments)
        ? state.uploadedAttachments
        : (state.uploadedAttachment ? [state.uploadedAttachment] : []);
      const draftSessionId = String(state.currentSessionId || "");
      const draftProjectKey = state.workflowChat.practiceProjectKey || state.practice.projectKey;
      const typedText = input.value.trim();
      const text = typedText || (attachments.length ? EMPTY_ATTACHMENT_PROMPT : "");
      if (!text) return;
      const unsupported = attachments.find((attachment) => isUnsupportedImageAttachment(attachment));
      if (unsupported) {
        toast("暂不支持该图片格式，请先转换为 PNG、JPEG、GIF 或 WebP 后再上传。", "error");
        return;
      }
      input.value = "";
      resizeComposer();
      state.uploadedAttachment = null;
      state.uploadedAttachments = [];
      clearComposerDraft("practice", state.currentSessionId, state.workflowChat.practiceProjectKey || state.practice.projectKey);
      const accepted = await sendPracticeMessage(text, {
        projectKey: draftProjectKey,
        attachments
      });
      if (!accepted) {
        if (String(state.currentSessionId || "") === draftSessionId && state.workflowChat.open && state.workflowChat.scope === "practice" && state.workflowChat.practiceProjectKey === draftProjectKey) {
          input.value = text;
          resizeComposer();
          state.uploadedAttachments = attachments;
          state.uploadedAttachment = attachments[0] || null;
          captureComposerDraft("practice", draftSessionId, draftProjectKey);
          renderAll();
        } else storeComposerDraft("practice", draftSessionId, draftProjectKey, text, attachments);
      }
      return;
    }
    const newSessionDraft = !state.currentSessionId
      ? {
        text: String(input.value || ""),
        attachments: (Array.isArray(state.uploadedAttachments) ? state.uploadedAttachments : (state.uploadedAttachment ? [state.uploadedAttachment] : [])).map((item) => ({ ...item }))
      }
      : null;
    if (!state.currentSessionId) {
      await createSession();
      if (!state.currentSessionId) return;
      // Creating the first session must not erase the turn the user had typed
      // before the session id existed; move that draft to the new session key.
      if (newSessionDraft && (newSessionDraft.text || newSessionDraft.attachments.length)) {
        input.value = newSessionDraft.text;
        state.uploadedAttachments = newSessionDraft.attachments.map((item) => ({ ...item }));
        state.uploadedAttachment = state.uploadedAttachments[0] || null;
        captureComposerDraft("main", state.currentSessionId, "");
      }
    }
    if (state.currentSession && state.currentSession.pending) { toast("请先批准或拒绝上方操作，再发送新的消息。", ""); return; }
    if (sessionTurnBlocked(state.currentSession)) {
      toast(sessionHasQueue(state.currentSession) ? "上一轮工具队列待恢复，请先点击“继续执行工具队列”。" : "当前会话仍在运行，请等待完成。", "");
      return;
    }
    const attachments = Array.isArray(state.uploadedAttachments)
      ? state.uploadedAttachments
      : (state.uploadedAttachment ? [state.uploadedAttachment] : []);
    const draftSessionId = String(state.currentSessionId || "");
    const typedText = input.value.trim();
    const text = typedText || (attachments.length ? EMPTY_ATTACHMENT_PROMPT : "");
    if (!text) return;
    const pendingAction = state.pendingWorkflowAction
      && (!state.pendingWorkflowAction.sessionId || state.pendingWorkflowAction.sessionId === String(state.currentSessionId))
      ? state.pendingWorkflowAction
      : null;
    const unsupported = attachments.find((attachment) => isUnsupportedImageAttachment(attachment));
    if (unsupported) {
      toast("暂不支持该图片格式，请先转换为 PNG、JPEG、GIF 或 WebP 后再上传。", "error");
      return;
    }
    const attachmentPaths = attachments.map((attachment) => attachment.path || attachment.name).filter(Boolean);
    const imagePaths = attachments.filter((attachment) => isSupportedImageAttachment(attachment)).map((attachment) => attachment.path || attachment.name).filter(Boolean);
    const textPaths = attachments.filter((attachment) => !isSupportedImageAttachment(attachment)).map((attachment) => attachment.path || attachment.name).filter(Boolean);
    const attachmentNotes = [];
    if (textPaths.length) attachmentNotes.push(`已上传工作区附件，请使用相对路径读取：${textPaths.join("、")}`);
    if (imagePaths.length) attachmentNotes.push(`已上传图片：${imagePaths.join("、")}`);
    let composed = attachmentNotes.length ? `${text}\n\n${attachmentNotes.join("\n")}` : text;
    if (pendingAction) composed = `${composed}\n\n动作：${pendingAction.summary}`;
    const body = {
      text: composed,
      ...(pendingAction ? { workflow_action: { step: pendingAction.stepId, summary: pendingAction.summary } } : {})
    };
    // Image paths are part of this same user turn. The backend validates the
    // workspace path and turns them into ImageBlock content for the model.
    if (imagePaths.length) body.attachments = imagePaths;
    input.value = ""; resizeComposer(); state.uploadedAttachment = null; state.uploadedAttachments = [];
    clearComposerDraft("main", state.currentSessionId, "");
    const accepted = await enqueueAction("message", body, state.currentSessionId, {
      optimisticText: composed,
      restoreText: text,
      restoreAttachments: attachments.length ? attachments.map((attachment) => ({ ...attachment })) : null
    });
    if (!accepted) {
      if (String(state.currentSessionId || "") === draftSessionId && (!state.workflowChat.open || state.workflowChat.scope === "main")) captureComposerDraft("main", draftSessionId, "");
      else storeComposerDraft("main", draftSessionId, "", text, attachments);
    }
    if (accepted && pendingAction === state.pendingWorkflowAction) state.pendingWorkflowAction = null;
  }

  async function approveOrReject(kind) {
    const session = state.currentSession; const pending = session && session.pending; if (!pending || !pending.id) return;
    await enqueueAction(kind, { id: String(pending.id) }, state.currentSessionId);
  }

  async function continueSession() { await enqueueAction("continue", {}, state.currentSessionId); }

  async function resolveUncertain() {
    const session = state.currentSession; const id = approvalIdFor(session); if (!id) return;
    const note = String($("resolveNote") && $("resolveNote").value || "").trim();
    const target = String(state.currentSessionId);
    state.followUpJobs.set(target, "continue");
    const resolved = await enqueueAction("resolve", { id, note }, target);
    if (resolved && !state.jobs.has(target)) {
      state.followUpJobs.delete(target);
      await continueSession();
    }
  }

  function resizeComposer() {
    const input = $("composerInput"); input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
  }

  function voiceTargetLabel(input) {
    return input && input.id === "composerInput" ? "语音输入" : "语音填写";
  }

  function voiceLevelNodes(level) {
    return level ? qsa("span", level) : [];
  }

  function updateVoiceUi() {
    const activeTargetId = state.voice.active && state.voice.target ? state.voice.target.id : "";
    qsa("[data-voice-input-button]").forEach((button) => {
      const active = Boolean(activeTargetId && button.dataset.voiceTarget === activeTargetId);
      button.classList.toggle("recording", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
      const idleLabel = button.id === "voiceButton" ? "语音输入" : "语音填写";
      const label = button.querySelector("[data-voice-button-label]") || (button.id === "voiceButton" ? $("voiceButtonLabel") : null);
      if (label) setText(label, active ? "停止语音" : idleLabel);
      else if (button.id !== "voiceButton") setText(button, active ? "停止语音" : idleLabel);
      button.setAttribute("aria-label", active ? "停止语音输入" : `${idleLabel}（中文）`);
      button.title = active ? "停止语音" : `${idleLabel}（中文）`;
      const level = qsa("[data-voice-level]").find((node) => node.dataset.voiceTarget === button.dataset.voiceTarget);
      if (level) {
        level.classList.toggle("hidden", !active);
        level.setAttribute("aria-hidden", active ? "false" : "true");
      }
    });
  }

  function setVoiceVolume(value) {
    const volume = Math.max(0, Math.min(100, Number(value) || 0));
    qsa("[data-voice-level]").forEach((level) => {
      const active = Boolean(state.voice.active && state.voice.target && level.dataset.voiceTarget === state.voice.target.id);
      level.classList.toggle("hidden", !active);
      level.setAttribute("aria-hidden", active ? "false" : "true");
      const bars = voiceLevelNodes(level);
      bars.forEach((bar, index) => {
        const threshold = ((index + 1) / Math.max(1, bars.length)) * 100;
        bar.classList.toggle("on", active && volume >= threshold);
      });
    });
  }

  const VOICE_STOP_TIMEOUT_MS = 1200;

  function voiceSessionOwned(session) {
    return Boolean(session && state.voice.session === session && state.voice.generation === session.ticket);
  }

  function voiceSessionCurrent(session, { allowStopping = true } = {}) {
    if (!voiceSessionOwned(session)) return false;
    if (state.voice.target !== session.input) return false;
    if (typeof document.contains === "function" && !document.contains(session.input)) return false;
    if (!allowStopping && !state.voice.active) return false;
    if (!state.voice.active && !state.voice.stopping) return false;
    return session.sessionId === String(state.currentSessionId || "");
  }

  function stopVoiceMeter(meter = state.voice.meter) {
    if (!meter || meter.closed) return;
    meter.closed = true;
    if (state.voice.meter === meter) state.voice.meter = null;
    if (meter.frame && typeof window.cancelAnimationFrame === "function") window.cancelAnimationFrame(meter.frame);
    if (meter.stream && typeof meter.stream.getTracks === "function") {
      meter.stream.getTracks().forEach((track) => {
        try { track.stop(); } catch (_error) { /* already stopped */ }
      });
    }
    if (meter.context && typeof meter.context.close === "function") {
      try {
        const closing = meter.context.close();
        if (closing && typeof closing.catch === "function") void closing.catch(() => {});
      } catch (_error) { /* already closed */ }
    }
  }

  function releaseVoice({ ticket = null, abort = false } = {}) {
    const session = state.voice.session;
    if (ticket !== null && (!session || session.ticket !== ticket)) return false;
    state.voice.generation += 1;
    state.voice.active = false;
    state.voice.stopping = false;
    state.voice.target = null;
    if (session && session.stopTimer) {
      window.clearTimeout(session.stopTimer);
      session.stopTimer = null;
    }
    const recognition = session?.recognition || state.voice.recognition;
    if (session && session.inputListener && session.input && typeof session.input.removeEventListener === "function") {
      session.input.removeEventListener("input", session.inputListener);
    }
    state.voice.recognition = null;
    state.voice.session = null;
    if (recognition) {
      recognition.onresult = null;
      recognition.onend = null;
      recognition.onerror = null;
      if (abort) {
        try {
          if (typeof recognition.abort === "function") recognition.abort();
          else if (typeof recognition.stop === "function") recognition.stop();
        } catch (_error) { /* already stopped */ }
      }
    }
    stopVoiceMeter();
    state.voice.latestText = "";
    setVoiceVolume(0);
    updateVoiceUi();
    return true;
  }

  function stopVoiceInput({ cancel = false } = {}) {
    const session = state.voice.session;
    if (!session) {
      releaseVoice();
      return;
    }
    if (cancel) {
      releaseVoice({ ticket: session.ticket, abort: true });
      return;
    }
    if (state.voice.stopping) return;
    state.voice.active = false;
    state.voice.stopping = true;
    stopVoiceMeter();
    updateVoiceUi();
    const finish = () => releaseVoice({ ticket: session.ticket, abort: true });
    session.stopTimer = window.setTimeout(finish, VOICE_STOP_TIMEOUT_MS);
    const recognition = session.recognition;
    if (!recognition || typeof recognition.stop !== "function") {
      finish();
      return;
    }
    try {
      recognition.stop();
    } catch (_error) {
      finish();
    }
  }

  function startVoiceMeter(ticket) {
    const session = state.voice.session;
    if (!session || session.ticket !== ticket) return;
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
      toast("当前环境无法显示麦克风音量；语音识别仍可继续。", "", 3600);
      return;
    }
    void navigator.mediaDevices.getUserMedia({ audio: true }).then((stream) => {
      if (!voiceSessionCurrent(session, { allowStopping: false })) {
        if (stream && typeof stream.getTracks === "function") stream.getTracks().forEach((track) => track.stop());
        return;
      }
      let context = null;
      try {
        const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextCtor) throw new Error("audio_context_unavailable");
        if (typeof window.requestAnimationFrame !== "function") throw new Error("animation_frame_unavailable");
        context = new AudioContextCtor();
        const source = context.createMediaStreamSource(stream);
        const analyser = context.createAnalyser();
        analyser.fftSize = 512;
        analyser.smoothingTimeConstant = 0.55;
        source.connect(analyser);
        const data = new Uint8Array(analyser.frequencyBinCount);
        const meter = { stream, context, frame: 0, closed: false };
        state.voice.meter = meter;
        const tick = () => {
          if (!voiceSessionCurrent(session, { allowStopping: false }) || state.voice.meter !== meter) {
            stopVoiceMeter(meter);
            return;
          }
          analyser.getByteFrequencyData(data);
          const mean = data.reduce((sum, value) => sum + value, 0) / data.length;
          setVoiceVolume(Math.min(100, Math.max(0, (mean - 4) * 2.4)));
          meter.frame = window.requestAnimationFrame(tick);
        };
        tick();
      } catch (_error) {
        stopVoiceMeter({ stream, context, frame: 0, closed: false });
        if (voiceSessionCurrent(session, { allowStopping: false })) toast("麦克风音量检测初始化失败，语音识别仍可继续。", "", 3600);
      }
    }).catch(() => {
      if (voiceSessionCurrent(session, { allowStopping: false })) toast("无法获取麦克风权限，已关闭音量显示。", "", 3600);
    });
  }

  function toggleVoiceInput(targetInput = $("composerInput")) {
    const input = targetInput && targetInput.nodeType === 1 ? targetInput : $("composerInput");
    if (!input) return;
    if (state.voice.active || state.voice.stopping) {
      const sameTarget = state.voice.target === input;
      if (sameTarget && state.voice.active) {
        stopVoiceInput();
        return;
      }
      releaseVoice({ ticket: state.voice.session?.ticket, abort: true });
    }
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      toast("当前浏览器不支持网页语音识别。可使用 Windows 系统听写（Win+H）后粘贴文字，页面不会伪装成已录音。", "error", 5200);
      return;
    }
    const ticket = ++state.voice.generation;
    const session = {
      ticket,
      input,
      sessionId: String(state.currentSessionId || ""),
      baseText: String(input.value || ""),
      finalResults: new Map(),
      interimResults: new Map(),
      stopTimer: null,
      recognition: null,
      renderedText: String(input.value || ""),
      programmaticInput: false,
      inputListener: null
    };
    state.voice.session = session;
    state.voice.active = true;
    state.voice.stopping = false;
    state.voice.target = input;
    state.voice.latestText = session.baseText;
    session.inputListener = () => {
      if (session.programmaticInput || !voiceSessionOwned(session)) return;
      releaseVoice({ ticket, abort: true });
    };
    if (typeof input.addEventListener === "function") input.addEventListener("input", session.inputListener);
    updateVoiceUi();
    try {
      const recognition = new Recognition();
      session.recognition = recognition;
      state.voice.recognition = recognition;
      recognition.lang = "zh-CN";
      recognition.interimResults = true;
      recognition.continuous = true;
      recognition.onresult = (event) => {
        if (!voiceSessionCurrent(session)) return;
        if (String(input.value || "") !== session.renderedText) {
          releaseVoice({ ticket, abort: true });
          return;
        }
        const results = event && event.results;
        if (!results || typeof results.length !== "number") return;
        const firstChanged = Number.isInteger(event.resultIndex) ? Math.max(0, event.resultIndex) : 0;
        for (const index of session.interimResults.keys()) {
          if (index >= results.length) session.interimResults.delete(index);
        }
        for (let index = firstChanged; index < results.length; index += 1) {
          const result = results[index];
          const alternative = result && result[0];
          const transcript = alternative && typeof alternative.transcript === "string" ? alternative.transcript : "";
          if (result && result.isFinal === false) {
            session.interimResults.set(index, transcript);
          } else {
            // SpeechRecognition re-emits the same result while later words are
            // recognized. Replacing by result index prevents final text from
            // being appended twice, while still preserving repeated phrases at
            // distinct result indexes.
            session.finalResults.set(index, transcript);
            session.interimResults.delete(index);
          }
        }
        const finalText = [...session.finalResults.entries()]
          .sort(([left], [right]) => left - right)
          .map(([, text]) => text)
          .join("");
        const interimText = [...session.interimResults.entries()]
          .sort(([left], [right]) => left - right)
          .map(([, text]) => text)
          .join("");
        const nextText = `${session.baseText}${finalText}${interimText}`;
        state.voice.latestText = nextText;
        session.renderedText = nextText;
        if (input.value !== nextText) {
          session.programmaticInput = true;
          try {
            input.value = nextText;
            input.dispatchEvent(new Event("input", { bubbles: true }));
          } finally {
            session.programmaticInput = false;
          }
          if (input.id === "composerInput") resizeComposer();
        }
      };
      recognition.onend = () => {
        if (voiceSessionOwned(session)) releaseVoice({ ticket, abort: false });
      };
      recognition.onerror = (event) => {
        if (!voiceSessionOwned(session)) return;
        if (!voiceSessionCurrent(session)) {
          releaseVoice({ ticket, abort: true });
          return;
        }
        const errorCode = event && event.error;
        const expectedStop = state.voice.stopping || errorCode === "aborted";
        releaseVoice({ ticket, abort: true });
        if (!expectedStop && errorCode !== "aborted") {
          const voiceErrors = {
            "not-allowed": "麦克风权限被拒绝",
            "service-not-allowed": "语音服务不可用",
            "audio-capture": "未检测到可用麦克风",
            "no-speech": "没有检测到语音",
            network: "语音服务网络连接失败"
          };
          toast(`语音识别失败：${voiceErrors[errorCode] || "请检查麦克风权限或改用文字输入"}`, "error");
        }
      };
      recognition.start();
      startVoiceMeter(ticket);
    } catch (error) {
      stopVoiceInput({ cancel: true });
      toast(`启动语音识别失败：${String(error && error.message || error)}`, "error");
    }
  }

  let voiceInputSequence = 0;

  function voiceInputAllowed(input) {
    if (!input || input.dataset.voiceExclude === "true" || input.dataset.voiceAttached === "true") return false;
    if (input.id === "composerInput" || input.id === "localImportPath") return false;
    if (input.closest("#modelSettingsForm, #newSessionForm")) return false;
    const type = String(input.type || (input.tagName === "TEXTAREA" ? "textarea" : "")).toLowerCase();
    return input.tagName === "TEXTAREA" || type === "text" || type === "search";
  }

  function addVoiceInputControl(input) {
    if (!voiceInputAllowed(input)) return;
    if (!input.id) input.id = `voice-input-${++voiceInputSequence}`;
    const parent = input.parentNode;
    if (!parent) return;
    input.dataset.voiceAttached = "true";
    const wrap = document.createElement("div");
    wrap.className = "voice-input-wrap";
    input.classList.forEach((className) => wrap.classList.add(className));
    parent.insertBefore(wrap, input);
    wrap.appendChild(input);
    const tools = document.createElement("div");
    tools.className = "voice-input-tools";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "voice-input-button";
    button.dataset.voiceInputButton = "true";
    button.dataset.voiceTarget = input.id;
    button.appendChild(icon("mic"));
    const label = document.createElement("span");
    label.dataset.voiceButtonLabel = "true";
    label.textContent = "语音填写";
    button.appendChild(label);
    button.addEventListener("click", () => toggleVoiceInput(input));
    const level = document.createElement("div");
    level.className = "voice-level voice-level-inline hidden";
    level.dataset.voiceLevel = "true";
    level.dataset.voiceTarget = input.id;
    level.title = "实时音量";
    level.setAttribute("aria-label", "实时音量");
    for (let index = 0; index < 8; index += 1) level.appendChild(document.createElement("span"));
    tools.append(button, level);
    wrap.appendChild(tools);
    updateVoiceUi();
  }

  function enhanceVoiceInputs(root = document) {
    if ((state.voice.active || state.voice.stopping) && state.voice.target && !document.contains(state.voice.target)) stopVoiceInput({ cancel: true });
    qsa("textarea, input", root).forEach((input) => addVoiceInputControl(input));
    updateVoiceUi();
  }

  function setInspectorCollapsed(collapsed) {
    const pane = $("inspectPane");
    const toggle = $("inspectorToggle");
    if (!pane || !toggle) return;
    state.inspectorCollapsed = Boolean(collapsed);
    pane.classList.toggle("collapsed", state.inspectorCollapsed);
    const expanded = !state.inspectorCollapsed;
    toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
    const label = expanded ? "收起求职资料面板" : "打开求职资料面板";
    toggle.setAttribute("aria-label", label);
    toggle.setAttribute("title", label);
  }

  function toggleInspectorPane() {
    const pane = $("inspectPane");
    if (!pane) return;
    if (state.pageView) {
      // The workflow page hides the chat grid, so keep the inspector on the
      // current page as a fixed side panel instead of navigating back to chat.
      if (pane.parentNode !== $("appShell")) {
        $("appShell").appendChild(pane);
        pane.classList.add("workflow-inspector-overlay");
        setInspectorCollapsed(false);
      } else {
        setInspectorCollapsed(pane.classList.contains("collapsed") ? false : true);
      }
      renderInspector();
      return;
    }
    if (pane.classList.contains("workflow-inspector-overlay")) restoreInspectorPaneHost();
    setInspectorCollapsed(!pane.classList.contains("collapsed"));
  }

  function applyResponsiveInspectorDefault() {
    if (!$("inspectPane") || !$("inspectorToggle")) return;
    // The inspector is useful on desktop, but it must not cover the only
    // readable surface in the narrow embedded browser. Keep it closed until
    // the user explicitly opens it with the toolbar button.
    if (window.innerWidth < 900) setInspectorCollapsed(true);
    else setInspectorCollapsed(state.inspectorCollapsed);
  }

  async function uploadOne(file) {
    if (!file) return null;
    if (file.size > MAX_UPLOAD_BYTES) throw new Error(`“${file.name}”超过 10 MiB 限制。`);
    const candidate = { name: file.name || "剪贴板图片", mediaType: file.type || "" };
    if (isUnsupportedImageAttachment(candidate)) throw new Error("暂不支持该图片格式，请先转换为 PNG、JPEG、GIF 或 WebP 后再上传。");
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(new Error(`无法读取“${file.name || "附件"}”`));
      reader.readAsDataURL(file);
    });
    const comma = dataUrl.indexOf(",");
    const base64 = comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl;
    const safeName = file.name || `粘贴图片-${Date.now()}.png`;
    const payload = await request("/uploads", { method: "POST", body: { name: safeName, content_base64: base64 } });
    return {
      name: safeName,
      mediaType: file.type || "",
      path: payload && (payload.path || payload.name) || safeName
    };
  }

  async function handleUploads(files) {
    const list = Array.from(files || []).filter(Boolean);
    if (!list.length) return;
    const results = [];
    const errors = [];
    for (const file of list) {
      try {
        const uploaded = await uploadOne(file);
        if (uploaded) results.push(uploaded);
      } catch (error) {
        errors.push(error instanceof Error ? error.message : String(error));
      }
    }
    if (results.length) {
      state.uploadedAttachments = [...(state.uploadedAttachments || []), ...results];
      state.uploadedAttachment = state.uploadedAttachments[0] || null;
      captureComposerDraft();
      renderAll();
      toast(`已上传 ${results.length} 份材料，发送后才会读取。`, "success");
    }
    if (errors.length) toast(errors.join("；"), "error", 5200);
  }

  async function handlePlanProjectUploads(files) {
    const list = Array.from(files || []).filter(Boolean);
    if (!list.length) return;
    const sessionId = String(state.currentSessionId || "new");
    const results = [];
    const errors = [];
    for (const file of list) {
      try {
        const uploaded = await uploadOne(file);
        if (String(state.currentSessionId || "new") !== sessionId) throw new Error("会话已切换，项目材料未加入当前会话。请在目标会话重新上传。");
        if (uploaded) results.push(uploaded);
      } catch (error) {
        errors.push(error instanceof Error ? error.message : String(error));
      }
    }
    if (results.length && String(state.currentSessionId || "new") === sessionId) {
      planProjectFiles = [...planProjectFiles, ...results];
      renderPlanProjectResources();
      toast(`已加入 ${results.length} 份自有复现项目材料，发送计划时会传给同一会话。`, "success");
    }
    if (errors.length) toast(errors.join("；"), "error", 5200);
  }

  function pastedFiles(event) {
    const clipboard = event && event.clipboardData;
    if (!clipboard) return [];
    const files = Array.from(clipboard.files || []);
    if (files.length) return files;
    return Array.from(clipboard.items || [])
      .filter((item) => item.kind === "file")
      .map((item) => item.getAsFile && item.getAsFile())
      .filter(Boolean);
  }

  function bindEvents() {
    $("backToChat").addEventListener("click", () => openWorkflowChatDialog());
    qsa("[data-profile-tab]").forEach((button) => button.addEventListener("click", () => setProfileTab(button.dataset.profileTab)));
    $("profileDocuments").addEventListener("change", (event) => { profileFiles = Array.from(event.target.files || []); renderProfileFiles(); });
    $("profileImportForm").addEventListener("submit", submitProfilePage);
    $("planPageForm").addEventListener("submit", submitPlanPage);
    $("planOtherDiscuss").addEventListener("click", discussPlanOther);
    ["planInclude", "planExclude", "planMinStars", "planOther", "planPaperProjects", "planRerank"].forEach((id) => {
      const control = $(id);
      if (!control) return;
      const key = { planInclude: "include", planExclude: "exclude", planMinStars: "minStars", planOther: "other", planPaperProjects: "paperProjects", planRerank: "rerank" }[id];
      control.addEventListener(control.type === "checkbox" ? "change" : "input", () => rememberProjectFilter(state.currentSessionId, { [key]: control.type === "checkbox" ? control.checked : control.value }));
    });
    $("planProjectUrl").addEventListener("input", renderPlanProjectResources);
    $("planProjectUpload").addEventListener("click", () => $("planProjectFileInput").click());
    $("planProjectFileInput").addEventListener("change", (event) => { void handlePlanProjectUploads(event.target.files); event.target.value = ""; });
    $("planProjectLocalImport").addEventListener("click", () => openLocalImport("planProject"));
    $("planProjectRegister").addEventListener("click", () => { void submitOwnProjectPage(); });
    $("profileManual").addEventListener("click", async () => { if (!state.currentSessionId) await createSession(); openJobEditor("profile", workflowData().profile || {}); });
    $("profileCopy").addEventListener("click", () => openNewSession(state.currentSessionId));
    $("practiceStarter").addEventListener("click", () => { openPracticeChatDialog({ start: true }); });
    $("practiceFiles").addEventListener("click", () => { openWorkflowChatDialog(); state.selectedTab = "files"; setInspectorCollapsed(false); renderInspector(); loadFiles(state.filePath); });
    hydrateIcons();
    applyTheme(readTheme(), { persist: false });
    sidebarLastMobile = sidebarIsMobile();
    syncSidebarState();
    qsa("[data-theme-option]").forEach((button) => button.addEventListener("click", () => toggleTheme(button.dataset.themeOption)));
    qsa("[data-step]").forEach((button) => button.addEventListener("click", () => selectWorkflowStep(button.dataset.step)));
    $("newChatButton").addEventListener("click", () => openNewSession());
    $("modelSettingsButton").addEventListener("click", openModelSettings);
    $("onboardingOpenModelSettings").addEventListener("click", openModelSettings);
    $("globalModelChoice").addEventListener("change", switchGlobalModel);
    $("refreshButton").addEventListener("click", () => { refreshInfo(); refreshSessions(); refreshCurrentSession(state.currentSessionId, { scroll: false }); if (state.selectedTab === "files") loadFiles(state.filePath); });
    $("sidebarToggle").addEventListener("click", toggleSidebar);
    $("sidebarClose").addEventListener("click", closeSidebarDrawer);
    $("sidebarScrim").addEventListener("click", closeSidebarDrawer);
    $("inspectorToggle").addEventListener("click", toggleInspectorPane);
    $("paneClose").addEventListener("click", () => setInspectorCollapsed(true));
    $("dismissTokenNotice").addEventListener("click", () => { state.dismissTokenNotice = true; showTokenNotice(); });
    $("composerForm").addEventListener("submit", (event) => { event.preventDefault(); sendMessage(); });
    $("composerInput").addEventListener("input", () => { resizeComposer(); captureComposerDraft(); });
    $("composerInput").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendMessage(); } });
    $("uploadButton").addEventListener("click", () => $("fileUploadInput").click());
    $("localImportButton").addEventListener("click", openLocalImport);
    $("fileUploadInput").addEventListener("change", (event) => { handleUploads(event.target.files); event.target.value = ""; });
    $("voiceButton").addEventListener("click", toggleVoiceInput);
    $("composerInput").addEventListener("paste", (event) => {
      const files = pastedFiles(event);
      if (files.length) {
        event.preventDefault();
        void handleUploads(files);
      }
    });
    const composer = qs(".composer");
    ["dragenter", "dragover"].forEach((name) => composer.addEventListener(name, (event) => {
      if (event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files.length) {
        event.preventDefault(); composer.classList.add("drop-active");
      }
    }));
    ["dragleave", "drop"].forEach((name) => composer.addEventListener(name, (event) => {
      event.preventDefault(); composer.classList.remove("drop-active");
      if (name === "drop" && event.dataTransfer && event.dataTransfer.files) void handleUploads(event.dataTransfer.files);
    }));
    $("sessionList").addEventListener("click", (event) => {
      const remove = event.target.closest("[data-session-delete]");
      if (remove) { event.preventDefault(); event.stopPropagation(); void deleteSession(remove.dataset.sessionDelete); return; }
      const button = event.target.closest("[data-session-id]");
      if (button) { closeSidebarDrawer(); void selectSession(button.dataset.sessionId); }
    });
    $("commandRuntimeList").addEventListener("click", (event) => {
      const stop = event.target.closest("[data-command-stop]");
      if (stop) { stop.disabled = true; void stopCommandRuntime(stop.dataset.commandStop); }
    });
    qsa(".inspect-tab").forEach((button) => button.addEventListener("click", () => { if (!button.classList.contains("hidden")) { state.selectedTab = button.dataset.tab; renderInspector(); if (state.selectedTab === "files" && !state.fileEntries.length) loadFiles(state.filePath); } }));
    $("approvalRegion").addEventListener("click", (event) => { const action = event.target.closest("[data-action]"); if (!action) return; action.disabled = true; if (action.dataset.action === "approve") approveOrReject("approve"); else if (action.dataset.action === "reject") approveOrReject("reject"); else if (action.dataset.action === "resolve") resolveUncertain(); else if (action.dataset.action === "continue") continueSession(); });
    $("fileModalClose").addEventListener("click", closeFileModal);
    $("fileModalBackdrop").addEventListener("click", (event) => { if (event.target === $("fileModalBackdrop")) closeFileModal(); });
    $("modelSettingsClose").addEventListener("click", closeModelSettings);
    $("modelSettingsCancel").addEventListener("click", closeModelSettings);
    $("modelTestButton").addEventListener("click", testModelSettings);
    $("modelNewProfileButton").addEventListener("click", startNewModelProfile);
    $("modelChoice").addEventListener("change", (event) => {
      const choice = event.target;
      const selected = choice.selectedOptions && choice.selectedOptions[0];
      if (!selected || !selected.value) return;
      void selectConfiguredModel(selected);
    });
    $("githubTokenSave").addEventListener("click", saveGitHubToken);
    $("modelSettingsForm").addEventListener("submit", saveModelSettings);
    $("modelSettingsBackdrop").addEventListener("click", (event) => { if (event.target === $("modelSettingsBackdrop")) closeModelSettings(); });
    $("jobEditClose").addEventListener("click", closeJobEditor);
    $("jobEditCancel").addEventListener("click", closeJobEditor);
    $("jobEditForm").addEventListener("submit", saveJobEditor);
    $("jobEditBackdrop").addEventListener("click", (event) => { if (event.target === $("jobEditBackdrop")) closeJobEditor(); });
    $("clarificationClose").addEventListener("click", closeClarificationModal);
    $("clarificationCancel").addEventListener("click", closeClarificationModal);
    $("clarificationForm").addEventListener("submit", submitClarification);
    $("clarificationAnswer").addEventListener("input", () => {
      const answer = String($("clarificationAnswer").value || "");
      state.clarification.answer = answer;
      if (!state.clarification.busy && state.clarification.sessionId && state.clarification.question) {
        state.clarification.drafts.set(clarificationDraftKey(state.clarification.sessionId, state.clarification.question), answer);
      }
      syncClarificationModal();
    });
    $("clarificationRecovery").addEventListener("click", () => {
      closeClarificationModal();
      openWorkflowChatDialog({ scrollApproval: true });
    });
    $("clarificationBackdrop").addEventListener("click", (event) => { if (event.target === $("clarificationBackdrop")) closeClarificationModal(); });
    $("workflowChatClose").addEventListener("click", closeWorkflowChatDialog);
    $("workflowChatBackdrop").addEventListener("click", (event) => { if (event.target === $("workflowChatBackdrop")) closeWorkflowChatDialog(); });
    $("jdImportButton").addEventListener("click", openJdImport);
    $("jdImportClose").addEventListener("click", closeJdImport);
    $("jdImportBackdrop").addEventListener("click", (event) => { if (event.target === $("jdImportBackdrop")) closeJdImport(); });
    qsa("[data-jd-mode]").forEach((button) => button.addEventListener("click", () => setJdMode(button.dataset.jdMode)));
    JD_FIELDS.forEach((id) => $(id).addEventListener("input", saveJdDraft));
    $("jdImportSubmit").addEventListener("click", () => submitJdImport(jdDraft().mode));
    $("jdQuery").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); void submitJdImport("search"); } });
    $("jdImageDrop").addEventListener("click", () => $("jdImagePicker").click());
    $("jdImagePicker").addEventListener("change", (event) => { addJdFiles(event.target.files); event.target.value = ""; });
    $("jdImageDrop").addEventListener("dragover", (event) => event.preventDefault());
    $("jdImageDrop").addEventListener("drop", (event) => { event.preventDefault(); event.stopPropagation(); addJdFiles(event.dataTransfer.files); });
    $("jdImportBackdrop").addEventListener("paste", (event) => { const files = pastedFiles(event); if (files.length) { event.preventDefault(); event.stopPropagation(); setJdMode("image"); addJdFiles(files); } });
    $("newSessionClose").addEventListener("click", closeNewSession);
    $("newSessionCancel").addEventListener("click", closeNewSession);
    $("newSessionForm").addEventListener("submit", submitNewSession);
    $("copySessionSource").addEventListener("change", () => { $("newSessionCreate").textContent = $("copySessionSource").value ? "复制档案并新建" : "新建岗位会话"; });
    $("newSessionBackdrop").addEventListener("click", (event) => { if (event.target === $("newSessionBackdrop")) closeNewSession(); });
    $("localImportClose").addEventListener("click", closeLocalImport);
    $("localImportCancel").addEventListener("click", closeLocalImport);
    $("localImportForm").addEventListener("submit", submitLocalImport);
    $("localImportBackdrop").addEventListener("click", (event) => { if (event.target === $("localImportBackdrop")) closeLocalImport(); });
    document.addEventListener("keydown", (event) => {
      trapModelSettingsFocus(event);
      if (event.key === "Escape") { closeSidebarDrawer(); closeJdImport(); closeFileModal(); closeModelSettings(); closeJobEditor(); closeNewSession(); closeLocalImport(); closeClarificationModal(); closeWorkflowChatDialog(); }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); openNewSession(); }
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") {
        clearPolling();
        clearPracticeWindowRefresh();
        clearCommandRuntimeRefresh();
      } else {
        void refreshMainWindowOnReturn();
        if (state.currentSessionId && sessionBusy(state.currentSession)) startPolling(state.currentSessionId);
        if (state.practiceWindow.standalone) startPracticeWindowRefresh();
        startCommandRuntimeRefresh();
      }
    });
    window.addEventListener("focus", () => { void refreshMainWindowOnReturn(); });
    window.addEventListener("beforeunload", () => { clearPolling(); clearPracticeWindowRefresh(); clearCommandRuntimeRefresh(); stopVoiceInput({ cancel: true }); });
    window.addEventListener("hashchange", handleTokenHashChange);
    window.addEventListener("resize", handleSidebarResize);
  }

  async function bootstrap() {
    const windowParams = readPracticeWindowParams();
    state.practiceWindow.standalone = windowParams.standalone;
    state.practiceWindow.sessionId = windowParams.sessionId;
    state.practiceWindow.projectKey = windowParams.projectKey;
    state.practiceWindow.startRequested = Boolean(windowParams.start);
    state.practiceWindow.startConsumed = false;
    state.token = captureToken();
    bindEvents();
    startCommandRuntimeRefresh();
    applyPracticeWindowChrome();
    applyResponsiveInspectorDefault();
    showTokenNotice();
    renderAll();
    await Promise.allSettled([refreshInfo(), refreshSessions(), loadUiSettings()]);
    if (state.practiceWindow.standalone) {
      state.selectedStep = "practice";
      state.pageView = true;
      if (!state.practiceWindow.sessionId || !state.practiceWindow.projectKey) {
        showStandalonePracticeStale(
          state.practiceWindow.sessionId,
          state.practiceWindow.projectKey,
          "这个实操窗口缺少固定的会话或项目范围。请关闭此窗口，并从“计划 & 项目”重新打开实操。",
        );
        return;
      }
      const requestedPracticeSession = state.sessions.find((row) => String(row.id) === String(state.practiceWindow.sessionId));
      if (!requestedPracticeSession || !requestedPracticeSession.id) {
        showStandalonePracticeStale(
          state.practiceWindow.sessionId,
          state.practiceWindow.projectKey,
          "固定会话已不存在或无法读取。为避免打开错误会话，请关闭此窗口，并从当前岗位会话重新打开实操。",
        );
        return;
      }
      await selectSession(requestedPracticeSession.id, { restore: true });
      if (String(state.currentSessionId || "") !== String(state.practiceWindow.sessionId)) {
        showStandalonePracticeStale(
          state.practiceWindow.sessionId,
          state.practiceWindow.projectKey,
          "固定会话读取失败。请关闭此窗口，并从当前岗位会话重新打开实操。",
        );
        return;
      }
      renderAll();
      await openPracticeChatDialog({ focus: true, start: state.practiceWindow.startRequested });
      return;
    }
    const persisted = readPersistedSession();
    if (persisted) {
      const found = state.sessions.some((row) => String(row.id) === String(persisted));
      if (found) await selectSession(persisted, { restore: true });
      else if (state.sessions[0] && state.sessions[0].id) await selectSession(state.sessions[0].id);
    } else if (state.sessions[0] && state.sessions[0].id) {
      await selectSession(state.sessions[0].id);
    } else {
      renderAll();
      await loadFiles(".");
    }
  }

  bootstrap();
})();
