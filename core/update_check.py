"""Check GitHub Releases for a newer version, and (if the user opts in via
the GUI) download and launch the new installer. The check itself stays
notify-only in spirit - it never downloads or installs anything on its
own; download_installer/spawn_installer are separate, only ever called
after the user's own explicit confirmation."""

import json
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from .version import VERSION, GITHUB_REPO

_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_ASSET_NAME_RE = re.compile(r"^MeetingScribe-Setup-.*\.exe$", re.IGNORECASE)
_CHUNK_SIZE = 1 << 16

UPDATE_DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "MeetingScribe-Update"

_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


def _parse_version(v: str):
    v = v.lstrip("vV")
    nums = tuple(int(p) for p in re.findall(r"\d+", v)[:3])
    return nums or (0,)


def check_for_update(timeout: float = 5.0):
    """{"version": "v2.1.0", "url": "...", "installer_url": "..." or None,
    "installer_name": "..." or None, "installer_size": 0} if a newer
    release exists, else None. installer_url is None whenever the release
    has no asset named like MeetingScribe-Setup-*.exe (an older tag, or a
    manually-edited release) - callers must then fall back to the
    open-the-releases-page behavior, there is nothing to download. Never
    raises - any network/parsing failure just means no update is reported,
    since this is a convenience check, not a critical path."""
    try:
        req = urllib.request.Request(
            _API_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "MeetingScribe"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest_tag = data.get("tag_name", "")
        if _parse_version(latest_tag) > _parse_version(VERSION):
            installer_url = installer_name = None
            installer_size = 0
            for asset in data.get("assets") or []:
                if _ASSET_NAME_RE.match(asset.get("name", "")):
                    installer_url = asset.get("browser_download_url")
                    installer_name = asset.get("name")
                    installer_size = asset.get("size", 0)
                    break
            return {
                "version": latest_tag,
                "url": data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases/latest",
                "installer_url": installer_url,
                "installer_name": installer_name,
                "installer_size": installer_size,
            }
    except Exception:
        pass
    return None


def download_installer(url: str, dest_path: Path, cancel_token, on_progress, timeout: float = 10.0) -> None:
    """Streams the installer exe to dest_path, checking cancel_token every
    chunk. Writes to a sibling "<name>.part" file first and only
    Path.replace()s onto dest_path after a fully successful read, so a
    half-written file can never be mistaken for a real installer - on
    Cancelled or any other error, the .part file is removed and the
    exception re-raised."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest_path.with_name(dest_path.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "MeetingScribe"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            done = 0
            with open(part_path, "wb") as f:
                while True:
                    cancel_token.check()
                    chunk = resp.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    on_progress(float(done), float(total), dest_path.name)
        part_path.replace(dest_path)
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise


def cleanup_update_downloads() -> None:
    """Called once on every launch. Removes only *.exe/*.part (the
    multi-MB installer payloads) from UPDATE_DOWNLOAD_DIR - *.log files
    are deliberately kept, so a failed silent update leaves a diagnosable
    trail for at least one more launch. Best-effort, never raises."""
    if not UPDATE_DOWNLOAD_DIR.exists():
        return
    for p in UPDATE_DOWNLOAD_DIR.iterdir():
        if p.suffix.lower() in (".exe", ".part"):
            try:
                p.unlink()
            except OSError:
                pass


def spawn_installer(installer_path: Path, log_path: Path) -> None:
    """Fire-and-forget: launches the installer fully detached so it
    outlives this process once it exits (same defensive posture
    packaging/launcher/launcher.py already uses for its own child).
    Raises OSError if CreateProcess itself fails - the caller must treat
    that as "update did not start", not proceed to close the app."""
    args = [str(installer_path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log_path}"]
    subprocess.Popen(
        args,
        creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
