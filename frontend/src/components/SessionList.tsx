// 세션 목록 (열린 세션·지난 세션 공통): 프로젝트별로 묶고, 세션마다 하위 에이전트를 펼쳐 볼 수 있음
import { useState } from "react";
import type { SubAgent } from "../types";
import { C, ago, fmtT, level } from "../lib";
import { Muted } from "./ui";

export interface ListSession {
  session: string; project?: string | null; state?: string | null; live?: boolean;
  name?: string | null; title?: string | null; slug?: string | null;
  model?: string | null; model_name?: string | null; effort?: string | null;
  waiting_for?: string | null; state_since?: number | null;
  last_activity?: number | null; tokens?: number | null; context?: number | null; ctx_pct?: number | null;
  machine?: string; machine_id?: string; agents?: SubAgent[];
}

/** 컨텍스트 %: 정확한 값이 없으면 창 크기를 추정해 계산 (alerts.py 와 같은 규칙) */
export function ctxPct(s: ListSession): { pct: number; est: boolean } | null {
  if (s.ctx_pct != null) return { pct: s.ctx_pct, est: false };
  if (!s.context) return null;
  const m = (s.model_name || s.model || "").toLowerCase();
  const win = s.context > 200_000 || m.includes("[1m]") || ["-5", "4-6", "4-7", "4-8"].some((x) => m.includes(x)) ? 1_000_000 : 200_000;
  return { pct: (100 * s.context) / win, est: true };
}

const titleOf = (s: ListSession) => s.name || s.title || s.slug || s.project || s.session.slice(0, 8);
const working = (s: ListSession) => s.state === "working";
const waiting = (s: ListSession) => s.state === "waiting";
/** 정렬 순위: 입력 대기 > 작업 중 > 나머지 */
const rank = (s: ListSession) => (waiting(s) ? 2 : working(s) ? 1 : 0);
const WAIT_TEXT: Record<string, string> = {
  "dialog open": "승인·선택 대기", "input needed": "입력 필요", "sandbox request": "샌드박스 허용 대기",
  "worker request": "작업자 요청 대기", "goal proposal": "목표 확인 대기",
};
export const waitText = (w?: string | null) => (w && WAIT_TEXT[w]) || "입력 대기";

function AgentRows({ agents, now }: { agents: SubAgent[]; now: number }) {
  return (
    <div className="agents">
      {agents.map((a) => (
        <div key={a.agent} className="agent">
          <span className="branch">└</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="n">{a.description || a.type || a.agent}</div>
            <div className="s num">{[a.type, (a.model || "").replace("claude-", ""), `${a.count}회 응답`, a.last ? ago(now - a.last) : ""].filter(Boolean).join(" · ")}</div>
          </div>
          <b className="num">{fmtT(a.tokens)}</b>
        </div>
      ))}
    </div>
  );
}

function Row({ s, now, onOpen }: { s: ListSession; now: number; onOpen?: (s: ListSession) => void }) {
  const [open, setOpen] = useState(false);
  const agents = s.agents || [];
  const model = (s.model_name || (s.model || "").replace("claude-", "")) + (s.effort ? " · " + s.effort : "");
  const cp = s.live === false ? null : ctxPct(s);
  const st = s.live === false ? "종료" : working(s) || waiting(s) ? null : ago(s.last_activity ? now - s.last_activity : null);
  return (
    <>
      <div className={`sess ${s.live === false ? "" : s.state || ""} ${onOpen ? "click" : ""}`} onClick={onOpen ? () => onOpen(s) : undefined}>
        <span className="st" />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="n">{titleOf(s)}</div>
          <div className="s num">
            {s.machine ? <span className="pc">{s.machine}</span> : null}
            {model || "–"} · {waiting(s) ? <span className="wait-tag">{waitText(s.waiting_for)}{s.state_since ? ` · ${ago(now - s.state_since)}` : ""}</span>
              : working(s) ? <span style={{ color: C.green }}>작업 중</span> : st}
            {s.live === false && s.last_activity ? ` · ${ago(now - s.last_activity)}` : ""}
            {s.tokens ? ` · ${fmtT(s.tokens)} tok` : ""}
            {agents.length > 0 && (
              <button className="link sub-toggle" onClick={(e) => { e.stopPropagation(); setOpen(!open); }}>
                하위 에이전트 {agents.length}개 {open ? "▾" : "▸"}
              </button>
            )}
          </div>
        </div>
        <div className="ctx num">
          {cp ? (
            <>
              {cp.pct >= 90 && <span className="ctx-warn">곧 압축</span>}
              컨텍스트 {cp.est ? "~" : ""}{Math.round(cp.pct)}%
              <div className="bar"><i style={{ width: `${Math.min(100, cp.pct)}%`, background: level(cp.pct) || C.cyan }} /></div>
              {s.context ? <small>{fmtT(s.context)}</small> : null}
            </>
          ) : null}
        </div>
      </div>
      {open && <AgentRows agents={agents} now={now} />}
    </>
  );
}

/** 프로젝트별 묶음. 묶음이 하나뿐이면 머리줄 없이 바로 목록 */
export function SessionList({ sessions, now, onOpen, empty }: {
  sessions: ListSession[]; now: number; onOpen?: (s: ListSession) => void; empty?: string;
}) {
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  if (!sessions.length) return <Muted>{empty || "세션 없음"}</Muted>;
  const groups = new Map<string, ListSession[]>();
  for (const s of sessions) {
    const k = s.project || "(기타)";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k)!.push(s);
  }
  const list = [...groups.entries()].map(([project, ss]) => ({
    project, ss: ss.sort((a, b) => rank(b) - rank(a) || (b.last_activity || 0) - (a.last_activity || 0)),
    busy: ss.filter(working).length, wait: ss.filter(waiting).length, tokens: ss.reduce((a, s) => a + (s.tokens || 0), 0),
    last: Math.max(...ss.map((s) => s.last_activity || 0)),
  })).sort((a, b) => b.wait - a.wait || b.busy - a.busy || b.last - a.last);
  if (list.length === 1) return <>{list[0].ss.map((s) => <Row key={(s.machine_id || "") + s.session} s={s} now={now} onOpen={onOpen} />)}</>;
  return (
    <>
      {list.map((g) => {
        const isClosed = closed[g.project] ?? false;
        return (
          <div key={g.project} className="sgroup">
            <div className="sg-head" onClick={() => setClosed({ ...closed, [g.project]: !isClosed })}>
              <span className="caret">{isClosed ? "▸" : "▾"}</span>
              <b>{g.project}</b>
              <span className="muted num">{g.ss.length}개{g.wait ? ` · ${g.wait} 입력 대기` : ""}{g.busy ? ` · ${g.busy} 작업 중` : ""} · {fmtT(g.tokens)} tok</span>
            </div>
            {!isClosed && g.ss.map((s) => <Row key={(s.machine_id || "") + s.session} s={s} now={now} onOpen={onOpen} />)}
          </div>
        );
      })}
    </>
  );
}
