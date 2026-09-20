"""Check GitHub Releases for a newer version. Notify-only - never downloads
or installs anything; the user decides whether to act on it."""

import json
import re
import urllib.request

from .version import VERSION, GITHUB_REPO

_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _parse_version(v: str):
    v = v.lstrip("vV")
    nums = tuple(int(p) for p in re.findall(r"\d+", v)[:3])
    return nums or (0,)


def check_for_update(timeout: float = 5.0):
    """{"version": "v2.1.0", "url": "..."} if a newer release exists, else
    None. Never raises - any network/parsing failure just means no update is
    reported, since this is a convenience check, not a critical path."""
    try:
        req = urllib.request.Request(
            _API_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "MeetingScribe"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest_tag = data.get("tag_name", "")
        if _parse_version(latest_tag) > _parse_version(VERSION):
            return {
                "version": latest_tag,
                "url": data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases/latest",
            }
    except Exception:
        pass
    return None
