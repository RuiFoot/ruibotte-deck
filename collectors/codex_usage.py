"""GPT Codex CLI 사용량 · 세션 · 정확한 한도 ($CODEX_HOME 또는 ~/.codex). 표준 라이브러리만 사용.

기록: sessions/YYYY/MM/DD/rollout-*.jsonl (오래된 세션은 .jsonl.zst 로 압축됨 → Python 3.14+ 의 compression.zstd 로 읽음,
없으면 압축 파일은 건너뜀), archived_sessions/.
줄 형식: {"timestamp": ISO, "type": ..., "payload": {...}}
  session_meta.payload: id(세션), cwd, creator_account_id(계정)
  turn_context.payload: model
  event_msg.payload.type == "token_count": info.last_token_usage(이번 응답), info.total_token_usage(세션 누적),
      info.model_context_window, rate_limits{primary, secondary: {used_percent, window_minutes, resets_at|resets_in_seconds},
      plan_type, credits}
같은 누적값이 반복되는 줄이 있어 '세션 + 누적 total_tokens'를 레코드 키로 써서 중복을 없앤다.
OpenAI 방식: input_tokens 에 cached_input_tokens 가 포함되고, output_tokens 에 reasoning_output_tokens 가 포함됨.
"""
import json
import time
from pathlib import Path

try:
    from collectors.claude_usage import UsageLog, _basename, _parse_ts, account_hash
except ImportError:   # 원격 에이전트: claude_usage.py 가 앞에 이어 붙어 있음
    pass

try:
    from compression import zstd as _zstd   # Python 3.14+
except ImportError:
    _zstd = None

CODEX_LIVE_SEC = 20          # 이 시간 안에 기록이 있으면 '작업 중'
CODEX_SESSION_HOURS = 6      # 세션 목록에 보여 줄 최근 활동 범위


class CodexUsage(UsageLog):
    provider = "codex"

    def __init__(self, root, pricing=None, cache_path=None, keep_days=8):
        super().__init__(root, pricing, cache_path, keep_days)
        self.account = None
        self.rate = None          # 가장 최근 rate_limits (+ 기록 시각)
        self._names = (0.0, {})

    def _iter_files(self):
        for sub in ("sessions", "archived_sessions"):
            d = self.dir / sub
            if d.is_dir():
                yield from d.rglob("rollout-*.jsonl")
                if _zstd:
                    yield from d.rglob("rollout-*.jsonl.zst")

    def _scan(self):
        self._scan_jsonl()
        # 계정·한도는 가장 최근 파일 상태에서 (캐시로 다시 시작해도 파일 상태에 남아 있음)
        newest = max(self.fstate.values(), key=lambda s: s.get("last") or 0, default=None)
        if newest and newest.get("acct"):
            self.account = newest["acct"]
        rated = max((s for s in self.fstate.values() if s.get("rate")), key=lambda s: s["rate_t"], default=None)
        if rated:
            self.rate = {"t": rated["rate_t"], **rated["rate"]}

    def _read_new(self, path, start, size):
        if path.name.endswith(".zst"):
            if start == size:
                return None, size
            with open(path, "rb") as f:
                return _zstd.decompress(f.read()), size   # 압축 파일은 통째로 (다 쓴 세션이라 한 번만)
        return super()._read_new(path, start, size)

    def _parse_line(self, path, raw, state):
        if b'"session_meta"' not in raw and b'"turn_context"' not in raw and b'"token_count"' not in raw:
            return
        try:
            d = json.loads(raw)
        except Exception:
            return
        typ, p = d.get("type"), d.get("payload") or {}
        ts = _parse_ts(d.get("timestamp") or "")
        if typ == "session_meta":
            state["sid"] = p.get("id") or p.get("session_id")
            state["cwd"] = p.get("cwd")
            state["started"] = ts.timestamp() if ts else None
            acct = p.get("creator_account_id")
            state["acct"] = account_hash(acct, "codex") if acct else None
            return
        if typ == "turn_context":
            state["model"] = p.get("model") or state.get("model")
            return
        if typ != "event_msg" or p.get("type") != "token_count" or not ts:
            return
        rl = p.get("rate_limits")
        if rl:
            state["rate"] = rl
            state["rate_t"] = ts.timestamp()
        info = p.get("info")
        if not info:
            return
        last, total = info.get("last_token_usage") or {}, info.get("total_token_usage") or {}
        state["last"] = ts.timestamp()
        state["ctx_window"] = info.get("model_context_window")
        state["ctx"] = last.get("input_tokens")
        cum = total.get("total_tokens")
        if cum is None or cum == state.get("cum"):
            return   # 같은 누적값 반복 줄
        state["cum"] = cum
        inp = last.get("input_tokens", 0) or 0
        cached = last.get("cached_input_tokens", 0) or 0
        sid = state.get("sid") or path.name
        self._put(f"{sid}:{cum}", {
            "ts": ts,
            "model": state.get("model") or "unknown",
            "project": _basename(state.get("cwd") or "") or "(codex)",
            "session": sid,
            "in": max(0, inp - cached),
            "out": last.get("output_tokens", 0) or 0,
            "cw": last.get("cache_write_input_tokens", 0) or 0,
            "cr": cached,
            "rs": last.get("reasoning_output_tokens", 0) or 0,
        })

    def titles(self):
        return self.thread_names()

    def thread_names(self):
        """session_index.jsonl: 세션 id -> 대화 제목 (1분마다 다시 읽음)."""
        t, names = self._names
        if time.time() - t > 60:
            names = {}
            try:
                for line in (self.dir / "session_index.jsonl").read_text(encoding="utf-8").splitlines():
                    try:
                        d = json.loads(line)
                        names[d.get("id")] = d.get("thread_name")
                    except ValueError:
                        pass
            except OSError:
                pass
            self._names = (time.time(), names)
        return names


