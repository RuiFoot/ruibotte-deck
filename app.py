"""RuiBotte Deck 백엔드 (FastAPI).

수집기를 백그라운드 스레드로 돌려 결과를 메모리에 두고, /api/state 로 제공한다.
frontend/dist (Vite 빌드 결과)를 같은 서버에서 정적 파일로 서빙한다.
"""
import builtins
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import mimetypes
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import desktop  # noqa: E402  (쓰는 파일 위치 desktop.DATA — 설치판은 사용자 데이터 폴더)

# pyw(콘솔 없음)로 실행하면 stdout/stderr가 None이라 uvicorn 로깅이 즉시 죽는다 → 로그 파일로 돌림
if sys.stdout is None or sys.stderr is None:
    _log = open(desktop.DATA / "server.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

from collectors.claude_usage import five_hour_window  # noqa: E402
from collectors.plan_api import PlanAPI, find_claude_cli  # noqa: E402
from collectors.providers import PROVIDERS, detect_providers, find_profiles, profile_detect  # noqa: E402
from collectors.sessions import finalize_limits, read_limits  # noqa: E402
from machines import LocalMachine, RemoteMachine, detect, normalize_target, validate_distro  # noqa: E402
from store import DEFAULTS, Store  # noqa: E402
from pricing import PRICES_AS_OF, cost_usd  # noqa: E402
from alerts import KINDS as ALERT_KINDS, Alerts  # noqa: E402
from updater import UPDATER  # noqa: E402
import secure  # noqa: E402

mimetypes.add_type("application/manifest+json", ".webmanifest")   # 앱 정보 파일 (설치형 웹앱)

CFG = desktop.load_config()   # 없으면 기본값으로 만듦 (포트 8787, 이 PC에서만 접속)
DIST = ROOT / "frontend" / "dist"

# v2 내장 DB: 처음 켤 때 config.json의 원격 PC·주기 설정을 옮겨 오고, 이후로는 DB 값을 쓴다.
# DB를 열 수 없으면 v1처럼 config.json 값으로 동작(기록·PC 추가만 빠짐).
try:
    STORE = Store()
    STORE.migrate_v1_config(CFG)
    # 1~5단계 때 이미 v1 설정을 옮긴 DB: 원격 PC가 있으면 첫 실행 마법사 생략
    if not STORE.get("setup.done") and STORE.get("migrated.v1_config") and any(
            m["kind"] == "ssh" for m in STORE.machines()):
        STORE.set("setup.done", int(time.time()))
except Exception:
    STORE = None
    print("store unavailable: " + traceback.format_exc(limit=3))


def setting(key, v1_value):
    return STORE.get(key) if STORE else v1_value


def machine_rows():
    if STORE:
        return STORE.machines(enabled_only=True)
    rc = CFG.get("remote") or {}
    rows = [{"id": "local", "name": "이 PC", "kind": "local"}]
    if rc.get("hosts"):
        rows.append({"id": "remote", "name": rc.get("label") or rc["hosts"][0], "kind": "ssh",
                     "ssh_target": rc["hosts"][0], "ssh_fallbacks": rc["hosts"][1:],
                     "wsl_distro": rc.get("wsl_distro"), "home_override": rc.get("claude_home")})
    return rows


STATE = {"local": None, "local_extras": None, "claude": None, "live": None,
         "remote": None, "remote_claude": None,
         "remote_label": next((r["name"] for r in machine_rows() if r["kind"] == "ssh"), "원격")}
ERRORS = {}
UPDATED = {}   # 항목별 마지막 "성공" 수집 시각 (화면에 "N분 전 기록" 표시용)
CACHE = desktop.DATA / "state_cache.json"
MACHINES = {}           # id -> RemoteMachine (실행 중인 원격 PC)
MACHINE_ROWS = {}       # id -> 시작할 때의 설정 행 (변경 감지용)
PRIMARY = {"id": None}  # v1 화면의 "원격" 칸에 보여 줄 PC (켜진 원격 PC 중 첫 번째)
MLOCK = threading.RLock()
LOCAL_PROV = {}         # (provider, 키) -> 이 PC의 ProviderRunner (키: 기본 폴더 'local', 다른 계정 폴더 'local~프로필')
PSTATE_LOCAL = {}       # provider -> {키: 이 PC의 최신 결과 {ok, error, updated, usage, sessions, limits, account}}
PLAN_EXTRA = {}         # 프로필 id -> 다른 계정 폴더의 PlanAPI (Claude 정확한 한도)
PLAN = {"api": None}   # Claude 계정 사용량 API (화면의 '새로고침'에서 바로 다시 시도)


def load_cache():
    """서버를 다시 켜도 첫 수집이 끝날 때까지 마지막 기록을 보여주도록 저장본을 불러옴."""
    try:
        c = json.loads(CACHE.read_text(encoding="utf-8"))
        for k, v in c.get("state", {}).items():
            if k in STATE and k != "remote_label":
                STATE[k] = v
        UPDATED.update(c.get("updated", {}))
    except Exception:
        pass


def save_cache_loop():
    while True:
        time.sleep(30)
        try:
            tmp = CACHE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"state": STATE, "updated": UPDATED}, default=str, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(CACHE)
        except Exception:
            pass


def record(name, result):
    """이 PC 수집 결과를 DB 기록(분 평균)으로 남김. 기록 실패가 수집을 멈추면 안 됨."""
    if not STORE:
        return
    try:
        if name == "local":
            gpus = (STATE.get("local_extras") or {}).get("gpus") or []
            swap = result.get("swap") or {}
            STORE.record_sys("local", time.time(), cpu=result.get("cpu"), mem=(result.get("mem") or {}).get("pct"),
                             swap=100 * swap["used"] / swap["total"] if swap.get("total") else None,
                             net_down=(result.get("net") or {}).get("down"), net_up=(result.get("net") or {}).get("up"),
                             gpu=gpus[0].get("util") if gpus else None)
    except Exception:
        ERRORS[f"record:{name}"] = traceback.format_exc(limit=3)


def record_remote(mid, st):
    if not STORE:
        return
    try:
        w = st.get("windows") or {}
        gpus = st.get("gpus") or []
        STORE.record_sys(mid, st["sample_t"], cpu=w.get("cpu"),
                         mem=100 * (1 - (w.get("mem_free") or 0) / w["mem_total"]) if w.get("mem_total") else None,
                         swap=100 * w["pagefile_used"] / w["pagefile_total"] if w.get("pagefile_total") else None,
                         net_down=w.get("net_down"), net_up=w.get("net_up"),
                         gpu=gpus[0].get("util") if gpus else w.get("gpu_util"))
    except Exception:
        ERRORS[f"record:{mid}"] = traceback.format_exc(limit=3)


