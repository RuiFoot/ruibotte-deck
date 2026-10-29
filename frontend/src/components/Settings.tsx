// 설정 화면 (macOS 시스템 설정 방식): 왼쪽 분류 목록 → 오른쪽 페이지. 페이지는 '묶음(제목 + 둥근 목록 + 설명)'으로 구성.
// 분류: 일반 · PC · AI 도구별(Claude Code / GPT / Gemini) · 알림 · 기록 · 정보
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import QRCode from "qrcode";
import { C, ago, api, hm, sunTimes, themeCoords, usePoll } from "../lib";
import { Muted } from "./ui";
import { Overlay } from "./SessionModal";

interface MachineApi {
  id: string; name: string; kind: "local" | "ssh"; ssh_target?: string; ssh_fallbacks?: string[]; wsl_distro?: string | null;
  home_override?: string | null; enabled: boolean; online: boolean;
  status?: { mode?: string; os?: string; home?: string; hostname?: string; error?: string | null; last_ok?: number | null; latency_ms?: number | null; active_providers?: string[] | null } | null;
  system?: { cpu?: number | null; mem?: number | null; error?: string | null };
}
interface Detect {
  ok: boolean; error?: string | null; os?: string; mode?: string; latency_ms?: number; wsl_distro?: string | null; wsl_distros?: string[];
  home?: string; providers?: Record<string, number>; agent?: { hostname?: string; os_name?: string; homes?: { path: string; providers: Record<string, number> }[] } | null;
}
interface AppInfo { platform: string; autostart: boolean; port: number; lan: boolean; name?: string; version?: string; repo?: string; installed?: boolean; data_dir?: string }

const PROVIDER_LABEL: Record<string, string> = { claude: "Claude Code", codex: "GPT (Codex)", gemini: "Gemini" };
const MODE_LABEL: Record<string, string> = { wsl: "WSL Python", python: "Python", powershell: "PowerShell 전용 (AI 사용량 없음)" };

function useBusy() {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const run = async (fn: () => Promise<unknown>, okText?: string) => {
    setBusy(true); setMsg(null);
    try { await fn(); if (okText) setMsg({ ok: true, text: okText }); } catch (e) { setMsg({ ok: false, text: String((e as Error).message || e) }); }
    setBusy(false);
  };
  return { busy, msg, run, setMsg };
}

// ---------- 공통 부품 (묶음 · 줄 · 스위치 · 선택 버튼) ----------
function Group({ title, footer, children }: { title?: ReactNode; footer?: ReactNode; children: ReactNode }) {
  return (
    <div className="g">
      {title && <div className="g-h">{title}</div>}
      <div className="g-list">{children}</div>
      {footer && <div className="g-f">{footer}</div>}
    </div>
  );
}

function Row({ label, sub, children, dot, className }: { label: ReactNode; sub?: ReactNode; children?: ReactNode; dot?: boolean | null; className?: string }) {
  return (
    <div className={`g-row ${className || ""}`}>
      {dot != null && <span className={`dot ${dot ? "on" : ""}`} />}
      <div className="lb"><div className="t">{label}</div>{sub && <div className="s">{sub}</div>}</div>
      {children != null && <div className="ctl">{children}</div>}
    </div>
  );
}

function Switch({ on, onChange, disabled }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return <button type="button" role="switch" aria-checked={on} className={`sw ${on ? "on" : ""}`} disabled={disabled} onClick={() => onChange(!on)} />;
}

function Seg<T extends string>({ value, options, onChange, disabled }: { value: T; options: [T, string][]; onChange: (v: T) => void; disabled?: boolean }) {
  return (
    <div className="ios-seg">
      {options.map(([v, l]) => <button key={v} className={value === v ? "on" : ""} disabled={disabled} onClick={() => onChange(v)}>{l}</button>)}
    </div>
  );
}

const Msg = ({ m }: { m: { ok: boolean; text: string } | null }) =>
  m ? <div className="g-msg" style={{ color: m.ok ? C.green : C.red }}>{m.text}</div> : null;

// ---------- 일반: 테마 · 실행 · 화면 ----------
interface Display { id: string; name: string; device: string; primary: boolean; internal: boolean; x: number; y: number; width: number; height: number }

