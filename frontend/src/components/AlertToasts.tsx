// 화면 안 알림: 서버 알림(작업 끝·입력 대기·한도 등)이 새로 오면 위쪽에 유리 배너로 잠깐 보여 줌 + (켜 두면) 소리.
// 대시보드를 열어 둔 모든 기기(아이폰·아이패드 사파리 포함)에서 동작. 소리는 기기마다 설정 > 알림에서 켬.
import { useEffect, useRef, useState } from "react";

type Alert = { t: number; kind: string; title: string; message: string };
const SHOW_MS = 9000;

let ctx: AudioContext | null = null;
/** 아이폰은 사용자가 화면을 한 번 누른 뒤에만 소리를 낼 수 있음 → 첫 터치 때 오디오를 깨워 둠 */
export function unlockAudio() {
  try {
    const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    ctx = ctx || new AC();
    if (ctx.state === "suspended") ctx.resume();
  } catch { /* 소리 없음 */ }
}
export const soundOn = () => { try { return localStorage.getItem("alert-sound") === "1"; } catch { return false; } };

/** 짧은 두 음 (부드러운 알림음) */
export function chime() {
  if (!ctx) return;
  const now = ctx.currentTime;
  [[880, 0], [1320, 0.13]].forEach(([f, d]) => {
    const o = ctx!.createOscillator(), g = ctx!.createGain();
    o.type = "sine"; o.frequency.value = f;
    g.gain.setValueAtTime(0, now + d);
    g.gain.linearRampToValueAtTime(0.18, now + d + 0.02);
    g.gain.exponentialRampToValueAtTime(0.001, now + d + 0.45);
    o.connect(g).connect(ctx!.destination);
    o.start(now + d); o.stop(now + d + 0.5);
  });
}

export function AlertToasts({ alerts }: { alerts?: Alert[] }) {
  const last = useRef<number | null>(null);
  const [shown, setShown] = useState<Alert[]>([]);
  useEffect(() => {
    const on = () => { if (soundOn()) unlockAudio(); };
    window.addEventListener("pointerdown", on);
    return () => window.removeEventListener("pointerdown", on);
  }, []);
  useEffect(() => {
    if (!alerts) return;
    const newest = alerts.reduce((m, a) => Math.max(m, a.t), 0);
    if (last.current === null) { last.current = newest; return; }   // 처음 열었을 때 지난 알림은 띄우지 않음
    const fresh = alerts.filter((a) => a.t > last.current!).sort((a, b) => a.t - b.t);
    if (!fresh.length) return;
    last.current = newest;
    setShown((cur) => [...cur, ...fresh].slice(-3));
    if (soundOn()) chime();
    fresh.forEach((a) => setTimeout(() => setShown((cur) => cur.filter((x) => x !== a)), SHOW_MS));
  }, [alerts]);
  if (!shown.length) return null;
  return (
    <div className="toasts" role="status" aria-live="polite">
      {shown.map((a) => (
        <div key={a.t + a.title} className={`toast k-${a.kind}`} onClick={() => setShown((cur) => cur.filter((x) => x !== a))}>
          <i className="dot" />
          <div><b>{a.title}</b><span>{a.message}</span></div>
        </div>
      ))}
    </div>
  );
}
