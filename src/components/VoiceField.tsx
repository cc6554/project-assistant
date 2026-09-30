import { useRef, useState } from "react";

/**
 * 带语音输入的文本组件：textarea（多行）或 input（单行）+ 麦克风按钮。
 * 使用 Web Speech API（WebView2 / Chromium），无需后端，中文识别。
 * 录音期间通过 getUserMedia + AnalyserNode 实时读取麦克风音量，
 * 在按钮旁显示音量条（说话时跳动），让用户知道语音检测已开始。
 * 环境不支持或系统未开启语音服务时给出提示。
 */

interface Props {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  multiline?: boolean;
  minHeight?: number;
  disabled?: boolean;
  onKeyDown?: (e: React.KeyboardEvent) => void;
  style?: React.CSSProperties;
}

type SR = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((e: any) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: any) => void) | null;
  start: () => void;
  stop: () => void;
};

function getSR(): (new () => SR) | null {
  const w = window as any;
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export default function VoiceField({
  value,
  onChange,
  placeholder,
  multiline = true,
  minHeight,
  disabled,
  onKeyDown,
  style,
}: Props) {
  const [listening, setListening] = useState(false);
  const [vol, setVol] = useState(0); // 0~100 实时音量
  const recRef = useRef<SR | null>(null);
  const meterRef = useRef<{ ctx: AudioContext; stream: MediaStream; raf: number } | null>(null);

  /** 停止音量检测并释放麦克风流。 */
  const stopMeter = () => {
    const m = meterRef.current;
    if (m) {
      cancelAnimationFrame(m.raf);
      try {
        m.ctx.close();
      } catch {
        /* noop */
      }
      m.stream.getTracks().forEach((t) => t.stop());
      meterRef.current = null;
    }
    setVol(0);
  };

  /** 启动音量检测：AnalyserNode 实时取频域均值，映射为 0~100。 */
  const startMeter = () => {
    try {
      if (!navigator.mediaDevices?.getUserMedia) return;
      navigator.mediaDevices
        .getUserMedia({ audio: true })
        .then((stream) => {
          const w = window as any;
          const Ctx = w.AudioContext || w.webkitAudioContext;
          if (!Ctx) {
            stream.getTracks().forEach((t) => t.stop());
            return;
          }
          const ctx = new Ctx();
          const src = ctx.createMediaStreamSource(stream);
          const an = ctx.createAnalyser();
          an.fftSize = 512;
          an.smoothingTimeConstant = 0.55;
          src.connect(an);
          const data = new Uint8Array(an.frequencyBinCount);
          const tick = () => {
            an.getByteFrequencyData(data);
            let sum = 0;
            for (let i = 0; i < data.length; i++) sum += data[i];
            const avg = sum / data.length; // 0~255：安静 <8，说话 20+
            const v = Math.round(Math.min(100, Math.max(0, (avg - 4) * 2.4)));
            setVol(v);
            if (meterRef.current) meterRef.current.raf = requestAnimationFrame(tick);
          };
          meterRef.current = { ctx, stream, raf: 0 };
          tick();
        })
        .catch(() => {
          /* 拿不到麦克风流则静默：文字识别仍走 Web Speech API，只是不显示音量条 */
        });
    } catch {
      /* noop */
    }
  };

  const toggleMic = () => {
    const SR = getSR();
    if (!SR) {
      alert("当前环境不支持语音输入（需要 WebView2/Chromium 的语音识别服务）。请检查系统是否开启「在线语音识别」。");
      return;
    }
    if (listening) {
      recRef.current?.stop();
      setListening(false);
      stopMeter();
      return;
    }
    try {
      const rec = new SR();
      rec.lang = "zh-CN";
      rec.interimResults = false;
      rec.continuous = true;
      rec.onresult = (e: any) => {
        let t = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          t += e.results[i][0].transcript;
        }
        if (t) onChange(value + t);
      };
      rec.onend = () => {
        setListening(false);
        stopMeter();
      };
      rec.onerror = (e: any) => {
        setListening(false);
        stopMeter();
        if (e?.error === "not-allowed" || e?.error === "service-not-allowed") {
          alert("语音识别不可用：请检查麦克风权限或系统语音识别服务是否开启。");
        }
      };
      recRef.current = rec;
      rec.start();
      setListening(true);
      startMeter();
    } catch (err) {
      setListening(false);
      alert("启动语音识别失败：" + String(err));
    }
  };

  const micArea = (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <button
        type="button"
        className={`btn ${listening ? "danger" : "secondary"}`}
        onClick={toggleMic}
        disabled={disabled}
        title={listening ? "停止录音" : "语音输入"}
        style={{ minWidth: 44 }}
      >
        {listening ? "■ 停止" : "🎤"}
      </button>
      {listening && (
        <div className="voice-level" title="实时音量（说话时跳动）">
          {Array.from({ length: 10 }).map((_, i) => (
            <span
              key={i}
              className={vol >= (i + 1) * 10 ? "on" : ""}
              style={{ height: 5 + i * 1.7 }}
            />
          ))}
        </div>
      )}
    </div>
  );

  if (!multiline) {
    return (
      <div style={{ display: "flex", gap: 8, alignItems: "center", flex: 1, ...style }}>
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          style={{ flex: 1 }}
        />
        {micArea}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "flex-start", ...style }}>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        style={{ flex: 1, minHeight: minHeight ?? 90 }}
      />
      {micArea}
    </div>
  );
}
