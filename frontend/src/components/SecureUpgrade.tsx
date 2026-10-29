// QR(http)로 열었을 때: 이 기기에 인증서가 이미 설치돼 있으면 보안 주소(https)로 자동으로 옮겨 감.
// 아직이면 http 로 그대로 보여 주고, '보안 연결 설정하기' 안내(알림·화면 켜 두기에 필요)를 띄움.
import { useEffect, useState } from "react";
import { authToken } from "../lib";

const standalone = () => window.matchMedia("(display-mode: standalone)").matches
  || (navigator as Navigator & { standalone?: boolean }).standalone === true;
const local = () => ["localhost", "127.0.0.1", "::1"].includes(location.hostname);

export function SecureUpgrade({ onSetup }: { onSetup: () => void }) {
  const [hint, setHint] = useState(false);
  useEffect(() => {
    if (location.protocol !== "http:" || local()) return;
    let alive = true;
    (async () => {
      try {
        const h = await (await fetch("/api/health", { cache: "no-store" })).json();
        if (!h.https_port) return;
        const base = `https://${location.hostname}:${h.https_port}`;
        const ctl = new AbortController();
        const timer = setTimeout(() => ctl.abort(), 3000);
        // 인증서를 믿지 않는 기기면 여기서 실패 → http 로 계속
        const ok = await fetch(`${base}/api/health`, { cache: "no-store", signal: ctl.signal }).then((r) => r.ok).catch(() => false);
        clearTimeout(timer);
        if (!alive) return;
        // 홈 화면 앱은 다른 주소로 옮기면 앱 밖(사파리)으로 열리므로 옮기지 않고 안내만
        if (ok && !standalone()) {
          const t = authToken();
          location.replace(`${base}${location.pathname}${t ? `?t=${encodeURIComponent(t)}` : ""}`);
          return;
        }
        try { if (localStorage.getItem("secure-hint-dismissed")) return; } catch { /* 저장 불가 */ }
        setHint(true);
      } catch { /* 서버 확인 실패 → 그대로 */ }
    })();
    return () => { alive = false; };
  }, []);
  if (!hint) return null;
  const close = () => { setHint(false); try { localStorage.setItem("secure-hint-dismissed", "1"); } catch { /* 저장 불가 */ } };
  return (
    <div className="a2hs">
      <div>
        <b>보안 연결로 바꾸면 알림·화면 켜 두기가 돼요</b>
        <span>지금은 일반 연결(http)이에요. 이 기기에 인증서를 한 번 설치하면 다음부터 같은 QR로 열어도 자동으로 보안 연결이 돼요.</span>
        <span><button className="link" onClick={onSetup}>보안 연결 설정하기 ›</button></span>
      </div>
      <button className="x" onClick={close} aria-label="닫기">✕</button>
    </div>
  );
}
