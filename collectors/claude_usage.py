"""AI 도구 토큰 사용량 공통 집계(UsageLog) + Claude Code 파서(~/.claude/projects/**/*.jsonl 증분 파싱).

표준 라이브러리만 사용: 원격 PC에도 소스 그대로 보내 실행한다. Codex/Gemini 파서는 같은 UsageLog를 상속한다.
레코드 공통 필드: ts(datetime), model, project, session, in(캐시 제외 입력), out(출력, 추론 포함), cw(캐시 쓰기),
cr(캐시 읽기), rs(추론 — out에 포함된 값, 참고용). 합계 = in + out + cw + cr."""
import hashlib
import json
import os
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
BLOCK_HOURS = 5


def _parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone()
    except Exception:
        return None


def _basename(path):
    """Windows/리눅스 경로 모두에서 마지막 폴더 이름 (원격 WSL에서 Windows 경로를 다룰 때 필요)."""
    return re.split(r"[\\/]", path.rstrip("\\/"))[-1] if path else ""


def _project_name(cwd, folder):
    if cwd:
        name = _basename(cwd)
        if name.startswith("scratch-"):
            return "(scratch)"
        return name
    return folder


CACHE_VERSION = 2   # 2: 세션 제목(custom-title)·하위 에이전트(agentId) 수집 추가 (2026-10-29)


def account_hash(raw, prefix):
    """계정 식별값은 원문 대신 해시 일부만 쓴다."""
    return f"{prefix}:{hashlib.sha256(raw.encode()).hexdigest()[:12]}" if raw else None


def five_hour_window(ts_list, now=None, hours=BLOCK_HOURS):
    """메시지 시각 목록(epoch 초)으로 현재 5시간 한도 창을 추정.
    창은 '직전 창이 끝난 뒤 첫 메시지'에서 시작해 5시간 뒤 리셋된다 (계속 쓰면 창이 연달아 이어짐).
    현재 열린 창이 없으면 None."""
    now = now or time.time()
    span = hours * 3600
    start = end = None
    for t in sorted(ts_list):
        if end is None or t >= end:
            start, end = t, t + span
    if end is None or end <= now:
        return None
    return {"start": start, "end": end}


