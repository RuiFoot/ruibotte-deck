"""Gemini CLI 사용량 · 세션 · 한도 추정 (~/.gemini/tmp/<프로젝트>/chats/). 표준 라이브러리만 사용.

대화 파일 두 가지 형식:
  - session-*.json : 대화 전체를 담은 JSON (저장할 때마다 통째로 다시 씀) — 2026-10 실데이터로 확인한 형식
  - session-*.jsonl: 변경 기록. 메타 줄, 메시지 줄(id), {"$set":{...}}, {"$patch":{...}}, {"$rewindTo": id}
    → 처음부터 재생(replay)해 최종 상태를 만든다 (공식 소스 기준, 실데이터 미확인)
하위 에이전트 대화는 chats/<부모 세션 id>/<id>.json.
대화: sessionId, projectHash, startTime, lastUpdated, messages[], kind(main|subagent)
메시지: id, timestamp, type(user|gemini|info|error), model, tokens{input, output, cached, thoughts, tool, total}
  input 에 cached 가 포함됨, thoughts·tool 은 별도 → 합계 = input + output + thoughts + tool.
한도: 로컬 기록·공식 조회 방법이 없어 '오늘 요청 수 / 하루 기준값'으로만 추정해 표시한다.
"""
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    from collectors.claude_usage import UsageLog, _basename, _parse_ts
except ImportError:   # 원격 에이전트: claude_usage.py 가 앞에 이어 붙어 있음
    pass

GEMINI_DAILY_REQUESTS = 1000   # 무료 등급 하루 요청 한도로 알려진 값 (추정 기준, 설정에서 바꿀 예정)
GEMINI_LIVE_SEC = 30
GEMINI_SESSION_HOURS = 6


def _replay_jsonl(text):
    conv, msgs, order = {}, {}, []
    for line in text.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        if "$set" in d and isinstance(d["$set"], dict):
            conv.update(d["$set"])
        elif "$patch" in d and isinstance(d["$patch"], dict):
            p = d["$patch"]
            if p.get("id") in msgs:
                msgs[p["id"]].update(p)
            else:
                conv.update(p)
        elif "$rewindTo" in d:
            rid = d["$rewindTo"]
            if rid in msgs:
                keep = order[:order.index(rid) + 1]
                for k in order[len(keep):]:
                    msgs.pop(k, None)
                order = keep
        elif "id" in d and "type" in d:
            if d["id"] not in msgs:
                order.append(d["id"])
            msgs[d["id"]] = d
        else:
            conv.update(d)   # 메타 레코드
    conv["messages"] = [msgs[k] for k in order]
    return conv


class GeminiUsage(UsageLog):
    provider = "gemini"

    def __init__(self, root, pricing=None, cache_path=None, keep_days=8):
        super().__init__(root, pricing, cache_path, keep_days)
        self._proj = {}

    def _iter_files(self):
        for p in self.dir.rglob("*.json*"):
            if "chats" in p.parts and p.suffix in (".json", ".jsonl"):
                yield p

    def _project(self, path):
        parts = path.parts
        try:
            folder = Path(*parts[:parts.index("chats")])
        except ValueError:
            return "(gemini)"
        name = folder.name
        if name not in self._proj:
            label = name
            if re.fullmatch(r"[0-9a-f]{64}", name):   # 해시 폴더: .project_root 에 실제 경로가 있으면 그 이름
                try:
                    label = _basename((folder / ".project_root").read_text(encoding="utf-8").strip()) or name[:8]
                except OSError:
                    label = name[:8]
            self._proj[name] = label
        return self._proj[name]

    def _scan(self):
        """대화 파일은 통째로 다시 쓰이므로 (크기, 수정 시각)이 바뀐 파일만 전체를 다시 읽는다."""
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
            if st.st_mtime < cutoff:
                continue
            if full:
                self.active.append(path)
            sig = f"{st.st_size}:{int(st.st_mtime)}"
            if self.offsets.get(key) == sig:
                continue
            try:
                text = path.read_text(encoding="utf-8")
                conv = _replay_jsonl(text) if path.suffix == ".jsonl" else json.loads(text)
            except Exception:
                continue   # 쓰는 중이면 다음에 다시
            self.offsets[key] = sig
            self._ingest(path, key, conv)

    def _ingest(self, path, key, conv):
        sid = conv.get("sessionId") or path.stem
        project = self._project(path)
        state = self.fstate[key] = {"sid": sid, "project": project, "kind": conv.get("kind") or "main",
                                    "started": None, "last": None, "model": None, "ctx": None}
        t0 = _parse_ts(conv.get("startTime") or "")
        t1 = _parse_ts(conv.get("lastUpdated") or "")
        state["started"] = t0.timestamp() if t0 else None
        state["last"] = t1.timestamp() if t1 else None
        for m in conv.get("messages") or []:
            tok = m.get("tokens")
            if m.get("type") != "gemini" or not tok:
                continue
            ts = _parse_ts(m.get("timestamp") or "")
            if not ts:
                continue
            inp, cached = tok.get("input", 0) or 0, tok.get("cached", 0) or 0
            thoughts = tok.get("thoughts", 0) or 0
            state["model"] = m.get("model") or state["model"]
            state["ctx"] = inp
            self._put(f"{sid}:{m.get('id')}", {
                "ts": ts, "model": m.get("model") or "unknown", "project": project, "session": sid,
                "in": max(0, inp - cached) + (tok.get("tool", 0) or 0),
                "out": (tok.get("output", 0) or 0) + thoughts,
                "cw": 0, "cr": cached, "rs": thoughts,
            })


def gemini_limits(log, daily=GEMINI_DAILY_REQUESTS):
    """오늘 요청 수(응답 메시지 수) / 하루 기준값 — 추정."""
    now = datetime.now().astimezone()
    today = now.date()
    n = sum(1 for r in log.messages.values() if r["ts"].date() == today)
    midnight = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
    return {"source": "estimate", "captured_at": time.time(),
            "windows": [{"key": "daily_requests", "window_minutes": 1440, "used_pct": round(100 * n / daily, 1),
                         "requests": n, "limit": daily, "resets_at": midnight, "reset_passed": False,
                         "estimated": True}]}


def gemini_sessions(log):
    now = time.time()
    totals = log.session_totals()
    out = []
    for st in log.fstate.values():
        last = st.get("last")
        if st.get("kind") == "subagent" or not last or now - last > GEMINI_SESSION_HOURS * 3600:
            continue
        out.append({
            "pid": None, "session": st["sid"], "project": st.get("project"), "started": st.get("started"),
            "last_activity": last, "state": "working" if now - last < GEMINI_LIVE_SEC else "idle",
            "model": st.get("model"), "model_name": st.get("model"), "context": st.get("ctx"), "ctx_pct": None,
            "title": None, "name": None, "tokens": totals.get(st["sid"]), "effort": None,
        })
    out.sort(key=lambda s: -(s["last_activity"] or 0))
    return out
