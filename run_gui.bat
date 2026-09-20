@echo off
title MeetingScribe
chcp 65001 >nul
cd /d "%~dp0"
if exist config.env (
    for /f "usebackq tokens=1,* delims==" %%A in ("config.env") do (
        if not "%%A"=="" if not "%%B"=="" set "%%A=%%B"
    )
)
rem Prefer the installer's dedicated venv (see packaging/meetingscribe.iss)
rem if one exists next to this script; falls back to system Python for a
rem plain dev checkout, where there is no venv.
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" run_gui.py
) else (
    python run_gui.py
)
