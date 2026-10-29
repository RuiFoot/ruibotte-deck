# 원격 PC에서 실행되는 수집 에이전트 (v2). 표준 라이브러리만 사용.
# 이 PC가 claude_usage.py, sessions.py, codex_usage.py, gemini_usage.py, providers.py 소스 뒤에
# 이 파일을 붙이고 설정값(_CFG)을 채워 SSH stdin으로 보낸다.
# 원격에는 아무것도 설치하지 않는다. 연결 하나를 유지하며 줄 단위로 결과를 보낸다:
#   @@HELLO {...}   시작 정보(OS, 홈 폴더 등)        @@DETECT {...}  감지 전용 모드 결과 후 종료
#   @@SYS {...}     시스템 지표(sys_interval마다)     @@PLIST {...}  감지된 AI 도구 목록
#   @@PROV {...}    도구별 사용량·세션·한도(interval마다)  @@EV {...}  DB용 새 사용량 레코드
#   @@PONG {n}       이 PC가 stdin으로 보낸 '@@PING n'에 대한 답(연결 왕복 시간 측정)
#   @@ERR 메시지
import base64 as _b64
import platform as _platform
import shutil as _shutil
import socket as _socket
import sys as _sys
import tempfile as _tempfile
import threading as _threading

_CFG = __CFG__
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_LOCK = _threading.Lock()


def _is_wsl():
    try:
        return "microsoft" in open("/proc/version").read().lower()
    except Exception:
        return False


_WSL = _sys.platform.startswith("linux") and _is_wsl()
_PS = (_shutil.which("powershell.exe") or _shutil.which("powershell")) if (_WSL or os.name == "nt") else None


def _emit(tag, obj):
    try:
        with _LOCK:
            _sys.stdout.write(tag + json.dumps(obj, default=str, ensure_ascii=False) + "\n")
            _sys.stdout.flush()
    except Exception:
        os._exit(0)   # 이 PC 쪽 연결이 끊김 → 원격에 고아 프로세스를 남기지 않음


def _watchdog():
    # 리눅스/WSL: 부모(sshd 쪽)가 사라지면 부모 PID가 바뀜 → 종료. Windows는 출력 실패(_emit)로 감지.
    ppid = os.getppid()
    while True:
        time.sleep(3)
        if os.getppid() != ppid:
            os._exit(0)


def _stdin_watch():
    """이 PC가 stdin을 열어 둔 채 연결을 유지함 → EOF(= SSH 연결 끊김)면 바로 종료.
    연결 품질 측정: '@@PING <번호>' 줄이 오면 바로 '@@PONG' 으로 답함 (SSH 왕복 시간)."""
    try:
        for line in _sys.stdin.buffer:
            if line.startswith(b"@@PING"):
                _emit("@@PONG", {"n": line[6:].strip().decode("ascii", "replace")})
    except Exception:
        pass
    os._exit(0)


def _run(args, timeout=20):
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=_NOWIN)
        return r.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


# ---------- 홈 폴더 · AI 도구 감지 ----------
_MARKERS = {
    "claude": [".claude/history.jsonl", ".claude/projects", ".claude/sessions"],
    "codex": [".codex/session_index.jsonl", ".codex/sessions"],
    "gemini": [".gemini/tmp"],
}


def _activity(home, provider):
    best = None
    for m in _MARKERS[provider]:
        try:
            t = (home / m).stat().st_mtime
            best = t if best is None or t > best else best
        except OSError:
            pass
    return best


