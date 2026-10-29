# RuiBotte Deck 설치 파일 빌드: 화면 빌드 → PyInstaller(exe) → Inno Setup(설치 프로그램)
# 필요: Node.js, Python 3.12+ (pip install -r requirements.txt pyinstaller), Inno Setup 6 (winget install JRSoftware.InnoSetup)
# 결과: dist\RuiBotteDeck\RuiBotteDeck.exe, dist\RuiBotteDeck-Setup-<버전>.exe
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
# 버전: desktop.py 의 APP_VERSION (한 곳에서만 관리)
$ver = [regex]::Match((Get-Content desktop.py -Raw), 'APP_VERSION = "([0-9.]+)"').Groups[1].Value
if (-not $ver) { throw 'APP_VERSION not found in desktop.py' }
Write-Host "version $ver"
# CI(GitHub Actions)는 setup-python 이 고른 python, 이 PC는 py 런처 (러너의 py 는 다른 버전을 고를 수 있음)
$py = if ($env:CI -or -not (Get-Command py -ErrorAction SilentlyContinue)) { 'python' } else { 'py' }

Write-Host '[1/3] frontend build'
Push-Location frontend
if (-not (Test-Path node_modules)) { npm ci }
npm run build
if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
Pop-Location

Write-Host '[2/3] PyInstaller'
& $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\ruibotte-deck.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }

Write-Host '[3/3] Inno Setup'
$iscc = @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 (ISCC.exe) not found: winget install JRSoftware.InnoSetup' }
# 백신이 막 만든 exe를 검사하는 동안 'EndUpdateResource failed'가 가끔 남 → 잠시 뒤 다시 (최대 3번)
for ($i = 1; $i -le 3; $i++) {
  & $iscc "/DAppVersion=$ver" packaging\installer.iss
  if ($LASTEXITCODE -eq 0) { break }
  if ($i -eq 3) { throw "ISCC failed ($LASTEXITCODE)" }
  Start-Sleep 5
}
Write-Host 'done:' (Get-ChildItem dist\RuiBotteDeck-Setup-*.exe | Select-Object -Last 1).FullName
