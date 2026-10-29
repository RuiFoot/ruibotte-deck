"""v2 Machine 추상화: 이 PC(LocalMachine)와 SSH 원격 PC(RemoteMachine).

원격 PC는 처음 연결할 때 OS·WSL·Python을 자동 감지하고, 수집 에이전트(collectors/agent.py)를
SSH 연결 하나로 계속 실행해 시스템 지표와 Claude 집계를 함께 받는다(v1의 10초마다 새 SSH 접속 대체).
Python을 쓸 수 없는 Windows는 v1 PowerShell 프로브(collectors/remote.Remote)로 축소 동작.
SSH는 키 기반만(BatchMode=yes): 비밀번호를 묻거나 저장하지 않는다.
"""
import base64
import json
import re
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from collectors import procjob
from collectors.remote import PROBES, Remote, parse_wsl

HERE = Path(__file__).resolve().parent
COLLECTORS = HERE / "collectors"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ssh 대상: 별칭/호스트, user@host, ssh://user@host:port. 옵션 주입(-o...)·공백·따옴표 차단
_TARGET_RE = re.compile(r"^(ssh://)?[A-Za-z0-9._~%\-]+(@[A-Za-z0-9._\-\[\]:%]+)?(:\d{1,5})?$")
_DISTRO_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
SYS_STALE_SEC = 40       # 시스템 지표가 이보다 오래되면 끊김으로 표시
CLAUDE_STALE_SEC = 150   # 원격이 바쁘면 결과 간격이 수십 초까지 늘어남
PING_SEC = 10            # 연결 품질(SSH 왕복 시간) 측정 간격
HUNG_SEC = 180           # 이 시간 동안 아무 줄도 안 오면 연결을 끊고 다시 연결


def normalize_target(t):
    """'user@host:port' 는 OpenSSH가 이해하는 'ssh://user@host:port' 로 바꿈. 형식이 이상하면 ValueError."""
    t = (t or "").strip()
    if not t or t.startswith("-") or not _TARGET_RE.match(t):
        raise ValueError(f"SSH 대상 형식이 올바르지 않습니다: {t!r}")
    if not t.startswith("ssh://") and re.match(r"^[^:]+:\d+$", t):
        t = "ssh://" + t
    return t


def validate_distro(d):
    if d and not _DISTRO_RE.match(d):
        raise ValueError(f"WSL 배포판 이름이 올바르지 않습니다: {d!r}")
    return d or None


def _decode(b):
    if b[:2] in (b"\xff\xfe", b"\xfe\xff") or (len(b) > 3 and b[1:2] == b"\x00"):
        return b.decode("utf-16-le", "replace").replace("﻿", "")   # wsl.exe 출력은 UTF-16
    for enc in ("utf-8", "cp949"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "replace")


def _ssh_args(target, connect_timeout=15):
    return ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "--", target]


def ssh(target, cmd, stdin=None, timeout=40, connect_timeout=15):
    r = procjob.run(_ssh_args(target, connect_timeout) + [cmd], input=stdin, timeout=timeout)
    return r.returncode, _decode(r.stdout), _decode(r.stderr)


def _ssh_err(rc, err):
    lines = [l for l in err.strip().splitlines() if l.strip()]
    return (lines[-1] if lines else f"exit {rc}")[:200]


# 원격에서 실행하는 짧은 시작 코드: 첫 줄의 길이만큼 스크립트를 읽어 실행하고, stdin은 닫지 않은 채 남겨 둠.
# 에이전트는 stdin이 끝나면(= 이 PC 쪽 SSH 연결이 끊기면) 바로 스스로 종료한다 (부모 PID 감시만으로는 WSL에서 놓치는 경우가 있었음).
BOOTSTRAP = '-c "import sys;exec(sys.stdin.buffer.read(int(sys.stdin.buffer.readline())))"'


def framed(script: bytes) -> bytes:
    return str(len(script)).encode() + b"\n" + script


def _log(msg):
    print(time.strftime("%m-%d %H:%M:%S"), msg, flush=True)


