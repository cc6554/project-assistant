// sidecar HTTP 客户端（本地 FastAPI，127.0.0.1:17689）
import type {
  ConfigInfo,
  GitHubRepo,
  JDCard,
  LearningPlan,
  SessionState,
  SessionSummary,
  UserSkillProfile,
} from "./types";

const BASE = "http://127.0.0.1:17689";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, init);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  health: () => req<{ ok: boolean }>("/health"),

  // 会话
  listSessions: () => req<SessionSummary[]>("/api/sessions"),
  createSession: (sourceSessionId?: string) =>
    req<{ id: string }>(
      "/api/sessions",
      json("POST", sourceSessionId ? { source_session_id: sourceSessionId } : {}),
    ),
  getSession: (id: string) => req<SessionState>(`/api/sessions/${id}`),
  deleteSession: (id: string) => req<{ ok: boolean }>(`/api/sessions/${id}`, { method: "DELETE" }),

  // JD
  parseJd: (sessionId: string, files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return req<{ jd: JDCard; uploads: string[] }>(`/api/sessions/${sessionId}/jd`, {
      method: "POST",
      body: form,
    });
  },
  searchJd: (sessionId: string, query: string) =>
    req<{ jd: JDCard; sources: number }>(
      `/api/sessions/${sessionId}/jd/search`,
      json("POST", { query }),
    ),
  updateJd: (sessionId: string, jd: JDCard) =>
    req<{ ok: boolean; jd: JDCard }>(`/api/sessions/${sessionId}/jd`, json("PUT", jd)),

  // 档案
  profileFromText: (sessionId: string, text: string, source: string) =>
    req<UserSkillProfile>(`/api/sessions/${sessionId}/profile/text`, json("POST", { text, source })),
  profileFromDocuments: (sessionId: string, source: string, files: File[]) => {
    const form = new FormData();
    form.append("source", source);
    files.forEach((f) => form.append("files", f));
    return req<UserSkillProfile>(`/api/sessions/${sessionId}/profile/documents`, {
      method: "POST",
      body: form,
    });
  },
  profileFromObsidian: (sessionId: string, vaultPath: string) =>
    req<UserSkillProfile & { read_files: string[]; failed_files: string[] }>(
      `/api/sessions/${sessionId}/profile/obsidian`,
      json("POST", { vault_path: vaultPath }),
    ),
  profileFromPath: (sessionId: string, localPath: string) =>
    req<UserSkillProfile & { read_files: string[]; failed_files: string[] }>(
      `/api/sessions/${sessionId}/profile/path`,
      json("POST", { path: localPath }),
    ),

  // 待澄清对话
  clarifyNext: (sessionId: string) =>
    req<{ question: string | null; remaining: number; done: boolean }>(
      `/api/sessions/${sessionId}/profile/clarify/next`,
    ),
  clarifyAnswer: (sessionId: string, question: string, answer: string) =>
    req<{ profile: UserSkillProfile; question: string | null; remaining: number; done: boolean }>(
      `/api/sessions/${sessionId}/profile/clarify/answer`,
      json("POST", { question, answer }),
    ),
  clarifyChat: (sessionId: string, message: string, history: { role: string; content: string }[]) =>
    req<{
      kind: string;
      reply: string;
      question: string | null;
      remaining: number;
      done: boolean;
      profile?: UserSkillProfile | null;
    }>(
      `/api/sessions/${sessionId}/profile/clarify/chat`,
      json("POST", { message, history }),
    ),

  // 访谈
  nextQuestion: (sessionId: string) =>
    req<{ question: string | null; done: boolean }>(`/api/sessions/${sessionId}/interview/question`),
  answerQuestion: (sessionId: string, question: string, answer: string) =>
    req<UserSkillProfile>(
      `/api/sessions/${sessionId}/interview/answer`,
      json("POST", { question, answer }),
    ),

  // 计划 + GitHub
  buildPlan: (sessionId: string, minStars = 50, llmRerank = true) =>
    req<{ plan: LearningPlan; repos: GitHubRepo[] }>(
      `/api/sessions/${sessionId}/plan`,
      json("POST", { min_stars: minStars, llm_rerank: llmRerank }),
    ),

  // 导师指导
  coachStart: (sessionId: string, projectFullName: string) =>
    req<{ message: string; history: [string, string][] }>(
      `/api/sessions/${sessionId}/coach/start`,
      json("POST", { project_full_name: projectFullName }),
    ),
  coachChat: (sessionId: string, message: string) =>
    req<{ message: string; history: [string, string][] }>(
      `/api/sessions/${sessionId}/coach`,
      json("POST", { message }),
    ),

  // 实操 Agent（能操作电脑）——SSE 流式，返回原始 Response 由页面逐步解析
  agentTurnStream: (sessionId: string, message: string) =>
    fetch(BASE + `/api/sessions/${sessionId}/agent/turn`, json("POST", { message })),
  agentConfirmStream: (sessionId: string, actionId: string, approve: boolean) =>
    fetch(BASE + `/api/sessions/${sessionId}/agent/confirm`, json("POST", { action_id: actionId, approve })),
  agentReset: (sessionId: string) =>
    req<{ ok: boolean }>(`/api/sessions/${sessionId}/agent/reset`, { method: "POST" }),

  // 模型配置
  getConfig: () => req<ConfigInfo>("/api/config"),
  putConfig: (cfg: {
    providers: {
      name: string;
      api_mode: string;
      base_url: string;
      api_key_env?: string | null;
      api_key?: string | null;
      timeout: number;
    }[];
    tasks: Record<string, { provider: string; model: string }[]>;
  }) => req<{ ok: boolean }>("/api/config", json("PUT", cfg)),
};
