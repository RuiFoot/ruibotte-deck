// 설정 화면: PC 관리(추가·연결 테스트·이름·끄기·삭제), AI 도구 켜기/끄기, 기록 보관 기간·수집 주기
import { useEffect, useState } from "react";
import { C, ago, api, usePoll } from "../lib";
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

function DetectView({ d, onPickHome }: { d: Detect; onPickHome: (h: string) => void }) {
  if (!d.ok) return <div className="err">연결 실패: {d.error}</div>;
  const provs = Object.keys(d.providers || {});
  return (
    <div className="detect">
      <div><b style={{ color: C.green }}>연결 성공</b> · {d.agent?.hostname || ""} · {d.os} · {MODE_LABEL[d.mode || ""] || d.mode}{d.wsl_distro ? ` (${d.wsl_distro})` : ""} · {d.latency_ms}ms</div>
      <div>홈 폴더: <code>{d.home || "–"}</code> · 감지된 AI 도구: {provs.length ? provs.map((p) => PROVIDER_LABEL[p] || p).join(", ") : "없음"}</div>
      {(d.agent?.homes || []).filter((h) => Object.keys(h.providers).length).length > 1 && (
        <div>다른 홈 폴더 후보:{" "}
          {(d.agent?.homes || []).filter((h) => Object.keys(h.providers).length && h.path !== d.home).map((h) => (
            <button key={h.path} className="link" onClick={() => onPickHome(h.path)}>{h.path} ({Object.keys(h.providers).join(", ")})</button>
          ))}
        </div>
      )}
      {d.mode === "powershell" && <div style={{ color: C.amber }}>Python이 없어 시스템 지표만 수집합니다.</div>}
    </div>
  );
}

