// AI 도구 사용 현황(공급자 공통): 오늘 토큰 + 7일, 실시간 60분, 열린 세션.
// 한 PC 또는 여러 PC 합산(U 를 합쳐서 넘김) 모두 같은 배치로 그림.
import type { ReactNode } from "react";
import type { Session, Usage } from "../types";
import { C, TOKEN_STOPS, fmtT, scaleCss } from "../lib";
import type { Stops } from "../lib";
import { Card, Head, Muted } from "./ui";
import { LiveTokens, WeekBars } from "./charts";
import { SessionList } from "./SessionList";

export type ViewSession = Session & { machine?: string; machine_id?: string };

export function ClaudeView({ icon, title, note, U, S, now, stops = TOKEN_STOPS, outColor = C.cyan, head, onSession, emptyText, history, historyTitle, timeline }: {
  icon?: ReactNode; title?: string; note?: string; U?: Usage | null; S?: ViewSession[] | null; now: number;
  stops?: Stops; outColor?: string; head?: ReactNode; onSession?: (s: ViewSession) => void; emptyText?: string;
  history?: ReactNode; historyTitle?: ReactNode;   // 기간 그래프(없으면 최근 7일 막대)
  timeline?: ReactNode;                             // 오늘 흐름(자정~24시 + 지금 선). 없으면 최근 60분 막대
}) {
  const sessions = S || [];
  const working = sessions.filter((s) => s.state === "working").length;
  const t = U?.today;
  const mins = U?.minutes;
  const isLive = working > 0 || !!(mins && (mins[59] || mins[58]));
  const waiting = sessions.filter((s) => s.state === "waiting").length;
  const tag = `${sessions.length}개 세션${waiting ? ` · ${waiting} 입력 대기` : ""}${working ? ` · ${working} 작업 중` : ""}`;
  return (
    <>
      {head ?? <Head icon={icon} title={title} sub={note} tag={tag} tagCls={waiting ? "a" : working ? "g" : ""} />}

      {/* 오늘 토큰 + 기간 그래프 + 실시간 60분을 한 카드로 (열린 세션 목록에 공간을 더 줌) */}
      <div className="card usage-card">
        <div className="uc-top">
          <div style={{ minWidth: 0 }}>
            <div className="ttl">오늘 사용 토큰</div>
            <div className="big num">{t ? fmtT(t.total) : "–"}<small>tokens</small></div>
            {/* 탭을 바꿔도 카드 높이가 그대로이도록 값이 없어도 같은 줄 수를 유지 */}
            <div className="muted num uc-sub">{t ? `${t.count} 응답 · ${U!.sessions_today} 세션` : "기록 없음"}</div>
            <div className="uc-kv num">
              <span>입력 <b>{fmtT(t?.in)}</b></span><span>출력 <b>{fmtT(t?.out)}</b></span>
              <span>캐시 쓰기 <b>{fmtT(t?.cw)}</b></span><span>캐시 읽기 <b>{fmtT(t?.cr)}</b></span>
            </div>
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="ttl" style={{ marginBottom: ".2em" }}>{historyTitle ?? "최근 7일"}</div>
            <div className="uc-hist">{history ?? (U ? <WeekBars days={U.days} stops={stops} /> : <Muted>–</Muted>)}</div>
          </div>
        </div>
        <div className="uc-live">
          <div className="ttl">
            <span className={`livedot ${isLive ? "on" : ""}`} />{timeline ? "오늘 흐름" : "실시간 · 최근 60분"}
            {timeline && <span className="sub">10분 단위 · <i className="lg" style={{ background: scaleCss(stops) }} />전체 <i className="lg" style={{ background: outColor }} />출력</span>}
            <span className="r num">{mins ? <>최근 60분 <b className="hl">{fmtT(mins.reduce((x, y) => x + y, 0))}</b> · 이번 분 <b className="hl">{fmtT(mins[59])}</b></> : null}</span>
          </div>
          <div className="uc-chart">
            {timeline ?? (mins ? <LiveTokens minutes={mins} outs={U!.minutes_out || []} stops={stops} outColor={outColor} /> : <Muted>수집 중…</Muted>)}
          </div>
        </div>
      </div>

      <Card title="열린 세션" className="grow" right={sessions.length ? tag : ""}>
        <div className="scroll">
          <SessionList sessions={sessions} now={now} onOpen={onSession ? (x) => onSession(x as ViewSession) : undefined}
            empty={emptyText || "열린 세션 없음"} />
        </div>
      </Card>
    </>
  );
}
