# RuiBotte Deck 설치 파일 빌드: 화면 빌드 → PyInstaller(exe) → Inno Setup(설치 프로그램)
# 필요: Node.js, Python 3.12+ (pip install -r requirements.txt pyinstaller), Inno Setup 6 (winget install JRSoftware.InnoSetup)
# 결과: dist\RuiBotteDeck\RuiBotteDeck.exe, dist\RuiBotteDeck-Setup-<버전>.exe
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

Write-Host '[1/3] frontend build'
Push-Location frontend
if (-not (Test-Path node_modules)) { npm ci }
npm run build
Pop-Location

Write-Host '[2/3] PyInstaller'
py -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\ruibotte-deck.spec

Write-Host '[3/3] Inno Setup'
$iscc = @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 (ISCC.exe) not found: winget install JRSoftware.InnoSetup' }
& $iscc packaging\installer.iss
Write-Host 'done:' (Get-ChildItem dist\RuiBotteDeck-Setup-*.exe | Select-Object -Last 1).FullName
