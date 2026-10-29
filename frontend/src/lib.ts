import { useEffect, useState } from "react";
import type { State } from "./types";

// 테마별 색 (차트처럼 CSS 변수를 못 쓰는 곳용). applyTheme()이 C 값을 바꾸고 <html data-theme>를 맞춤.
const PALETTE = {
  dark: {
    blue: "#6aa5ff", green: "#43d39e", amber: "#f7b955", red: "#ff6b6b", violet: "#a991ff", cyan: "#45cde0",
    panel2: "#1b2030", dim: "#8d94a6", faint: "#4f5668", text: "#eceef3", wash: "255,255,255",
  },
  light: {
    blue: "#3b7ff0", green: "#12a473", amber: "#d48a0c", red: "#e5484d", violet: "#7c5cf0", cyan: "#0e9fb3",
    panel2: "#dce1e8", dim: "#5f6879", faint: "#949cab", text: "#2b3240", wash: "20,30,50",
  },
};
export type Theme = keyof typeof PALETTE;
export const C = { ...PALETTE.dark };
/** 흰색(다크) / 남색(라이트) 반투명 — 격자선·빈 칸·호버 */
export const wash = (a: number) => `rgba(${C.wash},${a})`;
let curTheme: Theme | null = null;
export function applyTheme(t: Theme) {
  if (t === curTheme) return;
  // 처음 그릴 때는 바로, 이후(낮↔밤 전환)에는 천천히 바뀌게
  if (curTheme) document.documentElement.classList.add("theme-anim");
  curTheme = t;
  Object.assign(C, PALETTE[t]);
  // 어두운 색에서 시작하는 척도(잔디·막대 등)는 라이트 모드에서 시작 색을 밝게: 다음 단계 색을 흰색에 섞음
  for (const [stops, dark] of DARK_START) {
    const next = stops[1][1];
    stops[0] = [stops[0][0], t === "light" ? next.map((x) => Math.round(x + (255 - x) * 0.8)) as [number, number, number] : dark];
  }
  document.documentElement.dataset.theme = t;
}
const DARK_START: [Stops, [number, number, number]][] = [];
/** 라이트 모드에서 시작 색을 밝게 바꿀 척도 등록 (lib 아래쪽 정의 뒤에 호출) */
const darkStart = (...ss: Stops[]) => ss.forEach((s) => DARK_START.push([s, s[0][1]]));
export interface ThemeCfg { mode?: string; light_from?: string; dark_from?: string; lat?: number | null; lon?: number | null }

// 위치를 정하지 않았을 때: PC 시간대의 대표 도시 좌표 (없으면 UTC 시차로 경도만 어림)
const TZ_COORDS: Record<string, [number, number]> = {
  "Asia/Seoul": [37.57, 126.98], "Asia/Tokyo": [35.68, 139.69], "Asia/Shanghai": [31.23, 121.47], "Asia/Taipei": [25.03, 121.57],
  "Asia/Hong_Kong": [22.32, 114.17], "Asia/Singapore": [1.35, 103.82], "Asia/Kolkata": [28.61, 77.21], "Europe/London": [51.51, -0.13],
  "Europe/Paris": [48.86, 2.35], "Europe/Berlin": [52.52, 13.4], "America/New_York": [40.71, -74.01], "America/Chicago": [41.88, -87.63],
  "America/Denver": [39.74, -104.99], "America/Los_Angeles": [34.05, -118.24], "Australia/Sydney": [-33.87, 151.21],
};
export function themeCoords(cfg?: ThemeCfg): { lat: number; lon: number; guessed: boolean } {
  if (cfg?.lat != null && cfg?.lon != null) return { lat: cfg.lat, lon: cfg.lon, guessed: false };
  let tz = "";
  try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { /* 알 수 없음 */ }
  const c = TZ_COORDS[tz];
  return c ? { lat: c[0], lon: c[1], guessed: true } : { lat: 37, lon: -new Date().getTimezoneOffset() / 4, guessed: true };
}

