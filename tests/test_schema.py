import sqlite3

import pytest
from codescape.database import Database
from codescape.storage_schema import SCHEMA_REVISION, SCHEMA_VERSION, StorageError


def legacy(root):
    database = Database(root)
    database.insert({"id": "p", "name": "旧学习项目", "source": "不删除的来源", "created_at": "now",
                     "status": "ready", "snapshot": {"files": {}}, "version_id": "v", "demo": 0})
    database.set_learning("p", "f", "v", "mastered", 0)
    database.engine.dispose()
    with sqlite3.connect(root / "app.db") as connection:
        connection.execute("PRAGMA user_version=0")
        connection.execute("DROP TABLE alembic_version")
    return root / "app.db"


def test_legacy_upgrade_preserves_data_and_creates_verified_backup(tmp_path):
    path = legacy(tmp_path)
    database = Database(tmp_path)
    assert database.get("p")["name"] == "旧学习项目"
    assert database.states("p")["f"]["state"] == "mastered"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == SCHEMA_REVISION
    copies = list((tmp_path / "migration-backups").glob("*.sqlite"))
    assert len(copies) == 1
    with sqlite3.connect(copies[0]) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("SELECT name FROM prototype_projects").fetchone()[0] == "旧学习项目"
    database.engine.dispose()
    again = Database(tmp_path)
    assert len(list((tmp_path / "migration-backups").glob("*.sqlite"))) == 1
    again.engine.dispose()


def test_failed_upgrade_restores_original_and_stops_writes(tmp_path, monkeypatch):
    path = legacy(tmp_path)
    from codescape.storage_schema import command
    def interrupted(config, revision):
        connection = config.attributes["connection"]
        connection.exec_driver_sql("UPDATE prototype_projects SET name='错误修改'")
        connection.exec_driver_sql("CREATE TABLE partial_changes(value TEXT)")
        raise RuntimeError("模拟迁移中断")
    monkeypatch.setattr(command, "upgrade", interrupted)
    with pytest.raises(StorageError, match="恢复原数据"):
        Database(tmp_path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name FROM prototype_projects").fetchone()[0] == "旧学习项目"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name='partial_changes'").fetchall()
    monkeypatch.undo()
    restarted = Database(tmp_path)
    assert restarted.states("p")["f"]["state"] == "mastered"
    restarted.engine.dispose()


def test_newer_or_unknown_schema_is_not_downgraded(tmp_path):
    path = legacy(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    before = path.read_bytes()
    with pytest.raises(StorageError, match="更新版本"):
        Database(tmp_path)
    assert path.read_bytes() == before
    assert not (tmp_path / "migration-backups").exists()


def test_corrupt_database_is_not_replaced_by_empty_database(tmp_path):
    path = tmp_path / "app.db"
    original = b"damaged application data -- never silently replace"
    path.write_bytes(original)
    with pytest.raises(StorageError, match="损坏"):
        Database(tmp_path)
    assert path.read_bytes() == original


def test_missing_current_schema_table_is_not_silently_recreated(tmp_path):
    database = Database(tmp_path)
    database.engine.dispose()
    with sqlite3.connect(tmp_path / "app.db") as connection:
        connection.execute("DROP TABLE prototype_learning")
    with pytest.raises(StorageError, match="缺少"):
        Database(tmp_path)
    with sqlite3.connect(tmp_path / "app.db") as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name='prototype_learning'").fetchall()