def _window_key(minutes):
    return {300: "five_hour", 10080: "seven_day", 1440: "one_day"}.get(minutes, f"{minutes}m")


def codex_limits(log):
    """가장 최근 요청 시점의 정확한 한도 (로그에 기록된 값). 리셋이 지났으면 0%."""
    rl = log.rate
    if not rl:
        return None
    now = time.time()
    windows = []
    for slot in ("primary", "secondary"):
        w = rl.get(slot)
        if not w or w.get("used_percent") is None:
            continue
        reset = w.get("resets_at")
        if not reset and w.get("resets_in_seconds") is not None:   # 예전 형식
            reset = rl["t"] + w["resets_in_seconds"]
        minutes = w.get("window_minutes") or 0
        passed = bool(reset and reset <= now)
        windows.append({"key": _window_key(minutes), "slot": slot, "window_minutes": minutes,
                        "used_pct": 0.0 if passed else w["used_percent"], "resets_at": reset,
                        "reset_passed": passed})
    credits = rl.get("credits") or {}
    return {"source": "log", "captured_at": rl["t"], "windows": windows, "plan_type": rl.get("plan_type"),
            "limit_reached": rl.get("rate_limit_reached_type"),
            "credits": {"has_credits": credits.get("has_credits"), "unlimited": credits.get("unlimited"),
                        "balance": credits.get("balance")} if credits else None}


def codex_sessions(log):
    """최근 활동한 세션 (Claude 세션 목록과 같은 키)."""
    now = time.time()
    names = log.thread_names()
    totals = log.session_totals()
    out = []
    for st in log.fstate.values():
        last = st.get("last")
        sid = st.get("sid")
        if not sid or not last or now - last > CODEX_SESSION_HOURS * 3600:
            continue
        win = st.get("ctx_window")
        out.append({
            "pid": None, "session": sid, "project": _basename(st.get("cwd") or "") or "(codex)",
            "started": st.get("started"), "last_activity": last,
            "state": "working" if now - last < CODEX_LIVE_SEC else "idle",
            "model": st.get("model"), "model_name": st.get("model"), "context": st.get("ctx"),
            "ctx_pct": round(100 * st["ctx"] / win, 1) if win and st.get("ctx") else None,
            "title": names.get(sid), "name": names.get(sid), "tokens": totals.get(sid), "effort": None,
        })
    out.sort(key=lambda s: -(s["last_activity"] or 0))
    return out


if __name__ == "__main__":
    import sys
    log = CodexUsage(Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".codex")
    s = log.snapshot()
    print(json.dumps({"today": s["today"], "files": s["files"], "limits": codex_limits(log),
                      "sessions": len(codex_sessions(log))}, indent=2, default=str))
