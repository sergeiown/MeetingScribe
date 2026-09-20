"""Vendor-icon selection for detected hardware, based on the device name
strings core.detect_hardware_info() returns.

These are original, brand-colored badges (Intel blue / AMD red / NVIDIA
green) - not the vendors' actual trademarked logos, which this project has
no verified rights to redistribute. Falls back to a generic chip/card icon
when the vendor can't be determined from the name string.
"""

from pathlib import Path

_ASSETS_DIR = Path(__file__).parent / "assets"


def cpu_icon_path(cpu_name: str) -> Path:
    name = (cpu_name or "").lower()
    if "intel" in name:
        return _ASSETS_DIR / "intel.svg"
    if "amd" in name:
        return _ASSETS_DIR / "amd.svg"
    return _ASSETS_DIR / "cpu.svg"


def gpu_icon_path(gpu_name: str) -> Path:
    name = (gpu_name or "").lower()
    if "nvidia" in name or "geforce" in name or "rtx" in name or "gtx" in name or "quadro" in name:
        return _ASSETS_DIR / "nvidia.svg"
    return _ASSETS_DIR / "gpu.svg"
