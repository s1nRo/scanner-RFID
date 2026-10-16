@echo off
REM Builds dist\rfid.exe (console menu, like rfid.cmd) and
REM dist\RFID-Scanner.exe (window, like rfid-gui.cmd).
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

REM The console exe never opens the window, so PyQt5 stays out of it.
"%PY%" -m PyInstaller --noconfirm --clean --onefile --console --name rfid --exclude-module PyQt5 ^
  --workpath build\pyinstaller --specpath build\pyinstaller packaging\rfid_console.py
if errorlevel 1 exit /b 1
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name RFID-Scanner ^
  --workpath build\pyinstaller --specpath build\pyinstaller packaging\rfid_gui.py
if errorlevel 1 exit /b 1

echo.
echo Done: dist\rfid.exe and dist\RFID-Scanner.exe
echo Put them into the program folder, next to data\ and tables\.
