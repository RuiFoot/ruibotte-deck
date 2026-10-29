// 한도 잔량(공급자 공통): 창마다 남은 % 게이지 + 페이스 바(시간 경과 vs 사용량)
import { useState } from "react";
import type { LimitWin, ProviderLimits } from "../types";
import { C, TIME_STOPS, USAGE_STOPS, ago, api, fmtDur, hm, scaleColor, scaleCss } from "../lib";
import { Card, Muted, Ring } from "./ui";
import { FiveHourTrend } from "./charts";

// 남은 % → 색 (100% 초록 … 0% 빨강): 사용률 척도를 거꾸로 적용
const remainColor = (left: number) => scaleColor(USAGE_STOPS, 1 - left / 100);
const USAGE_CSS = scaleCss(USAGE_STOPS);
const TIME_CSS = scaleCss(TIME_STOPS);

const WINDOW_LABEL: Record<string, string> = {
  five_hour: "5시간 한도", seven_day: "주간 한도", one_day: "일간 한도", daily_requests: "오늘 요청 (추정)",
};
const winLabel = (w: LimitWin) => WINDOW_LABEL[w.key] || `${Math.round((w.window_minutes || 0) / 60)}시간 한도`;

/** 사용 속도가 시간 경과보다 빠른지 판단 */
function verdict(used: number, elapsed: number) {
  const diff = used - elapsed;
  if (diff > 15) return { text: "빠르게 쓰는 중", color: C.red, bg: "rgba(255,107,107,.12)" };
  if (diff > 5) return { text: "조금 빠름", color: C.amber, bg: "rgba(247,185,85,.12)" };
  if (diff > 0) return { text: "적정 페이스", color: C.cyan, bg: "rgba(69,205,224,.12)" };
  return { text: "여유 있음", color: C.green, bg: "rgba(67,211,158,.12)" };
}

/** 바 전체 폭에 그라데이션을 깔고 채운 만큼만 보여줌 → 많이 찰수록 바 끝 색이 뒤쪽 색(빨강 등)으로 감 */
function PaceRow({ label, pct, gradient }: { label: string; pct: number; gradient: string }) {
  const w = Math.max(0.5, Math.min(100, pct));
  return (
    <div className="prow">
      <span>{label}</span>
      <span className="track"><i style={{ width: `${w}%`, backgroundImage: gradient, backgroundSize: `${(100 / w) * 100}% 100%` }} /></span>
      <span className="num" style={{ textAlign: "right", color: "var(--text)" }}>{Math.round(pct)}%</span>
    </div>
  );
}

/** 한도 소진 예측: 이번 창에서 지금까지의 평균 속도로 남은 시간을 계산 */
function Forecast({ w, used, now }: { w: LimitWin; used: number; now: number }) {
  const length = (w.window_minutes || 0) * 60;
  if (!w.resets_at || !length || w.reset_passed) return null;
  const elapsed = now - (w.resets_at - length);
  if (elapsed < 600 || used <= 0) return <span className="muted">사용 기록이 적어 예측 전</span>;
  const rate = used / elapsed;                        // %/초
  const fullAt = now + (100 - used) / rate;
  if (fullAt < w.resets_at) {
    const d = new Date(fullAt * 1000);
    const when = (w.window_minutes || 0) > 1440
      ? d.toLocaleDateString("ko-KR", { weekday: "short" }) + " " + hm(d) : hm(d);
    return <span className="fc bad">이 속도면 <b>{when}</b>쯤 소진 · 리셋 {fmtDur(w.resets_at - fullAt)} 전</span>;
  }
  const projected = Math.min(100, used + rate * (w.resets_at - now));
  return <span className="fc">이 속도면 리셋 때 약 <b>{Math.round(projected)}%</b> 사용</span>;
}

