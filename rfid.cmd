@echo off
REM Wrapper around "python -m rfid".
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
if not exist "%PY%" (
  echo Python not found in venv.
  echo Create it:  py -3 -m venv venv
  echo Then:       venv\Scripts\python.exe -m pip install -r requirements.txt
  exit /b 1
)
"%PY%" -m rfid %*
