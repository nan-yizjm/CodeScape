import hashlib
import sqlite3

import pytest
from codescape.database import Database
from codescape.release_check import ReleaseCheckError, validate_release_data
from codescape.storage_schema import KNOWN_REVISIONS, SCHEMA_VERSION


def contract(database):
    return {"version": SCHEMA_VERSION, "revisions": KNOWN_REVISIONS,
        "columns": {name: [column.name for column in table.columns] for name, table in database.projects.metadata.tables.items()}}


def test_install_check_is_read_only_and_accepts_current_data(tmp_path):
    database = Database(tmp_path)
    expected = contract(database)
    database.engine.dispose()
    before = hashlib.sha256((tmp_path / "app.db").read_bytes()).hexdigest()
    validate_release_data(tmp_path, expected)
    assert hashlib.sha256((tmp_path / "app.db").read_bytes()).hexdigest() == before
    assert not (tmp_path / "migration-backups").exists()
    missing = tmp_path / "not-created"
    validate_release_data(missing, expected)
    assert not missing.exists()


@pytest.mark.parametrize("mutation", ["newer", "wrong-revision", "missing-table", "missing-column", "corrupt"])
def test_installer_refuses_incompatible_or_corrupt_data_before_replacement(tmp_path, mutation):
    database = Database(tmp_path)
    expected = contract(database)
    database.engine.dispose()
    path = tmp_path / "app.db"
    if mutation == "corrupt":
        path.write_bytes(b"owned-corrupt-fixture")
    else:
        with sqlite3.connect(path) as connection:
            if mutation == "newer":
                connection.execute("PRAGMA user_version=999")
            elif mutation == "wrong-revision":
                connection.execute("UPDATE alembic_version SET version_num='future'")
            elif mutation == "missing-table":
                connection.execute("DROP TABLE prototype_learning")
            else:
                connection.execute("ALTER TABLE prototype_sessions DROP COLUMN value")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ReleaseCheckError):
        validate_release_data(tmp_path, expected)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not (tmp_path / "migration-backups").exists()
