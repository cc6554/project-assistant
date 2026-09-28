import { useEffect, useState } from "react";
import { api } from "./api";
import type { SessionState, SessionSummary } from "./types";
import JobsPage from "./pages/Jobs";
import ProfilePage from "./pages/Profile";
import PlanPage from "./pages/Plan";
import AgentPage from "./pages/Agent";
import ConfigPage from "./pages/Config";

type Page = "jobs" | "profile" | "plan" | "agent" | "config";

const PAGES: { key: Page; label: string }[] = [
  { key: "jobs", label: "① 岗位 JD" },
  { key: "profile", label: "② 技能档案" },
  { key: "plan", label: "③ 计划 & 项目" },
  { key: "agent", label: "④ Agent 实操" },
  { key: "config", label: "⚙ 模型配置" },
];

export default function App() {
  const [page, setPage] = useState<Page>("jobs");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [state, setState] = useState<SessionState | null>(null);
  const [sidecarOk, setSidecarOk] = useState(false);
  const [checking, setChecking] = useState(true);
  const [agentPrefill, setAgentPrefill] = useState<string>("");
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    try {
      return localStorage.getItem("jr-theme") === "light" ? "light" : "dark";
    } catch {
      return "dark";
    }
  });

  /** 主题切换：写入 <html data-theme>，并持久化到 localStorage。 */
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("jr-theme", theme);
    } catch {
      /* 忽略存储失败 */
    }
  }, [theme]);

  /** 从「③ 计划 & 项目」跳转到 Agent 实操，并带上基于计划的引导语。 */
  const startAgentFromPlan = () => {
    const plan = state?.plan;
    if (!plan) {
      setPage("agent");
      return;
    }
    const top = plan.gaps.find((g) => g.status === "missing" || g.status === "weak") ?? plan.gaps[0];
    const firstPhase = plan.phases[0];
    const repo = state?.repos?.[0];
    const lines = [
      `请按学习计划带我实操补足技能点。目标方向：「${plan.direction}」。`,
      top ? `当前最大差距：${top.requirement}（${top.status === "missing" ? "缺失" : top.status === "weak" ? "偏弱" : "证据不足"}）。` : "",
      firstPhase ? `从第一阶段开始：「${firstPhase.phase}」——${firstPhase.goal}` : "",
      repo ? `用项目 ${repo.full_name} 作为实践载体，把练习拆成对应技能点。` : "",
      "先给我一个今天的可执行计划，然后开始动手。",
    ].filter(Boolean);
    setAgentPrefill(lines.join("\n"));
    setPage("agent");
  };

  useEffect(() => {
    const check = async () => {
      try {
        await api.health();
        setSidecarOk(true);
        const list = await api.listSessions();
        setSessions(list);
        if (list.length && !sessionId) setSessionId(list[0].id);
        setChecking(false);
      } catch {
        setSidecarOk(false);
        setChecking(false);
      }
    };
    void check();
    const timer = window.setInterval(check, 5000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!sessionId) return;
    api
      .getSession(sessionId)
      .then(setState)
      .catch(() => setState(null));
  }, [sessionId]);

  const refreshSessions = (keepId = sessionId) =>
    api.listSessions().then((list) => {
      setSessions(list);
      if (!keepId || !list.some((s) => s.id === keepId)) {
        if (list.length) {
          setSessionId(list[0].id);
        } else {
          setSessionId(null);
          setState(null);
        }
      }
    });

  const newSession = async () => {
    const res = await api.createSession();
    setSessionId(res.id);
    setState(null);
    await refreshSessions(res.id);
    setPage("jobs");
  };

  const selectSession = async (id: string) => {
    setSessionId(id);
    setPage("jobs");
  };

  const removeSession = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    await api.deleteSession(id);
    await refreshSessions();
  };

  if (checking) return <div className="app" style={{ alignItems: "center", justifyContent: "center" }}><span className="spinner" />正在连接本地服务…</div>;

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">📡 校招雷达</div>
        {PAGES.map((p) => (
          <button key={p.key} className={`nav-item ${page === p.key ? "active" : ""}`} onClick={() => setPage(p.key)}>
            {p.label}
          </button>
        ))}
        <div className="sess-head">会话（岗位分析任务）</div>
        {sessions.map((s) => (
          <div key={s.id} className={`sess-item ${sessionId === s.id ? "active" : ""}`} onClick={() => selectSession(s.id)}>
            <span>{s.job_title || s.company || "（未命名岗位）"}</span>
            <span className="sub">
              {s.skill_count ? `${s.skill_count} 技能 · ` : ""}
              {s.interview_rounds ? `${s.interview_rounds} 轮访谈 · ` : ""}
              {s.has_plan ? "有计划 ✓" : "未出计划"}
            </span>
            <button
              className="btn danger"
              style={{ padding: "2px 8px", fontSize: 11, alignSelf: "flex-end", marginTop: 2 }}
              onClick={(e) => removeSession(e, s.id)}
            >
              删除
            </button>
          </div>
        ))}
        <button className="btn-new" onClick={newSession}>
          ＋ 新建会话
        </button>
        <div className="theme-switch">
          <button
            className={`theme-opt ${theme === "dark" ? "active" : ""}`}
            onClick={() => setTheme("dark")}
          >
            深色
          </button>
          <button
            className={`theme-opt ${theme === "light" ? "active" : ""}`}
            onClick={() => setTheme("light")}
          >
            浅色
          </button>
        </div>
        <div className="page-sub" style={{ padding: "0 8px" }}>
          {sidecarOk ? (
            <span style={{ color: "var(--ok)" }}>● 本地服务正常</span>
          ) : (
            <span style={{ color: "var(--danger)" }}>● 本地服务未连接</span>
          )}
        </div>
      </aside>

      <main className="main">
        {!sidecarOk ? (
          <div className="alert error">本地 AI 服务未启动（127.0.0.1:17689），请重启应用。</div>
        ) : !sessionId ? (
          <div className="card">
            <h3>开始之前</h3>
            <p className="page-sub">先新建一个会话，然后按 ①→②→③ 的顺序操作：上传岗位截图 → 建立技能档案 → 生成计划。</p>
            <button className="btn" onClick={newSession}>
              ＋ 新建会话
            </button>
          </div>
        ) : (
          <>
            {page === "jobs" && <JobsPage sessionId={sessionId} state={state} onState={setState} />}
            {page === "profile" && <ProfilePage sessionId={sessionId} state={state} onState={setState} />}
            {page === "plan" && (
              <PlanPage
                sessionId={sessionId}
                state={state}
                onState={setState}
                onStartAgent={startAgentFromPlan}
              />
            )}
            {page === "agent" && (
              <AgentPage
                sessionId={sessionId}
                state={state}
                onState={setState}
                prefill={agentPrefill}
                onPrefillUsed={() => setAgentPrefill("")}
              />
            )}
            {page === "config" && <ConfigPage />}
          </>
        )}
      </main>
    </div>
  );
}
