// /api/state 응답 타입 (백엔드 collectors/* 의 출력과 대응)

export interface Proc { name: string; cpu?: number; mem?: number }
export interface Container { name: string; image: string; state: string; status: string; cpu: number; mem: number }

export interface LocalState {
  host: string;
  spec: { cpu_name?: string; os_name?: string; cores?: number; threads?: number };
  cpu: number;
  cpu_per: number[];
  cpu_freq: number | null;
  mem: { total: number; used: number; pct: number };
  net: { down: number; up: number };
  disks: { name: string; total: number; used: number }[];
  battery: { pct: number; plugged: boolean } | null;
  uptime: number;
  top_cpu: Proc[];
  top_mem: Proc[];
  hist: { cpu: number[]; mem: number[]; down: number[]; up: number[] };
  proc_count?: number;
  hist_cores?: number[][];
  swap?: { used: number; total: number };
  net_total?: { down: number; up: number };
}

export interface LocalExtras {
  gpus: { name: string; util: number | null; shared_used: number | null }[];
  containers: Container[];
  docker_error: string | null;
}

export interface WinProbe {
  host?: string; cpu?: number; cpu_per?: number[]; mem_total?: number; mem_free?: number; uptime_sec?: number;
  disks?: { name: string; size: number; free: number }[];
  top_cpu?: Proc[]; top_mem?: Proc[]; net_down?: number; net_up?: number;
  gpus?: { name: string }[]; gpu_util?: number;
  cpu_name?: string; cores?: number; threads?: number; os_name?: string;
  proc_count?: number; pagefile_used?: number; pagefile_total?: number; net_total_down?: number; net_total_up?: number;
}

export interface RemoteState {
  ok: boolean; host: string | null; latency_ms: number | null; last_ok: number | null; error: string | null;
  fallback?: boolean;   // 첫 번째가 아닌 대체 SSH 주소로 연결됨
  containers?: Container[]; docker_error?: string | null;
  gpus?: { name: string; util: number | null; mem_used: number | null; mem_total: number | null; temp: number | null; power: number | null }[];
  mem?: { total: number; used: number } | null;
  disk?: { total: number; used: number } | null;
  load?: { l1: number; l5: number; cpus: number } | null;
  windows?: WinProbe | null;
  hist?: { cpu: number[]; mem: number[]; down: number[]; up: number[] };
  hist_cores?: number[][];
}

export interface Usage {
  today: { in: number; out: number; cw: number; cr: number; total: number; count: number; cost: number };
  days: { date: string; total: number }[];
  sessions_today: number;
  minutes?: number[];
  minutes_out?: number[];
  last_activity: string | null;
  last_model: string | null;
}

export interface SubAgent {
  agent: string; description?: string | null; type?: string | null; model?: string | null;
  tokens: number; count: number; first?: number; last?: number;
}
export interface Session {
  pid: number; session: string; project: string; state: "working" | "idle" | "waiting"; waiting_for?: string | null; state_since?: number | null;
  last_activity: number | null; model: string | null; context: number | null; title: string | null;
  tokens: number | null; name: string | null; ctx_pct: number | null; model_name: string | null; effort: string | null;
  slug?: string | null; agents?: SubAgent[];
}

export interface LimitWindow {
  used_pct: number | null; resets_at: number | null; reset_in_sec: number | null; reset_passed: boolean; elapsed_pct: number | null;
  estimated?: boolean;   // 리셋 시각을 두 PC 로그로 추정한 경우
}
export interface Limits {
  source: "desktop" | "statusline" | "api"; captured_at: number; age_sec: number; machine: "local" | "remote" | "api";
  five_hour?: LimitWindow; seven_day?: LimitWindow;
  history: [number, number | null, number | null][];
}

export interface PlanApiStatus { error: string | null; login_required: boolean; retry_in_sec: number; last_ok: number | null; manual_fails?: number }