function ThemeGroup() {
  const [tick, setTick] = useState(0);
  const { data } = usePoll<Record<string, unknown>>(`/api/settings?_=${tick}`, 600000);
  const { busy, msg, run } = useBusy();
  if (!data) return <Muted>불러오는 중…</Muted>;
  const mode = String(data["display.theme"] ?? "sun") as "sun" | "auto" | "light" | "dark";
  const save = (patch: Record<string, unknown>) => run(async () => { await api("/api/settings", "PUT", patch); setTick((t) => t + 1); });
  const pos = themeCoords({ lat: data["display.lat"] as number | null, lon: data["display.lon"] as number | null });
  const today = sunTimes(new Date(), pos.lat, pos.lon);
  const here = () => run(() => new Promise<void>((ok, fail) => {
    if (!navigator.geolocation) return fail(new Error("이 브라우저는 위치를 알려주지 않아요"));
    navigator.geolocation.getCurrentPosition(
      async (p) => { await api("/api/settings", "PUT", { "display.lat": +p.coords.latitude.toFixed(2), "display.lon": +p.coords.longitude.toFixed(2) }); setTick((t) => t + 1); ok(); },
      () => fail(new Error("위치를 가져오지 못했어요 (권한을 허용하거나 시간대 기준을 쓰세요)")), { timeout: 15000 });
  }), "현재 위치로 맞췄어요.");
  return (
    <Group title="테마" footer={<>일출·일몰이면 해 뜰 때 라이트, 해 질 때 다크로 바뀌어요. 아이패드 등 다른 화면에도 같이 적용돼요.<Msg m={msg} /></>}>
      <Row label="모양">
        <Seg value={mode} disabled={busy} onChange={(v) => save({ "display.theme": v })}
          options={[["sun", "일출·일몰"], ["auto", "시간 지정"], ["light", "라이트"], ["dark", "다크"]]} />
      </Row>
      {mode === "sun" && <>
        <Row label="오늘" sub={today.polar ? (today.polar === "day" ? "해가 지지 않는 날" : "해가 뜨지 않는 날") : undefined}>
          {!today.polar && <span className="g-val">일출 {hm(today.rise!)} · 일몰 {hm(today.set!)}</span>}
        </Row>
        <Row label="위치" sub={pos.guessed ? "PC 시간대로 추정한 위치 (좌표는 이 PC에만 저장)" : `위도 ${pos.lat} · 경도 ${pos.lon}`}>
          <button className="g-btn" disabled={busy} onClick={here}>현재 위치 사용</button>
          {!pos.guessed && <button className="g-btn" disabled={busy} onClick={() => save({ "display.lat": null, "display.lon": null })}>시간대 기준</button>}
        </Row>
      </>}
      {mode === "auto" && <>
        <Row label="라이트 시작"><input className="g-in" type="time" defaultValue={String(data["display.light_from"] ?? "07:00")} disabled={busy}
          onBlur={(e) => e.target.value && save({ "display.light_from": e.target.value })} /></Row>
        <Row label="다크 시작"><input className="g-in" type="time" defaultValue={String(data["display.dark_from"] ?? "19:00")} disabled={busy}
          onBlur={(e) => e.target.value && save({ "display.dark_from": e.target.value })} /></Row>
      </>}
    </Group>
  );
}

