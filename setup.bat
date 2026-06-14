@echo off
title MeetingScribe - Setup
chcp 65001 >nul
cd /d "%~dp0"

rem Working folders (all git-ignored) so the user has somewhere to drop files
rem right after setup, before run.bat is ever launched.
if not exist input mkdir input
if not exist output mkdir output
if not exist logs mkdir logs
set LOG=logs\setup.log
echo === Setup started %DATE% %TIME% > %LOG%

echo [1/7] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found. Installing via winget...
    echo Python: installing via winget >> %LOG%
    winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Auto-install failed. Install manually: https://python.org
        echo Python: install FAILED >> %LOG%
        goto :done
    )
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
    python --version >nul 2>&1
    if errorlevel 1 (
        echo Python installed but PATH not updated. Close this window and run setup.bat again.
        echo Python: installed, PATH not updated - manual restart needed >> %LOG%
        goto :done
    )
    echo Python ready.
    echo Python: installed OK >> %LOG%
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo Python: %%v >> %LOG%
python --version

rem Keep the PC awake for the whole setup (deps, CUDA torch and model downloads
rem can take many minutes). A tiny background process holds the system-required
rem flag and is killed at the end (:done).
start "MS_AWAKE" /min python -c "import ctypes,time;ctypes.windll.kernel32.SetThreadExecutionState(2147483649);time.sleep(14400)" >nul 2>&1
echo Sleep prevention: on >> %LOG%

echo.
echo [2/7] Checking ffmpeg...
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo ffmpeg not found. Installing via winget...
    echo ffmpeg: installing via winget >> %LOG%
    winget install -e --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
    if errorlevel 1 (
        echo ERROR: ffmpeg auto-install failed.
        echo Install manually: https://www.gyan.dev/ffmpeg/builds/
        echo ffmpeg: install FAILED >> %LOG%
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
echo [3/7] Installing dependencies...
echo Dependencies: installing >> %LOG%
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed.
    echo Dependencies: FAILED >> %LOG%
    goto :done
)
echo Dependencies: OK >> %LOG%

echo.
echo [4/7] Checking GPU...
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
echo [5/7] Checking config.env...
if not exist config.env (
    if exist config.env.example (
        copy config.env.example config.env >nul
        echo Created config.env from example.
        echo IMPORTANT: Edit config.env and set your HF_TOKEN before continuing.
    ) else (
        echo WARNING: config.env not found. Diarization will be disabled.
    )
) else (
    echo config.env found.
)

echo.
echo [6/7] HuggingFace model licenses
echo -------------------------------------------------------
echo Before downloading you must accept the license for each
echo pyannote model while logged in with your HF account:
echo.
echo   https://hf.co/pyannote/speaker-diarization-3.1
echo   https://hf.co/pyannote/segmentation-3.0
echo   https://hf.co/pyannote/embedding
echo.
echo Open all three links, click "Agree and access repository"
echo -------------------------------------------------------
pause

echo.
echo [7/7] Downloading models...
echo (mandatory models install automatically; you will be asked about optional ones)
python download_models.py
if errorlevel 1 (
    echo Models: download reported an error >> %LOG%
) else (
    echo Models: OK >> %LOG%
)

:done
taskkill /fi "WINDOWTITLE eq MS_AWAKE" /f >nul 2>&1
echo Sleep prevention: off >> %LOG%
echo === Setup finished %DATE% %TIME% >> %LOG%
echo.
echo ============================================================
echo   Setup finished.
echo   Log: %LOG%
echo.
echo   Next: put your audio/video files into the input\ folder
echo   (.webm .mp4 .mkv .mov .avi .m4a .mp3 .wav),
echo   then run run.bat to start transcription.
echo ============================================================
echo.
pause