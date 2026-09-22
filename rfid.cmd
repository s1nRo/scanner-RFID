@echo off
REM Wrapper. The interpreter lives in venv\bin (venv built by MSYS2 Python),
REM not in venv\Scripts. Keep this file ASCII-only: cmd.exe reads .cmd in the
REM OEM codepage and mangles UTF-8 comments into bogus commands.
"%~dp0venv\bin\python.exe" -m rfid %*
