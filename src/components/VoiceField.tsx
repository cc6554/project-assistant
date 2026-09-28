import { useRef, useState } from "react";

/**
 * 带语音输入的文本组件：textarea（多行）或 input（单行）+ 麦克风按钮。
 * 使用 Web Speech API（WebView2 / Chromium），无需后端，中文识别。
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
  const recRef = useRef<SR | null>(null);

  const toggleMic = () => {
    const SR = getSR();
    if (!SR) {
      alert("当前环境不支持语音输入（需要 WebView2/Chromium 的语音识别服务）。请检查系统是否开启「在线语音识别」。");
      return;
    }
    if (listening) {
      recRef.current?.stop();
      setListening(false);
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
      rec.onend = () => setListening(false);
      rec.onerror = (e: any) => {
        setListening(false);
        if (e?.error === "not-allowed" || e?.error === "service-not-allowed") {
          alert("语音识别不可用：请检查麦克风权限或系统语音识别服务是否开启。");
        }
      };
      recRef.current = rec;
      rec.start();
      setListening(true);
    } catch (err) {
      setListening(false);
      alert("启动语音识别失败：" + String(err));
    }
  };

  const micBtn = (
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
        {micBtn}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "flex-start", ...style }}>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        style={{ flex: 1, minHeight: minHeight ?? 90 }}
      />
      {micBtn}
    </div>
  );
}
