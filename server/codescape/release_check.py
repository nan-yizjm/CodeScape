"""Read-only release compatibility checks, independent of application startup."""
from __future__ import annotations

import sqlite3
from pathlib import Path


class ReleaseCheckError(RuntimeError):
    pass


def validate_release_data(root: Path, contract: dict) -> None:
    path = root / "app.db"
    if path.is_symlink() or path.is_junction() or path.resolve().parent != root.resolve():
        raise ReleaseCheckError("数据库文件不在应用数据目录内，安装已停止。")
    if not path.exists():
        return
    try:
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ReleaseCheckError("数据库校验失败；现有程序和学习数据保持不变。")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            revisions = list(connection.execute("SELECT version_num FROM alembic_version")) if "alembic_version" in tables else []
            revision = revisions[0][0] if len(revisions) == 1 else None
            if (version > contract["version"] or len(revisions) > 1
                    or (version == 0 and revision is not None)
                    or (version > 0 and contract["revisions"].get(revision) != version)):
                raise ReleaseCheckError("数据版本与本安装包不兼容；没有替换现有程序。")
            expected = contract["columns"]
            if tables - set(expected) - {"alembic_version", "sqlite_sequence"}:
                raise ReleaseCheckError("数据表无法识别，安装已停止。")
            for name, columns in expected.items():
                if name not in tables:
                    if version == contract["version"]:
                        raise ReleaseCheckError("数据库缺少预期数据表，安装已停止。")
                    continue
                actual = {row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')}
                required = set(columns)
                if version < 2 and name == "prototype_projects":
                    required.discard("lifecycle")
                if not required.issubset(actual):
                    raise ReleaseCheckError("数据库字段无法核验，安装已停止。")
    except sqlite3.Error:
        raise ReleaseCheckError("数据库不可读取或已损坏；没有创建空库或替换现有程序。") from None
