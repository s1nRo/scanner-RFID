@echo off
REM Starts the PyQt5 interface. Install first with install.cmd.
REM Keep this file ASCII-only: cmd.exe may read it in the OEM codepage.
setlocal
set "RFID_HOME=%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%~dp0venv\bin\python.exe"
if not exist "%PY%" goto not_installed
"%PY%" -P -c "import rfid" 2>nul
if errorlevel 1 goto not_installed
"%PY%" -P -m rfid gui %*
exit /b %errorlevel%

:not_installed
echo The program is not installed yet. Run install.cmd from this folder first.
exit /b 1
