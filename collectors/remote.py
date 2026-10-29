"""v1 원격 수집(SSH 접속을 주기마다 새로 함). v2에서는 Python이 없는 Windows 원격 PC의 축소 모드(PowerShell 전용)와
parse_wsl(에이전트의 Docker/GPU/WSL 섹션 해석)에만 쓰인다. 프로브 스크립트는 stdin으로 전달."""
import threading
import json
import subprocess
import time
from collections import deque
from pathlib import Path

try:
    from collectors import procjob
except ImportError:   # collectors 폴더에서 직접 실행할 때
    import procjob

PROBES = Path(__file__).resolve().parent.parent / "probes"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ssh(host, remote_cmd, stdin=None, timeout=45):
    args = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12",
            "-o", "ServerAliveInterval=5", host, remote_cmd]
    return procjob.run(args, input=stdin, timeout=timeout)


def _decode(b):
    for enc in ("utf-8", "cp949"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "replace")


def _size(s):
    """docker stats의 '1.5GiB' 같은 값을 바이트로."""
    s = s.strip()
    units = {"B": 1, "kB": 1e3, "KB": 1e3, "KiB": 1024, "MB": 1e6, "MiB": 1024**2,
             "GB": 1e9, "GiB": 1024**3, "TB": 1e12, "TiB": 1024**4}
    for u in sorted(units, key=len, reverse=True):
        if s.endswith(u):
            try:
                return float(s[: -len(u)]) * units[u]
            except ValueError:
                return 0
    try:
        return float(s)
    except ValueError:
        return 0


def parse_wsl(text):
    sections, cur = {}, None
    for line in text.splitlines():
        if line.startswith("@@"):
            cur = line[2:].strip()
            sections[cur] = []
        elif cur:
            sections[cur].append(line)

    def jsonl(lines):
        out = []
        for l in lines:
            l = l.strip()
            if l.startswith("{"):
                try:
                    out.append(json.loads(l))
                except Exception:
                    pass
        return out

    stats = {s.get("Name"): s for s in jsonl(sections.get("STATS", []))}
    containers = []
    for c in jsonl(sections.get("PS", [])):
        st = stats.get(c.get("Names"), {})
        mem_used = 0
        if st.get("MemUsage"):
            mem_used = _size(st["MemUsage"].split("/")[0])
        containers.append({
            "name": c.get("Names"),
            "image": c.get("Image"),
            "state": c.get("State"),
            "status": c.get("Status"),
            "ports": c.get("Ports"),
            "cpu": float((st.get("CPUPerc") or "0%").rstrip("%") or 0),
            "mem": mem_used,
            "mem_pct": float((st.get("MemPerc") or "0%").rstrip("%") or 0),
        })
    containers.sort(key=lambda c: (c["state"] != "running", c["name"] or ""))
    docker_err = None
    if not containers and sections.get("PS"):
        docker_err = " ".join(sections["PS"])[:200] or None

    gpus = []
    for l in sections.get("GPU", []):
        p = [x.strip() for x in l.split(",")]
        if len(p) >= 4 and not l.lower().startswith(("nvidia-smi", "failed", "command")):
            def num(x):
                try:
                    return float(x)
                except ValueError:
                    return None
            gpus.append({"name": p[0], "util": num(p[1]), "mem_used": num(p[2]),
                         "mem_total": num(p[3]), "temp": num(p[4]) if len(p) > 4 else None,
                         "power": num(p[5]) if len(p) > 5 else None})

    mem = None
    m = (sections.get("MEM") or [""])[0].split()
    if len(m) == 3:
        total, used, avail = map(int, m)
        mem = {"total": total, "used": total - avail}
    disk = None
    d = (sections.get("DF") or [""])[0].split()
    if len(d) == 3:
        disk = {"total": int(d[1]), "used": int(d[2])}
    load = None
    lo = sections.get("LOAD", [])
    if len(lo) >= 2:
        load = {"l1": float(lo[0].split()[0]), "l5": float(lo[0].split()[1]), "cpus": int(lo[1])}
    uptime = None
    if sections.get("UPTIME"):
        try:
            uptime = int(float(sections["UPTIME"][0]))
        except ValueError:
            pass
    return {"containers": containers, "docker_error": docker_err, "gpus": gpus,
            "mem": mem, "disk": disk, "load": load, "uptime": uptime}


