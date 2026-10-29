"""Claude Code 플랜 한도와 열린 세션. 표준 라이브러리만 사용 (원격 PC에서도 그대로 실행됨)."""
import json
import os
import re
import subprocess
import time
from pathlib import Path

WEEK = 7 * 86400


def _pid_alive_set():
    """실행 중인 PID 집합. psutil이 없으면 tasklist 사용."""
    try:
        import psutil
        return set(psutil.pids())
    except ImportError:
        pass
    try:
        exe = "tasklist" if os.name == "nt" else "tasklist.exe"   # WSL에서는 tasklist.exe로 Windows 프로세스 확인
        out = subprocess.run([exe, "/FO", "CSV", "/NH"], capture_output=True,
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        pids = set()
        for line in out.decode("utf-8", "replace").splitlines():
            parts = line.split('","')
            if len(parts) > 1:
                try:
                    pids.add(int(parts[1].strip('"')))
                except ValueError:
                    pass
        return pids
    except Exception:
        return None


def _desktop_history_paths(home):
    """Claude 데스크톱 앱의 plan-usage-history.json 후보 (일반 설치 / MS Store 설치)."""
    home = Path(home)
    cands = [home / "AppData" / "Roaming" / "Claude" / "plan-usage-history.json"]
    pk = home / "AppData" / "Local" / "Packages"
    if pk.exists():
        cands += list(pk.glob("Claude_*/LocalCache/Roaming/Claude/plan-usage-history.json"))
    return [p for p in cands if p.exists()]


def _desktop_history(home):
    """데스크톱 앱이 기록하는 플랜 사용률 샘플 (fh=5시간 %, sd=7일 %). 여러 개면 합쳐서 시간순."""
    samples = []
    for p in _desktop_history_paths(home):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            samples += [x for x in d.get("samples", []) if x.get("u")]
        except Exception:
            pass
    return sorted(samples, key=lambda x: x["t"])


def _next_weekly_reset(anchor, now):
    """주간 리셋은 매주 같은 시각. 기준 시각(anchor)에서 7일 단위로 다음 리셋을 계산."""
    if not anchor:
        return None
    k = -(-(now - anchor) // WEEK)  # ceil
    return anchor + max(k, 0) * WEEK


def read_limits(weekly_anchor=None, home=None, claude_dir=None, desktop=True):
    """플랜 한도 원본 데이터. statusLine 브리지 값(리셋 시각 포함)이 더 새로우면 우선, 아니면 데스크톱 앱 기록.
    claude_dir: 다른 계정 폴더(CLAUDE_CONFIG_DIR), desktop=False 면 데스크톱 앱 기록은 보지 않음(기본 계정 것이라서)."""
    home = Path(home or Path.home())
    cdir = Path(claude_dir) if claude_dir else home / ".claude"
    bridge = None
    try:
        bridge = json.loads((cdir / "dashboard" / "rate_limits.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    hist = _desktop_history(home) if desktop else []
    last = hist[-1] if hist else None
    if bridge and (not last or bridge.get("captured_at", 0) >= last["t"] / 1000 - 600):
        return {"source": "statusline", "captured_at": bridge.get("captured_at"), "org": None,
                "five_hour": bridge.get("five_hour"), "seven_day": bridge.get("seven_day"),
                "history": [[x["t"] / 1000, x["u"].get("fh"), x["u"].get("sd")] for x in hist[-400:]]}
    if not last:
        return None
    cap = last["t"] / 1000
    return {"source": "desktop", "captured_at": cap, "org": last.get("org"),
            "five_hour": {"used_percentage": last["u"].get("fh"), "resets_at": None},
            "seven_day": {"used_percentage": last["u"].get("sd"),
                          "resets_at": _next_weekly_reset(weekly_anchor, cap)},
            "history": [[x["t"] / 1000, x["u"].get("fh"), x["u"].get("sd")] for x in hist[-400:]]}


def finalize_limits(raw, weekly_anchor=None, now=None):
    """표시용으로 가공: 리셋 지난 창은 0%, 남은 시간, 최근 24시간 추이."""
    if not raw:
        return None
    now = now or time.time()
    out = {"source": raw["source"], "captured_at": raw["captured_at"],
           "age_sec": int(now - (raw["captured_at"] or now)), "machine": raw.get("machine")}
    for key in ("five_hour", "seven_day"):
        w = raw.get(key)
        if not w or w.get("used_percentage") is None:
            continue
        reset = w.get("resets_at")
        passed = bool(reset and reset <= now)
        if key == "seven_day" and weekly_anchor and (passed or not reset):
            reset = _next_weekly_reset(weekly_anchor, now)
        length = 5 * 3600 if key == "five_hour" else WEEK
        left = reset - now if reset and reset > now else None
        out[key] = {
            "used_pct": 0.0 if passed else w.get("used_percentage"),
            "resets_at": reset,
            "reset_in_sec": int(left) if left is not None else None,
            "reset_passed": passed,
            # 페이스 비교용: 이번 창에서 시간이 얼마나 지났는지 (리셋 시각을 알 때만)
            "elapsed_pct": round(100 * (1 - left / length), 1) if left is not None and left <= length else None,
            "estimated": bool(w.get("estimated")),
        }
    out["history"] = [h for h in raw.get("history", []) if h[0] >= now - 86400]
    return out


def _bridge_status(claude_dir, sid):
    try:
        return json.loads((Path(claude_dir) / "dashboard" / "status" / f"{sid}.json")
                          .read_text(encoding="utf-8"))
    except Exception:
        return {}


def _tail_info(path, max_bytes=400_000):
    """세션 jsonl 끝부분에서 마지막 assistant usage(=현재 컨텍스트 크기)와 모델을 읽음."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - max_bytes))
            lines = f.read().splitlines()
    except OSError:
        return {}
    info = {}
    for raw in reversed(lines):
        if b'"usage"' not in raw:
            continue
        try:
            d = json.loads(raw)
        except Exception:
            continue
        if d.get("type") != "assistant" or d.get("isSidechain"):
            continue
        m = d.get("message") or {}
        u = m.get("usage") or {}
        if m.get("model") == "<synthetic>":
            continue
        info["model"] = m.get("model")
        info["context"] = (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                           + u.get("cache_read_input_tokens", 0) + u.get("output_tokens", 0))
        info["title"] = d.get("slug")
        break
    return info


class Sessions:
    def __init__(self, home=None, claude_dir=None):
        self.home = Path(home or Path.home())
        self.claude_dir = Path(claude_dir) if claude_dir else self.home / ".claude"
        self.path_cache = {}

    def _find_log(self, sid):
        p = self.path_cache.get(sid)
        if p and p.exists():
            return p
        for p in (self.claude_dir / "projects").glob(f"*/{sid}.jsonl"):
            self.path_cache[sid] = p
            return p
        return None

    def snapshot(self, usage_by_session=None):
        now = time.time()
        alive = _pid_alive_set()
        out = []
        for f in (self.claude_dir / "sessions").glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            pid = d.get("pid")
            if not pid or (alive is not None and pid not in alive):
                continue
            sid = d.get("sessionId")
            log = self._find_log(sid) if sid else None
            last_write = log.stat().st_mtime if log else None
            tail = _tail_info(log) if log else {}
            idle = now - last_write if last_write else None
            cwd = d.get("cwd") or ""
            name = re.split(r"[\\/]", cwd.rstrip("\\/"))[-1]
            st = _bridge_status(self.claude_dir, sid) if sid else {}
            # 상태: 최근 CLI는 세션 파일에 status(busy|idle|waiting)+waitingFor를 씀 → 우선 사용, 없으면 로그 기록 시각으로 추정
            status = d.get("status")
            if status == "waiting":
                state = "waiting"
            elif status in ("busy", "idle"):
                state = "working" if status == "busy" else "idle"
            else:
                state = "working" if idle is not None and idle < 20 else "idle"
            out.append({
                "pid": pid,
                "session": sid,
                "project": "(scratch)" if name.startswith("scratch-") else name,
                "entry": d.get("entrypoint") or d.get("kind"),
                "started": (d.get("startedAt") or 0) / 1000,
                "last_activity": last_write,
                "state": state,
                "waiting_for": d.get("waitingFor") if state == "waiting" else None,
                "state_since": (d.get("statusUpdatedAt") or 0) / 1000 or None,
                "model": tail.get("model"),
                "context": tail.get("context"),
                "title": None,                 # 아래 ClaudeProvider 가 로그의 세션 제목으로 채움
                "slug": tail.get("title"),     # 자동 생성 영문 이름 (예: warm-dreaming-thacker)
                "tokens": (usage_by_session or {}).get(sid),
                # 세션 이름: 데스크톱 앱이 붙인 제목(한글 등) → statusLine 브리지 이름
                "name": d.get("name") or st.get("session_name"),
                "ctx_pct": st.get("ctx_used_pct"),
                "cost_usd": st.get("cost_usd"),
                "model_name": st.get("model"),
                "effort": st.get("effort"),
            })
        out.sort(key=lambda s: -(s["last_activity"] or 0))
        return out
