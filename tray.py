"""RuiBotte Deck 트레이 앱.

바탕화면/시작 메뉴의 "RuiBotte Deck" 아이콘이 이 파일을 pythonw로 실행한다 (콘솔 창 없음).
- 대시보드 서버(app.py)를 이 프로세스 안에서 실행하고, 작업 표시줄 트레이에 아이콘을 띄운다.
- 이미 실행 중이면 서버를 새로 띄우지 않고 대시보드 창만 연다.
- 트레이: 마우스를 올리면 한도 잔량·오늘 사용량, 우클릭 메뉴 맨 위에 한도·리셋·원격 PC 연결·최근 알림
- 트레이 메뉴: 대시보드 열기 / 일반 창으로 열기 / 로그인 시 자동 실행 / 종료
- OS별 처리(창 열기·자동 실행·바로가기·화면 찾기)는 desktop.py
사용법:  pythonw tray.py            (앱 실행)
         python  tray.py --install  (바탕화면·시작 메뉴 바로가기 만들기)
         pythonw tray.py --background (창 없이 트레이만)
"""
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import desktop  # noqa: E402

if sys.stdout is None or sys.stderr is None:   # pythonw·설치판 exe: 출력이 없으므로 로그 파일로
    _log = open(desktop.DATA / "server.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

CFG = desktop.load_config()
PORT = CFG["port"]
URL = f"http://localhost:{PORT}"


# ---------- 서버 ----------
def server_up(timeout=1.0):
    try:
        with urllib.request.urlopen(URL + "/api/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def port_open(timeout=0.5):
    """서버가 바빠 응답이 늦어도 포트가 열려 있으면 '이미 실행 중'으로 본다."""
    import socket
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=timeout).close()
        return True
    except OSError:
        return False


def run_server():
    """이전 실행이 아직 끝나는 중이라 포트가 안 비었으면 최대 60초 동안 다시 시도."""
    import uvicorn
    import app as dashboard
    for _ in range(30):
        try:
            uvicorn.Server(uvicorn.Config(dashboard.app, host=CFG["bind"], port=PORT, log_level="warning")).run()
            return   # 정상 종료
        except SystemExit:   # 포트 사용 중이면 uvicorn이 SystemExit → 잠시 뒤 다시
            time.sleep(2)


def wait_server(sec=30):
    end = time.time() + sec
    while time.time() < end:
        if server_up():
            return True
        time.sleep(0.5)
    return False


# ---------- 대시보드 창 ----------
def monitor_setting():
    """설정의 '표시 모니터' (같은 프로세스의 DB, 아니면 실행 중인 서버에 물어봄). 기본 auto."""
    try:
        if "app" in sys.modules and sys.modules["app"].STORE:
            return sys.modules["app"].STORE.get("display.monitor") or "auto"
        with urllib.request.urlopen(URL + "/api/settings", timeout=3) as r:
            return json.load(r).get("display.monitor") or "auto"
    except Exception:
        return "auto"


def open_window(fullscreen=True):
    desktop.open_window(URL, monitor_setting(), fullscreen)


# ---------- 트레이 ----------
def run_tray():
    import pystray
    from PIL import Image

    def quit_app(icon, _item):
        """누르는 즉시 아이콘을 숨기고, 정리는 뒤에서. 무엇이 멈춰도 6초 뒤에는 반드시 종료."""
        threading.Timer(6, lambda: os._exit(0)).start()
        try:
            icon.visible = False
        except Exception:
            pass

        def cleanup():
            if "app" in sys.modules:   # 같은 프로세스에서 서버를 돌렸으면 원격 연결 끊기·기록 저장
                try:
                    sys.modules["app"].shutdown()
                except Exception:
                    pass
            desktop.close_windows(quick=True)
            os._exit(0)   # 서버 스레드까지 함께 종료

        threading.Thread(target=cleanup, daemon=True).start()
        icon.stop()

    def bg(fn, *args):
        return lambda *_: threading.Thread(target=fn, args=args, daemon=True).start()

    info = {"lines": []}

    def info_items():
        # 우클릭 메뉴 맨 위: 한도·오늘 사용량·원격 PC 연결·최근 알림 (누를 수 없는 정보 줄)
        if not info["lines"]:
            return
        for ln in info["lines"]:
            yield pystray.MenuItem(ln, None, enabled=False)
        yield pystray.Menu.SEPARATOR

    menu = pystray.Menu(
        lambda: (*info_items(),
        pystray.MenuItem("대시보드 열기 (전체 화면)", bg(open_window), default=True),
        pystray.MenuItem("일반 창으로 열기", bg(open_window, False)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("로그인할 때 자동 실행", lambda *_: desktop.set_autostart(not desktop.autostart_on()),
                         checked=lambda _i: desktop.autostart_on()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("종료", quit_app)),
    )
    tray_img = {"path": desktop.tray_icon_path()}
    icon = pystray.Icon("pc-dashboard", Image.open(tray_img["path"]), desktop.APP_NAME, menu)
    desktop.NOTIFY_FN = lambda title, message: icon.notify(message, title)   # 대시보드 알림 → Windows 알림

    def refresh_loop():
        """15초마다 마우스 올렸을 때 보이는 요약(툴팁)과 우클릭 메뉴의 정보 줄을 갱신."""
        while True:
            time.sleep(15 if info["lines"] else 5)
            try:
                if "app" not in sys.modules:   # 서버를 다른 프로세스가 돌리는 경우는 생략
                    continue
                p = desktop.tray_icon_path()   # Windows 밝은/어두운 테마가 바뀌면 아이콘도 바꿈
                if p != tray_img["path"]:
                    tray_img["path"] = p
                    icon.icon = Image.open(p)
                sm = sys.modules["app"].tray_summary()
                info["lines"] = sm["lines"]
                icon.title = sm["tip"]
                icon.update_menu()
            except Exception as e:
                print("tray summary:", repr(e)[:200])

    threading.Thread(target=refresh_loop, daemon=True, name="tray-summary").start()
    icon.run()


def main():
    if "--install" in sys.argv:
        made = desktop.install_shortcuts()
        print("shortcuts created:", *made) if made else print("이 OS에서는 바로가기를 만들지 않습니다. python3 tray.py 로 실행하세요.")
        return
    if server_up() or (port_open() and wait_server(15)):
        # 이미 실행 중(트레이 아이콘이 이미 있음): 창만 다시 연다
        open_window()
        return
    threading.Thread(target=run_server, daemon=True, name="server").start()
    if wait_server(90) and "--background" not in sys.argv:   # --background: 창 없이 트레이만 (시험·조용한 시작)
        threading.Thread(target=open_window, daemon=True).start()
    run_tray()


if __name__ == "__main__":
    main()
