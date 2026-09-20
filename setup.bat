@echo off
title MeetingScribe - Setup
chcp 65001 >nul
cd /d "%~dp0"

rem Working folders (all git-ignored) so the user has somewhere to drop files
rem right after setup, before the app is ever launched.
if not exist input mkdir input
if not exist output mkdir output
if not exist logs mkdir logs
set LOG=logs\setup.log
echo === Setup started %DATE% %TIME% > %LOG%

echo [1/5] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found. Installing via winget...
    echo Python: installing via winget >> %LOG%
    winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Auto-install failed. Install manually: https://python.org
        echo Python: install FAILED >> %LOG%
        set SETUP_FAILED=1
        goto :done
    )
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
    python --version >nul 2>&1
    if errorlevel 1 (
        echo Python installed but PATH not updated. Close this window and run setup.bat again.
        echo Python: installed, PATH not updated - manual restart needed >> %LOG%
        set SETUP_FAILED=1
        goto :done
    )
    echo Python ready.
    echo Python: installed OK >> %LOG%
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo Python: %%v >> %LOG%
python --version

echo.
echo [2/5] Checking ffmpeg...
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo ffmpeg not found. Installing via winget...
    echo ffmpeg: installing via winget >> %LOG%
    winget install -e --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
    if errorlevel 1 (
        echo ERROR: ffmpeg auto-install failed.
        echo Install manually: https://www.gyan.dev/ffmpeg/builds/
        echo ffmpeg: install FAILED >> %LOG%
        set SETUP_FAILED=1
        goto :done
    )
    set "PATH=C:\Program Files\ffmpeg\bin;%PATH%"
    echo ffmpeg installed.
    echo ffmpeg: installed OK >> %LOG%
) else (
    echo ffmpeg found.
    echo ffmpeg: already present >> %LOG%
)

echo.
echo [3/5] Installing dependencies...
echo Dependencies: installing >> %LOG%
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed.
    echo Dependencies: FAILED >> %LOG%
    set SETUP_FAILED=1
    goto :done
)
echo Dependencies: OK >> %LOG%

echo.
echo [4/5] Checking GPU...
set GPU_FOUND=0
where nvidia-smi >nul 2>&1
if not errorlevel 1 set GPU_FOUND=1
if exist "%windir%\System32\nvidia-smi.exe" set GPU_FOUND=1
if exist "%ProgramFiles%\NVIDIA Corporation\NVSMI\nvidia-smi.exe" set GPU_FOUND=1
echo GPU check: GPU_FOUND=%GPU_FOUND% >> %LOG%
if "%GPU_FOUND%"=="0" goto :no_gpu

echo NVIDIA GPU found. Installing CUDA torch (this may take a few minutes)...
echo GPU: NVIDIA detected >> %LOG%
python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126 --upgrade
if errorlevel 1 goto :cuda_fail
echo CUDA torch installed.
echo GPU: CUDA torch OK >> %LOG%
goto :after_gpu

:cuda_fail
echo WARNING: CUDA torch install failed. CPU will be used.
echo GPU: CUDA torch FAILED >> %LOG%
goto :after_gpu

:no_gpu
echo No NVIDIA GPU detected - using CPU.
echo GPU: not found, CPU mode >> %LOG%

:after_gpu

echo.
echo [5/5] Checking config.env...
if not exist config.env (
    if exist config.env.example (
        copy config.env.example config.env >nul
        echo Created config.env from example.
        echo (Speaker diarization needs your own HF_TOKEN - set it any time in
        echo  the app's Settings ^> General, no need to edit config.env by hand.)
    ) else (
        echo WARNING: config.env not found. Diarization will be disabled.
    )
) else (
    echo config.env found.
)

:done
echo === Setup finished %DATE% %TIME% >> %LOG%
if "%SETUP_FAILED%"=="1" (
    echo.
    echo ============================================================
    echo   Setup did not finish - see the errors above. Log: %LOG%
    echo ============================================================
    echo.
    pause
    goto :eof
)
echo.
echo ============================================================
echo   Setup finished. Log: %LOG%
echo   Launching MeetingScribe...
echo ============================================================
echo.
call run_gui.bat