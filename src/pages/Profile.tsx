import { useRef, useState } from "react";
import { api } from "../api";
import VoiceField from "../components/VoiceField";
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
  // 待澄清对话（自由对话式）
  const [clarifyOpen, setClarifyOpen] = useState(false);
  const [clarifyA, setClarifyA] = useState("");
  const [clarifyBusy, setClarifyBusy] = useState(false);
  const [clarifyMsgs, setClarifyMsgs] = useState<{ role: "assistant" | "user"; content: string }[]>([]);
  // 待澄清提纲：系统生成的问题清单给 AI 作参考，AI 用自然对话问出，回复是主力
  const [clarifyOutline, setClarifyOutline] = useState<string[]>([]);
  const [clarifyOutlineOpen, setClarifyOutlineOpen] = useState(true);
  const [clarifyRemaining, setClarifyRemaining] = useState(0);
  const docRef = useRef<HTMLInputElement>(null);
  const clarifyCardRef = useRef<HTMLDivElement>(null);

  const profile = state?.profile ?? null;

  const updateProfile = (p: UserSkillProfile) => {
    onState({
      ...(state ?? { session_id: sessionId, repos: [], interview_history: [], coach_history: [] }),
      profile: p,
    });
  };

  /** 打开待澄清弹窗：加载提纲 + AI 自然开场（把第一条问题口语化问出）。 */
  const loadClarifyStart = async () => {
    setClarifyBusy(true);
    try {
      const res = await api.clarifyStart(sessionId);
      if (res.done) {
        setClarifyOpen(false);
        setMsg({ kind: "info", text: "待澄清内容已全部确认 ✅ 技能档案更新完成" });
        return;
      }
      setClarifyOutline(res.outline);
      setClarifyOutlineOpen(true);
      setClarifyRemaining(res.remaining);
      setClarifyA("");
      setClarifyMsgs(res.opening ? [{ role: "assistant", content: res.opening }] : []);
    } catch (e) {
      setMsg({ kind: "error", text: String((e as Error)?.message ?? e) });
    } finally {
      setClarifyBusy(false);
    }
  };

  /** 打开待澄清弹窗并自动开场。 */
  const openClarify = () => {
    setClarifyMsgs([]);
    setClarifyOutline([]);
    setClarifyA("");
    setClarifyOpen(true);
    void loadClarifyStart();
  };

  /** 资料解析并入档案后：若有待澄清，自动进入对话式澄清。 */
  const openClarifyIfAny = async (p: UserSkillProfile) => {
    if (!p.open_questions.length) return;
    openClarify();
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

  /** 自由对话发送：回答当前问题 / 追问"什么意思" / 跳过 / 补充背景，交给后端判定。 */
  const sendClarify = async (text?: string) => {
    const msg = (text ?? clarifyA).trim();
    if (!msg) return;
    const history = clarifyMsgs;
    const userMsg: { role: "user"; content: string } = { role: "user", content: msg };
    setClarifyBusy(true);
    setClarifyMsgs((msgs) => [...msgs, userMsg]);
    setClarifyA("");
    try {
      const res = await api.clarifyChat(sessionId, msg, history);
      if (res.profile) updateProfile(res.profile);
      const next = [...history, userMsg];
      if (res.reply && res.reply.trim()) next.push({ role: "assistant", content: res.reply });
      setClarifyRemaining(res.remaining ?? 0);
      if (res.done) {
        setClarifyOpen(false);
        setMsg({ kind: "info", text: "待澄清内容已全部确认 ✅ 技能档案更新完成" });
      } else {
        setClarifyMsgs(next);
      }
    } catch (e) {
      setMsg({ kind: "error", text: String((e as Error)?.message ?? e) });
    } finally {
      setClarifyBusy(false);
    }
  };

  /** 主动结束核对：关闭弹窗，剩余问题保留在档案里，之后可随时继续。 */
  const finishClarify = () => {
    setClarifyOpen(false);
    setMsg({
      kind: "info",
      text:
        clarifyRemaining > 0
          ? `已暂停核对，剩余 ${clarifyRemaining} 条问题保留在档案里，可随时回来继续。`
          : "待澄清内容已全部确认 ✅ 技能档案更新完成",
    });
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

  const submitPath = () => {
    if (!vaultPath.trim()) return;
    run(
      () => api.profileFromPath(sessionId, vaultPath.trim()),
      "本地路径文件已并入档案 ✅",
    );
  };

  return (
    <div>
      <h2 className="page-title">技能档案</h2>
      <p className="page-sub">
        告诉 AI 你现在会什么（自述 / 简历 / 工作日志 / 上传文档 / 本地路径读取 / 问答访谈），解析后如有疑问会直接问你，而不是留一堆待办让你自己看。
      </p>

      {msg && <div className={`alert ${msg.kind}`}>{msg.text}</div>}

      {/* 待澄清对话：自由对话弹窗（可回答，也可随时追问"什么意思"） */}
      {clarifyOpen && (
        <div className="modal-overlay">
          <div ref={clarifyCardRef} className="modal">
            <button
              className="modal-close"
              onClick={() => setClarifyOpen(false)}
              title="关闭"
            >
              ✕
            </button>
            <h3 style={{ margin: "0 0 4px" }}>
              待澄清对话
              {clarifyRemaining > 0 && (
                <span style={{ fontSize: 12, color: "var(--muted)", marginLeft: 8 }}>
                  还剩 {clarifyRemaining} 条
                </span>
              )}
            </h3>
            {clarifyOutline.length > 0 && (
              <div
                className="card"
                style={{ margin: "0 0 10px", padding: "8px 12px", cursor: "pointer" }}
                onClick={() => setClarifyOutlineOpen((v) => !v)}
              >
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: clarifyOutlineOpen ? 6 : 0 }}>
                  待澄清提纲 {clarifyOutlineOpen ? "▾" : "▸"}
                </div>
                {clarifyOutlineOpen && (
                  <ol style={{ margin: 0, paddingLeft: 20, fontSize: 12, color: "var(--muted)" }}>
                    {clarifyOutline.map((q, i) => (
                      <li key={i}>{q}</li>
                    ))}
                  </ol>
                )}
              </div>
            )}
            <div className="clarify-chat">
              {clarifyMsgs.map((m, i) => (
                <div key={i} className={`clarify-msg ${m.role}`}>
                  {m.content}
                </div>
              ))}
              {clarifyBusy && (
                <div className="clarify-msg assistant">
                  <span className="spinner" /> 思考中…
                </div>
              )}
            </div>
            <VoiceField
              placeholder="直接回答；也可以随时问：这个问题什么意思？（回车发送，Shift+回车换行）"
              value={clarifyA}
              onChange={setClarifyA}
              minHeight={60}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void sendClarify();
                }
              }}
            />
            <div style={{ marginTop: 10, display: "flex", gap: 10 }}>
              <button className="btn" onClick={() => sendClarify()} disabled={clarifyBusy || !clarifyA.trim()}>
                发送
              </button>
              <button
                className="btn secondary"
                onClick={() => sendClarify("跳过")}
                disabled={clarifyBusy}
              >
                跳过当前问题
              </button>
              <button
                className="btn secondary"
                onClick={finishClarify}
                disabled={clarifyBusy}
                title="结束本轮核对，剩余问题保留，之后可继续"
              >
                完成核对
              </button>
            </div>
          </div>
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
            {t === "text" ? "文本自述 / 简历 / 日志" : t === "documents" ? "上传文件" : t === "obsidian" ? "本地路径 / Obsidian" : "访谈问答"}
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
          <VoiceField
            placeholder="粘贴你的技能自述 / 简历要点 / 工作日志，越具体越好，例如：我用 Python 写过爬虫，对 FastAPI 比较熟，PyTorch 只会调库…"
            value={text}
            onChange={setText}
            minHeight={160}
          />
          <button className="btn" onClick={submitText} disabled={busy || !text.trim()}>
            {busy ? "解析中…" : "解析并入档案"}
          </button>
        </div>
      )}

      {tab === "documents" && (
        <div className="card">
          <label>文件类型（证据来源）</label>
          <select value={source} onChange={(e) => setSource(e.target.value)} style={{ maxWidth: 260 }}>
            <option value="resume">简历（resume）</option>
            <option value="work_log">工作日志（work_log）</option>
          </select>
          <p className="page-sub">
            任意类型文件都可以上传，数量不限。支持 PDF / Word / Excel / PPT / TXT / Markdown / JSON / CSV 自动解析，
            其他类型会跳过并在结果里提示（不会被当作档案内容）。
          </p>
          <div
            className="upload-zone"
            onClick={() => docRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              setFiles(Array.from(e.dataTransfer.files));
            }}
          >
            {files.length ? `已选 ${files.length} 个：${files.map((f) => f.name).join("、")}` : "点击或拖拽任意文件（可多选）"}
          </div>
          <input ref={docRef} type="file" multiple hidden onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
          <button className="btn" onClick={submitDocs} disabled={busy || !files.length}>
            {busy ? "解析中…" : "解析并入档案"}
          </button>
        </div>
      )}

      {tab === "obsidian" && (
        <div className="card">
          <h3>从本地路径读取经历（Obsidian 库 / 任意笔记目录）</h3>
          <p className="page-sub">
            填一个本地目录或文件路径，Agent 自己读取路径下的全部文件（支持 .md / .txt / .log / .pdf，数量不限制，跳过附件 / 模板 / 程序目录），
            从中抽取你的技能与经历。Obsidian 库直接填 Vault 目录即可。
            文件多时全部解析可能需要几分钟。
          </p>
          <label>本地路径（目录或单个文件）</label>
          <input
            placeholder="例如 D:\我的笔记 或 /home/cxy/Documents/Obsidian 或 F:\resume.pdf"
            value={vaultPath}
            onChange={(e) => setVaultPath(e.target.value)}
          />
          <button className="btn" onClick={submitPath} disabled={busy || !vaultPath.trim()}>
            {busy ? "读取并解析中…" : "让 Agent 读取并解析"}
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
              <VoiceField
                placeholder="你的回答（可详述项目经历、掌握程度、使用频率…）（回车提交，Shift+回车换行）"
                value={answer}
                onChange={setAnswer}
                minHeight={80}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    if (answer.trim()) submitAnswer();
                  }
                }}
              />
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
                onClick={openClarify}
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