function Window({ w, history, now }: { w: LimitWin; history?: ProviderLimits["history"]; now: number }) {
  const used = w.used_pct ?? 0, left = Math.max(0, 100 - used), col = remainColor(left);
  const r = w.resets_at ? new Date(w.resets_at * 1000) : null;
  const long = (w.window_minutes || 0) > 1440;
  const when = !r ? "" : long ? r.toLocaleDateString("ko-KR", { month: "numeric", day: "numeric", weekday: "short" }) + " " + hm(r) : hm(r);
  const reset = w.reset_passed ? "리셋됨 · 갱신 대기"
    : w.reset_in_sec != null ? <><b>{fmtDur(w.reset_in_sec)}</b> 후 리셋 <span className="muted">· {when}{w.estimated && w.key !== "daily_requests" ? " (로그 기준 추정)" : ""}</span></>
    : <span className="muted">리셋 시각 미확인</span>;
  const v = w.elapsed_pct != null ? verdict(used, w.elapsed_pct) : null;
  return (
    <div className="lim">
      <div className="top">
        <Ring pct={left} color={col} useLevel={false} className="ring" />
        <div style={{ minWidth: 0 }}>
          <div className="v num" style={{ color: col }}>{Math.round(left)}<small>% 남음</small></div>
          <div className="l">{winLabel(w)}{w.requests != null ? ` · ${w.requests}/${w.limit}회` : ""} · {reset}</div>
        </div>
      </div>
      {w.elapsed_pct != null && w.key !== "daily_requests" ? (
        <div className="pace">
          <PaceRow label="시간 경과" pct={w.elapsed_pct} gradient={TIME_CSS} />
          <PaceRow label="사용량" pct={used} gradient={USAGE_CSS} />
          <div className="verdict">
            <b style={{ color: v!.color, background: v!.bg }}>{v!.text}</b>
            <Forecast w={w} used={used} now={now} />
          </div>
        </div>
      ) : (
        <div className="pace">
          <PaceRow label="사용량" pct={used} gradient={USAGE_CSS} />
          {w.key === "five_hour" && history ? (
            <>
              <FiveHourTrend history={history} now={now} />
              <div className="legend" style={{ marginTop: 0 }}><span><i style={{ background: C.amber }} />최근 6시간 사용률 추이</span></div>
            </>
          ) : w.key === "daily_requests" ? <Muted>로컬 기록이 없어 오늘 응답 수 ÷ 하루 기준값으로 추정한 값이에요.</Muted> : null}
        </div>
      )}
    </div>
  );
}

function sourceText(pid: string, L: ProviderLimits, now: number, machineName: (id?: string) => string) {
  const age = L.captured_at ? ago(now - L.captured_at) : "";
  if (pid === "claude") {
    if (L.source === "api") return `Claude 계정 사용량 · ${age} 갱신 (3분 간격)`;
    return `${machineName(L.machine)} ${L.source === "statusline" ? "CLI" : "데스크톱 앱"} 기록 · ${age} 갱신${L.source === "desktop" ? " · 최대 15분 지연" : ""}`;
  }
  if (L.source === "estimate") return "추정값";
  return `${machineName(L.machine)} 마지막 요청 시점 · ${age}${L.plan_type ? ` · ${L.plan_type} 요금제` : ""}`;
}

/** Claude 한도 '새로고침': 서버가 대기 시간을 무시하고 지금 바로 조회 (최소 1분 간격) */
function RefreshButton() {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const run = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api<{ ok: boolean; message: string }>("/api/claude/limits/refresh", "POST");
      setMsg({ ok: r.ok, text: r.message });
    } catch (e) { setMsg({ ok: false, text: String((e as Error).message || e) }); }
    setBusy(false);
    setTimeout(() => setMsg(null), 15000);
  };
  return (
    <span className="refresh">
      {msg && <span style={{ color: msg.ok ? C.green : C.amber }}>{msg.text}</span>}
      <button className="link" disabled={busy} onClick={run} title="대기 시간을 무시하고 지금 다시 조회">{busy ? "조회 중…" : "↻ 새로고침"}</button>
    </span>
  );
}

