import { useEffect, useRef, useState } from "react";

interface Props {
  value: string; onChange: (value: string) => void; placeholder?: string;
  multiline?: boolean; minHeight?: number; disabled?: boolean;
  onKeyDown?: (event: React.KeyboardEvent) => void; style?: React.CSSProperties;
}

type Meter = { stream: MediaStream; context: AudioContext; frame: number };

export default function VoiceField({ value, onChange, placeholder, multiline = true, minHeight, disabled, onKeyDown, style }: Props) {
  const [listening, setListening] = useState(false);
  const [volume, setVolume] = useState(0);
  const latest = useRef(value);
  const changed = useRef(onChange);
  latest.current = value;
  changed.current = onChange;
  const generation = useRef(0);
  const active = useRef(false);
  const recognition = useRef<any>(null);
  const meter = useRef<Meter | null>(null);

  const release = () => {
    generation.current++;
    active.current = false;
    const rec = recognition.current;
    recognition.current = null;
    if (rec) {
      rec.onresult = rec.onend = rec.onerror = null;
      try { rec.abort ? rec.abort() : rec.stop(); } catch { /* already stopped */ }
    }
    const current = meter.current;
    meter.current = null;
    if (current) {
      cancelAnimationFrame(current.frame);
      current.stream.getTracks().forEach(track => track.stop());
      void current.context.close().catch(() => {});
    }
  };
  const stop = () => { release(); setListening(false); setVolume(0); };
  useEffect(() => () => release(), []);
  useEffect(() => { if (disabled) stop(); }, [disabled]);

  const update = (text: string) => { latest.current = text; changed.current(text); };
  const toggle = () => {
    if (active.current) { stop(); return; }
    const BrowserRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!BrowserRecognition) { alert("当前环境不支持语音识别，请使用文字输入。"); return; }
    const ticket = ++generation.current;
    active.current = true;
    try {
      const rec = new BrowserRecognition();
      recognition.current = rec;
      rec.lang = "zh-CN"; rec.interimResults = false; rec.continuous = true;
      rec.onresult = (event: any) => {
        if (!active.current || generation.current !== ticket) return;
        let text = "";
        for (let i = event.resultIndex; i < event.results.length; i++) {
          if (event.results[i].isFinal !== false) text += event.results[i][0].transcript;
        }
        if (text) update(latest.current + text);
      };
      rec.onend = () => { if (generation.current === ticket) stop(); };
      rec.onerror = (event: any) => {
        if (generation.current !== ticket) return;
        stop();
        alert("语音识别失败：" + (event.error || "请检查语音服务和麦克风权限"));
      };
      rec.start(); setListening(true);
      if (!navigator.mediaDevices?.getUserMedia) { alert("此环境无法显示麦克风音量。"); return; }
      void navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
        if (!active.current || generation.current !== ticket) { stream.getTracks().forEach(track => track.stop()); return; }
        let context: AudioContext | null = null;
        try {
          const Audio = window.AudioContext || (window as any).webkitAudioContext;
          context = new Audio();
          const source = context!.createMediaStreamSource(stream);
          const analyser = context!.createAnalyser();
          analyser.fftSize = 512; analyser.smoothingTimeConstant = 0.55;
          source.connect(analyser);
          const data = new Uint8Array(analyser.frequencyBinCount);
          meter.current = { stream, context: context!, frame: 0 };
          const tick = () => {
            if (!active.current || generation.current !== ticket || !meter.current) return;
            analyser.getByteFrequencyData(data);
            const mean = data.reduce((sum, v) => sum + v, 0) / data.length;
            setVolume(Math.round(Math.min(100, Math.max(0, (mean - 4) * 2.4))));
            meter.current.frame = requestAnimationFrame(tick);
          };
          tick();
        } catch {
          stream.getTracks().forEach(track => track.stop());
          if (context) void context.close().catch(() => {});
          alert("麦克风音量检测初始化失败。");
        }
      }).catch(() => {
        if (generation.current === ticket) { stop(); alert("无法获取麦克风权限，请在系统设置中允许后重试。"); }
      });
    } catch (error) { stop(); alert("启动语音识别失败：" + String(error)); }
  };

  return <div style={{ display: "flex", gap: 8, alignItems: multiline ? "flex-start" : "center", flex: 1, ...style }}>
    {multiline ? <textarea value={value} onChange={event => update(event.target.value)} placeholder={placeholder} disabled={disabled} onKeyDown={onKeyDown} style={{ flex: 1, minHeight: minHeight ?? 90 }} />
      : <input value={value} onChange={event => update(event.target.value)} placeholder={placeholder} disabled={disabled} onKeyDown={onKeyDown} style={{ flex: 1 }} />}
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <button type="button" className={`btn ${listening ? "danger" : "secondary"}`} onClick={toggle} disabled={disabled} title={listening ? "停止录音" : "语音输入"}>{listening ? "■ 停止" : "🎤"}</button>
      {listening && <div className="voice-level" title="实时音量">{Array.from({ length: 10 }).map((_, i) => <span key={i} className={volume >= (i + 1) * 10 ? "on" : ""} style={{ height: 5 + i * 1.7 }} />)}</div>}
    </div>
  </div>;
}
