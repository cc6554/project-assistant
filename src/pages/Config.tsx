import { useEffect, useState } from "react";
import { api } from "../api";
import type { ConfigInfo } from "../types";

interface ProviderForm {
  name: string;
  api_mode: string;
  base_url: string;
  api_key_env: string;
  api_key: string;
  timeout: number;
}

const EMPTY: ProviderForm = {
  name: "",
  api_mode: "chat_completions",
  base_url: "",
  api_key_env: "DEEPSEEK_API_KEY",
  api_key: "",
  timeout: 180,
};

export default function ConfigPage() {
  const [cfg, setCfg] = useState<ConfigInfo | null>(null);
  const [providers, setProviders] = useState<ProviderForm[]>([]);
  const [tasks, setTasks] = useState<Record<string, { provider: string; model: string }[]>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ kind: "error" | "info"; text: string } | null>(null);
  const [started, setStarted] = useState(false);

  useEffect(() => {
    api
      .getConfig()
      .then((c) => {
        setCfg(c);
        setTasks(c.tasks);
        setProviders(
          Object.entries(c.providers).map(([name, p]) => ({
            name,
            api_mode: p.api_mode,
            base_url: p.base_url ?? "",
            api_key_env: p.api_key_env ?? "",
            api_key: "",
            timeout: p.timeout,
          })),
        );
        setStarted(true);
      })
      .catch((e) => setMsg({ kind: "error", text: String((e as Error)?.message ?? e) }))
      .finally(() => setLoading(false));
  }, []);

  const updateProvider = (i: number, patch: Partial<ProviderForm>) => {
    setProviders((ps) => ps.map((p, idx) => (idx === i ? { ...p, ...patch } : p)));
  };

  const addProvider = () => setProviders((ps) => [...ps, { ...EMPTY }]);
  const removeProvider = (i: number) => {
    setProviders((ps) => ps.filter((_, idx) => idx !== i));
    const name = providers[i]?.name;
    if (name) {
      const nt: Record<string, { provider: string; model: string }[]> = {};
      Object.entries(tasks).forEach(([k, chain]) => {
        nt[k] = chain.filter((t) => t.provider !== name);
      });
      setTasks(nt);
    }
  };

  const updateTask = (task: string, i: number, patch: Partial<{ provider: string; model: string }>) => {
    setTasks((ts) => ({
      ...ts,
      [task]: ts[task].map((t, idx) => (idx === i ? { ...t, ...patch } : t)),
    }));
  };

  const addTaskStep = (task: string) =>
    setTasks((ts) => ({
      ...ts,
      [task]: [...(ts[task] ?? []), { provider: providers[0]?.name ?? "", model: "" }],
    }));

  const removeTaskStep = (task: string, i: number) =>
    setTasks((ts) => ({ ...ts, [task]: ts[task].filter((_, idx) => idx !== i) }));

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      await api.putConfig({
        providers: providers.map((p) => ({
          name: p.name.trim(),
          api_mode: p.api_mode,
          base_url: p.base_url.trim(),
          api_key_env: p.api_key_env.trim() || null,
          api_key: p.api_key || null,
          timeout: Number(p.timeout) || 120,
        })),
        tasks,
      });
      setMsg({ kind: "info", text: "配置已保存并校验通过 ✅" });
      // 重新加载以刷新 key 状态
      const c = await api.getConfig();
      setCfg(c);
      setProviders((ps) =>
        ps.map((p, i) => ({ ...p, api_key: "", api_key_env: Object.keys(c.providers)[i] ?? p.api_key_env })),
      );
    } catch (e) {
      setMsg({ kind: "error", text: String((e as Error)?.message ?? e) });
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="page-sub">加载配置中…</div>;

  return (
    <div>
      <h2 className="page-title">模型配置</h2>
      <p className="page-sub">
        在这里添加任意 OpenAI 兼容 / Anthropic 兼容厂商，保存后立即可用；API Key 仅写入本地{" "}
        <code>.env</code>，不会上传。
      </p>

      {msg && <div className={`alert ${msg.kind}`}>{msg.text}</div>}
      {!started && <div className="alert error">配置加载失败，无法编辑</div>}

      <div className="card">
        <h3>Provider 列表</h3>
        {providers.map((p, i) => (
          <div key={i} style={{ borderBottom: "1px solid var(--border)", paddingBottom: 14, marginBottom: 14 }}>
            <div className="field-row">
              <div>
                <label>名称（唯一标识）</label>
                <input value={p.name} onChange={(e) => updateProvider(i, { name: e.target.value })} placeholder="deepseek" />
              </div>
              <div>
                <label>API 模式</label>
                <select value={p.api_mode} onChange={(e) => updateProvider(i, { api_mode: e.target.value })}>
                  <option value="chat_completions">OpenAI 兼容 (chat_completions)</option>
                  <option value="anthropic_messages">Anthropic (messages)</option>
                </select>
              </div>
            </div>
            <label>Base URL（以 /v1 结尾，如 https://api.deepseek.com/v1）</label>
            <input value={p.base_url} onChange={(e) => updateProvider(i, { base_url: e.target.value })} placeholder="https://api.deepseek.com/v1" />
            <div className="field-row">
              <div>
                <label>环境变量名（API Key 存哪里）</label>
                <input value={p.api_key_env} onChange={(e) => updateProvider(i, { api_key_env: e.target.value })} placeholder="DEEPSEEK_API_KEY" />
              </div>
              <div>
                <label>
                  API Key（留空表示保持现有）
                  {cfg && p.api_key_env && (cfg.providers[p.name]?.key_configured ? " · 已配置 ✅" : " · 未配置 ⚠️")}
                </label>
                <input type="password" value={p.api_key} onChange={(e) => updateProvider(i, { api_key: e.target.value })} placeholder="sk-..." />
              </div>
            </div>
            <div className="field-row" style={{ alignItems: "end" }}>
              <div>
                <label>超时（秒）</label>
                <input type="number" value={p.timeout} onChange={(e) => updateProvider(i, { timeout: Number(e.target.value) })} />
              </div>
              <div>
                <button className="btn danger" onClick={() => removeProvider(i)}>
                  删除此 Provider
                </button>
              </div>
            </div>
          </div>
        ))}
        <button className="btn secondary" onClick={addProvider}>
          + 添加 Provider
        </button>
      </div>

      <div className="card">
        <h3>任务路由（每个任务按顺序尝试，失败自动 fallback）</h3>
        <p className="page-sub" style={{ marginBottom: 10 }}>
          模型名需与厂商实际模型一致（如 deepseek-flash / deepseek-v4-pro / gpt-4o / claude-sonnet-4）。
        </p>
        {Object.entries(tasks).map(([task, chain]) => (
          <div key={task} style={{ marginBottom: 14 }}>
            <label style={{ fontWeight: 700 }}>任务：{task}</label>
            {chain.map((t, i) => (
              <div key={i} className="edit-line">
                <input
                  placeholder="provider"
                  value={t.provider}
                  onChange={(e) => updateTask(task, i, { provider: e.target.value })}
                  style={{ width: 180 }}
                />
                <input placeholder="model" value={t.model} onChange={(e) => updateTask(task, i, { model: e.target.value })} />
                <button className="btn danger" onClick={() => removeTaskStep(task, i)}>
                  ✕
                </button>
              </div>
            ))}
            <button className="btn secondary" onClick={() => addTaskStep(task)} style={{ marginTop: 6 }}>
              + 添加备选模型
            </button>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>使用中的模型</h3>
        {(cfg?.models_in_use ?? []).length === 0 ? (
          <p className="page-sub">尚未配置任何模型</p>
        ) : (
          (cfg?.models_in_use ?? []).map((m) => (
            <span className="chip" key={m}>
              {m}
            </span>
          ))
        )}
      </div>

      <button className="btn" onClick={save} disabled={saving || !started}>
        {saving ? "保存中…" : "保存配置"}
      </button>
    </div>
  );
}
