"""Claude Code statusLine 명령. stdin JSON을 대시보드용 파일로 저장하고 짧은 상태줄을 출력.

저장 위치 (~/.claude/dashboard/):
  rate_limits.json         가장 최근 플랜 한도 (5시간 / 7일)
  status/<session_id>.json 세션별 최신 상태 (컨텍스트, 비용, 모델 등)
"""
import json
import os
import sys
import time
from pathlib import Path

OUT = Path.home() / ".claude" / "dashboard"


def write_atomic(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except Exception:
        data = {}
    now = time.time()
    sid = data.get("session_id")
    rl = data.get("rate_limits") or {}
    try:
        if rl.get("five_hour") or rl.get("seven_day"):
            write_atomic(OUT / "rate_limits.json", {**rl, "captured_at": now, "session_id": sid})
        if sid:
            cw = data.get("context_window") or {}
            write_atomic(OUT / "status" / f"{sid}.json", {
                "captured_at": now,
                "session_id": sid,
                "session_name": data.get("session_name"),
                "cwd": data.get("cwd") or (data.get("workspace") or {}).get("current_dir"),
                "model": (data.get("model") or {}).get("display_name"),
                "model_id": (data.get("model") or {}).get("id"),
                "effort": (data.get("effort") or {}).get("level"),
                "cost_usd": (data.get("cost") or {}).get("total_cost_usd"),
                "duration_ms": (data.get("cost") or {}).get("total_duration_ms"),
                "lines_added": (data.get("cost") or {}).get("total_lines_added"),
                "lines_removed": (data.get("cost") or {}).get("total_lines_removed"),
                "ctx_used_pct": cw.get("used_percentage"),
                "ctx_size": cw.get("context_window_size"),
                "version": data.get("version"),
            })
    except Exception:
        pass

    # 상태줄에 보일 내용
    parts = []
    m = (data.get("model") or {}).get("display_name")
    if m:
        parts.append(m)
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        w = rl.get(key)
        if w and w.get("used_percentage") is not None:
            left = 100 - w["used_percentage"]
            parts.append(f"{label} {left:.0f}% left")
    ctx = (data.get("context_window") or {}).get("used_percentage")
    if ctx is not None:
        parts.append(f"ctx {ctx:.0f}%")
    sys.stdout.write(" | ".join(parts))


if __name__ == "__main__":
    main()
