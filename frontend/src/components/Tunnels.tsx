// 원격 PC의 포트를 이 PC의 localhost 로 여는 SSH 터널 목록 (켜기·끄기·열기·추가·삭제). 서버는 tunnels.py
import { useCallback, useEffect, useState } from "react";
import { C, api } from "../lib";
import { Muted } from "./ui";

interface Tunnel {
  id: string; machine: string; name: string; local: number; remote: number; host: string; scheme: string;
  state: "off" | "connecting" | "on" | "retry"; error?: string | null; target?: string | null; retry_in?: number;
}

const STATE: Record<Tunnel["state"], [string, string]> = {
  off: ["꺼짐", "var(--dim)"], connecting: ["연결 중…", C.amber], on: ["켜짐", C.green], retry: ["다시 연결 대기", C.red],
};
// 터널은 이 PC의 localhost 에 열리므로 다른 기기(휴대폰 등)에서 '열기'는 의미 없음
const HERE = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);

export function TunnelPanel({ id }: { id: string }) {
  const [list, setList] = useState<Tunnel[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [f, setF] = useState({ name: "", local: "", remote: "", https: false });

  const load = useCallback(() => api<{ tunnels: Tunnel[] }>(`/api/tunnels?machine=${encodeURIComponent(id)}`)
    .then((d) => setList(d.tunnels)).catch((e) => setErr(String(e.message || e))), [id]);
  useEffect(() => { load(); const t = setInterval(load, 2000); return () => clearInterval(t); }, [load]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); } catch (e) { setErr(String((e as Error).message || e)); }
    setBusy(false); load();
  };
  const add = () => run(async () => {
    await api("/api/tunnels", "POST", { machine: id, name: f.name, local: f.local || f.remote, remote: f.remote || f.local,
      scheme: f.https ? "https" : "http", start: true });
    setF({ name: "", local: "", remote: "", https: false });
  });
  const url = (t: Tunnel) => `${t.scheme}://localhost:${t.local}`;

  if (!list) return err ? <div className="err">{err}</div> : <Muted>불러오는 중…</Muted>;
  return (
    <div className="tunnels">
      {list.length === 0 && <Muted>아직 터널이 없어요. 아래에서 원격 PC의 포트를 추가하면 이 PC의 localhost 로 열 수 있어요.</Muted>}
      {list.map((t) => {
        const [label, color] = STATE[t.state];
        const on = t.state !== "off";
        return (
          <div key={t.id} className={`ctr ${t.state === "retry" ? "down" : ""}`}>
            <span className="dot" style={{ background: color }} />
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="n">{t.name}</div>
              <div className="s num" title={t.error || t.target || ""}>
                localhost:{t.local} → {t.host === "localhost" ? "" : t.host}:{t.remote}
                {" · "}<span style={{ color }}>{label}{t.state === "retry" && t.retry_in ? ` ${t.retry_in}초` : ""}</span>
                {t.state === "retry" && t.error ? ` · ${t.error}` : t.target && on ? ` · ${t.target}` : ""}
              </div>
            </div>
            <div className="tun-act">
              {t.state === "on" && HERE && <a className="g-btn" href={url(t)} target="_blank" rel="noreferrer">열기 ↗</a>}
              <button className={`g-btn ${on ? "" : "primary"}`} disabled={busy}
                onClick={() => run(() => api(`/api/tunnels/${t.id}/${on ? "stop" : "start"}`, "POST"))}>{on ? "끄기" : "켜기"}</button>
              <button className="g-btn" disabled={busy} title="목록에서 삭제"
                onClick={() => { if (confirm(`'${t.name}' 터널을 지울까요?`)) run(() => api(`/api/tunnels/${t.id}`, "DELETE")); }}>✕</button>
            </div>
          </div>
        );
      })}
      <div className="tun-add">
        <input className="g-in" placeholder="이름 (예: Portainer)" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        <input className="g-in num" placeholder="원격 포트" inputMode="numeric" value={f.remote}
          onChange={(e) => setF({ ...f, remote: e.target.value, local: f.local === f.remote ? e.target.value : f.local })} />
        <span className="muted">→ localhost:</span>
        <input className="g-in num" placeholder="이 PC" inputMode="numeric" value={f.local} onChange={(e) => setF({ ...f, local: e.target.value })} />
        <label className="muted"><input type="checkbox" checked={f.https} onChange={(e) => setF({ ...f, https: e.target.checked })} /> https</label>
        <button className="g-btn primary" disabled={busy || !(f.local || f.remote)} onClick={add}>추가하고 켜기</button>
      </div>
      {err && <div className="err">{err}</div>}
    </div>
  );
}
