import { useState } from "react";
import { api } from "../api";
import type { SessionState } from "../types";

interface Props {
  sessionId: string;
  state: SessionState | null;
  onState: (s: SessionState) => void;
  onStartAgent?: () => void;
}

const GAP_BADGE = {
  met: { cls: "ok", label: "已满足" },
  weak: { cls: "weak", label: "偏弱" },
  missing: { cls: "missing", label: "缺失" },
  evidence_insufficient: { cls: "insufficient", label: "证据不足" },
} as const;

export default function PlanPage({ sessionId, state, onState, onStartAgent }: Props) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "error" | "info"; text: string } | null>(null);
  const [minStars, setMinStars] = useState(50);
  const [rerank, setRerank] = useState(true);

  const plan = state?.plan ?? null;
  const repos = state?.repos ?? [];
  const ready = !!state?.jd && !!state?.profile;

  const build = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const res = await api.buildPlan(sessionId, minStars, rerank);
      onState({
        ...(state ?? { session_id: sessionId, repos: [], interview_history: [], coach_history: [] }),
        plan: res.plan,
        repos: res.repos,
      });
      setMsg({ kind: "info", text: "计划已生成 ✅" });
    } catch (e) {
      setMsg({ kind: "error", text: String((e as Error)?.message ?? e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <h2 className="page-title">学习计划 & 复现项目</h2>
      <p className="page-sub">
        基于「岗位要求 vs 你的技能档案」做差距分析，输出分阶段学习计划，并到 GitHub 检索可复现项目。
      </p>

      {!ready && (
        <div className="alert error">
          还缺前置条件：{!state?.jd ? "① 岗位 JD（去「岗位 JD」页上传截图）" : ""}
          {!state?.jd && !state?.profile ? " 且 " : ""}
          {!state?.profile ? "② 技能档案（去「技能档案」页填写）" : ""}
        </div>
      )}

      {msg && <div className={`alert ${msg.kind}`}>{msg.text}</div>}

      <div className="card">
        <div className="field-row" style={{ maxWidth: 480 }}>
          <div>
            <label>GitHub 最低 star 数</label>
            <input type="number" value={minStars} onChange={(e) => setMinStars(Number(e.target.value))} />
          </div>
          <div>
            <label>使用 LLM 重排项目（更贴合你的差距）</label>
            <select value={rerank ? "1" : "0"} onChange={(e) => setRerank(e.target.value === "1")}>
              <option value="1">是</option>
              <option value="0">否（更快）</option>
            </select>
          </div>
        </div>
        <button className="btn" onClick={build} disabled={busy || !ready} style={{ marginTop: 14 }}>
          {busy ? (
            <>
              <span className="spinner" />
              分析中（差距分析 + GitHub 检索，约 1–2 分钟）…
            </>
          ) : (
            "生成学习计划 + 检索项目"
          )}
        </button>
      </div>

      {plan && (
        <div className="card">
          <h3>差距分析（{plan.direction}）</h3>
          <table>
            <thead>
              <tr>
                <th>岗位要求</th>
                <th>你的匹配</th>
                <th>状态</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {plan.gaps.map((g, i) => (
                <tr key={i}>
                  <td>{g.requirement}</td>
                  <td>{g.matched_skill ?? "—"}</td>
                  <td>
                    <span className={`badge ${GAP_BADGE[g.status].cls}`}>{GAP_BADGE[g.status].label}</span>
                  </td>
                  <td className="page-sub" style={{ fontSize: 12 }}>
                    {g.note}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {plan && (
        <div className="card">
          <h3>分阶段学习计划</h3>
          {plan.phases.map((p, i) => (
            <div className="list-item" key={i}>
              <p style={{ margin: 0, fontWeight: 700 }}>
                {p.phase}
                {p.duration_weeks ? <span className="page-sub"> · 约 {p.duration_weeks} 周</span> : null}
              </p>
              <p className="page-sub" style={{ margin: "4px 0" }}>
                {p.goal}
              </p>
              {p.topics.map((t) => (
                <span className="chip" key={t}>
                  {t}
                </span>
              ))}
              {p.suggested_practice && (
                <p className="page-sub" style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>
                  练习：{p.suggested_practice}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {plan && onStartAgent && (
        <div className="card" style={{ borderColor: "var(--accent)" }}>
          <h3>下一步：让 Agent 带你把计划做出来</h3>
          <p className="page-sub">
            差距分析、分阶段计划、复现项目会一并交给「④ Agent 实操」——它会在你电脑上按计划逐个补技能点，用项目实践验证。
          </p>
          <button className="btn" onClick={onStartAgent}>
            → 用 Agent 开始实操
          </button>
        </div>
      )}

      {repos.length > 0 && (
        <div className="card">
          <h3>GitHub 复现项目（{repos.length} 个）</h3>
          {repos.map((r) => (
            <div className="list-item" key={r.full_name}>
              <p style={{ margin: 0, fontWeight: 700 }}>
                <a href={r.html_url} target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>
                  {r.full_name}
                </a>
                <span className="chip" style={{ marginLeft: 8 }}>
                  ⭐ {r.stargazers_count}
                </span>
                {r.language && <span className="chip">{r.language}</span>}
                <span className="page-sub" style={{ fontSize: 11 }}>
                  {" "}
                  匹配度 {Math.round(r.match_score * 100)}%
                </span>
              </p>
              <p className="page-sub" style={{ margin: "4px 0" }}>
                {r.description}
              </p>
              <p className="page-sub" style={{ margin: 0, fontSize: 12, color: "var(--ok)" }}>
                💡 {r.reason}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
