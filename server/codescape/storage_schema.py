from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

SCHEMA_VERSION = 1
SCHEMA_REVISION = "cs_0001"
_migration_lock = threading.RLock()


class StorageError(RuntimeError):
    pass


def check_database(path: Path) -> tuple[int, str | None, set[str]]:
    try:
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise StorageError("本地数据库校验失败，已停止启动；请保留数据并修复，不能新建空库替代。")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            revisions = list(connection.execute("SELECT version_num FROM alembic_version")) if "alembic_version" in tables else []
            revision = revisions[0][0] if len(revisions) == 1 else None
            if len(revisions) > 1:
                raise StorageError("数据库模式存在多个版本，已停止启动。")
            return version, revision, tables
    except sqlite3.Error:
        raise StorageError("本地数据库不可读取或已损坏，已停止启动；原数据库没有被空库替换。") from None


def _copy_database(source: Path, destination: Path):
    with sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True) as previous:
        with sqlite3.connect(destination) as following:
            previous.backup(following)


def migrate_database(root: Path, metadata):
    """Inspect first, back up before upgrade, and fail closed on any mismatch."""
    path = root / "app.db"
    with _migration_lock:
        if path.is_symlink() or (path.exists() and path.resolve().parent != root.resolve()):
            raise StorageError("数据库文件不在应用数据目录内，已停止启动。")
        existing = path.exists()
        version, revision, tables = check_database(path) if existing else (0, None, set())
        if version > SCHEMA_VERSION or revision not in {None, SCHEMA_REVISION}:
            raise StorageError("数据库来自更新版本的 CodeScape，请使用相应版本；不会自动降级。")
        if version == SCHEMA_VERSION:
            if revision != SCHEMA_REVISION:
                raise StorageError("数据库模式标识不一致，已停止启动。")
            engine = create_engine(f"sqlite:///{path.as_posix()}")
            try:
                validate_tables(engine, metadata)
            finally:
                engine.dispose()
            return
        if revision is not None or tables - set(metadata.tables) - {"alembic_version", "sqlite_sequence"}:
            raise StorageError("无法识别旧数据库结构，已停止升级；原数据保持不变。")
        backup = None
        if existing:
            directory = root / "migration-backups"
            directory.mkdir(exist_ok=True)
            if not directory.resolve().is_relative_to(root.resolve()):
                raise StorageError("迁移安全副本目录不在应用数据目录内，已停止升级。")
            backup = directory / f"{uuid.uuid4()}.sqlite"
            try:
                _copy_database(path, backup)
                check_database(backup)
            except (OSError, sqlite3.Error):
                raise StorageError("迁移安全副本创建失败，未开始升级。") from None
        engine = create_engine(f"sqlite:///{path.as_posix()}")
        try:
            # Old prototype tables must have their known columns before any DDL.
            validate_tables(engine, metadata, existing_only=True)
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
            config.attributes["metadata"] = metadata
            with engine.connect() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                config.attributes["connection"] = connection
                command.upgrade(config, SCHEMA_REVISION)
                validate_tables(connection, metadata)
                connection.exec_driver_sql(f"PRAGMA user_version={SCHEMA_VERSION}")
                connection.commit()
            actual, recorded, _ = check_database(path)
            if (actual, recorded) != (SCHEMA_VERSION, SCHEMA_REVISION):
                raise StorageError("升级后的模式校验不一致。")
        except Exception:
            engine.dispose()
            if backup:
                try:
                    _copy_database(backup, path)
                    check_database(path)
                except (OSError, sqlite3.Error, StorageError):
                    raise StorageError("数据库升级失败且安全副本自动恢复失败；请保留数据库和迁移副本，已停止写入。") from None
            raise StorageError("数据库升级失败，已恢复原数据并停止启动。" if backup else "数据库初始化失败，已停止启动。") from None
        finally:
            engine.dispose()
        # Application-owned technical recovery copies; retain only the newest three.
        if backup:
            try:
                previous = sorted(backup.parent.glob("*.sqlite"), key=lambda item: item.stat().st_mtime, reverse=True)
            except OSError:
                return
            for candidate in previous[3:]:
                try:
                    uuid.UUID(candidate.stem)
                    if candidate.resolve().parent == backup.parent.resolve():
                        candidate.unlink()
                except (ValueError, OSError):
                    continue


def validate_tables(bind, metadata, existing_only=False):
    inspector = inspect(bind)
    available = set(inspector.get_table_names())
    for name, table in metadata.tables.items():
        if name not in available:
            if existing_only:
                continue
            raise StorageError("数据库缺少预期数据表，已停止启动。")
        actual = {column["name"] for column in inspector.get_columns(name)}
        if not {column.name for column in table.columns}.issubset(actual):
            raise StorageError("数据库字段与预期不一致，已停止升级。")