def _home_candidates():
    out, seen = [], set()
    roots = [Path.home().parent] + ([Path("/mnt/c/Users")] if _WSL else [])
    cands = [Path.home()]
    for r in roots:
        try:
            cands += sorted(p for p in r.iterdir() if p.is_dir())
        except OSError:
            pass
    for c in cands:
        k = str(c)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def _detect():
    homes = []
    for h in _home_candidates():
        prov = {p: t for p in _MARKERS if (t := _activity(h, p))}
        if prov or h == Path.home():
            homes.append({"path": str(h), "providers": prov})
    # 기본 홈: Claude/Codex/Gemini 중 가장 최근에 쓴 흔적이 있는 폴더 (SSH 계정과 실제 사용 계정이 다를 수 있음)
    best = max(homes, key=lambda h: max(h["providers"].values(), default=0), default=None)
    if not best or not best["providers"]:
        best = {"path": str(Path.home())}
    if _WSL:
        os_kind, os_name = "windows", "Windows (WSL " + _platform.release() + ")"
    elif os.name == "nt":
        os_kind, os_name = "windows", _platform.platform()
    else:
        os_kind = "linux" if _sys.platform.startswith("linux") else _sys.platform
        os_name = _platform.platform()
    return {"os": os_kind, "os_name": os_name, "hostname": _socket.gethostname(), "python": _platform.python_version(),
            "wsl": _WSL, "powershell": bool(_PS), "home": str(Path.home()), "homes": homes, "best_home": best["path"],
            "profiles_found": find_profiles(best["path"])}


# ---------- 시스템 지표 ----------
def _win_probe():
    """Windows 공통 프로브(remote_win.ps1)를 실행. WSL에서는 interop으로 powershell.exe 호출."""
    if not _PS or not _CFG.get("ps1"):
        return None
    out = _run([_PS, "-NoProfile", "-NonInteractive", "-EncodedCommand", _CFG["ps1"]], timeout=60)
    i = out.find("{")
    try:
        return json.loads(out[i:]) if i >= 0 else None
    except Exception:
        return None


