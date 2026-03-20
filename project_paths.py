from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
LOCAL_WORKSPACE = PROJECT_ROOT / ".local-workspace"


def default_project_dir() -> Path:
    return PROJECT_ROOT


def default_workspace_dir() -> Path:
    return LOCAL_WORKSPACE


def default_base_dir() -> Path:
    return default_workspace_dir()