/** 표시 모니터 · 로그인 시 자동 실행 · 다른 기기 접속 (첫 실행 마법사에서도 씀) */
export function DisplayAndApp() {
  const [tick, setTick] = useState(0);
  const { data: D } = usePoll<{ displays: Display[]; selected: string }>(`/api/displays?_=${tick}`, 600000);
  const { data: A } = usePoll<AppInfo>(`/api/app?_=${tick}`, 600000);
  const { busy, msg, run } = useBusy();
  const pick = (v: string) => run(async () => { await api("/api/settings", "PUT", { "display.monitor": v }); setTick((t) => t + 1); }, "다음에 대시보드를 열 때 적용돼요.");
  const sameIds = (id: string) => (D?.displays || []).filter((d) => d.id === id).length > 1;
  const opts: [string, string][] = [["auto", "자동 (노트북 내장 화면 → 주 모니터)"], ["primary", "주 모니터"],
    ...(D?.displays || []).map((d): [string, string] => [sameIds(d.id) ? d.device : d.id,
      `${d.internal ? "내장 화면" : d.id || d.device} · ${d.width}×${d.height}${d.primary ? " · 주 모니터" : ""}${sameIds(d.id) ? ` (${d.device.replace("\\\\.\\", "")})` : ""}`])];
  return (
    <Group title="실행 · 화면" footer={msg ? <Msg m={msg} /> : undefined}>
      <Row label="로그인할 때 자동 실행" sub={A?.platform === "win32" ? "시작프로그램 · 트레이 메뉴에서도 바꿀 수 있어요" : A?.platform === "darwin" ? "LaunchAgent" : "~/.config/autostart"}>
        <Switch on={!!A?.autostart} disabled={busy || !A} onChange={(v) => run(async () => { await api("/api/app", "PUT", { autostart: v }); setTick((t) => t + 1); })} />
      </Row>
      {A?.platform === "win32" || !A ? (
        <Row label="대시보드를 띄울 화면" sub="트레이의 '대시보드 열기'">
          <select className="g-in" value={D?.selected || "auto"} disabled={busy || !D} onChange={(e) => pick(e.target.value)}>
            {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </Row>
      ) : <Row label="대시보드 화면" sub="이 OS에서는 기본 브라우저/앱 창을 전체 화면으로 열어요" />}
    </Group>
  );
}

interface Share { lan: boolean; active: boolean; needs_restart: boolean; port: number; addresses: { name: string; ip: string; url: string }[] }

/** 다른 기기(아이패드·휴대폰)에서 보기: 스위치 → 다시 시작 → QR (접속 토큰 포함 주소) */
function ShareGroup() {
  const [tick, setTick] = useState(0);
  const { data: S, error } = usePoll<Share>(`/api/app/share?_=${tick}`, 15000);
  const { busy, msg, run } = useBusy();
  const [qr, setQr] = useState<string | null>(null);
  const url = S?.addresses[0]?.url;
  useEffect(() => {
    if (!url || !S?.active) { setQr(null); return; }
    QRCode.toDataURL(url, { margin: 1, width: 360, color: { dark: "#111318", light: "#ffffff" } }).then(setQr).catch(() => setQr(null));
  }, [url, S?.active]);
  if (error && !S) return <Group title="다른 기기에서 보기"><Row label="이 PC 화면에서만 설정할 수 있어요" /></Group>;
  if (!S) return null;
  return (
    <Group title="다른 기기에서 보기" footer={<>QR에는 접속 토큰이 들어 있어요 — 믿을 수 있는 기기로만 찍으세요. 처음 켤 때 Windows 방화벽 창이 뜨면
      '개인 네트워크'를 허용하세요.<Msg m={msg} /></>}>
      <Row label="같은 네트워크의 기기에서 접속" sub={S.active ? `켜짐 · 포트 ${S.port}` : "꺼짐 · 이 PC에서만"}>
        <Switch on={S.lan} disabled={busy} onChange={(v) => run(async () => { await api("/api/app", "PUT", { lan: v }); setTick((t) => t + 1); })} />
      </Row>
      {S.needs_restart && (
        <Row label="다시 시작하면 적용돼요" sub="앱이 잠깐 꺼졌다가 바로 다시 켜져요">
          <button className="g-btn primary" disabled={busy} onClick={() => run(() => api("/api/app/restart", "POST"), "다시 시작하는 중… 잠시 뒤 창이 다시 열려요.")}>지금 다시 시작</button>
        </Row>
      )}
      {S.active && (S.addresses.length ? (
        <Row label={<div className="qr-box">{qr && <img src={qr} alt="접속 QR 코드" />}
          <div className="qr-txt"><div className="t">아이패드·휴대폰 카메라로 찍으면 바로 열려요</div>
            <code className="sel">{url}</code>
            {S.addresses.length > 1 && <div className="s">다른 주소: {S.addresses.slice(1).map((a) => `${a.ip} (${a.name})`).join(", ")}</div>}</div></div>} />
      ) : <Row label="네트워크 주소를 찾지 못했어요" sub="Wi-Fi나 이더넷에 연결돼 있는지 확인하세요" />)}
    </Group>
  );
}

function GeneralPage({ onWizard }: { onWizard?: () => void }) {
  return (
    <>
      <ThemeGroup />
      <DisplayAndApp />
      <ShareGroup />
      {onWizard && <Group><Row label="처음 설정 다시 보기"><button className="g-btn" onClick={onWizard}>열기 ›</button></Row></Group>}
    </>
  );
}

// ---------- PC ----------
function DetectView({ d, onPickHome }: { d: Detect; onPickHome: (h: string) => void }) {
  if (!d.ok) return <Row label={<span style={{ color: C.red }}>연결 실패</span>} sub={d.error} />;
  const provs = Object.keys(d.providers || {});
  const others = (d.agent?.homes || []).filter((h) => Object.keys(h.providers).length && h.path !== d.home);
  return (
    <>
      <Row dot label={<span style={{ color: C.green }}>연결 성공 · {d.agent?.hostname || ""}</span>}
        sub={`${d.os} · ${MODE_LABEL[d.mode || ""] || d.mode}${d.wsl_distro ? ` (${d.wsl_distro})` : ""} · ${d.latency_ms}ms`} />
      <Row label="홈 폴더" sub={`AI 도구: ${provs.length ? provs.map((p) => PROVIDER_LABEL[p] || p).join(", ") : "없음"}`}><code>{d.home || "–"}</code></Row>
      {others.map((h) => (
        <Row key={h.path} label={<code>{h.path}</code>} sub={`다른 홈 폴더 후보 · ${Object.keys(h.providers).join(", ")}`}>
          <button className="g-btn" onClick={() => onPickHome(h.path)}>이 폴더 사용</button>
        </Row>
      ))}
      {d.mode === "powershell" && <Row label={<span style={{ color: C.amber }}>Python이 없어 시스템 지표만 수집해요</span>} />}
    </>
  );
}

/** 원격 PC 추가 (첫 실행 마법사에서도 씀) */
export function AddMachine({ onDone }: { onDone: () => void }) {
  const empty = { name: "", ssh_target: "", ssh_fallbacks: "", wsl_distro: "", home_override: "" };
  const [f, setF] = useState(empty);
  const [det, setDet] = useState<Detect | null>(null);
  const { busy, msg, run } = useBusy();
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const body = () => ({ ...f, name: f.name || f.ssh_target, ssh_fallbacks: f.ssh_fallbacks });
  const field = (k: keyof typeof f, label: string, ph: string) => (
    <Row label={label}><input className="g-in wide" value={f[k]} onChange={set(k)} placeholder={ph} /></Row>
  );
  return (
    <Group title="원격 PC 추가" footer={<>SSH 키 인증만 써요(비밀번호는 묻지도 저장하지도 않음). 원격에는 아무것도 설치하지 않고,
      연결할 때 작은 수집 프로그램을 보내 실행해요 (Python 3 필요 · Windows는 WSL 또는 Python).<Msg m={msg} /></>}>
      {field("ssh_target", "SSH 대상", "~/.ssh/config 별칭, user@host, user@host:port")}
      {field("name", "표시 이름", "예: 회사 PC (비우면 SSH 대상)")}
      {field("ssh_fallbacks", "대체 주소", "쉼표로 구분 (선택)")}
      {field("wsl_distro", "WSL 배포판", "자동 감지 (선택)")}
      {field("home_override", "홈 폴더", "자동 감지 (선택)")}
      <Row label="">
        <button className="g-btn" disabled={busy || !f.ssh_target} onClick={() => run(async () => setDet(await api<Detect>("/api/machines/test", "POST", body())))}>
          {busy && !det ? "연결 테스트 중…" : "연결 테스트"}</button>
        <button className="g-btn primary" disabled={busy || !f.ssh_target} onClick={() => run(async () => {
          await api("/api/machines", "POST", body()); setF(empty); setDet(null); onDone();
        }, "추가했어요. 곧 연결을 시작해요.")}>추가</button>
      </Row>
      {det && <DetectView d={det} onPickHome={(h) => setF({ ...f, home_override: h })} />}
    </Group>
  );
}

function MachineRow({ m, onChange }: { m: MachineApi; onChange: () => void }) {
  const [name, setName] = useState(m.name);
  const [confirm, setConfirm] = useState(false);
  const { busy, msg, run } = useBusy();
  useEffect(() => setName(m.name), [m.name]);
  const st = m.status;
  const now = Date.now() / 1000;
  const sub = m.kind === "local" ? "이 PC" : [
    m.ssh_target + (m.ssh_fallbacks?.length ? ` (+${m.ssh_fallbacks.join(", ")})` : ""),
    st?.mode && (MODE_LABEL[st.mode] || st.mode), st?.home,
    st?.active_providers?.length ? st.active_providers.map((p) => PROVIDER_LABEL[p] || p).join(", ") : null,
    m.online ? (st?.latency_ms != null ? `${st.latency_ms}ms` : "연결됨") : !m.enabled ? "꺼짐"
      : st?.error || m.system?.error || (st?.last_ok ? `끊김 (${ago(now - st.last_ok)} 마지막 수신)` : "연결 중…"),
  ].filter(Boolean).join(" · ");
  return (
    <Row dot={m.enabled ? m.online : null} className={m.enabled ? "" : "off"}
      label={<input className="g-in name" value={name} onChange={(e) => setName(e.target.value)}
        onBlur={() => name && name !== m.name && run(async () => { await api(`/api/machines/${m.id}`, "PUT", { name }); onChange(); })} />}
      sub={<>{sub}{msg && !msg.ok ? <span style={{ color: C.red }}> · {msg.text}</span> : null}</>}>
      {m.kind !== "local" && <>
        {confirm
          ? <button className="g-btn danger" disabled={busy} onClick={() => run(async () => { await api(`/api/machines/${m.id}`, "DELETE"); onChange(); })}>정말 삭제</button>
          : <button className="g-btn" onClick={() => { setConfirm(true); setTimeout(() => setConfirm(false), 4000); }}>삭제</button>}
        <Switch on={m.enabled} disabled={busy} onChange={(v) => run(async () => { await api(`/api/machines/${m.id}`, "PUT", { enabled: v }); onChange(); })} />
      </>}
    </Row>
  );
}

function PcPage() {
  const [tick, setTick] = useState(0);
  const { data, error } = usePoll<{ machines: MachineApi[] }>(`/api/machines?_=${tick}`, 4000);
  const refresh = () => setTick((t) => t + 1);
  return (
    <>
      <Group title="PC" footer="이름을 눌러 바꿀 수 있어요. 스위치를 끄면 그 PC는 수집·표시하지 않아요.">
        {error && !data ? <Row label={<span style={{ color: C.red }}>{error}</span>} />
          : !data ? <Row label="불러오는 중…" /> : data.machines.map((m) => <MachineRow key={m.id} m={m} onChange={refresh} />)}
      </Group>
      <AddMachine onDone={refresh} />
    </>
  );
}

// ---------- AI 도구 (도구마다 한 페이지) ----------
interface ProfileRow { id: string; path: string; label: string | null; enabled: boolean; key: string; account?: string | null }
interface Slot {
  machine_id: string; machine: string; provider: string; label: string;
  default: { id: string | null; label: string; key: string; detected: boolean; account?: string | null };
  extra: ProfileRow[]; found: string[];
}
const CONFIG_ENV: Record<string, string> = { claude: "CLAUDE_CONFIG_DIR", codex: "CODEX_HOME" };
const CLI_NAME: Record<string, string> = { claude: "claude", codex: "codex" };
const PROVIDER_NOTE: Record<string, string> = {
  claude: "한도: 계정 사용량 API(정확) → 데스크톱 앱 기록 → 로그 추정",
  codex: "한도: 로그에 남은 정확한 값 (마지막 요청 시점)",
  gemini: "한도: 공식 값이 없어 오늘 요청 수로 추정",
};

/** CLI 로그인 창 열기. CLI가 없으면 '설치할까요?' → 확인하면 설치 후 같은 창에서 이어서 로그인 */
export async function cliLogin(body: Record<string, unknown>) {
  let r = await api<{ ok: boolean; need_install?: boolean; message: string }>("/api/profiles/login", "POST", body);
  if (r.need_install) {
    if (!window.confirm(r.message)) throw new Error("설치를 취소했어요");
    r = await api("/api/profiles/login", "POST", { ...body, install: true });
  }
  return r;
}

function AiToolPage({ pid }: { pid: string }) {
  const [tick, setTick] = useState(0);
  const { data, error } = usePoll<{ slots: Slot[] }>(`/api/profiles?_=${tick}`, 600000);
  const { data: provs } = usePoll<Record<string, { enabled: boolean; auto?: boolean }>>(`/api/providers/settings?_=${tick}`, 600000);
  const { busy, msg, run } = useBusy();
  const [draft, setDraft] = useState<Record<string, { path: string; label: string }>>({});
  const [newName, setNewName] = useState("");
  const refresh = () => setTick((t) => t + 1);
  const save = (fn: () => Promise<unknown>, ok?: string) => run(async () => { await fn(); refresh(); }, ok);
  if (error && !data) return <div className="err">{error}</div>;
  if (!data || !provs) return <Muted>불러오는 중…</Muted>;
  const pv = provs[pid] || { enabled: false };
  const slots = data.slots.filter((x) => x.provider === pid);
  const canLogin = !!CONFIG_ENV[pid];
  const loginMsg = "창을 열었어요. 브라우저에서 로그인을 마치면 그 계정으로 집계돼요 (한 번 사용한 뒤부터).";
  return (
    <>
      <Group footer={<>{PROVIDER_NOTE[pid]}{pv.auto ? " · 직접 켜고 끈 적이 없어 '최근 14일 안에 쓴 흔적'으로 자동 결정 중이에요." : ""}</>}>
        <Row label={`${PROVIDER_LABEL[pid]} 사용`} sub={pv.enabled ? "PC마다 기록을 찾아 수집해요" : "꺼져 있어요"}>
          <Switch on={pv.enabled} disabled={busy} onChange={(v) => save(() => api(`/api/providers/${pid}`, "PUT", { enabled: v }))} />
        </Row>
      </Group>
      {pv.enabled && <>
        {slots.map((sl) => {
          const k = sl.machine_id, d = draft[k] || { path: "", label: "" };
          const setD = (v: Partial<typeof d>) => setDraft({ ...draft, [k]: { ...d, ...v } });
          const local = sl.machine_id === "local";
          return (
            <Group key={k} title={sl.machine}>
              <Row dot={sl.default.detected} label={sl.default.account || "기본 계정"}
                sub={sl.default.detected ? "기본 폴더 · 사용 중" : "아직 로그인·사용 기록 없음"}>
                {!sl.default.detected && local && canLogin &&
                  <button className="g-btn primary" disabled={busy} onClick={() => save(() => cliLogin({ provider: pid, label: sl.default.label, default: true }), loginMsg)}>로그인</button>}
                <input className="g-in alias" placeholder="별명" defaultValue={sl.default.label} disabled={busy}
                  onBlur={(e) => e.target.value !== sl.default.label && save(() => api("/api/profiles", "POST",
                    { machine_id: sl.machine_id, provider: pid, path: "", label: e.target.value }))} />
              </Row>
              {sl.extra.map((x) => (
                <Row key={x.id} dot={x.enabled} className={x.enabled ? "" : "off"} label={x.account || x.path}
                  sub={<>{x.account ? <code>{x.path}</code> : null}{local && canLogin && <> · 이 계정으로 쓰기 <code className="sel">$env:{CONFIG_ENV[pid]}="{x.path}"; {CLI_NAME[pid]}</code></>}</>}>
                  <input className="g-in alias" placeholder="별명" defaultValue={x.label || ""} disabled={busy}
                    onBlur={(e) => e.target.value && e.target.value !== x.label && save(() => api(`/api/profiles/${x.id}`, "PUT", { label: e.target.value }))} />
                  <button className="g-btn" disabled={busy} onClick={() => save(() => api(`/api/profiles/${x.id}`, "DELETE"))}>삭제</button>
                  <Switch on={x.enabled} disabled={busy} onChange={(v) => save(() => api(`/api/profiles/${x.id}`, "PUT", { enabled: v }))} />
                </Row>
              ))}
              {sl.found.map((f) => (
                <Row key={f} label={<code>{f}</code>} sub="찾은 계정 폴더 · 아직 등록 안 됨">
                  <button className="g-btn" disabled={busy} onClick={() => setD({ path: f })}>등록…</button>
                </Row>
              ))}
              <Row label="계정 폴더 등록" sub={local ? "이미 로그인된 다른 계정의 폴더" : "그 PC에 로그인된 계정 폴더 (/로 시작하는 경로)"}>
                <input className="g-in wide" placeholder={local ? String.raw`C:\Users\me\.claude-personal` : "/mnt/c/Users/me/.claude-personal"}
                  value={d.path} onChange={(e) => setD({ path: e.target.value })} disabled={busy} />
                <input className="g-in alias" placeholder="이름" value={d.label} onChange={(e) => setD({ label: e.target.value })} disabled={busy} />
                <button className="g-btn" disabled={busy || !d.path || !d.label} onClick={() => save(async () => {
                  await api("/api/profiles", "POST", { machine_id: sl.machine_id, provider: pid, path: d.path, label: d.label });
                  setDraft({ ...draft, [k]: { path: "", label: "" } });
                }, "등록했어요. 잠시 뒤 AI 사용량에 계정으로 나와요.")}>추가</button>
              </Row>
            </Group>
          );
        })}
        {canLogin && (
          <Group title="새 계정 추가 (이 PC)" footer={<>그 계정 전용 폴더(~/.{CLI_NAME[pid]}-이름)를 만들고 그 폴더로 로그인 창을 열어요. CLI가 없으면 설치부터 해요.
            토큰은 CLI가 그 폴더에 저장하고, 대시보드는 복사·저장하지 않아요.</>}>
            <Row label="로그인해서 계정 추가">
              <input className="g-in alias" placeholder="계정 이름" value={newName} onChange={(e) => setNewName(e.target.value)} disabled={busy} />
              <button className="g-btn primary" disabled={busy || !newName} onClick={() => save(async () => {
                await cliLogin({ provider: pid, label: newName }); setNewName("");
              }, loginMsg)}>로그인</button>
            </Row>
          </Group>
        )}
        {!slots.length && <Muted>아직 어느 PC에서도 기록을 찾지 못했어요.</Muted>}
        <div className="g-f">같은 이름 = 같은 계정 (PC가 달라도 한도·합계를 같이 봐요). 별명을 비우면 실제 로그인 계정(이메일)으로 보여요.</div>
      </>}
      <Msg m={msg} />
    </>
  );
}

// ---------- 알림 ----------
function NotifyPage() {
  const [tick, setTick] = useState(0);
  const { data } = usePoll<{ kinds: Record<string, string>; enabled: Record<string, boolean> }>(`/api/notify?_=${tick}`, 600000);
  const { busy, msg, run } = useBusy();
  if (!data) return <Muted>불러오는 중…</Muted>;
  return (
    <>
      <Group title="알림 종류" footer="Windows 알림(트레이 아이콘)으로 보내요. 지난 알림은 화면 위쪽 '알림'에서 볼 수 있어요.">
        {Object.entries(data.kinds).map(([k, label]) => (
          <Row key={k} label={label}>
            <Switch on={!!data.enabled[k]} disabled={busy} onChange={(v) => run(async () => {
              await api("/api/settings", "PUT", { [`notify.${k}`]: v }); setTick((t) => t + 1);
            })} />
          </Row>
        ))}
      </Group>
      <Group footer={<Msg m={msg} />}>
        <Row label="알림 시험" sub="지금 시험 알림을 하나 보내요">
          <button className="g-btn" disabled={busy} onClick={() => run(async () => {
            const r = await api<{ tray: boolean }>("/api/notify/test", "POST");
            if (!r.tray) throw new Error("트레이 앱이 아니라 알림을 띄울 수 없어요 (로그에만 남음)");
          }, "보냈어요. 화면 오른쪽 아래를 확인해 보세요.")}>보내기</button>
        </Row>
      </Group>
    </>
  );
}

// ---------- 기록 · 수집 ----------
const RETENTION: { key: string; label: string; unit: string }[] = [
  { key: "retention.sys_minute_days", label: "시스템 지표 (1분 단위)", unit: "일" },
  { key: "retention.sys_hour_days", label: "시스템 지표 (1시간 단위)", unit: "일" },
  { key: "retention.net_minute_days", label: "원격 연결 품질", unit: "일" },
  { key: "retention.usage_event_days", label: "AI 사용량 (응답 단위)", unit: "일" },
  { key: "retention.usage_hour_days", label: "AI 사용량 (1시간 단위)", unit: "일" },
  { key: "retention.usage_day_days", label: "AI 사용량 (하루 단위)", unit: "일 · 0 = 무기한" },
  { key: "retention.limit_snapshot_days", label: "한도 기록", unit: "일" },
  { key: "retention.session_days", label: "세션 기록", unit: "일" },
];
const INTERVALS: { key: string; label: string; unit: string }[] = [
  { key: "interval.local_sec", label: "이 PC", unit: "초" },
  { key: "interval.remote_sec", label: "원격 PC 시스템", unit: "초" },
  { key: "interval.remote_claude_sec", label: "원격 PC AI 사용량", unit: "초" },
];

function HistoryPage() {
  const { data } = usePoll<Record<string, unknown>>("/api/settings", 600000);
  const [v, setV] = useState<Record<string, string>>({});
  const { busy, msg, run } = useBusy();
  const all = [...RETENTION, ...INTERVALS];
  useEffect(() => { if (data) setV(Object.fromEntries(all.map((f) => [f.key, String(data[f.key] ?? "")]))); }, [data]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!data) return <Muted>불러오는 중…</Muted>;
  const changed = all.filter((f) => v[f.key] !== String(data[f.key] ?? ""));
  const rows = (fs: typeof RETENTION) => fs.map((f) => (
    <Row key={f.key} label={f.label}>
      <input className="g-in num" type="number" min={0} value={v[f.key] ?? ""} onChange={(e) => setV({ ...v, [f.key]: e.target.value })} />
      <span className="unit">{f.unit}</span>
    </Row>
  ));
  return (
    <>
      <Group title="보관 기간" footer="기간이 지난 기록은 매시 정리해요. 짧은 단위 기록은 긴 단위로 합쳐져 남아요.">{rows(RETENTION)}</Group>
      <Group title="수집 주기" footer="주기 변경은 앱을 다시 켜면 적용돼요.">{rows(INTERVALS)}</Group>
      <div className="g-actions">
        <button className="g-btn primary" disabled={busy || !changed.length} onClick={() => run(() =>
          api("/api/settings", "PUT", Object.fromEntries(changed.map((f) => [f.key, Number(v[f.key])]))), "저장했어요.")}>저장</button>
        <Msg m={msg} />
      </div>
    </>
  );
}

// ---------- 정보 ----------
interface UpdateInfo {
  current: string; latest?: string | null; newer?: boolean; notes?: string; page?: string; error?: string | null; installed?: boolean;
  checked_at?: number | null; state: { phase: "idle" | "downloading" | "installing" | "error"; pct?: number; version?: string; error?: string };
}

/** 업데이트: GitHub Releases 확인 → 받아서 설치 → 앱이 다시 켜짐 */
function UpdateGroup() {
  const [q, setQ] = useState("");
  const { data: U } = usePoll<UpdateInfo>(`/api/update?_=${q}`, 2000);
  const { busy, msg, run } = useBusy();
  if (!U) return null;
  const st = U.state;
  const label = st.phase === "downloading" ? `${st.version} 받는 중… ${st.pct ?? 0}%`
    : st.phase === "installing" ? `${st.version} 설치 중 — 앱이 잠깐 꺼졌다가 다시 켜져요`
    : U.error ? U.error : U.newer ? `새 버전 ${U.latest}이 있어요` : `최신 버전이에요 (${U.current})`;
  return (
    <Group title="업데이트" footer={<>{U.checked_at ? `마지막 확인 ${ago(Date.now() / 1000 - U.checked_at)} · 6시간마다 자동 확인` : ""}
      {st.phase === "error" && <div className="g-msg" style={{ color: C.red }}>{st.error}</div>}<Msg m={msg} /></>}>
      <Row label={label} sub={U.newer && U.notes ? U.notes.split("\n").filter(Boolean).slice(0, 4).join(" · ") : undefined}>
        {U.newer && st.phase !== "installing" && st.phase !== "downloading" ? (U.installed
          ? <button className="g-btn primary" disabled={busy} onClick={() => run(() => api("/api/update/install", "POST"))}>지금 업데이트</button>
          : <span className="g-val">소스 실행 중 — git pull</span>)
          : <button className="g-btn" disabled={busy || st.phase === "downloading"} onClick={() => setQ(String(Date.now()))}>확인</button>}
      </Row>
      {st.phase === "downloading" && <Row label={<div className="bar"><i style={{ width: `${st.pct ?? 0}%`, background: "var(--ios-blue)" }} /></div>} />}
    </Group>
  );
}

function LogViewer({ onClose }: { onClose: () => void }) {
  const { data } = usePoll<{ path: string; lines: string[]; errors: Record<string, string> }>("/api/logs?lines=400", 5000);
  const errs = Object.entries(data?.errors || {});
  return (
    <Group title={<>로그 <button className="link" onClick={onClose}>닫기</button></>} footer={data?.path}>
      {errs.length > 0 && <Row label={<span style={{ color: C.red }}>지금 난 오류 {errs.length}건</span>}
        sub={errs.map(([k, v]) => `${k}: ${v}`).join("\n")} />}
      <div className="logbox"><pre>{(data?.lines || []).join("\n") || "로그 없음"}</pre></div>
    </Group>
  );
}

function AboutPage() {
  const { data: A } = usePoll<AppInfo>("/api/app", 600000);
  const [logs, setLogs] = useState(false);
  const { busy, msg, run } = useBusy();
  const issue = () => {
    const body = [`- 버전: ${A?.version || "?"} (${A?.installed ? "설치판" : "소스"})`, `- OS: ${A?.platform || "?"}`, "",
      "## 어떤 문제인가요?", "", "## 어떻게 하면 생기나요?", "",
      "<!-- 설정 > 정보 > 로그 보기의 내용을 붙이면 도움이 돼요. 세션 제목·PC 이름 등 개인정보는 지우고 올려 주세요. -->"].join("\n");
    window.open(`${A?.repo}/issues/new?title=${encodeURIComponent("[문제] ")}&body=${encodeURIComponent(body)}`, "_blank");
  };
  return (
    <>
      <div className="about">
        <img src="/icon-192.png?v=2" alt="" />
        <div><div className="nm">{A?.name || "RuiBotte Deck"}</div><div className="muted">버전 {A?.version || "–"} · made by RuiBotte</div></div>
      </div>
      <UpdateGroup />
      <Group title="문제 해결" footer={<Msg m={msg} />}>
        <Row label="로그 보기" sub="최근 기록·지금 난 오류"><button className="g-btn" onClick={() => setLogs(!logs)}>{logs ? "닫기" : "보기"}</button></Row>
        <Row label="데이터 폴더" sub={A?.data_dir || "설정·기록(DB)·로그"}>
          <button className="g-btn" disabled={busy} onClick={() => run(() => api("/api/app/open-folder", "POST"))}>열기</button>
        </Row>
        <Row label="문제 보고" sub="GitHub에 새 이슈 쓰기 (버전·OS가 미리 채워져요)">
          {A?.repo && <button className="g-btn" onClick={issue}>보고하기 ›</button>}
        </Row>
      </Group>
      {logs && <LogViewer onClose={() => setLogs(false)} />}
      <Group>
        <Row label="소스 코드" sub="GitHub">{A?.repo && <a className="g-btn" href={A.repo} target="_blank" rel="noreferrer">열기 ›</a>}</Row>
        <Row label="설치 방식" sub={A?.installed ? "설치판 · 제거는 Windows 설정 > 앱" : "소스에서 실행 중"} />
      </Group>
    </>
  );
}

// ---------- 창 ----------
type Page = "general" | "pc" | "ai:claude" | "ai:codex" | "ai:gemini" | "notify" | "history" | "about";
// 분류 아이콘: 글꼴마다 크기·높이가 다른 이모지 대신 같은 24칸 SVG (흰 선 2px, 가운데 정렬)
const svg = (d: ReactNode, fill = false) => (
  <svg viewBox="0 0 24 24" fill={fill ? "currentColor" : "none"} stroke={fill ? "none" : "currentColor"} strokeWidth={2}
    strokeLinecap="round" strokeLinejoin="round">{d}</svg>
);
const NAV_ICON: Record<string, ReactNode> = {
  general: svg(<><circle cx="12" cy="12" r="3.2" /><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7" /></>),
  pc: svg(<><rect x="3" y="4.5" width="18" height="12" rx="2" /><path d="M9 20h6M12 16.5V20" /></>),
  claude: svg(<path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6L5.6 18.4" />),
  codex: svg(<><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3.2" /></>),
  gemini: svg(<path d="M12 2.5c.9 5 3.6 8.4 9.5 9.5-5.9 1.1-8.6 4.5-9.5 9.5-.9-5-3.6-8.4-9.5-9.5 5.9-1.1 8.6-4.5 9.5-9.5z" />, true),
  notify: svg(<><path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 1.5h-15z" /><path d="M10 20.5a2 2 0 0 0 4 0" /></>),
  history: svg(<><circle cx="12" cy="13" r="8" /><path d="M12 9v4l2.5 2.5M10 2.8h4" /></>),
  about: svg(<><circle cx="12" cy="12" r="9" /><path d="M12 11v6" /><circle cx="12" cy="7.6" r="1.1" fill="currentColor" stroke="none" /></>),
};
const NAV: { sec?: string; id: Page; label: string; icon: string; color: string }[] = [
  { id: "general", label: "일반", icon: "general", color: "#8e8e93" },
  { id: "pc", label: "PC", icon: "pc", color: "#5e5ce6" },
  { sec: "AI 도구", id: "ai:claude", label: "Claude Code", icon: "claude", color: "#d97757" },
  { id: "ai:codex", label: "GPT (Codex)", icon: "codex", color: "#10a37f" },
  { id: "ai:gemini", label: "Gemini", icon: "gemini", color: "#4285f4" },
  { sec: " ", id: "notify", label: "알림", icon: "notify", color: "#ff453a" },
  { id: "history", label: "기록 · 수집", icon: "history", color: "#ff9f0a" },
  { id: "about", label: "정보", icon: "about", color: "#636366" },
];

export function Settings({ onClose, onWizard }: { onClose: () => void; onWizard?: () => void }) {
  const [page, setPage] = useState<Page>(() => {
    try { return (localStorage.getItem("settings-page") as Page) || "general"; } catch { return "general"; }
  });
  const { data: provs } = usePoll<Record<string, { enabled: boolean }>>("/api/providers/settings", 10000);
  const go = (p: Page) => { setPage(p); try { localStorage.setItem("settings-page", p); } catch { /* 저장 불가 */ } };
  const cur = NAV.find((n) => n.id === page) || NAV[0];
  return (
    <Overlay wide title="설정" onClose={onClose} className="settings-sheet">
      <div className="st-win">
        <nav className="st-side">
          {NAV.map((n) => (
            <div key={n.id}>
              {n.sec && <div className="sec">{n.sec.trim()}</div>}
              <button className={page === n.id ? "on" : ""} onClick={() => go(n.id)}>
                <span className="st-ic" style={{ backgroundColor: n.color }}>{NAV_ICON[n.icon]}</span>
                <span className="lbl">{n.label}</span>
                {n.id.startsWith("ai:") && provs && !provs[n.id.slice(3)]?.enabled && <span className="off-tag">꺼짐</span>}
              </button>
            </div>
          ))}
        </nav>
        <div className="st-main">
          <h2>{cur.label}</h2>
          {page === "general" && <GeneralPage onWizard={onWizard} />}
          {page === "pc" && <PcPage />}
          {page.startsWith("ai:") && <AiToolPage key={page} pid={page.slice(3)} />}
          {page === "notify" && <NotifyPage />}
          {page === "history" && <HistoryPage />}
          {page === "about" && <AboutPage />}
        </div>
      </div>
    </Overlay>
  );
}
