"""SSH 터널: 원격 PC의 포트를 이 PC의 localhost 로 열고 닫기 (`ssh -N -L`).

목록과 켜짐 여부(`on`)는 DB 설정 `tunnels.list`(DB가 없으면 메모리)에 저장 → 켜 둔 터널은 앱을 다시 켜도 자동으로 연결.
켜 둔 터널이 끊기면(망 전환·원격 재부팅 등) 원격 PC의 현재 연결 주소 → 대체 주소 순으로 다시 연결한다.
ssh 는 procjob 으로 띄우므로 앱이 어떻게 끝나든 함께 종료된다.
"""
import re
import secrets
import subprocess
import threading
import time

from collectors import procjob

_HOST_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9.\-]{0,252}|::1)$")
# 원격의 '자기 자신' 주소. 'localhost'는 sshd 가 ::1 로 풀 수 있어 IPv4 에서만 받는 프로그램(node 등)에 못 붙음 → 기본은 127.0.0.1
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
RETRY = (3, 5, 10, 20, 30, 60)   # 다시 연결 대기(초), 실패할수록 길게


def _port(v, what):
    try:
        p = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} 포트는 숫자로 넣어 주세요") from None
    if not 1 <= p <= 65535:
        raise ValueError(f"{what} 포트는 1~65535 사이여야 해요")
    return p


def _listening(pid, port):
    """ssh(pid)가 127.0.0.1:port 에서 받고 있는지 = 인증까지 끝나 터널이 실제로 열렸는지."""
    # Windows OpenSSH 의 받는 소켓은 상태가 LISTEN 이 아니라 NONE/CLOSE 등으로 보여서, 상대 주소 없는 소켓이 그 포트를 쥐었는지로 판단
    try:
        import psutil
        return any(c.laddr and c.laddr.port == port and not c.raddr
                   for c in psutil.Process(pid).net_connections(kind="tcp"))
    except Exception:
        return None


def _port_free(port):
    import socket
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


# ---------- 원격 PC에서 지금 열려 있는 포트 (터널 탭의 '열린 포트' 목록) ----------
_PS_PORTS = r"""$p=@{}; Get-Process | ForEach-Object { $p[$_.Id]=$_.ProcessName }
Get-NetTCPConnection -State Listen | ForEach-Object { "$($_.LocalAddress)|$($_.LocalPort)|$($p[[int]$_.OwningProcess])" }"""
# Windows 기본 서비스·원격 접속용 포트 (웹 화면이 아님)
_SYSTEM = {"system", "svchost", "lsass", "wininit", "services", "spoolsv", "sshd", "vmms", "jhi_service", "idle",
           "systemd", "systemd-resolved", "sshd:", "cupsd", "rpcbind", "chronyd", "avahi-daemon"}
_WEB = re.compile(r"node|python|java|docker|wslrelay|nginx|httpd|apache|dotnet|php|ruby|deno|bun|uvicorn|gunicorn|"
                  r"caddy|traefik|code|jupyter|grafana|postgres|redis|mongo|go$", re.I)
_LOCAL_ADDRS = {"127.0.0.1", "::1", "0.0.0.0", "::", "*", "[::]", "[::1]"}


def _parse_ss(text):
    """`ss -ltnpH` 줄 → (주소, 포트, 프로세스)."""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or ":" not in parts[3]:
            continue
        addr, _, port = parts[3].rpartition(":")
        m = re.search(r'users:\(\("([^"]+)"', line)
        yield addr.split("%")[0], port, m.group(1) if m else ""


def remote_ports(target, os_name):
    """원격 PC가 localhost 로 받을 수 있는 TCP 포트 목록 [{port, proc, web}] (SSH 한 번, 수 초)."""
    import base64
    from machines import ssh
    if os_name == "windows":
        enc = base64.b64encode(_PS_PORTS.encode("utf-16-le")).decode("ascii")
        rc, out, err = ssh(target, f"powershell -NoProfile -NonInteractive -EncodedCommand {enc}", timeout=45)
        rows = (l.strip().split("|") for l in out.splitlines() if l.count("|") == 2)
    else:
        rc, out, err = ssh(target, "ss -ltnpH 2>/dev/null", timeout=30)
        rows = _parse_ss(out)
    found = {}
    for addr, port, proc in rows:
        if addr not in _LOCAL_ADDRS or not port.isdigit():
            continue   # 특정 네트워크 주소에만 묶인 포트는 원격의 localhost 로 못 엶
        port = int(port)
        if proc.lower() in _SYSTEM or (port >= 49152 and not _WEB.search(proc)):
            continue   # 기본 서비스, 임시 포트 대역의 잡다한 도우미 프로그램
        cur = found.setdefault(port, {"port": port, "proc": proc, "web": False, "v4": False})
        cur["v4"] = cur["v4"] or ":" not in addr.strip("[]") or addr == "*"
        if proc and (not cur["proc"] or cur["proc"] == "wslrelay"):
            cur["proc"] = proc   # WSL 전달(wslrelay)보다 실제 프로그램 이름(docker 등)을 보여 줌
        cur["web"] = cur["web"] or bool(_WEB.search(proc))
    if not found and rc != 0:
        raise RuntimeError((err.strip().splitlines() or [f"exit {rc}"])[-1][:200])
    return sorted(found.values(), key=lambda x: (not x["web"], x["port"]))