/** 그날의 일출·일몰 시각 (천문 계산, 지평선 보정 90.833°). 백야·극야면 null */
export function sunTimes(day: Date, lat: number, lon: number): { rise: Date | null; set: Date | null; polar?: "day" | "night" } {
  const rad = Math.PI / 180, deg = 180 / Math.PI;
  const y = day.getFullYear(), m = day.getMonth(), d = day.getDate();
  const N = Math.floor((Date.UTC(y, m, d) - Date.UTC(y, 0, 0)) / 86400000);
  const lngHour = lon / 15;
  const calc = (rising: boolean): Date | "day" | "night" => {
    const t = N + ((rising ? 6 : 18) - lngHour) / 24;
    const M = 0.9856 * t - 3.289;
    let L = M + 1.916 * Math.sin(M * rad) + 0.020 * Math.sin(2 * M * rad) + 282.634;
    L = ((L % 360) + 360) % 360;
    let RA = deg * Math.atan(0.91764 * Math.tan(L * rad));
    RA = ((RA % 360) + 360) % 360;
    RA = (RA + (Math.floor(L / 90) * 90 - Math.floor(RA / 90) * 90)) / 15;
    const sinDec = 0.39782 * Math.sin(L * rad), cosDec = Math.cos(Math.asin(sinDec));
    const cosH = (Math.cos(90.833 * rad) - sinDec * Math.sin(lat * rad)) / (cosDec * Math.cos(lat * rad));
    if (cosH > 1) return "night";
    if (cosH < -1) return "day";
    const H = (rising ? 360 - deg * Math.acos(cosH) : deg * Math.acos(cosH)) / 15;
    const T = H + RA - 0.06571 * t - 6.622;
    const UT = (((T - lngHour) % 24) + 24) % 24;
    // 현지 날짜 기준으로 UT 시각을 붙이고, 하루 차이가 나면 맞춤
    let ms = Date.UTC(y, m, d) + UT * 3600000;
    const local = new Date(ms);
    if (local.getDate() !== d) ms += (local < day ? 1 : -1) * 86400000;
    return new Date(ms);
  };
  const r = calc(true), s = calc(false);
  if (typeof r === "string" || typeof s === "string") return { rise: null, set: null, polar: (typeof r === "string" ? r : s) as "day" | "night" };
  return { rise: r, set: s };
}

/** 설정(sun|auto|dark|light)과 지금 시각으로 실제 테마. sun = 일출~일몰 라이트, auto = light_from~dark_from 라이트 */
export function themeFor(cfg: ThemeCfg | undefined, now: Date): Theme {
  const mode = cfg?.mode || "sun";
  if (mode === "dark" || mode === "light") return mode;
  if (mode === "sun") {
    const { lat, lon } = themeCoords(cfg);
    const st = sunTimes(now, lat, lon);
    if (st.polar) return st.polar === "day" ? "light" : "dark";
    return now >= st.rise! && now < st.set! ? "light" : "dark";
  }
  const mins = (s: string | undefined, d: number) => { const m = /^(\d{1,2}):(\d{2})$/.exec(s || ""); return m ? +m[1] * 60 + +m[2] : d; };
  const a = mins(cfg?.light_from, 7 * 60), b = mins(cfg?.dark_from, 19 * 60), x = now.getHours() * 60 + now.getMinutes();
  return (a <= b ? x >= a && x < b : x >= a || x < b) ? "light" : "dark";
}

