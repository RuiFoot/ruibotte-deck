# PyInstaller 설정: tray.py → RuiBotteDeck.exe (폴더형, 콘솔 없음). 빌드는 packaging/build.ps1
# 원격 PC 에이전트는 collectors/*.py 원본을 SSH로 보내므로 .py 파일을 자료로도 함께 넣는다.
import re
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
# 버전은 desktop.py 의 APP_VERSION 한 곳에서만 관리 → exe 파일 정보에 넣음
VERSION = re.search(r'APP_VERSION = "([0-9.]+)"', (ROOT / "desktop.py").read_text(encoding="utf-8")).group(1)
_v = tuple(int(x) for x in (VERSION.split(".") + ["0"] * 4)[:4])
_vfile = Path(workpath) / "version_info.txt"
_vfile.parent.mkdir(parents=True, exist_ok=True)
_vfile.write_text((ROOT / "packaging" / "version_template.txt").read_text(encoding="utf-8")
                  .replace("{VER_TUPLE}", repr(_v)).replace("{VER}", VERSION), encoding="utf-8")

datas = [
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "probes"), "probes"),
    (str(ROOT / "find-screen.ps1"), "."),
] + [(str(p), "collectors") for p in (ROOT / "collectors").glob("*.py")]

hidden = collect_submodules("uvicorn") + ["pystray._win32", "app"]

a = Analysis([str(ROOT / "tray.py")], pathex=[str(ROOT)], datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "matplotlib", "numpy", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="RuiBotteDeck", icon=str(ROOT / "assets" / "icon.ico"),
          console=False, version=str(_vfile))
coll = COLLECT(exe, a.binaries, a.datas, name="RuiBotteDeck")
