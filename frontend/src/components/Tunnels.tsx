// 원격 PC의 포트를 이 PC의 localhost 로 여는 SSH 터널 (서버는 tunnels.py)
// 원격에서 지금 열려 있는 포트를 보여 주고 '열기' 한 번으로 터널+브라우저. 그렇게 연 건 임시(끄면 사라짐), 📌로 저장.
import { useCallback, useEffect, useRef, useState } from "react";
import { C, ago, api } from "../lib";
import { Muted } from "./ui";

interface Tunnel {
  id: string; machine: string; name: string; local: number; remote: number; host: string; scheme: string; temp?: boolean;
  state: "off" | "connecting" | "on" | "retry"; error?: string | null; target?: string | null; retry_in?: number;
}
interface Port { port: number; proc: string; web: boolean; v4?: boolean }
interface PortsResp { ports: Port[]; checked_at: number }

const STATE: Record<Tunnel["state"], [string, string]> = {
  off: ["꺼짐", "var(--dim)"], connecting: ["연결 중…", C.amber], on: ["켜짐", C.green], retry: ["다시 연결 대기", C.red],
};
// 터널은 이 PC의 localhost 에 열리므로 다른 기기(휴대폰 등)에서 '열기'는 의미 없음
const HERE = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
const LOCAL = ["127.0.0.1", "localhost", "::1"];   // 원격의 자기 자신 주소
const url = (t: Tunnel) => `${t.scheme}://localhost:${t.local}`;