def maintenance_loop():
    """1분마다 롤업(새 사용량을 시간/일 합계에 반영 — 화면의 기간 합계가 최신이 되도록),
    매시(와 켤 때) 보관 기간 지난 기록 삭제."""
    last_hour = None
    while True:
        try:
            hour = int(time.time() // 3600)
            if hour != last_hour:
                STORE.maintenance()
                last_hour = hour
            else:
                STORE.rollup()
            ERRORS.pop("maintenance", None)
        except Exception:
            ERRORS["maintenance"] = traceback.format_exc(limit=3)
        time.sleep(60)


def put_state(name, result, ok, t=None):
    """v1 규칙: 실패하면 이전 값은 그대로 두고 실패 정보만 갱신."""
    prev = STATE.get(name)
    if ok or not prev:
        STATE[name] = result
    else:
        STATE[name] = {**prev, "ok": False, "error": result.get("error")}
    if ok:
        UPDATED[name] = t or time.time()


def loop(name, fn, interval):
    while True:
        t = time.time()
        try:
            result = fn()
            ok = not (isinstance(result, dict) and result.get("ok") is False)
            put_state(name, result, ok)
            if ok:
                record(name, result)
            ERRORS.pop(name, None)
        except Exception:
            ERRORS[name] = traceback.format_exc(limit=3)
        time.sleep(max(0.5, interval - (time.time() - t)))


# ---------- 원격 PC ----------
def on_detect(mid, info):
    if STORE:
        try:
            STORE.set(f"machine.{mid}.detect", {k: v for k, v in info.items() if k != "launcher"})
            _DETECTED["t"] = 0.0   # 새로 감지한 도구를 바로 '자동 켜짐'에 반영
        except Exception:
            pass


def profile_key(mid, prof=None):
    """상태·DB 키: 기본 폴더 = PC id, 다른 계정 폴더 = 'PC id~프로필 id' (docs/ACCOUNTS_DESIGN.md)."""
    return f"{mid}~{prof}" if prof else mid


def split_key(key):
    mid, _, prof = (key or "").partition("~")
    return mid, prof or None


def profile_slots(mid, pid):
    """그 PC·도구의 계정 폴더 [(키, 폴더 경로 | None=기본 폴더, 사용자가 붙인 이름 | None)] — 기본 먼저, 켜진 것만."""
    rows = [x for x in STORE.profiles(mid) if x["provider"] == pid] if STORE else []
    default = next((x for x in rows if not x["path"]), None)
    out = [(mid, None, (default or {}).get("label") or None)]
    out += [(profile_key(mid, x["id"]), x["path"], x["label"] or None) for x in rows if x["path"] and x["enabled"]]
    return out


def auto_label(st):
    """이름을 안 붙인 계정: 그 폴더에 실제로 로그인된 계정(이메일 → 조직 → 계정 ID 앞자리)."""
    info = (st or {}).get("account_info") or {}
    if info.get("email") or info.get("org"):
        return info.get("email") or info.get("org")
    acct = (st or {}).get("account")
    return f"계정 {acct[:6]}" if acct else None


def account_labels(pid, states=None):
    """{키: 계정 이름} — 이름이 같으면 같은 계정으로 묶음 (PC가 달라도). 사용자가 붙인 이름이 없으면 실제 로그인 계정."""
    states = provider_states(pid) if states is None else states
    out = {}
    for r in machine_rows():
        for key, _, label in profile_slots(r["id"], pid):
            out[key] = label or auto_label(states.get(key)) or "기본"
    return out


def remote_profiles_cfg(mid):
    """원격 에이전트에 보낼 다른 계정 폴더 목록 {도구: [{k, dir}]}."""
    out = {}
    for x in (STORE.profiles(mid) if STORE else []):
        if x["path"] and x["enabled"]:
            out.setdefault(x["provider"], []).append({"k": x["id"], "dir": x["path"]})
    return out


_DETECTED = {"t": 0.0, "v": set()}


AUTO_RECENT_DAYS = 14   # '자동' 켜짐 기준: 이 기간 안에 쓴 흔적이 있는 도구


def detected_anywhere():
    """최근(AUTO_RECENT_DAYS) 쓴 흔적이 있는 AI 도구 (이 PC + 연결 시험으로 감지한 원격 PC). 30초 캐시."""
    if time.time() - _DETECTED["t"] < 30:
        return _DETECTED["v"]
    cut = time.time() - AUTO_RECENT_DAYS * 86400
    found = set()
    for pid in detect_providers(Path.home()):
        try:
            if PROVIDERS[pid].data_dir(Path.home()).stat().st_mtime >= cut:
                found.add(pid)
        except OSError:
            pass
    if STORE:
        for r in machine_rows():
            if r["kind"] == "ssh":
                d = STORE.get(f"machine.{r['id']}.detect") or {}
                found |= {p for p, t in (d.get("providers") or {}).items() if (t or 0) >= cut}
    _DETECTED.update(t=time.time(), v=found)
    return found


def enabled_providers():
    """켜진 AI 도구. 사용자가 직접 켜고 끈 적 없는 도구는 '자동': 어느 PC에서든 최근 쓴 흔적이 있으면 켜짐 (안 쓰는 도구는 꺼진 채)."""
    if not STORE:
        return list(PROVIDERS)
    ps = STORE.provider_settings()
    auto = None
    out = []
    for p in PROVIDERS:
        if p in ps:
            if ps[p]["enabled"]:
                out.append(p)
        else:
            auto = detected_anywhere() if auto is None else auto
            if p in auto:
                out.append(p)
    return out


def store_events(machine_id, pid, account, events):
    """공급자 사용량 레코드 → DB (중복은 DB가 걸러냄)."""
    if not STORE or not events:
        return
    try:
        STORE.add_provider_events(pid, machine_id, account, events)
    except Exception:
        ERRORS[f"events:{machine_id}:{pid}"] = traceback.format_exc(limit=3)


_SESS_SAVED = {}


def persist(machine_id, pid, st):
    """세션 목록(30초마다)과 정확한 한도(값이 바뀔 때)를 DB에 남김."""
    if not STORE or not st or not st.get("ok"):
        return
    try:
        k = (machine_id, pid)
        if time.time() - _SESS_SAVED.get(k, 0) >= 30 and (st.get("sessions") or st.get("recent")):
            _SESS_SAVED[k] = time.time()
            live = {x.get("session") for x in st.get("sessions") or []}
            # 로그로 만든 최근 세션 요약(닫힌 세션 포함) 먼저, 그 위에 지금 열린 세션
            rows = [{"provider": pid, "machine_id": machine_id, "session_id": x["session"], "account_id": st.get("account"),
                     "project": x.get("project"), "model": x.get("model"), "started_at": x.get("started"),
                     "last_activity": x.get("last_activity"), "state": "ended", "tokens": x.get("tokens"),
                     "title": x.get("title"), "agents": x.get("agents")}
                    for x in st.get("recent") or [] if x.get("session") not in live]
            rows += [{"provider": pid, "machine_id": machine_id, "session_id": x.get("session") or str(x.get("pid")),
                      "account_id": st.get("account"), "project": x.get("project"),
                      "model": x.get("model_name") or x.get("model"), "started_at": x.get("started"),
                      "last_activity": x.get("last_activity"), "state": x.get("state"), "tokens": x.get("tokens"),
                      "title": x.get("name") or x.get("title"), "agents": x.get("agents")}
                     for x in st.get("sessions") or []]
            STORE.upsert_sessions(rows)
        lim = st.get("limits") or {}
        if pid != "claude" and lim.get("windows"):   # Claude는 live()에서 출처별로 기록
            for w in lim["windows"]:
                if not w.get("estimated"):
                    STORE.record_limit(pid, w["key"], lim.get("captured_at"), w.get("used_pct"), w.get("resets_at"),
                                       lim.get("source"), st.get("account") or "")
    except Exception:
        ERRORS[f"persist:{machine_id}:{pid}"] = traceback.format_exc(limit=3)


def backfill_args(machine_id):
    """원격 에이전트용: (아직 안 채운 공급자 -> 이전 시각, 채울 일수)"""
    if not STORE:
        return {}, 90
    return STORE.backfill_pending(machine_id, enabled_providers()), setting("history.backfill_days", 90)


def backfill_done(machine_id, pid, n):
    if STORE:
        STORE.backfill_done(machine_id, pid, n)


def local_backfill(pid, runner, before, key="local"):
    """이 PC: 보관 기간을 넓혀 과거 기록을 한 번 읽어 DB에 넣음 (뒤에서, 수집을 막지 않게)."""
    try:
        ev = runner.history_events(setting("history.backfill_days", 90), before)
        store_events(key, pid, runner.account(), ev)
        backfill_done(key, pid, len(ev))
    except Exception:
        ERRORS[f"backfill:{pid}"] = traceback.format_exc(limit=3)


def local_providers_loop(interval, pricing):
    """이 PC의 AI 도구(감지된 것만)를 원격과 같은 플러그인으로 집계. Claude 결과는 v1 화면의 claude 칸에도 넣음."""
    home = Path.home()
    synced = set()
    last_scan = 0.0
    while True:
        t = time.time()
        if STORE and t - last_scan > 300:   # 다른 계정 폴더(.claude-*, .codex-* …) 자동 찾기·등록
            last_scan = t
            try:
                STORE.auto_profiles("local", find_profiles(home))
            except Exception:
                ERRORS["profiles:local"] = traceback.format_exc(limit=2)
        try:
            found = set(detect_providers(home, enabled_providers()))
            active = set()
            for pid in enabled_providers():
                for key, root, _label in profile_slots("local", pid):
                    if (root is None and pid not in found) or (root and not profile_detect(pid, root)):
                        continue
                    rk = (pid, key)
                    active.add(rk)
                    states = PSTATE_LOCAL.setdefault(pid, {})
                    try:
                        r = LOCAL_PROV.get(rk)
                        if r is None:
                            r = LOCAL_PROV[rk] = PROVIDERS[pid](home, pricing=pricing, cache=False, root=root)
                        snap = r.tick()
                        if "recent" not in snap and key in states:
                            snap["recent"] = states[key].get("recent")
                        st = states[key] = {"ok": True, "error": None, "updated": time.time(), **snap}
                        if pid == "claude" and key == "local":
                            put_state("claude", snap["usage"], True)
                        if rk in synced:
                            ev = r.drain_events()
                        else:
                            since = (STORE.last_event_ts(key).get(pid) or 0) if STORE else 0
                            ev = r.all_events(max(0, since - 600))
                            synced.add(rk)
                            pending = STORE.backfill_pending(key, [pid]) if STORE else {}
                            if pid in pending:
                                threading.Thread(target=local_backfill, args=(pid, r, pending[pid], key), daemon=True,
                                                 name=f"backfill-{pid}-{key}").start()
                        store_events(key, pid, snap.get("account"), ev)
                        persist(key, pid, st)
                        ERRORS.pop(f"provider:{pid}:{key}", None)
                    except Exception:
                        ERRORS[f"provider:{pid}:{key}"] = traceback.format_exc(limit=3)
                        if key in states:
                            states[key] = {**states[key], "ok": False, "error": "수집 오류"}
            for rk in list(LOCAL_PROV):   # 지워지거나 꺼진 계정 폴더
                if rk not in active:
                    LOCAL_PROV.pop(rk, None)
                    (PSTATE_LOCAL.get(rk[0]) or {}).pop(rk[1], None)
                    synced.discard(rk)
        except Exception:
            ERRORS["providers"] = traceback.format_exc(limit=3)
        time.sleep(max(0.5, interval - (time.time() - t)))


def provider_states(pid):
    """공급자 하나의 PC별 최신 결과 {machine_id: state} (기록이 없는 PC는 빠짐)."""
    out = dict(PSTATE_LOCAL.get(pid) or {})
    with MLOCK:
        items = list(MACHINES.items())
    for mid, m in items:
        for key, st in m.provider_states(pid).items():
            if not st.get("absent") and (st.get("usage") or st.get("ok")):
                out[key] = st
    return out


def apply_machines():
    """DB의 PC 목록과 실행 중인 원격 PC를 맞춤: 새 PC 시작, 지워지거나 꺼진 PC 중지, 설정이 바뀐 PC 재시작."""
    rows = {r["id"]: {**r, "_profiles": remote_profiles_cfg(r["id"])} for r in machine_rows() if r["kind"] == "ssh"}
    with MLOCK:
        for mid in list(MACHINES):
            if mid not in rows or rows[mid] != MACHINE_ROWS.get(mid):
                MACHINES.pop(mid).stop()
                MACHINE_ROWS.pop(mid, None)
        for mid, row in rows.items():
            if mid not in MACHINES:
                MACHINES[mid] = RemoteMachine(row, interval=setting("interval.remote_claude_sec", 5),
                                              sys_interval=setting("interval.remote_sec", 10),
                                              on_detect=on_detect, on_events=store_events,
                                              since_fn=STORE.last_event_ts if STORE else None,
                                              backfill_fn=backfill_args, on_backfill=backfill_done,
                                              providers=enabled_providers).start()
                MACHINE_ROWS[mid] = row
        first = next(iter(rows.values()), None)
        new_primary = first["id"] if first else None
        if new_primary != PRIMARY["id"]:
            if PRIMARY["id"] is not None:   # 다른 PC로 바뀜: 이전 PC의 화면 값을 비움
                STATE["remote"] = STATE["remote_claude"] = None
            PRIMARY["id"] = new_primary
        STATE["remote_label"] = first["name"] if first else "원격"


def machines_loop():
    """원격 PC들의 최신 결과를 2초마다 모아 기록하고, 첫 번째 원격 PC는 v1 화면 형식(remote, remote_claude)으로 둠."""
    last_sample = {}
    last_net = 0.0
    last_scan = 0.0
    while True:
        try:
            with MLOCK:
                items = list(MACHINES.items())
                primary = PRIMARY["id"]
            if STORE and time.time() - last_scan > 300:
                last_scan = time.time()
                added = 0
                for mid, m in items:
                    try:
                        added += STORE.auto_profiles(mid, m.describe().get("profiles_found"))
                    except Exception:
                        ERRORS[f"profiles:{mid}"] = traceback.format_exc(limit=2)
                if added:
                    apply_machines()
            net_due = time.time() - last_net >= 10
            if net_due:
                last_net = time.time()
            for mid, m in items:
                if net_due and STORE:   # 연결 품질: 10초마다 표본 (끊겨 있어도 '끊김' 표본으로 남김)
                    try:
                        smp = m.net_sample()
                        if smp:
                            STORE.record_net(mid, time.time(), *smp)
                    except Exception:
                        ERRORS[f"net:{mid}"] = traceback.format_exc(limit=3)
                st = m.system_state()
                if st.get("sample_t") and st["sample_t"] != last_sample.get(mid):
                    last_sample[mid] = st["sample_t"]
                    record_remote(mid, st)
                for pid in PROVIDERS:
                    for key, pst in m.provider_states(pid).items():
                        persist(key, pid, pst)
                if mid == primary:
                    put_state("remote", st, st["ok"], st.get("sample_t"))
                    cl = m.claude_state()
                    put_state("remote_claude", cl, cl["ok"])
            ERRORS.pop("machines", None)
        except Exception:
            ERRORS["machines"] = traceback.format_exc(limit=3)
        time.sleep(2)


def remote_claude_states():
    """원격 PC 기본 폴더의 Claude 결과 중 이 PC 기본 폴더와 같은 계정(이름)인 것만 — 다른 계정 한도와 섞이지 않게."""
    with MLOCK:
        items = list(MACHINES.values())
    labels = account_labels("claude")
    mine = labels.get("local", "기본")
    return [m.claude_state() for m in items if labels.get(m.id, mine) == mine]


def start_collectors():
    local_m = LocalMachine({"id": "local", "name": "이 PC"})
    pricing = setting("claude.pricing_per_mtok_usd", CFG.get("pricing_per_mtok_usd"))
    anchor = None
    anchor_iso = setting("claude.weekly_reset_anchor", CFG.get("weekly_reset_anchor"))
    if anchor_iso:
        anchor = datetime.fromisoformat(anchor_iso.replace("Z", "+00:00")).timestamp()
    apply_machines()

    plan_api = PLAN["api"] = PlanAPI(min_interval=180)

    def live():
        # 같은 계정이므로 플랜 한도는 모든 PC 기록 중 가장 최신 쪽 하나만 사용 (섞으면 추이가 들쭉날쭉해짐)
        cands = []
        loc = read_limits(anchor)
        if loc:
            cands.append({**loc, "machine": "local"})
        remotes = [c for c in remote_claude_states() if c.get("usage") or c.get("limits")]
        for c in remotes:
            if c.get("limits"):
                cands.append({**c["limits"], "machine": "remote"})
        best = max(cands, key=lambda c: c.get("captured_at") or 0) if cands else None
        record_limits(best)
        # 1순위: Claude 계정 사용량 API (정확한 사용률·리셋 시각). 10분 넘게 갱신 못 하면 기록 기반으로 돌아감
        api = plan_api.snapshot()
        if api and time.time() - api["captured_at"] < 600:
            record_limits(api)
            best = {**api, "machine": "api", "history": (best or {}).get("history", [])}
        # 5시간 리셋 시각: 데스크톱 기록에는 없으므로 모든 PC의 메시지 시각을 합쳐 추정
        fh = (best or {}).get("five_hour")
        if fh is not None and not fh.get("resets_at"):
            ts = list((STATE.get("claude") or {}).get("recent_ts") or [])
            for c in remotes:
                ts += (c.get("usage") or {}).get("recent_ts") or []
            win = five_hour_window(ts)
            if win:
                best = {**best, "five_hour": {**fh, "resets_at": win["end"], "estimated": True,
                                              "estimated_from": "both" if remotes else "local"}}
        extra_plan_tick()
        return {"limits": finalize_limits(best, anchor),
                "sessions": ((PSTATE_LOCAL.get("claude") or {}).get("local") or {}).get("sessions") or [],
                "plan_api": plan_api.status()}

    def record_limits(raw):
        # 한도 스냅샷 기록 (출처 시각이 바뀔 때만). 로그 기반 추정 리셋 시각은 기록하지 않음
        if not STORE or not raw:
            return
        try:
            for win in ("five_hour", "seven_day"):
                w = raw.get(win) or {}
                STORE.record_limit("claude", win, raw.get("captured_at"), w.get("used_percentage"),
                                   w.get("resets_at"), raw.get("source"))
        except Exception:
            ERRORS["record:limits"] = traceback.format_exc(limit=3)

    jobs = [
        ("local", local_m.local.snapshot, setting("interval.local_sec", CFG["local_interval_sec"])),
        ("local_extras", local_m.extras.snapshot, 10),
        ("live", live, 3),
    ]
    for name, fn, iv in jobs:
        threading.Thread(target=loop, args=(name, fn, iv), daemon=True, name=name).start()
    threading.Thread(target=local_providers_loop, daemon=True, name="providers",
                     args=(setting("interval.claude_sec", CFG["claude_interval_sec"]), pricing)).start()
    threading.Thread(target=machines_loop, daemon=True, name="machines").start()
    threading.Thread(target=alerts_loop, daemon=True, name="alerts").start()
    threading.Thread(target=UPDATER.background, args=(lambda t, m: desktop.notify(t, m),), daemon=True, name="update-check").start()


PUSHER = secure.Pusher(STORE) if STORE else None   # 휴대폰 알림 (Web Push)


def _send_alert(title, msg):
    desktop.notify(title, msg)          # 이 PC: Windows 알림
    if PUSHER:
        PUSHER.send_all(title, msg)     # 알림을 켠 아이폰·아이패드 등


ALERTS = Alerts(send=_send_alert,
                enabled=lambda kind: bool(setting(f"notify.{kind}", True)))


def alerts_loop():
    """5초마다 알림 조건 확인 (세션 작업 끝·한도·연결·디스크·CPU·컨텍스트)."""
    time.sleep(20)   # 켠 직후 첫 수집이 끝나길 기다림
    while True:
        try:
            v2 = _v2_state()
            ALERTS.check(v2.get("ai"), v2.get("machines"), v2.get("systems"), STATE.get("local"), _machine_names().get)
            ERRORS.pop("alerts", None)
        except Exception:
            ERRORS["alerts"] = traceback.format_exc(limit=3)
        time.sleep(5)


def shutdown():
    """종료 직전 정리: 원격 연결 끊기(원격 에이전트도 곧 스스로 종료), 진행 중인 분 기록 저장. 트레이 '종료'에서도 호출."""
    with MLOCK:
        for m in MACHINES.values():
            m.stop()
    if STORE:
        try:
            STORE.flush()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_app):
    # 수집기 준비(사양 조회 등 수 초)를 기다리지 않고 서버가 바로 응답하도록 별도 스레드에서 시작
    load_cache()
    threading.Thread(target=start_collectors, daemon=True, name="startup").start()
    threading.Thread(target=save_cache_loop, daemon=True, name="cache").start()
    if STORE:
        threading.Thread(target=maintenance_loop, daemon=True, name="maintenance").start()
    yield
    shutdown()


