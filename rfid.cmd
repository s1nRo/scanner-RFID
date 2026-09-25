@echo off
REM Starts the program: rfid.cmd [command] [options]. Without a command - menu.
REM Install first with install.cmd.
REM
REM Works with either venv layout: Scripts (standard Windows Python) or bin
REM (venv built by an MSYS2/MinGW Python, as on the original dev machine).
REM
REM Keep this file ASCII-only: cmd.exe reads .cmd in the OEM codepage and
REM mangles UTF-8 comments into bogus commands.
setlocal
REM The working folder (data\ and tables\) is where this file lives,
REM no matter which folder the command is started from.
set "RFID_HOME=%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%~dp0venv\bin\python.exe"
if not exist "%PY%" goto not_installed
"%PY%" -c "import rfid" 2>nul
if errorlevel 1 goto not_installed
"%PY%" -m rfid %*
exit /b %errorlevel%

:not_installed
echo The program is not installed yet. Run install.cmd from this folder first.
exit /b 1
