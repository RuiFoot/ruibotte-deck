@echo off
rem Stop the dashboard (tray app / server) and its dedicated Edge window
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -match '^pythonw?\.exe$' -and ($_.CommandLine -like '*pc-dashboard*app.py*' -or $_.CommandLine -like '*pc-dashboard*tray.py*')) -or ($_.Name -eq 'msedge.exe' -and $_.CommandLine -like '*pc-dashboard-edge*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo stopped.