class Remote:
    def __init__(self, hosts, wsl_distro="Ubuntu", win_every=1):
        self.hosts = hosts
        self.distro = wsl_distro
        self.active = None
        self.win_every = win_every
        self.tick = 0
        self.last_ok = None
        self.last = {}
        self.win = None
        self.hist = {k: deque(maxlen=60) for k in ("cpu", "mem", "down", "up")}
        self.hist_cores = deque(maxlen=60)   # 코어별 사용률 기록 (10초 x 60 = 최근 10분)
        self.wsl_script = (PROBES / "remote_wsl.sh").read_bytes().replace(b"\r\n", b"\n")
        # 스크립트가 길어 명령줄 길이 제한(8191자)을 넘으므로 stdin으로 보내고 원격에서 통째로 실행
        ps = (PROBES / "remote_win.ps1").read_text(encoding="utf-8")
        ps = "\n".join(l for l in ps.splitlines() if not l.lstrip().startswith("#"))
        self.win_script = ps.encode("utf-8")
        self.win_cmd = ("powershell -NoProfile -NonInteractive -Command "
                        "\"[Console]::InputEncoding=[Text.Encoding]::UTF8; iex ([Console]::In.ReadToEnd())\"")

    def _win_probe(self, host, box):
        try:
            r = _ssh(host, self.win_cmd, stdin=self.win_script, timeout=45)
            txt = _decode(r.stdout).strip()
            if "{" in txt:
                box["win"] = json.loads(txt[txt.find("{"):])
        except Exception:
            pass

    def _try_hosts(self):
        order = ([self.active] if self.active else []) + [h for h in self.hosts if h != self.active]
        err = None
        if not self.distro:
            # WSL 없음(PowerShell 전용 동작): Windows 프로브 성공 여부로 연결 판단
            for h in order:
                t = time.time()
                box = {}
                self._win_probe(h, box)
                if box.get("win"):
                    self.active = h
                    self._pending_win = box["win"]
                    return h, int((time.time() - t) * 1000), "@@END", None
                err = f"{h}: 응답 없음"
            return None, None, None, err
        for h in order:
            t = time.time()
            try:
                r = _ssh(h, f"wsl -d {self.distro} bash -s", stdin=self.wsl_script)
            except subprocess.TimeoutExpired:
                err = f"{h}: timeout"
                continue
            out = _decode(r.stdout)
            if "@@END" in out:
                self.active = h
                return h, int((time.time() - t) * 1000), out, None
            err = f"{h}: {_decode(r.stderr).strip()[:160] or 'exit ' + str(r.returncode)}"
        return None, None, None, err

    def snapshot(self):
        # Windows 프로브(수 초 소요)를 WSL 프로브와 동시에 실행
        box = {}
        if self.distro:
            guess = self.active or self.hosts[0]
            th = threading.Thread(target=self._win_probe, args=(guess, box), daemon=True)
            th.start()
            host, latency, out, err = self._try_hosts()
            th.join(timeout=50)
            if host and host != guess:
                self._win_probe(host, box)
        else:
            host, latency, out, err = self._try_hosts()
            if host:
                box["win"] = self._pending_win
        now = time.time()
        if host:
            data = parse_wsl(out)
            if box.get("win"):
                self.win = box["win"]
            self.tick += 1
            self.last_ok = now
            if self.win and self.win.get("mem_total"):
                self.hist["cpu"].append(self.win.get("cpu") or 0)
                self.hist["mem"].append(round(100 * (1 - self.win["mem_free"] / self.win["mem_total"]), 1))
                self.hist["down"].append(self.win.get("net_down") or 0)
                self.hist["up"].append(self.win.get("net_up") or 0)
                if self.win.get("cpu_per"):
                    self.hist_cores.append(list(self.win["cpu_per"]))
            data["hist"] = {k: list(v) for k, v in self.hist.items()}
            data["hist_cores"] = list(self.hist_cores)
            self.last = {**data, "windows": self.win}
            return {"ok": True, "host": host, "latency_ms": latency,
                    "last_ok": self.last_ok, "error": None, **self.last}
        self.active = None
        return {"ok": False, "host": None, "latency_ms": None, "last_ok": self.last_ok,
                "error": err, **self.last}


if __name__ == "__main__":
    import sys
    hosts = sys.argv[1:]   # 예: py collectors/remote.py <ssh 대상> [대체 주소...]
    print(json.dumps(Remote(hosts).snapshot(), indent=2, ensure_ascii=False))
