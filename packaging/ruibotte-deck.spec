# PyInstaller 설정: tray.py → RuiBotteDeck.exe (폴더형, 콘솔 없음). 빌드는 packaging/build.ps1
# 원격 PC 에이전트는 collectors/*.py 원본을 SSH로 보내므로 .py 파일을 자료로도 함께 넣는다.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent

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
          console=False, version=str(ROOT / "packaging" / "version.txt"))
coll = COLLECT(exe, a.binaries, a.datas, name="RuiBotteDeck")
