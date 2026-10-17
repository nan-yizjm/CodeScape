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


def test_cpp_parser_name_correction_requires_identical_snapshot_and_unique_definition(tmp_path):
    files = {'owned.cpp':'namespace api { int target(){return 1;} }\n'}
    (tmp_path/'owned.cpp').write_text(files['owned.cpp'],encoding='utf-8')
    old = cpp_analysis(tmp_path,files,'old')
    new = cpp_analysis(tmp_path,files,'new')
    old.entities[0].qualified_name = 'incorrect_return_type api::target'
    assert not learning_mapping(old,new)[0]
    mapping, report = learning_mapping(old,new,same_snapshot=True)
    assert mapping == {old.entities[0].id:new.entities[0].id}
    assert '解析名称修正' in report['mapping'][0]['reason']
    for field,value in [('implementation_hash','changed'),('signature_key','changed')]:
        changed = new.model_copy(deep=True)
        setattr(changed.entities[0],field,value)
        assert not learning_mapping(old,changed,same_snapshot=True)[0]
    changed = new.model_copy(deep=True)
    changed.entities[0].location.start_byte += 1
    assert not learning_mapping(old,changed,same_snapshot=True)[0]
    ambiguous = old.model_copy(deep=True)
    ambiguous.entities.append(old.entities[0].model_copy(update={'id':'duplicate'}))
    assert not learning_mapping(ambiguous,new,same_snapshot=True)[0]
    # 同一旧身份有多个源码范围，逐项匹配不能复用上一个候选。
    old_multiple = old.model_copy(deep=True)
    new_multiple = new.model_copy(deep=True)
    for analysis,suffix in [(old_multiple,'old2'),(new_multiple,'new2')]:
        other = analysis.entities[0].model_copy(deep=True)
        other.id = suffix
        other.location.start_byte += 100
        other.location.end_byte += 100
        analysis.entities.append(other)
    assert learning_mapping(old_multiple,new_multiple,same_snapshot=True)[0] == {
        old.entities[0].id:new.entities[0].id,'old2':'new2'}


def test_candidate_publication_keeps_learning_when_cpp_name_is_corrected(tmp_path,monkeypatch):
    root,source = tmp_path/'data',tmp_path/'source'
    source.mkdir()
    text = 'namespace api { int target(){return 1;} }\n'
    (source/'owned.cpp').write_bytes(text.encode('utf-8'))
    async def fixed(self,analysis,files,**callbacks):
        assert files['owned.cpp'] == text
        return architecture(analysis),{}
    monkeypatch.setattr(Provider,'architecture',fixed)
    app = create_app(root,'test',architecture_runner=lambda provider,*args,**kwargs:provider.architecture(*args,**kwargs))
    with TestClient(app) as client:
        login(client,'test')
        imported = client.post('/api/v1/imports',json={'path':str(source)}).json()['data']
        url = f"/api/v1/projects/{imported['id']}"
        old = wait_status(client,url,'indexed')
        analysis = cpp_analysis(source,{'owned.cpp':text},old['version_id'])
        entity = analysis.entities[0]
        entity.qualified_name = 'incorrect_return_type api::target'
        db = Database(root)
        db.update(old['id'],result=analysis.model_dump(),architecture=architecture(analysis).model_dump(),status='ready')
        assert client.put(url+'/learning/'+entity.id,json={'version_id':old['version_id'],
            'state':'mastered','expected_revision':0}).status_code == 200
        candidate = prepare(client,url)
        client.post(url+'/consent',json={'accepted':True,'version_id':candidate['version_id']})
        assert client.post(url+'/analyze',json={}).status_code == 200
        result = wait_status(client,url,'ready')
        current = next(item for item in result['analysis']['entities'] if item['qualified_name']=='api::target')
        assert result['learning'][current['id']]['state'] == 'mastered'
        assert result['migration_report']['kept_count'] == 1
        assert (source/'owned.cpp').read_text(encoding='utf-8') == text
        db.engine.dispose()


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
        note = client.put(url + '/notes', json={'anchor_version':old['version_id'], 'anchor_kind':'function',
            'anchor_id':keep['id'], 'text':'发布期间仍可保存的学习笔记', 'expected_revision':0}).json()['data']
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
        # 0.2.0保留最近五版；旧的临时读取接口仍过期，历史接口明确绑定快照。
        assert db.version(old["id"], old["version_id"])["status"] == "retired"
        history_url = f"{url}/versions/{old['version_id']}"
        assert client.get(history_url).json()['data']['readonly']
        migrated_note = next(item for item in client.get(url + '/notes').json()['data'] if item['id'] == note['id'])
        current_keep = next(entity for entity in new['analysis']['entities'] if entity['name'] == 'keep')
        assert migrated_note['anchor_id'] == current_keep['id'] and migrated_note['anchor_version'] == new['version_id']
        assert migrated_note['text'] == note['text']
        removed = client.delete(history_url, headers={'content-type':'application/json',
            'idempotency-key':str(uuid.uuid4())})
        assert removed.status_code == 200
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
