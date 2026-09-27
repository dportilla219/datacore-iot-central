@echo off
REM DataCore UNAB - inicia toda la flota por 4 horas (doble clic)
cd /d "%~dp0"
powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0iniciar_todo.ps1" -Horas 4 -Nota Captura
