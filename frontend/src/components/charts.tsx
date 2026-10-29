// Recharts 기반 차트들. 2초마다 데이터가 바뀌므로 애니메이션은 끄고, 호버 툴팁만 사용.
import {
  Area, AreaChart, Bar, BarChart, Cell, ComposedChart, LabelList, Line, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid,
} from "recharts";
import type { TooltipProps } from "recharts";
import { C, TOKEN_STOPS, wash, fmtRate, fmtT, hm, scaleColor, scaleCss } from "../lib";
import type { Stops } from "../lib";
import type { NetPoint, SeriesPoint } from "../types";

export const tokenColor = (t: number) => scaleColor(TOKEN_STOPS, t);
export const TOKEN_GRADIENT = scaleCss(TOKEN_STOPS);

const grid = () => <CartesianGrid stroke={wash(.06)} strokeDasharray="3 4" vertical={false} />;

function Tip({ active, payload, label, fmt, labelFmt }: TooltipProps<number, string> & {
  fmt: (v: number, name: string) => string; labelFmt?: (l: unknown) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tip">
      {labelFmt && <div className="h">{labelFmt(label)}</div>}
      {payload.map((p) => (
        <div key={String(p.dataKey)} style={{ color: p.color }}>{p.name} <b className="num">{fmt(Number(p.value), String(p.name))}</b></div>
      ))}
    </div>
  );
}

const gradient = (id: string, color: string, top = 0.35) => (
  <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stopColor={color} stopOpacity={top} />
    <stop offset="1" stopColor={color} stopOpacity={0} />
  </linearGradient>
);