class _Linux:
    """/proc 기반 (psutil 없이). 결과 형식은 Windows 프로브와 같은 키를 쓴다."""

    def __init__(self):
        self.tck = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        self.ncpu = os.cpu_count() or 1
        self.prev_cpu = self._cpu()
        self.prev_net = self._net()
        self.prev_proc = self._procs()
        self.prev_t = time.time()
        self.spec = self._spec()

    @staticmethod
    def _read(p):
        try:
            with open(p) as f:
                return f.read()
        except OSError:
            return ""

    def _cpu(self):
        rows = []
        for line in self._read("/proc/stat").splitlines():
            if not line.startswith("cpu"):
                break
            v = [int(x) for x in line.split()[1:]]
            idle = v[3] + (v[4] if len(v) > 4 else 0)
            rows.append((idle, sum(v[:8])))
        return rows

    def _net(self):
        rx = tx = 0
        for line in self._read("/proc/net/dev").splitlines()[2:]:
            name, _, rest = line.partition(":")
            if name.strip() == "lo":
                continue
            f = rest.split()
            if len(f) >= 9:
                rx += int(f[0])
                tx += int(f[8])
        return rx, tx

    def _procs(self):
        out = {}
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            s = self._read(f"/proc/{d}/stat")
            r = s.rfind(")")
            if r < 0:
                continue
            name = s[s.find("(") + 1:r]
            f = s[r + 2:].split()
            try:
                out[int(d)] = (name, int(f[11]) + int(f[12]), int(f[21]) * os.sysconf("SC_PAGE_SIZE"))
            except (ValueError, IndexError):
                pass
        return out

    def _spec(self):
        cpu_name, cores = None, set()
        phys = None
        for line in self._read("/proc/cpuinfo").splitlines():
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip()
            if k == "model name" and not cpu_name:
                cpu_name = v
            elif k == "physical id":
                phys = v
            elif k == "core id":
                cores.add((phys, v))
        osr = dict(l.split("=", 1) for l in self._read("/etc/os-release").splitlines() if "=" in l)
        return {"cpu_name": cpu_name or _platform.processor(), "cores": len(cores) or self.ncpu,
                "threads": self.ncpu, "os_name": osr.get("PRETTY_NAME", _platform.platform()).strip('"')}

    def snap(self):
        now = time.time()
        dt = max(now - self.prev_t, 0.001)
        cpu = self._cpu()
        pct = [round(100 * (1 - (c[0] - p[0]) / max(c[1] - p[1], 1)), 1) for c, p in zip(cpu, self.prev_cpu)]
        net = self._net()
        procs = self._procs()
        agg = {}
        for pid, (name, ticks, rss) in procs.items():
            prev = self.prev_proc.get(pid)
            used = (ticks - prev[1]) if prev and prev[0] == name else 0
            a = agg.setdefault(name, [0.0, 0])
            a[0] += 100 * used / self.tck / dt / self.ncpu
            a[1] += rss
        mi = {}
        for line in self._read("/proc/meminfo").splitlines():
            k, _, v = line.partition(":")
            try:
                mi[k] = int(v.split()[0]) * 1024
            except (ValueError, IndexError):
                pass
        disks, seen = [], set()
        for line in self._read("/proc/mounts").splitlines():
            f = line.split()
            if len(f) < 3 or not f[0].startswith("/dev/") or f[0] in seen or f[2] in ("squashfs", "iso9660"):
                continue
            try:
                st = os.statvfs(f[1])
            except OSError:
                continue
            if st.f_blocks:
                seen.add(f[0])
                disks.append({"name": f[1], "size": st.f_blocks * st.f_frsize, "free": st.f_bavail * st.f_frsize})
        down, up = (net[0] - self.prev_net[0]) / dt, (net[1] - self.prev_net[1]) / dt
        self.prev_cpu, self.prev_net, self.prev_proc, self.prev_t = cpu, net, procs, now
        try:
            uptime = int(float(self._read("/proc/uptime").split()[0]))
        except (ValueError, IndexError):
            uptime = None
        return {
            "host": _socket.gethostname(), "cpu": pct[0] if pct else None, "cpu_per": [round(p) for p in pct[1:]],
            "mem_total": mi.get("MemTotal"), "mem_free": mi.get("MemAvailable", mi.get("MemFree")),
            "uptime_sec": uptime, "disks": disks[:8],
            "top_cpu": [{"name": n, "cpu": round(v[0], 1)} for n, v in sorted(agg.items(), key=lambda x: -x[1][0])[:8]],
            "top_mem": [{"name": n, "mem": v[1]} for n, v in sorted(agg.items(), key=lambda x: -x[1][1])[:8]],
            "net_down": down, "net_up": up, "net_total_down": net[0], "net_total_up": net[1],
            "proc_count": len(procs), "gpus": [], "gpu_util": None,
            "pagefile_used": mi.get("SwapTotal", 0) - mi.get("SwapFree", 0), "pagefile_total": mi.get("SwapTotal"),
            **self.spec,
        }


