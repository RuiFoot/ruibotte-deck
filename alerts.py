"""알림 엔진: 화면 상태를 5초마다 보고 '한 번만' 알릴 일을 골라 Windows 알림(트레이)으로 보냄.

- 세션 작업 끝: '작업 중'이 1분 이상 이어지던 세션이 '대기'로 바뀜
- 입력 대기: 세션이 승인·질문 답을 15초 넘게 기다림 (세션 파일 status=waiting)
- 한도 리셋: 이번 창에서 쓴 적이 있는 한도가 리셋됨
- 한도: 남은 양 10% 이하 (창·리셋 주기마다 한 번)
- 원격 PC: 1분 넘게 끊김 / 다시 연결됨
- 디스크: 95% 이상 (같은 디스크 12시간에 한 번)
- CPU: 90% 이상이 5분 이어짐 (같은 PC 1시간에 한 번)
- 컨텍스트: 세션 컨텍스트 90% 이상 (세션마다 한 번, 창 크기는 추정)
종류별 켜기/끄기는 설정 `notify.<종류>`. 최근 30건은 화면에 보여 주려고 보관.
"""
import time
from collections import deque

KINDS = {
    "session_done": "세션 작업 끝",
    "waiting": "입력 대기 (승인·질문)",
    "limit_reset": "한도 리셋",
    "limits": "한도 10% 이하",
    "machine": "원격 PC 연결",
    "disk": "디스크 부족",
    "cpu": "CPU 고부하",
    "context": "컨텍스트 90%",
}
WORK_MIN_SEC = 60
OFFLINE_SEC = 60
CPU_HIGH = 90
CPU_SUSTAIN_SEC = 300
DISK_FULL = 0.95
WAIT_SEC = 15
WAIT_LABEL = {"dialog open": "승인·선택을 기다려요", "input needed": "입력이 필요해요",
              "sandbox request": "샌드박스 허용을 기다려요", "worker request": "작업자 요청을 기다려요",
              "goal proposal": "목표 확인을 기다려요"}
LIMIT_LABEL = {"five_hour": "5시간 한도", "seven_day": "주간 한도", "one_day": "일간 한도"}


def context_window(model, context):
    """세션 컨텍스트 창 크기 추정: 최근 모델(4.6+/5.x)은 1M, 이미 20만을 넘었으면 1M, 그 외 20만."""
    m = (model or "").lower()
    if (context or 0) > 200_000 or "[1m]" in m or any(x in m for x in ("-5", "4-6", "4-7", "4-8")):
        return 1_000_000
    return 200_000


def context_pct(s):
    if s.get("ctx_pct") is not None:
        return float(s["ctx_pct"])
    if s.get("context"):
        return 100.0 * s["context"] / context_window(s.get("model_name") or s.get("model"), s["context"])
    return None


