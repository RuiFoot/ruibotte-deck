// RuiBotte Deck 서비스 워커: 휴대폰 알림(Web Push)만 처리. 화면 파일은 캐시하지 않음(새 버전이 바로 보이도록).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { title: "RuiBotte Deck", body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "RuiBotte Deck", {
    body: d.body || "",
    icon: "/icon-192.png?v=2",
    badge: "/icon-192.png?v=2",
    tag: d.tag || undefined,
    data: { url: d.url || "/" },
  }));
});

// 알림을 누르면 열려 있는 대시보드로, 없으면 새로 열기
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((ws) => {
    for (const w of ws) if ("focus" in w) return w.focus();
    return self.clients.openWindow((e.notification.data && e.notification.data.url) || "/");
  }));
});