class _Mac:
    """macOS 간이 수집 (ps/sysctl/vm_stat/netstat). 시험 환경이 없어 최소 항목만."""

    def __init__(self):
        self.ncpu = os.cpu_count() or 1
        self.prev_net, self.prev_t = self._net(), time.time()
        self.spec = {"cpu_name": _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or _platform.processor(),
                     "cores": int(_run(["sysctl", "-n", "hw.physicalcpu"]).strip() or self.ncpu),
                     "threads": self.ncpu,
                     "os_name": "macOS " + _run(["sw_vers", "-productVersion"]).strip()}

    def _net(self):
        rx = tx = 0
        seen = set()
        for line in _run(["netstat", "-ibn"]).splitlines()[1:]:
            f = line.split()
            if len(f) >= 10 and f[0] not in seen and f[2].startswith("<Link") and not f[0].startswith("lo"):
                seen.add(f[0])
                try:
                    rx += int(f[6])
                    tx += int(f[9])
                except ValueError:
                    pass
        return rx, tx

    def snap(self):
        now = time.time()
        agg = {}
        for line in _run(["ps", "-A", "-o", "%cpu=,rss=,comm="]).splitlines():
            f = line.split(None, 2)
            if len(f) == 3:
                a = agg.setdefault(f[2].rsplit("/", 1)[-1], [0.0, 0])
                a[0] += float(f[0]) / self.ncpu
                a[1] += int(f[1]) * 1024
        total = int(_run(["sysctl", "-n", "hw.memsize"]).strip() or 0)
        vm, page = {}, 4096
        for line in _run(["vm_stat"]).splitlines():
            if "page size of" in line:
                page = int(line.split("page size of")[1].split()[0])
            k, _, v = line.partition(":")
            vm[k.strip()] = int(v.strip().rstrip(".") or 0) if v.strip().rstrip(".").isdigit() else 0
        free = (vm.get("Pages free", 0) + vm.get("Pages inactive", 0) + vm.get("Pages speculative", 0)) * page
        st = os.statvfs("/")
        boot = _run(["sysctl", "-n", "kern.boottime"])
        try:
            uptime = int(now - int(boot.split("sec =")[1].split(",")[0]))
        except (IndexError, ValueError):
            uptime = None
        net = self._net()
        dt = max(now - self.prev_t, 0.001)
        down, up = (net[0] - self.prev_net[0]) / dt, (net[1] - self.prev_net[1]) / dt
        self.prev_net, self.prev_t = net, now
        return {
            "host": _socket.gethostname(), "cpu": round(min(100.0, sum(a[0] for a in agg.values())), 1), "cpu_per": [],
            "mem_total": total or None, "mem_free": free if total else None, "uptime_sec": uptime,
            "disks": [{"name": "/", "size": st.f_blocks * st.f_frsize, "free": st.f_bavail * st.f_frsize}],
            "top_cpu": [{"name": n, "cpu": round(v[0], 1)} for n, v in sorted(agg.items(), key=lambda x: -x[1][0])[:8]],
            "top_mem": [{"name": n, "mem": v[1]} for n, v in sorted(agg.items(), key=lambda x: -x[1][1])[:8]],
            "net_down": down, "net_up": up, "net_total_down": net[0], "net_total_up": net[1],
            "proc_count": len(agg), "gpus": [], "gpu_util": None, **self.spec,
        }


def _extras():
    """Docker · NVIDIA GPU (+ WSL이면 WSL 메모리/디스크/부하). v1 remote_wsl.sh 와 같은 섹션 형식 → 이 PC가 parse_wsl로 해석."""
    parts = []
    docker = _shutil.which("docker")
    parts.append("@@PS")
    parts.append(_run([docker, "ps", "-a", "--format", "{{json .}}"]) if docker else "docker 없음")
    parts.append("@@STATS")
    if docker:
        parts.append(_run([docker, "stats", "--no-stream", "--format", "{{json .}}"], timeout=30))
    smi = _shutil.which("nvidia-smi") or (_shutil.which("nvidia-smi.exe") if _WSL else None)
    parts.append("@@GPU")
    if smi:
        parts.append(_run([smi, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                           "--format=csv,noheader,nounits"]))
    if _WSL:
        mi = {}
        for line in open("/proc/meminfo"):
            k, _, v = line.partition(":")
            mi[k] = int(v.split()[0]) * 1024
        parts += ["@@MEM", f"{mi['MemTotal']} {mi['MemTotal'] - mi['MemAvailable']} {mi['MemAvailable']}"]
        st = os.statvfs("/")
        parts += ["@@DF", f"/ {st.f_blocks * st.f_frsize} {(st.f_blocks - st.f_bfree) * st.f_frsize}"]
        parts += ["@@LOAD", open("/proc/loadavg").read().strip(), str(os.cpu_count())]
        parts += ["@@UPTIME", open("/proc/uptime").read().split()[0]]
    parts.append("@@END")
    return "\n".join(parts)