export function AddMachine({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState({ name: "", ssh_target: "", ssh_fallbacks: "", wsl_distro: "", home_override: "" });
  const [det, setDet] = useState<Detect | null>(null);
  const { busy, msg, run } = useBusy();
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const body = () => ({ ...f, name: f.name || f.ssh_target, ssh_fallbacks: f.ssh_fallbacks });
  return (
    <div className="card form">
      <div className="ttl">원격 PC 추가<span className="r">SSH 키 인증만 사용 · 비밀번호는 저장하지 않음</span></div>
      <div className="fgrid">
        <label>표시 이름<input value={f.name} onChange={set("name")} placeholder="예: 회사 PC" /></label>
        <label>SSH 대상 *<input value={f.ssh_target} onChange={set("ssh_target")} placeholder="~/.ssh/config 별칭, user@host, user@host:port" /></label>
        <label>대체 주소<input value={f.ssh_fallbacks} onChange={set("ssh_fallbacks")} placeholder="쉼표로 구분 (선택)" /></label>
        <label>WSL 배포판<input value={f.wsl_distro} onChange={set("wsl_distro")} placeholder="자동 감지 (선택)" /></label>
        <label className="wide">홈 폴더<input value={f.home_override} onChange={set("home_override")} placeholder="자동 감지 — AI 도구 기록이 있는 폴더 (선택)" /></label>
      </div>
      <div className="actions">
        <button disabled={busy || !f.ssh_target} onClick={() => run(async () => setDet(await api<Detect>("/api/machines/test", "POST", body())))}>
          {busy && !det ? "연결 테스트 중…" : "연결 테스트"}</button>
        <button className="primary" disabled={busy || !f.ssh_target} onClick={() => run(async () => {
          await api("/api/machines", "POST", body()); setF({ name: "", ssh_target: "", ssh_fallbacks: "", wsl_distro: "", home_override: "" }); setDet(null); onDone();
        }, "추가했습니다. 곧 연결을 시작합니다.")}>저장</button>
        {msg && <span style={{ color: msg.ok ? C.green : C.red }}>{msg.text}</span>}
      </div>
      {det && <DetectView d={det} onPickHome={(h) => setF({ ...f, home_override: h })} />}
    </div>
  );
}

function MachineItem({ m, onChange }: { m: MachineApi; onChange: () => void }) {
  const [name, setName] = useState(m.name);
  const [confirm, setConfirm] = useState(false);
  const { busy, msg, run } = useBusy();
  useEffect(() => setName(m.name), [m.name]);
  const st = m.status;
  const now = Date.now() / 1000;
  return (
    <div className={`mrow ${m.enabled ? "" : "off"}`}>
      <span className={`dot ${m.online ? "on" : ""}`} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="nm">
          <input value={name} onChange={(e) => setName(e.target.value)} />
          {name !== m.name && <button disabled={busy} onClick={() => run(async () => { await api(`/api/machines/${m.id}`, "PUT", { name }); onChange(); })}>이름 저장</button>}
        </div>
        <div className="s">
          {m.kind === "local" ? "이 PC" : <>
            {m.ssh_target}{m.ssh_fallbacks?.length ? ` (+${m.ssh_fallbacks.join(", ")})` : ""}
            {st?.mode ? ` · ${MODE_LABEL[st.mode] || st.mode}` : ""}{st?.home ? ` · ${st.home}` : ""}
            {st?.active_providers?.length ? ` · ${st.active_providers.map((p) => PROVIDER_LABEL[p] || p).join(", ")}` : ""}
            {m.online ? (st?.latency_ms != null ? ` · ${st.latency_ms}ms` : "") : !m.enabled ? " · 꺼짐" : st?.error || m.system?.error ? ` · ${st?.error || m.system?.error}` : st?.last_ok ? ` · 끊김 (${ago(now - st.last_ok)} 마지막 수신)` : " · 연결 중…"}
          </>}
        </div>
        {msg && !msg.ok && <div className="err">{msg.text}</div>}
      </div>
      {m.kind !== "local" && (
        <div className="btns">
          <button disabled={busy} onClick={() => run(async () => { await api(`/api/machines/${m.id}`, "PUT", { enabled: !m.enabled }); onChange(); })}>{m.enabled ? "끄기" : "켜기"}</button>
          {confirm
            ? <button className="danger" disabled={busy} onClick={() => run(async () => { await api(`/api/machines/${m.id}`, "DELETE"); onChange(); })}>정말 삭제</button>
            : <button onClick={() => { setConfirm(true); setTimeout(() => setConfirm(false), 4000); }}>삭제</button>}
        </div>
      )}
    </div>
  );
}

function NotifySettings() {
  const [tick, setTick] = useState(0);
  const { data } = usePoll<{ kinds: Record<string, string>; enabled: Record<string, boolean> }>(`/api/notify?_=${tick}`, 600000);
  const { busy, msg, run } = useBusy();
  if (!data) return <Muted>불러오는 중…</Muted>;
  return (
    <div className="card form">
      <div className="ttl">알림<span className="r">Windows 알림(트레이 아이콘)으로 보내요</span></div>
      {Object.entries(data.kinds).map(([k, label]) => (
        <div key={k} className="mrow">
          <span className={`dot ${data.enabled[k] ? "on" : ""}`} />
          <div style={{ flex: 1 }}><div className="nm">{label}</div></div>
          <div className="btns"><button disabled={busy} onClick={() => run(async () => {
            await api("/api/settings", "PUT", { [`notify.${k}`]: !data.enabled[k] }); setTick((t) => t + 1);
          })}>{data.enabled[k] ? "끄기" : "켜기"}</button></div>
        </div>
      ))}
      <div className="actions">
        <button disabled={busy} onClick={() => run(async () => {
          const r = await api<{ tray: boolean }>("/api/notify/test", "POST");
          if (!r.tray) throw new Error("트레이 앱이 아니라 알림을 띄울 수 없어요 (로그에만 남음)");
        }, "시험 알림을 보냈어요. 화면 오른쪽 아래를 확인해 보세요.")}>알림 시험</button>
        {msg && <span style={{ color: msg.ok ? C.green : C.red }}>{msg.text}</span>}
      </div>
    </div>
  );
}

const SETTING_FIELDS: { key: string; label: string; unit: string }[] = [
  { key: "retention.sys_minute_days", label: "시스템 지표 (1분 단위)", unit: "일" },
  { key: "retention.sys_hour_days", label: "시스템 지표 (1시간 단위)", unit: "일" },
  { key: "retention.usage_event_days", label: "AI 사용량 (응답 단위)", unit: "일" },
  { key: "retention.usage_hour_days", label: "AI 사용량 (1시간 단위)", unit: "일" },
  { key: "retention.usage_day_days", label: "AI 사용량 (하루 단위)", unit: "일 (0 = 무기한)" },
  { key: "retention.limit_snapshot_days", label: "한도 기록", unit: "일" },
  { key: "retention.session_days", label: "세션 기록", unit: "일" },
  { key: "interval.local_sec", label: "이 PC 수집 주기", unit: "초" },
  { key: "interval.remote_sec", label: "원격 PC 시스템 수집 주기", unit: "초" },
  { key: "interval.remote_claude_sec", label: "원격 PC AI 사용량 주기", unit: "초" },
];

function HistorySettings() {
  const { data } = usePoll<Record<string, unknown>>("/api/settings", 600000);
  const [v, setV] = useState<Record<string, string>>({});
  const { busy, msg, run } = useBusy();
  useEffect(() => { if (data) setV(Object.fromEntries(SETTING_FIELDS.map((f) => [f.key, String(data[f.key] ?? "")]))); }, [data]);
  if (!data) return <Muted>불러오는 중…</Muted>;
  const changed = SETTING_FIELDS.filter((f) => v[f.key] !== String(data[f.key] ?? ""));
  return (
    <div className="card form">
      <div className="ttl">기록 보관 기간 · 수집 주기<span className="r">주기 변경은 앱을 다시 켜면 적용</span></div>
      <div className="fgrid three">
        {SETTING_FIELDS.map((f) => (
          <label key={f.key}>{f.label}<span className="unit"><input type="number" min={0} value={v[f.key] ?? ""}
            onChange={(e) => setV({ ...v, [f.key]: e.target.value })} />{f.unit}</span></label>
        ))}
      </div>
      <div className="actions">
        <button className="primary" disabled={busy || !changed.length} onClick={() => run(() =>
          api("/api/settings", "PUT", Object.fromEntries(changed.map((f) => [f.key, Number(v[f.key])]))), "저장했습니다.")}>저장</button>
        {msg && <span style={{ color: msg.ok ? C.green : C.red }}>{msg.text}</span>}
      </div>
    </div>
  );
}

interface Display { id: string; name: string; device: string; primary: boolean; internal: boolean; x: number; y: number; width: number; height: number }

/** 표시 모니터 · 로그인 시 자동 실행 */
/** 테마: 자동(낮 라이트·밤 다크, 시각 지정) / 항상 다크 / 항상 라이트 — 모든 기기(아이패드 포함)에 같이 적용 */
interface ProfileRow { id: string; path: string; label: string | null; enabled: boolean; key: string; account?: string | null }
interface Slot {
  machine_id: string; machine: string; provider: string; label: string;
  default: { id: string | null; label: string; key: string; detected: boolean; account?: string | null };
  extra: ProfileRow[]; found: string[];
}
const CONFIG_ENV: Record<string, string> = { claude: "CLAUDE_CONFIG_DIR", codex: "CODEX_HOME" };
const CLI_NAME: Record<string, string> = { claude: "claude", codex: "codex" };

/** CLI 로그인 창 열기. CLI가 없으면 '설치할까요?' → 확인하면 설치 후 같은 창에서 이어서 로그인 */
export async function cliLogin(body: Record<string, unknown>) {
  let r = await api<{ ok: boolean; need_install?: boolean; message: string }>("/api/profiles/login", "POST", body);
  if (r.need_install) {
    if (!window.confirm(r.message)) throw new Error("설치를 취소했어요");
    r = await api("/api/profiles/login", "POST", { ...body, install: true });
  }
  return r;
}

const PROVIDER_NOTE: Record<string, string> = {
  claude: "한도: 계정 API → 데스크톱 기록 → 로그 추정", codex: "한도: 로그의 정확한 값", gemini: "한도: 하루 요청 수로 추정",
};

/** AI 관리: 도구마다 켜기/끄기 + PC별 계정(로그인 폴더). 이 PC는 '로그인'으로 바로 추가. 토큰은 복사·저장하지 않고 폴더를 읽기만 */
function AiManagement() {
  const [tick, setTick] = useState(0);
  const { data, error } = usePoll<{ slots: Slot[] }>(`/api/profiles?_=${tick}`, 600000);
  const { data: provs } = usePoll<Record<string, { enabled: boolean }>>(`/api/providers/settings?_=${tick}`, 600000);
  const { busy, msg, run } = useBusy();
  const [draft, setDraft] = useState<Record<string, { path: string; label: string }>>({});
  const refresh = () => setTick((t) => t + 1);
  const save = (fn: () => Promise<unknown>, ok?: string) => run(async () => { await fn(); refresh(); }, ok);
  if (error && !data) return <div className="err">{error}</div>;
  if (!data || !provs) return <Muted>불러오는 중…</Muted>;
  const loginApi = cliLogin;
  const login = (provider: string, label: string, isDefault = false) => save(
    () => loginApi({ provider, label, default: isDefault }),
    "창을 열었어요. 브라우저에서 로그인을 마치면 그 계정으로 집계돼요 (한 번 사용한 뒤부터).");
  const renderSlot = (sl: Slot) => {
        const k = `${sl.machine_id}/${sl.provider}`, d = draft[k] || { path: "", label: "" };
        const setD = (v: Partial<typeof d>) => setDraft({ ...draft, [k]: { ...d, ...v } });
        return (
          <div key={k} className="acct-slot">
            <div className="sub-ttl">{sl.machine} · {sl.label}</div>
            <div className="mrow">
              <span className={`dot ${sl.default.detected ? "on" : ""}`} />
              <div style={{ flex: 1, minWidth: 0 }}><div className="nm">{sl.default.account || "기본 폴더 계정"}</div>
                <div className="s">{sl.default.detected ? "기본 폴더 · 사용 중" : "아직 로그인·사용 기록 없음"}</div></div>
              {!sl.default.detected && sl.machine_id === "local" && CONFIG_ENV[sl.provider] && (
                <button disabled={busy} onClick={() => login(sl.provider, sl.default.label, true)}>로그인</button>)}
              <input className="acct-label" placeholder="별명 (선택)" defaultValue={sl.default.label} disabled={busy}
                onBlur={(e) => e.target.value !== sl.default.label && save(() => api("/api/profiles", "POST",
                  { machine_id: sl.machine_id, provider: sl.provider, path: "", label: e.target.value }))} />
            </div>
            {sl.extra.map((x) => (
              <div key={x.id} className="mrow">
                <span className={`dot ${x.enabled ? "on" : ""}`} />
                <div style={{ flex: 1, minWidth: 0 }}><div className="nm" title={x.path}>{x.account || x.path}</div>
                  <div className="s">{x.account ? `${x.path} · ` : ""}{x.enabled ? "수집 중" : "꺼짐"}{CONFIG_ENV[sl.provider] && sl.machine_id === "local" && (
                    <> · 이 계정으로 쓰기: <code className="sel">$env:{CONFIG_ENV[sl.provider]}="{x.path}"; {CLI_NAME[sl.provider]}</code></>)}</div></div>
                <input className="acct-label" placeholder="별명 (선택)" defaultValue={x.label || ""} disabled={busy}
                  onBlur={(e) => e.target.value && e.target.value !== x.label && save(() => api(`/api/profiles/${x.id}`, "PUT", { label: e.target.value }))} />
                <div className="btns">
                  <button disabled={busy} onClick={() => save(() => api(`/api/profiles/${x.id}`, "PUT", { enabled: !x.enabled }))}>{x.enabled ? "끄기" : "켜기"}</button>
                  <button className="danger" disabled={busy} onClick={() => save(() => api(`/api/profiles/${x.id}`, "DELETE"))}>삭제</button>
                </div>
              </div>
            ))}
            {sl.found.map((f) => (
              <div key={f} className="mrow found">
                <span className="dot" />
                <div style={{ flex: 1, minWidth: 0 }}><div className="nm" title={f}>{f}</div><div className="s">찾은 계정 폴더 · 아직 등록 안 됨</div></div>
                <button disabled={busy} onClick={() => setD({ path: f })}>이 폴더 등록</button>
              </div>
            ))}
            {sl.machine_id === "local" && CONFIG_ENV[sl.provider] && (
              <div className="acct-add">
                <input className="acct-label" placeholder="새 계정 이름" value={draft[k + "/new"]?.label || ""} disabled={busy}
                  onChange={(e) => setDraft({ ...draft, [k + "/new"]: { path: "", label: e.target.value } })} />
                <button disabled={busy || !draft[k + "/new"]?.label} onClick={() => save(async () => {
                  const r = await loginApi({ provider: sl.provider, label: draft[k + "/new"].label });
                  setDraft({ ...draft, [k + "/new"]: { path: "", label: "" } });
                  return r;
                }, "로그인 창을 열었어요. 브라우저에서 로그인을 마치면 이 계정으로 집계돼요 (그 계정으로 한 번 사용한 뒤부터).")}>+ 로그인해서 계정 추가</button>
                <span className="muted small">전용 폴더를 만들고 그 폴더로 로그인 창을 열어요</span>
              </div>
            )}
            <div className="acct-add">
              <input placeholder={sl.machine_id === "local" ? String.raw`이미 로그인된 다른 계정 폴더 (예: C:\Users\me\.claude-personal)` : "그 PC에 로그인된 계정 폴더 (예: /mnt/c/Users/me/.claude-personal)"}
                value={d.path} onChange={(e) => setD({ path: e.target.value })} disabled={busy} />
              <input className="acct-label" placeholder="이름 (예: 개인)" value={d.label} onChange={(e) => setD({ label: e.target.value })} disabled={busy} />
              <button disabled={busy || !d.path || !d.label} onClick={() => save(async () => {
                await api("/api/profiles", "POST", { machine_id: sl.machine_id, provider: sl.provider, path: d.path, label: d.label });
                setDraft({ ...draft, [k]: { path: "", label: "" } });
              }, "등록했어요. 잠시 뒤 AI 사용량에 계정으로 나와요.")}>추가</button>
            </div>
          </div>
        );
  };
  return (
    <>
      {Object.entries(provs).map(([pid, pv]) => {
        const slots = data.slots.filter((x) => x.provider === pid);
        return (
          <div key={pid} className={`card form accounts ${pv.enabled ? "" : "off"}`}>
            <div className="ttl">
              <span className={`dot ${pv.enabled ? "on" : ""}`} style={{ marginRight: ".5em" }} />{PROVIDER_LABEL[pid] || pid}
              <span className="r">{PROVIDER_NOTE[pid]}
                <button style={{ marginLeft: ".8em" }} disabled={busy} onClick={() => save(() => api(`/api/providers/${pid}`, "PUT", { enabled: !pv.enabled }))}>{pv.enabled ? "끄기" : "켜기"}</button></span>
            </div>
            {!pv.enabled ? <Muted>꺼져 있어요 — 켜면 PC마다 기록을 찾아 수집하고, 여기서 로그인해 계정을 추가할 수 있어요.</Muted>
              : slots.length ? slots.map(renderSlot) : <Muted>아직 어느 PC에서도 기록을 찾지 못했어요.</Muted>}
          </div>
        );
      })}
      <div className="muted small" style={{ margin: ".2em .3em 0" }}>
        같은 이름 = 같은 계정 (PC가 달라도 한도·합계를 같이 봐요).
        다른 계정을 쓰려면 그 계정 전용 폴더로 CLI를 실행해 로그인하세요 —{" "}
        {Object.entries(CONFIG_ENV).map(([p, env], i) => <span key={p}>{i ? " · " : ""}<code>{env}</code></span>)} 환경 변수로 폴더를 지정합니다.
        토큰은 대시보드에 복사·저장하지 않아요.
      </div>
      {msg && <div style={{ color: msg.ok ? C.green : C.red, fontSize: ".85em", marginTop: ".5em" }}>{msg.text}</div>}
    </>
  );
}

function ThemeSettings() {
  const [tick, setTick] = useState(0);
  const { data } = usePoll<Record<string, unknown>>(`/api/settings?_=${tick}`, 600000);
  const { busy, msg, run } = useBusy();
  if (!data) return <Muted>불러오는 중…</Muted>;
  const mode = String(data["display.theme"] ?? "auto");
  const save = (patch: Record<string, string>) => run(async () => { await api("/api/settings", "PUT", patch); setTick((t) => t + 1); });
  const opts: [string, string][] = [["auto", "자동 (낮 라이트 · 밤 다크)"], ["dark", "항상 다크"], ["light", "항상 라이트"]];
  return (
    <div className="card form">
      <div className="ttl">테마<span className="r">몇 초 안에 모든 화면에 적용돼요</span></div>
      <div className="minitabs" style={{ marginLeft: 0 }}>
        {opts.map(([v, l]) => <button key={v} className={mode === v ? "on" : ""} disabled={busy} onClick={() => save({ "display.theme": v })}>{l}</button>)}
      </div>
      {mode === "auto" && (
        <div className="row" style={{ display: "flex", gap: "1em", marginTop: ".7em" }}>
          <label>라이트 시작<input type="time" defaultValue={String(data["display.light_from"] ?? "07:00")} disabled={busy}
            onBlur={(e) => e.target.value && save({ "display.light_from": e.target.value })} /></label>
          <label>다크 시작<input type="time" defaultValue={String(data["display.dark_from"] ?? "19:00")} disabled={busy}
            onBlur={(e) => e.target.value && save({ "display.dark_from": e.target.value })} /></label>
        </div>
      )}
      {msg && !msg.ok && <div style={{ color: C.red, fontSize: ".85em", marginTop: ".5em" }}>{msg.text}</div>}
    </div>
  );
}

export function DisplayAndApp() {
  const [tick, setTick] = useState(0);
  const { data: D } = usePoll<{ displays: Display[]; selected: string }>(`/api/displays?_=${tick}`, 600000);
  const { data: A } = usePoll<{ platform: string; autostart: boolean; port: number; lan: boolean }>(`/api/app?_=${tick}`, 600000);
  const { busy, msg, run } = useBusy();
  const pick = (v: string) => run(async () => { await api("/api/settings", "PUT", { "display.monitor": v }); setTick((t) => t + 1); }, "저장했습니다. 다음에 대시보드를 열 때 적용돼요.");
  const sameIds = (id: string) => (D?.displays || []).filter((d) => d.id === id).length > 1;
  const opts: [string, string][] = [["auto", "자동 (노트북 내장 화면 → 없으면 주 모니터)"], ["primary", "주 모니터"],
    ...(D?.displays || []).map((d): [string, string] => [sameIds(d.id) ? d.device : d.id,
      `${d.internal ? "내장 화면" : d.id || d.device} · ${d.width}×${d.height}${d.primary ? " · 주 모니터" : ""}${sameIds(d.id) ? ` (${d.device.replace("\\\\.\\", "")})` : ""}`])];
  return (
    <div className="card form">
      <div className="ttl">화면 · 실행</div>
      {A?.platform === "win32" || !A ? (
        <label>대시보드를 띄울 화면 (트레이의 '대시보드 열기')
          <select value={D?.selected || "auto"} disabled={busy || !D} onChange={(e) => pick(e.target.value)}>
            {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
      ) : <Muted>이 OS에서는 기본 브라우저/앱 창을 전체 화면으로 엽니다.</Muted>}
      <div className="mrow" style={{ marginTop: ".6em" }}>
        <span className={`dot ${A?.autostart ? "on" : ""}`} />
        <div style={{ flex: 1 }}><div className="nm">로그인할 때 자동 실행</div>
          <div className="s">{A?.platform === "win32" ? "시작프로그램 바로가기" : A?.platform === "darwin" ? "LaunchAgent" : "~/.config/autostart"} · 트레이 메뉴에서도 바꿀 수 있어요</div></div>
        <div className="btns"><button disabled={busy || !A} onClick={() => run(async () => { await api("/api/app", "PUT", { autostart: !A!.autostart }); setTick((t) => t + 1); })}>{A?.autostart ? "끄기" : "켜기"}</button></div>
      </div>
      {A && <div className="muted" style={{ marginTop: ".5em" }}>다른 기기 접속: {A.lan ? `켜짐 (같은 네트워크에서 :${A.port}, 주소에 ?t=토큰 필요)` : "꺼짐 (이 PC에서만) — config.json의 bind를 0.0.0.0으로 바꾸면 켜져요"}</div>}
      {msg && <div style={{ color: msg.ok ? C.green : C.red, fontSize: ".85em", marginTop: ".5em" }}>{msg.text}</div>}
    </div>
  );
}

export function Settings({ onClose, onWizard }: { onClose: () => void; onWizard?: () => void }) {
  const [tick, setTick] = useState(0);
  const { data, error } = usePoll<{ machines: MachineApi[] }>(`/api/machines?_=${tick}`, 4000);
  const refresh = () => setTick((t) => t + 1);
  return (
    <Overlay wide title="설정" onClose={onClose}>
      <div className="settings">
        <section>
          <h3>PC 관리</h3>
          <div className="card form">
            {error && !data ? <div className="err">{error}</div> : !data ? <Muted>불러오는 중…</Muted>
              : data.machines.map((m) => <MachineItem key={m.id} m={m} onChange={refresh} />)}
          </div>
          <AddMachine onDone={refresh} />
        </section>
        <section>
          <h3>AI 관리</h3>
          <AiManagement />
          <h3>알림</h3>
          <NotifySettings />
          <h3>화면 · 실행</h3>
          <ThemeSettings />
          <DisplayAndApp />
          <h3>기록</h3>
          <HistorySettings />
          {onWizard && <button className="link" style={{ alignSelf: "flex-start" }} onClick={onWizard}>처음 설정 다시 보기 ›</button>}
        </section>
      </div>
    </Overlay>
  );
}