app = FastAPI(title="RuiBotte Deck", lifespan=lifespan)


@app.middleware("http")
async def token_guard(request: Request, call_next):
    """같은 PC에서의 접속은 허용, 다른 기기는 데이터(/api/state)에 ?t=<token> 필요.
    화면 파일(HTML/JS/CSS)과 상태 확인(/api/health)에는 데이터가 없으므로 토큰 없이 허용."""
    host = request.client.host if request.client else ""
    path = request.url.path
    needs_token = path.startswith("/api/") and path != "/api/health"
    if needs_token and host not in ("127.0.0.1", "::1") and request.query_params.get("t") != CFG.get("token"):
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    resp = await call_next(request)
    # 캐시: 새 버전이면 아이폰·아이패드 사파리도 바로 새 화면을 받도록
    #   이름에 내용 해시가 붙은 파일(/assets/…)은 오래 보관, 페이지·서비스워커·앱 정보·API는 매번 확인
    if path.startswith("/assets/"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif not path.startswith("/api/") or path == "/api/health":
        resp.headers["Cache-Control"] = "no-cache"
    else:
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.get("/api/health")
def health():
    """start.bat이 서버 기동 여부만 빠르게 확인하는 용도 (작은 응답)"""
    return {"ok": True}


def _v2_state():
    """v2 화면용: 켜진 PC 목록 + 원격 PC 전부의 시스템 상태 + 공급자별 PC별 실시간 결과·한도."""
    with MLOCK:
        running = dict(MACHINES)
    machines, systems = [], {}
    for r in machine_rows():
        if r["kind"] == "local":
            machines.append({"id": "local", "name": r["name"], "kind": "local", "online": bool(STATE.get("local"))})
            continue
        m = running.get(r["id"])
        st = m.system_state() if m else None
        systems[r["id"]] = st
        machines.append({"id": r["id"], "name": r["name"], "kind": "ssh", "online": bool(st and st["ok"])})
    ai = {}
    for pid in enabled_providers():
        states = provider_states(pid)
        det = _detected_on(pid)
        if not det and not states:
            continue
        slim = {mid: {k: ({kk: vv for kk, vv in v.items() if kk != "recent_ts"} if k == "usage" and v else v)
                      for k, v in st.items() if k != "recent"} for mid, st in states.items()}
        labels = account_labels(pid, states)
        ai[pid] = {"label": PROVIDERS[pid].label, "machines": sorted(det), "states": slim,
                   "limits": provider_limits(pid, states),
                   "accounts": {k: labels.get(k, "기본") for k in slim}}   # 키 -> 계정 이름 (같은 이름 = 같은 계정)
    return {"machines": machines, "systems": systems, "ai": ai, "alerts": list(ALERTS.log)[:15],
            "names": _machine_names(), "version": desktop.APP_VERSION,
            "setup_done": bool(STORE.get("setup.done")) if STORE else True,
            "theme": {"mode": setting("display.theme", "sun"), "light_from": setting("display.light_from", "07:00"),
                      "dark_from": setting("display.dark_from", "19:00"),
                      "lat": setting("display.lat", None), "lon": setting("display.lon", None)}}


def _fmt_tok(n):
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            return f"{n / div:.{2 if n < div * 10 else 1}f}{unit}"
    return str(int(n))


def _fmt_left(sec):
    sec = max(0, int(sec))
    h, m = divmod(sec // 60, 60)
    return f"{h // 24}일 {h % 24}시간" if h >= 24 else f"{h}시간 {m}분" if h else f"{m}분"


WIN_SHORT = {"five_hour": "5시간", "seven_day": "주간", "one_day": "일간", "daily_requests": "오늘 요청"}


def tray_summary():
    """트레이 아이콘용 요약: tip = 마우스를 올렸을 때(128자 제한), lines = 우클릭 메뉴 맨 위 정보 줄."""
    v2 = _v2_state()
    tip, lines = [], []
    for pid, P in v2["ai"].items():
        wins = [w for w in (P.get("limits") or {}).get("windows") or [] if w.get("used_pct") is not None]
        today = sum(((st.get("usage") or {}).get("today") or {}).get("total", 0) for st in P["states"].values())
        sess = [x for st in P["states"].values() for x in st.get("sessions") or []]
        work = sum(1 for x in sess if x.get("state") == "working")
        wait = sum(1 for x in sess if x.get("state") == "waiting")
        short = " · ".join(f"{WIN_SHORT.get(w['key'], w['key'])} {round(100 - w['used_pct'])}%" for w in wins)
        tip.append(f"{P['label']}: {short + ' 남음' if short else '한도 정보 없음'}")
        lines.append(P["label"])
        for w in wins:
            reset = f" · {_fmt_left(w['reset_in_sec'])} 뒤 리셋" if w.get("reset_in_sec") else ""
            lines.append(f"    {WIN_SHORT.get(w['key'], w['key'])} 한도 {round(100 - w['used_pct'])}% 남음{reset}")
        act = f"오늘 {_fmt_tok(today)} 토큰" + (f" · 작업 중 {work}" if work else "") + (f" · 입력 대기 {wait}" if wait else "")
        tip.append("  " + act)
        lines.append("    " + act)
    with MLOCK:
        running = dict(MACHINES)
    for m in v2["machines"]:
        if m["kind"] != "ssh":
            continue
        rm = running.get(m["id"])
        rtt = getattr(rm, "rtt_ms", None) if rm else None
        lines.append(f"{m['name']}: " + (f"연결됨 · {rtt:.0f}ms" if m["online"] and rtt is not None else "연결됨" if m["online"] else "끊김"))
        if not m["online"]:
            tip.append(f"{m['name']} 끊김")
    if v2["alerts"]:
        a = v2["alerts"][0]
        lines.append(f"최근 알림 {time.strftime('%H:%M', time.localtime(a['t']))} · {a['title']}"[:60])
    text = "\n".join([desktop.APP_NAME, *tip])
    return {"tip": text if len(text) <= 127 else text[:126] + "…", "lines": lines}


@app.get("/api/state")
def state():
    try:
        v2 = _v2_state()
    except Exception:
        ERRORS["state"] = traceback.format_exc(limit=3)
        v2 = {}
    return JSONResponse(json.loads(json.dumps({**STATE, **v2, "errors": ERRORS, "updated": UPDATED, "now": time.time()},
                                              default=str)),
                        headers={"Cache-Control": "no-store"})


# ---------- v2: PC 관리 · 설정 ----------
def _need_store():
    if not STORE:
        raise HTTPException(503, "내장 DB를 열 수 없어 설정을 바꿀 수 없습니다")


def _clean_machine(body, partial=False):
    """요청 값 검증. SSH 대상·WSL 이름은 원격 명령줄에 들어가므로 형식을 엄격히 제한."""
    out = {}
    try:
        if "name" in body or not partial:
            name = str(body.get("name") or body.get("ssh_target") or "").strip()
            if not name or len(name) > 40:
                raise ValueError("이름은 1~40자로 입력하세요")
            out["name"] = name
        if "ssh_target" in body or not partial:
            out["ssh_target"] = normalize_target(body.get("ssh_target"))
        if "ssh_fallbacks" in body:
            fb = body.get("ssh_fallbacks") or []
            if isinstance(fb, str):
                fb = [x for x in re.split(r"[,\s]+", fb) if x]
            out["ssh_fallbacks"] = [normalize_target(x) for x in fb][:5]
        if "wsl_distro" in body:
            out["wsl_distro"] = validate_distro((body.get("wsl_distro") or "").strip())
        if "home_override" in body:
            h = (body.get("home_override") or "").strip()
            if len(h) > 260 or any(c in h for c in "\r\n\0"):
                raise ValueError("홈 경로가 올바르지 않습니다")
            out["home_override"] = h or None
        if "enabled" in body:
            out["enabled"] = bool(body["enabled"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return out


def _all_rows():
    return STORE.machines() if STORE else machine_rows()


def _machine_view(row):
    mid = row["id"]
    if row["kind"] == "local":
        loc = STATE.get("local") or {}
        return {**row, "online": bool(loc),
                "system": {"cpu": loc.get("cpu"), "mem": (loc.get("mem") or {}).get("pct"), "host": loc.get("host")}}
    with MLOCK:
        m = MACHINES.get(mid)
    st = m.system_state() if m else {}
    w = st.get("windows") or {}
    return {**row, "online": bool(st.get("ok")), "status": m.describe() if m else None,
            "detect": STORE.get(f"machine.{mid}.detect") if STORE else None,
            "system": {"cpu": w.get("cpu"),
                       "mem": round(100 * (1 - (w.get("mem_free") or 0) / w["mem_total"]), 1) if w.get("mem_total") else None,
                       "host": w.get("host"), "error": st.get("error")}}


def _json(obj):
    return JSONResponse(json.loads(json.dumps(obj, default=str)), headers={"Cache-Control": "no-store"})


@app.get("/api/machines")
def list_machines():
    return _json({"machines": [_machine_view(r) for r in _all_rows()]})


@app.get("/api/machines/{mid}")
def get_machine(mid: str):
    row = next((r for r in _all_rows() if r["id"] == mid), None)
    if not row:
        raise HTTPException(404, "없는 PC")
    out = _machine_view(row)
    if row["kind"] == "local":
        out["state"] = {"system": STATE.get("local"), "extras": STATE.get("local_extras")}
    else:
        with MLOCK:
            m = MACHINES.get(mid)
        out["state"] = {"system": m.system_state(), "claude": m.claude_state()} if m else None
    return _json(out)


@app.post("/api/machines/test")
def test_machine(body: dict = Body(...)):
    """연결 테스트: SSH 접속, OS·WSL·Python 감지, 홈 폴더 후보와 AI 도구 감지 결과 (저장하지 않음)."""
    v = _clean_machine({**body, "name": "test"})
    try:
        return detect(v["ssh_target"], v.get("wsl_distro"), v.get("home_override"))
    except Exception as e:
        return {"ok": False, "error": repr(e)[:300]}


@app.post("/api/machines")
def add_machine(body: dict = Body(...)):
    _need_store()
    v = _clean_machine(body)
    mid = STORE.add_machine(v["name"], v["ssh_target"], v.get("ssh_fallbacks", []), v.get("wsl_distro"),
                            v.get("home_override"))
    apply_machines()
    return {"ok": True, "machine": STORE.machine(mid)}


@app.put("/api/machines/{mid}")
def update_machine(mid: str, body: dict = Body(...)):
    _need_store()
    row = STORE.machine(mid)
    if not row:
        raise HTTPException(404, "없는 PC")
    if row["kind"] == "local":
        body = {k: body[k] for k in ("name", "sort") if k in body}
    v = _clean_machine(body, partial=True)
    if "sort" in body:
        v["sort"] = int(body["sort"])
    STORE.update_machine(mid, **v)
    apply_machines()
    return {"ok": True, "machine": STORE.machine(mid)}


@app.delete("/api/machines/{mid}")
def delete_machine(mid: str):
    _need_store()
    if not STORE.machine(mid):
        raise HTTPException(404, "없는 PC")
    try:
        STORE.delete_machine(mid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    apply_machines()
    return {"ok": True}


# ---------- AI 계정 (로그인 폴더) — docs/ACCOUNTS_DESIGN.md ----------
@app.get("/api/profiles")
def get_profiles():
    """PC·도구별 계정 폴더 목록(기본 + 추가)과 찾은 후보 폴더."""
    _need_store()
    rows = STORE.profiles()
    with MLOCK:
        running = dict(MACHINES)
    pstates = {pid: provider_states(pid) for pid in enabled_providers()}
    out = []
    for m in machine_rows():
        mid = m["id"]
        if mid == "local":
            found = find_profiles(Path.home())
            detected = set(detect_providers(Path.home()))
        else:
            d = running[mid].describe() if mid in running else {}
            found = d.get("profiles_found") or {}
            detected = set(d.get("active_providers") or d.get("providers") or [])
        for pid in enabled_providers():
            mine = [x for x in rows if x["machine_id"] == mid and x["provider"] == pid]
            default = next((x for x in mine if not x["path"]), None)
            extras = [x for x in mine if x["path"]]
            if pid not in detected and not extras and mid != "local":
                continue
            known = {x["path"] for x in extras}
            out.append({"machine_id": mid, "machine": m["name"], "provider": pid, "label": PROVIDERS[pid].label,
                        "default": {"id": default["id"] if default else None, "label": (default or {}).get("label") or "",
                                    "key": mid, "detected": pid in detected, "account": auto_label(pstates[pid].get(mid))},
                        "extra": [{**x, "key": profile_key(mid, x["id"]),
                                   "account": auto_label(pstates[pid].get(profile_key(mid, x["id"])))} for x in extras],
                        "found": [f for f in found.get(pid, []) if f not in known]})
    return _json({"slots": out})


_BAD_PATH = re.compile(r"[\x00-\x1f]")


@app.post("/api/profiles")
def add_profile(body: dict = Body(...)):
    """계정 폴더 등록 {machine_id, provider, path, label}. path '' = 기본 폴더에 이름만 붙임."""
    _need_store()
    mid, pid = body.get("machine_id"), body.get("provider")
    path, label = (body.get("path") or "").strip(), (body.get("label") or "").strip()[:40]
    if pid not in PROVIDERS or not any(r["id"] == mid for r in machine_rows()):
        raise HTTPException(400, "PC 또는 도구가 올바르지 않습니다")
    if path:
        if _BAD_PATH.search(path) or len(path) > 400:
            raise HTTPException(400, "폴더 경로가 올바르지 않습니다")
        if mid == "local":
            if not Path(path).expanduser().is_absolute():
                raise HTTPException(400, "전체 경로로 입력해 주세요 (예: C:\\Users\\me\\.claude-personal)")
            path = str(Path(path).expanduser())
            if not profile_detect(pid, path):
                raise HTTPException(400, f"이 폴더에서 {PROVIDERS[pid].label} 기록을 찾지 못했어요. 그 계정으로 한 번 실행한 뒤 다시 등록하세요.")
        elif not (path.startswith("/") or path.startswith("~")):
            raise HTTPException(400, "원격 PC 폴더는 /로 시작하는 전체 경로로 입력해 주세요 (WSL이면 /mnt/c/Users/...)")
        if not label:
            raise HTTPException(400, "계정 이름을 입력해 주세요 (예: 개인, 회사)")
    new_id = STORE.add_profile(mid, pid, path, label)
    apply_machines()   # 원격 PC는 계정 폴더가 바뀌면 다시 연결
    return {"ok": True, "id": new_id}


@app.put("/api/profiles/{prof}")
def put_profile(prof: str, body: dict = Body(...)):
    _need_store()
    if not STORE.profile(prof):
        raise HTTPException(404, "없는 계정")
    STORE.update_profile(prof, **{k: (v.strip()[:40] if k == "label" else v) for k, v in body.items() if k in ("label", "enabled")})
    apply_machines()
    return {"ok": True}


@app.delete("/api/profiles/{prof}")
def delete_profile(prof: str):
    """계정 폴더 등록 해제 (그 폴더의 기록 파일과 DB 기록은 그대로 둠)."""
    _need_store()
    STORE.delete_profile(prof)
    apply_machines()
    return {"ok": True}


@app.post("/api/claude/limits/refresh")
def refresh_claude_limits():
    """Claude 한도 '새로고침': 대기 시간을 무시하고 토큰 갱신·사용량 조회를 지금 한 번 시도 (최소 1분 간격)."""
    api = PLAN["api"]
    if not api:
        raise HTTPException(503, "아직 준비 중이에요")
    return api.force()


@app.post("/api/claude/login")
def claude_login(request: Request, dry: int = 0):
    """'다시 로그인': 이 PC에 PowerShell 창을 띄워 `claude auth login` 실행(브라우저 로그인 화면이 열림).
    로그인을 마치면 자격 증명 파일이 바뀌고 plan_api가 알아채 바로 다시 조회한다. 이 PC 화면에서만 허용.
    dry=1 이면 실행하지 않고 어떤 명령을 쓸지만 돌려줌(점검용)."""
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "이 PC의 대시보드 화면에서만 할 수 있어요")
    exe = find_claude_cli()
    if not exe:
        return {"ok": False, "message": "Claude Code CLI를 찾지 못했어요. PowerShell에서 "
                "irm https://claude.ai/install.ps1 | iex 로 설치한 뒤 다시 눌러 주세요."}
    if dry:
        return {"ok": True, "dry": True, "cli": exe}
    if sys.platform == "win32":
        # 보이는 새 창에서 실행 (트레이는 콘솔이 없으므로 새 콘솔을 만듦). 대시보드를 꺼도 창은 남도록 Job에 넣지 않음
        subprocess.Popen(["powershell", "-NoExit", "-NoProfile", "-Command",
                          f"Write-Host 'Claude 계정으로 다시 로그인합니다. 브라우저에서 로그인을 마친 뒤 이 창을 닫아 주세요.';"
                          f" & '{exe}' auth login"],
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        return {"ok": False, "message": f"터미널에서 '{exe} auth login' 을 실행해 주세요."}
    if PLAN["api"]:
        PLAN["api"].manual_fails = 0
    return {"ok": True, "message": "로그인 창을 열었어요. 브라우저에서 로그인을 마치면 자동으로 다시 조회해요."}


LOGIN_CLI = {"claude": ("CLAUDE_CONFIG_DIR", ".claude", ["auth", "login"]), "codex": ("CODEX_HOME", ".codex", ["login"])}
CLI_LABEL = {"claude": "Claude Code CLI", "codex": "Codex CLI"}
# CLI가 없을 때 (사용자가 '설치'를 누르면) 공식 설치 방법으로 설치한 뒤 같은 창에서 이어서 로그인. 끝나면 $cli 에 실행 파일 경로
INSTALL_PS = {
    "claude": r"irm https://claude.ai/install.ps1 | iex; $cli = Join-Path $HOME '.local\bin\claude.exe'",
    "codex": ("if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Write-Host 'Node.js 설치 중...';"
              " winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements;"
              " $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User') };"
              r" npm i -g @openai/codex; $cli = Join-Path $env:APPDATA 'npm\codex.cmd'"),
}


def find_codex_cli():
    p = shutil.which("codex")
    if p:
        return p
    c = Path(os.environ.get("APPDATA", "")) / "npm" / "codex.cmd"
    return str(c) if c.exists() else None


@app.post("/api/profiles/login")
def profile_login(request: Request, body: dict = Body(...)):
    """'새 계정 로그인' (이 PC): 그 계정 전용 폴더(~/.claude-<이름>)를 만들어 등록하고, 그 폴더로 CLI 로그인 창을 띄움.
    토큰은 CLI가 그 폴더에 저장 — 대시보드는 복사·저장하지 않음."""
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "이 PC의 대시보드 화면에서만 할 수 있어요")
    _need_store()
    pid, label = body.get("provider"), (body.get("label") or "").strip()[:40]
    default = bool(body.get("default"))   # 기본 폴더(~/.claude 등)로 로그인
    if pid not in LOGIN_CLI:
        raise HTTPException(400, "Claude Code 또는 GPT(Codex)만 지원해요")
    name = re.sub(r"[^\w-]", "", label)
    if not name and not default:
        raise HTTPException(400, "계정 이름을 입력해 주세요 (예: 개인)")
    env, base, args = LOGIN_CLI[pid]
    exe = find_claude_cli() if pid == "claude" else find_codex_cli()
    install = not exe
    if install and not body.get("install"):   # 화면이 '설치할까요?'를 묻고, 확인하면 install=true 로 다시 부름
        return {"ok": False, "need_install": True, "cli": CLI_LABEL[pid],
                "message": f"{CLI_LABEL[pid]}가 이 PC에 없어요. 설치한 뒤 이어서 로그인할까요?"}
    if install and sys.platform != "win32":
        raise HTTPException(400, f"{CLI_LABEL[pid]}를 먼저 설치해 주세요")
    if default:
        folder = None
        if label:
            STORE.add_profile("local", pid, "", label)
    else:
        folder = Path.home() / f"{base}-{name}"
        folder.mkdir(exist_ok=True)
        STORE.add_profile("local", pid, str(folder), label)
    if sys.platform != "win32":
        return {"ok": True, "folder": str(folder), "message": f"터미널에서 {env}='{folder}' {exe} {' '.join(args)} 를 실행해 주세요."}
    q = lambda s: "'" + str(s).replace("'", "''") + "'"   # noqa: E731 (PowerShell 작은따옴표 문자열)
    script = (f"Write-Host {q(CLI_LABEL[pid] + ' 설치 중...')}; {INSTALL_PS[pid]}; " if install else f"$cli = {q(exe)}; ") \
        + (f"$env:{env}={q(folder)}; " if folder else "") \
        + f"Write-Host {q(f'[{label or PROVIDERS[pid].label}] 계정으로 로그인합니다. 브라우저에서 로그인을 마친 뒤 이 창을 닫아 주세요.')};" \
        + f" & $cli {' '.join(args)}"
    subprocess.Popen(["powershell", "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                     creationflags=subprocess.CREATE_NEW_CONSOLE)
    apply_machines()
    return {"ok": True, "folder": str(folder) if folder else None,
            "message": ("설치 창을 열었어요. 설치가 끝나면 같은 창에서 로그인이 이어져요." if install
                        else "로그인 창을 열었어요. 브라우저에서 로그인을 마치면 됩니다.")
            + (f" 이 계정으로 쓰려면 {env} 를 이 폴더로 지정해 CLI를 실행하세요." if folder else "")}


# ---------- 업데이트 (updater.py) ----------
@app.get("/api/update")
def get_update(check: int = 0):
    """최신 버전 정보 + 설치 진행 상황. check=1 이면 지금 다시 확인."""
    if check or not UPDATER.info:
        UPDATER.check(force=bool(check))
    return UPDATER.status()


@app.post("/api/update/install")
def install_update(request: Request):
    """받아서 설치 시작 (이 PC에서만). 설치 프로그램이 앱을 끝내고 설치한 뒤 다시 실행."""
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "이 PC의 대시보드 화면에서만 할 수 있어요")
    try:
        UPDATER.install()
    except ValueError as e:
        raise HTTPException(400, str(e))
    return UPDATER.status()


@app.get("/api/notify")
def get_notify():
    return {"kinds": ALERT_KINDS, "enabled": {k: bool(setting(f"notify.{k}", True)) for k in ALERT_KINDS},
            "recent": list(ALERTS.log)}


@app.post("/api/notify/test")
def notify_test(request: Request):
    """알림 시험 (이 PC에서만)."""
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "이 PC에서만 할 수 있어요")
    desktop.notify("RuiBotte Deck 알림 시험", "이 알림이 보이면 알림이 정상이에요.")
    return {"ok": True, "tray": desktop.NOTIFY_FN is not None}


@app.get("/api/displays")
def get_displays():
    """대시보드 창을 띄울 수 있는 화면 목록 (Windows). 현재 설정값과 함께."""
    return {"displays": desktop.displays(), "selected": setting("display.monitor", "auto")}


@app.get("/api/app")
def get_app_info():
    return {"platform": sys.platform, "autostart": desktop.autostart_on(), "port": CFG["port"],
            "lan": CFG.get("bind") not in ("127.0.0.1", "localhost", "::1"),
            "name": desktop.APP_NAME, "version": desktop.APP_VERSION, "repo": desktop.REPO_URL,
            "installed": desktop.FROZEN, "data_dir": str(desktop.DATA)}


@app.get("/api/app/share")
def app_share(request: Request):
    """다른 기기 접속 정보 (토큰 포함 주소 → 화면의 QR). 토큰이 들어 있으니 이 PC에서만."""
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "이 PC에서만 볼 수 있어요")
    cfg = desktop.load_config()
    want = cfg.get("bind") not in ("127.0.0.1", "localhost", "::1")
    running = CFG.get("bind") not in ("127.0.0.1", "localhost", "::1")
    addrs = desktop.lan_addresses()
    hp = secure.https_port(CFG["port"])
    return {"lan": want, "active": running, "needs_restart": want != running, "port": CFG["port"], "https_port": hp,
            "addresses": [{**a, "url": f"http://{a['ip']}:{CFG['port']}/?t={cfg.get('token', '')}",
                           "https": f"https://{a['ip']}:{hp}/?t={cfg.get('token', '')}",
                           "ca": f"http://{a['ip']}:{CFG['port']}/ca.crt"} for a in addrs],
            "cert": secure.cert_info()}


# ---------- HTTPS · 휴대폰 알림 (secure.py) ----------
def lan_on():
    return CFG.get("bind") not in ("127.0.0.1", "localhost", "::1")


def https_server_config():
    """다른 기기 접속이 켜져 있으면 HTTPS 서버 설정 (포트+1, 로컬 CA가 발급한 인증서). 트레이가 따로 띄움."""
    if not lan_on():
        return None
    try:
        crt, key = secure.ensure_certs()
    except Exception:
        ERRORS["https"] = traceback.format_exc(limit=2)
        return None
    return uvicorn.Config(app, host=CFG["bind"], port=secure.https_port(CFG["port"]), ssl_certfile=str(crt),
                          ssl_keyfile=str(key), log_level="warning", lifespan="off")


@app.get("/ca.crt")
def ca_certificate():
    """휴대폰에 설치할 로컬 CA 인증서 (공개 정보, 토큰 불필요). 아이폰은 받으면 '프로파일 다운로드됨'."""
    from fastapi import Response
    return Response(secure.ca_cert_bytes(), media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="RuiBotteDeck-CA.crt"', "Cache-Control": "no-cache"})


@app.get("/api/push/info")
def push_info(request: Request):
    _, pub = secure.vapid()
    return {"public_key": pub, "https_port": secure.https_port(CFG["port"]), "lan": lan_on(),
            "secure": request.url.scheme == "https", "devices": len(STORE.push_subs()) if STORE else 0, **secure.cert_info()}


@app.post("/api/push/subscribe")
def push_subscribe(body: dict = Body(...)):
    """이 기기 알림 켜기: 브라우저가 만든 구독(endpoint·keys)을 저장."""
    _need_store()
    sub = body.get("subscription") or {}
    ep, keys = sub.get("endpoint"), sub.get("keys") or {}
    if not (isinstance(ep, str) and ep.startswith("https://") and keys.get("p256dh") and keys.get("auth")):
        raise HTTPException(400, "구독 정보가 올바르지 않아요")
    STORE.push_add(ep, {"p256dh": keys["p256dh"], "auth": keys["auth"]}, str(body.get("device") or "")[:80])
    return {"ok": True}


@app.post("/api/push/unsubscribe")
def push_unsubscribe(body: dict = Body(...)):
    _need_store()
    STORE.push_remove(str(body.get("endpoint") or ""))
    return {"ok": True}


@app.get("/api/push/devices")
def push_devices():
    _need_store()
    return {"devices": [{"id": i, "device": s["device"] or "기기", "created_at": s["created_at"], "last_ok": s["last_ok"],
                         "service": s["endpoint"].split("/")[2]} for i, s in enumerate(STORE.push_subs())]}


@app.delete("/api/push/devices/{idx}")
def push_device_remove(idx: int):
    _need_store()
    subs = STORE.push_subs()
    if 0 <= idx < len(subs):
        STORE.push_remove(subs[idx]["endpoint"])
    return {"ok": True}


@app.post("/api/push/test")
def push_test():
    n = PUSHER.send_all(f"{desktop.APP_NAME} 알림 시험", "이 알림이 보이면 휴대폰 알림이 정상이에요.", "test") if PUSHER else 0
    return {"ok": True, "devices": n}


@app.get("/api/logs")
def get_logs(request: Request, lines: int = 300):
    """server.log 끝부분 (설정 > 정보 > 로그 보기). 세션 제목 등이 들어 있으니 이 PC에서만."""
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "이 PC에서만 볼 수 있어요")
    path = desktop.DATA / "server.log"
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 400_000))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        text = ""
    out = text.splitlines()[-max(20, min(2000, lines)):]
    return {"path": str(path), "lines": out, "errors": {k: v.splitlines()[-1][:300] for k, v in ERRORS.items()}}


@app.post("/api/app/open-folder")
def open_data_folder(request: Request):
    """데이터 폴더(설정·기록·로그)를 탐색기로 열기 (이 PC에서만)."""
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "이 PC에서만 할 수 있어요")
    if sys.platform == "win32":
        os.startfile(str(desktop.DATA))   # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(desktop.DATA)])
    else:
        subprocess.Popen(["xdg-open", str(desktop.DATA)])
    return {"ok": True}


