from __future__ import annotations

import os
from pathlib import Path


def app_home() -> Path:
    configured = os.environ.get("OLX_MONITOR_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "OLXMarketMonitor"
    return Path.home() / ".local" / "share" / "OLXMarketMonitor"


def ensure_directories(home: Path | None = None) -> Path:
    root = home or app_home()
    (root / "reports").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    return root