// ---------- v2 ----------
export interface SysPoint { ts: number; cpu: number | null; cpu_max: number | null; mem: number | null; swap: number | null; gpu: number | null; down: number | null; up: number | null }
export interface SysHistoryResp { machine: string; range: string; step: number; points: SysPoint[]; has_data: boolean }
export interface NetPoint { ts: number; rtt: number | null; rtt_max: number | null; tcp: number | null; loss: number | null }
export interface NetResp {
  machine: string; range: string; step: number; points: NetPoint[]; has_data: boolean;
  now: { ok: boolean; tcp: number | null; rtt: number | null };
  summary: { rtt: number | null; rtt_max: number | null; uptime: number | null; down_min: number };
  outages: { start: number; end: number }[];
}
export interface MachineRow { id: string; name: string; kind: "local" | "ssh"; online: boolean }

export interface LimitWin {
  key: string; slot?: string; window_minutes?: number; used_pct: number | null; resets_at: number | null;
  reset_passed?: boolean; estimated?: boolean; reset_in_sec: number | null; elapsed_pct: number | null;
  requests?: number; limit?: number;
}
export interface ProviderLimits {
  source: string; captured_at: number | null; windows: LimitWin[]; machine?: string; account?: string | null;
  history?: [number, number | null, number | null][]; plan_api?: PlanApiStatus;
  plan_type?: string | null; credits?: { has_credits?: boolean; unlimited?: boolean; balance?: string } | null;
  by_account?: ProviderLimits[];
  label?: string; keys?: string[];                 // 계정(이름)별 한도일 때
  accounts?: ProviderLimits[];                     // 계정이 2개 이상이면 계정별 한도 (맨 위 값 = 이 PC 기본 계정)
}
export interface ProviderMachineState {
  ok: boolean; error: string | null; updated?: number; usage?: Usage & { models_today?: Record<string, number>; models_week?: Record<string, number> };
  sessions?: Session[]; account?: string | null; absent?: boolean;
}
export interface AiProvider {
  label: string; machines: string[]; states: Record<string, ProviderMachineState>; limits: ProviderLimits | null;
  accounts?: Record<string, string>;   // 키(PC 또는 PC~계정 폴더) -> 계정 이름
}

export interface SeriesPoint { ts: number; in: number; out: number; cr: number; cw: number; total: number; count: number }
export interface ProviderSummary {
  id: string; label: string; range: string; series?: SeriesPoint[]; models_range?: Record<string, number>;
  machines_range?: Record<string, { total: number; count: number }>;
}
export interface SessionRow extends Partial<Session> {
  session: string; machine_id: string; machine: string; live: boolean; started?: number | null;
}
export interface SessionDetail {
  provider: string; session: string; info: (SessionRow & Record<string, unknown>) | null;
  minutes?: { ts: number; in: number; out: number; cr: number; cw: number; n: number }[];
  models?: Record<string, number>;
  totals?: { a: number | null; b: number | null; i: number | null; o: number | null; cr: number | null; cw: number | null; rs: number | null; n: number };
}

export interface State {
  now: number;
  machines?: MachineRow[];
  setup_done?: boolean;
  names?: Record<string, string>;   // PC·계정 폴더 키 -> 표시 이름
  theme?: { mode: "auto" | "dark" | "light"; light_from: string; dark_from: string };
  alerts?: { t: number; kind: string; title: string; message: string }[];
  systems?: Record<string, RemoteState | null>;
  ai?: Record<string, AiProvider>;
  remote_label: string;
  errors: Record<string, string>;
  updated?: Record<string, number>;   // 항목별 마지막 성공 수집 시각 (epoch 초)
  local: LocalState | null;
  local_extras: LocalExtras | null;
  claude: Usage | null;
  live: { limits: Limits | null; sessions: Session[]; plan_api?: PlanApiStatus } | null;
  remote: RemoteState | null;
  remote_claude: ({ ok: boolean; error: string | null; usage?: Usage; sessions?: Session[] }) | null;
}
