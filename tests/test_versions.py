import asyncio
import threading
import time
import uuid

from codescape.analyzers import cpp_analysis, python_analysis, typescript_analysis
from codescape.app import create_app
from codescape.database import Database
from codescape.migration import learning_mapping
from codescape.models import Architecture, Module, Overview
from codescape.provider import Provider
from fastapi.testclient import TestClient


def architecture(analysis):
    return Architecture(modules=[Module(id="leaf", name="数据处理", summary="校验和转换数据。",
        code_paths=list({entity.location.path for entity in analysis.entities}),
        entity_ids=[entity.id for entity in analysis.entities if entity.visible])],
        overview=Overview(introduction="处理输入数据的样例。", capabilities=["数据转换"], principle="按步骤转换。", limitations=[]))


def wait_status(client, url, status):
    for _ in range(200):
        value = client.get(url).json()["data"]
        if value["status"] == status:
            return value
        time.sleep(.02)
    raise AssertionError(value)


def login(client, nonce):
    client.headers["x-csrf-token"] = client.post("/api/v1/session/exchange", json={"nonce": nonce}).json()["data"]["csrf_token"]


def prepare(client, url):
    preview = client.post(url + "/reanalysis/preview", json={}).json()["data"]
    return client.post(url + "/reanalysis", json={"preview_id": preview["preview_id"]}).json()["data"]


def test_python_migration_format_docstrings_moves_and_ambiguity():
    old = python_analysis({"a.py": 'def f(x):\n    "说明"\n    return x + 1\n'}, "old")
    new = python_analysis({"a.py": '\n# 注释\ndef f( x ):\n    "说明"\n    return x+1\n'}, "new")
    assert len(learning_mapping(old, new)[0]) == 1
    for files in [{"b.py": 'def f(x):\n    "说明"\n    return x + 1\n'},
                  {"a.py": 'def f(x):\n    "改变说明"\n    return x + 1\n'},
                  {"a.py": 'def f(x): return x+1\ndef f(x): return x+1'}]:
        assert not learning_mapping(old, python_analysis(files, "new"))[0]


def test_typescript_identity_preserves_semantic_newline(tmp_path):
    path = tmp_path / "a.ts"
    def parse(text, version):
        path.write_text(text, encoding="utf-8")
        return typescript_analysis(tmp_path, {"a.ts": text}, version)
    old = parse('export function f(x: number) { return x + 1; }', "old")
    new = parse('\n// 注释\nexport function f (x : number) {\n return x+1;\n}', "new")
    assert len(learning_mapping(old, new)[0]) == 1
    new = parse('export function f(x: number) { return\nx + 1; }', "new2")
    assert not learning_mapping(old, new)[0]


def test_cpp_ast_identity_preserves_format_but_resets_tokens(tmp_path):
    path = tmp_path / "a.cpp"
    def parse(text, version):
        path.write_text(text, encoding="utf-8")
        return cpp_analysis(tmp_path, {"a.cpp": text}, version)
    old = parse("int f(int x) { return x + 1; }", "old")
    same = parse("// comment\nint f (int x) {\n return x+1; /* ordinary */\n}", "same")
    assert len(learning_mapping(old, same)[0]) == 1
    changed = parse("int f(int x) { return x + 2; }", "new")
    assert not learning_mapping(old, changed)[0]


