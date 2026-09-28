import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import VoiceField from "../components/VoiceField";
import type { AgentLogEntry, SessionState } from "../types";

interface Props {
  sessionId: string;
  state: SessionState | null;
  onState: (s: SessionState) => void;
  prefill?: string;
  onPrefillUsed?: () => void;
}

interface PendingInfo {
  action_id: string;
  kind: string;
  summary: string;
  tool_call_id: string;
  payload: Record<string, unknown>;
}

interface TimelineItem {
  id: number;
  type: "user" | "assistant" | "system" | "tool" | "pending" | "thinking";
  content?: string;
  kind?: string;
  summary?: string;
  output?: string;
  running?: boolean;
  pending?: PendingInfo;
  ts: string;
}

let idSeq = 0;
const nid = () => ++idSeq;

/** 把后端持久化的 agent_logs 还原成时间线条目（历史记录，刷新不丢）。 */
function logsToItems(logs?: AgentLogEntry[] | null): TimelineItem[] {
  if (!logs || !logs.length) return [];
  return logs.map((l) => {
    const base = { id: nid(), ts: l.ts };
    if (l.type === "message") {
      return { ...base, type: (l.role ?? "assistant") as TimelineItem["type"], content: l.content ?? "" };
    }
    if (l.type === "pending") {
      return {
        ...base,
        type: "tool",
        kind: l.kind ?? "confirm",
        summary: `${l.summary ?? "等待确认的操作"}`,
        output: "",
      };
    }
    return {
      ...base,
      type: "tool",
      kind: l.kind,
      summary: l.summary,
      output: l.output ?? "",
    };
  });
}

/** 解析 SSE 流，逐步回调事件。 */
async function consumeSSE(
  res: Response,
  onEvent: (event: string, data: Record<string, unknown>) => void,
): Promise<void> {
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
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message";
      let dataStr = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataStr += line.slice(5).trim();
      }
      if (dataStr) {
        try {
          onEvent(event, JSON.parse(dataStr) as Record<string, unknown>);
        } catch {
          /* 忽略坏帧 */
        }
      }
    }
  }
}

