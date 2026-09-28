import { useRef, useState } from "react";
import { api } from "../api";
import type { SessionState, UserSkillProfile } from "../types";

interface Props {
  sessionId: string;
  state: SessionState | null;
  onState: (s: SessionState) => void;
}

type Tab = "text" | "documents" | "obsidian" | "interview";

const PROF = { aware: "了解", working: "可用", proficient: "熟练", expert: "精通" } as const;

export default function ProfilePage({ sessionId, state, onState }: Props) {
  const [tab, setTab] = useState<Tab>("text");
  const [text, setText] = useState("");
  const [source, setSource] = useState("self_report");
  const [files, setFiles] = useState<File[]>([]);
  const [vaultPath, setVaultPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "error" | "info"; text: string } | null>(null);
  const [question, setQuestion] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");
  const [asking, setAsking] = useState(false);
  // 待澄清对话
  const [clarifyOpen, setClarifyOpen] = useState(false);
  const [clarifyQ, setClarifyQ] = useState<string | null>(null);
  const [clarifyA, setClarifyA] = useState("");
  const [clarifyBusy, setClarifyBusy] = useState(false);
  const docRef = useRef<HTMLInputElement>(null);

  const profile = state?.profile ?? null;

  const updateProfile = (p: UserSkillProfile) => {
    onState({
      ...(state ?? { session_id: sessionId, repos: [], interview_history: [], coach_history: [] }),
      profile: p,
    });
  };

  /** 资料解析并入档案后：若有待澄清，自动进入对话式澄清。 */
  const openClarifyIfAny = async (p: UserSkillProfile) => {
    if (!p.open_questions.length) return;
    setClarifyOpen(true);
    setClarifyBusy(true);
    try {
      const res = await api.clarifyNext(sessionId);
      setClarifyQ(res.question);
      setClarifyA("");
      if (res.done) setClarifyOpen(false);
    } catch (e) {
      setMsg({ kind: "error", text: String((e as Error)?.message ?? e) });
    } finally {
      setClarifyBusy(false);
    }
  };

  const run = async (fn: () => Promise<unknown>, okText: string) => {
    setBusy(true);
    setMsg(null);
    try {
      const p = (await fn()) as UserSkillProfile;
      updateProfile(p);
      setMsg({ kind: "info", text: okText });
      void openClarifyIfAny(p);
    } catch (e) {
      setMsg({ kind: "error", text: String((e as Error)?.message ?? e) });
    } finally {
      setBusy(false);
    }
  };

  const askNext = async () => {
    setAsking(true);
    setMsg(null);
    try {
      const res = await api.nextQuestion(sessionId);
      setQuestion(res.question);
      setAnswer("");
      if (res.done) setMsg({ kind: "info", text: "访谈结束（已达最大轮次）" });
    } catch (e) {
      setMsg({ kind: "error", text: String((e as Error)?.message ?? e) });
    } finally {
      setAsking(false);
    }
  };

  const submitAnswer = () => {
    if (!question || !answer.trim()) return;
    run(
      async () => {
        const p = await api.answerQuestion(sessionId, question, answer);
        return p;
      },
      "答案已并入技能档案 ✅",
    ).then(() => setQuestion(null));
  };

  const submitClarify = async () => {
    if (!clarifyQ || !clarifyA.trim()) return;
    setClarifyBusy(true);
    setMsg(null);
    try {
      const res = await api.clarifyAnswer(sessionId, clarifyQ, clarifyA);
      updateProfile(res.profile);
      setClarifyQ(res.question);
      setClarifyA("");
      if (res.done) {
        setClarifyOpen(false);
        setMsg({ kind: "info", text: "待澄清内容已全部确认 ✅ 技能档案更新完成" });
      }
    } catch (e) {
      setMsg({ kind: "error", text: String((e as Error)?.message ?? e) });
    } finally {
      setClarifyBusy(false);
    }
  };

  const submitText = () => {
    if (!text.trim()) return;
    run(
      () => api.profileFromText(sessionId, text, source),
      "技能档案已更新 ✅",
    ).then(() => setText(""));
  };

  const submitDocs = () => {
    if (!files.length) return;
    run(
      () => api.profileFromDocuments(sessionId, source, files),
      "文档技能已并入档案 ✅",
    ).then(() => setFiles([]));
  };

  const submitObsidian = () => {
    if (!vaultPath.trim()) return;
    run(
      () => api.profileFromObsidian(sessionId, vaultPath.trim()),
      "Obsidian 笔记已并入档案 ✅",
    );
  };

  return (
    <div>
      <h2 className="page-title">技能档案</h2>
      <p className="page-sub">
        告诉 AI 你现在会什么（自述 / 简历 / 工作日志 / Obsidian 笔记 / 问答访谈），解析后如有疑问会直接问你，而不是留一堆待办让你自己看。
      </p>

      {msg && <div className={`alert ${msg.kind}`}>{msg.text}</div>}

      {/* 待澄清对话：资料解析后自动弹出，逐个问题交互 */}
      {clarifyOpen && (
        <div className="card" style={{ borderColor: "var(--accent)" }}>
          <h3>有几个问题需要当面确认</h3>
          {clarifyBusy ? (
            <p>
              <span className="spinner" /> 读取下一条问题…
            </p>
          ) : clarifyQ ? (
            <div>
              <div className="chat-line">
                <div className="q">Q：{clarifyQ}</div>
              </div>
              <textarea
                placeholder="直接回答：做过什么、做到什么程度、有没有项目能证明…"
                value={clarifyA}
                onChange={(e) => setClarifyA(e.target.value)}
              />
              <div style={{ marginTop: 10, display: "flex", gap: 10 }}>
                <button className="btn" onClick={submitClarify} disabled={clarifyBusy || !clarifyA.trim()}>
                  回答并继续
                </button>
                <button
                  className="btn secondary"
                  onClick={async () => {
                    setClarifyBusy(true);
                    try {
                      const res = await api.clarifyAnswer(sessionId, clarifyQ, "这个问题我暂时没有更多信息。");
                      updateProfile(res.profile);
                      setClarifyQ(res.question);
                      setClarifyA("");
                      if (res.done) {
                        setClarifyOpen(false);
                        setMsg({ kind: "info", text: "待澄清内容已处理 ✅" });
                      }
                    } catch (e) {
                      setMsg({ kind: "error", text: String((e as Error)?.message ?? e) });
                    } finally {
                      setClarifyBusy(false);
                    }
                  }}
                >
                  跳过这个
                </button>
              </div>
            </div>
          ) : (
            <p>没有待澄清问题了。</p>
          )}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        {(["text", "documents", "obsidian", "interview"] as Tab[]).map((t) => (
          <button
            key={t}
            className={`nav-item ${tab === t ? "active" : ""}`}
            style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "7px 16px", cursor: "pointer" }}
            onClick={() => setTab(t)}
          >
            {t === "text" ? "文本自述 / 简历 / 日志" : t === "documents" ? "上传文档" : t === "obsidian" ? "Obsidian 笔记库" : "访谈问答"}
          </button>
        ))}
      </div>

      {tab === "text" && (
        <div className="card">
          <label>材料类型</label>
          <select value={source} onChange={(e) => setSource(e.target.value)} style={{ maxWidth: 260 }}>
            <option value="self_report">技能自述（self_report）</option>
            <option value="resume">简历（resume）</option>
            <option value="work_log">工作日志（work_log）</option>
          </select>
          <label>内容</label>
          <textarea
            placeholder="粘贴你的技能自述 / 简历要点 / 工作日志，越具体越好，例如：我用 Python 写过爬虫，对 FastAPI 比较熟，PyTorch 只会调库…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            style={{ minHeight: 160 }}
          />
          <button className="btn" onClick={submitText} disabled={busy || !text.trim()}>
            {busy ? "解析中…" : "解析并入档案"}
          </button>
        </div>
      )}

      {tab === "documents" && (
        <div className="card">
          <label>文档类型</label>
          <select value={source} onChange={(e) => setSource(e.target.value)} style={{ maxWidth: 260 }}>
            <option value="resume">简历（resume）</option>
            <option value="work_log">工作日志（work_log）</option>
          </select>
          <div
            className="upload-zone"
            onClick={() => docRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              setFiles(Array.from(e.dataTransfer.files));
            }}
          >
            {files.length ? `已选 ${files.length} 个：${files.map((f) => f.name).join("、")}` : "点击或拖拽 PDF / TXT / Markdown 文档"}
          </div>
          <input ref={docRef} type="file" accept=".pdf,.txt,.md" multiple hidden onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
          <button className="btn" onClick={submitDocs} disabled={busy || !files.length}>
            {busy ? "解析中…" : "解析并入档案"}
          </button>
        </div>
      )}

      {tab === "obsidian" && (
        <div className="card">
          <h3>从 Obsidian 读取经历日志</h3>
          <p className="page-sub">
            填入你的 Obsidian 库路径，应用读取其中最近的 Markdown 笔记（默认 20 篇，跳过 .obsidian / 附件 / 模板 / 程序目录），
            从中抽取你的技能与经历。日记、周记、项目笔记都会成为档案证据。
            建议填你个人的笔记目录，不要填程序或项目根目录，避免读入无关文件。
          </p>
          <label>Obsidian 库路径（Vault 目录）</label>
          <input
            placeholder="例如 D:\我的笔记 或 /home/cxy/Documents/Obsidian"
            value={vaultPath}
            onChange={(e) => setVaultPath(e.target.value)}
          />
          <button className="btn" onClick={submitObsidian} disabled={busy || !vaultPath.trim()}>
            {busy ? "读取并解析中…" : "读取并解析"}
          </button>
        </div>
      )}

      {tab === "interview" && (
        <div className="card">
          <h3>访谈问答</h3>
          <p className="page-sub">
            系统基于你的档案和岗位要求向你提问（最多 8 轮），你的回答会被并入技能档案，作为差距判断的证据。
          </p>
          {!question && (
            <button className="btn" onClick={askNext} disabled={asking}>
              {asking ? "思考中…" : "问下一个问题"}
            </button>
          )}
          {question && (
            <div>
              <div className="chat-line">
                <div className="q">Q：{question}</div>
              </div>
              <textarea placeholder="你的回答（可详述项目经历、掌握程度、使用频率…）" value={answer} onChange={(e) => setAnswer(e.target.value)} />
              <div style={{ marginTop: 10, display: "flex", gap: 10 }}>
                <button className="btn" onClick={submitAnswer} disabled={busy || !answer.trim()}>
                  提交回答
                </button>
                <button className="btn secondary" onClick={() => setQuestion(null)}>
                  跳过
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {profile && (
        <div className="card">
          <h3>当前档案（{profile.skills.length} 项技能）</h3>
          {profile.summary && <p className="page-sub">{profile.summary}</p>}
          <table>
            <thead>
              <tr>
                <th>技能</th>
                <th>程度</th>
                <th>证据</th>
              </tr>
            </thead>
            <tbody>
              {profile.skills.map((s) => (
                <tr key={s.name}>
                  <td style={{ fontWeight: 600 }}>{s.name}</td>
                  <td>{PROF[s.proficiency]}</td>
                  <td className="page-sub" style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>
                    {s.evidence || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {profile.open_questions.length > 0 && (
            <div className="alert info" style={{ marginTop: 12 }}>
              还有 {profile.open_questions.length} 条待澄清问题未回答
              <button
                className="btn secondary"
                style={{ marginLeft: 10, padding: "4px 12px" }}
                onClick={() => setClarifyOpen(true)}
              >
                现在回答
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
