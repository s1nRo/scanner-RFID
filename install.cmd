@echo off
REM One-time setup: creates venv\ next to this file and installs the program.
REM Run it again after unpacking a newer version to update.
REM
REM Keep this file ASCII-only: cmd.exe reads .cmd in the OEM codepage and
REM mangles UTF-8 comments into bogus commands.
setlocal
cd /d "%~dp0"

set "PY="
if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
if not defined PY if exist "venv\bin\python.exe" set "PY=venv\bin\python.exe"

if not defined PY (
  where py >nul 2>nul
  if errorlevel 1 (
    echo Python not found. Install Python 3.11 or newer from python.org
    echo and tick "Add python.exe to PATH" / "py launcher", then run this again.
    exit /b 1
  )
  py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))"
  if errorlevel 1 (
    echo Python 3.11 or newer is required. Installed versions:
    py -0
    exit /b 1
  )
  echo Creating venv...
  py -3 -m venv venv
  if errorlevel 1 exit /b 1
  set "PY=venv\Scripts\python.exe"
)

echo Installing the program and its dependencies...
"%PY%" -m pip install --upgrade --quiet pip
"%PY%" -m pip install --upgrade --quiet .
if errorlevel 1 (
  echo Installation failed, see the messages above.
  exit /b 1
)

REM Working folders. Subject folders inside tables\ are yours to create.
if not exist "data" mkdir "data"
if not exist "tables" mkdir "tables"

echo.
echo Done. Checking the system:
echo.
call "%~dp0rfid.cmd" doctor
exit /b 0
