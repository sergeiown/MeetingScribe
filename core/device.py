"""Compute-device detection and Windows sleep-prevention."""

import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional


def _get_cpu_name():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        name = winreg.QueryValueEx(key, "ProcessorNameString")[0]
        winreg.CloseKey(key)
        return name.strip()
    except Exception:
        pass
    try:
        import platform
        return platform.processor() or "CPU"
    except Exception:
        return "CPU"


def _add_cuda_dll_dirs():
    """Windows: ctranslate2 needs cuBLAS/cuDNN DLLs; torch ships them in torch\\lib.
    Also covers nvidia-* pip packages (site-packages/nvidia/*/bin)."""
    if os.name != "nt" or getattr(_add_cuda_dll_dirs, "_done", False):
        return
    _add_cuda_dll_dirs._done = True
    try:
        import torch
        candidates = [Path(torch.__file__).parent / "lib"]
        import site
        for sp in site.getsitepackages():
            nv = Path(sp) / "nvidia"
            if nv.is_dir():
                candidates += list(nv.glob("*/bin"))
        for d in candidates:
            if d.is_dir():
                os.add_dll_directory(str(d))
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


def detect_device(prefer: str = "auto"):
    """Returns (device_str, compute_type, device_name). Only call from the
    process-isolated ML pipeline - imports torch, which must never load
    alongside PySide6 (see gui/process_worker.py). Use detect_hardware_info()
    for a torch-free summary in the main GUI."""
    if prefer != "cpu":
        try:
            import torch
            if torch.cuda.is_available():
                _add_cuda_dll_dirs()
                major, _ = torch.cuda.get_device_capability(0)
                # Pascal (sm_6x) and older have no efficient fp16 -> int8_float32
                ctype = "float16" if major >= 7 else "int8_float32"
                return "cuda", ctype, torch.cuda.get_device_name(0)
        except Exception:
            pass
    return "cpu", "int8", _get_cpu_name()


def _find_nvidia_smi() -> Optional[str]:
    exe = shutil.which("nvidia-smi")
    if exe:
        return exe
    for p in (
        r"C:\Windows\System32\nvidia-smi.exe",
        r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
    ):
        if os.path.exists(p):
            return p
    return None


def nvidia_gpu_present() -> bool:
    """Torch-free NVIDIA GPU presence check - safe to call from the main GUI
    process, unlike detect_device(), which must import torch."""
    return _find_nvidia_smi() is not None


def nvidia_gpu_name() -> Optional[str]:
    """Torch-free GPU model name via `nvidia-smi`, or None if unavailable."""
    exe = _find_nvidia_smi()
    if not exe:
        return None
    try:
        result = subprocess.run(
            [exe, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = result.stdout.strip().splitlines()
        return lines[0].strip() if lines and lines[0].strip() else None
    except Exception:
        return None


def _any_gpu_names() -> list:
    """Every video controller Windows knows about (any vendor) - torch-free
    fallback so a non-NVIDIA GPU is still reported, just as unsupported."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        # Drop Windows' generic fallback-adapter placeholder if a real GPU is also listed.
        real = [n for n in names if "microsoft basic" not in n.lower()]
        return real or names
    except Exception:
        return []


def detect_hardware_info():
    """(cpu_name, gpu_name_or_None, gpu_supported) - torch-free, safe for the
    main GUI process. gpu_supported is True only for NVIDIA (the only kind
    this app's CUDA acceleration can use). Display only - detect_device()
    decides the actual device used for a run.

    Cached for the process lifetime: on a machine with no NVIDIA GPU, the
    fallback below spawns a whole PowerShell process to query WMI for
    *any* video controller - confirmed by direct timing to take ~1.9s on
    its own, which is nearly the entire delay in opening Settings (it
    previously ran fresh every single time the dialog was built). Hardware
    doesn't change while the app is running, so there's nothing to gain by
    re-querying it every time the user reopens Settings.

    A lock guards the actual computation (not just the cache read) since
    this is deliberately also kicked off from a background thread right at
    startup to pre-warm the cache before Settings is ever opened - without
    it, a call from the GUI thread arriving before that background call
    finishes would just redundantly re-run the same slow subprocess calls
    instead of waiting for the one already in flight."""
    cached = getattr(detect_hardware_info, "_cache", None)
    if cached is not None:
        return cached
    with _hardware_info_lock:
        cached = getattr(detect_hardware_info, "_cache", None)
        if cached is not None:
            return cached
        cpu_name = _get_cpu_name()
        nvidia_name = nvidia_gpu_name()
        if nvidia_name:
            result = (cpu_name, nvidia_name, True)
        else:
            others = _any_gpu_names()
            result = (cpu_name, (others[0] if others else None), False)
        detect_hardware_info._cache = result
        return result


_hardware_info_lock = threading.Lock()


def set_sleep_prevention(active: bool) -> None:
    """Windows: keep the machine awake during long-running work. No-op elsewhere."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        if active:
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
        else:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except Exception:
        pass
