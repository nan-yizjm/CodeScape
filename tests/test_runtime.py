import subprocess
import sys

import pytest
from codescape import runtime
from codescape.snapshots import create_snapshot


def test_frozen_runtime_uses_owned_files_and_never_path_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(runtime.shutil, "which", lambda name: pytest.fail("冻结程序不允许依赖开发机 PATH"))
    bundled = tmp_path / "runtime/node/node.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"owned-resource")
    assert runtime.resource_root() == tmp_path
    assert runtime.executable("runtime/node/node.exe", "node") == str(bundled)
    with pytest.raises(ValueError, match="运行组件缺失"):
        runtime.executable("runtime/git/cmd/git.exe", "git")
    assert runtime.worker_command("codescape.index_worker", "--index-worker") == [sys.executable, "--index-worker"]


def test_index_worker_reads_snapshot_without_executing_original(tmp_path):
    import json

    source = tmp_path / "source"
    source.mkdir()
    marker = tmp_path / "must-not-exist"
    text = f"raise RuntimeError({str(marker)!r})\ndef target(): return 1\ndef caller(): return target()\n"
    (source / "owned.py").write_text(text, encoding="utf-8")
    manifest = create_snapshot(source, tmp_path / "workspace")
    (source / "owned.py").write_text("invalid modified original", encoding="utf-8")
    result = subprocess.run(runtime.worker_command("codescape.index_worker", "--index-worker"),
        input=json.dumps({"root": str(tmp_path / "workspace/snapshots" / manifest["id"]),
            "manifest": manifest, "version": "owned-version"}).encode(), capture_output=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0
    value = json.loads(result.stdout)
    assert {entity["name"] for entity in value["entities"] if entity["visible"]} == {"target", "caller"}
    assert len([call for call in value["calls"] if call["resolution"] == "confirmed"]) == 1
    assert not marker.exists()
