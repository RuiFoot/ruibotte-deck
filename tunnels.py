"""SSH 터널: 원격 PC의 포트를 이 PC의 localhost 로 열고 닫기 (`ssh -N -L`).

목록은 DB 설정 `tunnels.list`(DB가 없으면 메모리)에 저장, 켜고 끈 상태는 저장하지 않는다(앱을 다시 켜면 모두 꺼짐).
켜 둔 터널이 끊기면(망 전환·원격 재부팅 등) 원격 PC의 현재 연결 주소 → 대체 주소 순으로 다시 연결한다.
ssh 는 procjob 으로 띄우므로 앱이 어떻게 끝나든 함께 종료된다.
"""
import re
import secrets
import subprocess
import threading
import time

from collectors import procjob

_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}$")
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


class Tunnels:
    def __init__(self, store, targets_fn):
        self.store = store
        self.targets_fn = targets_fn   # machine id -> SSH 대상 목록 (지금 연결된 주소가 앞)
        self.lock = threading.RLock()
        self.mem = []                  # DB가 없을 때의 목록
        self.run = {}                  # tunnel id -> {proc, target, err, tries, next_try, want}
        threading.Thread(target=self._loop, daemon=True, name="tunnels").start()

    # ---------- 목록 ----------
    def _rows(self):
        return list(self.store.get("tunnels.list", []) or []) if self.store else list(self.mem)

    def _save(self, rows):
        if self.store:
            self.store.set("tunnels.list", rows)
        else:
            self.mem = rows

    def clean(self, body):
        mid = str(body.get("machine") or "").strip()
        if not mid:
            raise ValueError("PC를 골라 주세요")
        local = _port(body.get("local"), "이 PC")
        remote = _port(body.get("remote") or local, "원격")
        host = str(body.get("host") or "localhost").strip()
        if not _HOST_RE.match(host):
            raise ValueError(f"원격 주소 형식이 올바르지 않아요: {host!r}")
        name = str(body.get("name") or "").strip()[:40] or f"포트 {remote}"
        scheme = "https" if body.get("scheme") == "https" else "http"
        return {"machine": mid, "name": name, "local": local, "remote": remote, "host": host, "scheme": scheme}

    def add(self, body):
        t = self.clean(body)
        with self.lock:
            rows = self._rows()
            if any(r["local"] == t["local"] for r in rows):
                raise ValueError(f"이 PC의 {t['local']} 포트는 이미 다른 터널이 써요")
            t["id"] = secrets.token_hex(4)
            rows.append(t)
            self._save(rows)
        return t

    def remove(self, tid):
        self.stop(tid)
        with self.lock:
            rows = self._rows()
            if not any(r["id"] == tid for r in rows):
                raise KeyError(tid)
            self._save([r for r in rows if r["id"] != tid])

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
            self.run[tid] = {"want": True, "proc": None, "target": None, "err": None, "tries": 0,
                             "next_try": 0, "started": 0}
            self._spawn(r, self.run[tid])

    def stop(self, tid):
        with self.lock:
            st = self.run.pop(tid, None)
        if st:
            self._kill(st["proc"])

    def stop_all(self):
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
                "-L", f"127.0.0.1:{r['local']}:{r['host']}:{r['remote']}", "--", target]
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