@app.post("/api/app/restart")
def app_restart(request: Request):
    """앱 다시 시작 (다른 기기 접속 켜기/끄기 적용 등). 이 PC에서만."""
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "이 PC에서만 할 수 있어요")

    def later():
        time.sleep(0.8)
        try:
            shutdown()
        except Exception:
            pass
        desktop.restart_app()
        desktop.close_windows(quick=True)
        os._exit(0)
    threading.Thread(target=later, daemon=True).start()
    return {"ok": True}


@app.put("/api/app")
def put_app_info(request: Request, body: dict = Body(...)):
    """자동 실행 켜기/끄기 — 이 PC의 시작 프로그램을 바꾸므로 같은 PC에서만."""
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "이 PC에서만 바꿀 수 있습니다")
    if "autostart" in body:
        desktop.set_autostart(bool(body["autostart"]))
    if "lan" in body:   # 다른 기기 접속: bind 를 바꾸고, 앱을 다시 시작하면 적용 (토큰이 없으면 새로 만듦)
        import secrets
        cfg = desktop.load_config()
        desktop.save_config(bind="0.0.0.0" if body["lan"] else "127.0.0.1", token=cfg.get("token") or secrets.token_urlsafe(9))
    return get_app_info()


@app.get("/api/settings")
def get_settings():
    _need_store()
    return {k: v for k, v in STORE.settings().items() if k in DEFAULTS}


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    """알려진 설정 키만 변경. 주기 변경은 다시 켤 때 적용."""
    _need_store()
    bad = [k for k in body if k not in DEFAULTS]
    if bad:
        raise HTTPException(400, f"알 수 없는 설정: {', '.join(bad)}")
    for k, v in body.items():
        if k.startswith(("retention.", "interval.")) and not (isinstance(v, (int, float)) and v >= 0):
            raise HTTPException(400, f"{k}: 0 이상의 숫자여야 합니다")
        if k == "display.theme" and v not in ("sun", "auto", "dark", "light"):
            raise HTTPException(400, "display.theme: sun | auto | dark | light")
        if k in ("display.lat", "display.lon") and v is not None and not (
                isinstance(v, (int, float)) and abs(v) <= (90 if k.endswith("lat") else 180)):
            raise HTTPException(400, f"{k}: 위도 -90~90, 경도 -180~180")
        if k in ("display.light_from", "display.dark_from") and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", str(v)):
            raise HTTPException(400, f"{k}: HH:MM 형식이어야 합니다")
    for k, v in body.items():
        STORE.set(k, v)
    return get_settings()


