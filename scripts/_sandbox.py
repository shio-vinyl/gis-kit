"""Runtime defaults for restricted desktop sandboxes."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Build matplotlib's font list without `system_profiler`: in restricted macOS sandboxes it returns
# a plist without "_items" and matplotlib 3.9 raises KeyError while building the cache, again on
# every run because the cache is never written. fontconfig and the system font folders are still
# scanned, so the list matches an unrestricted build in practice.
_SEED_FONT_CACHE = """
import subprocess
_check_output = subprocess.check_output
def _guarded(command, *args, **kwargs):
    if command and command[0] == 'system_profiler':
        raise OSError('system_profiler skipped while seeding the font cache')
    return _check_output(command, *args, **kwargs)
subprocess.check_output = _guarded
import matplotlib.font_manager
"""


def seed_matplotlib_font_cache(directory: Path | str | None = None) -> bool:
    """Write the font cache once if it is missing (macOS only). Returns True when seeded."""
    if sys.platform != "darwin":
        return False
    directory = Path(directory or os.environ.get("MPLCONFIGDIR") or Path.home() / ".matplotlib")
    if any(directory.glob("fontlist-v*.json")):
        return False
    directory.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([sys.executable, "-c", _SEED_FONT_CACHE], env={**os.environ, "MPLCONFIGDIR": str(directory)},
                            capture_output=True, text=True, timeout=600)
    return result.returncode == 0 and any(directory.glob("fontlist-v*.json"))


def configure_writable_caches() -> None:
    cache_root = Path(tempfile.gettempdir()) / "codex-gis-cache"
    matplotlib_dir = cache_root / "matplotlib"
    xdg_cache_dir = cache_root / "xdg"
    fontconfig_dir = xdg_cache_dir / "fontconfig"
    matplotlib_dir.mkdir(parents=True, exist_ok=True)
    xdg_cache_dir.mkdir(parents=True, exist_ok=True)
    fontconfig_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_dir))
    os.environ.setdefault("XDG_CACHE_HOME", str(xdg_cache_dir))
    seed_matplotlib_font_cache()
