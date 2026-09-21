"""Picks a vendor icon for detected hardware. These are original badges,
not vendors' trademarked logos, which this project has no rights to
redistribute; falls back to a generic icon when the vendor is unclear."""

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
