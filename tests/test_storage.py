from concurrent.futures import ThreadPoolExecutor

import pytest
from codescape.database import Database
from codescape.security import CredentialStore
from codescape.snapshots import create_snapshot, read_snapshot


def test_snapshot_filters_and_integrity_without_source_execution(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    original = "raise RuntimeError('不可执行')\n"
    (source / "x.py").write_bytes(original.encode())
    (source / ".env").write_text("secret", encoding="utf-8")
    (source / "omit.py").write_text("pass", encoding="utf-8")
    (source / "bom.py").write_bytes(b"\xef\xbb\xbfpass")
    manifest = create_snapshot(source, tmp_path / "data", ["omit.py"])
    assert set(manifest["files"]) == {"x.py"}
    assert len(manifest["excluded"]) == 3
    root = tmp_path / "data/snapshots" / manifest["id"]
    assert read_snapshot(root, manifest) == {"x.py": original}
    assert (source / "x.py").read_text(encoding="utf-8") == original
    (root / "x.py").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="校验"):
        read_snapshot(root, manifest)


def test_learning_concurrent_revision_and_persistence(tmp_path):
    database = Database(tmp_path)

    def write():
        try:
            database.set_learning("p", "f", "v", "mastered", 0)
            return "saved"
        except ValueError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: write(), range(2)))
    assert sorted(results) == ["conflict", "saved"]
    database.engine.dispose()
    restarted = Database(tmp_path)
    assert restarted.states("p")["f"] == {"state": "mastered", "revision": 1}
    restarted.engine.dispose()


def test_dpapi_does_not_save_plaintext(tmp_path):
    store = CredentialStore(tmp_path)
    value = "仅用于本地单元测试的假密钥"
    store.save(value)
    assert store.read() == value
    assert value.encode() not in (tmp_path / "credentials/provider.dpapi").read_bytes()
