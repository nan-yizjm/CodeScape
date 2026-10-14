"""Resolve application-owned resources in development and the Windows distribution."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


def executable(relative_path: str, developer_name: str) -> str:
    bundled = resource_root() / relative_path
    if bundled.is_file():
        return str(bundled)
    fallback = None if getattr(sys, "frozen", False) else shutil.which(developer_name)
    if not fallback:
        raise ValueError(f"{developer_name} 运行组件缺失，请修复 CodeScape 安装。")
    return fallback


def worker_command(module: str, flag: str) -> list[str]:
    return [sys.executable, flag] if getattr(sys, "frozen", False) else [sys.executable, "-m", module]
