// 연결 코드 입력: 홈 화면 앱처럼 접속 토큰이 없을 때, PC 화면의 6자리 코드로 토큰을 받아 저장
import { useState } from "react";
import { api, saveToken } from "../lib";

export function PairBox() {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const go = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await api<{ token: string }>("/api/pair", "POST", { code });
      saveToken(r.token);
    } catch (e) { setErr(String((e as Error).message || e)); }
    setBusy(false);
  };
  return (
    <span className="pair">
      <input inputMode="numeric" autoComplete="one-time-code" maxLength={7} placeholder="000000" value={code}
        onChange={(e) => setCode(e.target.value.replace(/[^\d]/g, "").slice(0, 6))} onKeyDown={(e) => e.key === "Enter" && code.length === 6 && go()} />
      <button disabled={busy || code.length !== 6} onClick={go}>{busy ? "연결 중…" : "연결"}</button>
      {err && <em>{err}</em>}
    </span>
  );
}
