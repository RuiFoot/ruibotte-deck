"""앱 안 업데이트: GitHub Releases의 최신 버전 확인 → 설치 파일 받기(크기·SHA-256 확인) → 조용히 설치 → 다시 실행.

- 확인은 6시간마다(또는 화면에서 '확인'), GitHub 공개 API만 부름 (사용자 정보는 보내지 않음).
- 설치는 설치판(exe)에서만. 소스로 실행 중이면 git pull 안내.
- 설치 프로그램은 앱의 자식 프로세스가 아니게 띄움(cmd start): 설치 프로그램이 실행 중인 앱을 끝낼 때 같이 죽지 않도록.
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import desktop

LATEST = "https://api.github.com/repos/RuiFoot/ruibotte-deck/releases/latest"
CHECK_EVERY = 6 * 3600
ASSET = re.compile(r"^RuiBotteDeck-Setup-[0-9.]+\.exe$")


def ver_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4]) or (0,)


class Updater:
    def __init__(self):
        self.lock = threading.Lock()
        self.info = None          # 마지막 확인 결과
        self.checked = 0.0
        self.state = {"phase": "idle"}   # idle | downloading | installing | error
        self.notified = None      # 알림을 보낸 버전

    def _get(self, url, timeout=15):
        req = urllib.request.Request(url, headers={"User-Agent": f"RuiBotteDeck/{desktop.APP_VERSION}",
                                                   "Accept": "application/vnd.github+json"})
        return urllib.request.urlopen(req, timeout=timeout)

    def check(self, force=False):
        """최신 릴리스 정보. 6시간 캐시. 실패해도 예외 없이 error 를 담아 돌려줌."""
        with self.lock:
            if not force and self.info and time.time() - self.checked < CHECK_EVERY:
                return self.info
        cur = desktop.APP_VERSION
        try:
            with self._get(LATEST) as r:
                d = json.load(r)
            tag = d.get("tag_name") or ""
            asset = next((a for a in d.get("assets") or [] if ASSET.match(a.get("name") or "")), None)
            info = {"current": cur, "latest": tag.lstrip("v"), "newer": ver_tuple(tag) > ver_tuple(cur),
                    "notes": (d.get("body") or "")[:3000], "page": d.get("html_url"), "published": d.get("published_at"),
                    "asset": {"name": asset["name"], "size": asset.get("size"), "url": asset["browser_download_url"],
                              "digest": asset.get("digest")} if asset else None, "error": None}
        except Exception as e:
            info = {"current": cur, "latest": None, "newer": False, "error": f"확인 실패: {type(e).__name__}"}
        with self.lock:
            self.info, self.checked = info, time.time()
        return info

    def status(self):
        return {**(self.info or {"current": desktop.APP_VERSION}), "state": dict(self.state),
                "installed": desktop.FROZEN, "checked_at": self.checked or None}

    def install(self):
        """백그라운드로 받아서 설치 시작. 진행 상황은 status()['state']."""
        if not desktop.FROZEN:
            raise ValueError("소스로 실행 중이에요. git pull 로 받은 뒤 다시 실행해 주세요.")
        info = self.info if self.info and self.info.get("newer") else self.check(force=True)
        if not info.get("newer") or not info.get("asset"):
            raise ValueError("설치할 새 버전이 없어요")
        if self.state.get("phase") in ("downloading", "installing"):
            return
        self.state = {"phase": "downloading", "pct": 0, "version": info["latest"]}
        threading.Thread(target=self._run, args=(info,), daemon=True, name="updater").start()

    def _run(self, info):
        a = info["asset"]
        try:
            d = Path(tempfile.gettempdir()) / "RuiBotteDeck-update"
            d.mkdir(exist_ok=True)
            path = d / a["name"]
            h, got = hashlib.sha256(), 0
            with self._get(a["url"], timeout=60) as r, open(path, "wb") as f:
                total = int(r.headers.get("Content-Length") or a.get("size") or 0)
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    if total:
                        self.state["pct"] = round(100 * got / total)
            if a.get("size") and got != a["size"]:
                raise ValueError(f"받은 크기가 달라요 ({got} / {a['size']})")
            dg = a.get("digest") or ""
            if dg.startswith("sha256:") and h.hexdigest() != dg.split(":", 1)[1]:
                raise ValueError("파일 확인(SHA-256) 실패 — 설치하지 않았어요")
            self.state = {"phase": "installing", "version": info["latest"]}
            print(time.strftime("%m-%d %H:%M:%S"), f"업데이트: {info['latest']} 설치 시작", flush=True)
            # cmd start 로 띄워 앱의 자식이 아니게 함 → 설치 프로그램이 앱을 끝내도 설치는 계속, 끝나면 앱을 다시 실행(/relaunch=1)
            subprocess.Popen(["cmd", "/c", "start", "", str(path), "/SILENT", "/NORESTART", "/relaunch=1"],
                             creationflags=subprocess.CREATE_NO_WINDOW,
                             close_fds=True, cwd=str(d))
        except Exception as e:
            self.state = {"phase": "error", "error": str(e)[:200] or type(e).__name__}

    def background(self, notify):
        """6시간마다 확인, 새 버전이면 한 번 알림."""
        time.sleep(60)
        while True:
            info = self.check()
            if info.get("newer") and info["latest"] != self.notified:
                self.notified = info["latest"]
                try:
                    notify(f"{desktop.APP_NAME} {info['latest']} 업데이트", "설정 > 정보에서 바로 설치할 수 있어요")
                except Exception:
                    pass
            time.sleep(CHECK_EVERY + 5)


UPDATER = Updater()
