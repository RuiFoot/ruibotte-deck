// 첫 실행 마법사: 이 PC 감지 결과 → 감지된 AI 도구 → (선택) 원격 PC 추가 → 화면·자동 실행
import { useState } from "react";
import type { State } from "../types";
import { C, api, fmtB, fmtT, shortCpu, usePoll } from "../lib";
import { Muted } from "./ui";
import { Overlay } from "./SessionModal";
import { AddMachine, DisplayAndApp, cliLogin } from "./Settings";

const CAN_LOGIN = new Set(["claude", "codex"]);   // 대시보드에서 설치·로그인 창을 열 수 있는 도구
const TOOLS: [string, string, string][] = [
  ["claude", "Claude Code", "~/.claude"],
  ["codex", "GPT (Codex CLI)", "~/.codex 또는 $CODEX_HOME"],
  ["gemini", "Gemini CLI", "~/.gemini"],
];

export function Wizard({ s, onClose }: { s: State | null; onClose: () => void }) {
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  const { data: M } = usePoll<{ machines: { id: string; name: string; kind: string; online: boolean; ssh_target?: string }[] }>(`/api/machines?_=${tick}`, 5000);
  const { data: PS } = usePoll<Record<string, { enabled: boolean; auto?: boolean }>>(`/api/providers/settings?_=${tick}`, 5000);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true); setMsg(null);
    try { await fn(); if (ok) setMsg({ ok: true, text: ok }); } catch (e) { setMsg({ ok: false, text: String((e as Error).message || e) }); }
    setBusy(false); setTick((t) => t + 1);
  };
  const L = s?.local;
  const ai = s?.ai || {};
  const finish = async () => {
    setBusy(true);
    try { await api("/api/settings", "PUT", { "setup.done": Math.floor(Date.now() / 1000) }); } catch { /* 저장 실패해도 닫음 */ }
    setBusy(false);
    onClose();
  };
  const steps = [
    {
      title: "이 PC",
      body: (
        <>
          <p>대시보드는 이 PC의 상태를 바로 보여줘요. 설치할 것도, 따로 설정할 것도 없어요.</p>
          {L ? (
            <div className="info-grid num" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
              <div><span>컴퓨터 이름</span><b>{L.host}</b></div>
              <div><span>운영체제</span><b>{(L.spec?.os_name || "").replace("Microsoft ", "") || "–"}</b></div>
              <div><span>CPU</span><b>{shortCpu(L.spec?.cpu_name) || "–"} · {L.spec?.cores}C/{L.spec?.threads}T</b></div>
              <div><span>메모리</span><b>{fmtB(L.mem.total)}</b></div>
            </div>
          ) : <Muted>이 PC 정보를 모으는 중…</Muted>}
        </>
      ),
    },
    {
      title: "AI 도구",
      body: (
        <>
          <p>최근에 쓴 AI 코딩 도구는 자동으로 켜져요. 각 도구의 기록 파일을 <b>읽기만</b> 하고, 로그인 토큰은 복사·저장하지 않아요.
            아직 없는 도구는 여기서 바로 설치·로그인할 수 있고, 원격 PC를 추가하면 그 PC의 도구도 자동으로 찾아요.</p>
          {TOOLS.map(([pid, label, path]) => {
            const P = ai[pid];
            const here = !!P && P.machines.includes("local");
            const today = here ? P!.states.local?.usage?.today?.total : null;
            const on = !!PS?.[pid]?.enabled;
            return (
              <div key={pid} className="mrow">
                <span className={`dot ${on ? "on" : ""}`} />
                <div style={{ flex: 1 }}><div className="nm">{label}</div>
                  <div className="s">{!on ? "꺼짐 · " : PS?.[pid]?.auto ? "자동으로 켜짐 · " : ""}
                    {here ? `이 PC에서 사용 중${today != null ? ` · 오늘 ${fmtT(today)} 토큰` : ""}` : `이 PC에서 찾지 못함 (${path})`}
                    {P && !here ? ` · 원격 PC ${P.machines.length}대에서 감지` : ""}</div></div>
                <div className="btns">
                  {!here && CAN_LOGIN.has(pid) && (
                    <button disabled={busy} onClick={() => act(async () => {
                      await api(`/api/providers/${pid}`, "PUT", { enabled: true });
                      await cliLogin({ provider: pid, label: "", default: true });
                    }, "창을 열었어요. 설치(필요하면)와 브라우저 로그인을 마치면 이 도구가 집계돼요.")}>설치·로그인</button>
                  )}
                  <button disabled={busy || !PS} onClick={() => act(() => api(`/api/providers/${pid}`, "PUT", { enabled: !on }))}>{on ? "끄기" : "켜기"}</button>
                </div>
              </div>
            );
          })}
          {msg && <div style={{ color: msg.ok ? C.green : C.red, fontSize: ".85em", marginTop: ".5em" }}>{msg.text}</div>}
        </>
      ),
    },
    {
      title: "원격 PC (선택)",
      body: (
        <>
          <p>다른 PC도 함께 보려면 SSH로 추가하세요. 키 인증만 쓰고 비밀번호는 저장하지 않아요. 원격 PC에는 아무것도 설치하지 않고,
            연결할 때마다 작은 수집 프로그램을 보내 실행해요(Python 3 필요, Windows는 WSL 또는 Python).</p>
          {(M?.machines || []).filter((m) => m.kind === "ssh").map((m) => (
            <div key={m.id} className="mrow"><span className={`dot ${m.online ? "on" : ""}`} />
              <div style={{ flex: 1 }}><div className="nm">{m.name}</div><div className="s">{m.ssh_target} · {m.online ? "연결됨" : "연결 중…"}</div></div></div>
          ))}
          <AddMachine onDone={() => setTick((t) => t + 1)} />
        </>
      ),
    },
    {
      title: "화면 · 실행",
      body: (
        <>
          <p>트레이 아이콘의 '대시보드 열기'가 어느 화면에 띄울지, 로그인할 때 자동으로 켤지 정해요. 나중에 ⚙ 설정에서 바꿀 수 있어요.</p>
          <DisplayAndApp />
        </>
      ),
    },
  ];
  const last = step === steps.length - 1;
  return (
    <Overlay title="RuiBotte Deck 시작하기" onClose={finish} modal>
      <div className="wizard">
        <div className="steps">{steps.map((_, i) => <span key={i} className={i <= step ? "on" : ""} />)}</div>
        <h2>{step + 1}. {steps[step].title}</h2>
        {steps[step].body}
        <div className="nav">
          {step > 0 && <button onClick={() => setStep(step - 1)}>이전</button>}
          {!last && <button onClick={finish} style={{ color: C.dim }}>건너뛰기</button>}
          <button className="primary" disabled={busy} onClick={() => (last ? finish() : setStep(step + 1))}>{last ? "시작하기" : "다음"}</button>
        </div>
      </div>
    </Overlay>
  );
}