/** '다시 로그인': 이 PC에 claude auth login 창을 띄움 (브라우저 로그인) */
function LoginButton() {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const run = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api<{ ok: boolean; message: string }>("/api/claude/login", "POST");
      setMsg({ ok: r.ok, text: r.message });
    } catch (e) { setMsg({ ok: false, text: String((e as Error).message || e) }); }
    setBusy(false);
  };
  return (
    <div className="login-row">
      <button className="login-btn" disabled={busy} onClick={run}>{busy ? "여는 중…" : "↗ 다시 로그인 (브라우저)"}</button>
      {msg && <span style={{ color: msg.ok ? C.green : C.amber }}>{msg.text}</span>}
    </div>
  );
}

const STALE_SEC = 3 * 3600;   // 정확한 값(API)이 아닌 기록이 이보다 오래되면 숫자를 흐리게 + 'N일 전 값'

export function LimitsCard({ pid, label, L, now, machineName, account, main = true }:
  { pid: string; label: string; L: ProviderLimits | null; now: number; machineName: (id?: string) => string;
    account?: string; main?: boolean }) {
  const wins = L?.windows || [];
  const age = L?.captured_at ? now - L.captured_at : null;
  const old = pid === "claude" && L?.source !== "api" && age != null && age > STALE_SEC;
  const low = !old && wins.some((w) => !w.estimated && 100 - (w.used_pct ?? 0) <= 10);
  const api = L?.plan_api;
  const stale = !!L?.captured_at && now - L.captured_at > 1200 && L.source !== "estimate" && pid === "claude";
  return (
    <Card className="plan-card" title={<>{label} 한도 잔량{account ? <b className="acct-name">{account}</b> : L?.by_account ? ` · 계정 ${L.by_account.length}개 중 최근` : ""}{pid === "claude" && main && <RefreshButton />}</>}
      right={L?.captured_at ? <span style={{ color: stale ? C.amber : undefined }}>{sourceText(pid, L, now, machineName)}</span> : null} alert={low}>
      {pid === "claude" && main && api?.error && L?.source !== "api" && (
        <div className="plan-warn">
          ⚠ 정확한 계정 사용량 조회 실패 — {api.login_required
            ? <>로그인이 만료됐어요. 터미널에서 <code>claude auth login</code>으로 다시 로그인하면 바로 복구돼요.</>
            : <>{api.retry_in_sec > 0 ? `${Math.ceil(api.retry_in_sec / 60)}분 뒤 자동 재시도` : "곧 재시도"} ({api.error}) — '새로고침'으로 다시 시도하거나,
                계속 실패하면 아래 버튼으로 다시 로그인하세요.</>}
          {L?.captured_at ? <> · 아래 숫자는 <b>{ago(now - L.captured_at)}</b> 기록{old ? "이라 지금과 다를 수 있어요" : ""}</> : ""}
          <LoginButton />
        </div>
      )}
      {wins.length ? (
        <div className={`plan ${old ? "old" : ""}`} style={wins.length === 1 ? { gridTemplateColumns: "1fr" } : undefined}>
          {wins.map((w) => <Window key={w.key + (w.slot || "")} w={w} history={L?.history} now={now} />)}
        </div>
      ) : <Muted>{pid === "claude" && !main ? "이 계정의 한도 기록 없음 — 계정 폴더에 로그인 정보(.credentials.json)가 있으면 정확한 값을 가져와요."
        : pid === "claude" ? "한도 데이터 없음 — Claude 데스크톱 앱을 열면 표시됩니다." : "한도 기록 없음 — 한 번 사용하면 표시됩니다."}</Muted>}
      {L?.credits && (L.credits.has_credits || L.credits.unlimited) && (
        <div className="muted" style={{ marginTop: ".6em" }}>크레딧 {L.credits.unlimited ? "무제한" : L.credits.balance}</div>
      )}
    </Card>
  );
}
