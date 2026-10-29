"""데스크톱 연동 (OS별): 설정 파일, 화면 목록, 대시보드 창 열기/닫기, 자동 실행, 바로가기.

트레이(tray.py)와 서버의 설정 API(app.py)가 같이 쓴다.
Windows는 실제 환경에서 검증. macOS·Linux 분기는 작성만 했고 실기기 시험은 아직 안 함.
"""
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent      # 소스(개발) 또는 exe 안의 묶음 폴더 — 읽기 전용 자원
FROZEN = bool(getattr(sys, "frozen", False))   # 설치판(PyInstaller exe)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
IS_WIN, IS_MAC = sys.platform == "win32", sys.platform == "darwin"
ICON = ROOT / "assets" / "icon.ico"
APP_NAME = "RuiBotte Deck"
APP_VERSION = "2.2.0"
REPO_URL = "https://github.com/RuiFoot/ruibotte-deck"
LEGACY_NAMES = ("PC Dashboard", "DeskPulse")            # 예전 바로가기 이름 (자동 실행 확인·정리용)


def data_dir():
    """쓰는 파일(config.json·server.log·캐시) 위치: 설치판은 사용자 데이터 폴더(DB와 같은 곳), 개발 중엔 소스 폴더."""
    if not FROZEN:
        return ROOT
    if os.environ.get("PCDASHBOARD_DATA"):
        d = Path(os.environ["PCDASHBOARD_DATA"])
    elif IS_WIN:
        d = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "PCDashboard"
    elif IS_MAC:
        d = Path.home() / "Library" / "Application Support" / "PCDashboard"
    else:
        d = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "pcdashboard"
    d.mkdir(parents=True, exist_ok=True)
    return d


DATA = data_dir()


def taskbar_light():
    """작업 표시줄이 밝은 테마인지 (Windows 설정 '기본 Windows 모드'). 다른 OS는 어두운 것으로 봄."""
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


def tray_icon_path():
    """트레이 아이콘: 밝은 작업 표시줄엔 진회색 맥박선, 어두운 작업 표시줄엔 흰 맥박선 (make_icon.py)."""
    return ROOT / "assets" / ("tray-light.png" if taskbar_light() else "tray-dark.png")


# ---------- 설정 파일 (포트·접속 토큰) ----------
CONFIG_DEFAULTS = {
    "port": 8787,
    "bind": "127.0.0.1",          # 새 설치는 이 PC에서만. 다른 기기(아이패드 등)에서 보려면 "0.0.0.0"
    "local_interval_sec": 2,
    "claude_interval_sec": 3,
}


def load_config():
    """config.json 을 읽음. 없으면 기본값으로 만들고 다른 기기 접속용 토큰을 새로 발급."""
    path = DATA / "config.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        cfg = {**CONFIG_DEFAULTS, "token": secrets.token_urlsafe(9)}
        path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    for k, v in CONFIG_DEFAULTS.items():
        cfg.setdefault(k, v)
    cfg.setdefault("token", "")
    return cfg


def save_config(**changes):
    """config.json 일부 바꾸기 (bind 등 — 서버를 다시 켜야 적용)."""
    path = DATA / "config.json"
    cfg = load_config()
    cfg.update(changes)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg


def lan_addresses():
    """같은 네트워크의 다른 기기가 쓸 이 PC 주소 (사설 IPv4, 가상 어댑터 제외, Wi-Fi·이더넷 먼저)."""
    try:
        import psutil
        stats = psutil.net_if_stats()
        out = []
        for name, addrs in psutil.net_if_addrs().items():
            if not (stats.get(name) and stats[name].isup):
                continue
            if any(k in name.lower() for k in ("vethernet", "wsl", "virtualbox", "vmware", "hyper-v", "loopback", "bluetooth", "docker")):
                continue
            for a in addrs:
                ip = a.address
                if a.family == 2 and not ip.startswith(("127.", "169.254.")):
                    pref = 0 if any(k in name.lower() for k in ("wi-fi", "wifi", "wlan", "ethernet", "이더넷", "무선")) else 1
                    out.append((pref, name, ip))
        return [{"name": n, "ip": ip} for _, n, ip in sorted(out)]
    except Exception:
        return []


def restart_app():
    """앱 다시 시작: 새 인스턴스를 (이 앱의 자식이 아니게) 띄우고 이 프로세스는 끝냄. 새 인스턴스는 포트가 빌 때까지 기다림."""
    exe, args, cwd = _launch()
    cmd = ["cmd", "/c", "start", "", exe] + ([args.strip('"')] if args else []) + ["--restart"] if IS_WIN \
        else [exe] + ([args.strip('"')] if args else []) + ["--restart"]
    subprocess.Popen(cmd, cwd=str(cwd), creationflags=NO_WINDOW, close_fds=True, start_new_session=not IS_WIN)


