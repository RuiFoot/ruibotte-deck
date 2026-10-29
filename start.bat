@echo off
rem Same as the "RuiBotte Deck" desktop icon: runs the tray app (server + dashboard window), no console.
cd /d "%~dp0"
start "" pyw "%~dp0tray.py"
