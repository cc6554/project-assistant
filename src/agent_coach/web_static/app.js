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
    filePath: ".",
    fileEntries: [],
    fileLoading: false,
    fileError: "",
    jobs: new Map(),
    followUpJobs: new Map(),
    pollTimer: null,
    pollTarget: null,
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
      previousFocus: null
    },
    jdImport: { open: false, opening: false, busy: false, sessionId: "", drafts: new Map(), previousFocus: null },
    newSession: {
      open: false,
      busy: false,
      previousFocus: null
    },
    github: {
      hasToken: false,
      busy: false
    },
    renderSerial: 0,
    lastResultSeen: new Map(),
    dismissTokenNotice: false,
    loadingSession: false,
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

  function currentJob() {
    return state.currentSessionId ? state.jobs.get(String(state.currentSessionId)) || null : null;
  }

  function persistCurrentSession() {
    if (!state.currentSessionId) return;
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
    toast(message, String(result.status || "").toLowerCase() === "failed" ? "error" : "success");
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
    setText($("modeBreadcrumb"), mode === "demo" ? "岗位雷达 · 演示模式" : "岗位雷达");
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
      if (busy) dot.classList.add("busy"); else if (pending) dot.classList.add("pending");
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
    setText($("currentTitle"), session ? sessionTitle(session) : "新会话");
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
    const messages = visibleMessages(state.currentSession);
    const toolMap = new Map(messages.filter((message) => message.role === "tool" && message.tool_call_id).map((message) => [String(message.tool_call_id), message]));
    messages.forEach((message) => {
      if (message.role === "tool" && message.tool_call_id && toolMap.has(String(message.tool_call_id))) {
        const hasCall = messages.some((candidate) => candidate.tool_calls && candidate.tool_calls.some((call) => String(call.id) === String(message.tool_call_id)));
        if (hasCall) return;
      }
      list.appendChild(createMessageElement(message, toolMap));
    });
    if (state.optimisticMessage && state.optimisticMessage.sessionId === String(state.currentSessionId || "") && !serverHasOptimisticUser(state.currentSession, state.optimisticMessage)) {
      const optimistic = createMessageElement({ role: "user", content: state.optimisticMessage.text }, new Map());
      optimistic.classList.add("pending-message");
      list.appendChild(optimistic);
    }
    const hasMessages = list.childElementCount > 0;
    $("welcomePanel").classList.toggle("hidden", hasMessages);
    if (shouldScroll) requestAnimationFrame(() => { $("chatScroll").scrollTop = $("chatScroll").scrollHeight; });
  }

  function renderActivity() {
    const panel = $("activityPanel");
    const events = Array.isArray(state.currentSession && state.currentSession.events) ? state.currentSession.events.slice(-5) : [];
    panel.classList.toggle("hidden", events.length === 0 && !sessionBusy(state.currentSession));
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
    const busy = sessionBusy(state.currentSession);
    setText($("activityStatus"), busy ? "处理中" : events.length ? "已记录" : "等待操作");
    $("activityPanel").querySelector(".activity-pulse").style.animationPlayState = busy ? "running" : "paused";
  }

  function approvalIdFor(session) {
    if (!session) return "";
    const pending = session.pending;
    if (pending && pending.id) return String(pending.id);
    const execution = session.execution || {};
    return String(execution.approval_id || execution.call_id || "");
  }

  function renderApproval() {
    const region = $("approvalRegion");
    region.replaceChildren();
    const session = state.currentSession;
    if (!session) return;
    const pending = session.pending;
    const execution = session.execution || {};
    const isUnknown = !sessionBusy(session) && ["uncertain", "running"].includes(String(execution.status || "")) && approvalIdFor(session);
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
    const blocked = busy || pending;
    $("busyBar").classList.toggle("hidden", !busy);
    $("sendButton").disabled = blocked;
    $("composerInput").disabled = blocked;
    $("sendButton").setAttribute("aria-busy", busy ? "true" : "false");
    $("composerInput").placeholder = pending ? "请先处理上方审批，再发送新的消息…" : busy ? "求职教练正在处理当前会话…" : "粘贴 JD、说明已有技能，或上传相关材料…";
    const job = currentJob();
    setText($("busyText"), job ? "求职教练正在运行，状态每秒更新…" : "会话正在恢复，状态会自动更新…");
  }

  function renderAll({ scroll = false } = {}) {
    state.renderSerial += 1;
    renderHeader();
    renderMessages(scroll);
    renderActivity();
    renderApproval();
    renderBusy();
    renderInspector();
    renderWorkflowPage();
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
    if (state.jdImport.open) saveJdDraft();
    state.selectedStep = id;
    state.pageView = true;
    state.selectedTab = "job";
    renderInspector();
    renderWorkflowPage();
  }

  function showSharedChat() {
    if (state.jdImport.open) saveJdDraft();
    state.pageView = false;
    const chat = qs(".chat-column");
    $("chatHome").prepend(chat);
    $("workflowPage").classList.add("hidden");
    $("chatHome").classList.remove("hidden");
    $("composerInput").focus();
  }

  const pageDrafts = new Map();
  let pageSession = null;
  let profileTab = "text";
  let profileFiles = [];
  let pageSubmitting = false;
  const PAGE_FIELDS = ["profileSource", "profileText", "profilePath", "profileAnswer", "planInclude", "planExclude", "planMinStars", "planRerank"];
  function syncPageDrafts() {
    const id = String(state.currentSessionId || "new");
    if (pageSession === id) return;
    if (pageSession !== null) {
      const values = {};
      PAGE_FIELDS.forEach((name) => { values[name] = $(name).type === "checkbox" ? $(name).checked : $(name).value; });
      pageDrafts.set(pageSession, { values, tab: profileTab, files: profileFiles });
    }
    pageSession = id;
    const draft = pageDrafts.get(id);
    PAGE_FIELDS.forEach((name) => { const el = $(name); const value = draft?.values[name] ?? (name === "planMinStars" ? "50" : name === "profileSource" ? "self_report" : name === "planRerank" ? true : ""); if (el.type === "checkbox") el.checked = value; else el.value = value; });
    profileFiles = draft?.files || [];
    $("profileDocuments").value = "";
    setProfileTab(draft?.tab || "text");
    renderProfileFiles();
    setText($("profileImportStatus"), ""); setText($("planPageStatus"), "");
  }
  function setProfileTab(tab) {
    profileTab = tab;
    qsa("[data-profile-tab]").forEach((el) => el.setAttribute("aria-selected", String(el.dataset.profileTab === tab)));
    qsa("[data-profile-panel]").forEach((el) => el.classList.toggle("hidden", el.dataset.profilePanel !== tab));
  }
  function renderProfileFiles() { $("profileDocumentList").textContent = profileFiles.map((file) => file.name).join("、"); }
  function renderWorkflowPage() {
    syncPageDrafts();
    if (!state.pageView) return;
    const step = WORKFLOW_STEPS.find((item) => item.id === state.selectedStep);
    $("workflowPage").classList.remove("hidden"); $("chatHome").classList.add("hidden");
    $("workflowPageTitle").textContent = step.title;
    $("workflowPageHint").textContent = step.hint;
    qsa(".step-page").forEach((page) => page.classList.toggle("hidden", page.id !== `${step.id}Page`));
    const busy = sessionBusy(state.currentSession) || state.jobs.has(String(state.currentSessionId));
    $("workflowPageStatus").textContent = state.currentSession?.pending ? "当前操作等待确认。返回对话查看详情并批准或拒绝。" : busy ? "正在处理；可返回对话查看回复与运行记录。" : "";
    $("workflowPageStatus").classList.toggle("hidden", !state.currentSession?.pending && !busy);
    ["profileImportSubmit", "planGenerate", "practiceStarter"].forEach((id) => { $(id).disabled = Boolean(pageSubmitting || busy || state.currentSession?.pending || state.loadingSession); });
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
    $("profileManual").disabled = Boolean(state.loadingSession || state.newSession.busy || state.currentSession?.pending || busy);
    if (step.id === "plan") qsa(".job-card", results).forEach((card) => { if (qs("h3", card)?.textContent === "下一步与差距") card.remove(); });
    if (step.id === "profile") qsa(".job-card", results).forEach((card) => { if (qs("h3", card)?.textContent === "已有技能") card.remove(); });
    if (step.id === "profile" && data.profile?.skills?.length) {
      if (data.profile.summary) { const summary = document.createElement("p"); summary.className = "profile-summary"; summary.textContent = data.profile.summary; results.prepend(summary); }
      results.prepend(pageTable("当前档案", ["技能", "熟练程度", "事实证据"], data.profile.skills.map((skill) => [typeof skill === "string" ? skill : skill.name || skill.skill, jobEnumLabel(skill.proficiency || skill.level || skill.status), skill.evidence || skill.proof])));
    }
    if (step.id === "plan" && data.plan) {
      results.prepend(pageTable("差距分析", ["岗位要求", "已有技能", "状态", "说明"], (data.plan.gaps || []).map((gap) => [typeof gap === "string" ? gap : gap.requirement || gap.name || gap.skill, gap.matched_skill, jobEnumLabel(gap.status || gap.priority), gap.note])));
      const phases = document.createElement("section"); phases.className = "job-card";
      const title = document.createElement("h3"); title.textContent = "分阶段学习计划"; phases.appendChild(title);
      (data.plan.phases || []).forEach((phase) => { const item = document.createElement("div"); item.className = "plan-phase"; const h = document.createElement("h4"); h.textContent = typeof phase === "string" ? phase : `${phase.phase || phase.title || phase.name || "学习阶段"}${phase.duration_weeks ? ` · ${phase.duration_weeks} 周` : ""}`; item.appendChild(h); for (const key of ["goal", "topics", "suggested_practice", "tasks", "checkpoints"]) { if (phase[key]) { const p = document.createElement("p"); p.textContent = `${({goal:"目标",topics:"学习内容",suggested_practice:"建议练习",tasks:"任务",checkpoints:"检查点"})[key]}：${Array.isArray(phase[key]) ? phase[key].map((value) => typeof value === "string" ? value : jobItemText(value, ["title", "name", "description", "text"]) || safeString(value)).join("、") : typeof phase[key] === "string" ? phase[key] : safeString(phase[key])}`; item.appendChild(p); } } phases.appendChild(item); });
      if (data.plan.phases?.length) { const repos = qsa(".job-card", results).find((card) => qs("h3", card)?.textContent.startsWith("候选练习项目")); if (repos) results.insertBefore(phases, repos); else results.appendChild(phases); }
    }
    if (step.id === "jd") {
      if ($("jdImportBackdrop").parentNode !== $("jdPageImport")) $("jdPageImport").appendChild($("jdImportBackdrop"));
      $("jdImportBackdrop").classList.remove("hidden");
      if (state.jdImport.sessionId !== String(state.currentSessionId || "")) {
        if (state.jdImport.open) saveJdDraft();
        state.jdImport.sessionId = String(state.currentSessionId || "");
        JD_FIELDS.forEach((id) => { $(id).value = jdDraft().fields[id] || (id === "jdPlatform" ? "boss" : ""); });
        setJdMode(jdDraft().mode); renderJdFiles();
      }
      state.jdImport.open = true;
    }
    if (step.id === "practice" && qs(".chat-column").parentNode !== $("practiceChatHost")) $("practiceChatHost").appendChild(qs(".chat-column"));
    enhanceVoiceInputs($("workflowPage"));
  }
  function pageTable(title, headers, rows) {
    const card = document.createElement("section"); card.className = "job-card page-table-card";
    const heading = document.createElement("h3"); heading.textContent = title; card.appendChild(heading);
    const table = document.createElement("table"); const head = document.createElement("thead"); const row = document.createElement("tr");
    headers.forEach((value) => { const cell = document.createElement("th"); cell.textContent = value; row.appendChild(cell); }); head.appendChild(row); table.appendChild(head);
    const body = document.createElement("tbody"); rows.forEach((values) => { const row = document.createElement("tr"); values.forEach((value) => { const cell = document.createElement("td"); cell.textContent = value == null ? "—" : typeof value === "object" ? safeString(value) : String(value); row.appendChild(cell); }); body.appendChild(row); }); table.appendChild(body); card.appendChild(table); return card;
  }
  async function sendPageAction(text, status, attachments = [], sessionId = state.currentSessionId) {
    if (state.loadingSession || state.currentSession?.pending || sessionBusy(state.currentSession) || state.jobs.has(String(state.currentSessionId))) throw new Error("请先完成当前操作或等待会话运行结束。");
    if (sessionId && String(sessionId) !== String(state.currentSessionId)) throw new Error("会话已切换，输入已保留，请在原会话重试。");
    if (!state.currentSessionId) await createSession();
    const id = String(state.currentSessionId);
    const imagePaths = attachments.filter(isSupportedImageAttachment).map((item) => item.path || item.name).filter(Boolean);
    const textPaths = attachments.filter((item) => !isSupportedImageAttachment(item)).map((item) => item.path || item.name).filter(Boolean);
    const composed = textPaths.length ? `${text}\n\n已上传工作区附件，请使用 read_document 或文件读取工具读取相对路径：${textPaths.join("、")}` : text;
    const body = { text: composed }; if (imagePaths.length) body.attachments = imagePaths;
    const accepted = await enqueueAction("message", body, id, { optimisticText: composed });
    if (!accepted) throw new Error("发送失败，输入已保留。");
    status.textContent = "已提交。返回对话可查看回复与待确认操作，结果会同步到本页。";
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
      if (state.loadingSession || sessionBusy(state.currentSession) || state.currentSession?.pending) throw new Error("请先完成当前操作。");
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
  async function submitPlanPage(event) {
    event.preventDefault(); if (pageSubmitting) return;
    const data = workflowData(); const status = $("planPageStatus");
    if (!data.jd || !data.profile) { status.textContent = "请先填写并确认岗位 JD 和技能档案。"; return; }
    const stars = Number($("planMinStars").value);
    if (!Number.isInteger(stars) || stars < 0) { status.textContent = "最低 star 数应为非负整数。"; return; }
    pageSubmitting = true;
    try { await sendPageAction(`请基于当前已确认 JD 与技能档案生成差距分析、分阶段学习计划（含任务、检查点），并搜索真实 GitHub 复现项目。包含关键词：${$("planInclude").value || "不限"}；排除关键词：${$("planExclude").value || "无"}；最低 star 数：${stars}。${$("planRerank").checked ? "由你比较候选项目与我的差距并说明排序理由。" : "按搜索匹配度展示候选项目。"}保存计划与候选项目，等待我选择项目，不要自动开始实操。`, status); }
    catch (error) { status.textContent = error.message; }
    finally { pageSubmitting = false; renderWorkflowPage(); }
  }

  function draftWorkflowPrompt(text) {
    const input = $("composerInput");
    if (input.value.trim()) { toast("消息框已有草稿，请先发送或清空后再准备引导。", ""); return; }
    input.value = `${text}\n只处理当前步骤；完成后展示结果，等待我选择下一步，不要自行推进其他阶段。`;
    resizeComposer();
    if (window.matchMedia("(max-width: 900px)").matches) {
      $("inspectPane").classList.add("collapsed");
      $("inspectorToggle").setAttribute("aria-expanded", "false");
    }
    input.focus();
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
    const note = document.createElement("small"); note.textContent = "准备引导只填写消息草稿；由你点击发送后才会处理。"; card.appendChild(note);
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
    return String(project.full_name || project.fullName || project.repository || project.name || project.html_url || project.url || "").trim();
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
    if (!canPrepareJobAction()) return;
    const name = String(project.full_name || project.name || id);
    const text = `请切换到练习项目“${name}”。调用 select_project，使用 full_name=${name}，恢复该项目已有的练习进度；不要清空或覆盖其他项目的历史记录。切换完成后说明当前项目状态。`;
    const input = $("composerInput");
    input.value = text;
    resizeComposer();
    selectWorkflowStep("practice");
    void sendMessage();
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
      name.textContent = String(item.project.full_name || item.project.name || item.identity);
      body.appendChild(name);
      const meta = document.createElement("span");
      meta.className = "project-history-meta";
      const language = item.project.language ? String(item.project.language) : "";
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
    state.jobEditor.open = true;
    state.jobEditor.kind = kind;
    state.jobEditor.value = JSON.parse(JSON.stringify(value));
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
        jobEditField("硬性要求", "hard_requirements", editableList(draft.hard_requirements), { multiline: true, hint: "每行一项" }),
        jobEditField("技能标签", "skill_tags", editableList(draft.skill_tags), { multiline: true, hint: "每行一项" }),
        jobEditField("工作职责", "responsibilities", editableList(draft.responsibilities), { multiline: true, hint: "每行一项" }),
        jobEditField("加分项", "nice_to_haves", editableList(draft.nice_to_haves), { multiline: true, hint: "每行一项" }),
        jobEditField("不确定字段", "uncertain_fields", editableList(draft.uncertain_fields), { multiline: true, hint: "每行一个字段名，例如 salary、location" })
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
    if (state.voice.active && state.voice.target && $("jobEditBackdrop").contains(state.voice.target)) stopVoiceInput();
    state.jobEditor.open = false;
    state.jobEditor.value = null;
    $("jobEditBackdrop").classList.add("hidden");
    if (!state.modelSettings.open && !state.newSession.open) document.body.classList.remove("modal-open");
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
    if (!state.jobEditor.open || !state.jobEditor.value) return;
    const draft = JSON.parse(JSON.stringify(state.jobEditor.value));
    let missingEvidence = [];
    qsa("[data-job-edit-field]", $("jobEditFields")).forEach((field) => {
      const key = field.dataset.jobEditField;
      if (["hard_requirements", "skill_tags", "responsibilities", "nice_to_haves", "open_questions", "uncertain_fields"].includes(key)) draft[key] = linesFromField(field);
      else draft[key] = String(field.value || "").trim();
    });
    const kind = state.jobEditor.kind;
    if (kind === "profile") {
      const profileSkills = readProfileSkillsEditor(draft.skills, draft.open_questions);
      draft.skills = profileSkills.skills;
      draft.open_questions = profileSkills.openQuestions;
      missingEvidence = profileSkills.missingEvidence;
    }
    const kindLabel = kind === "jd" ? "岗位 JD" : "技能档案";
    const button = $("jobEditSave");
    button.disabled = true;
    setText($("jobEditStatus"), `正在保存${kindLabel}…`);
    try {
      const payload = await request(`/sessions/${encodeURIComponent(state.currentSessionId)}/${kind}`, { method: "POST", body: { [kind]: draft, confirmed: true } });
      const session = unwrapSession(payload);
      if (session && session.id) state.currentSession = session;
      else await refreshCurrentSession(state.currentSessionId, { scroll: false });
      // JD edits also change the sidebar title. Refresh the summaries before
      // rendering so both the row and the top title use the new JD immediately.
      await refreshSessions();
      const summary = state.sessions.find((row) => String(row.id) === String(state.currentSessionId));
      if (summary && state.currentSession) state.currentSession = { ...state.currentSession, title: summary.title, job_title: summary.job_title, company: summary.company };
      closeJobEditor();
      renderAll();
      toast(missingEvidence.length ? `${kindLabel}已确认并保存；${missingEvidence.length} 项缺少事实证据，已转入待澄清问题。` : `${kindLabel}已确认并保存。`, "success");
    } catch (error) {
      setText($("jobEditStatus"), `保存失败：${error.message || "无法保存资料"}`);
    } finally {
      button.disabled = false;
    }
  }

  function commaList(value) {
    return String(value || "").split(/[，,]/).map((item) => item.trim()).filter(Boolean);
  }

  function canPrepareJobAction() {
    if (state.currentSession?.pending) { toast("请先批准或拒绝当前操作。", ""); return false; }
    if (sessionBusy(state.currentSession)) { toast("当前会话仍在运行，请等待完成。", ""); return false; }
    if ($("composerInput").value.trim()) { toast("消息框已有草稿，请先发送或清空后继续。", ""); return false; }
    return true;
  }

  function submitProjectFilter() {
    if (!canPrepareJobAction()) return;
    const card = qs(".project-filter-card", $("inspectContent"));
    if (!card) return;
    const minStars = String((qs('[data-job-filter="minStars"]', card) || {}).value || "").trim();
    const include = commaList((qs('[data-job-filter="include"]', card) || {}).value);
    const exclude = commaList((qs('[data-job-filter="exclude"]', card) || {}).value);
    if (!minStars && !include.length && !exclude.length) { toast("请先填写至少一项筛选偏好。", ""); return; }
    const lines = ["请按下面的明确偏好重新搜索并整理候选练习项目，仍在当前求职工作流中处理："];
    if (minStars) lines.push(`最低星标数：${minStars}`);
    if (include.length) lines.push(`包含关键词：${include.join("、")}`);
    if (exclude.length) lines.push(`排除关键词：${exclude.join("、")}`);
    lines.push("搜索完成后展示候选项目，并说明每个项目与当前岗位差距的对应关系。");
    const input = $("composerInput"); input.value = lines.join("\n"); resizeComposer();
    void sendMessage();
  }

  function startSelectedProject(repo) {
    if (!canPrepareJobAction()) return;
    const name = String(repo && (repo.full_name || repo.name) || "").trim();
    if (!name) return;
    const input = $("composerInput");
    input.value = `我选用练习项目 ${name}。请调用当前求职工作流记录这个项目，并先给出今天的上手步骤，再开始实践。`;
    resizeComposer();
    selectWorkflowStep("practice");
    void sendMessage();
  }

  function answerClarification(question) {
    if (!canPrepareJobAction()) return;
    const text = String(question || "").trim();
    if (!text) return;
    const input = $("composerInput");
    input.value = `我来回答待澄清问题：“${text}”\n请先听取我的事实回答，并更新当前会话中的技能档案；回答清楚后从待澄清列表移除这个问题。`;
    resizeComposer();
    showSharedChat();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    toast("问题已带入消息框，请补充事实后发送。", "success", 3600);
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
    if (state.currentSession?.pending || sessionBusy(state.currentSession) || state.jobs.has(id)) { setText($("jdImportStatus"), "请先处理当前待确认操作或等待会话运行完成。"); return; }
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
      if (String(state.currentSessionId) !== id || state.loadingSession || state.currentSession?.pending || sessionBusy(state.currentSession) || state.jobs.has(id)) throw new Error("会话状态已变化，输入已保留，请稍后重试。");
      const body = { text }; if (attachments.length) body.attachments = attachments.map((item) => item.path);
      const accepted = await enqueueAction("message", body, id, { optimisticText: text });
      if (!accepted) throw new Error("发送失败，输入已保留，请重试。");
      if (kind === "search") $("jdQuery").value = "";
      else if (kind === "source") { $("jdSourceUrl").value = ""; $("jdSourceText").value = ""; }
      else { draft.files = []; $("jdImageNote").value = ""; }
      saveJdDraft(); state.jdImport.busy = false; setText($("jdImportStatus"), "已提交，结果会同步到本页。返回对话可查看回复与待确认操作。");
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
      const jdCard = jobObjectCard("目标 JD", stateData.jd, ["job_title", "company", "salary", "location", "team", "raw_summary", "uncertain_fields"], "jd");
      appendJobList(jdCard, "硬性要求", stateData.jd.hard_requirements, (item) => jobItemText(item, ["requirement", "name", "text", "title"]));
      appendJobList(jdCard, "技能标签", stateData.jd.skill_tags, (item) => jobItemText(item, ["name", "skill", "tag", "text"]));
      appendJobList(jdCard, "岗位职责", stateData.jd.responsibilities, (item) => jobItemText(item, ["responsibility", "name", "text", "title"]));
      appendJobList(jdCard, "加分项", stateData.jd.nice_to_haves, (item) => jobItemText(item, ["requirement", "name", "text", "title"]));
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
    const questions = [...new Set([...clarificationQuestions, ...profileQuestions].map((item) => String(item || "").trim()).filter(Boolean))];
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
          const answer = document.createElement("button"); answer.type = "button"; answer.className = "job-card-action"; answer.textContent = "现在回答"; answer.addEventListener("click", () => answerClarification(question)); row.appendChild(answer);
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
      const minStars = document.createElement("input"); minStars.type = "number"; minStars.min = "0"; minStars.step = "1"; minStars.placeholder = "最低星标数"; minStars.dataset.jobFilter = "minStars"; minStars.setAttribute("aria-label", "最低星标数");
      const include = document.createElement("input"); include.type = "text"; include.placeholder = "包含关键词（逗号分隔）"; include.dataset.jobFilter = "include"; include.setAttribute("aria-label", "包含关键词");
      const exclude = document.createElement("input"); exclude.type = "text"; exclude.placeholder = "排除关键词（逗号分隔）"; exclude.dataset.jobFilter = "exclude"; exclude.setAttribute("aria-label", "排除关键词");
      filterFields.append(minStars, include, exclude); filterCard.appendChild(filterFields);
      const filterButton = document.createElement("button"); filterButton.type = "button"; filterButton.className = "action-button secondary project-filter-submit"; filterButton.textContent = "重新搜索"; filterButton.addEventListener("click", submitProjectFilter); filterCard.appendChild(filterButton);
      root.appendChild(filterCard);
    }
    if (["plan", "practice"].includes(step) && stateData.selected_project) root.appendChild(jobObjectCard("已选练习项目", stateData.selected_project, ["full_name", "language", "stargazers_count", "html_url"]));
    if (step === "plan" && Array.isArray(stateData.repos) && stateData.repos.length) {
      const card = document.createElement("section"); card.className = "job-card"; const h = document.createElement("h3"); h.textContent = `候选练习项目 · ${stateData.repos.length}`; card.appendChild(h);
      stateData.repos.slice(0, 8).forEach((repo) => { const row = document.createElement("div"); row.className = "job-repo"; const info = document.createElement("div"); const strong = document.createElement("strong"); strong.textContent = String(repo.full_name || repo.name || "未命名项目"); info.appendChild(strong); if (repo.html_url) { try { const url = new URL(repo.html_url); if (["https:", "http:"].includes(url.protocol)) { const link = document.createElement("a"); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; link.textContent = "查看 GitHub 仓库 ↗"; info.appendChild(link); } } catch (_) {} } const span = document.createElement("span"); span.textContent = `${repo.language || "未知语言"}${repo.stargazers_count != null ? ` · ${repo.stargazers_count} 星标` : ""}`; info.appendChild(span); for (const key of ["description", "reason", "match_score"]) { if (repo[key] != null) { const detail = document.createElement("p"); detail.className = "repo-detail"; detail.textContent = `${key === "match_score" ? "匹配分：" : key === "reason" ? "推荐理由：" : ""}${key === "match_score" && Number.isFinite(Number(repo[key])) ? `${Math.round(Number(repo[key]) <= 1 ? Number(repo[key]) * 100 : Number(repo[key]))}%` : repo[key]}`; info.appendChild(detail); } } row.appendChild(info); const use = document.createElement("button"); use.type = "button"; use.className = "job-card-action"; use.textContent = "选用并开始实践"; use.addEventListener("click", () => startSelectedProject(repo)); row.appendChild(use); card.appendChild(row); }); root.appendChild(card);
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
    high: "高优先", medium: "中优先", low: "低优先", unknown: "待确认"
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
      if (value != null && String(value).trim()) return String(value);
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

  function appendJobList(card, label, items, formatter) {
    if (!Array.isArray(items) || !items.length) return;
    const heading = document.createElement("div"); heading.className = "job-subtitle"; heading.textContent = label; card.appendChild(heading);
    const list = document.createElement("div"); list.className = "job-list";
    items.slice(0, 12).forEach((item) => { const value = formatter(item); if (!value) return; const row = document.createElement("div"); row.className = "job-list-item"; row.textContent = String(value); list.appendChild(row); });
    if (list.childElementCount) card.appendChild(list);
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

  function openLocalImport() {
    if (state.newSession.open || state.modelSettings.open) return;
    $("localImportPath").value = "";
    setText($("localImportStatus"), "");
    $("localImportBackdrop").classList.remove("hidden");
    document.body.classList.add("modal-open");
    $("localImportClose").focus();
  }

  function closeLocalImport() {
    $("localImportBackdrop").classList.add("hidden");
    if (!state.modelSettings.open && !state.jobEditor.open && !state.newSession.open) document.body.classList.remove("modal-open");
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
      // Only paths in the explicit imported list become message attachments.
      // The response envelope may contain the original source path and must
      // never be treated as an attachment when zero files were imported.
      const imported = Array.isArray(payload && payload.imported) ? payload.imported : [];
      const skipped = Array.isArray(payload && payload.skipped) ? payload.skipped : [];
      const attachments = imported.map((item) => ({
        name: String(typeof item === "string" ? item : item && item.path || ""),
        path: String(typeof item === "string" ? item : item && item.path || ""),
        mediaType: String(item && (item.media_type || item.mediaType) || "")
      })).filter((item) => item.path);
      const summary = localImportSummary(payload, attachments.length, skipped);
      if (attachments.length) {
        state.uploadedAttachments = [...(state.uploadedAttachments || []), ...attachments];
        state.uploadedAttachment = state.uploadedAttachments[0] || null;
        closeLocalImport();
        renderAll();
        toast(summary, "success", 7200);
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
    const item = document.createElement("div"); item.className = `toast ${kind}`;
    item.appendChild(icon(kind === "error" ? "warning" : kind === "success" ? "check" : "spark", "toast-icon")); item.appendChild(document.createTextNode(String(message || "")));
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
    document.body.classList.remove("modal-open");
    const previousFocus = state.modelSettings.previousFocus;
    state.modelSettings.previousFocus = null;
    restoreSidebarAwareFocus(previousFocus);
  }

  function trapModelSettingsFocus(event) {
    if (event.key !== "Tab") return;
    const backdrop = state.newSession.open ? $("newSessionBackdrop") : state.modelSettings.open ? $("modelSettingsBackdrop") : null;
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
    if (!state.modelSettings.open && !state.jobEditor.open) document.body.classList.remove("modal-open");
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
      clearPolling(); stopVoiceInput();
      state.optimisticMessage = null; state.uploadedAttachment = null; state.uploadedAttachments = [];
      state.currentSessionId = String(session.id); state.currentSession = session; persistCurrentSession();
      state.sessions = [{ ...session, has_profile: Boolean(session.skill_state?.["job-radar"]?.profile), message_count: visibleMessages(session).length }, ...state.sessions.filter((row) => String(row.id) !== String(session.id))];
      if (state.newSession.open) { state.newSession.busy = false; closeNewSession(); }
      renderAll({ scroll: true });
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
    clearPolling();
    stopVoiceInput();
    state.currentSessionId = target; state.currentSession = null; state.loadingSession = true; state.optimisticMessage = null; state.uploadedAttachment = null; state.uploadedAttachments = []; persistCurrentSession();
    renderAll();
    const serial = ++state.sessionRequestSerial;
    try {
      const session = unwrapSession(await request(`/sessions/${encodeURIComponent(target)}`));
      if (serial !== state.sessionRequestSerial || String(state.currentSessionId) !== target) return;
      state.currentSession = session || { id: target, messages: [] };
      state.loadingSession = false;
      renderAll({ scroll: true });
      if (restore && (state.currentSession.pending || sessionBusy(state.currentSession))) toast(state.currentSession.pending ? "已恢复待审批会话，请核对完整预览。" : "已恢复正在运行的会话，状态会自动更新。", "success");
      if (sessionBusy(state.currentSession)) startPolling(target);
      else maybeShowLastResult(target, state.currentSession);
      await loadFiles(".");
    } catch (error) {
      if (serial !== state.sessionRequestSerial || String(state.currentSessionId) !== target) return;
      state.loadingSession = false; state.currentSession = { id: target, messages: [], events: [] }; renderAll(); toast(error.message || "读取会话失败", "error");
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

  async function refreshCurrentSession(target = state.currentSessionId, { scroll = false, clearOptimistic = false } = {}) {
    const id = String(target || ""); if (!id) return null;
    try {
      const session = unwrapSession(await request(`/sessions/${encodeURIComponent(id)}`));
      if (String(state.currentSessionId) !== id) return null;
      if (session) {
        const job = state.jobs.get(id);
        session.busy = Boolean(session.busy || (job && job.id));
        if (clearOptimistic && !session.busy && state.optimisticMessage && state.optimisticMessage.sessionId === id) state.optimisticMessage = null;
        state.currentSession = session;
        const index = state.sessions.findIndex((row) => String(row.id) === id);
        if (index >= 0) state.sessions[index] = { ...state.sessions[index], ...session, message_count: visibleMessages(session).length };
        renderAll({ scroll });
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

  async function pollOnce(target) {
    const id = String(target || "");
    if (String(state.currentSessionId || "") !== id || document.visibilityState === "hidden") return;
    const job = state.jobs.get(id);
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
        // The worker has reached a terminal state, so the server snapshot is
        // authoritative. Clear the optimistic bubble before the refresh
        // renders it, including failed jobs where the result is still shown.
        if (String(state.currentSessionId) === id && state.optimisticMessage && state.optimisticMessage.sessionId === id) state.optimisticMessage = null;
      }
      const session = await refreshCurrentSession(id, { scroll: Boolean(terminalPayload), clearOptimistic: !job });
      if (terminalPayload) {
        state.optimisticMessage = null;
        const status = String(terminalPayload.status || "");
        const result = terminalPayload.result;
        if (status === "failed") toast(String(terminalPayload.error || "AI 教练执行失败"), "error");
        else if (result && result.message) toast(String(result.message), result.status === "completed" ? "success" : "");
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
    }
  }

  async function enqueueAction(path, body, targetId = state.currentSessionId, { optimisticText = "", restoreText = "", restoreAttachment = null, restoreAttachments = null } = {}) {
    const id = String(targetId || ""); if (!id || String(state.currentSessionId) !== id) return false;
    if (optimisticText) {
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
      // The user can switch sessions while the POST is in flight. Preserve
      // the job association before checking the visible session so the
      // result can still be observed when they return.
      if (jobId) state.jobs.set(id, { id: String(jobId) });
      if (String(state.currentSessionId) !== id) return true;
      const session = unwrapSession(payload);
      if (session && session.id) { state.currentSession = session; renderAll(); }
      if (jobId) {
        if (state.currentSession) state.currentSession.busy = true;
        renderAll(); startPolling(id);
      } else {
        state.optimisticMessage = null;
        const latest = await refreshCurrentSession(id, { scroll: true });
        if (latest && latest.busy) startPolling(id);
        await refreshSessions();
      }
      return true;
    } catch (error) {
      state.jobs.delete(id);
      state.followUpJobs.delete(id);
      if (String(state.currentSessionId) === id) {
        state.optimisticMessage = null;
        if (state.currentSession) state.currentSession.busy = false;
        if (restoreText) { $("composerInput").value = restoreText; resizeComposer(); }
        if (restoreAttachments) {
          state.uploadedAttachments = restoreAttachments.map((item) => ({ ...item }));
          state.uploadedAttachment = state.uploadedAttachments[0] || null;
        } else if (restoreAttachment) {
          state.uploadedAttachments = [{ ...restoreAttachment }];
          state.uploadedAttachment = state.uploadedAttachments[0];
        }
        renderAll();
        toast(error.message || "操作失败", "error");
      }
      return false;
    }
  }

  async function sendMessage() {
    const input = $("composerInput");
    if (!state.currentSessionId) { await createSession(); if (!state.currentSessionId) return; }
    if (state.currentSession && state.currentSession.pending) { toast("请先批准或拒绝上方操作，再发送新的消息。", ""); return; }
    if (sessionBusy(state.currentSession)) { toast("当前会话仍在运行，请等待完成。", ""); return; }
    const attachments = Array.isArray(state.uploadedAttachments)
      ? state.uploadedAttachments
      : (state.uploadedAttachment ? [state.uploadedAttachment] : []);
    const typedText = input.value.trim();
    const text = typedText || (attachments.length ? EMPTY_ATTACHMENT_PROMPT : "");
    if (!text) return;
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
    const composed = attachmentNotes.length ? `${text}\n\n${attachmentNotes.join("\n")}` : text;
    const body = { text: composed };
    // Image paths are part of this same user turn. The backend validates the
    // workspace path and turns them into ImageBlock content for the model.
    if (imagePaths.length) body.attachments = imagePaths;
    input.value = ""; resizeComposer(); state.uploadedAttachment = null; state.uploadedAttachments = [];
    await enqueueAction("message", body, state.currentSessionId, {
      optimisticText: composed,
      restoreText: text,
      restoreAttachments: attachments.length ? attachments.map((attachment) => ({ ...attachment })) : null
    });
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

  function releaseVoice() {
    state.voice.generation += 1;
    state.voice.active = false;
    state.voice.target = null;
    const recognition = state.voice.recognition;
    state.voice.recognition = null;
    if (recognition) {
      recognition.onresult = null;
      recognition.onend = null;
      recognition.onerror = null;
      try { recognition.abort ? recognition.abort() : recognition.stop(); } catch (_error) { /* already stopped */ }
    }
    const meter = state.voice.meter;
    state.voice.meter = null;
    if (meter) {
      window.cancelAnimationFrame(meter.frame);
      meter.stream.getTracks().forEach((track) => track.stop());
      if (meter.context && typeof meter.context.close === "function") void meter.context.close().catch(() => {});
    }
    setVoiceVolume(0);
    updateVoiceUi();
  }

  function stopVoiceInput() {
    releaseVoice();
    state.voice.latestText = "";
  }

  function startVoiceMeter(ticket) {
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
      toast("当前环境无法显示麦克风音量；语音识别仍可继续。", "", 3600);
      return;
    }
    void navigator.mediaDevices.getUserMedia({ audio: true }).then((stream) => {
      if (!state.voice.active || state.voice.generation !== ticket) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      let context = null;
      try {
        const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextCtor) throw new Error("audio_context_unavailable");
        context = new AudioContextCtor();
        const source = context.createMediaStreamSource(stream);
        const analyser = context.createAnalyser();
        analyser.fftSize = 512;
        analyser.smoothingTimeConstant = 0.55;
        source.connect(analyser);
        const data = new Uint8Array(analyser.frequencyBinCount);
        const meter = { stream, context, frame: 0 };
        state.voice.meter = meter;
        const tick = () => {
          if (!state.voice.active || state.voice.generation !== ticket || state.voice.meter !== meter) return;
          analyser.getByteFrequencyData(data);
          const mean = data.reduce((sum, value) => sum + value, 0) / data.length;
          setVoiceVolume(Math.min(100, Math.max(0, (mean - 4) * 2.4)));
          meter.frame = window.requestAnimationFrame(tick);
        };
        tick();
      } catch (_error) {
        stream.getTracks().forEach((track) => track.stop());
        if (context && typeof context.close === "function") void context.close().catch(() => {});
        toast("麦克风音量检测初始化失败，语音识别仍可继续。", "", 3600);
      }
    }).catch(() => {
      if (state.voice.active && state.voice.generation === ticket) toast("无法获取麦克风权限，已关闭音量显示。", "", 3600);
    });
  }

  function toggleVoiceInput(targetInput = $("composerInput")) {
    const input = targetInput && targetInput.nodeType === 1 ? targetInput : $("composerInput");
    if (!input) return;
    if (state.voice.active) {
      const sameTarget = state.voice.target === input;
      stopVoiceInput();
      if (sameTarget) return;
    }
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      toast("当前浏览器不支持网页语音识别。可使用 Windows 系统听写（Win+H）后粘贴文字，页面不会伪装成已录音。", "error", 5200);
      return;
    }
    const ticket = ++state.voice.generation;
    state.voice.active = true;
    state.voice.target = input;
    state.voice.latestText = String(input.value || "");
    updateVoiceUi();
    try {
      const recognition = new Recognition();
      state.voice.recognition = recognition;
      recognition.lang = "zh-CN";
      recognition.interimResults = false;
      recognition.continuous = true;
      recognition.onresult = (event) => {
        if (!state.voice.active || state.voice.generation !== ticket || state.voice.target !== input) return;
        let text = "";
        for (let index = event.resultIndex; index < event.results.length; index += 1) {
          if (event.results[index].isFinal !== false) text += event.results[index][0].transcript;
        }
        if (text) {
          state.voice.latestText += text;
          input.value = state.voice.latestText;
          input.dispatchEvent(new Event("input", { bubbles: true }));
          if (input.id === "composerInput") resizeComposer();
        }
      };
      recognition.onend = () => {
        if (state.voice.generation === ticket) stopVoiceInput();
      };
      recognition.onerror = (event) => {
        if (state.voice.generation !== ticket) return;
        stopVoiceInput();
        if (event && event.error !== "aborted") {
          const voiceErrors = {
            "not-allowed": "麦克风权限被拒绝",
            "service-not-allowed": "语音服务不可用",
            "audio-capture": "未检测到可用麦克风",
            "no-speech": "没有检测到语音",
            network: "语音服务网络连接失败"
          };
          toast(`语音识别失败：${voiceErrors[event.error] || "请检查麦克风权限或改用文字输入"}`, "error");
        }
      };
      recognition.start();
      startVoiceMeter(ticket);
    } catch (error) {
      stopVoiceInput();
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
    if (state.voice.active && state.voice.target && !document.contains(state.voice.target)) stopVoiceInput();
    qsa("textarea, input", root).forEach((input) => addVoiceInputControl(input));
    updateVoiceUi();
  }
  function applyResponsiveInspectorDefault() {
    const pane = $("inspectPane");
    const toggle = $("inspectorToggle");
    if (!pane || !toggle || window.innerWidth >= 900) return;
    // The inspector is useful on desktop, but it must not cover the only
    // readable surface in the narrow embedded browser. Keep it closed until
    // the user explicitly opens it with the toolbar button.
    pane.classList.add("collapsed");
    toggle.setAttribute("aria-expanded", "false");
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
      renderAll();
      toast(`已上传 ${results.length} 份材料，发送后才会读取。`, "success");
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
    $("backToChat").addEventListener("click", showSharedChat);
    qsa("[data-profile-tab]").forEach((button) => button.addEventListener("click", () => setProfileTab(button.dataset.profileTab)));
    $("profileDocuments").addEventListener("change", (event) => { profileFiles = Array.from(event.target.files || []); renderProfileFiles(); });
    $("profileImportForm").addEventListener("submit", submitProfilePage);
    $("planPageForm").addEventListener("submit", submitPlanPage);
    $("profileManual").addEventListener("click", async () => { if (!state.currentSessionId) await createSession(); openJobEditor("profile", workflowData().profile || {}); });
    $("profileCopy").addEventListener("click", () => openNewSession(state.currentSessionId));
    $("practiceStarter").addEventListener("click", () => { draftWorkflowPrompt(WORKFLOW_STEPS[3].prompt); });
    $("practiceFiles").addEventListener("click", () => { showSharedChat(); state.selectedTab = "files"; $("inspectPane").classList.remove("collapsed"); renderInspector(); loadFiles(state.filePath); });
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
    $("inspectorToggle").addEventListener("click", () => { const pane = $("inspectPane"); const collapsed = pane.classList.toggle("collapsed"); $("inspectorToggle").setAttribute("aria-expanded", collapsed ? "false" : "true"); });
    $("paneClose").addEventListener("click", () => { $("inspectPane").classList.add("collapsed"); $("inspectorToggle").setAttribute("aria-expanded", "false"); });
    $("dismissTokenNotice").addEventListener("click", () => { state.dismissTokenNotice = true; showTokenNotice(); });
    $("composerForm").addEventListener("submit", (event) => { event.preventDefault(); sendMessage(); });
    $("composerInput").addEventListener("input", resizeComposer);
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
    qsa(".inspect-tab").forEach((button) => button.addEventListener("click", () => { if (!button.classList.contains("hidden")) { state.selectedTab = button.dataset.tab; renderInspector(); if (state.selectedTab === "files" && !state.fileEntries.length) loadFiles(state.filePath); } }));
    $("approvalRegion").addEventListener("click", (event) => { const action = event.target.closest("[data-action]"); if (!action) return; action.disabled = true; if (action.dataset.action === "approve") approveOrReject("approve"); else if (action.dataset.action === "reject") approveOrReject("reject"); else if (action.dataset.action === "resolve") resolveUncertain(); });
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
      if (event.key === "Escape") { closeSidebarDrawer(); closeJdImport(); closeFileModal(); closeModelSettings(); closeJobEditor(); closeNewSession(); closeLocalImport(); }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); openNewSession(); }
    });
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") clearPolling(); else if (state.currentSessionId && sessionBusy(state.currentSession)) startPolling(state.currentSessionId); });
    window.addEventListener("beforeunload", () => { clearPolling(); stopVoiceInput(); });
    window.addEventListener("hashchange", handleTokenHashChange);
    window.addEventListener("resize", handleSidebarResize);
  }

  async function bootstrap() {
    state.token = captureToken();
    bindEvents();
    applyResponsiveInspectorDefault();
    showTokenNotice();
    renderAll();
    await Promise.allSettled([refreshInfo(), refreshSessions(), loadUiSettings()]);
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