# ---------- v2: AI 공급자 ----------
RANGES = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}
WINDOW_MIN = {"five_hour": 300, "seven_day": 10080, "one_day": 1440, "daily_requests": 1440}


def _machine_names():
    names = {r["id"]: r["name"] for r in _all_rows()}
    if STORE:
        rows = [x for x in STORE.profiles() if x["path"] and x["machine_id"] in names]
        labels = {}
        for pid in {x["provider"] for x in rows}:
            labels.update(account_labels(pid))
        for x in rows:
            key = profile_key(x["machine_id"], x["id"])
            names[key] = f"{names[x['machine_id']]} · {labels.get(key) or Path(x['path']).name}"
    return names


def _range_days(rng):
    if rng not in RANGES:
        raise HTTPException(400, f"range는 {', '.join(RANGES)} 중 하나")
    return RANGES[rng]


def _day_start(days_back):
    d = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp()) - (days_back - 1) * 86400


def _norm_windows(windows, now):
    """한도 창마다 남은 시간·페이스(창에서 지난 시간 비율)를 붙임."""
    out = []
    for w in windows:
        w = dict(w)
        reset = w.get("resets_at")
        length = (w.get("window_minutes") or WINDOW_MIN.get(w.get("key")) or 0) * 60
        left = reset - now if reset and reset > now else None
        w["reset_in_sec"] = int(left) if left is not None else None
        w["elapsed_pct"] = round(100 * (1 - left / length), 1) if left is not None and length and left <= length else None
        out.append(w)
    return out


