from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
LOCAL_WORKSPACE = PROJECT_ROOT / ".local-workspace"


def default_project_dir() -> Path:
    return PROJECT_ROOT


def default_workspace_dir() -> Path:
    return LOCAL_WORKSPACE


def default_base_dir() -> Path:
    return default_workspace_dir()


# ---------------------------------------------------------------------------
# Platform-aware user-data directories
# ---------------------------------------------------------------------------

def _xdg_data_home() -> Path:
    """Return XDG_DATA_HOME, defaulting to ~/.local/share on Linux."""
    xdg = os.environ.get("XDG_DATA_HOME", "")
    if xdg:
        return Path(xdg)
    return Path.home() / ".local" / "share"


def _user_data_dir(app_name: str) -> Path:
    """Return a platform-appropriate user-data directory for *app_name*.

    - macOS:   ~/Library/Application Support/<app_name>
    - Windows: %APPDATA%\\<app_name>
    - Linux:   $XDG_DATA_HOME/<app_name>  (default ~/.local/share/<app_name>)
    """
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    if system == "Windows":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / app_name
    # Linux / other POSIX
    return _xdg_data_home() / app_name


def _downloads_dir() -> Path:
    """Return the platform-appropriate Downloads directory.

    Falls back to ~/Downloads if the system directory cannot be determined.
    """
    system = platform.system()
    if system == "Windows":
        # Try the Windows known-folders registry path
        try:
            import winreg  # noqa: PLC0415
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            )
            raw, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
            return Path(os.path.expandvars(raw))
        except Exception:
            pass

    xdg_dl = os.environ.get("XDG_DOWNLOAD_DIR", "")
    if xdg_dl:
        return Path(xdg_dl)

    return Path.home() / "Downloads"


APP_DATA_DIR = _user_data_dir("downloadscapper")

# Default directories used by csv_download_client.py and download_index.py
DEFAULT_DOWNLOADS_ROOT = _downloads_dir() / "csv-download-client"
DEFAULT_SCRAPE_SAVE_DIR = _downloads_dir() / "downloadscapper-scrapes"
DEFAULT_STATE_DIR = APP_DATA_DIR / "state"
DEFAULT_LOG_DIR = APP_DATA_DIR / "logs"