def tz_env():
    """원격 python의 '오늘' 기준을 이 PC 시간대와 맞춤 (POSIX TZ: 부호가 반대)."""
    return "TZ=LOC%+d" % (time.timezone // 3600)


# ---------- 에이전트 스크립트 ----------
def _source(name):
    s = (COLLECTORS / name).read_text(encoding="utf-8")
    i = s.find('\nif __name__ == "__main__":')
    return s if i < 0 else s[:i]


def _ps1_encoded():
    ps = (PROBES / "remote_win.ps1").read_text(encoding="utf-8")
    ps = "\n".join(l for l in ps.splitlines() if not l.lstrip().startswith("#"))
    return base64.b64encode(ps.encode("utf-16-le")).decode("ascii")


# 원격으로 보내는 순서 (뒤 파일이 앞 파일의 이름을 씀). 모두 표준 라이브러리만 사용
AGENT_SOURCES = ("claude_usage.py", "sessions.py", "codex_usage.py", "gemini_usage.py", "providers.py", "agent.py")


def build_agent(home=None, interval=5, sys_interval=10, detect=False, system=True, since=None, providers=None,
                backfill=None, backfill_days=90, profiles=None):
    cfg = {"home": home, "interval": float(interval), "sys_interval": float(sys_interval), "detect": detect,
           "system": system, "ps1": _ps1_encoded(), "since": since or {}, "providers": providers,
           "backfill": backfill or {}, "backfill_days": backfill_days, "profiles": profiles or {}}
    parts = [_source(n) for n in AGENT_SOURCES]
    parts[-1] = parts[-1].replace("_CFG = __CFG__", "_CFG = " + repr(cfg), 1)
    return "\n".join(parts).encode("utf-8")


def _parse_lines(text):
    out = {}
    for line in text.splitlines():
        for tag in ("@@DETECT", "@@HELLO"):
            if line.startswith(tag + "{"):
                try:
                    out[tag] = json.loads(line[len(tag):])
                except ValueError:
                    pass
    return out


# ---------- 연결 테스트 / 자동 감지 ----------
def detect(target, wsl_distro=None, home_override=None):
    """SSH 접속 → OS 감지 → (Windows) WSL·Python 확인 → 에이전트 감지 모드로 홈 폴더·AI 도구 확인.
    반환: {"ok", "error", "target", "latency_ms", "os", "mode": wsl|python|powershell, "launcher", "wsl_distro",
           "wsl_distros", "agent": {...}, "home", "providers"}"""
    target = normalize_target(target)
    wsl_distro = validate_distro(wsl_distro)
    res = {"ok": False, "target": target, "error": None, "os": None, "mode": None, "launcher": None,
           "wsl_distro": None, "wsl_distros": [], "agent": None, "home": home_override or None, "providers": {}}
    t = time.time()
    try:
        rc, out, err = ssh(target, "echo %OS%", timeout=30, connect_timeout=12)
    except subprocess.TimeoutExpired:
        res["error"] = "접속 시간 초과"
        return res
    res["latency_ms"] = int((time.time() - t) * 1000)
    if rc == 255:
        res["error"] = _ssh_err(rc, err)
        return res
    tz = tz_env()
    if out.strip() == "Windows_NT":
        res["os"] = "windows"
        rc, out, _ = ssh(target, "wsl -l -q", timeout=30)
        res["wsl_distros"] = [l.strip() for l in out.replace("\x00", "").splitlines()
                              if l.strip() and rc == 0 and "docker-desktop" not in l]
        rc, out, _ = ssh(target, "where python", timeout=30)
        # MS Store판 Python(WindowsApps)은 SSH 계정으로 실행되지 않는 경우가 많아 제외 (v1에서 겪은 사례)
        native = rc == 0 and any(l.strip() and "WindowsApps" not in l for l in out.splitlines())
        distro = wsl_distro or (None if native else (res["wsl_distros"] or [None])[0])
        if distro:
            rc, out, _ = ssh(target, f"wsl -d {distro} -e python3 -V", timeout=40)
            if rc == 0 and "Python 3" in out:
                res.update(mode="wsl", wsl_distro=distro,
                           launcher=f"wsl -d {distro} -e env {tz} python3 -u {BOOTSTRAP}")
        if not res["mode"] and native:
            res.update(mode="python", launcher=f"python -u {BOOTSTRAP}")
        if not res["mode"]:
            res["mode"] = "powershell"   # Python 없음: 시스템 지표만 (AI 사용량 집계 불가)
    else:
        rc, out, _ = ssh(target, "uname -s; command -v python3", timeout=30)
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        res["os"] = {"Linux": "linux", "Darwin": "darwin"}.get(lines[0] if lines else "", (lines or ["?"])[0].lower())
        if any(l.endswith("python3") for l in lines[1:]):
            res.update(mode="python", launcher=f"env {tz} python3 -u {BOOTSTRAP}")
        else:
            res["error"] = "원격에 python3이 없습니다 (표준 라이브러리만 있으면 됩니다)"
            return res
    if res["launcher"]:
        try:
            rc, out, err = ssh(target, res["launcher"], stdin=framed(build_agent(home_override, detect=True)), timeout=60)
            res["agent"] = _parse_lines(out).get("@@DETECT")
        except subprocess.TimeoutExpired:
            pass
        if not res["agent"]:
            res["error"] = "에이전트 실행 실패: " + _ssh_err(rc, err)
            return res
        res["home"] = home_override or res["agent"]["best_home"]
        h = next((x for x in res["agent"]["homes"] if x["path"] == res["home"]), None)
        res["providers"] = (h or {}).get("providers", {})
    res["ok"] = True
    return res


def _tcp_latency(target, cache={}):
    """ssh -G 로 실제 호스트·포트를 알아내 TCP 접속 시간(ms) 측정 (~/.ssh/config 별칭도 반영)."""
    try:
        if target not in cache:
            out = procjob.run(["ssh", "-G", "--", target], timeout=10).stdout.decode("utf-8", "replace")
            kv = dict(l.split(" ", 1) for l in out.splitlines() if " " in l)
            cache[target] = (kv.get("hostname"), int(kv.get("port", 22)))
        host, port = cache[target]
        t = time.time()
        socket.create_connection((host, port), timeout=3).close()
        return int((time.time() - t) * 1000)
    except Exception:
        return None


# ---------- Machine ----------
class LocalMachine:
    kind = "local"

    def __init__(self, row):
        from collectors.local import Local
        from collectors.local_extras import LocalExtras
        self.id, self.name = row["id"], row["name"]
        self.local = Local()
        self.extras = LocalExtras()


class RemoteMachine:
    kind = "ssh"

    def __init__(self, row, interval=5, sys_interval=10, on_detect=None, on_events=None, since_fn=None,
                 providers=None, backfill_fn=None, on_backfill=None):
        self.id, self.name = row["id"], row["name"]
        self.targets = [row["ssh_target"], *row.get("ssh_fallbacks", [])]
        self.distro = row.get("wsl_distro")
        self.home = row.get("home_override")
        self.interval, self.sys_interval = interval, sys_interval
        self.on_detect = on_detect
        self.on_events = on_events    # (machine_id, provider, account, events) -> DB 저장
        self.since_fn = since_fn      # machine_id -> {provider: DB에 있는 마지막 시각}
        self.providers = providers    # 켜진 공급자 id 목록 (None = 전부)
        self.backfill_fn = backfill_fn  # machine_id -> ({provider: 이 시각 이전을 채움}, 채울 일수) — 아직 안 채운 것만
        self.on_backfill = on_backfill  # (machine_id, provider, 개수) -> 채움 완료 기록
        self.info = None          # 마지막 감지 결과
        self.hello = None         # 에이전트 시작 정보
        self.active = None
        self.error = None
        self.proc = None
        self.legacy = None        # PowerShell 전용 모드의 v1 Remote
        self.sys_raw, self.sys_t, self.last_ok = None, 0.0, None
        self.prov = {}            # (provider, 계정 폴더 k — 기본 "") -> (마지막 @@PROV 결과, 받은 시각)
        self.profiles = row.get("_profiles") or {}   # {provider: [{"k": 프로필 id, "dir": 경로}]} 다른 계정 폴더
        self.plist = None         # 에이전트가 감지한 공급자 목록
        self.latency_ms = None
        self.rtt_ms, self.rtt_t = None, 0.0   # SSH 연결 안 왕복 시간(@@PING→@@PONG)과 받은 시각
        self._pings = {}                      # 번호 -> 보낸 시각
        self.stream_t = 0.0                   # 지금 연결을 시작한 시각
        self.last_line = 0.0
        self.hist = {k: deque(maxlen=60) for k in ("cpu", "mem", "down", "up")}
        self.hist_cores = deque(maxlen=60)   # 10초 x 60 = 최근 10분
        self._stop = threading.Event()
        self.thread = None

    # ----- 수명 -----
    def start(self):
        if not self.thread:
            self.thread = threading.Thread(target=self._run, daemon=True, name=f"machine-{self.id}")
            self.thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._kill()

    def _kill(self):
        p = self.proc
        if p:
            try:
                p.kill()
            except Exception:
                pass

    def _order(self):
        return ([self.active] if self.active else []) + [t for t in self.targets if t != self.active]

    def _run(self):
        while not self._stop.is_set():
            for target in self._order():
                if self._stop.is_set():
                    return
                try:
                    info = detect(target, self.distro, self.home)
                except Exception as e:
                    info = {"ok": False, "error": repr(e)[:200]}
                if not info.get("ok"):
                    self.error = f"{target}: {info.get('error')}"
                    continue
                if self._stop.is_set():   # 연결 시험 중에 멈춤 요청 → 새로 연결하지 않음
                    return
                self.info, self.active = info, target
                if self.on_detect:
                    self.on_detect(self.id, info)
                try:
                    if info["mode"] == "powershell":
                        self._legacy_loop(target)
                    else:
                        self._stream(target, info)
                except Exception as e:
                    self.error = f"{target}: {repr(e)[:200]}"
                break   # 연결이 끊기면 처음(마지막 성공 주소)부터 다시
            self._stop.wait(10)

    def _stream(self, target, info):
        since = self.since_fn(self.id) if self.since_fn else {}
        bf, bf_days = self.backfill_fn(self.id) if self.backfill_fn else ({}, 90)
        profs = {}
        for pid, items in self.profiles.items():   # 계정 폴더마다 DB 마지막 시각·과거 기록 채우기 여부 (키 = PC~프로필)
            for x in items:
                key = self.key(x["k"])
                profs.setdefault(pid, []).append({**x, "since": (self.since_fn(key) if self.since_fn else {}).get(pid),
                                                  "backfill": (self.backfill_fn(key)[0] if self.backfill_fn else {}).get(pid)})
        script = build_agent(self.home or info.get("home"), self.interval, self.sys_interval,
                             since=since, providers=self.providers, backfill=bf, backfill_days=bf_days, profiles=profs)
        p = self.proc = procjob.popen(_ssh_args(target, 20) + [info["launcher"]], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _log(f"machine {self.id}: 연결 시작 {target} (ssh pid {p.pid}, obj {id(self):x})")
        self.stream_t = time.time()
        self.last_line = time.time()
        guard = threading.Thread(target=self._hang_guard, args=(p,), daemon=True)
        guard.start()
        try:
            if self._stop.is_set():
                return
            p.stdin.write(framed(script))
            p.stdin.flush()   # stdin은 닫지 않음: 연결이 살아 있는 동안 원격 에이전트도 살아 있음
            threading.Thread(target=self._pinger, args=(p,), daemon=True).start()
            for raw in p.stdout:
                if self._stop.is_set():
                    break
                self.last_line = time.time()
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                tag, _, body = line.partition("{")
                body = "{" + body
                if tag == "@@PONG":
                    sent = self._pings.pop(str(json.loads(body).get("n")), None)
                    if sent:
                        self.rtt_ms, self.rtt_t = round((time.time() - sent) * 1000, 1), time.time()
                elif tag == "@@SYS":
                    self._on_sys(json.loads(body))
                elif tag == "@@PROV":
                    d = json.loads(body)
                    pk = (d.pop("p"), d.pop("k", ""))
                    if "recent" not in d and pk in self.prov:   # 최근 세션 요약은 1분마다만 옴 → 이전 것 유지
                        d["recent"] = self.prov[pk][0].get("recent")
                    self.prov[pk] = (d, time.time())
                    self.error = None
                elif tag == "@@EV":
                    d = json.loads(body)
                    if self.on_events:
                        self.on_events(self.key(d.get("k")), d["p"], d.get("account"), d["events"])
                elif tag == "@@BFDONE":
                    d = json.loads(body)
                    if self.on_backfill:
                        self.on_backfill(self.key(d.get("k")), d["p"], d.get("n"))
                elif tag == "@@PLIST":
                    self.plist = json.loads(body)["providers"]
                elif tag == "@@HELLO":
                    self.hello = json.loads(body)
                elif line.startswith("@@ERR"):
                    self.error = line[5:].strip()[:300]
        finally:
            self._kill()
        err = p.stderr.read().decode("utf-8", "replace").strip().splitlines()
        _log(f"machine {self.id}: 연결 종료 (ssh pid {p.pid}, exit {p.poll()}, 멈춤 요청 {self._stop.is_set()})"
             + (f" — {err[-1][:160]}" if err else ""))
        if not self._stop.is_set():
            self.error = f"{target}: " + (err[-1] if err else f"연결 종료 (exit {p.returncode})")[:200]

    def _pinger(self, p):
        """연결 품질: 10초마다 원격 에이전트에 @@PING을 보내 왕복 시간을 잼 (답이 늦으면 그만큼 크게 나옴)."""
        n = 0
        while p.poll() is None and not self._stop.is_set():
            n += 1
            self._pings = {k: v for k, v in self._pings.items() if time.time() - v < 60}
            self._pings[str(n)] = time.time()
            try:
                p.stdin.write(b"@@PING %d\n" % n)
                p.stdin.flush()
            except Exception:
                return
            self._stop.wait(PING_SEC)

    def net_sample(self):
        """연결 품질 기록용 현재 값: (연결됨?, TCP 접속 ms, SSH 왕복 ms).
        연결 직후 30초는 None(기록 안 함): 과거 기록을 몰아 보내는 동안이라 왕복 시간이 부풀고 아직 첫 응답 전이라 '끊김'으로 잡힘."""
        now = time.time()
        if self.legacy:   # PowerShell 전용 모드: 매번 새 SSH 접속 → 접속 시간만
            ok = bool(self.sys_raw) and now - self.sys_t < SYS_STALE_SEC
            return ok, self.latency_ms if ok else None, None
        fresh = now - self.rtt_t < PING_SEC * 2.5
        alive = bool(self.proc and self.proc.poll() is None)
        if alive and now - self.stream_t < 30:
            return None
        return alive and fresh, self.latency_ms if alive else None, self.rtt_ms if fresh else None

    def _hang_guard(self, p):
        """응답이 오래 없거나 멈춤 요청이 오면 그 연결을 끊음 (멈춤 요청 뒤에 시작된 연결도 확실히 정리)."""
        while p.poll() is None:
            time.sleep(2)
            if self._stop.is_set():
                self._kill()
                return
            if time.time() - self.last_line > HUNG_SEC:
                self.error = "응답 없음 → 다시 연결"
                self._kill()
                return

    def _legacy_loop(self, target):
        self.legacy = Remote(self._order(), None)
        while not self._stop.is_set():
            t = time.time()
            r = self.legacy.snapshot()
            if r.get("ok") and r.get("windows"):
                self._on_sys({"t": time.time(), "probe": r["windows"], "extras": None})
                self.latency_ms = r.get("latency_ms")
            else:
                self.error = r.get("error")
                return
            self._stop.wait(max(1.0, self.sys_interval - (time.time() - t)))

    def _on_sys(self, d):
        now = time.time()
        extras = parse_wsl(d["extras"]) if d.get("extras") else {}
        w = d.get("probe") or {}
        self.sys_raw = {**extras, "windows": w or None}
        self.sys_t = self.last_ok = now
        self.error = None
        if w.get("mem_total"):
            self.hist["cpu"].append(w.get("cpu") or 0)
            self.hist["mem"].append(round(100 * (1 - (w.get("mem_free") or 0) / w["mem_total"]), 1))
            self.hist["down"].append(w.get("net_down") or 0)
            self.hist["up"].append(w.get("net_up") or 0)
            if w.get("cpu_per"):
                self.hist_cores.append(list(w["cpu_per"]))
        if self.active and not self.legacy:
            self.latency_ms = _tcp_latency(self.active)

    # ----- 상태 (v1 /api/state 의 remote · remote_claude 와 같은 형식) -----
    def system_state(self):
        ok = bool(self.sys_raw) and time.time() - self.sys_t < SYS_STALE_SEC
        base = self.sys_raw or {}
        return {"ok": ok, "host": self.active, "fallback": bool(self.active and self.active != self.targets[0]),
                "latency_ms": self.latency_ms, "last_ok": self.last_ok,
                "error": None if ok else (self.error or ("첫 수집 중" if not self.sys_raw else "응답 없음")),
                "sample_t": self.sys_t or None, **base,
                "hist": {k: list(v) for k, v in self.hist.items()}, "hist_cores": list(self.hist_cores)}

    def key(self, k=None):
        """상태·DB 키: 기본 폴더 = PC id, 다른 계정 폴더 = 'PC id~프로필 id'."""
        return f"{self.id}~{k}" if k else self.id

    def provider_states(self, pid):
        """{키: 결과} — 기본 폴더 + 수집 중인 다른 계정 폴더."""
        out = {self.id: self.provider_state(pid)}
        for (p, k), _ in list(self.prov.items()):
            if p == pid and k:
                out[self.key(k)] = self.provider_state(pid, k)
        return out

    def provider_state(self, pid, k=""):
        """공급자 하나(계정 폴더 하나)의 최신 결과 {ok, error, usage, sessions, limits, account, updated}."""
        got = self.prov.get((pid, k))
        if not got and k:
            return {"ok": False, "error": self.error or "첫 수집 중"}
        if not got:
            if self.info and self.info.get("mode") == "powershell":
                return {"ok": False, "error": "Python이 없어 AI 사용량을 수집할 수 없습니다"}
            if self.plist is not None and pid not in self.plist:
                return {"ok": False, "absent": True, "error": "기록 없음"}
            return {"ok": False, "error": self.error or "첫 수집 중"}
        d, t = got
        fresh = time.time() - t < CLAUDE_STALE_SEC
        return {"ok": fresh, "error": None if fresh else (self.error or "응답 없음"), "updated": t, **d}

    def claude_state(self):
        return self.provider_state("claude")

    def describe(self):
        """설정 화면·API용 요약 (지표 본문 제외)."""
        hello = self.hello or {}
        info = self.info or {}
        return {"active_target": self.active, "error": self.error, "mode": info.get("mode"), "os": info.get("os"),
                "wsl_distro": info.get("wsl_distro"), "home": hello.get("using_home") or info.get("home"),
                "hostname": hello.get("hostname") or (info.get("agent") or {}).get("hostname"),
                "os_name": hello.get("os_name"), "providers": info.get("providers"), "active_providers": self.plist,
                "profiles_found": hello.get("profiles_found") or (info.get("agent") or {}).get("profiles_found") or {},
                "last_ok": self.last_ok, "latency_ms": self.latency_ms}


if __name__ == "__main__":
    import sys
    print(json.dumps(detect(*sys.argv[1:]), indent=2, ensure_ascii=False))