export function TunnelPanel({ id }: { id: string }) {
  const [list, setList] = useState<Tunnel[] | null>(null);
  const [ports, setPorts] = useState<PortsResp | null>(null);
  const [portsBusy, setPortsBusy] = useState(false);
  const [all, setAll] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [manual, setManual] = useState("");
  // '열기'를 누른 터널: 켜지면 미리 띄워 둔 창을 그 주소로 (팝업 차단을 피하려고 클릭 순간에 창을 엶)
  const pending = useRef<{ remote: number; win: Window | null; until: number } | null>(null);

  const load = useCallback(() => api<{ tunnels: Tunnel[] }>(`/api/tunnels?machine=${encodeURIComponent(id)}`)
    .then((d) => {
      setList(d.tunnels);
      const p = pending.current, t = p && d.tunnels.find((x) => x.remote === p.remote);
      if (p && t?.state === "on") {
        if (p.win && !p.win.closed) p.win.location.href = url(t); else window.open(url(t), "_blank");
        pending.current = null;
      } else if (p && (Date.now() > p.until || (t && t.state === "retry"))) {
        p.win?.close(); pending.current = null;
      }
    }).catch((e) => setErr(String(e.message || e))), [id]);
  useEffect(() => { load(); const t = setInterval(load, 1500); return () => clearInterval(t); }, [load]);

  const scan = useCallback(() => {
    setPortsBusy(true);
    api<PortsResp>(`/api/tunnels/ports?machine=${encodeURIComponent(id)}`)
      .then(setPorts).catch((e) => setErr(String(e.message || e))).finally(() => setPortsBusy(false));
  }, [id]);
  useEffect(() => { scan(); }, [scan]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); } catch (e) { setErr(String((e as Error).message || e)); }
    setBusy(false); load();
  };
  const open = (remote: number, name = "", v4 = true) => {
    const t = list?.find((x) => x.remote === remote && LOCAL.includes(x.host));
    if (t?.state === "on") { if (HERE) window.open(url(t), "_blank"); return; }
    if (HERE) pending.current = { remote, win: window.open("about:blank", "_blank"), until: Date.now() + 40000 };
    run(() => api("/api/tunnels/quick", "POST", { machine: id, remote, name, v4 }));
  };

  if (!list) return err ? <div className="err">{err}</div> : <Muted>불러오는 중…</Muted>;
  const byRemote = new Map(list.filter((t) => LOCAL.includes(t.host)).map((t) => [t.remote, t]));
  const shown = ports ? ports.ports.filter((p) => all || p.web) : [];
  const hidden = ports ? ports.ports.length - ports.ports.filter((p) => p.web).length : 0;

  return (
    <div className="tunnels">
      {list.map((t) => {
        const [label, color] = STATE[t.state];
        const on = t.state !== "off";
        return (
          <div key={t.id} className={`ctr ${t.state === "retry" ? "down" : ""}`}>
            <span className="dot" style={{ background: color }} />
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="n">{t.name}{t.temp && <span className="tun-tag">임시</span>}</div>
              <div className="s num" title={t.error || t.target || ""}>
                localhost:{t.local} → {LOCAL.includes(t.host) ? "" : t.host}:{t.remote}
                {" · "}<span style={{ color }}>{label}{t.state === "retry" && t.retry_in ? ` ${t.retry_in}초` : ""}</span>
                {t.state === "retry" && t.error ? ` · ${t.error}` : ""}
              </div>
            </div>
            <div className="tun-act">
              {t.state === "on" && HERE && <a className="g-btn" href={url(t)} target="_blank" rel="noreferrer">열기 ↗</a>}
              {t.temp && <button className="g-btn" disabled={busy} title="저장 — 끄더라도 목록에 남고, 켜 두면 앱을 다시 켜도 자동 연결"
                onClick={() => run(() => api(`/api/tunnels/${t.id}/keep`, "POST"))}>📌</button>}
              <button className={`g-btn ${on ? "" : "primary"}`} disabled={busy} title={t.temp && on ? "끄면 목록에서 사라져요" : undefined}
                onClick={() => run(() => api(`/api/tunnels/${t.id}/${on ? "stop" : "start"}`, "POST"))}>{on ? "끄기" : "켜기"}</button>
              {!t.temp && <button className="g-btn" disabled={busy} title="목록에서 삭제"
                onClick={() => { if (confirm(`'${t.name}' 터널을 지울까요?`)) run(() => api(`/api/tunnels/${t.id}`, "DELETE")); }}>✕</button>}
            </div>
          </div>
        );
      })}

      <div className="sub-ttl tun-head">
        <span>원격에서 열려 있는 포트</span>
        <span className="muted">{portsBusy ? "확인 중…" : ports ? `${ago(Date.now() / 1000 - ports.checked_at)} 확인` : ""}</span>
        <button className="g-btn" disabled={portsBusy} onClick={scan}>↻</button>
      </div>
      {!ports ? <Muted>{portsBusy ? "원격 PC에 물어보는 중… (몇 초 걸려요)" : "포트 목록을 못 가져왔어요"}</Muted> : (
        <div className="tun-ports">
          {shown.map((p) => {
            const t = byRemote.get(p.port);
            return (
              <button key={p.port} className={`tun-port ${t && t.state !== "off" ? "on" : ""}`} disabled={busy}
                title={t ? `localhost:${t.local} · ${STATE[t.state][0]}` : `localhost 로 열기`} onClick={() => open(p.port, p.proc, p.v4 !== false)}>
                <b className="num">{p.port}</b><span>{p.proc || "?"}</span>
                {t && t.state !== "off" && <i style={{ background: STATE[t.state][1] }} />}
              </button>
            );
          })}
          {hidden > 0 && <button className="g-btn" onClick={() => setAll(!all)}>{all ? "접기" : `나머지 ${hidden}개`}</button>}
        </div>
      )}
      <div className="tun-add">
        <span className="muted">포트 직접</span>
        <input className="g-in num" placeholder="예: 3000" inputMode="numeric" value={manual}
          onChange={(e) => setManual(e.target.value.replace(/\D/g, ""))}
          onKeyDown={(e) => { if (e.key === "Enter" && manual) { open(Number(manual)); setManual(""); } }} />
        <button className="g-btn primary" disabled={busy || !manual} onClick={() => { open(Number(manual)); setManual(""); }}>열기</button>
        <span className="muted tun-hint">끄면 사라지는 임시 터널 · 📌 누르면 저장(켜 두면 앱을 다시 켜도 자동 연결)</span>
      </div>
      {err && <div className="err">{err}</div>}
    </div>
  );
}