def _sys_loop():
    base = _Linux() if (_sys.platform.startswith("linux") and not _WSL) else _Mac() if _sys.platform == "darwin" else None
    while True:
        t = time.time()
        box = {}
        th = _threading.Thread(target=lambda: box.__setitem__("extras", _extras()), daemon=True)
        th.start()
        try:
            probe = _win_probe() if _PS else base.snap() if base else None
        except Exception as e:
            probe = None
            _emit("@@ERR", "sys: " + repr(e)[:200])
        th.join(60)
        _emit("@@SYS", {"t": time.time(), "probe": probe, "extras": box.get("extras")})
        time.sleep(max(1.0, _CFG["sys_interval"] - (time.time() - t)))


def _prov_loop(home):
    """감지된 AI 도구마다 집계해 @@PROV(화면용)와 @@EV(DB용 새 레코드)를 보냄.
    연결 직후 한 번은 보관 중인 레코드를 since(이 PC DB에 이미 있는 마지막 시각) 이후로 모두 보내 빈 곳을 채움.
    여러 계정: 기본 폴더(k="") + 설정의 계정 폴더(profiles: {도구: [{k, dir, since, backfill}]})마다 따로 집계, 줄마다 k 포함."""
    enabled = _CFG.get("providers")
    since = _CFG.get("since") or {}
    backfill = dict(_CFG.get("backfill") or {})   # provider -> 이 시각 이전 기록을 보냄 (아직 안 채운 공급자만)
    extra = _CFG.get("profiles") or {}
    runners, synced = {}, set()

    def _backfill(pid, k, r, before):
        try:
            ev = r.history_events(_CFG.get("backfill_days") or 90, before)
            for i in range(0, len(ev), 2000):
                _emit("@@EV", {"p": pid, "k": k, "account": r.account(), "events": ev[i:i + 2000], "backfill": True})
            _emit("@@BFDONE", {"p": pid, "k": k, "n": len(ev)})
        except Exception as e:
            _emit("@@ERR", f"backfill {pid}: " + repr(e)[:300])
    while True:
        t = time.time()
        found = detect_providers(home, enabled)
        _emit("@@PLIST", {"providers": found})
        slots = [(pid, "", None, since.get(pid), backfill.pop(pid, None)) for pid in found]
        for pid, profs in extra.items():
            if enabled is None or pid in enabled:
                slots += [(pid, x["k"], x["dir"], x.get("since"), x.pop("backfill", None))
                          for x in profs if profile_detect(pid, x["dir"])]
        for pid, k, root, since_ts, bf in slots:
            try:
                r = runners.get((pid, k))
                if r is None:
                    r = runners[(pid, k)] = PROVIDERS[pid](home, root=root)
                snap = r.tick()
                _emit("@@PROV", {"p": pid, "k": k, **snap})
                if (pid, k) in synced:
                    ev = r.drain_events()
                else:
                    ev = r.all_events(max(0, (since_ts or 0) - 600))
                    synced.add((pid, k))
                    if bf:
                        _threading.Thread(target=_backfill, args=(pid, k, r, bf), daemon=True).start()
                for i in range(0, len(ev), 2000):
                    _emit("@@EV", {"p": pid, "k": k, "account": snap.get("account"), "events": ev[i:i + 2000]})
            except Exception as e:
                _emit("@@ERR", f"{pid}{'/' + k if k else ''}: " + repr(e)[:300])
        time.sleep(max(1.0, _CFG["interval"] - (time.time() - t)))


def _main():
    if _CFG.get("detect"):
        _emit("@@DETECT", _detect())
        return
    _threading.Thread(target=_stdin_watch, daemon=True).start()
    if os.name != "nt":
        _threading.Thread(target=_watchdog, daemon=True).start()
    info = _detect()
    home = Path(_CFG.get("home") or info["best_home"]).expanduser()
    _emit("@@HELLO", {**info, "using_home": str(home)})
    if _CFG.get("system", True):
        _threading.Thread(target=_sys_loop, daemon=True).start()
    _prov_loop(home)


_main()
