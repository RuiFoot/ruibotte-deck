"""v2 내장 DB (SQLite, WAL): 설정 · PC 목록 · 공급자 · 기록 · 롤업 · 보관 정책.

표준 라이브러리만 사용. 여러 수집 스레드가 같이 쓰므로 연결 하나 + 잠금으로 직렬화한다.
AI 인증 토큰은 절대 저장하지 않는다(각 도구의 파일을 읽기만).
"""
import json
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS machines(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,          -- kind: local | ssh
    ssh_target TEXT, ssh_fallbacks TEXT DEFAULT '[]', wsl_distro TEXT, home_override TEXT,
    enabled INTEGER DEFAULT 1, sort INTEGER DEFAULT 0, created_at INTEGER);
CREATE TABLE IF NOT EXISTS profiles(                                      -- AI 도구 계정(로그인 폴더). path ''= 기본 폴더
    id TEXT PRIMARY KEY, machine_id TEXT NOT NULL, provider TEXT NOT NULL, path TEXT DEFAULT '', label TEXT,
    enabled INTEGER DEFAULT 1, created_at INTEGER);
CREATE TABLE IF NOT EXISTS providers(id TEXT PRIMARY KEY, enabled INTEGER DEFAULT 1, options_json TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS sys_minute(
    machine_id TEXT, ts INTEGER, cpu REAL, mem REAL, swap REAL, net_down REAL, net_up REAL, gpu REAL,
    PRIMARY KEY(machine_id, ts));
CREATE TABLE IF NOT EXISTS sys_hour(
    machine_id TEXT, ts INTEGER, cpu_avg REAL, cpu_max REAL, mem_avg REAL, mem_max REAL,
    net_down_sum REAL, net_up_sum REAL, gpu_avg REAL, n INTEGER,
    PRIMARY KEY(machine_id, ts));
CREATE TABLE IF NOT EXISTS net_minute(
    machine_id TEXT, ts INTEGER, tcp_ms REAL, rtt_ms REAL, rtt_max REAL, ok INTEGER, n INTEGER,   -- ok: 연결된 표본 수
    PRIMARY KEY(machine_id, ts));
CREATE TABLE IF NOT EXISTS usage_event(
    provider TEXT, machine_id TEXT, account_id TEXT DEFAULT '', session_id TEXT, ts INTEGER, model TEXT,
    input INTEGER DEFAULT 0, output INTEGER DEFAULT 0, cache_read INTEGER DEFAULT 0, cache_write INTEGER DEFAULT 0,
    reasoning INTEGER DEFAULT 0, event_key TEXT, rolled INTEGER DEFAULT 0,      -- rolled: 시간/일 합계에 반영됨
    PRIMARY KEY(provider, event_key));
CREATE INDEX IF NOT EXISTS usage_event_ts ON usage_event(ts);
CREATE INDEX IF NOT EXISTS usage_event_unrolled ON usage_event(rolled) WHERE rolled=0;
CREATE TABLE IF NOT EXISTS usage_hour(
    provider TEXT, machine_id TEXT, account_id TEXT, ts INTEGER, model TEXT,
    input INTEGER, output INTEGER, cache_read INTEGER, cache_write INTEGER, reasoning INTEGER, n INTEGER,
    PRIMARY KEY(provider, machine_id, account_id, ts, model));
CREATE TABLE IF NOT EXISTS usage_day(
    provider TEXT, machine_id TEXT, account_id TEXT, ts INTEGER, model TEXT,
    input INTEGER, output INTEGER, cache_read INTEGER, cache_write INTEGER, reasoning INTEGER, n INTEGER,
    PRIMARY KEY(provider, machine_id, account_id, ts, model));
CREATE TABLE IF NOT EXISTS limit_snapshot(
    provider TEXT, account_id TEXT DEFAULT '', ts INTEGER, window TEXT, used_pct REAL, resets_at INTEGER,
    source TEXT, PRIMARY KEY(provider, account_id, window, ts));
CREATE TABLE IF NOT EXISTS session(
    provider TEXT, machine_id TEXT, session_id TEXT, account_id TEXT, project TEXT, model TEXT,
    started_at INTEGER, last_activity INTEGER, state TEXT, tokens INTEGER,
    PRIMARY KEY(provider, machine_id, session_id));
CREATE TABLE IF NOT EXISTS scan_state(
    provider TEXT, machine_id TEXT, file_path TEXT, offset INTEGER,
    PRIMARY KEY(provider, machine_id, file_path));
"""

# 설정 기본값 (DB에 없으면 이 값). 보관 기간 단위는 일, 0 = 무기한
DEFAULTS = {
    "retention.sys_minute_days": 7,
    "retention.sys_hour_days": 90,
    "retention.net_minute_days": 30,
    "retention.usage_event_days": 30,
    "retention.usage_hour_days": 180,
    "retention.usage_day_days": 0,
    "retention.limit_snapshot_days": 90,
    "retention.session_days": 90,
    "interval.local_sec": 2,
    "interval.remote_sec": 10,
    "interval.claude_sec": 3,
    "interval.remote_claude_sec": 5,
    "history.backfill_days": 90,
    "display.theme": "sun",           # sun(일출~일몰 라이트) | auto(정한 시각) | dark | light
    "display.light_from": "07:00", "display.dark_from": "19:00",
    "display.lat": None, "display.lon": None,   # 일출·일몰 계산 위치 (없으면 화면이 시간대로 추정)
    "display.monitor": "auto",        # 대시보드 창을 띄울 화면: auto(내장 화면→주 모니터) | primary | 모니터 ID | \\.\DISPLAYn
    "setup.done": None,
    # 알림 켜기/끄기 (alerts.py)
    "notify.session_done": True, "notify.waiting": True, "notify.limit_reset": True, "notify.limits": True, "notify.machine": True,
    "notify.disk": True, "notify.cpu": True, "notify.context": True,               # 첫 실행 마법사를 마친 시각      # AI 사용량 과거 기록을 처음 한 번 채울 기간(일)
    "claude.weekly_reset_anchor": None,
    "claude.pricing_per_mtok_usd": {},
}

# (테이블, 시각 열, 보관 설정 키)
RETENTION = [
    ("sys_minute", "ts", "retention.sys_minute_days"),
    ("sys_hour", "ts", "retention.sys_hour_days"),
    ("net_minute", "ts", "retention.net_minute_days"),
    ("usage_event", "ts", "retention.usage_event_days"),
    ("usage_hour", "ts", "retention.usage_hour_days"),
    ("usage_day", "ts", "retention.usage_day_days"),
    ("limit_snapshot", "ts", "retention.limit_snapshot_days"),
    ("session", "last_activity", "retention.session_days"),
]

USAGE_COLS = ("input", "output", "cache_read", "cache_write", "reasoning")


def data_dir():
    """OS별 앱 데이터 폴더. PCDASHBOARD_DATA 환경 변수로 바꿀 수 있음(시험용)."""
    if os.environ.get("PCDASHBOARD_DATA"):
        return Path(os.environ["PCDASHBOARD_DATA"])
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "PCDashboard"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PCDashboard"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "pcdashboard"


def _slug(s):
    s = re.sub(r"[^a-z0-9_-]+", "-", (s or "").lower()).strip("-")
    return s or "pc"


def hour_floor(ts):
    return int(ts) // 3600 * 3600


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else data_dir() / "dashboard.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)
            # 이후 추가된 열 (예전 DB에도 붙임)
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(session)")}
            for col in ("title TEXT", "agents TEXT"):
                if col.split()[0] not in cols:
                    self.db.execute(f"ALTER TABLE session ADD COLUMN {col}")
            self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.db.commit()
        self._minute = {}       # machine_id -> {"ts": 분 시작, 합계...}  (분 단위 평균용 누적)
        self._net = {}          # machine_id -> 연결 품질 분 누적
        self._limits_seen = {}  # (provider, window) -> 마지막으로 기록한 captured_at
        self.ensure_local()

    # ---------- 기본 ----------
    def _exec(self, sql, args=(), many=False):
        with self.lock:
            cur = self.db.executemany(sql, args) if many else self.db.execute(sql, args)
            self.db.commit()
            return cur

    def _all(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def query(self, sql, args=()):
        """읽기 전용 조회 (API용)."""
        return self._all(sql, args)

    # ---------- 설정 ----------
    def get(self, key, default=None):
        with self.lock:
            r = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if r is None:
            return DEFAULTS.get(key, default)
        return json.loads(r[0])

    def set(self, key, value):
        self._exec("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (key, json.dumps(value)))

    def settings(self):
        out = dict(DEFAULTS)
        for r in self._all("SELECT key, value FROM settings"):
            out[r["key"]] = json.loads(r["value"])
        return out

    # ---------- PC 목록 ----------
    def ensure_local(self):
        self._exec("INSERT OR IGNORE INTO machines(id, name, kind, enabled, sort, created_at) "
                   "VALUES('local', '이 PC', 'local', 1, 0, ?)", (int(time.time()),))

    def machines(self, enabled_only=False):
        rows = self._all("SELECT * FROM machines" + (" WHERE enabled=1" if enabled_only else "")
                         + " ORDER BY sort, created_at")
        for r in rows:
            r["ssh_fallbacks"] = json.loads(r.get("ssh_fallbacks") or "[]")
            r["enabled"] = bool(r["enabled"])
        return rows

    def machine(self, mid):
        return next((m for m in self.machines() if m["id"] == mid), None)

    def add_machine(self, name, ssh_target, ssh_fallbacks=(), wsl_distro=None, home_override=None, mid=None):
        if not mid:
            # 이름에 영문·숫자가 거의 없으면(한글 이름 등) SSH 대상의 호스트 이름으로 ID를 만든다
            mid = _slug(name)
            if not (name or "").isascii() or len(re.sub(r"[^a-z0-9]", "", mid)) < 2:
                host = re.sub(r"^ssh://", "", ssh_target or "").split("@")[-1].split(":")[0]
                mid = _slug(host)
        base, i = mid, 2
        while self.machine(mid):
            mid, i = f"{base}-{i}", i + 1
        sort = (self._all("SELECT COALESCE(MAX(sort), 0) AS s FROM machines")[0]["s"] or 0) + 1
        self._exec("INSERT INTO machines(id, name, kind, ssh_target, ssh_fallbacks, wsl_distro, home_override, "
                   "enabled, sort, created_at) VALUES(?, ?, 'ssh', ?, ?, ?, ?, 1, ?, ?)",
                   (mid, name, ssh_target, json.dumps(list(ssh_fallbacks)), wsl_distro, home_override,
                    sort, int(time.time())))
        return mid

    def update_machine(self, mid, **fields):
        allowed = {"name", "ssh_target", "ssh_fallbacks", "wsl_distro", "home_override", "enabled", "sort"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if "ssh_fallbacks" in fields:
            fields["ssh_fallbacks"] = json.dumps(list(fields["ssh_fallbacks"]))
        if "enabled" in fields:
            fields["enabled"] = int(bool(fields["enabled"]))
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE machines SET {sets} WHERE id=?", (*fields.values(), mid))

    def delete_machine(self, mid):
        if mid == "local":
            raise ValueError("이 PC는 삭제할 수 없습니다")
        # 기록(sys_*, usage_*)은 보관 정책에 따라 자연히 지워지도록 남겨 둔다
        self._exec("DELETE FROM machines WHERE id=?", (mid,))

    # ---------- AI 도구 계정 (로그인 폴더) ----------
    def profiles(self, machine_id=None):
        rows = self._all("SELECT * FROM profiles" + (" WHERE machine_id=?" if machine_id else "")
                         + " ORDER BY machine_id, provider, path != '', created_at", (machine_id,) if machine_id else ())
        for r in rows:
            r["enabled"] = bool(r["enabled"])
        return rows

    def profile(self, pid):
        return next(iter(self._all("SELECT * FROM profiles WHERE id=?", (pid,))), None)

    def add_profile(self, machine_id, provider, path, label):
        """다른 계정 폴더 등록 (path='' 는 기본 폴더에 이름만 붙임 — 이미 있으면 이름만 바꿈). 반환: 프로필 id"""
        path = (path or "").strip()
        same = self._all("SELECT id FROM profiles WHERE machine_id=? AND provider=? AND path=?", (machine_id, provider, path))
        if same:
            self._exec("UPDATE profiles SET label=? WHERE id=?", (label, same[0]["id"]))
            return same[0]["id"]
        import secrets
        pid = "p" + secrets.token_hex(3)
        self._exec("INSERT INTO profiles(id, machine_id, provider, path, label, enabled, created_at) VALUES(?, ?, ?, ?, ?, 1, ?)",
                   (pid, machine_id, provider, path, label, int(time.time())))
        return pid

    def auto_profiles(self, machine_id, found):
        """찾은 계정 폴더 {도구: [경로]} 중 처음 보는 것을 이름 없이 등록 (사용자가 지운 폴더는 다시 넣지 않음). 반환: 추가한 수"""
        ignored = set(self.get("profiles.ignored") or [])
        known = {(r["provider"], r["path"]) for r in self.profiles(machine_id)}
        n = 0
        for prov, paths in (found or {}).items():
            for path in paths:
                if (prov, path) not in known and f"{machine_id}|{prov}|{path}" not in ignored:
                    self.add_profile(machine_id, prov, path, "")
                    n += 1
        return n

    def delete_profile(self, pid):
        r = self.profile(pid)
        if r and r["path"]:   # 자동 찾기로 다시 들어오지 않게 기억
            ignored = list(self.get("profiles.ignored") or [])
            ignored.append(f"{r['machine_id']}|{r['provider']}|{r['path']}")
            self.set("profiles.ignored", ignored[-200:])
        self._exec("DELETE FROM profiles WHERE id=?", (pid,))

    def update_profile(self, pid, **fields):
        fields = {k: (int(bool(v)) if k == "enabled" else v) for k, v in fields.items() if k in ("label", "enabled")}
        if fields:
            self._exec(f"UPDATE profiles SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), pid))

    # ---------- 공급자(AI 도구) 켜기/끄기 ----------
    def provider_settings(self):
        """{provider: {"enabled": bool, "options": {...}}} — DB에 없는 공급자는 켜진 것으로 본다."""
        return {r["id"]: {"enabled": bool(r["enabled"]), "options": json.loads(r["options_json"] or "{}")}
                for r in self._all("SELECT * FROM providers")}

    def enabled_providers(self, all_ids):
        ps = self.provider_settings()
        return [p for p in all_ids if ps.get(p, {"enabled": True})["enabled"]]

    def set_provider(self, pid, enabled=None, options=None):
        cur = self.provider_settings().get(pid, {"enabled": True, "options": {}})
        if enabled is not None:
            cur["enabled"] = bool(enabled)
        if options is not None:
            cur["options"] = options
        self._exec("INSERT OR REPLACE INTO providers(id, enabled, options_json) VALUES(?, ?, ?)",
                   (pid, int(cur["enabled"]), json.dumps(cur["options"])))

    # ---------- v1 config.json 이전 (최초 1회) ----------
    def migrate_v1_config(self, cfg):
        """config.json의 원격 PC와 주기·Claude 설정을 DB로 옮긴다. 이미 옮겼으면 아무것도 안 함."""
        if self.get("migrated.v1_config"):
            return False
        rc = cfg.get("remote") or {}
        hosts = [h for h in rc.get("hosts") or [] if h]
        if hosts:
            name = rc.get("label") or hosts[0]
            if not any(m["kind"] == "ssh" and m["ssh_target"] == hosts[0] for m in self.machines()):
                self.add_machine(name, hosts[0], hosts[1:], rc.get("wsl_distro"), rc.get("claude_home"))
        mapping = [
            (cfg.get("local_interval_sec"), "interval.local_sec"),
            (cfg.get("claude_interval_sec"), "interval.claude_sec"),
            (rc.get("interval_sec"), "interval.remote_sec"),
            (rc.get("claude_interval_sec"), "interval.remote_claude_sec"),
            (cfg.get("weekly_reset_anchor"), "claude.weekly_reset_anchor"),
            (cfg.get("pricing_per_mtok_usd"), "claude.pricing_per_mtok_usd"),
        ]
        for value, key in mapping:
            if value is not None:
                self.set(key, value)
        self.set("migrated.v1_config", int(time.time()))
        if hosts:   # v1을 쓰던 사용자는 이미 설정을 마친 것으로 보고 첫 실행 마법사를 건너뜀
            self.set("setup.done", int(time.time()))
        return True

    # ---------- 시스템 기록 ----------
    def record_sys(self, machine_id, ts, cpu=None, mem=None, swap=None, net_down=None, net_up=None, gpu=None):
        """수집할 때마다 호출. 같은 분의 값은 평균 내어 분이 바뀔 때 sys_minute에 한 줄 기록."""
        minute = int(ts) // 60 * 60
        vals = {"cpu": cpu, "mem": mem, "swap": swap, "net_down": net_down, "net_up": net_up, "gpu": gpu}
        b = self._minute.get(machine_id)
        if b and b["ts"] != minute:
            self._flush_minute(machine_id, b)
            b = None
        if not b:
            b = self._minute[machine_id] = {"ts": minute, "sum": {}, "cnt": {}}
        for k, v in vals.items():
            if v is not None:
                b["sum"][k] = b["sum"].get(k, 0.0) + float(v)
                b["cnt"][k] = b["cnt"].get(k, 0) + 1

    def _flush_minute(self, machine_id, b):
        avg = {k: round(b["sum"][k] / b["cnt"][k], 2) for k in b["sum"]}
        if not avg:
            return
        self._exec("INSERT OR REPLACE INTO sys_minute(machine_id, ts, cpu, mem, swap, net_down, net_up, gpu) "
                   "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                   (machine_id, b["ts"], avg.get("cpu"), avg.get("mem"), avg.get("swap"),
                    avg.get("net_down"), avg.get("net_up"), avg.get("gpu")))

    def flush(self):
        """종료 직전 등: 진행 중인 분 버킷을 기록."""
        for mid, b in list(self._minute.items()):
            self._flush_minute(mid, b)
        for mid, b in list(self._net.items()):
            self._flush_net(mid, b)

    def record_net(self, machine_id, ts, ok, tcp_ms=None, rtt_ms=None):
        """원격 PC 연결 품질 표본(10초마다). 분마다 평균·최대·연결된 비율로 net_minute에 한 줄."""
        minute = int(ts) // 60 * 60
        b = self._net.get(machine_id)
        if b and b["ts"] != minute:
            self._flush_net(machine_id, b)
            b = None
        if not b:
            b = self._net[machine_id] = {"ts": minute, "n": 0, "ok": 0, "tcp": [], "rtt": []}
        b["n"] += 1
        b["ok"] += 1 if ok else 0
        if tcp_ms is not None:
            b["tcp"].append(float(tcp_ms))
        if rtt_ms is not None:
            b["rtt"].append(float(rtt_ms))

    def _flush_net(self, machine_id, b):
        if not b["n"]:
            return
        avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None   # noqa: E731
        self._exec("INSERT OR REPLACE INTO net_minute(machine_id, ts, tcp_ms, rtt_ms, rtt_max, ok, n) "
                   "VALUES(?, ?, ?, ?, ?, ?, ?)",
                   (machine_id, b["ts"], avg(b["tcp"]), avg(b["rtt"]), max(b["rtt"]) if b["rtt"] else None,
                    b["ok"], b["n"]))

    def sys_history(self, machine_id, since, resolution="minute"):
        if resolution == "hour":
            return self._all("SELECT * FROM sys_hour WHERE machine_id=? AND ts>=? ORDER BY ts", (machine_id, since))
        return self._all("SELECT * FROM sys_minute WHERE machine_id=? AND ts>=? ORDER BY ts", (machine_id, since))

    # ---------- AI 사용량 기록 ----------
    def add_usage_events(self, events):
        """events: dict(provider, machine_id, account_id, session_id, ts, model, input, output,
        cache_read, cache_write, reasoning, event_key). 중복(event_key)은 무시. 새로 들어간 개수 반환."""
        rows = [(e["provider"], e["machine_id"], e.get("account_id") or "", e.get("session_id"), int(e["ts"]),
                 e.get("model") or "", *(int(e.get(c) or 0) for c in USAGE_COLS), e["event_key"]) for e in events]
        if not rows:
            return 0
        with self.lock:
            before = self.db.total_changes
            self.db.executemany("INSERT OR IGNORE INTO usage_event(provider, machine_id, account_id, session_id, ts, "
                                "model, input, output, cache_read, cache_write, reasoning, event_key) "
                                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            added = self.db.total_changes - before
            self.db.commit()
        return added

    def add_provider_events(self, provider, machine_id, account_id, events):
        """공급자 에이전트 형식(key, ts, model, session, in, out, cw, cr, rs) → usage_event."""
        return self.add_usage_events([{
            "provider": provider, "machine_id": machine_id, "account_id": account_id or "",
            "session_id": e.get("session"), "ts": e["ts"], "model": e.get("model"), "input": e.get("in"),
            "output": e.get("out"), "cache_read": e.get("cr"), "cache_write": e.get("cw"), "reasoning": e.get("rs"),
            "event_key": e["key"]} for e in events if e.get("key") and e.get("ts")])

    def last_event_ts(self, machine_id):
        """PC별·공급자별로 DB에 있는 마지막 사용량 시각 (원격 에이전트가 이어서 보낼 시작점)."""
        out = {}
        for r in self._all("SELECT provider, MAX(ts) AS t FROM usage_event WHERE machine_id=? GROUP BY provider",
                           (machine_id,)):
            out[r["provider"]] = r["t"]
        return out

    def backfill_pending(self, machine_id, providers):
        """아직 과거 기록을 채우지 않은 공급자 -> 이 시각 이전 기록을 채움(DB에 있는 가장 오래된 기록 시각, 없으면 지금).
        한 번 채우면 'backfill.<PC>.<공급자>' 로 표시해 다시 하지 않음 (보관 기간으로 지운 기록을 다시 넣어 중복 합산되지 않게)."""
        out = {}
        for p in providers:
            if self.get(f"backfill.{machine_id}.{p}"):
                continue
            r = self._all("SELECT MIN(ts) AS t FROM usage_event WHERE machine_id=? AND provider=?", (machine_id, p))
            out[p] = r[0]["t"] if r and r[0]["t"] else int(time.time())
        return out

    def backfill_done(self, machine_id, provider, n):
        self.set(f"backfill.{machine_id}.{provider}", {"at": int(time.time()), "events": n})

    def record_limit(self, provider, window, captured_at, used_pct, resets_at=None, source=None, account_id=""):
        """한도 스냅샷: 같은 출처 시각(captured_at)이면 다시 쓰지 않음."""
        if captured_at is None or used_pct is None:
            return
        key = (provider, account_id, window)
        if self._limits_seen.get(key) == captured_at:
            return
        self._limits_seen[key] = captured_at
        self._exec("INSERT OR REPLACE INTO limit_snapshot(provider, account_id, ts, window, used_pct, resets_at, source) "
                   "VALUES(?, ?, ?, ?, ?, ?, ?)",
                   (provider, account_id or "", int(captured_at), window, float(used_pct),
                    int(resets_at) if resets_at else None, source))

    def upsert_sessions(self, rows):
        """세션 기록 (title: 세션 제목, agents: 하위 에이전트 목록). 제목이 없으면 예전 제목은 지우지 않음."""
        self._exec("INSERT INTO session(provider, machine_id, session_id, account_id, project, model, "
                   "started_at, last_activity, state, tokens, title, agents) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                   "ON CONFLICT(provider, machine_id, session_id) DO UPDATE SET account_id=excluded.account_id, "
                   "project=excluded.project, model=COALESCE(excluded.model, model), "
                   "started_at=MIN(COALESCE(excluded.started_at, started_at), COALESCE(started_at, excluded.started_at)), "
                   "last_activity=MAX(COALESCE(excluded.last_activity, 0), COALESCE(last_activity, 0)), "
                   "state=excluded.state, tokens=MAX(COALESCE(excluded.tokens, 0), COALESCE(tokens, 0)), "
                   "title=COALESCE(excluded.title, title), agents=COALESCE(excluded.agents, agents)",
                   [(r["provider"], r["machine_id"], r["session_id"], r.get("account_id") or "", r.get("project"),
                     r.get("model"), r.get("started_at"), r.get("last_activity"), r.get("state"), r.get("tokens"),
                     r.get("title"), json.dumps(r["agents"], ensure_ascii=False) if r.get("agents") else None)
                    for r in rows], many=True)

    # ---------- 증분 파싱 위치 ----------
    def scan_offset(self, provider, machine_id, file_path):
        r = self._all("SELECT offset FROM scan_state WHERE provider=? AND machine_id=? AND file_path=?",
                      (provider, machine_id, file_path))
        return r[0]["offset"] if r else 0

    def set_scan_offset(self, provider, machine_id, file_path, offset):
        self._exec("INSERT OR REPLACE INTO scan_state(provider, machine_id, file_path, offset) VALUES(?, ?, ?, ?)",
                   (provider, machine_id, file_path, int(offset)))

    # ---------- 롤업 · 보관 정책 ----------
    def rollup(self, now=None):
        """시스템: 끝난 시간대를 시간 단위로 합산(REPLACE라 다시 계산해도 같음).
        사용량: 새 이벤트를 시간/일 합계에 더함(rolled 표시로 한 번만). 한 트랜잭션에서 처리."""
        now = now or time.time()
        cur_hour = hour_floor(now)
        with self.lock:
            db = self.db
            # 시스템: 직전 처리 지점부터 끝난 시간대까지 (처음이면 남아 있는 분 기록 전체)
            since = self.get("rollup.sys_until")
            if since is None:
                r = db.execute("SELECT MIN(ts) FROM sys_minute").fetchone()[0]
                since = hour_floor(r) if r is not None else cur_hour
            since = min(since, cur_hour - 3600)   # 정각 직후 늦게 저장된 마지막 분까지 반영되도록 직전 1시간은 항상 다시
            if since < cur_hour:
                db.execute("""
                    INSERT OR REPLACE INTO sys_hour(machine_id, ts, cpu_avg, cpu_max, mem_avg, mem_max,
                                                    net_down_sum, net_up_sum, gpu_avg, n)
                    SELECT machine_id, (ts/3600)*3600, AVG(cpu), MAX(cpu), AVG(mem), MAX(mem),
                           SUM(net_down)*60, SUM(net_up)*60, AVG(gpu), COUNT(*)
                    FROM sys_minute WHERE ts>=? AND ts<? GROUP BY machine_id, (ts/3600)*3600""",
                           (since, cur_hour))
            db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('rollup.sys_until', ?)",
                       (json.dumps(cur_hour),))

            # 사용량: 아직 반영 안 된 이벤트(rolled=0)를 시간/일 합계에 더한다.
            # 재시작 후 채워진 과거 이벤트도 그대로 더해지고, 이벤트가 보관 기간으로 지워져도 합계는 남는다.
            sums = ", ".join(f"SUM({c})" for c in USAGE_COLS)
            add = ", ".join(f"{c}={c}+excluded.{c}" for c in USAGE_COLS)
            cols = ", ".join(USAGE_COLS)
            db.execute(f"""
                INSERT INTO usage_hour(provider, machine_id, account_id, ts, model, {cols}, n)
                SELECT provider, machine_id, account_id, (ts/3600)*3600 AS h, model, {sums}, COUNT(*)
                FROM usage_event WHERE rolled=0 GROUP BY provider, machine_id, account_id, h, model
                ON CONFLICT(provider, machine_id, account_id, ts, model) DO UPDATE SET {add}, n=n+excluded.n""")
            # 일 단위는 이 PC 시간대의 자정 기준
            db.execute(f"""
                INSERT INTO usage_day(provider, machine_id, account_id, ts, model, {cols}, n)
                SELECT provider, machine_id, account_id,
                       CAST(strftime('%s', date(ts, 'unixepoch', 'localtime'), 'utc') AS INTEGER) AS d, model,
                       {sums}, COUNT(*)
                FROM usage_event WHERE rolled=0 GROUP BY provider, machine_id, account_id, d, model
                ON CONFLICT(provider, machine_id, account_id, ts, model) DO UPDATE SET {add}, n=n+excluded.n""")
            db.execute("UPDATE usage_event SET rolled=1 WHERE rolled=0")
            db.commit()

    def cleanup(self, now=None):
        """보관 기간이 지난 행 삭제. 지운 행 수를 테이블별로 반환."""
        now = now or time.time()
        removed = {}
        with self.lock:
            for table, col, key in RETENTION:
                days = self.get(key)
                if not days:
                    continue
                cur = self.db.execute(f"DELETE FROM {table} WHERE {col} < ?", (int(now - days * 86400),))
                if cur.rowcount:
                    removed[table] = cur.rowcount
            self.db.commit()
        return removed

    def maintenance(self):
        self.rollup()
        removed = self.cleanup()
        with self.lock:
            self.db.execute("PRAGMA optimize")
            # WAL 파일 비우기 (자동 체크포인트만으로는 16MB까지 쌓여 있었음, 2026-10-29)
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return removed

    def close(self):
        try:
            self.flush()
        finally:
            with self.lock:
                self.db.close()