PLAN_EXTRA_SNAP = {}    # 키 -> (PlanAPI 결과, 상태)


def extra_plan_tick():
    """이 PC의 다른 Claude 계정 폴더마다 정확한 한도 조회 (폴더에 .credentials.json 이 있을 때만, 3분 간격·백오프는 PlanAPI가)."""
    if not STORE:
        return
    keep = set()
    for x in STORE.profiles("local"):
        if x["provider"] != "claude" or not x["path"] or not x["enabled"]:
            continue
        cred = Path(x["path"]) / ".credentials.json"
        if not cred.exists():
            continue
        keep.add(x["id"])
        api = PLAN_EXTRA.get(x["id"])
        if api is None or api.cred_path != cred:
            api = PLAN_EXTRA[x["id"]] = PlanAPI(cred_path=cred, min_interval=180)
        try:
            PLAN_EXTRA_SNAP[profile_key("local", x["id"])] = (api.snapshot(), api.status())
        except Exception:
            ERRORS[f"plan:{x['id']}"] = traceback.format_exc(limit=2)
    for k in list(PLAN_EXTRA):
        if k not in keep:
            PLAN_EXTRA.pop(k, None)
            PLAN_EXTRA_SNAP.pop(profile_key("local", k), None)


def _claude_windows(L, plan_status=None):
    """finalize_limits 결과 → 화면용 한도 {source, captured_at, windows, history, plan_api}."""
    now = time.time()
    wins = []
    for key in ("five_hour", "seven_day"):
        w = L.get(key)
        if w:
            wins.append({"key": key, "window_minutes": WINDOW_MIN[key], "used_pct": w.get("used_pct"),
                         "resets_at": w.get("resets_at"), "reset_passed": w.get("reset_passed"),
                         "estimated": w.get("estimated")})
    return {"source": L.get("source"), "captured_at": L.get("captured_at"), "windows": _norm_windows(wins, now),
            "history": L.get("history"), "plan_api": plan_status}


def _claude_group_limits(states):
    """기본이 아닌 Claude 계정 하나의 한도: 그 계정 폴더들의 정확한 값(PlanAPI, 10분 안) → 없으면 statusLine 기록 중 최신."""
    for key in states:
        api, status = PLAN_EXTRA_SNAP.get(key) or (None, None)
        if api and time.time() - api["captured_at"] < 600:
            return _claude_windows(finalize_limits({**api, "machine": "api"}), status)
    cands = [{**st["limits"], "machine": key} for key, st in states.items() if st.get("limits")]
    if not cands:   # 기록이 없어도 계정은 보여 줌 (화면: '이 계정의 한도 기록 없음')
        status = next((PLAN_EXTRA_SNAP[k][1] for k in states if k in PLAN_EXTRA_SNAP), None)
        return {"source": None, "captured_at": None, "windows": [], "plan_api": status}
    best = max(cands, key=lambda c: c.get("captured_at") or 0)
    return {**_claude_windows(finalize_limits(best)), "machine": best["machine"]}


def provider_limits(pid, states):
    """공급자 한도. 계정(이름)이 여러 개면 accounts 에 계정별로, 맨 위 값은 이 PC 기본 폴더의 계정."""
    labels = account_labels(pid, states)
    mine = labels.get("local", "기본")
    groups = {}
    for key, st in states.items():
        groups.setdefault(labels.get(key, "기본"), {})[key] = st
    if pid == "claude":
        groups.setdefault(mine, {})
        accts = []
        for label, sts in groups.items():
            lim = _single_limits(pid, sts) if label == mine else _claude_group_limits(sts)
            if lim:
                accts.append({**lim, "label": label, "keys": sorted(sts)})
    else:
        accts = [{**lim, "label": label, "keys": sorted(sts)} for label, sts in groups.items()
                 if (lim := _single_limits(pid, sts))]
    if not accts:
        return None
    main = next((a for a in accts if a["label"] == mine), None) or max(accts, key=lambda a: a.get("captured_at") or 0)
    out = dict(main)
    if len(accts) > 1:
        out["accounts"] = sorted(accts, key=lambda a: (a["label"] != mine, a["label"]))
    return out


def _single_limits(pid, states):
    """공급자 한도 요약. Claude는 기존 live() 결과(API·데스크톱 기록·추정), 그 외는 PC들의 로그 중 가장 최근 값."""
    now = time.time()
    if pid == "claude":
        L = (STATE.get("live") or {}).get("limits")
        if not L:
            return None
        wins = []
        for key in ("five_hour", "seven_day"):
            w = L.get(key)
            if w:
                wins.append({"key": key, "window_minutes": WINDOW_MIN[key], "used_pct": w.get("used_pct"),
                             "resets_at": w.get("resets_at"), "reset_passed": w.get("reset_passed"),
                             "estimated": w.get("estimated")})
        return {"source": L.get("source"), "captured_at": L.get("captured_at"), "windows": _norm_windows(wins, now),
                "history": L.get("history"), "plan_api": (STATE.get("live") or {}).get("plan_api")}
    by_acct = {}
    for mid, st in states.items():
        lim = st.get("limits")
        if not lim or not lim.get("windows"):
            continue
        acct = st.get("account") or ""
        if acct not in by_acct or (lim.get("captured_at") or 0) > (by_acct[acct].get("captured_at") or 0):
            by_acct[acct] = {**lim, "machine": mid, "account": acct or None}
    if not by_acct:
        return None
    best = max(by_acct.values(), key=lambda x: x.get("captured_at") or 0)
    out = {**best, "windows": _norm_windows(best["windows"], now)}
    if len(by_acct) > 1:   # 계정이 여러 개면 계정별로도
        out["by_account"] = [{**v, "windows": _norm_windows(v["windows"], now)} for v in by_acct.values()]
    return out


def _sum_today(states):
    acc = {"in": 0, "out": 0, "cw": 0, "cr": 0, "total": 0, "count": 0, "cost": 0.0}
    for st in states.values():
        t = (st.get("usage") or {}).get("today") or {}
        for k in acc:
            acc[k] += t.get(k) or 0
    return acc


def _sum_lists(states, key, n=60):
    out = [0] * n
    for st in states.values():
        for i, v in enumerate(((st.get("usage") or {}).get(key) or [])[-n:]):
            out[i] += v or 0
    return out


def _merge_dicts(states, key):
    out = {}
    for st in states.values():
        for k, v in ((st.get("usage") or {}).get(key) or {}).items():
            out[k] = out.get(k, 0) + (v or 0)
    return dict(sorted(out.items(), key=lambda x: -x[1]))


def _detected_on(pid):
    """그 공급자 기록이 있는 PC (최근 결과가 없어도 감지만 된 PC 포함)."""
    mids = set(provider_states(pid))
    if any(k[0] == pid and k[1] == "local" for k in LOCAL_PROV):
        mids.add("local")
    with MLOCK:
        items = list(MACHINES.items())
    for mid, m in items:
        if pid in (m.plist or []) or pid in ((m.info or {}).get("providers") or {}):
            mids.add(mid)
    return mids


