@echo off
REM Assembles dist\RFID-Portable: the folder to copy onto a USB flash drive.
REM   RFID-Scanner.exe   the program (build it with build-exe.cmd first)
REM   portable.txt       marker: data\ and tables\ live next to the exe
REM   tables\            empty; data\ is created on first start
REM   driver\<package>   the program installs it itself (admin, once per PC)
REM Driver package: DRIVER_DIR, default driver\CDM_2.12.36.20 (from the
REM release asset ironlogic-driver-*.zip). CI sets DRIVER_DIR.
REM
REM Keep this file ASCII-only: cmd.exe reads .cmd in the OEM codepage.
setlocal
cd /d "%~dp0"

if not exist "dist\RFID-Scanner.exe" (
  echo dist\RFID-Scanner.exe not found. Run build-exe.cmd first.
  exit /b 1
)
set "DRV=%DRIVER_DIR%"
if not defined DRV set "DRV=driver\CDM_2.12.36.20"
if not exist "%DRV%\ftdibus.inf" goto nodriver
if not exist "%DRV%\ftdiport.inf" goto nodriver
for %%I in ("%DRV%") do set "DRVNAME=%%~nxI"

set "OUT=dist\RFID-Portable"
if exist "%OUT%" rmdir /s /q "%OUT%"
mkdir "%OUT%\tables"
if errorlevel 1 exit /b 1
REM Without portable.txt the exe behaves as a regular one and may open the
REM database remembered in the registry, so every copy has to be checked.
copy /y "dist\RFID-Scanner.exe" "%OUT%\" >nul
if errorlevel 1 exit /b 1
copy /y "packaging\portable\portable.txt" "%OUT%\portable.txt" >nul
if errorlevel 1 exit /b 1
copy /y "packaging\portable\README.txt" "%OUT%\README.txt" >nul
if errorlevel 1 exit /b 1
xcopy /e /i /q /y "%DRV%" "%OUT%\driver\%DRVNAME%" >nul
if errorlevel 1 exit /b 1

echo.
echo Done: %OUT%
echo Copy the whole folder onto the flash drive.
exit /b 0

:nodriver
echo No driver package in %DRV% (ftdibus.inf and ftdiport.inf).
echo Unpack ironlogic-driver-*.zip from the release into driver\ or set DRIVER_DIR.
exit /b 1
