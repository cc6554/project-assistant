import VoiceField from "./VoiceField";

/** Standard text input adapter. Passwords and file controls keep native input. */
export default function VoiceInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  const { value, onChange, placeholder, disabled, onKeyDown, style } = props;
  if (props.type && props.type !== "text") return <input {...props} />;
  return <VoiceField multiline={false} value={String(value ?? "")} placeholder={placeholder}
    disabled={disabled} style={style} onKeyDown={onKeyDown as (event: React.KeyboardEvent) => void}
    onChange={text => onChange?.({ target: { value: text }, currentTarget: { value: text } } as React.ChangeEvent<HTMLInputElement>)} />;
}