/** 최근 5분 CPU/메모리(%) + 네트워크(B/s) */
export function SystemHistory({ cpu, mem, down, up, stepSec, times }:
  { cpu: (number | null)[]; mem: (number | null)[]; down: (number | null)[]; up: (number | null)[]; stepSec: number; times?: number[] }) {
  const n = Math.max(cpu.length, mem.length);
  // times(실제 시각)가 있으면 DB 기록: 빈 구간(null)은 선을 끊어 보여줌
  const data = Array.from({ length: n }, (_, i) => ({
    t: times ? times[i] : -(n - 1 - i) * stepSec, cpu: cpu[i] ?? null, mem: mem[i] ?? null,
    down: times ? down[i] ?? null : down[i] ?? 0, up: times ? up[i] ?? null : up[i] ?? 0,
  }));
  const long = stepSec >= 3600;
  const secLabel = times
    ? (l: unknown) => { const d = new Date(Number(l) * 1000); return long || stepSec >= 300 ? d.toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : hm(d); }
    : (l: unknown) => (Number(l) === 0 ? "지금" : `${Math.round(-Number(l))}초 전`);
  return (
    <>
      <div className="chart-fill" style={{ height: "5.2em" }}>
        <ResponsiveContainer>
          <AreaChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
            <defs>{gradient("gCpu", C.blue)}</defs>
            {grid()}
            <XAxis dataKey="t" hide />
            <YAxis domain={[0, 100]} hide />
            <Tooltip content={<Tip fmt={(v) => `${v.toFixed(0)}%`} labelFmt={secLabel} />} cursor={{ stroke: wash(.2) }} />
            <Area type="monotone" dataKey="mem" name="메모리" stroke={C.violet} strokeWidth={1.8} fill="none" isAnimationActive={false} dot={false} />
            <Area type="monotone" dataKey="cpu" name="CPU" stroke={C.blue} strokeWidth={1.8} fill="url(#gCpu)" isAnimationActive={false} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div style={{ height: "2.8em", marginTop: ".4em" }}>
        <ResponsiveContainer>
          <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
            <defs>{gradient("gDown", C.green)}</defs>
            <XAxis dataKey="t" hide />
            <YAxis hide />
            <Tooltip content={<Tip fmt={(v) => fmtRate(v)} labelFmt={secLabel} />} cursor={{ stroke: wash(.2) }} />
            <Area type="monotone" dataKey="up" name="업로드" stroke={C.amber} strokeWidth={1.5} fill="none" isAnimationActive={false} dot={false} />
            <Area type="monotone" dataKey="down" name="다운로드" stroke={C.green} strokeWidth={1.5} fill="url(#gDown)" isAnimationActive={false} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}

/** 원격 PC 연결 품질: SSH 왕복 시간(평균 면 + 최고 점선, 제곱근 눈금) + 끊긴 비율(빨간 막대) */
export function NetChart({ points, stepSec }: { points: NetPoint[]; stepSec: number }) {
  const data = points.map((p) => ({ t: p.ts, rtt: p.rtt, max: p.rtt_max, loss: p.loss ? p.loss : null }));
  const label = (l: unknown) => {
    const d = new Date(Number(l) * 1000);
    return stepSec >= 300 ? d.toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : hm(d);
  };
  const fmt = (v: number, name: string) => (name === "끊김" ? `${v.toFixed(0)}%` : v >= 1000 ? `${(v / 1000).toFixed(1)}초` : `${Math.round(v)}ms`);
  return (
    <ResponsiveContainer>
      <ComposedChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }} barCategoryGap="0%">
        <defs>{gradient("gRtt", C.cyan)}</defs>
        {grid()}
        <XAxis dataKey="t" hide />
        <YAxis yAxisId="ms" scale="sqrt" domain={[0, "auto"]} width={34} tick={{ fill: C.faint, fontSize: 10 }} tickLine={false} axisLine={false}
          tickFormatter={(v) => (v >= 1000 ? `${v / 1000}s` : `${v}`)} />
        <YAxis yAxisId="pct" domain={[0, 100]} hide orientation="right" />
        <Tooltip content={<Tip fmt={fmt} labelFmt={label} />} cursor={{ stroke: wash(.2) }} />
        <Bar yAxisId="pct" dataKey="loss" name="끊김" fill={C.red} fillOpacity={0.55} isAnimationActive={false} />
        <Line yAxisId="ms" type="monotone" dataKey="max" name="최고" stroke={C.faint} strokeDasharray="3 3" strokeWidth={1} dot={false} isAnimationActive={false} />
        <Area yAxisId="ms" type="monotone" dataKey="rtt" name="왕복" stroke={C.cyan} strokeWidth={1.6} fill="url(#gRtt)" dot={false} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

/** 최근 60분 분당 토큰 (막대) + 출력 토큰 (선) */
export function LiveTokens({ minutes, outs, stops = TOKEN_STOPS, outColor = C.cyan }: { minutes: number[]; outs: number[]; stops?: Stops; outColor?: string }) {
  const n = minutes.length;
  const data = minutes.map((v, i) => ({ m: i - (n - 1), total: v, out: outs[i] ?? 0 }));
  const max = Math.max(1, ...minutes);
  const mLabel = (l: unknown) => (Number(l) === 0 ? "이번 분" : `${-Number(l)}분 전`);
  return (
    <div className="live-chart" style={{ height: "6em" }}>
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }} barCategoryGap="18%">
          <defs>
            <linearGradient id="gLive" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={C.amber} /><stop offset="1" stopColor={C.amber} stopOpacity={0.35} />
            </linearGradient>
          </defs>
          {grid()}
          <XAxis dataKey="m" tick={{ fill: C.faint, fontSize: 10 }} tickLine={false} axisLine={false}
            ticks={[-59, -45, -30, -15, 0]} tickFormatter={(v) => (v === 0 ? "지금" : `${-v}분`)} />
          <YAxis yAxisId="t" hide />
          <YAxis yAxisId="o" hide orientation="right" />
          <Tooltip content={<Tip fmt={(v) => fmtT(v)} labelFmt={mLabel} />} cursor={{ fill: wash(.04) }} />
          <Bar yAxisId="t" dataKey="total" name="전체 토큰" radius={[2, 2, 0, 0]} isAnimationActive={false}>
            {data.map((d, i) => <Cell key={i} fill={scaleColor(stops, d.total / max)}
              stroke={d.m === 0 ? C.text : undefined} strokeWidth={d.m === 0 ? 1.5 : 0} />)}
          </Bar>
          <Line yAxisId="o" type="monotone" dataKey="out" name="출력 토큰" stroke={outColor} strokeWidth={1.5} dot={false} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/** 최근 7일 일별 토큰 */
export function WeekBars({ days, stops = TOKEN_STOPS }: { days: { date: string; total: number }[]; stops?: Stops }) {
  const data = days.map((d, i) => {
    const x = new Date(d.date + "T00:00");
    return { label: `${x.getMonth() + 1}/${x.getDate()}`, total: d.total, today: i === days.length - 1 };
  });
  const max = Math.max(1, ...days.map((d) => d.total));
  return (
    <div style={{ height: "6.2em" }}>
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: 16, right: 0, bottom: 0, left: 0 }} barCategoryGap="22%">
          <XAxis dataKey="label" tick={{ fill: C.dim, fontSize: 10 }} tickLine={false} axisLine={false} interval={0} />
          <YAxis hide />
          <Tooltip content={<Tip fmt={(v) => fmtT(v) + " tokens"} labelFmt={(l) => String(l)} />} cursor={{ fill: wash(.04) }} />
          <Bar dataKey="total" name="토큰" radius={[5, 5, 0, 0]} isAnimationActive={false}>
            {data.map((d, i) => <Cell key={i} fill={scaleColor(stops, d.total / max)}
              stroke={d.today ? C.text : undefined} strokeWidth={d.today ? 1.5 : 0} />)}
            <LabelList dataKey="total" position="top" formatter={(v: number) => (v ? fmtT(v) : "")} style={{ fill: C.dim, fontSize: 10 }} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** 5시간 한도 사용률 추이 (최근 6시간, 실제 시각 축) */
export function FiveHourTrend({ history, now }: { history: [number, number | null, number | null][]; now: number }) {
  const t0 = now - 6 * 3600;
  const pts = history.filter((h) => h[0] >= t0 - 1800 && h[1] != null).map((h) => ({ t: Math.max(h[0], t0), v: h[1] as number }));
  if (pts.length) pts.push({ t: now, v: pts[pts.length - 1].v });
  // 사용률이 낮을 때도 변화가 보이도록 세로 범위를 값에 맞춰 키움 (최소 30%)
  const top = Math.min(100, Math.max(30, Math.ceil((Math.max(0, ...pts.map((p) => p.v)) * 1.4) / 10) * 10));
  if (pts.length < 2) return <div className="muted" style={{ height: "4.2em", display: "grid", placeItems: "center" }}>최근 6시간 기록 없음</div>;
  return (
    <div style={{ height: "4.2em" }}>
      <ResponsiveContainer>
        <AreaChart data={pts} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
          <defs>{gradient("gFh", C.amber, 0.4)}</defs>
          {grid()}
          <XAxis dataKey="t" type="number" domain={[t0, now]} tick={{ fill: C.faint, fontSize: 10 }} tickLine={false} axisLine={false}
            ticks={[t0, t0 + 2 * 3600, t0 + 4 * 3600, now]} tickFormatter={(v) => (v >= now - 60 ? "지금" : hm(new Date(v * 1000)))} />
          <YAxis domain={[0, top]} hide />
          {top >= 100 && <ReferenceLine y={100} stroke={C.red} strokeDasharray="3 3" strokeOpacity={0.5} />}
          {[25, 50, 75].filter((y) => y < top).map((y) => (
            <ReferenceLine key={y} y={y} stroke={wash(.08)} label={{ value: `${y}%`, position: "insideLeft", fill: C.faint, fontSize: 9 }} />
          ))}
          <Tooltip content={<Tip fmt={(v) => `${v}% 사용`} labelFmt={(l) => hm(new Date(Number(l) * 1000))} />} cursor={{ stroke: wash(.2) }} />
          <Area type="stepAfter" dataKey="v" name="5시간 한도" stroke={C.amber} strokeWidth={1.8} fill="url(#gFh)" isAnimationActive={false} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** DB 기록의 기간 합계 (일/시간 단위 막대). hourly 면 시간 축, 아니면 날짜 축. 빈 날은 0으로 채움 */
export function SeriesBars({ series, days, hourly = false, stops = TOKEN_STOPS, height = "6.2em" }:
  { series: SeriesPoint[]; days: number; hourly?: boolean; stops?: Stops; height?: string }) {
  const by = new Map(series.map((p) => [p.ts, p.total]));
  const data: { label: string; total: number; cur: boolean }[] = [];
  const now = new Date();
  if (hourly) {
    const h0 = Math.floor(Date.now() / 3600000) * 3600;
    for (let i = 23; i >= 0; i--) {
      const t = h0 - i * 3600;
      data.push({ label: `${new Date(t * 1000).getHours()}시`, total: by.get(t) || 0, cur: i === 0 });
    }
  } else {
    const d0 = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    for (let i = days - 1; i >= 0; i--) {
      const d = new Date(d0); d.setDate(d0.getDate() - i);
      data.push({ label: `${d.getMonth() + 1}/${d.getDate()}`, total: by.get(Math.floor(d.getTime() / 1000)) || 0, cur: i === 0 });
    }
  }
  const max = Math.max(1, ...data.map((d) => d.total));
  const showLabels = data.length <= 10;
  return (
    <div style={{ height }}>
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: showLabels ? 16 : 4, right: 0, bottom: 0, left: 0 }} barCategoryGap="18%">
          <XAxis dataKey="label" tick={{ fill: C.dim, fontSize: 10 }} tickLine={false} axisLine={false}
            interval={data.length <= 10 ? 0 : Math.ceil(data.length / 8) - 1} />
          <YAxis hide />
          <Tooltip content={<Tip fmt={(v) => fmtT(v) + " tokens"} labelFmt={(l) => String(l)} />} cursor={{ fill: wash(.04) }} />
          <Bar dataKey="total" name="토큰" radius={[4, 4, 0, 0]} isAnimationActive={false}>
            {data.map((d, i) => <Cell key={i} fill={scaleColor(stops, d.total / max)}
              stroke={d.cur ? C.text : undefined} strokeWidth={d.cur ? 1.5 : 0} />)}
            {showLabels && <LabelList dataKey="total" position="top" formatter={(v: number) => (v ? fmtT(v) : "")} style={{ fill: C.dim, fontSize: 10 }} />}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** 세션 분 단위 토큰 추이 (DB 기록) */
export function SessionMinutes({ points, stops = TOKEN_STOPS }: { points: { ts: number; total: number; out: number }[]; stops?: Stops }) {
  const max = Math.max(1, ...points.map((p) => p.total));
  return (
    <div style={{ height: "9em" }}>
      <ResponsiveContainer>
        <ComposedChart data={points} margin={{ top: 4, right: 0, bottom: 0, left: 0 }} barCategoryGap="10%">
          {grid()}
          <XAxis dataKey="ts" type="category" tick={{ fill: C.faint, fontSize: 10 }} tickLine={false} axisLine={false}
            tickFormatter={(v) => hm(new Date(Number(v) * 1000))} minTickGap={30} />
          <YAxis yAxisId="t" hide />
          <YAxis yAxisId="o" hide orientation="right" />
          <Tooltip content={<Tip fmt={(v) => fmtT(v)} labelFmt={(l) => hm(new Date(Number(l) * 1000))} />} cursor={{ fill: wash(.04) }} />
          <Bar yAxisId="t" dataKey="total" name="전체 토큰" isAnimationActive={false}>
            {points.map((p, i) => <Cell key={i} fill={scaleColor(stops, p.total / max)} />)}
          </Bar>
          <Line yAxisId="o" type="monotone" dataKey="out" name="출력 토큰" stroke={C.cyan} strokeWidth={1.4} dot={false} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/** 오늘 흐름: step 초 단위 막대 + 지금 시각 세로선(시간이 지나면 오른쪽으로 이동).
 *  가로축은 '오늘 처음 쓴 시각(또는 지금 2시간 전) ~ 24시'만 보여 줘 막대가 넓게 보이게 함 */
export function DayTimeline({ buckets, start, nowTs, stops = TOKEN_STOPS, outColor = C.cyan, height = "9.5em" }: {
  buckets: { x: number; total: number; out: number }[]; start: number; nowTs: number; stops?: Stops; outColor?: string; height?: string;
}) {
  const nowX = Math.max(0, Math.min(86400, nowTs - start));
  const first = buckets.find((b) => b.total > 0);
  const fromH = Math.max(0, Math.min(first ? Math.floor(first.x / 3600) : 24, Math.floor(nowX / 3600) - 2));
  const from = fromH * 3600;
  const data = buckets.filter((b) => b.x >= from).map((b) => ({ ...b, total: b.x <= nowX ? b.total : null, out: b.x <= nowX ? b.out : null }));
  const max = Math.max(1, ...buckets.map((b) => b.total));
  const hhmm = (x: number) => hm(new Date((start + x) * 1000));
  const span = 24 - fromH;
  const every = span > 16 ? 3 : span > 8 ? 2 : 1;
  const ticks: number[] = [];
  for (let h = fromH; h <= 24; h += every) ticks.push(h * 3600);
  return (
    <div className="live-chart day-chart" style={{ height }}>
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 18, right: 8, bottom: 0, left: 0 }} barCategoryGap="12%">
          {grid()}
          <XAxis dataKey="x" type="number" domain={[from, 86400]} ticks={ticks}
            tickFormatter={(v) => `${Math.round(v / 3600)}시`} tick={{ fill: C.dim, fontSize: 12 }} tickLine={false} axisLine={false} />
          <YAxis yAxisId="t" hide domain={[0, "dataMax"]} />
          <YAxis yAxisId="o" hide orientation="right" domain={[0, "dataMax"]} />
          <Tooltip content={<Tip fmt={(v) => fmtT(v)} labelFmt={(l) => `${hhmm(Number(l))} ~ ${hhmm(Number(l) + (buckets[1]?.x || 600))}`} />}
            cursor={{ fill: wash(.05) }} />
          <Bar yAxisId="t" dataKey="total" name="전체 토큰" isAnimationActive={false} radius={[2, 2, 0, 0]}>
            {data.map((d, i) => <Cell key={i} fill={scaleColor(stops, (d.total || 0) / max)} />)}
          </Bar>
          <Line yAxisId="o" type="monotone" dataKey="out" name="출력 토큰" stroke={outColor} strokeWidth={1.6} dot={false}
            isAnimationActive={false} connectNulls={false} />
          <ReferenceLine yAxisId="t" x={nowX} stroke={C.text} strokeWidth={2} strokeDasharray="4 3"
            label={{ value: `지금 ${hhmm(nowX)}`, position: "top", fill: C.text, fontSize: 12, fontWeight: 700 }} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/** 깃허브 잔디: 열 = 주(최근 weeks주), 행 = 요일(월~일), 색 = 그날 토큰 */
export function Grass({ series, weeks = 13, stops = TOKEN_STOPS, cell = "0.85em" }: {
  series: SeriesPoint[]; weeks?: number; stops?: Stops; cell?: string;
}) {
  const by = new Map(series.map((p) => [p.ts, p.total]));
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const dow = (today.getDay() + 6) % 7;            // 월=0 … 일=6
  const first = new Date(today); first.setDate(today.getDate() - dow - (weeks - 1) * 7);
  const cells: { d: Date; v: number | null }[] = [];
  for (let i = 0; i < weeks * 7; i++) {
    const d = new Date(first); d.setDate(first.getDate() + i);
    cells.push({ d, v: d > today ? null : by.get(Math.floor(d.getTime() / 1000)) || 0 });
  }
  const max = Math.max(1, ...cells.map((c) => c.v || 0));
  const total = cells.reduce((a, c) => a + (c.v || 0), 0);
  const active = cells.filter((c) => (c.v || 0) > 0).length;
  return (
    <div className="grass" style={{ ["--cell" as string]: cell }}>
      <div className="grass-days"><span>월</span><span /><span>수</span><span /><span>금</span><span /><span>일</span></div>
      <div className="grass-grid" style={{ gridTemplateColumns: `repeat(${weeks}, var(--cell))` }}>
        {cells.map((c, i) => (
          <i key={i} className={c.v == null ? "future" : c.v ? "" : "zero"}
            style={c.v ? { background: scaleColor(stops, 0.15 + 0.85 * (c.v / max)) } : undefined}
            title={`${c.d.getMonth() + 1}/${c.d.getDate()} (${"월화수목금토일"[(c.d.getDay() + 6) % 7]}) · ${c.v == null ? "-" : fmtT(c.v) + " tokens"}`} />
        ))}
      </div>
      <div className="grass-foot muted num">최근 {weeks}주 · 사용한 날 {active}일 · 합계 {fmtT(total)}</div>
    </div>
  );
}