def test_publish_latest_learning_old_source_and_reclaim(tmp_path, monkeypatch):
    root, source = tmp_path / "data", tmp_path / "source"
    source.mkdir()
    text = 'def keep(): return 1\ndef change(): return 2\ndef removed(): return 3\n'
    modified = '# 注释\ndef keep(): return 1\ndef change(): return 9\ndef added(): return 4\n'
    (source / "a.py").write_bytes(text.encode("utf-8"))
    release, entered = threading.Event(), threading.Event()
    async def fixed(self, analysis, files, **callbacks):
        assert files["a.py"] == modified
        entered.set()
        while not release.is_set():
            await asyncio.sleep(.01)
        return architecture(analysis), {"total_tokens": 8}
    monkeypatch.setattr(Provider, "architecture", fixed)
    app = create_app(root, "test", architecture_runner=lambda provider,*args,**kwargs: provider.architecture(*args,**kwargs))
    with TestClient(app) as client:
        login(client, "test")
        imported = client.post("/api/v1/imports", json={"path": str(source)}).json()["data"]
        url = f"/api/v1/projects/{imported['id']}"
        old = wait_status(client, url, "indexed")
        db = Database(root)
        db.update(old["id"], architecture=architecture(python_analysis({"a.py": text}, old["version_id"])).model_dump(), status="ready")
        client.post(url + "/consent", json={"accepted": True})
        (source / "a.py").write_bytes(modified.encode("utf-8"))
        task = prepare(client, url)
        assert client.post(url + "/analyze", json={}).status_code == 403
        (source / "a.py").write_text("raise RuntimeError('禁止执行')", encoding="utf-8")
        assert client.get(url).json()["data"]["version_id"] == old["version_id"]
        client.post(url + "/consent", json={"accepted": True, "version_id": task["version_id"]})
        assert client.post(url + "/analyze", json={}).status_code == 200
        assert entered.wait(3)
        keep = next(entity for entity in old["analysis"]["entities"] if entity["name"] == "keep")
        state = {"version_id": old["version_id"], "state": "mastered", "expected_revision": 0}
        assert client.put(url + "/learning/" + keep["id"], json=state).status_code == 200
        identity = str(uuid.uuid4())
        client.headers["x-client-id"] = identity
        client.post("/api/v1/clients/heartbeat", json={"client_id": identity,
            "version_leases": [{"project_id": old["id"], "version_id": old["version_id"]}]})
        release.set()
        new = wait_status(client, url, "ready")
        assert new["version_id"] == task["version_id"]
        entity = next(entity for entity in new["analysis"]["entities"] if entity["name"] == "keep")
        assert new["learning"][entity["id"]]["state"] == "mastered"
        report = new["migration_report"]
        assert [report[key] for key in ["kept_count", "reset_count", "added_count", "removed_count"]] == [1, 1, 1, 1]
        conflict = client.put(url + "/learning/" + keep["id"], json=state)
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "VERSION_CHANGED"
        old_file_url = f"{url}/files/{old['file_ids']['a.py']}?version_id={old['version_id']}"
        assert client.get(old_file_url).json()["data"]["text"] == text
        app.state.cleanup_versions()
        assert db.version(old["id"], old["version_id"])["status"] == "retired"
        assert client.get(old_file_url, headers={"x-client-id": str(uuid.uuid4())}).status_code == 410
        renew = client.post("/api/v1/clients/heartbeat", json={"client_id": identity,
            "version_leases": [{"project_id": old["id"], "version_id": old["version_id"]}]})
        assert renew.json()["data"]["version_changes"] == [{"project_id": old["id"], "version_id": new["version_id"]}]
        with db.engine.begin() as connection:
            connection.execute(db.versions.update().where(db.versions.c.id == old["version_id"])
                               .values(created_at="2020-01-01T00:00:00+00:00"))
        assert client.get(old_file_url).status_code == 410
        client.post("/api/v1/clients/heartbeat", json={"client_id": identity,
            "version_leases": [{"project_id": new["id"], "version_id": new["version_id"]}]})
        app.state.cleanup_versions()
        assert db.version(old["id"], old["version_id"])["status"] == "purged"
        db.engine.dispose()
    with TestClient(create_app(root, "again")):
        pass
    db = Database(root)
    retired = db.version(old["id"], old["version_id"])
    assert retired["status"] == "purged" and "result" not in retired["value"]
    assert not (root / "projects" / old["id"] / "snapshots" / old["scope"]["snapshot_id"]).exists()
    assert (source / "a.py").read_text(encoding="utf-8") == "raise RuntimeError('禁止执行')"
    db.engine.dispose()


def test_failed_cancelled_candidate_preserves_current(tmp_path, monkeypatch):
    source, root = tmp_path / "source", tmp_path / "data"
    source.mkdir()
    (source / "a.py").write_text("def f(): return 1", encoding="utf-8")
    async def failing(*args, **kwargs):
        raise ValueError("固定失败响应")
    monkeypatch.setattr(Provider, "architecture", failing)
    with TestClient(create_app(root, "test", architecture_runner=lambda provider,*args,**kwargs: provider.architecture(*args,**kwargs))) as client:
        login(client, "test")
        imported = client.post("/api/v1/imports", json={"path": str(source)}).json()["data"]
        url = f"/api/v1/projects/{imported['id']}"
        old = wait_status(client, url, "indexed")
        for attempt in range(2):
            task = prepare(client, url)
            if attempt == 0:
                client.post(url + "/consent", json={"accepted": True, "version_id": task["version_id"]})
                client.post(url + "/analyze", json={})
                wait_status(client, url, "paused")
            assert client.post(f"/api/v1/tasks/{task['task_id']}/cancel", json={}).status_code == 200
            detail = client.get(url).json()["data"]
            assert detail["version_id"] == old["version_id"] and detail["analysis"] == old["analysis"]
            assert detail["candidate"] is None