def guess_scheme(port):
    return "https" if port in (443, 8443, 9443) or str(port).endswith("443") else "http"


class Tunnels:
    def __init__(self, store, targets_fn):
        self.store = store
        self.targets_fn = targets_fn   # machine id -> SSH 대상 목록 (지금 연결된 주소가 앞)
        self.lock = threading.RLock()
        self.mem = []                  # DB가 없을 때의 목록
        self.run = {}                  # tunnel id -> {proc, target, err, tries, next_try, want}
        for r in self._rows():         # 지난번에 켜 둔 터널: 연결은 _loop 가 (포트가 막혀 있으면 오류 표시 후 재시도)
            if r.get("on"):
                self.run[r["id"]] = self._new_run()
        threading.Thread(target=self._loop, daemon=True, name="tunnels").start()

    @staticmethod
    def _new_run():
        return {"want": True, "proc": None, "target": None, "err": None, "tries": 0, "next_try": 0, "started": 0}

    def _set_on(self, tid, on):
        with self.lock:
            rows = self._rows()
            for r in rows:
                if r["id"] == tid:
                    r["on"] = on
            self._save(rows)

    # ---------- 목록 ----------
    def _rows(self):
        return list(self.store.get("tunnels.list", []) or []) if self.store is not None else list(self.mem)

    def _save(self, rows):
        if self.store is not None:
            self.store.set("tunnels.list", rows)
        else:
            self.mem = rows

    def clean(self, body):
        mid = str(body.get("machine") or "").strip()
        if not mid:
            raise ValueError("PC를 골라 주세요")
        remote = _port(body.get("remote") or body.get("local"), "원격")
        local = _port(body["local"], "이 PC") if body.get("local") else None   # 없으면 add()가 빈 포트를 고름
        host = str(body.get("host") or "127.0.0.1").strip()
        if not _HOST_RE.match(host):
            raise ValueError(f"원격 주소 형식이 올바르지 않아요: {host!r}")
        name = str(body.get("name") or "").strip()[:40] or f"포트 {remote}"
        scheme = "https" if body.get("scheme") == "https" else "http"
        t = {"machine": mid, "name": name, "local": local, "remote": remote, "host": host, "scheme": scheme}
        if body.get("temp"):
            t["temp"] = True   # '열린 포트'에서 바로 연 것: 끄면 목록에서 사라짐
        return t

    def _pick_local(self, remote, used):
        """원격과 같은 번호 → +10000 → 운영체제가 주는 빈 포트."""
        for p in (remote, remote + 10000):
            if p <= 65535 and p not in used and _port_free(p):
                return p
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def add(self, body):
        t = self.clean(body)
        with self.lock:
            rows = self._rows()
            used = {r["local"] for r in rows}
            if t["local"] is None:
                t["local"] = self._pick_local(t["remote"], used)
            elif t["local"] in used:
                raise ValueError(f"이 PC의 {t['local']} 포트는 이미 다른 터널이 써요")
            t["id"] = secrets.token_hex(4)
            rows.append(t)
            self._save(rows)
        return t

    def quick(self, mid, remote, name="", v4=True):
        """열린 포트 목록에서 '열기': 같은 원격 포트 터널이 있으면 그걸 켜고, 없으면 임시 터널을 만들어 켬."""
        remote = _port(remote, "원격")
        r = next((x for x in self._rows() if x["machine"] == mid and x["remote"] == remote and x["host"] in LOCAL_HOSTS), None)
        if not r:
            r = self.add({"machine": mid, "remote": remote, "name": name or f"포트 {remote}", "host": "127.0.0.1" if v4 else "::1",
                          "scheme": guess_scheme(remote), "temp": True})
        try:
            self.start(r["id"])
        except ValueError:
            if r.get("temp"):
                self._drop(r["id"])
            raise
        return r

    def _drop(self, tid):
        with self.lock:
            rows = self._rows()
            if not any(r["id"] == tid for r in rows):
                raise KeyError(tid)
            self._save([r for r in rows if r["id"] != tid])

    def remove(self, tid):
        self._halt(tid)
        self._drop(tid)

    def remove_machine(self, mid):
        for r in self._rows():
            if r["machine"] == mid:
                self.remove(r["id"])

    def list(self, mid=None):
        out = []
        with self.lock:
            for r in self._rows():
                if mid and r["machine"] != mid:
                    continue
                st = self.run.get(r["id"])
                out.append({**r, **self._status(r, st)})
        return out

    def _status(self, r, st):
        if not st or not st["want"]:
            return {"state": "off", "error": None, "target": None}
        p = st["proc"]
        if p and p.poll() is None:
            up = _listening(p.pid, r["local"])
            if up is None:   # psutil 없음: 몇 초 살아 있으면 열린 것으로 봄
                up = time.time() - st["started"] > 4
            return {"state": "on" if up else "connecting", "error": None, "target": st["target"]}
        if not p and not st["err"]:   # 앱 시작 직후 자동 연결 대기
            return {"state": "connecting", "error": None, "target": None}
        return {"state": "retry", "error": st["err"], "target": st["target"],
                "retry_in": max(0, round(st["next_try"] - time.time()))}

    # ---------- 켜기·끄기 ----------
    def start(self, tid):
        with self.lock:
            r = next((x for x in self._rows() if x["id"] == tid), None)
            if not r:
                raise KeyError(tid)
            st = self.run.get(tid)
            if st and st["want"]:
                return
            if not _port_free(r["local"]):
                raise ValueError(f"이 PC의 {r['local']} 포트를 이미 다른 프로그램이 쓰고 있어요")
            self.run[tid] = self._new_run()
            self._spawn(r, self.run[tid])
        self._set_on(tid, True)

    def keep(self, tid):
        """임시 터널을 저장된 터널로 (끄더라도 목록에 남음)."""
        with self.lock:
            rows = self._rows()
            r = next((x for x in rows if x["id"] == tid), None)
            if not r:
                raise KeyError(tid)
            r.pop("temp", None)
            self._save(rows)

    def _halt(self, tid):
        with self.lock:
            st = self.run.pop(tid, None)
        if st:
            self._kill(st["proc"])

    def stop(self, tid):
        """사용자가 끔: 다음에 앱을 켜도 꺼진 채로. 임시 터널은 목록에서 지움."""
        self._halt(tid)
        if any(r["id"] == tid and r.get("temp") for r in self._rows()):
            self._drop(tid)
        else:
            self._set_on(tid, False)

    def stop_all(self):
        """앱 종료: 연결만 닫고 켜짐 여부는 그대로 (다음 실행 때 다시 연결)."""
        with self.lock:
            sts, self.run = list(self.run.values()), {}
        for st in sts:
            self._kill(st["proc"])

    @staticmethod
    def _kill(p):
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                p.kill()

    def _spawn(self, r, st):
        targets = self.targets_fn(r["machine"]) or []
        if not targets:
            st.update(proc=None, err="이 PC가 목록에 없어요", next_try=time.time() + 30)
            return
        target = targets[st["tries"] % len(targets)]   # 실패할 때마다 다음 주소(대체 주소)로
        args = ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=15",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                "-L", f"127.0.0.1:{r['local']}:{'[::1]' if r['host'] == '::1' else r['host']}:{r['remote']}", "--", target]
        p = procjob.popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        st.update(proc=p, target=target, started=time.time(), err=None)
        threading.Thread(target=self._read_err, args=(p, st), daemon=True).start()

    @staticmethod
    def _read_err(p, st):
        lines = []
        for raw in p.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                lines = (lines + [line])[-3:]
        p.wait()
        if st.get("proc") in (p, None):   # 그사이 다시 연결했으면 새 연결의 상태를 덮지 않음
            st["err"] = " / ".join(lines)[:300] or f"연결 끊김 (exit {p.returncode})"

    def _loop(self):
        while True:
            time.sleep(2)
            with self.lock:
                rows = {r["id"]: r for r in self._rows()}
                for tid, st in list(self.run.items()):
                    r = rows.get(tid)
                    p = st["proc"]
                    if not r:
                        self._kill(p)
                        del self.run[tid]
                        continue
                    if p and p.poll() is None:
                        if time.time() - st["started"] > 30:
                            st["tries"] = 0   # 오래 버티면 다음 끊김은 처음부터
                        continue
                    if p is not None:   # 방금 끊김 → 대기 후 다시
                        st["proc"] = None
                        st["next_try"] = time.time() + RETRY[min(st["tries"], len(RETRY) - 1)]
                        st["tries"] += 1
                    elif time.time() >= st["next_try"]:
                        self._spawn(r, st)
