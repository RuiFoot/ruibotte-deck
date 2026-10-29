// 휴대폰·태블릿 알림(Web Push) 켜기. 아이폰·아이패드 규칙: ① HTTPS ② 홈 화면에 추가한 앱 ③ 사용자가 버튼을 눌러 허용.
// 이 PC가 만든 로컬 인증서를 기기에 한 번 설치·신뢰하면 https://<PC 주소>:<포트+1> 이 경고 없이 열림 (secure.py).
import { useEffect, useState } from "react";
import { C, api, authToken, usePoll } from "../lib";
import { Group, Msg, Row } from "./Settings";

interface Info { public_key: string; https_port: number; lan: boolean; secure: boolean; devices: number; ready?: boolean }
interface Device { id: number; device: string; created_at: number; last_ok: number | null; service: string }

const isIOS = () => /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const standalone = () => window.matchMedia("(display-mode: standalone)").matches
  || (navigator as Navigator & { standalone?: boolean }).standalone === true;
const pushOK = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const deviceName = () => {
  const ua = navigator.userAgent;
  return /iPad/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) ? "iPad"
    : /iPhone/.test(ua) ? "iPhone" : /Android/.test(ua) ? "Android" : /Mac/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : "기기";
};
const b64 = (s: string) => {
  const p = "=".repeat((4 - (s.length % 4)) % 4), raw = atob((s + p).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
};

async function currentSub() {
  if (!pushOK()) return null;
  const reg = await navigator.serviceWorker.getRegistration("/");
  return reg ? reg.pushManager.getSubscription() : null;
}

export function PhonePush() {
  const [tick, setTick] = useState(0);
  const { data: I } = usePoll<Info>(`/api/push/info?_=${tick}`, 600000);
  const { data: D } = usePoll<{ devices: Device[] }>(`/api/push/devices?_=${tick}`, 600000);
  const [subbed, setSubbed] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { currentSub().then((s) => setSubbed(!!s)).catch(() => setSubbed(false)); }, [tick]);
  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true); setMsg(null);
    try { await fn(); if (ok) setMsg({ ok: true, text: ok }); } catch (e) { setMsg({ ok: false, text: String((e as Error).message || e) }); }
    setBusy(false); setTick((t) => t + 1);
  };
  if (!I) return null;

  const enable = () => run(async () => {
    // 아이폰: 사용자가 누른 그 순간에 권한을 물어야 함
    const perm = await Notification.requestPermission();
    if (perm !== "granted") throw new Error("알림이 허용되지 않았어요 — 설정 앱에서 이 앱의 알림을 켜 주세요");
    const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    await navigator.serviceWorker.ready;
    const sub = (await reg.pushManager.getSubscription())
      || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(I.public_key) });
    await api("/api/push/subscribe", "POST", { subscription: sub.toJSON(), device: deviceName() });
  }, "이 기기에서 알림을 받아요. '시험'으로 확인해 보세요.");
  const disable = () => run(async () => {
    const sub = await currentSub();
    if (sub) { await api("/api/push/unsubscribe", "POST", { endpoint: sub.endpoint }); await sub.unsubscribe(); }
  }, "이 기기 알림을 껐어요.");

  const local = ["localhost", "127.0.0.1"].includes(location.hostname);
  const httpsUrl = `https://${location.hostname}:${I.https_port}/${authToken() ? `?t=${encodeURIComponent(authToken()!)}` : ""}`;
  let body;
  if (local) {
    body = <Row label="휴대폰에서 설정해요" sub="설정 › 일반 › 다른 기기에서 보기의 QR로 휴대폰에서 열고, 거기서 설정 › 알림 › 휴대폰 알림을 따라 하세요." />;
  } else if (!I.lan) {
    body = <Row label="PC에서 '다른 기기에서 보기'를 먼저 켜 주세요" />;
  } else if (!I.secure) {
    body = <>
      <Row label="1. 인증서 받기" sub="이 PC가 만든 인증서예요. 받으면 '프로파일이 다운로드됨'이 떠요.">
        <a className="g-btn" href="/ca.crt">받기</a>
      </Row>
      <Row label="2. 설치" sub="설정 앱 › 일반 › VPN 및 기기 관리 › 'RuiBotte Deck Local CA' › 설치" />
      <Row label="3. 신뢰 켜기" sub="설정 앱 › 일반 › 정보 › 인증서 신뢰 설정 › 'RuiBotte Deck Local CA' 켜기" />
      <Row label="4. 보안 주소로 열기" sub="경고 없이 열리면 성공이에요. 거기서 이 화면으로 다시 오세요.">
        <a className="g-btn primary" href={httpsUrl}>열기</a>
      </Row>
    </>;
  } else if (!pushOK() || (isIOS() && !standalone())) {
    body = <Row label="5. 홈 화면에 추가" sub="사파리 공유 버튼 › 홈 화면에 추가 → 그 아이콘으로 열어 이 화면에서 '알림 켜기'를 누르세요. (아이폰·아이패드는 홈 화면 앱에서만 알림이 돼요)" />;
  } else {
    body = subbed
      ? <Row dot label="이 기기 알림 켜짐" sub="작업 끝·입력 대기·한도 등 PC와 같은 알림이 와요">
          <button className="g-btn" disabled={busy} onClick={() => run(() => api("/api/push/test", "POST"), "보냈어요. 잠시 뒤 알림이 와요.")}>시험</button>
          <button className="g-btn" disabled={busy} onClick={disable}>끄기</button>
        </Row>
      : <Row label="이 기기에서 알림 받기" sub="누르면 알림 허용을 물어요">
          <button className="g-btn primary" disabled={busy} onClick={enable}>알림 켜기</button>
        </Row>;
  }
  return (
    <Group title="휴대폰 · 태블릿 알림" footer={<>아이폰·아이패드는 보안 연결(HTTPS)과 홈 화면 앱에서만 알림이 돼요. 알림 내용은 암호화돼 Apple 푸시 서버를 거쳐 기기로 가요.
      <Msg m={msg} /></>}>
      {body}
      {(D?.devices || []).map((d) => (
        <Row key={d.id} label={`${d.device} — 알림 받는 기기`} sub={`${d.service}${d.last_ok ? ` · 마지막 전송 ${new Date(d.last_ok * 1000).toLocaleString("ko-KR", { hour12: false })}` : ""}`}>
          <button className="g-btn" disabled={busy} onClick={() => run(() => api(`/api/push/devices/${d.id}`, "DELETE"))}>삭제</button>
        </Row>
      ))}
      {msg === null && I.secure && !I.ready && <Row label={<span style={{ color: C.amber }}>인증서 준비 중</span>} />}
    </Group>
  );
}
