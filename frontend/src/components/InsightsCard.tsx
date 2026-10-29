// AI 인사이트: 프로젝트 순위 · 시간대 패턴(요일 × 시간) · API 환산 비용 — 컴퓨터 영역 아래, 모든 PC 합산
import { Fragment, useState } from "react";
import { C, fmtT, providerColor, wash, scaleColor, store, usePoll } from "../lib";
import { Card, Icon, Muted } from "./ui";

type Tab = "projects" | "pattern" | "cost";
type Range = "today" | "7d" | "30d";
const RANGE_LABEL: Record<Range, string> = { today: "오늘", "7d": "7일", "30d": "30일" };
const DAYS = "월화수목금토일";

interface Projects { total: number; projects: { project: string; tokens: number; sessions: number; machines: string[] }[] }
interface Pattern { days: number; cells: number[][] }
interface Cost { usd: number; models: { model: string; usd: number; tokens: number }[]; unknown_models: string[]; prices_as_of: string }

const usd = (v: number) => `$${v >= 1000 ? v.toLocaleString("en-US", { maximumFractionDigits: 0 }) : v.toFixed(v >= 100 ? 0 : 2)}`;

export function InsightsCard({ pid, label }: { pid: string; label: string }) {
  const [tab, setTab] = useState<Tab>(() => store.get<Tab>("ins-tab", "projects"));
  const [rng, setRng] = useState<Range>(() => store.get<Range>("ins-range", "7d"));
  const pick = (t: Tab) => { setTab(t); store.set("ins-tab", t); };
  const pickR = (r: Range) => { setRng(r); store.set("ins-range", r); };
  const col = providerColor(pid);
  const { data: P } = usePoll<Projects>(tab === "projects" ? `/api/providers/${pid}/projects?range=${rng}` : null, 60000);
  const { data: T } = usePoll<Pattern>(tab === "pattern" ? `/api/providers/${pid}/pattern?days=30` : null, 600000);
  const { data: K } = usePoll<Cost>(tab === "cost" ? `/api/providers/${pid}/cost?range=${rng}` : null, 60000);

  const tabs: [Tab, string][] = [["projects", "프로젝트 순위"], ["pattern", "시간대 패턴"], ["cost", "API 환산 비용"]];
  const right = tab === "pattern" ? <span className="muted">최근 30일 · 모든 PC</span> : (
    <span className="minitabs">
      {(Object.keys(RANGE_LABEL) as Range[]).map((r) => <button key={r} className={rng === r ? "on" : ""} onClick={() => pickR(r)}>{RANGE_LABEL[r]}</button>)}
    </span>
  );
  let body;
  if (tab === "projects") {
    const max = Math.max(1, ...(P?.projects || []).map((p) => p.tokens));
    body = !P ? <Muted>불러오는 중…</Muted> : !P.projects.length ? <Muted>이 기간 기록 없음</Muted> : (
      <div className="ins-list">
        {P.projects.slice(0, 8).map((p, i) => (
          <div key={p.project} className="ins-row">
            <span className="rank num">{i + 1}</span>
            <span className="n" title={p.machines.join(", ")}>{p.project}<small>{p.sessions}세션 · {p.machines.join(", ")}</small></span>
            <span className="bar"><i style={{ width: `${(100 * p.tokens) / max}%`, background: scaleColor(col.stops, 0.35 + 0.65 * (p.tokens / max)) }} /></span>
            <span className="v num">{fmtT(p.tokens)}<small>{P.total ? `${Math.round((100 * p.tokens) / P.total)}%` : ""}</small></span>
          </div>
        ))}
      </div>
    );
  } else if (tab === "pattern") {
    const max = Math.max(1, ...(T?.cells || []).flat());
    const peak = T ? T.cells.flatMap((row, d) => row.map((v, h) => ({ v, d, h }))).sort((a, b) => b.v - a.v)[0] : null;
    body = !T ? <Muted>불러오는 중…</Muted> : (
      <div className="pattern">
        <div className="pat-grid">
          <span />
          {Array.from({ length: 24 }, (_, h) => <span key={h} className="hh">{h % 3 === 0 ? h : ""}</span>)}
          {T.cells.map((row, d) => (
            <Fragment key={d}>
              <span className="dd">{DAYS[d]}</span>
              {row.map((v, h) => <i key={`${d}-${h}`} title={`${DAYS[d]} ${h}시 · ${fmtT(v)}`}
                style={{ background: v ? scaleColor(col.stops, 0.12 + 0.88 * (v / max)) : wash(.04) }} />)}
            </Fragment>
          ))}
        </div>
        {peak && peak.v > 0 && <div className="muted">가장 많이 쓰는 때: <b style={{ color: C.text }}>{DAYS[peak.d]}요일 {peak.h}시</b></div>}
      </div>
    );
  } else {
    const max = Math.max(0.01, ...(K?.models || []).map((m) => m.usd));
    body = !K ? <Muted>불러오는 중…</Muted> : (
      <div className="cost">
        <div className="cost-big num">{usd(K.usd)}<small>{RANGE_LABEL[rng]} · API 가격으로 환산</small></div>
        {K.models.slice(0, 5).map((m) => (
          <div key={m.model} className="ins-row">
            <span className="n">{m.model.replace("claude-", "")}<small>{fmtT(m.tokens)} tokens</small></span>
            <span className="bar"><i style={{ width: `${(100 * m.usd) / max}%`, background: col.accent }} /></span>
            <span className="v num">{usd(m.usd)}</span>
          </div>
        ))}
        <div className="muted small">구독 사용량을 공식 API 가격({K.prices_as_of} 기준)으로 계산한 추정값이에요.{K.unknown_models.length ? ` 가격 미상: ${K.unknown_models.join(", ")}` : ""}</div>
      </div>
    );
  }
  return (
    <Card className="insights" title={<>{Icon.spark}<span style={{ marginRight: ".5em" }}>{label} 인사이트</span>
      <span className="minitabs" style={{ marginLeft: 0 }}>
        {tabs.map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => pick(k)}>{l}</button>)}
      </span></>} right={right}>
      <div className="ins-body">{body}</div>
    </Card>
  );
}
