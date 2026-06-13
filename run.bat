@echo off
title AudioProc - Transcription
chcp 65001 >nul
cd /d "%~dp0"
if exist config.env (
    for /f "usebackq tokens=1,* delims==" %%A in ("config.env") do (
        if not "%%A"=="" if not "%%B"=="" set "%%A=%%B"
    )
)
python run_interactive.py