class UsageLog:
    """공급자 공통: 사용량 레코드를 모아 화면용 집계(snapshot)와 DB용 이벤트(drain_events)를 만든다.
    하위 클래스는 _scan() (보통 _scan_jsonl + _parse_line) 만 구현한다."""
    provider = "?"

    def __init__(self, root, pricing=None, cache_path=None, keep_days=8):
        self.dir = Path(root)
        self.pricing = pricing or {}
        self.offsets = {}      # path -> 읽은 바이트 위치
        self.fstate = {}       # path -> 파일별 파싱 상태 (현재 모델·세션 등, 줄 사이에 이어지는 값)
        self.messages = {}     # 레코드 키 -> 레코드 (중복 제거, 마지막 값 유지)
        self.pending = []      # 새로 들어온 레코드 키 (DB 이벤트용)
        self.lock = threading.Lock()
        self.keep_sec = keep_days * 86400
        self.cache_path = Path(cache_path) if cache_path else None
        # 전체 폴더 탐색은 무거워서(원격 /mnt/c 는 수 초) 가끔만 하고, 그 사이에는 최근 파일만 확인
        self.full_every = 60
        self.last_full = 0.0
        self.active = []          # 보관 기간 안에 바뀐 파일 목록
        self.last_save = 0.0
        self._load_cache()

    def _load_cache(self):
        """원격처럼 매번 새 프로세스로 실행될 때 읽은 위치와 최근 레코드를 이어서 쓰기 위한 캐시."""
        if not self.cache_path or not self.cache_path.exists():
            return
        try:
            c = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if c.get("dir") != str(self.dir) or c.get("v") != CACHE_VERSION:
                return
            self.offsets = c.get("offsets", {})
            self.fstate = c.get("fstate", {})
            for mid, r in c.get("messages", {}).items():
                r["ts"] = datetime.fromtimestamp(r["ts"]).astimezone()
                r.setdefault("rs", 0)
                self.messages[mid] = r
        except Exception:
            self.offsets, self.fstate, self.messages = {}, {}, {}

    def _save_cache(self):
        if not self.cache_path:
            return
        try:
            msgs = {mid: {**r, "ts": r["ts"].timestamp()} for mid, r in self.messages.items()}
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"v": CACHE_VERSION, "dir": str(self.dir), "offsets": self.offsets, "fstate": self.fstate,
                                       "messages": msgs}), encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except Exception:
            pass

    def _put(self, key, rec):
        rec.setdefault("rs", 0)
        if key not in self.messages:
            self.pending.append(key)
        self.messages[key] = rec

    def _iter_files(self):
        """증분 대상 파일 목록 (기본: 폴더 아래 *.jsonl 전체)."""
        return self.dir.rglob("*.jsonl")

    def _scan_jsonl(self):
        """줄 단위 JSONL 증분 읽기. 완결된 줄마다 self._parse_line(path, raw_bytes, state) 호출."""
        if not self.dir.exists():
            return
        now = time.time()
        cutoff = now - self.keep_sec
        full = now - self.last_full >= self.full_every or not self.active
        paths = self._iter_files() if full else list(self.active)
        if full:
            self.last_full = now
            self.active = []
        for path in paths:
            key = str(path)
            try:
                st = path.stat()
            except OSError:
                continue
            size = st.st_size
            if st.st_mtime < cutoff:   # 보관 기간보다 오래 안 바뀐 파일은 건너뜀
                continue
            if full:
                self.active.append(path)
            start = self.offsets.get(key, 0)
            if size < start:   # 파일이 잘렸으면 처음부터
                start = 0
                self.fstate.pop(key, None)
            if size == start:
                continue
            try:
                data, new_off = self._read_new(path, start, size)
            except Exception:
                continue
            if data is None:
                continue
            self.offsets[key] = new_off
            state = self.fstate.setdefault(key, {})
            for raw in data.splitlines():
                self._parse_line(path, raw, state)

    def _read_new(self, path, start, size):
        """start 이후 새로 쓰인 완결된 줄들과 다음 시작 위치. 마지막 줄이 아직 쓰는 중일 수 있어 줄바꿈까지만."""
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read()
        end = data.rfind(b"\n")
        if end < 0:
            return None, start
        return data[: end + 1], start + end + 1

    def _scan(self):
        self._scan_jsonl()

    def _parse_line(self, path, raw, state):
        raise NotImplementedError

    @staticmethod
    def _event(key, r):
        return {"key": key, "ts": r["ts"].timestamp(), "model": r["model"], "session": r.get("session"),
                "project": r.get("project"), "in": r["in"], "out": r["out"], "cw": r["cw"], "cr": r["cr"],
                "rs": r.get("rs", 0)}

    def drain_events(self):
        """지난 호출 이후 새로 들어온 레코드를 DB 이벤트 형식으로 (snapshot() 뒤에 호출)."""
        with self.lock:
            keys, self.pending = self.pending, []
            return [self._event(k, self.messages[k]) for k in keys if k in self.messages]

    def all_events(self, since=0):
        """보관 중인 레코드 전체(since 이후). 연결 직후 한 번 보내 DB 빈 곳을 채우는 용도 (DB가 중복 제거)."""
        with self.lock:
            self.pending = []
            return [self._event(k, r) for k, r in self.messages.items() if r["ts"].timestamp() >= since]

    def titles(self):
        """세션 id -> 제목 (공급자별로 다시 정의)."""
        return {}

    def agent_meta(self):
        """하위 에이전트 id -> {description, type, model} (Claude만)."""
        return {}

    def session_summaries(self):
        """보관 중인 레코드로 세션별 요약: 프로젝트·모델·시작/마지막·토큰·응답 수·제목·하위 에이전트 목록.
        닫힌 세션도 포함 (DB 세션 기록·화면 묶음용)."""
        titles, metas = self.titles(), self.agent_meta()
        with self.lock:
            recs = list(self.messages.values())
        out = {}
        for r in recs:
            sid = r.get("session")
            if not sid:
                continue
            ts, tot = r["ts"].timestamp(), self._total(r)
            s = out.get(sid)
            if s is None:
                s = out[sid] = {"session": sid, "project": r.get("project"), "model": None, "started": ts,
                                "last_activity": ts, "tokens": 0, "count": 0, "_agents": {}, "_mt": 0}
            s["started"] = min(s["started"], ts)
            s["last_activity"] = max(s["last_activity"], ts)
            s["tokens"] += tot
            s["count"] += 1
            a = r.get("agent")
            if a:
                g = s["_agents"].get(a)
                if g is None:
                    m = metas.get(a) or {}
                    g = s["_agents"][a] = {"agent": a, "description": m.get("description"), "type": m.get("type"),
                                           "model": r["model"], "tokens": 0, "count": 0, "first": ts, "last": ts}
                g["tokens"] += tot
                g["count"] += 1
                g["first"], g["last"] = min(g["first"], ts), max(g["last"], ts)
            elif ts >= s["_mt"]:
                s["model"], s["_mt"] = r["model"], ts
        for s in out.values():
            s["agents"] = sorted(s.pop("_agents").values(), key=lambda g: g["first"])
            s.pop("_mt")
            s["title"] = titles.get(s["session"])
        return sorted(out.values(), key=lambda s: -s["last_activity"])

    def session_totals(self):
        with self.lock:
            out = {}
            for r in self.messages.values():
                out[r["session"]] = out.get(r["session"], 0) + self._total(r)
            return out

    @staticmethod
    def _total(r):
        return r["in"] + r["out"] + r["cw"] + r["cr"]

    def _cost(self, r):
        p = self.pricing.get(r["model"])
        if not p:
            return None
        return (r["in"] * p.get("input", 0) + r["out"] * p.get("output", 0)
                + r["cw"] * p.get("cache_write", 0) + r["cr"] * p.get("cache_read", 0)) / 1e6

    def _blocks(self, recs):
        """ccusage 방식: 첫 메시지 시각(정시 내림)부터 5시간 블록, 간격이 5시간 넘으면 새 블록."""
        blocks = []
        cur = None
        for r in recs:
            ts = r["ts"]
            if cur is None or ts >= cur["end"] or (ts - cur["last"]) >= timedelta(hours=BLOCK_HOURS):
                start = ts.replace(minute=0, second=0, microsecond=0)
                cur = {"start": start, "end": start + timedelta(hours=BLOCK_HOURS),
                       "last": ts, "tokens": 0, "out": 0, "count": 0}
                blocks.append(cur)
            cur["last"] = ts
            cur["tokens"] += self._total(r)
            cur["out"] += r["out"]
            cur["count"] += 1
        return blocks

    def snapshot(self):
        with self.lock:
            self._scan()
            cutoff = datetime.now().astimezone() - timedelta(seconds=self.keep_sec)
            self.messages = {k: r for k, r in self.messages.items() if r["ts"] >= cutoff}
            if time.time() - self.last_save >= 60:
                self._save_cache()
                self.last_save = time.time()
            recs = sorted(self.messages.values(), key=lambda r: r["ts"])
        now = datetime.now().astimezone()
        today = now.date()
        week_start = today - timedelta(days=6)

        def empty():
            return {"in": 0, "out": 0, "cw": 0, "cr": 0, "total": 0, "count": 0, "cost": 0.0}

        def add(acc, r):
            for k in ("in", "out", "cw", "cr"):
                acc[k] += r[k]
            acc["total"] += self._total(r)
            acc["count"] += 1
            c = self._cost(r)
            if c is not None:
                acc["cost"] += c

        today_acc = empty()
        days = {(week_start + timedelta(days=i)).isoformat(): empty() for i in range(7)}
        models_today = defaultdict(int)
        models_week = defaultdict(int)
        projects_today = defaultdict(int)
        sessions_today = set()
        hourly = [0] * 24
        for r in recs:
            d = r["ts"].date()
            if d >= week_start:
                add(days[d.isoformat()], r)
                models_week[r["model"]] += self._total(r)
            if d == today:
                add(today_acc, r)
                models_today[r["model"]] += self._total(r)
                projects_today[r["project"]] += self._total(r)
                sessions_today.add(r["session"])
                hourly[r["ts"].hour] += self._total(r)

        block = None
        blocks = self._blocks(recs)
        if blocks and blocks[-1]["end"] > now:
            b = blocks[-1]
            elapsed = (now - b["start"]).total_seconds()
            block = {
                "start": b["start"].isoformat(),
                "end": b["end"].isoformat(),
                "elapsed_pct": round(100 * elapsed / (BLOCK_HOURS * 3600), 1),
                "remaining_sec": int((b["end"] - now).total_seconds()),
                "tokens": b["tokens"],
                "out": b["out"],
                "count": b["count"],
                "burn_per_min": int(b["tokens"] / max(elapsed / 60, 1)),
            }
        past = [b["tokens"] for b in blocks[:-1][-30:]] if blocks else []

        # 최근 60분 분당 토큰 (실시간 흐름 차트용, 0번이 가장 오래된 분)
        minutes = [0] * 60
        minutes_out = [0] * 60
        cur_min = now.replace(second=0, microsecond=0)
        for r in reversed(recs):
            idx = 59 - int((cur_min - r["ts"].replace(second=0, microsecond=0)).total_seconds() // 60)
            if idx < 0:
                break
            if idx <= 59:
                minutes[idx] += self._total(r)
                minutes_out[idx] += r["out"]

        # 최근 12시간 메시지 시각(분 단위, 중복 제거): 두 PC를 합쳐 5시간 창을 추정하는 데 사용
        since = now - timedelta(hours=12)
        recent_ts = sorted({int(r["ts"].timestamp()) // 60 * 60 for r in recs if r["ts"] >= since})

        last = recs[-1] if recs else None
        return {
            "today": today_acc,
            "days": [{"date": k, **v} for k, v in days.items()],
            "models_today": dict(sorted(models_today.items(), key=lambda x: -x[1])),
            "models_week": dict(sorted(models_week.items(), key=lambda x: -x[1])),
            "projects_today": sorted(projects_today.items(), key=lambda x: -x[1])[:6],
            "sessions_today": len(sessions_today),
            "hourly_today": hourly,
            "minutes": minutes,
            "recent_ts": recent_ts,
            "minutes_out": minutes_out,
            "block": block,
            "block_max_recent": max(past) if past else None,
            "last_activity": last["ts"].isoformat() if last else None,
            "last_model": last["model"] if last else None,
            "has_pricing": bool(self.pricing),
            "files": len(self.offsets),
        }


class ClaudeUsage(UsageLog):
    provider = "claude"

    def __init__(self, projects_dir=PROJECTS_DIR, pricing=None, cache_path=None, keep_days=8):
        super().__init__(projects_dir, pricing, cache_path, keep_days)

    def titles(self):
        return {st["sid"]: st["title"] for st in self.fstate.values() if st.get("sid") and st.get("title")}

    def agent_meta(self):
        return {st["meta"]["id"]: st["meta"] for st in self.fstate.values() if st.get("meta")}

    def _parse_line(self, path, raw, state):
        if b'"custom-title"' in raw or b'"agent-name"' in raw:
            # 세션 제목 (데스크톱 앱이 붙이는 한글 제목 등). custom-title 이 있으면 그것을 우선
            try:
                d = json.loads(raw)
            except Exception:
                return
            typ = d.get("type")
            t = d.get("customTitle") if typ == "custom-title" else d.get("agentName") if typ == "agent-name" else None
            if t and d.get("sessionId") and not d.get("isSidechain"):
                state["sid"] = d["sessionId"]
                if typ == "custom-title" or not state.get("title_custom"):
                    state["title"] = t
                    state["title_custom"] = typ == "custom-title"
            return
        if b'"usage"' not in raw:
            return
        try:
            d = json.loads(raw)
        except Exception:
            return
        if d.get("type") != "assistant":
            return
        m = d.get("message") or {}
        u = m.get("usage")
        mid = m.get("id") or d.get("requestId") or d.get("uuid")
        ts = _parse_ts(d.get("timestamp", ""))
        if not u or not mid or not ts:
            return
        model = m.get("model") or "unknown"
        if model == "<synthetic>":
            return
        agent = d.get("agentId") if d.get("isSidechain") else None
        if agent and "meta" not in state:
            # 하위 에이전트 설명: subagents/agent-<id>.meta.json (description·agentType·model)
            meta = {}
            try:
                j = json.loads(path.with_name(path.stem + ".meta.json").read_text(encoding="utf-8"))
                meta = {"id": agent, "description": j.get("description"), "type": j.get("agentType"),
                        "model": j.get("model")}
            except Exception:
                meta = {"id": agent}
            state["meta"] = meta
        self._put(mid, {
            "agent": agent,
            "ts": ts,
            "model": model,
            "project": _project_name(d.get("cwd"), path.parent.name),
            "session": d.get("sessionId"),
            "in": u.get("input_tokens", 0) or 0,
            "out": u.get("output_tokens", 0) or 0,
            "cw": u.get("cache_creation_input_tokens", 0) or 0,
            "cr": u.get("cache_read_input_tokens", 0) or 0,
        })


if __name__ == "__main__":
    import time
    t = time.time()
    s = ClaudeUsage().snapshot()
    print(f"parsed in {time.time() - t:.2f}s, files={s['files']}")
    print(json.dumps({k: s[k] for k in ("today", "block", "models_today", "projects_today", "sessions_today")},
                     indent=2, ensure_ascii=False, default=str))