@app.get("/api/providers")
def list_providers():
    enabled = set(enabled_providers())
    out = []
    for pid, cls in PROVIDERS.items():
        states = provider_states(pid)
        last = max((((st.get("usage") or {}).get("last_activity")) or "" for st in states.values()), default="")
        out.append({"id": pid, "label": cls.label, "enabled": pid in enabled, "machines": sorted(_detected_on(pid)),
                    "today": _sum_today(states), "last_activity": last or None,
                    "working": sum(1 for st in states.values() for x in st.get("sessions") or []
                                   if x.get("state") == "working"),
                    "waiting": sum(1 for st in states.values() for x in st.get("sessions") or []
                                   if x.get("state") == "waiting"),
                    "accounts": sorted({st.get("account") for st in states.values() if st.get("account")}),
                    "limits": provider_limits(pid, states)})
    return _json({"providers": out})


def _mcond(machine, col="machine_id"):
    """machine 쿼리: 키 하나 또는 쉼표로 여러 개(계정 하나의 여러 PC) → (SQL 조건, 인자)."""
    keys = [k for k in (machine or "").split(",") if k]
    if not keys:
        return "", []
    return f" AND {col} IN ({','.join('?' * len(keys))})", keys


@app.get("/api/providers/{pid}/summary")
def provider_summary(pid: str, range: str = "7d", machine: str = None):   # noqa: A002 (쿼리 이름)
    """machine 을 주면 그 PC만 (기간 합계·모델·실시간 모두)."""
    if pid not in PROVIDERS:
        raise HTTPException(404, "없는 공급자")
    days = _range_days(range)
    states = provider_states(pid)
    if machine:
        states = {k: v for k, v in states.items() if k in machine.split(",")}
    mcond, margs = _mcond(machine)
    out = {"id": pid, "label": PROVIDERS[pid].label, "range": range, "limits": provider_limits(pid, states),
           "today": _sum_today(states), "minutes": _sum_lists(states, "minutes"),
           "minutes_out": _sum_lists(states, "minutes_out"),
           "models_today": _merge_dicts(states, "models_today"), "models_week": _merge_dicts(states, "models_week"),
           "accounts": sorted({st.get("account") for st in states.values() if st.get("account")})}
    if STORE:
        since = _day_start(days)
        if range == "24h":   # 시간 단위
            rows = STORE.query("SELECT ts, SUM(input) i, SUM(output) o, SUM(cache_read) cr, SUM(cache_write) cw, "
                               f"SUM(n) n FROM usage_hour WHERE provider=? AND ts>=?{mcond} GROUP BY ts ORDER BY ts",
                               (pid, int(time.time()) // 3600 * 3600 - 23 * 3600, *margs))
        else:
            rows = STORE.query("SELECT ts, SUM(input) i, SUM(output) o, SUM(cache_read) cr, SUM(cache_write) cw, "
                               f"SUM(n) n FROM usage_day WHERE provider=? AND ts>=?{mcond} GROUP BY ts ORDER BY ts",
                               (pid, since, *margs))
        out["series"] = [{"ts": r["ts"], "in": r["i"], "out": r["o"], "cr": r["cr"], "cw": r["cw"],
                          "total": r["i"] + r["o"] + r["cr"] + r["cw"], "count": r["n"]} for r in rows]
        mrows = STORE.query("SELECT model, SUM(input+output+cache_read+cache_write) t FROM usage_day "
                            f"WHERE provider=? AND ts>=?{mcond} GROUP BY model ORDER BY t DESC", (pid, since, *margs))
        out["models_range"] = {r["model"]: r["t"] for r in mrows}
        prow = STORE.query("SELECT machine_id, SUM(input+output+cache_read+cache_write) t, SUM(n) n FROM usage_day "
                           "WHERE provider=? AND ts>=? GROUP BY machine_id", (pid, since))
        out["machines_range"] = {r["machine_id"]: {"total": r["t"], "count": r["n"]} for r in prow}
    return _json(out)


@app.get("/api/providers/{pid}/timeline")
def provider_timeline(pid: str, machine: str = None, step: int = 600):
    """오늘(이 PC 시간대 자정부터) 토큰 흐름: step 초(기본 10분) 단위 합계. 응답 단위 기록(usage_event)으로 계산 → 몇 초 지연."""
    if pid not in PROVIDERS:
        raise HTTPException(404, "없는 공급자")
    if not STORE:
        raise HTTPException(503, "내장 DB 없음")
    step = max(60, min(3600, int(step)))
    start = _day_start(1)
    rows = STORE.query(
        "SELECT (ts - ?) / ? AS b, SUM(input+output+cache_read+cache_write) t, SUM(output) o "
        "FROM usage_event WHERE provider=? AND ts>=?" + _mcond(machine)[0] + " GROUP BY b",
        [start, step, pid, start] + _mcond(machine)[1])
    by = {r["b"]: r for r in rows}
    buckets = [{"x": b * step, "total": (by[b]["t"] if b in by else 0), "out": (by[b]["o"] if b in by else 0)}
               for b in builtins.range(86400 // step)]
    return _json({"start": start, "step": step, "now": time.time(), "buckets": buckets})


# ---------- v2: AI 인사이트 (프로젝트 순위 · 시간대 패턴 · API 환산 비용) ----------
INSIGHT_RANGES = {"today": 1, "7d": 7, "30d": 30}


def _insight_args(pid, rng):
    if pid not in PROVIDERS:
        raise HTTPException(404, "없는 공급자")
    if not STORE:
        raise HTTPException(503, "내장 DB 없음")
    if rng not in INSIGHT_RANGES:
        raise HTTPException(400, "range는 today, 7d, 30d 중 하나")
    return _day_start(INSIGHT_RANGES[rng])


@app.get("/api/providers/{pid}/projects")
def provider_projects(pid: str, range: str = "7d", machine: str = None):   # noqa: A002
    """프로젝트별 사용량 순위 (응답 기록 + 세션 기록의 프로젝트). 보관 30일 안에서."""
    since = _insight_args(pid, range)
    names = _machine_names()
    rows = STORE.query(
        "SELECT COALESCE(s.project, '(알 수 없음)') AS project, SUM(e.input+e.output+e.cache_read+e.cache_write) AS t, "
        "COUNT(DISTINCT e.session_id) AS sessions, COUNT(*) AS n, GROUP_CONCAT(DISTINCT e.machine_id) AS machines "
        "FROM usage_event e LEFT JOIN session s ON s.provider=e.provider AND s.machine_id=e.machine_id "
        "AND s.session_id=e.session_id WHERE e.provider=? AND e.ts>=?" + (" AND e.machine_id=?" if machine else "")
        + " GROUP BY 1 ORDER BY t DESC LIMIT 20", [pid, since] + ([machine] if machine else []))
    total = sum(r["t"] or 0 for r in rows)
    return _json({"range": range, "total": total, "projects": [
        {"project": r["project"], "tokens": r["t"], "sessions": r["sessions"], "responses": r["n"],
         "machines": [names.get(m, m) for m in (r["machines"] or "").split(",") if m]} for r in rows]})


@app.get("/api/providers/{pid}/pattern")
def provider_pattern(pid: str, days: int = 30, machine: str = None):
    """요일 × 시간대 사용 패턴 (이 PC 시간대, 시간 합계 기록 기준). cells[요일 0=월][시 0~23] = 토큰."""
    if pid not in PROVIDERS:
        raise HTTPException(404, "없는 공급자")
    if not STORE:
        raise HTTPException(503, "내장 DB 없음")
    days = max(7, min(180, int(days)))
    rows = STORE.query(
        "SELECT CAST(strftime('%w', ts, 'unixepoch', 'localtime') AS INTEGER) AS w, "
        "CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) AS h, "
        "SUM(input+output+cache_read+cache_write) AS t FROM usage_hour WHERE provider=? AND ts>=?"
        + (" AND machine_id=?" if machine else "") + " GROUP BY w, h",
        [pid, _day_start(days)] + ([machine] if machine else []))
    cells = [[0] * 24 for _ in builtins.range(7)]
    for r in rows:
        cells[(r["w"] + 6) % 7][r["h"]] += r["t"] or 0   # sqlite %w: 0=일 → 월=0 으로
    return _json({"days": days, "cells": cells})


@app.get("/api/providers/{pid}/cost")
def provider_cost(pid: str, range: str = "7d", machine: str = None):   # noqa: A002
    """API 환산 비용(추정): 기간 토큰 × 공식 API 가격. 구독 사용분을 'API로 썼다면'으로 본 값."""
    since = _insight_args(pid, range)
    rows = STORE.query(
        "SELECT model, SUM(input) i, SUM(output) o, SUM(cache_write) cw, SUM(cache_read) cr FROM usage_day "
        "WHERE provider=? AND ts>=?" + (" AND machine_id=?" if machine else "") + " GROUP BY model",
        [pid, since] + ([machine] if machine else []))
    models, total, unknown = [], 0.0, []
    for r in rows:
        c = cost_usd(r["model"], r["i"] or 0, r["o"] or 0, r["cw"] or 0, r["cr"] or 0)
        tok = (r["i"] or 0) + (r["o"] or 0) + (r["cw"] or 0) + (r["cr"] or 0)
        if c is None:
            unknown.append(r["model"])
            continue
        total += c
        models.append({"model": r["model"], "usd": round(c, 2), "tokens": tok,
                       "parts": {"input": r["i"], "output": r["o"], "cache_write": r["cw"], "cache_read": r["cr"]}})
    models.sort(key=lambda m: -m["usd"])
    return _json({"range": range, "usd": round(total, 2), "models": models, "unknown_models": unknown,
                  "prices_as_of": PRICES_AS_OF})


@app.get("/api/providers/{pid}/machines")
def provider_machines(pid: str):
    if pid not in PROVIDERS:
        raise HTTPException(404, "없는 공급자")
    states = provider_states(pid)
    names = _machine_names()
    week = {}
    if STORE:
        for r in STORE.query("SELECT machine_id, SUM(input+output+cache_read+cache_write) t FROM usage_day "
                             "WHERE provider=? AND ts>=? GROUP BY machine_id", (pid, _day_start(7))):
            week[r["machine_id"]] = r["t"]
    out = []
    for mid in sorted(_detected_on(pid) | set(week), key=lambda m: (m != "local", names.get(m, m))):
        st = states.get(mid) or {}
        u = st.get("usage") or {}
        sess = st.get("sessions") or []
        out.append({"id": mid, "name": names.get(mid, mid), "ok": bool(st.get("ok")), "error": st.get("error"),
                    "today": (u.get("today") or {}).get("total", 0), "week": week.get(mid, 0),
                    "sessions": len(sess), "working": sum(1 for x in sess if x.get("state") == "working"), "waiting": sum(1 for x in sess if x.get("state") == "waiting"),
                    "last_activity": u.get("last_activity"), "last_model": u.get("last_model"),
                    "account": st.get("account"), "updated": st.get("updated")})
    return _json({"provider": pid, "machines": out})


@app.get("/api/providers/{pid}/sessions")
def provider_sessions(pid: str, machine: str = None, days: int = 7):
    """열린(최근) 세션 + DB에 남은 지난 세션. machine 을 주면 그 PC만."""
    if pid not in PROVIDERS:
        raise HTTPException(404, "없는 공급자")
    names = _machine_names()
    live, seen = [], set()
    for mid, st in provider_states(pid).items():
        if machine and mid != machine:
            continue
        for x in st.get("sessions") or []:
            live.append({**x, "machine_id": mid, "machine": names.get(mid, mid), "live": True})
            seen.add((mid, x.get("session")))
    past = []
    if STORE:
        args = [pid, time.time() - max(1, min(days, 90)) * 86400]
        sql = "SELECT * FROM session WHERE provider=? AND last_activity>=?"
        if machine:
            sql += " AND machine_id=?"
            args.append(machine)
        for r in STORE.query(sql + " ORDER BY last_activity DESC LIMIT 200", args):
            if (r["machine_id"], r["session_id"]) not in seen:
                past.append({"session": r["session_id"], "project": r["project"], "model": r["model"],
                             "started": r["started_at"], "last_activity": r["last_activity"], "state": "ended",
                             "tokens": r["tokens"], "machine_id": r["machine_id"], "title": r.get("title"),
                             "agents": json.loads(r["agents"]) if r.get("agents") else [],
                             "machine": names.get(r["machine_id"], r["machine_id"]), "live": False})
    return _json({"provider": pid, "sessions": live + past})


@app.get("/api/providers/{pid}/sessions/{sid}")
def provider_session(pid: str, sid: str, machine: str = None):
    """세션 상세: 현재 상태(열려 있으면) + DB 기록의 분 단위 토큰 추이·모델별 합계."""
    if pid not in PROVIDERS:
        raise HTTPException(404, "없는 공급자")
    info = None
    for mid, st in provider_states(pid).items():
        if machine and mid != machine:
            continue
        for x in st.get("sessions") or []:
            if x.get("session") == sid:
                info = {**x, "machine_id": mid, "live": True}
    out = {"provider": pid, "session": sid, "info": info}
    if STORE:
        args = [pid, sid] + ([machine] if machine else [])
        cond = "provider=? AND session_id=?" + (" AND machine_id=?" if machine else "")
        if not info:
            rows = STORE.query(f"SELECT * FROM session WHERE {cond}", args)
            if rows:
                r = rows[0]
                info = out["info"] = {**r, "live": False, "agents": json.loads(r["agents"]) if r.get("agents") else []}
        out["minutes"] = STORE.query(
            f"SELECT (ts/60)*60 AS ts, SUM(input) AS 'in', SUM(output) AS out, SUM(cache_read) AS cr, "
            f"SUM(cache_write) AS cw, COUNT(*) AS n FROM usage_event WHERE {cond} GROUP BY (ts/60)*60 ORDER BY 1", args)
        out["models"] = {r["model"]: r["t"] for r in STORE.query(
            f"SELECT model, SUM(input+output+cache_read+cache_write) t FROM usage_event WHERE {cond} GROUP BY model",
            args)}
        tot = STORE.query(f"SELECT MIN(ts) a, MAX(ts) b, SUM(input) i, SUM(output) o, SUM(cache_read) cr, "
                          f"SUM(cache_write) cw, SUM(reasoning) rs, COUNT(*) n FROM usage_event WHERE {cond}", args)[0]
        out["totals"] = tot
    if not info and not (out.get("minutes")):
        raise HTTPException(404, "세션 기록 없음")
    return _json(out)


@app.get("/api/providers/settings")
def get_provider_settings():
    _need_store()
    ps = STORE.provider_settings()
    on = set(enabled_providers())
    return {pid: ps.get(pid) or {"enabled": pid in on, "options": {}, "auto": True} for pid in PROVIDERS}


@app.put("/api/providers/{pid}")
def put_provider(pid: str, body: dict = Body(...)):
    """공급자 켜기/끄기 (원격 PC는 다시 연결할 때 반영)."""
    _need_store()
    if pid not in PROVIDERS:
        raise HTTPException(404, "없는 공급자")
    STORE.set_provider(pid, enabled=body.get("enabled"), options=body.get("options"))
    if body.get("enabled") is False:
        for rk in [k for k in LOCAL_PROV if k[0] == pid]:
            LOCAL_PROV.pop(rk, None)
        PSTATE_LOCAL.pop(pid, None)
    with MLOCK:   # 원격 PC들도 새 목록으로 다시 연결
        for mid in list(MACHINES):
            MACHINES.pop(mid).stop()
            MACHINE_ROWS.pop(mid, None)
    apply_machines()
    return get_provider_settings()


# ---------- v2: 기간 추이 ----------
SYS_RANGES = {   # 범위: (초, 묶는 단위 초, 출처)
    "1h": (3600, 60, "minute"),
    "24h": (86400, 300, "minute"),
    "7d": (7 * 86400, 3600, "minute"),
    "30d": (30 * 86400, 3 * 3600, "hour"),
}


@app.get("/api/machines/{mid}/history")
def machine_history(mid: str, range: str = "24h"):   # noqa: A002 (쿼리 이름)
    """PC 시스템 지표 추이 (DB 기록). 앱이 꺼져 있던 구간은 값 없음(null)으로 채워 그래프에 빈 칸으로 보임.
    네트워크는 평균 속도(B/s)."""
    if range not in SYS_RANGES:
        raise HTTPException(400, f"range는 {', '.join(SYS_RANGES)} 중 하나")
    if not STORE:
        raise HTTPException(503, "내장 DB 없음")
    span, step, src = SYS_RANGES[range]
    now = int(time.time())
    end = now // step * step
    start = end - span + step
    if src == "minute":
        rows = STORE.query(
            "SELECT (ts/?)*? AS b, AVG(cpu) cpu, MAX(cpu) cpu_max, AVG(mem) mem, AVG(swap) swap, AVG(gpu) gpu, "
            "AVG(net_down) down, AVG(net_up) up FROM sys_minute WHERE machine_id=? AND ts>=? GROUP BY b",
            (step, step, mid, start))
    else:
        rows = STORE.query(
            "SELECT (ts/?)*? AS b, SUM(cpu_avg*n)/SUM(n) cpu, MAX(cpu_max) cpu_max, SUM(mem_avg*n)/SUM(n) mem, "
            "NULL swap, AVG(gpu_avg) gpu, SUM(net_down_sum)/(SUM(n)*60.0) down, SUM(net_up_sum)/(SUM(n)*60.0) up "
            "FROM sys_hour WHERE machine_id=? AND ts>=? GROUP BY b", (step, step, mid, start))
        # 아직 시간 합계로 묶이지 않은 최근 구간은 분 기록에서
        last_hour = now // 3600 * 3600 - 3600
        rows = [r for r in rows if r["b"] < last_hour // step * step] + STORE.query(
            "SELECT (ts/?)*? AS b, AVG(cpu) cpu, MAX(cpu) cpu_max, AVG(mem) mem, AVG(swap) swap, AVG(gpu) gpu, "
            "AVG(net_down) down, AVG(net_up) up FROM sys_minute WHERE machine_id=? AND ts>=? GROUP BY b",
            (step, step, mid, last_hour // step * step))
    by = {r["b"]: r for r in rows}
    keys = ("cpu", "cpu_max", "mem", "swap", "gpu", "down", "up")
    pts = []
    for b in builtins.range(start, end + 1, step):   # 매개변수 이름 range 가 내장 함수를 가림
        r = by.get(b)
        pts.append({"ts": b, **{k: (round(r[k], 2) if r and r[k] is not None else None) for k in keys}})
    return _json({"machine": mid, "range": range, "step": step, "points": pts,
                  "has_data": any(p["cpu"] is not None for p in pts)})



@app.get("/api/machines/{mid}/net")
def machine_net(mid: str, range: str = "24h"):   # noqa: A002
    """원격 PC 연결 품질 추이: SSH 왕복 시간(평균·최대), TCP 접속 시간, 끊긴 비율. 앱이 꺼져 있던 구간은 null."""
    if range not in SYS_RANGES:
        raise HTTPException(400, f"range는 {', '.join(SYS_RANGES)} 중 하나")
    if not STORE:
        raise HTTPException(503, "내장 DB 없음")
    span, step, _ = SYS_RANGES[range]
    now = int(time.time())
    end = now // step * step
    start = end - span + step
    rows = STORE.query(
        "SELECT (ts/?)*? AS b, AVG(rtt_ms) rtt, MAX(rtt_max) rtt_max, AVG(tcp_ms) tcp, SUM(ok) ok, SUM(n) n, "
        "SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) down_min FROM net_minute WHERE machine_id=? AND ts>=? GROUP BY b",
        (step, step, mid, start))
    by = {r["b"]: r for r in rows}
    pts = []
    for b in builtins.range(start, end + 1, step):
        r = by.get(b)
        pts.append({"ts": b, "rtt": round(r["rtt"], 1) if r and r["rtt"] is not None else None,
                    "rtt_max": r["rtt_max"] if r else None, "tcp": round(r["tcp"], 1) if r and r["tcp"] is not None else None,
                    "loss": round(100 * (1 - r["ok"] / r["n"]), 1) if r and r["n"] else None})
    tot = STORE.query("SELECT AVG(rtt_ms) rtt, MAX(rtt_max) rtt_max, SUM(ok) ok, SUM(n) n, "
                      "SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) down_min FROM net_minute WHERE machine_id=? AND ts>=?",
                      (mid, start))[0]
    # 끊김 구간: 연속된 '끊긴 분'을 묶음 (최근 것부터 최대 10개)
    downs, cur = [], None
    for r in STORE.query("SELECT ts FROM net_minute WHERE machine_id=? AND ts>=? AND ok=0 ORDER BY ts", (mid, start)):
        if cur and r["ts"] - cur[1] <= 60:
            cur[1] = r["ts"]
        else:
            cur = [r["ts"], r["ts"]]
            downs.append(cur)
    with MLOCK:
        m = MACHINES.get(mid)
    ok, tcp, rtt = (m.net_sample() if m else None) or (bool(m), None, None)
    return _json({"machine": mid, "range": range, "step": step, "points": pts,
                  "has_data": any(p["loss"] is not None for p in pts),
                  "now": {"ok": ok, "tcp": tcp, "rtt": rtt},
                  "summary": {"rtt": round(tot["rtt"], 1) if tot["rtt"] is not None else None, "rtt_max": tot["rtt_max"],
                              "uptime": round(100 * tot["ok"] / tot["n"], 2) if tot["n"] else None,
                              "down_min": tot["down_min"] or 0},
                  "outages": [{"start": a, "end": b + 60} for a, b in downs[-10:][::-1]]})


@app.get("/manifest.webmanifest")
def web_manifest(t: str = None):
    """앱 정보(홈 화면 앱). 아이폰 홈 화면 앱은 사파리와 저장소가 따로라 주소의 토큰을 기억하지 못함
    → 올바른 토큰으로 요청하면 시작 주소에 토큰을 넣어 줌 (홈 화면 아이콘으로 열어도 바로 접속)."""
    from fastapi import Response
    try:
        m = json.loads((DIST / "manifest.webmanifest").read_text(encoding="utf-8"))
    except OSError:
        raise HTTPException(404)
    if t and t == CFG.get("token"):
        m["start_url"] = f"/?t={t}"
        m["id"] = "/"
    return Response(json.dumps(m, ensure_ascii=False), media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")


if __name__ == "__main__":
    print(f"dashboard: http://localhost:{CFG['port']}")
    hc = https_server_config()
    if hc:
        threading.Thread(target=uvicorn.Server(hc).run, daemon=True, name="https").start()
    uvicorn.run(app, host=CFG["bind"], port=CFG["port"], log_level="warning")
