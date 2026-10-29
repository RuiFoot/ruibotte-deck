import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ago, api, applyTheme, fmtT, hm, store, themeFor, useClock, useDashboardState, usePoll } from "./lib";
import type { AiProvider, MachineRow, RemoteState, State } from "./types";
import { Icon, Muted } from "./components/ui";
import { SegTabs } from "./components/SegTabs";
import { MachineView, fromLocal, fromRemote, machineAlert } from "./components/MachineView";
import type { Machine } from "./components/MachineView";
import { AiPanel, aiTabs } from "./components/AiPanel";
import type { ViewSession } from "./components/ClaudeView";
import { SessionBrowser, SessionModal } from "./components/SessionModal";
import { Settings } from "./components/Settings";
import { Wizard } from "./components/Wizard";
import { InsightsCard } from "./components/InsightsCard";
import { Overlay } from "./components/SessionModal";

const AUTO_SEC = 20;
const PROVIDER_ORDER = ["claude", "codex", "gemini"];

/** PC 화면들을 겹쳐 두고 선택한 것만 보임. 좌우로 쓸어도 넘어감 */
function Views({ index, onSwipe, views }: { index: number; onSwipe: (d: number) => void; views: { node: ReactNode; stale?: boolean }[] }) {
  const sx = useRef<number | null>(null);
  return (
    <div className="views"
      onPointerDown={(e) => { sx.current = e.clientX; }}
      onPointerUp={(e) => { if (sx.current == null) return; const dx = e.clientX - sx.current; sx.current = null; if (Math.abs(dx) > 80) onSwipe(dx < 0 ? 1 : -1); }}>
      {views.map((v, i) => (
        <div key={i} className={`view ${i === index ? "on" : ""} ${i < index ? "left" : ""} ${v.stale ? "stale" : ""}`}>{v.node}</div>
      ))}
    </div>
  );
}

/** 켜진 PC 목록 (v2 서버가 아니면 v1 형식으로 대체) */
function machineList(s: State | null): MachineRow[] {
  if (s?.machines?.length) return s.machines;
  const out: MachineRow[] = [{ id: "local", name: "이 PC", kind: "local", online: !!s?.local }];
  if (s?.remote) out.push({ id: "remote", name: s.remote_label || "원격", kind: "ssh", online: !!s.remote.ok });
  return out;
}

/** 공급자가 최근 7일 안에 쓰였는지 (자동 넘김 대상) */
const providerActive = (P: AiProvider) => Object.values(P.states).some((st) => (st.usage?.days || []).some((d) => d.total > 0));

