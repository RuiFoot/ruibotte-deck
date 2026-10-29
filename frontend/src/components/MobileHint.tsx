// 아이폰·아이패드(사파리)로 QR을 찍어 열었을 때: 전체 화면 버튼(지원하면) + '홈 화면에 추가' 안내 (한 번 닫으면 다시 안 뜸)
import { useEffect, useState } from "react";

const isIOS = () => /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const standalone = () => window.matchMedia("(display-mode: standalone)").matches
  || (navigator as Navigator & { standalone?: boolean }).standalone === true;

/** 헤더의 '전체 화면' 칩: 브라우저가 지원하고(아이패드 사파리·PC) 아직 앱으로 설치하지 않았을 때만 */
export function FullscreenChip() {
  const [on, setOn] = useState(!!document.fullscreenElement);
  useEffect(() => {
    const f = () => setOn(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", f);
    return () => document.removeEventListener("fullscreenchange", f);
  }, []);
  const el = document.documentElement as HTMLElement & { webkitRequestFullscreen?: () => void };
  const can = (document.fullscreenEnabled || !!el.webkitRequestFullscreen) && !standalone() && location.hostname !== "localhost" && location.hostname !== "127.0.0.1";
  if (!can) return null;
  const toggle = () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else if (el.requestFullscreen) el.requestFullscreen().catch(() => { /* 사용자가 거절 */ });
    else el.webkitRequestFullscreen?.();
  };
  return <button className="chip" title="전체 화면" onClick={toggle}>{on ? "⤡ 창 모드" : "⛶ 전체 화면"}</button>;
}

/** 아이폰 사파리: 웹 페이지는 전체 화면이 안 되므로 '홈 화면에 추가' 안내 */
export function AddToHomeHint() {
  const [show, setShow] = useState(() => {
    try { return isIOS() && !standalone() && !localStorage.getItem("a2hs-dismissed"); } catch { return false; }
  });
  if (!show) return null;
  const close = () => { setShow(false); try { localStorage.setItem("a2hs-dismissed", "1"); } catch { /* 저장 불가 */ } };
  return (
    <div className="a2hs">
      <div>
        <b>앱처럼 전체 화면으로 보기</b>
        <span>사파리 아래쪽 <b>공유</b> 버튼 <span className="ios-share" aria-hidden>⬆︎</span> → <b>홈 화면에 추가</b>를 누르면 주소창 없이 열려요.</span>
      </div>
      <button className="x" onClick={close} aria-label="닫기">✕</button>
    </div>
  );
}