# ---------- 알림 ----------
NOTIFY_FN = None   # 트레이가 실행 중이면 tray.py 가 아이콘 알림 함수를 넣어 둠


def notify(title, message):
    """Windows 알림(트레이 아이콘). 트레이 없이 서버만 돌 때는 로그에만 남김."""
    title, message = str(title)[:63], str(message)[:250]
    print(time.strftime("%m-%d %H:%M:%S"), "알림:", title, "-", message, flush=True)
    if NOTIFY_FN:
        NOTIFY_FN(title, message)


# ---------- 화면 ----------
def _ps_file(*args, timeout=20):
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", *map(str, args)],
                          capture_output=True, timeout=timeout, creationflags=NO_WINDOW)


def displays():
    """연결된 화면 목록 [{id, name, device, primary, internal, x, y, width, height}] (Windows만, 그 외 빈 목록)."""
    if not IS_WIN:
        return []
    try:
        out = _ps_file(ROOT / "find-screen.ps1", "-List").stdout.decode("utf-8", "replace")
        d = json.loads(out[out.find("["):])
        return d if isinstance(d, list) else [d]
    except Exception:
        return []


def screen_rect(monitor="auto"):
    """대시보드를 띄울 화면 (x, y, w, h). monitor: auto | primary | 하드웨어 ID | \\\\.\\DISPLAYn. 못 찾으면 None."""
    if not IS_WIN:
        return None
    try:
        r = _ps_file(ROOT / "find-screen.ps1", "-Monitor", monitor or "auto", timeout=15)
        x, y, w, h = map(int, r.stdout.decode().split())
        return x, y, w, h
    except Exception:
        return None


# ---------- 대시보드 창 ----------
_PIDS = []   # 이 앱이 띄운 대시보드 창 (종료할 때 바로 닫기용)


def _app_dir():
    if IS_WIN:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    if IS_MAC:
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


BROWSER_PROFILE = _app_dir() / "ruibotte-deck-edge"   # 대시보드 전용 브라우저 프로필 (아이콘 캐시 때문에 이름 변경, 예전: pc-dashboard-edge)


def _browser():
    """앱 창(--app)을 지원하는 브라우저 실행 파일. 없으면 None (기본 브라우저로 열기)."""
    if IS_WIN:
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if base:
                p = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
                if p.exists():
                    return str(p)
        return shutil.which("msedge") or shutil.which("chrome")
    if IS_MAC:
        for app in ("Google Chrome", "Microsoft Edge", "Chromium"):
            p = Path("/Applications") / f"{app}.app" / "Contents" / "MacOS" / app
            if p.exists():
                return str(p)
        return None
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def close_windows(quick=False):
    """대시보드 전용 창만 닫음 (다른 브라우저 창은 그대로). quick 이 아니면 예전 실행이 남긴 창도 찾아서 닫음."""
    for pid in _PIDS:
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=5,
                               creationflags=NO_WINDOW)
            else:
                os.kill(pid, 15)
        except Exception:
            pass
    _PIDS.clear()
    if quick:
        return
    try:
        if IS_WIN:
            ps = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
                  "Where-Object { $_.CommandLine -like '*ruibotte-deck-edge*' -or $_.CommandLine -like '*pc-dashboard-edge*' } | "
                  "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=20,
                           creationflags=NO_WINDOW)
        else:
            subprocess.run(["pkill", "-f", "ruibotte-deck-edge|pc-dashboard-edge"], capture_output=True, timeout=10)
    except Exception:
        pass


def find_window():
    """열려 있는 대시보드 창(전용 앱 창: 제목이 앱 이름 그대로)의 핸들. Windows 전용, 없으면 None."""
    if not IS_WIN:
        return None
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            cls, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 64)
            user32.GetWindowTextW(hwnd, title, 256)
            if cls.value == "Chrome_WidgetWin_1" and title.value == APP_NAME:
                found.append(hwnd)
                return False
        return True
    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def focus_window():
    """대시보드 창이 있으면 (최소화돼 있으면 되살려) 맨 앞으로. 성공하면 True."""
    hwnd = find_window()
    if not hwnd:
        return False
    import ctypes
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    user32.ShowWindow(hwnd, 5)              # SW_SHOW
    if not user32.SetForegroundWindow(hwnd):
        user32.keybd_event(0x12, 0, 0, 0)   # Alt 를 눌렀다 떼면 앞으로 가져오기 제한이 풀림 (Windows 알려진 방법)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetForegroundWindow(hwnd)
    return True


_OPEN_LOCK = __import__("threading").Lock()


