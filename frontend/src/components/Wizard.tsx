// 첫 실행 마법사: 이 PC 감지 결과 → 감지된 AI 도구 → (선택) 원격 PC 추가 → 화면·자동 실행
import { useState } from "react";
import type { State } from "../types";
import { C, api, fmtB, fmtT, shortCpu, usePoll } from "../lib";
import { Muted } from "./ui";
import { Overlay } from "./SessionModal";
import { AddMachine, DisplayAndApp } from "./Settings";

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
          <p>이 PC에서 찾은 AI 코딩 도구예요. 각 도구가 남기는 기록 파일을 <b>읽기만</b> 하고, 로그인 토큰은 읽거나 저장하지 않아요.
            원격 PC를 추가하면 그 PC의 도구도 자동으로 찾아요.</p>
          {TOOLS.map(([pid, label, path]) => {
            const P = ai[pid];
            const here = !!P && P.machines.includes("local");
            const today = here ? P!.states.local?.usage?.today?.total : null;
            return (
              <div key={pid} className="mrow">
                <span className={`dot ${here ? "on" : ""}`} />
                <div style={{ flex: 1 }}><div className="nm">{label}</div>
                  <div className="s">{here ? `감지됨${today != null ? ` · 오늘 ${fmtT(today)} 토큰` : ""}` : `이 PC에서 찾지 못함 (${path})`}
                    {P && !here ? ` · 원격 PC ${P.machines.length}대에서 감지` : ""}</div></div>
              </div>
            );
          })}
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
    <Overlay title="RuiBotte Deck 시작하기" onClose={finish}>
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
