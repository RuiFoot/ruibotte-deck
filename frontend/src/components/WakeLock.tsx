// 화면 켜 두기 (Screen Wake Lock): 아이폰·아이패드 등에서 대시보드를 보는 동안 화면이 꺼지지 않게.
// 브라우저 규칙상 HTTPS(보안 연결)에서만 동작 → http 로 열었으면 iOS 자동 잠금 설정 안내.
import { useEffect, useRef, useState } from "react";

type Sentinel = { released: boolean; release: () => Promise<void>; addEventListener: (t: string, f: () => void) => void };
const supported = () => "wakeLock" in navigator && window.isSecureContext;
const touch = () => window.matchMedia("(pointer: coarse)").matches;

export function WakeLockChip() {
  const [want, setWant] = useState(() => { try { return localStorage.getItem("wake-lock") === "1"; } catch { return false; } });
  const [held, setHeld] = useState(false);
  const [hint, setHint] = useState(false);
  const lock = useRef<Sentinel | null>(null);

  useEffect(() => {
    if (!want || !supported()) return;
    let alive = true;
    const acquire = async () => {
      if (document.visibilityState !== "visible" || (lock.current && !lock.current.released)) return;
      try {
        const s = await (navigator as Navigator & { wakeLock: { request: (t: "screen") => Promise<Sentinel> } }).wakeLock.request("screen");
        if (!alive) { s.release(); return; }
        lock.current = s; setHeld(true);
        s.addEventListener("release", () => setHeld(false));
      } catch { setHeld(false); }
    };
    acquire();
    // 다른 앱에 갔다 오면 잠금이 풀리므로 다시 잡음
    document.addEventListener("visibilitychange", acquire);
    return () => { alive = false; document.removeEventListener("visibilitychange", acquire); lock.current?.release().catch(() => {}); lock.current = null; setHeld(false); };
  }, [want]);

  if (!touch()) return null;   // 마우스를 쓰는 PC에서는 숨김
  const toggle = () => {
    if (!supported()) { setHint(!hint); return; }
    const v = !want; setWant(v);
    try { localStorage.setItem("wake-lock", v ? "1" : "0"); } catch { /* 저장 불가 */ }
  };
  return (
    <>
      <button className={`chip ${want && held ? "ok" : ""}`} title="화면 켜 두기" onClick={toggle}>
        <span className="d" />{want && held ? "화면 켜 둠" : "화면 켜 두기"}
      </button>
      {hint && (
        <div className="wl-hint" onClick={() => setHint(false)}>
          지금 연결(http)에서는 브라우저가 화면 켜 두기를 막아요. <b>HTTPS 주소</b>(설정 › 일반 › 다른 기기에서 보기)로 열거나,
          아이폰 <b>설정 › 디스플레이 및 밝기 › 자동 잠금 › 안 함</b>으로 바꿔 주세요.
        </div>
      )}
    </>
  );
}
