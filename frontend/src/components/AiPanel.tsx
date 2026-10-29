// AI 사용량 영역: 선택한 공급자의 한도 + [전체 | PC별] 사용 현황. 세션을 누르면 상세.
import { useState } from "react";
import type { AiProvider, ProviderMachineState, Usage } from "../types";
import { ago, fmtT, providerColor, store, usePoll } from "../lib";
import { DayTimeline, WeekBars } from "./charts";
import { UsageDetail } from "./UsageDetail";
import { Muted } from "./ui";
import { ClaudeView } from "./ClaudeView";
import type { ViewSession } from "./ClaudeView";
import { LimitsCard } from "./PlanCard";
import { SegTabs } from "./SegTabs";

/** 여러 PC의 사용량을 하나로 합침 (같은 계정 기준 요약) */
export function mergeUsage(states: ProviderMachineState[]): Usage | null {
  const us = states.map((s) => s.usage).filter(Boolean) as Usage[];
  if (!us.length) return null;
  const today = { in: 0, out: 0, cw: 0, cr: 0, total: 0, count: 0, cost: 0 };
  const days = new Map<string, number>();
  const minutes = new Array(60).fill(0), outs = new Array(60).fill(0);
  let last: string | null = null, lastModel: string | null = null, sess = 0;
  for (const u of us) {
    for (const k of Object.keys(today) as (keyof typeof today)[]) today[k] += u.today?.[k] || 0;
    for (const d of u.days || []) days.set(d.date, (days.get(d.date) || 0) + d.total);
    (u.minutes || []).forEach((v, i) => { minutes[i] += v; });
    (u.minutes_out || []).forEach((v, i) => { outs[i] += v; });
    sess += u.sessions_today || 0;
    if (u.last_activity && (!last || new Date(u.last_activity) > new Date(last))) { last = u.last_activity; lastModel = u.last_model; }
  }
  return {
    today, sessions_today: sess, minutes, minutes_out: outs, last_activity: last, last_model: lastModel,
    days: [...days.entries()].sort().slice(-7).map(([date, total]) => ({ date, total })),
  };
}

const lastAct = (iso?: string | null) => (iso ? "마지막 활동 " + ago((Date.now() - new Date(iso).getTime()) / 1000) : "");

/** 계정 이름 목록 (같은 이름 = 같은 계정). 2개 이상일 때만 계정 칩을 보여 줌 */
export const accountNames = (P: AiProvider) => [...new Set(Object.values(P.accounts || {}))];
export const acctOf = (pid: string) => store.get<string>(`ai-acct-${pid}`, "*");

/** 그 공급자를 쓰는 PC(·계정 폴더) 탭 목록: 고른 계정만, 2개 이상이면 맨 앞에 '전체' */
export function aiTabs(P: AiProvider, pid?: string) {
  const acct = pid && accountNames(P).length > 1 ? acctOf(pid) : "*";
  const ids = P.machines.slice().sort((a, b) => (a === "local" ? -1 : b === "local" ? 1 : a.localeCompare(b)))
    .filter((k) => acct === "*" || (P.accounts?.[k] || "기본") === acct);
  return ids.length > 1 ? ["*", ...ids] : ids;
}

