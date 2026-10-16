@echo off
REM Builds dist\RFID-Scanner.exe (window, like rfid-gui.cmd).
REM There is no console exe on purpose: console commands go through rfid.cmd.
REM Uses the regular Windows Python (py launcher), not the MSYS venv\:
REM PyInstaller has no wheels for MSYS Python. Its own venv is build-venv\.
REM CI sets BUILD_PY to its own python and skips the venv.
REM
REM Keep this file ASCII-only: cmd.exe reads .cmd in the OEM codepage.
setlocal
cd /d "%~dp0"

set "PY=%BUILD_PY%"
if defined PY goto install
if not exist "build-venv\Scripts\python.exe" (
  echo Creating build-venv...
  py -3 -m venv build-venv
  if errorlevel 1 exit /b 1
)
set "PY=build-venv\Scripts\python.exe"

:install
"%PY%" -m pip install --upgrade --quiet pip
"%PY%" -m pip install --upgrade --quiet ".[gui]" "pyinstaller>=6.10"
if errorlevel 1 exit /b 1
REM The version number rarely changes between builds, and then pip keeps the
REM previously installed copy: the exe would pack stale code. Always reinstall.
"%PY%" -m pip install --quiet --force-reinstall --no-deps .
if errorlevel 1 exit /b 1

"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name RFID-Scanner ^
  --workpath build\pyinstaller --specpath build\pyinstaller packaging\rfid_gui.py
if errorlevel 1 exit /b 1

echo.
echo Done: dist\RFID-Scanner.exe
echo Put it into the program folder, next to data\ and tables\.