export default function App() {
  const { state: s, error } = useDashboardState(2000);
  const clock = useClock();
  // 테마: 설정이 오기 전엔 마지막 테마, 이후 설정(자동이면 시각)대로. 렌더 전에 바꿔야 차트 색(C)도 같이 바뀜
  type ThemeMode = "sun" | "auto" | "dark" | "light";
  const [modeNow, setModeNow] = useState<ThemeMode | null>(null);   // 헤더에서 막 바꾼 값 (서버 반영 전까지)
  const savedCfg = s?.theme ?? store.get<{ mode: ThemeMode; light_from: string; dark_from: string; lat?: number | null; lon?: number | null } | undefined>("theme-cfg", undefined);
  const themeCfg = modeNow ? { ...(savedCfg || {}), mode: modeNow } : savedCfg;
  const themeMode: ThemeMode = themeCfg?.mode || "sun";
  const theme = themeFor(themeCfg, clock);
  applyTheme(theme);
  useEffect(() => { if (modeNow && s?.theme?.mode === modeNow) setModeNow(null); }, [s?.theme?.mode, modeNow]);
  const cycleTheme = () => {
    const next: ThemeMode = themeMode === "sun" || themeMode === "auto" ? "light" : themeMode === "light" ? "dark" : "sun";
    setModeNow(next);
    api("/api/settings", "PUT", { "display.theme": next }).catch(() => { /* 서버가 예전 버전이면 이 화면에만 적용 */ });
  };
  useEffect(() => { if (s?.theme) store.set("theme-cfg", s.theme); }, [s?.theme?.mode, s?.theme?.light_from, s?.theme?.dark_from, s?.theme?.lat, s?.theme?.lon]);

  const [auto, setAuto] = useState(() => store.get("auto", true));
  // 영역별 고정: 자동 넘김이 켜져 있어도 그 영역은 지금 탭에 머묾
  const [pinSys, setPinSys] = useState(() => store.get("pin-sys", false));
  const [pinAi, setPinAi] = useState(() => store.get("pin-ai", false));
  const [area, setArea] = useState<"sys" | "ai">(() => store.get("area", "sys"));
  const [sys, setSys] = useState(() => store.get("sub-sys", 0));
  const [pid, setPid] = useState<string>(() => store.get("ai-pid", "claude"));
  const [aiTab, setAiTab] = useState<Record<string, number>>(() => store.get("ai-tab", {}));
  const [turn, setTurn] = useState(0);              // 자동 넘김 진행 줄 재시작용
  const lastTurn = useRef(Date.now());
  const pauseUntil = useRef(0);
  const [paused, setPaused] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [sessionOpen, setSessionOpen] = useState<{ pid: string; sid: string; machine?: string } | null>(null);
  const [browse, setBrowse] = useState<string | null>(null);
  const [wizard, setWizard] = useState(false);
  const [showAlerts, setShowAlerts] = useState(false);
  // 새 버전 알림 (서버가 6시간마다 GitHub 확인, 여기서는 30분마다 결과만 읽음)
  const { data: upd } = usePoll<{ newer?: boolean; latest?: string | null }>("/api/update", 1800000);
  const [alertSeen, setAlertSeen] = useState<number>(() => store.get("alert-seen", 0));
  const [wizardSeen, setWizardSeen] = useState(false);
  const showWizard = wizard || (s?.setup_done === false && !wizardSeen);
  const overlay = showSettings || !!sessionOpen || !!browse || showWizard || showAlerts;

  const machines = machineList(s);
  const names = useCallback((id?: string) => (id && s?.names?.[id]) || machines.find((m) => m.id === id)?.name || (id === "local" ? "이 PC" : id || ""), [machines, s?.names]);
  const ai = s?.ai || {};
  const pids = PROVIDER_ORDER.filter((p) => ai[p]).concat(Object.keys(ai).filter((p) => !PROVIDER_ORDER.includes(p)));
  const curPid = pids.includes(pid) ? pid : pids[0];
  const P = curPid ? ai[curPid] : null;
  const tabsOf = (p: string) => (ai[p] ? aiTabs(ai[p], p).length : 1);
  const curAiTab = curPid ? Math.min(aiTab[curPid] || 0, tabsOf(curPid) - 1) : 0;
  const sysIdx = Math.min(sys, machines.length - 1);

  // 최신 값을 자동 넘김 타이머에서 쓰기 위한 참조
  const live = useRef({ machines: machines.length, pids, curPid, ai, curAiTab, pinSys, pinAi });
  live.current = { machines: machines.length, pids, curPid, ai, curAiTab, pinSys, pinAi };

  const pause = useCallback(() => {
    pauseUntil.current = Date.now() + 60000;
    lastTurn.current = Date.now();
    setPaused(true);
    setTurn((t) => t + 1);
  }, []);
  const pickSys = useCallback((i: number) => {
    const n = live.current.machines;
    const v = ((i % n) + n) % n;
    setSys(v); store.set("sub-sys", v); pause();
  }, [pause]);
  const pickAiTab = useCallback((p: string, i: number) => {
    setAiTab((cur) => { const next = { ...cur, [p]: i }; store.set("ai-tab", next); return next; });
    pause();
  }, [pause]);
  const pickPid = useCallback((p: string) => { setPid(p); store.set("ai-pid", p); pause(); }, [pause]);

  useEffect(() => {
    const id = setInterval(() => {
      const now = Date.now();
      const p = now < pauseUntil.current || overlay;
      setPaused(p);
      if (!auto || p || now - lastTurn.current < AUTO_SEC * 1000) return;
      lastTurn.current = now;
      const L = live.current;
      if (!L.pinSys) setSys((v) => (v + 1) % Math.max(1, L.machines));
      // AI: 현재 공급자의 PC 탭을 돌고, 한 바퀴 돌면 최근에 쓴 다음 공급자로
      if (!L.pinAi && L.curPid && L.ai[L.curPid]) {
        const n = aiTabs(L.ai[L.curPid], L.curPid).length;
        const next = L.curAiTab + 1;
        const actives = L.pids.filter((x) => providerActive(L.ai[x]));
        if (next >= n && actives.length > 1) {
          const np = actives[(actives.indexOf(L.curPid) + 1) % actives.length] || actives[0];
          setPid(np);
          setAiTab((cur) => ({ ...cur, [np]: 0 }));
        } else {
          setAiTab((cur) => ({ ...cur, [L.curPid!]: next % n }));
        }
      }
      setTurn((t) => t + 1);
    }, 500);
    return () => clearInterval(id);
  }, [auto, overlay]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (overlay) { if (e.key === "Escape") { setShowSettings(false); setSessionOpen(null); setBrowse(null); } return; }
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const d = e.key === "ArrowRight" ? 1 : -1;
      pickSys(sysIdx + d);
      if (curPid) pickAiTab(curPid, (curAiTab + d + tabsOf(curPid)) % tabsOf(curPid));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const now = s?.now ?? Date.now() / 1000;
  // 항목별로 마지막 성공 수집 후 얼마나 지났는지 (오래됐으면 이전 기록을 보여주며 "N분 전 기록" 표시)
  const staleTag = (R: RemoteState | null | undefined) => {
    const t = (R as RemoteState & { sample_t?: number })?.sample_t;
    return R?.ok && t && now - t > 40 ? `${ago(now - t)} 기록 · 갱신 중` : null;
  };
  const views: { m: MachineRow; M: Machine | null }[] = useMemo(() => machines.map((m) => {
    if (m.kind === "local") return { m, M: s?.local ? { ...fromLocal(s.local, s.local_extras), id: s?.machines ? "local" : undefined } : null };
    const R = s?.systems?.[m.id] ?? (m.id === "remote" ? s?.remote : null);
    if (!R) return { m, M: null };
    const M = fromRemote(R, m.name, now);
    if (s?.machines) M.id = m.id;
    const st = staleTag(R);
    if (M.ok && st) { M.tag = st; M.tagCls = "a"; }
    return { m, M };
  }), [s, machines, now]);   // eslint-disable-line react-hooks/exhaustive-deps

  const errs = Object.keys(s?.errors || {});
  const running = auto && !paused;
  const runSys = running && !pinSys, runAi = running && !pinAi;
  const pinBtn = (on: boolean, set: (v: boolean) => void, key: string) => (
    <button className={`pin ${on ? "on" : ""}`} title={on ? "고정 풀기 (자동 넘김 다시 따름)" : "이 영역은 자동으로 넘기지 않기"}
      onClick={() => { set(!on); store.set(key, !on); }}>{on ? "📌 고정됨" : "📌 고정"}</button>
  );
  const provItem = (p: string) => {
    const X = ai[p];
    const today = Object.values(X.states).reduce((a, st) => a + (st.usage?.today?.total || 0), 0);
    const w = (X.limits?.windows || []).filter((x) => !x.estimated);
    const left = w.length ? Math.min(...w.map((x) => 100 - (x.used_pct ?? 0))) : null;
    return { label: X.label, sub: `오늘 ${fmtT(today)}${left != null ? ` · ${Math.round(left)}% 남음` : ""}`, warn: left != null && left <= 10 };
  };
  const openSession = (p: string) => (x: ViewSession | { session: string; machine_id?: string }) =>
    setSessionOpen({ pid: p, sid: x.session, machine: x.machine_id });

  return (
    <div className={`app area-${area}`}>
      <header>
        <div className="clock">
          <div className="time num">{hm(clock)}</div>
          <div className="date">{clock.toLocaleDateString("ko-KR", { month: "long", day: "numeric", weekday: "long" })}</div>
        </div>
        <nav className="areas">
          <button className={area === "sys" ? "on" : ""} onClick={() => { setArea("sys"); store.set("area", "sys"); }}>{Icon.monitor}컴퓨터</button>
          <button className={area === "ai" ? "on" : ""} onClick={() => { setArea("ai"); store.set("area", "ai"); }}>{Icon.spark}AI 사용량</button>
        </nav>
        <div className="right">
          <span className={`chip ${auto ? "on" : ""}`} onClick={() => { const v = !auto; setAuto(v); store.set("auto", v); lastTurn.current = Date.now(); setTurn((t) => t + 1); }}>
            <span className="d" />{auto ? `자동 넘김 ${AUTO_SEC}초${paused ? " · 멈춤" : ""}` : "자동 넘김 꺼짐"}
          </span>
          <button className="chip" title="테마 바꾸기: 일출·일몰 → 라이트 → 다크" onClick={cycleTheme}>
            {themeMode === "sun" ? `◐ 일출·일몰 · ${theme === "light" ? "낮" : "밤"}` : themeMode === "auto" ? `◐ 시간 · ${theme === "light" ? "낮" : "밤"}`
              : themeMode === "light" ? "☀ 라이트" : "☾ 다크"}
          </button>
          <span className={`chip ${error || errs.length ? "bad" : s ? "ok" : ""}`} title={errs.map((k) => `${k}: ${(s?.errors[k] || "").split("\n").slice(-2).join(" ")}`).join("\n")}>
            <span className="d" />{error ? "서버 연결 끊김" : errs.length ? `수집 오류 ${errs.length}건` : s ? "실시간" : "연결 중"}
          </span>
          {(() => {
            const al = s?.alerts || [];
            const unread = al.filter((a) => a.t > alertSeen).length;
            return (
              <button className={`chip ${unread ? "bad" : ""}`} title="최근 알림" onClick={() => {
                setShowAlerts(true); const t = al[0]?.t || Date.now() / 1000; setAlertSeen(t); store.set("alert-seen", t);
              }}><span className="d" />알림{unread ? ` ${unread}` : ""}</button>
            );
          })()}
          {upd?.newer && (
            <button className="chip upd" title="새 버전 — 설정 > 정보에서 업데이트" onClick={() => { try { localStorage.setItem("settings-page", "about"); } catch { /* 저장 불가 */ } setShowSettings(true); }}>
              ⬆ {upd.latest} 업데이트</button>
          )}
          <button className="chip gear" title="설정" onClick={() => setShowSettings(true)}>{Icon.gear}</button>
        </div>
      </header>

      <main>
        <section className="panel p-sys">
          <div className="phead">
            <div className="ptitle">{Icon.monitor}컴퓨터</div>
            <SegTabs index={sysIdx} onSelect={pickSys} running={runSys} durationSec={AUTO_SEC} progressKey={turn}
              items={views.map(({ m, M }) => ({ label: m.name, sub: M ? M.summary : "수집 중…", warn: !!M && machineAlert(M) }))} />
            {auto && views.length > 1 && pinBtn(pinSys, setPinSys, "pin-sys")}
          </div>
          <Views index={sysIdx} onSwipe={(d) => pickSys(sysIdx + d)} views={views.map(({ M }) => ({
            node: M ? <MachineView M={M} /> : <Muted>연결 중…</Muted>, stale: !!M && !M.ok,
          }))} />
          {curPid && ai[curPid] && <InsightsCard key={curPid} pid={curPid} label={ai[curPid].label} />}
        </section>

        <section className="panel p-ai">
          <div className="phead">
            <div className="ptitle">{Icon.spark}AI 사용량</div>
            {pids.length > 0 && (
              <SegTabs index={Math.max(0, pids.indexOf(curPid!))} onSelect={(i) => pickPid(pids[i])} running={false}
                durationSec={AUTO_SEC} progressKey={turn} items={pids.map(provItem)} />
            )}
            {auto && pinBtn(pinAi, setPinAi, "pin-ai")}
          </div>
          {P && curPid ? (
            <AiPanel key={curPid} pid={curPid} P={P} tab={curAiTab} onTab={(i) => pickAiTab(curPid, i)} names={names} now={now}
              running={runAi} durationSec={AUTO_SEC} progressKey={turn}
              onSession={openSession(curPid)} onBrowse={() => setBrowse(curPid)} />
          ) : <div className="card"><Muted>{s ? "감지된 AI 도구가 없습니다. (Claude Code · Codex CLI · Gemini CLI 기록 폴더를 찾지 못함)" : "연결 중…"}</Muted></div>}
        </section>
      </main>

      {showSettings && <Settings onClose={() => setShowSettings(false)} onWizard={() => { setShowSettings(false); setWizard(true); }} />}
      {showWizard && <Wizard s={s} onClose={() => { setWizard(false); setWizardSeen(true); }} />}
      {showAlerts && (
        <Overlay title="최근 알림" onClose={() => setShowAlerts(false)}>
          {(s?.alerts || []).length ? (s?.alerts || []).map((a, i) => (
            <div key={i} className="alert-row">
              <span className="when num">{new Date(a.t * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })}</span>
              <div><b>{a.title}</b><div className="muted">{a.message}</div></div>
            </div>
          )) : <Muted>아직 알림이 없어요. 종류별 켜기/끄기는 ⚙ 설정 → 알림에서 할 수 있어요.</Muted>}
        </Overlay>
      )}
      {browse && ai[browse] && (
        <SessionBrowser pid={browse} label={ai[browse].label} machines={ai[browse].machines} names={names} now={now}
          onClose={() => setBrowse(null)} onOpen={(x) => setSessionOpen({ pid: browse, sid: x.session, machine: x.machine_id })} />
      )}
      {sessionOpen && (
        <SessionModal pid={sessionOpen.pid} sid={sessionOpen.sid} machine={sessionOpen.machine} now={now}
          label={ai[sessionOpen.pid]?.label || sessionOpen.pid} onClose={() => setSessionOpen(null)} />
      )}
    </div>
  );
}