export default function AgentPage({ sessionId, state, prefill, onPrefillUsed }: Props) {
  const [items, setItems] = useState<TimelineItem[]>([]);
  const [pending, setPending] = useState<PendingInfo | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const busyRef = useRef(false);
  busyRef.current = busy;

  useEffect(() => {
    if (prefill && !busyRef.current) {
      setInput(prefill);
      onPrefillUsed?.();
    }
  }, [prefill, onPrefillUsed]);

  const repos = state?.repos ?? [];
  const hasPlan = !!state?.plan;

  // 会话切换/刷新时：从持久化日志恢复过往记录
  useEffect(() => {
    idSeq = 0;
    setItems(logsToItems(state?.agent_logs));
    setPending(null);
    setErr(null);
  }, [sessionId, state?.agent_logs]);

  // 自动滚到底部
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [items, thinking, pending]);

  const append = (item: Omit<TimelineItem, "id">) =>
    setItems((prev) => [...prev, { ...item, id: nid() }]);

  const handleEvent = (event: string, data: Record<string, unknown>) => {
    switch (event) {
      case "thinking":
        setThinking(true);
        break;
      case "tool_start": {
        setThinking(false);
        append({
          type: "tool",
          kind: String(data.kind ?? "tool"),
          summary: String(data.summary ?? ""),
          output: "",
          running: true,
          ts: new Date().toISOString(),
        });
        break;
      }
      case "tool": {
        setThinking(false);
        setItems((prev) => {
          const copy = [...prev];
          for (let i = copy.length - 1; i >= 0; i--) {
            if (copy[i].type === "tool" && copy[i].running) {
              copy[i] = {
                ...copy[i],
                running: false,
                summary: String(data.summary ?? copy[i].summary ?? ""),
                output: String(data.output ?? ""),
              };
              break;
            }
          }
          return copy;
        });
        break;
      }
      case "message": {
        setThinking(false);
        const role = (data.role as string) ?? "assistant";
        append({
          type: role === "user" ? "user" : role === "system" ? "system" : "assistant",
          content: String(data.content ?? ""),
          ts: new Date().toISOString(),
        });
        break;
      }
      case "pending": {
        setThinking(false);
        setPending(data.pending as PendingInfo);
        break;
      }
      case "error": {
        setThinking(false);
        setErr(String(data.detail ?? "未知错误"));
        break;
      }
      default:
        break;
    }
  };

  const send = async (text?: string) => {
    const message = (text ?? input).trim();
    if (!message || busyRef.current) return;
    setBusy(true);
    setErr(null);
    setInput("");
    append({ type: "user", content: message, ts: new Date().toISOString() });
    try {
      const res = await api.agentTurnStream(sessionId, message);
      await consumeSSE(res, handleEvent);
    } catch (e) {
      setErr(String((e as Error)?.message ?? e));
    } finally {
      setThinking(false);
      setBusy(false);
    }
  };

  const confirm = async (approve: boolean) => {
    if (!pending || busyRef.current) return;
    setBusy(true);
    setErr(null);
    setPending(null);
    try {
      const res = await api.agentConfirmStream(sessionId, pending.action_id, approve);
      await consumeSSE(res, handleEvent);
    } catch (e) {
      setErr(String((e as Error)?.message ?? e));
    } finally {
      setThinking(false);
      setBusy(false);
    }
  };

  const reset = async () => {
    setBusy(true);
    try {
      await api.agentReset(sessionId);
      idSeq = 0;
      setItems([]);
      setPending(null);
      setErr(null);
    } catch (e) {
      setErr(String((e as Error)?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <h2 className="page-title">④ Agent 实操（能操作这台电脑）</h2>
      <p className="page-sub">
        像 Codex 一样：Agent 的每一步操作都实时显示在这里，过往记录会保存，刷新不丢。只读操作自动执行；
        写文件 / 跑命令 / git clone 会停在确认卡等你批准。工作区：<b>应用目录 /workspace</b>。
      </p>

      {!hasPlan && (
        <div className="alert error">
          还差一步：先在「③ 计划 &amp; 项目」页生成学习计划，Agent 才能基于你的岗位差距带你做项目。
        </div>
      )}

      {repos.length > 0 && (
        <div className="card">
          <h3>当前可复现项目（点一下让 Agent 去上手）</h3>
          <div className="chip-row">
            {repos.map((r) => (
              <button
                key={r.full_name}
                className="chip"
                style={{ cursor: "pointer", background: "var(--panel-2)" }}
                onClick={() => setInput(`帮我 clone 并上手 ${r.full_name} 这个项目，先摸清结构再给我行动计划`)}
                disabled={busy}
              >
                {r.full_name} ⭐{r.stargazers_count}
              </button>
            ))}
          </div>
        </div>
      )}

      {err && <div className="alert error">{err}</div>}

      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <div
          ref={listRef}
          style={{ maxHeight: "54vh", overflowY: "auto", padding: 14, fontFamily: "monospace" }}
        >
          {items.length === 0 && !thinking && !pending && (
            <p className="page-sub" style={{ margin: 8, color: "var(--muted)", fontFamily: "inherit" }}>
              {hasPlan
                ? "直接说目标，比如：帮我 clone 灵巧手操作算法项目并跑起来，或：我卡在环境配置，帮我看。"
                : "先生成计划。"}
            </p>
          )}

          {items.map((it) => (
            <div key={it.id} style={{ margin: "10px 0" }}>
              {it.type === "tool" ? (
                <div
                  style={{
                    background: "#0d1117",
                    border: "1px solid var(--border)",
                    borderRadius: 8,
                    padding: 10,
                  }}
                >
                  <p style={{ margin: 0, fontSize: 12.5, color: "var(--accent)" }}>
                    {it.running ? "⏳ " : "✓ "}
                    {it.summary}
                  </p>
                  {it.output && (
                    <pre
                      style={{
                        margin: "6px 0 0",
                        padding: 8,
                        background: "rgba(0,0,0,0.35)",
                        color: "#c9d1d9",
                        borderRadius: 6,
                        fontSize: 12,
                        overflowX: "auto",
                        whiteSpace: "pre-wrap",
                        maxHeight: 240,
                        overflowY: "auto",
                      }}
                    >
                      {it.output}
                    </pre>
                  )}
                </div>
              ) : (
                <div
                  style={{
                    background:
                      it.type === "user"
                        ? "rgba(0,210,190,0.10)"
                        : it.type === "system"
                          ? "rgba(147,160,189,0.15)"
                          : "var(--panel-2)",
                    padding: "10px 12px",
                    borderRadius: 10,
                    whiteSpace: "pre-wrap",
                    fontSize: 13.5,
                    lineHeight: 1.6,
                    fontFamily: "inherit",
                  }}
                >
                  {it.type === "system" && <span style={{ color: "var(--muted)", fontSize: 11 }}>[拒绝] </span>}
                  {it.content}
                </div>
              )}
            </div>
          ))}

          {thinking && (
            <p className="page-sub" style={{ margin: 10 }}>
              <span className="spinner" /> Agent 思考中…
            </p>
          )}

          {pending && (
            <div
              style={{
                border: "1px solid #e6a700",
                background: "rgba(230,167,0,0.12)",
                borderRadius: 10,
                padding: 12,
                margin: "10px 0",
              }}
            >
              <p style={{ margin: 0, fontWeight: 700, fontSize: 13, fontFamily: "inherit" }}>
                ⚠️ Agent 请求执行操作{`（${pending.kind}）`}
              </p>
              <pre
                style={{
                  margin: "8px 0",
                  whiteSpace: "pre-wrap",
                  fontSize: 12.5,
                  background: "rgba(0,0,0,0.25)",
                  padding: 8,
                  borderRadius: 6,
                }}
              >
                {pending.summary}
              </pre>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  className="btn"
                  style={{ background: "var(--ok)", color: "#04150a" }}
                  onClick={() => confirm(true)}
                  disabled={busy}
                >
                  {busy ? "执行中…" : "✓ 同意执行"}
                </button>
                <button className="btn danger" onClick={() => confirm(false)} disabled={busy}>
                  ✕ 拒绝
                </button>
              </div>
            </div>
          )}
        </div>

        <div
          style={{
            borderTop: "1px solid var(--border)",
            padding: 12,
            display: "flex",
            gap: 8,
          }}
        >
          <VoiceField
            multiline={false}
            value={input}
            onChange={setInput}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder={pending ? "先处理上面的确认…" : "告诉 Agent 你想做什么"}
            disabled={busy || !!pending || !hasPlan}
          />
          <button
            className="btn"
            onClick={() => send()}
            disabled={busy || !input.trim() || !!pending || !hasPlan}
          >
            发送
          </button>
          <button className="btn" onClick={reset} disabled={busy} title="清空对话与待确认（不影响工作区文件）">
            重置
          </button>
        </div>
      </div>

      <p className="page-sub" style={{ marginTop: 10 }}>
        说明：Agent 的命令在你的电脑上真实运行，仅限应用目录 /workspace；任何写文件、命令、克隆都需你确认。遇到危险命令直接点「拒绝」。
      </p>
    </div>
  );
}
