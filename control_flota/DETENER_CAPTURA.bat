@echo off
REM DataCore UNAB - detiene toda la flota (doble clic)
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "%~dp0detener_todo.ps1"
pause