def open_window(url, monitor="auto", fullscreen=True, force=False):
    """대시보드 창: 이미 떠 있으면 앞으로 가져오기만, 없으면 전용 앱 창으로 염(전체 화면이면 고른 모니터에 맞춤).
    force(일반 창으로 열기 등)면 닫고 새로 엶. 여러 번 눌러도 창이 하나만 뜨도록 한 번에 하나씩 처리."""
    with _OPEN_LOCK:
        if not force and focus_window():
            return
        _open_window(url, monitor, fullscreen)
        if IS_WIN:   # 창이 실제로 뜰 때까지 기다림 → 그 사이 또 누르면 새 창 대신 앞으로 가져오기
            end = time.time() + 10
            while time.time() < end and not find_window():
                time.sleep(0.25)


def _open_window(url, monitor="auto", fullscreen=True):
    close_windows()
    exe = _browser()
    if not exe:
        import webbrowser
        webbrowser.open(url)
        return
    args = [exe, f"--app={url}", f"--user-data-dir={BROWSER_PROFILE}", "--no-first-run"]
    scr = screen_rect(monitor) if fullscreen else None
    if scr:
        x, y, w, h = scr
        args += [f"--window-position={x},{y}", f"--window-size={w},{h}", "--start-fullscreen"]
    elif fullscreen and not IS_WIN:
        args += ["--start-fullscreen"]
    else:
        args += ["--window-size=1600,1000"]
    _PIDS.append(subprocess.Popen(args, creationflags=NO_WINDOW).pid)


# ---------- 바로가기 / 자동 실행 ----------
def _pythonw():
    if IS_WIN:
        p = Path(sys.executable).with_name("pythonw.exe")
        return str(p if p.exists() else sys.executable)
    return sys.executable


def _launch():
    """(실행 파일, 인자, 작업 폴더): 설치판은 exe 그대로, 개발 중엔 pythonw tray.py"""
    if FROZEN:
        return sys.executable, "", Path(sys.executable).parent
    return _pythonw(), f'\"{ROOT / "tray.py"}\"', ROOT


def _win_shortcut(lnk: Path):
    exe, args, cwd = _launch()
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
          f"$s.TargetPath='{exe}';$s.Arguments='{args}';"
          f"$s.WorkingDirectory='{cwd}';$s.IconLocation='{exe if FROZEN else ICON}';"
          f"$s.Description='{APP_NAME}';$s.Save()")
    lnk.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=20, creationflags=NO_WINDOW)


def _linux_desktop_entry(path: Path, autostart=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[Desktop Entry]\nType=Application\nName=RuiBotte Deck\n"
                    f"Exec=\"{sys.executable}\" \"{ROOT / 'tray.py'}\"\nPath={ROOT}\nIcon={ROOT / 'assets' / 'icon.png'}\n"
                    "Terminal=false\n" + ("X-GNOME-Autostart-enabled=true\n" if autostart else ""), encoding="utf-8")


def _autostart_path():
    if IS_WIN:
        return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / f"{APP_NAME}.lnk"
    if IS_MAC:
        return Path.home() / "Library" / "LaunchAgents" / "com.pcdashboard.tray.plist"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "pc-dashboard.desktop"


def _legacy_autostart():
    return [_autostart_path().with_name(f"{n}.lnk") for n in LEGACY_NAMES] if IS_WIN else []


def autostart_on():
    return _autostart_path().exists() or any(p.exists() for p in _legacy_autostart())


def set_autostart(on):
    """로그인할 때 자동 실행 (Windows: 시작프로그램 바로가기, macOS: LaunchAgent, Linux: autostart .desktop)."""
    p = _autostart_path()
    for old in _legacy_autostart():   # 예전 이름 바로가기는 정리 (켜면 새 이름으로 다시 만듦)
        old.unlink(missing_ok=True)
    if not on:
        p.unlink(missing_ok=True)
        return False
    if IS_WIN:
        _win_shortcut(p)
    elif IS_MAC:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.pcdashboard.tray</string>
  <key>ProgramArguments</key><array><string>{sys.executable}</string><string>{ROOT / 'tray.py'}</string></array>
  <key>WorkingDirectory</key><string>{ROOT}</string>
  <key>RunAtLoad</key><true/>
</dict></plist>
""", encoding="utf-8")
    else:
        _linux_desktop_entry(p, autostart=True)
    return True


def install_shortcuts():
    """바탕화면·시작 메뉴(Windows) / 앱 목록(Linux) 바로가기. 만든 경로 목록을 돌려줌."""
    if IS_WIN:
        made = [Path(os.environ["USERPROFILE"]) / "Desktop" / f"{APP_NAME}.lnk",
                Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"]
        for lnk in made:
            _win_shortcut(lnk)
        return made
    if IS_MAC:
        return []   # macOS: 터미널에서 python3 tray.py 로 실행하거나 자동 실행만 켜서 사용
    p = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "applications" / "pc-dashboard.desktop"
    _linux_desktop_entry(p)
    return [p]