class Alerts:
    def __init__(self, send, enabled):
        self.send = send              # (title, message) -> None
        self.enabled = enabled        # kind -> bool
        self.log = deque(maxlen=30)
        self.seen = {}                # 한 번만 알릴 키 -> 시각
        self.work_since = {}          # (pid, mid, sid) -> 작업 시작 시각
        self.offline_since = {}       # mid -> 끊긴 시각
        self.offline_told = set()
        self.cpu_since = {}           # mid -> 고부하 시작 시각
        self.wait_since = {}          # (pid, mid, sid) -> 입력 대기 시작 시각
        self.windows = {}             # (pid, 창) -> (리셋 시각, 사용 %) 직전 관찰
        self.primed = False           # 첫 관찰에서는 '바뀜'을 알리지 않음

    def _fire(self, kind, key, title, msg, cooldown=None):
        now = time.time()
        k = (kind, key)
        last = self.seen.get(k)
        if last is not None and (cooldown is None or now - last < cooldown):
            return
        self.seen[k] = now
        if not self.enabled(kind):
            return
        self.log.appendleft({"t": now, "kind": kind, "title": title, "message": msg})
        try:
            self.send(title, msg)
        except Exception:
            pass

    def check(self, ai, machines, systems, local, names):
        """ai: /api/state 의 ai, machines: [{id,name,kind,online}], systems: {mid: RemoteState}, local: 이 PC 상태."""
        now = time.time()
        # --- 세션 작업 끝 · 컨텍스트 ---
        live = set()
        for pid, P in (ai or {}).items():
            for mid, st in (P.get("states") or {}).items():
                for s in st.get("sessions") or []:
                    sid = s.get("session")
                    if not sid:
                        continue
                    k = (pid, mid, sid)
                    live.add(k)
                    title = s.get("name") or s.get("title") or s.get("project") or sid[:8]
                    state = s.get("state")
                    if state == "waiting":
                        since = self.wait_since.setdefault(k, now)
                        if now - since >= WAIT_SEC:
                            self._fire("waiting", (k, int(since)), f"입력 대기 · {title}",
                                       f"{names(mid)} · {WAIT_LABEL.get(s.get('waiting_for'), s.get('waiting_for') or '응답을 기다려요')}")
                    else:
                        self.wait_since.pop(k, None)
                    if state == "working":
                        self.work_since.setdefault(k, now)
                    elif state == "waiting":
                        pass               # 승인·답을 기다리는 중: 작업이 끝난 것이 아님
                    elif k in self.work_since:
                        started = self.work_since.pop(k)
                        if self.primed and now - started >= WORK_MIN_SEC:
                            dur = int((now - started) // 60)
                            self._fire("session_done", f"{k}:{int(started)}", f"작업 끝 · {title}",
                                       f"{names(mid)} · {s.get('project') or ''} · {dur}분 작업", cooldown=0)
                    pct = context_pct(s)
                    if pct is not None and pct >= 90:
                        self._fire("context", k, f"컨텍스트 {round(pct)}% · {title}",
                                   f"{names(mid)} · 곧 자동 정리(압축)될 수 있어요")
                # --- 한도 ---
            lim = P.get("limits") or {}
            for w in lim.get("windows") or []:
                self._reset_check(pid, P.get("label", pid), w, now)
                if w.get("estimated") or w.get("used_pct") is None:
                    continue
                left = 100 - w["used_pct"]
                if left <= 10:
                    self._fire("limits", (pid, w.get("key"), w.get("resets_at")),
                               f"{P.get('label', pid)} 한도 {round(left)}% 남음",
                               f"{w.get('key')} 창 · 리셋까지 {int((w.get('reset_in_sec') or 0) // 60)}분")
        for d in (self.work_since, self.wait_since):
            for k in list(d):
                if k not in live:      # 세션이 닫힘: 작업 끝으로 보지 않고 잊음
                    d.pop(k, None)
        # --- 원격 PC 연결 ---
        for m in machines or []:
            if m.get("kind") != "ssh":
                continue
            mid = m["id"]
            if m.get("online"):
                self.offline_since.pop(mid, None)
                if mid in self.offline_told:
                    self.offline_told.discard(mid)
                    self.seen.pop(("machine", (mid, "off")), None)
                    self._fire("machine", (mid, "on", int(now)), f"{m['name']} 다시 연결됨", "원격 PC 수집을 다시 시작했어요")
            elif self.primed:
                since = self.offline_since.setdefault(mid, now)
                if now - since >= OFFLINE_SEC and mid not in self.offline_told:
                    self.offline_told.add(mid)
                    self._fire("machine", (mid, "off"), f"{m['name']} 연결 끊김", "1분 넘게 원격 PC에서 응답이 없어요")
        # --- 디스크 · CPU ---
        sysmap = {"local": (names("local"), (local or {}).get("cpu"),
                            [(d["name"], d["used"] / d["total"]) for d in (local or {}).get("disks") or [] if d.get("total")])}
        for mid, R in (systems or {}).items():
            if not R or not R.get("ok"):
                continue
            w = R.get("windows") or {}
            sysmap[mid] = (names(mid), w.get("cpu"),
                           [(d["name"], 1 - d["free"] / d["size"]) for d in w.get("disks") or [] if d.get("size")])
        for mid, (nm, cpu, disks) in sysmap.items():
            for dn, frac in disks:
                if frac >= DISK_FULL:
                    self._fire("disk", (mid, dn), f"{nm} 디스크 {dn} {round(frac * 100)}%",
                               "남은 공간이 5% 이하예요", cooldown=12 * 3600)
            if cpu is not None and cpu >= CPU_HIGH:
                since = self.cpu_since.setdefault(mid, now)
                if now - since >= CPU_SUSTAIN_SEC:
                    self._fire("cpu", mid, f"{nm} CPU {round(cpu)}%", "5분 넘게 90% 이상이에요", cooldown=3600)
            else:
                self.cpu_since.pop(mid, None)
        self.primed = True

    def _reset_check(self, pid, label, w, now):
        """한도 창의 리셋 시각이 지나면(다음 창으로 바뀌거나 '리셋됨') 한 번 알림. 쓴 적 없는 창은 알리지 않음."""
        key = w.get("key")
        if key not in LIMIT_LABEL or w.get("estimated"):
            return
        resets, used = w.get("resets_at"), w.get("used_pct")
        prev = self.windows.get((pid, key))
        if resets and not w.get("reset_passed"):
            self.windows[(pid, key)] = (resets, used)
        if not prev or not prev[0]:
            return
        p_reset, p_used = prev
        crossed = now >= p_reset and (w.get("reset_passed") or (resets and resets > p_reset) or (used or 0) < (p_used or 0))
        if crossed and (p_used or 0) >= 1:
            self._fire("limit_reset", (pid, key, int(p_reset)), f"{label} {LIMIT_LABEL[key]} 리셋",
                       f"다시 100% 쓸 수 있어요 (리셋 전 {round(p_used)}% 사용)")
