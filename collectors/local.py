"""이 PC 상태 수집 (psutil)."""
import json
import os
import platform
import socket
import subprocess
import time
from collections import deque

import psutil

try:
    from collectors import winprocs
except ImportError:   # collectors 폴더에서 직접 실행할 때
    import winprocs


class Local:
    def __init__(self, history=150):
        self.hist = {k: deque(maxlen=history) for k in ("cpu", "mem", "down", "up")}
        self.hist_cores = deque(maxlen=90)   # 코어별 사용률 기록 (2초 x 90 = 최근 3분, 잔디 히트맵용)
        self.prev_net = psutil.net_io_counters()
        self.prev_t = time.time()
        self.procs = {}
        self.prev_ps = None       # winprocs: 지난 전체 프로세스 목록 (CPU 시간 차이 계산용)
        self.swap = (0.0, None)   # swap_memory는 느려서(0.25초) 30초마다만
        psutil.cpu_percent(percpu=True)
        self.ncpu = psutil.cpu_count() or 1
        self.spec = self._spec()

    def _spec(self):
        spec = {"cpu_name": platform.processor(), "os_name": platform.platform(),
                "cores": psutil.cpu_count(logical=False), "threads": psutil.cpu_count()}
        try:
            ps = ("$p=Get-CimInstance Win32_Processor|Select -First 1;$o=Get-CimInstance Win32_OperatingSystem;"
                  "@{cpu_name=$p.Name.Trim();os_name=$o.Caption}|ConvertTo-Json -Compress")
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=20,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            spec.update(json.loads(r.stdout.decode("utf-8", "replace")))
        except Exception:
            pass
        return spec

    def _top_fast(self):
        """Windows: 전체 프로세스를 한 번의 시스템 호출로 (psutil 프로세스별 조회는 수십 초 걸림)."""
        now = time.time()
        cur = winprocs.snapshot()
        prev, self.prev_ps = self.prev_ps, (now, cur)
        agg = {}
        for pid, (name, cpu_s, ws) in cur.items():
            if pid == 0 or name in ("System Idle Process", "Idle"):
                continue
            cpu = 0.0
            if prev:
                old = prev[1].get(pid)
                if old and old[0] == name:
                    cpu = max(0.0, (cpu_s - old[1]) / max(now - prev[0], 0.001) * 100 / self.ncpu)
            key = name.removesuffix(".exe")
            a = agg.setdefault(key, {"name": key, "cpu": 0.0, "mem": 0})
            a["cpu"] += cpu
            a["mem"] += ws
        by_cpu = [{"name": a["name"], "cpu": round(a["cpu"], 1)} for a in sorted(agg.values(), key=lambda a: -a["cpu"])[:8]]
        by_mem = [{"name": a["name"], "mem": a["mem"]} for a in sorted(agg.values(), key=lambda a: -a["mem"])[:8]]
        return by_cpu, by_mem, len(cur)

    def _top(self):
        if winprocs.available():
            try:
                return self._top_fast()
            except Exception:
                pass
        by_cpu, by_mem = self._top_psutil()
        return by_cpu, by_mem, len(psutil.pids())

    def _top_psutil(self):
        seen, rows = set(), []
        for p in psutil.process_iter(["name", "memory_info"]):
            seen.add(p.pid)
            if p.pid not in self.procs:
                self.procs[p.pid] = p
                try:
                    p.cpu_percent(None)
                except psutil.Error:
                    pass
                continue
            try:
                cpu = self.procs[p.pid].cpu_percent(None) / self.ncpu
            except psutil.Error:
                continue
            name = p.info["name"] or "?"
            if name in ("System Idle Process", "Idle"):
                continue
            mem = p.info["memory_info"].rss if p.info["memory_info"] else 0
            rows.append({"name": name.removesuffix(".exe"), "cpu": round(cpu, 1), "mem": mem})
        for pid in list(self.procs):
            if pid not in seen:
                del self.procs[pid]
        # 같은 이름 프로세스(msedge 등)는 CPU·메모리를 합산 (원격 PC 프로브와 같은 기준)
        agg = {}
        for r in rows:
            a = agg.setdefault(r["name"], {"name": r["name"], "cpu": 0.0, "mem": 0})
            a["cpu"] += r["cpu"]
            a["mem"] += r["mem"]
        by_cpu = [{"name": a["name"], "cpu": round(a["cpu"], 1)} for a in sorted(agg.values(), key=lambda a: -a["cpu"])[:8]]
        by_mem = [{"name": a["name"], "mem": a["mem"]} for a in sorted(agg.values(), key=lambda a: -a["mem"])[:8]]
        return by_cpu, by_mem

    def snapshot(self):
        now = time.time()
        per = psutil.cpu_percent(percpu=True)
        cpu = sum(per) / len(per) if per else 0
        vm = psutil.virtual_memory()
        net = psutil.net_io_counters()
        dt = max(now - self.prev_t, 0.001)
        down = (net.bytes_recv - self.prev_net.bytes_recv) / dt
        up = (net.bytes_sent - self.prev_net.bytes_sent) / dt
        self.prev_net, self.prev_t = net, now
        for k, v in (("cpu", cpu), ("mem", vm.percent), ("down", down), ("up", up)):
            self.hist[k].append(round(v, 1))
        self.hist_cores.append([round(p) for p in per])

        disks = []
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            disks.append({"name": part.mountpoint.rstrip("\\"), "total": u.total, "used": u.used})

        bat = psutil.sensors_battery()
        freq = psutil.cpu_freq()
        by_cpu, by_mem, nproc = self._top()
        if now - self.swap[0] > 30:
            sw = psutil.swap_memory()
            self.swap = (now, {"used": sw.used, "total": sw.total})
        return {
            "host": socket.gethostname(),
            "spec": self.spec,
            "cpu": round(cpu, 1),
            "cpu_per": per,
            "cpu_freq": round(freq.current) if freq else None,
            "mem": {"total": vm.total, "used": vm.total - vm.available, "pct": vm.percent},
            "net": {"down": down, "up": up},
            "disks": disks,
            "battery": {"pct": bat.percent, "plugged": bat.power_plugged} if bat else None,
            "uptime": int(now - psutil.boot_time()),
            "proc_count": nproc,
            "swap": self.swap[1],
            "net_total": {"down": net.bytes_recv, "up": net.bytes_sent},
            "top_cpu": by_cpu,
            "top_mem": by_mem,
            "hist": {k: list(v) for k, v in self.hist.items()},
            "hist_cores": list(self.hist_cores),
        }