export const fmtB = (b?: number | null) => {
  if (b == null) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return (b >= 100 || i === 0 ? b.toFixed(0) : b.toFixed(1)) + " " + u[i];
};
export const fmtRate = (b?: number | null) => fmtB(b) + "/s";
export const fmtT = (n?: number | null) => {
  if (n == null) return "–";
  if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e8 ? 0 : 1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e5 ? 0 : 1) + "K";
  return String(Math.round(n));
};
export const fmtDur = (s?: number | null) => {
  if (s == null) return "";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}일 ${h}시간` : h ? `${h}시간 ${m}분` : `${m}분`;
};
export const ago = (s?: number | null) => (s == null ? "" : s < 60 ? "방금" : fmtDur(s) + " 전");
export const hm = (d: Date) => d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
export const level = (p?: number | null) => (p == null ? null : p >= 90 ? C.red : p >= 70 ? C.amber : null);
export const shortCpu = (n?: string) =>
  (n || "").replace(/\(R\)|\(TM\)|CPU|Processor|\d+th Gen|@.*$/gi, "").replace(/\s+/g, " ").trim();

/** 다른 기기에서 열었을 때 주소의 ?t= 토큰을 API 요청에 붙임 */
// 주소의 ?t= 를 기억해 둠 → 홈 화면에 추가한 앱(주소에 토큰 없이 열림)에서도 계속 접속
const TOKEN = (() => {
  const t = new URLSearchParams(location.search).get("t");
  try {
    if (t) localStorage.setItem("rbd-token", t);
    return t || localStorage.getItem("rbd-token");
  } catch { return t; }
})();
/** 다른 기기 접속 토큰 (보안 주소 링크를 만들 때) */
export const authToken = () => TOKEN;
export const apiUrl = (path: string) =>
  path + (TOKEN ? (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(TOKEN) : "");

/** JSON API 호출. 실패하면 서버의 detail 메시지로 오류를 던짐 */
export async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const r = await fetch(apiUrl(path), {
    method, cache: "no-store",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((j as { detail?: string }).detail || `HTTP ${r.status}`);
  return j as T;
}

/** path 를 주기적으로 가져옴 (path 가 null 이면 쉼). 화면이 바뀌면 바로 다시 가져옴 */
export function usePoll<T>(path: string | null, intervalMs = 60000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setData(null);
    if (!path) return;
    let alive = true;
    const tick = () => api<T>(path).then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e) => { if (alive) setError(String(e.message || e)); });
    tick();
    const id = setInterval(tick, intervalMs);
    return () => { alive = false; clearInterval(id); };
  }, [path, intervalMs]);
  return { data, error };
}

/** 2초마다 /api/state 를 가져옴. 실패하면 error 를 채우고 마지막 값은 유지. */
export function useDashboardState(intervalMs = 2000) {
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const url = apiUrl("/api/state");
    let alive = true;
    const tick = async () => {
      try {
        const r = await fetch(url, { cache: "no-store" });
        if (r.status === 403) throw new Error("토큰 없음");   // 다른 기기인데 접속 토큰이 없거나 틀림
        if (!r.ok) throw new Error(String(r.status));
        const s = (await r.json()) as State;
        if (alive) { setState(s); setError(null); }
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    tick();
    const id = setInterval(tick, intervalMs);
    return () => { alive = false; clearInterval(id); };
  }, [intervalMs]);
  return { state, error };
}

export function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const id = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(id); }, []);
  return now;
}

export const store = {
  get<T>(k: string, d: T): T { try { const v = localStorage.getItem(k); return v == null ? d : (JSON.parse(v) as T); } catch { return d; } },
  set(k: string, v: unknown) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* 저장 불가 환경 */ } },
};

/* ---------- 그라데이션 색 척도 ----------
   stops: [위치(0~1), [r,g,b]] 목록. 값(0~1)에 맞는 중간색을 계산하고, 같은 색으로 CSS 그라데이션도 만든다. */
export type Stops = [number, [number, number, number]][];
export function scaleColor(stops: Stops, t: number) {
  const v = Math.max(0, Math.min(1, t));
  for (let i = 1; i < stops.length; i++) {
    const [p1, c1] = stops[i];
    if (v <= p1) {
      const [p0, c0] = stops[i - 1];
      const k = (v - p0) / (p1 - p0 || 1);
      const c = c0.map((x, j) => Math.round(x + (c1[j] - x) * k));
      return `rgb(${c[0]},${c[1]},${c[2]})`;
    }
  }
  const last = stops[stops.length - 1][1];
  return `rgb(${last.join(",")})`;
}
export const scaleCss = (stops: Stops) =>
  `linear-gradient(90deg, ${stops.map(([p, c]) => `rgb(${c.join(",")}) ${p * 100}%`).join(", ")})`;

// 모든 그라데이션의 공통 끝 구간: 같은 주황 → 같은 빨강 (시작 색만 항목마다 다름)
export const ORANGE: [number, number, number] = [247, 150, 64];
export const RED: [number, number, number] = [255, 82, 82];
/** 시작 색 구간(start)에 공통 끝 구간(주황 → 빨강)을 붙인 척도. orangeAt: 주황이 시작되는 위치 */
export const withHotTail = (start: Stops, orangeAt = 0.7): Stops => [...start, [orangeAt, ORANGE], [1, RED]];
const rgb = (hex: string): [number, number, number] => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number];

// Claude 토큰 강도: 어두운 보라 → 보라 → (주황 → 빨강)
export const TOKEN_STOPS: Stops = withHotTail([[0, [56, 44, 98]], [0.4, [124, 98, 238]]]);

/** 공급자별 색: 시작 색만 다르고 끝은 공통 주황 → 빨강.
 *  (Claude는 설계안의 앰버 대신 기존 보라 유지: 앰버로 시작하면 공통 주황 끝 구간과 구분이 안 됨) */
export const PROVIDER_COLOR: Record<string, { accent: string; stops: Stops }> = {
  claude: { get accent() { return C.violet; }, stops: TOKEN_STOPS },
  codex: { get accent() { return C.cyan; }, stops: withHotTail([[0, [22, 62, 70]], [0.4, [69, 205, 224]]]) },
  gemini: { get accent() { return C.blue; }, stops: withHotTail([[0, [32, 46, 96]], [0.4, [106, 140, 255]]]) },
};
export const providerColor = (pid: string) => PROVIDER_COLOR[pid] || PROVIDER_COLOR.claude;
// 사용률 위험도(플랜 사용량 바·잔량 원): 초록 → 노랑 → (주황 → 빨강)
export const USAGE_STOPS: Stops = withHotTail([[0, [67, 211, 158]], [0.5, [232, 214, 92]]], 0.75);
// CPU 잔디: 남색 → 파랑 → (주황 → 빨강), 위치는 0~1 (=0~100%), 90% 이상은 빨강
export const CPU_STOPS: Stops = [[0, [29, 40, 64]], [0.3, [53, 97, 168]], [0.65, ORANGE], [0.9, RED], [1, RED]];
darkStart(CPU_STOPS, TOKEN_STOPS, PROVIDER_COLOR.codex.stops, PROVIDER_COLOR.gemini.stops);
/** 원 게이지: 자기 색(base)으로 시작해 50%부터 같은 주황 → 90%에 빨강 */
export const gaugeStops = (baseHex: string): Stops => [[0, rgb(baseHex)], [0.5, rgb(baseHex)], [0.75, ORANGE], [0.9, RED], [1, RED]];
// 시간 경과 기준선: 차분한 회색 계열
export const TIME_STOPS: Stops = [[0, [70, 78, 96]], [1, [128, 136, 158]]];
