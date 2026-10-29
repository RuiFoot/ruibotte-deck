// 세션 상세(분 단위 토큰 추이·모델별 합계)와 지난 세션 목록. 화면 위에 겹쳐 뜨는 창.
import { useState } from "react";
import type { ReactNode } from "react";
import type { SessionDetail, SessionRow, SubAgent } from "../types";
import { C, ago, fmtDur, fmtT, hm, providerColor, usePoll } from "../lib";
import { Muted } from "./ui";
import { SessionMinutes } from "./charts";
import { SessionList } from "./SessionList";
import type { ListSession } from "./SessionList";

export function Overlay({ title, onClose, children, wide }: { title: ReactNode; onClose: () => void; children: ReactNode; wide?: boolean }) {
  return (
    <div className="overlay" onClick={onClose}>
      <div className={`sheet ${wide ? "wide" : ""}`} onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head"><div className="t">{title}</div><button className="x" onClick={onClose}>✕</button></div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>
  );
}

const dt = (t?: number | null) => (t ? new Date(t * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "–");

export function SessionModal({ pid, sid, machine, label, onClose, now }:
  { pid: string; sid: string; machine?: string; label: string; onClose: () => void; now: number }) {
  const q = `/api/providers/${pid}/sessions/${encodeURIComponent(sid)}${machine ? `?machine=${encodeURIComponent(machine)}` : ""}`;
  const { data: D, error } = usePoll<SessionDetail>(q, 15000);
  const info = (D?.info || {}) as Record<string, unknown> & Partial<SessionRow>;
  const T = D?.totals;
  const pts = (D?.minutes || []).map((m) => ({ ts: m.ts, total: m.in + m.out + m.cr + m.cw, out: m.out }));
  const total = T ? (T.i || 0) + (T.o || 0) + (T.cr || 0) + (T.cw || 0) : null;
  const models = Object.entries(D?.models || {}).sort((a, b) => b[1] - a[1]);
  const mtot = models.reduce((a, [, v]) => a + v, 0) || 1;
  const live = !!info.live;
  return (
    <Overlay onClose={onClose} title={<>{label} 세션 · {String(info.name || info.title || info.project || sid.slice(0, 8))}</>}>
      {error && !D ? <div className="err">불러오지 못함: {error}</div> : !D ? <Muted>불러오는 중…</Muted> : (
        <>
          <div className="info-grid num" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
            <div><span>상태</span><b style={{ color: info.state === "working" ? C.green : info.state === "waiting" ? C.amber : undefined }}>{live ? (info.state === "working" ? "작업 중" : info.state === "waiting" ? "입력 대기" : "대기") : "종료됨"}</b></div>
            <div><span>PC</span><b>{String(info.machine || info.machine_id || machine || "–")}</b></div>
            <div><span>프로젝트</span><b>{String(info.project || "–")}</b></div>
            <div><span>모델</span><b>{String(info.model_name || info.model || "–")}</b></div>
            <div><span>시작</span><b>{dt((info.started as number) || (info.started_at as number) || T?.a)}</b></div>
            <div><span>마지막 활동</span><b>{info.last_activity ? ago(now - Number(info.last_activity)) : dt(T?.b)}</b></div>
            <div><span>기록 기간</span><b>{T?.a && T?.b ? fmtDur(T.b - T.a) || "1분 미만" : "–"}</b></div>
            <div><span>컨텍스트</span><b>{info.ctx_pct != null ? `${Math.round(Number(info.ctx_pct))}%` : info.context ? fmtT(Number(info.context)) : "–"}</b></div>
          </div>
          <div className="kv num" style={{ gridTemplateColumns: "repeat(6, 1fr)", marginTop: ".8em" }}>
            <div><b>{fmtT(total)}</b><span>전체 토큰</span></div>
            <div><b>{fmtT(T?.i)}</b><span>입력</span></div>
            <div><b>{fmtT(T?.o)}</b><span>출력{T?.rs ? ` (추론 ${fmtT(T.rs)})` : ""}</span></div>
            <div><b>{fmtT(T?.cr)}</b><span>캐시 읽기</span></div>
            <div><b>{fmtT(T?.cw)}</b><span>캐시 쓰기</span></div>
            <div><b>{T?.n ?? "–"}</b><span>응답</span></div>
          </div>
          <div className="ttl" style={{ marginTop: "1em" }}>분 단위 토큰 추이<span className="r">{pts.length ? `${hm(new Date(pts[0].ts * 1000))} ~ ${hm(new Date(pts[pts.length - 1].ts * 1000))}` : ""}</span></div>
          {pts.length ? <SessionMinutes points={pts} stops={providerColor(pid).stops} /> : <Muted>기록된 토큰 없음 (DB 보관 기간: 30일)</Muted>}
          {Array.isArray(info.agents) && (info.agents as SubAgent[]).length > 0 && (
            <>
              <div className="ttl" style={{ marginTop: "1em" }}>하위 에이전트<span className="r">{(info.agents as SubAgent[]).length}개</span></div>
              {(info.agents as SubAgent[]).map((a) => (
                <div key={a.agent} className="row"><span className="n">{a.description || a.type || a.agent}</span>
                  <span className="muted">{[a.type, (a.model || "").replace("claude-", ""), `${a.count}회`].filter(Boolean).join(" · ")}</span>
                  <span className="v num">{fmtT(a.tokens)}</span></div>
              ))}
            </>
          )}
          {models.length > 0 && (
            <>
              <div className="ttl" style={{ marginTop: "1em" }}>모델별</div>
              {models.map(([m, v]) => (
                <div key={m} className="row"><span className="n">{m}</span>
                  <span className="bar"><i style={{ width: `${(100 * v) / mtot}%`, background: providerColor(pid).accent }} /></span>
                  <span className="v num">{fmtT(v)}</span></div>
              ))}
            </>
          )}
        </>
      )}
    </Overlay>
  );
}

export function SessionBrowser({ pid, label, machines, names, onOpen, onClose, now }: {
  pid: string; label: string; machines: string[]; names: (id?: string) => string;
  onOpen: (s: SessionRow) => void; onClose: () => void; now: number;
}) {
  const [m, setM] = useState<string>("");
  const [days, setDays] = useState(7);
  const { data, error } = usePoll<{ sessions: SessionRow[] }>(`/api/providers/${pid}/sessions?days=${days}${m ? `&machine=${encodeURIComponent(m)}` : ""}`, 30000);
  const rows = data?.sessions || [];
  return (
    <Overlay wide onClose={onClose} title={<>{label} 세션 목록</>}>
      <div className="filters">
        <div className="minitabs">
          <button className={!m ? "on" : ""} onClick={() => setM("")}>전체 PC</button>
          {machines.map((id) => <button key={id} className={m === id ? "on" : ""} onClick={() => setM(id)}>{names(id)}</button>)}
        </div>
        <div className="minitabs">
          {[1, 7, 30].map((d) => <button key={d} className={days === d ? "on" : ""} onClick={() => setDays(d)}>{d === 1 ? "오늘" : `${d}일`}</button>)}
        </div>
        <span className="muted" style={{ marginLeft: "auto" }}>{data ? `${rows.length}개` : ""}</span>
      </div>
      {error && !data ? <div className="err">{error}</div> : !data ? <Muted>불러오는 중…</Muted>
        : <SessionList sessions={rows as ListSession[]} now={now} onOpen={(x) => onOpen(x as SessionRow)} empty="이 기간에 기록된 세션이 없습니다." />}
    </Overlay>
  );
}
