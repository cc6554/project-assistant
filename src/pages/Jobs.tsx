import { useRef, useState } from "react";
import { api } from "../api";
import VoiceField from "../components/VoiceField";
import type { JDCard, SessionState } from "../types";

const EMPTY_JD: JDCard = {
  hard_requirements: [],
  skill_tags: [],
  responsibilities: [],
  nice_to_haves: [],
  raw_summary: "",
  uncertain_fields: [],
};

interface Props {
  sessionId: string;
  state: SessionState | null;
  onState: (s: SessionState) => void;
}

export default function JobsPage({ sessionId, state, onState }: Props) {
  const [mode, setMode] = useState<"search" | "upload">("search");
  // 方式 A：说岗位名，联网搜索
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  // 方式 B：上传 / 粘贴截图
  const [files, setFiles] = useState<File[]>([]);
  const [parsing, setParsing] = useState(false);
  const [msg, setMsg] = useState<{ kind: "error" | "info"; text: string } | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<JDCard>(EMPTY_JD);
  const inputRef = useRef<HTMLInputElement>(null);

  const jd = state?.jd ?? null;

  const applyJd = (nextJd: JDCard, infoText: string) => {
    const next: SessionState = {
      ...(state ?? { session_id: sessionId, repos: [], interview_history: [], coach_history: [] }),
      jd: nextJd,
    };
    onState(next);
    setDraft(nextJd);
    setEditing(true);
    setMsg({ kind: "info", text: infoText });
  };

  const startSearch = async () => {
    if (!query.trim()) {
      setMsg({ kind: "error", text: "先告诉我你想找什么岗位" });
      return;
    }
    setSearching(true);
    setMsg(null);
    try {
      const res = await api.searchJd(sessionId, query);
      applyJd(res.jd, `搜索并识别完成（基于 ${res.sources} 条结果），请核对下面的字段，有错的直接改`);
    } catch (e) {
      setMsg({ kind: "error", text: String((e as Error)?.message ?? e) });
    } finally {
      setSearching(false);
    }
  };

  const startParse = async () => {
    if (!files.length) {
      setMsg({ kind: "error", text: "请先选择截图" });
      return;
    }
    setParsing(true);
    setMsg(null);
    try {
      const res = await api.parseJd(sessionId, files);
      applyJd(res.jd, "识别完成，请核对下面的字段，有错的直接改");
    } catch (e) {
      setMsg({ kind: "error", text: String((e as Error)?.message ?? e) });
    } finally {
      setParsing(false);
    }
  };

  const addPastedImages = (filesLike: FileList | null) => {
    if (!filesLike) return;
    const imgs = Array.from(filesLike).filter((f) => f.type.startsWith("image/"));
    if (imgs.length) {
      setFiles((prev) => [...prev, ...imgs]);
      setMsg({ kind: "info", text: `已从剪贴板加入 ${imgs.length} 张图片` });
    }
  };

  const startEdit = () => {
    setDraft(jd ?? EMPTY_JD);
    setEditing(true);
  };

  const saveEdit = async () => {
    try {
      await api.updateJd(sessionId, draft);
      const next = { ...(state ?? { session_id: sessionId, repos: [], interview_history: [], coach_history: [] }), jd: draft };
      onState(next);
      setEditing(false);
      setMsg({ kind: "info", text: "岗位卡片已保存 ✅" });
    } catch (e) {
      setMsg({ kind: "error", text: String((e as Error)?.message ?? e) });
    }
  };

  const listEdit = (
    label: string,
    values: string[],
    set: (v: string[]) => void,
  ) => (
    <div>
      <label>{label}</label>
      {(values.length === 0 ? [""] : values).map((v, i) => (
        <div key={i} className="edit-line">
          <input
            value={v}
            onChange={(e) => {
              const next = [...values];
              next[i] = e.target.value;
              set(next);
            }}
          />
          <button
            className="btn danger"
            onClick={() => set(values.filter((_, idx) => idx !== i))}
          >
            ✕
          </button>
        </div>
      ))}
      <button className="btn secondary" onClick={() => set([...values, ""])}>
        + 添加
      </button>
    </div>
  );

  const setJd = (patch: Partial<JDCard>) => setDraft((d) => ({ ...d, ...patch }));

  return (
    <div onPaste={(e) => addPastedImages(e.clipboardData?.files ?? null)}>
      <h2 className="page-title">岗位 JD</h2>
      <p className="page-sub">两种方式告诉 AI 你要投的岗位：说岗位名让 AI 去搜，或者直接给岗位详情截图（可粘贴）。</p>

      {msg && <div className={`alert ${msg.kind}`}>{msg.text}</div>}

      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <button
          className={`nav-item ${mode === "search" ? "active" : ""}`}
          style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "7px 16px", cursor: "pointer" }}
          onClick={() => setMode("search")}
        >
          说岗位名，AI 去搜
        </button>
        <button
          className={`nav-item ${mode === "upload" ? "active" : ""}`}
          style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "7px 16px", cursor: "pointer" }}
          onClick={() => setMode("upload")}
        >
          上传 / 粘贴岗位截图
        </button>
      </div>

      {mode === "search" && (
        <div className="card">
          <label>岗位名称或方向</label>
          <VoiceField
            placeholder="例如：灵巧手抓取算法工程师 / 具身智能操作方向 / 大模型推理优化后端…可以加城市、关键词让搜索更准"
            value={query}
            onChange={setQuery}
            minHeight={72}
          />
          <button className="btn" onClick={startSearch} disabled={searching || !query.trim()}>
            {searching ? (
              <>
                <span className="spinner" />
                搜索并识别中（30–60 秒）…
              </>
            ) : (
              "搜索并识别"
            )}
          </button>
          <p className="page-sub" style={{ marginTop: 8 }}>
            联网搜索招聘 JD 文本后用 AI 结构化；结果可能不如截图精确，识别后务必核对。
          </p>
        </div>
      )}

      {mode === "upload" && (
        <div className="card">
          <h3>上传岗位截图</h3>
          <div
            className="upload-zone"
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              setFiles(Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith("image/")));
            }}
          >
            {files.length ? `已选 ${files.length} 张：${files.map((f) => f.name).join("、")}` : "点击、拖拽截图到这里，或直接 Ctrl+V 粘贴"}
          </div>
          <input
            ref={inputRef}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
          <button className="btn" onClick={startParse} disabled={parsing || !files.length}>
            {parsing ? (
              <>
                <span className="spinner" />
                识别中（可能需要 30–60 秒）…
              </>
            ) : (
              "开始识别"
            )}
          </button>
        </div>
      )}

      {(editing || jd) && (
        <div className="card">
          <h3>岗位卡片（可编辑）</h3>
          {!editing && (
            <button className="btn secondary" onClick={startEdit} style={{ marginBottom: 12 }}>
              编辑
            </button>
          )}
          {editing ? (
            <>
              <div className="field-row">
                <div>
                  <label>岗位名称</label>
                  <input value={draft.job_title ?? ""} onChange={(e) => setJd({ job_title: e.target.value })} />
                </div>
                <div>
                  <label>公司</label>
                  <input value={draft.company ?? ""} onChange={(e) => setJd({ company: e.target.value })} />
                </div>
              </div>
              <div className="field-row">
                <div>
                  <label>薪资（原文）</label>
                  <input value={draft.salary ?? ""} onChange={(e) => setJd({ salary: e.target.value })} />
                </div>
                <div>
                  <label>地点</label>
                  <input value={draft.location ?? ""} onChange={(e) => setJd({ location: e.target.value })} />
                </div>
              </div>
              <div>
                <label>团队 / 部门</label>
                <input value={draft.team ?? ""} onChange={(e) => setJd({ team: e.target.value })} />
              </div>
              {listEdit("硬性要求", draft.hard_requirements, (v) => setJd({ hard_requirements: v }))}
              {listEdit("技能标签", draft.skill_tags, (v) => setJd({ skill_tags: v }))}
              {listEdit("岗位职责", draft.responsibilities, (v) => setJd({ responsibilities: v }))}
              {listEdit("加分项", draft.nice_to_haves, (v) => setJd({ nice_to_haves: v }))}
              <div>
                <label>原始摘要</label>
                <VoiceField value={draft.raw_summary ?? ""} onChange={(v) => setJd({ raw_summary: v })} minHeight={80} />
              </div>
              <div style={{ marginTop: 14, display: "flex", gap: 10 }}>
                <button className="btn" onClick={saveEdit}>
                  保存
                </button>
                <button className="btn secondary" onClick={() => setEditing(false)}>
                  取消
                </button>
              </div>
            </>
          ) : (
            jd && (
              <>
                <p style={{ fontSize: 15, margin: "0 0 6px", fontWeight: 700 }}>
                  {jd.job_title || "（未识别岗位名）"}
                  {jd.company && <span style={{ color: "var(--muted)", fontWeight: 400 }}> · {jd.company}</span>}
                </p>
                <p className="page-sub" style={{ margin: "0 0 10px" }}>
                  {[jd.salary, jd.location, jd.team].filter(Boolean).join(" · ") || "薪资/地点/团队未识别"}
                </p>
                <p>
                  <strong>硬性要求：</strong>
                  {jd.hard_requirements.map((r, i) => (
                    <div key={i} style={{ margin: "3px 0" }}>
                      · {r}
                    </div>
                  ))}
                </p>
                <p>
                  <strong>技能标签：</strong>
                  {jd.skill_tags.map((t) => (
                    <span className="chip" key={t}>
                      {t}
                    </span>
                  ))}
                </p>
                <p>
                  <strong>加分项：</strong>
                  {jd.nice_to_haves.length
                    ? jd.nice_to_haves.map((n, i) => (
                        <div key={i} style={{ margin: "3px 0" }}>
                          · {n}
                        </div>
                      ))
                    : "无"}
                </p>
                {jd.uncertain_fields?.length ? (
                  <div className="alert info">⚠️ 以下字段识别不确定，建议核对：{jd.uncertain_fields.join("、")}</div>
                ) : null}
              </>
            )
          )}
        </div>
      )}
    </div>
  );
}