export function AiPanel({ pid, P, tab, onTab, names, now, running, durationSec, progressKey, onSession, onBrowse }: {
  pid: string; P: AiProvider; tab: number; onTab: (i: number) => void; names: (id?: string) => string; now: number;
  running: boolean; durationSec: number; progressKey: number;
  onSession: (s: ViewSession) => void; onBrowse: () => void;
}) {
  const col = providerColor(pid);
  const accts = accountNames(P);
  const [acct, setAcctState] = useState(() => (accts.length > 1 ? acctOf(pid) : "*"));
  const setAcct = (a: string) => { setAcctState(a); store.set(`ai-acct-${pid}`, a); onTab(0); };
  const tabs = aiTabs(P, pid);
  const cur = tabs[Math.min(tab, tabs.length - 1)] || "*";
  const states = P.states;
  const sessionsOf = (mid: string) => (states[mid]?.sessions || []).map((s) => ({ ...s, machine_id: mid }));
  const all = cur === "*";
  const U = all ? mergeUsage(tabs.slice(1).map((k) => states[k]).filter(Boolean)) : states[cur]?.usage || null;
  const S: ViewSession[] = all
    ? tabs.slice(1).flatMap((m) => sessionsOf(m).map((s) => ({ ...s, machine: names(m) })))
      .sort((a, b) => ({ waiting: 2, working: 1 }[b.state as string] || 0) - ({ waiting: 2, working: 1 }[a.state as string] || 0) || (b.last_activity || 0) - (a.last_activity || 0))
    : sessionsOf(cur);
  const st = all ? null : states[cur];
  const note = all ? lastAct(U?.last_activity)
    : !st ? "최근 기록 없음"
    : [lastAct(st.usage?.last_activity), st.ok ? null : `최근 수집 실패${st.error ? ` (${st.error})` : ""}`].filter(Boolean).join(" · ");
  const items = tabs.map((m) => {
    const u = m === "*" ? U : states[m]?.usage;
    const ss = m === "*" ? null : states[m];
    return { label: m === "*" ? `전체 ${tabs.length - 1}대` : names(m), sub: u ? `오늘 ${fmtT(u.today?.total)}` : "기록 없음",
      warn: !!ss && !ss.ok && !ss.absent };
  });
  // 카드에는 한눈에 볼 최근 7일만. 잔디·30일·90일은 '상세' 창에서
  const [detail, setDetail] = useState(false);
  const filtered = acct !== "*" && accts.length > 1;
  const mq = !all ? `&machine=${encodeURIComponent(cur)}` : filtered ? `&machine=${encodeURIComponent(tabs.slice(1).join(","))}` : "";
  const { data: tl } = usePoll<{ start: number; step: number; buckets: { x: number; total: number; out: number }[] }>(
    `/api/providers/${pid}/timeline?step=600${mq}`, 10000);
  const weekTotal = (U?.days || []).reduce((a, d) => a + d.total, 0);
  const historyTitle = (
    <>
      최근 7일
      <span className="r num">합계 <b className="hl">{fmtT(weekTotal)}</b>
        <button className="link more" onClick={() => setDetail(true)}>잔디 · 30일 · 90일 ›</button></span>
    </>
  );
  const history = U ? <WeekBars days={U.days} stops={col.stops} /> : <Muted>최근 7일 기록 없음</Muted>;
  const timeline = tl ? <DayTimeline buckets={tl.buckets} start={tl.start} nowTs={now} stops={col.stops} /> : null;
  // 한도: 고른 계정 것 (모든 계정이면 이 PC 기본 계정)
  const L = filtered ? (P.limits?.accounts?.find((a) => a.label === acct) || null) : P.limits;
  const mainLabel = P.limits?.accounts?.[0]?.label;
  const head = (
    <div className="subhead">
      <SegTabs items={items} index={Math.min(tab, tabs.length - 1)} onSelect={onTab} running={running} durationSec={durationSec} progressKey={progressKey} />
      <div className="note">{note}<button className="link" onClick={onBrowse}>지난 세션 보기 ›</button></div>
    </div>
  );
  return (
    <>
      {accts.length > 1 && (
        <div className="acct-chips">
          <span className="muted">계정</span>
          {["*", ...accts].map((a) => (
            <button key={a} className={acct === a ? "on" : ""} onClick={() => setAcct(a)}>{a === "*" ? "모든 계정" : a}</button>
          ))}
        </div>
      )}
      <LimitsCard pid={pid} label={P.label} L={L} now={now} machineName={(id) => (id === "api" ? "" : names(id))}
        account={L?.label || (accts.length > 1 ? mainLabel : undefined)} main={!L?.label || L.label === mainLabel} />
      <div className="views">
        <div className="view on">
          <ClaudeView head={head} U={U} S={S} now={now} stops={col.stops} onSession={onSession} history={history} historyTitle={historyTitle}
            timeline={timeline}
            emptyText={U?.last_activity ? `열린 세션 없음 · ${lastAct(U.last_activity)}` : "열린 세션 없음"} />
        </div>
      </div>
      {detail && <UsageDetail pid={pid} label={P.label} machine={all ? undefined : cur} machineName={all ? undefined : names(cur)}
        names={names} onClose={() => setDetail(false)} />}
    </>
  );
}
