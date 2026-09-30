"""Runtime defaults for restricted desktop sandboxes."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


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
