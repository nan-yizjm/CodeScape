import json
import os
import time
from pathlib import Path

import pytest
from codescape.app import create_app
from codescape.database import Database, now
from codescape.models import Architecture, Module, Overview
from codescape.publication import load_output, prepare_output
from fastapi.testclient import TestClient


def setup_publication(root, candidate=False):
    db = Database(root)
    project = {"id": "p", "name": "发布验证", "source": "fixture", "created_at": now(), "status": "analyzing",
               "snapshot": {"files": {}, "hash": "snapshot"}, "version_id": "old", "result": {"entities": [], "calls": []}}
    db.insert(project)
    job = {"id": "job", "project_id": "p", "kind": "reanalysis" if candidate else "architecture", "status": "running",
           "position": 0, "version_id": "old", "created_at": now(), "updated_at": now(), "detail": "验证"}
    db.insert_job(job)
    version = "new" if candidate else "old"
    if candidate:
        db.insert_candidate("p", version, "old", {"snapshot": project["snapshot"], "result": project["result"]})
    architecture = Architecture(modules=[Module(id="empty", name="占位", summary="没有可读源码")],
        overview=Overview(introduction="验证发布", capabilities=["事务验证"], principle="固定输入", limitations=[])).model_dump()
    descriptor = prepare_output(root, version, "snapshot", "test", project["result"], architecture, {})
    db.prepare_publication("p", version, "job", descriptor, candidate=candidate)
    return db, version, architecture, descriptor


def test_published_output_digest_also_binds_source_generation_origins(tmp_path):
    origins = ['source-old', 'source-current']
    descriptor = prepare_output(tmp_path, 'v', 'snapshot', 'processing-current', {}, {}, {},
                                source_generation_versions=origins)
    assert load_output(tmp_path, 'v', descriptor)['source_generation_versions'] == sorted(origins)
    with pytest.raises(ValueError, match='缺失或损坏'):
        load_output(tmp_path, 'v', {**descriptor, 'source_generation_versions': ['source-current']})


@pytest.mark.parametrize("candidate", [False, True])
def test_publication_commits_output_project_job_and_event_together(tmp_path, candidate):
    db, version, architecture, _ = setup_publication(tmp_path, candidate)
    assert db.candidate("p") is not None if candidate else db.candidate("p") is None
    if candidate:
        db.publish_candidate("p", version, "job", architecture)
    else:
        db.publish_initial("p", version, "job", architecture)
    assert db.get("p")["version_id"] == version and db.get("p")["status"] == "ready"
    assert db.version("p", version)["status"] == "published"
    assert db.get_job("job")["status"] == "completed"
    assert db.analysis_progress("p")["phase"] == "completed"
    with db.engine.connect() as connection:
        assert connection.execute(db.operations.select()).mappings().one()["detail"] == version
    db.recover_interrupted()
    assert db.get("p")["status"] == "ready" and db.get_job("job")["status"] == "completed"
    db.engine.dispose()


@pytest.mark.parametrize("candidate", [False, True])
def test_failed_publication_transaction_rolls_back_all_updates(tmp_path, monkeypatch, candidate):
    db, version, architecture, descriptor = setup_publication(tmp_path, candidate)
    before = db.get("p")
    original = db._publication_event
    def crash(*args):
        original(*args)
        raise RuntimeError("发布提交前故障注入")
    monkeypatch.setattr(db, "_publication_event", crash)
    with pytest.raises(RuntimeError, match="故障注入"):
        (db.publish_candidate if candidate else db.publish_initial)("p", version, "job", architecture)
    assert db.get("p") == before
    assert db.get_job("job")["status"] == "running"
    assert db.version("p", version)["status"] == "draft"
    with db.engine.connect() as connection:
        assert connection.execute(db.operations.select()).first() is None
    assert load_output(tmp_path, version, descriptor)["architecture"] == architecture
    db.recover_interrupted()
    assert db.get_job("job")["status"] == "paused"
    db.engine.dispose()


@pytest.mark.parametrize("candidate", [False, True])
@pytest.mark.parametrize("mutation", ["stopped", "deleting", "other_project", "other_version", "wrong_kind"])
def test_publication_rechecks_task_ownership_and_lifecycle(tmp_path, candidate, mutation):
    db, version, architecture, _ = setup_publication(tmp_path, candidate)
    if mutation == "deleting":
        db.update("p", lifecycle="deleting")
    else:
        db.update_job("job", **{"stopped": {"status": "cancelled"}, "other_project": {"project_id": "q"},
            "other_version": {"version_id": "stale"}, "wrong_kind": {"kind": "index"}}[mutation])
    with pytest.raises(ValueError, match="未发布"):
        (db.publish_candidate if candidate else db.publish_initial)("p", version, "job", architecture)
    assert db.get("p")["architecture"] is None and db.version("p", version)["status"] == "draft"
    db.engine.dispose()


def test_prepared_output_refuses_corruption_and_path_traversal(tmp_path):
    descriptor = prepare_output(tmp_path, "v", "hash", "pipeline", {}, {}, {})
    (tmp_path / "version-v.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="未重新请求模型"):
        load_output(tmp_path, "v", descriptor)
    with pytest.raises(ValueError, match="标识无效"):
        prepare_output(tmp_path, "../v", "hash", "pipeline", {}, {}, {})


def test_failed_disk_flush_does_not_replace_verified_output(tmp_path, monkeypatch):
    descriptor = prepare_output(tmp_path, "v", "hash", "pipeline", {}, {"saved": True}, {})
    def disk_error(_):
        raise OSError("应用自有磁盘故障注入")
    monkeypatch.setattr(os, "fsync", disk_error)
    with pytest.raises(OSError):
        prepare_output(tmp_path, "v", "hash", "pipeline", {}, {"saved": False}, {})
    assert load_output(tmp_path, "v", descriptor)["architecture"] == {"saved": True}


def test_prepared_receiver_identity_excludes_secrets_and_detects_model_mismatch(tmp_path):
    config = {'base_url': 'https://relay.invalid/v1', 'model': '固定模型',
              'key': 'private-credential-marker', 'has_key': True}
    descriptor = prepare_output(tmp_path, 'v', 'hash', 'pipeline', {}, {}, {}, provider_config=config)
    value = load_output(tmp_path, 'v', descriptor)
    assert value['provider_config'] == {'base_url': config['base_url'], 'model': config['model']}
    assert 'private-credential-marker' not in (tmp_path / 'version-v.json').read_text(encoding='utf-8')
    assert 'key' not in json.dumps(descriptor)
    changed = {**descriptor, 'provider_config': {**descriptor['provider_config'], 'model': '另一个模型'}}
    with pytest.raises(ValueError, match='缺失或损坏'):
        load_output(tmp_path, 'v', changed)


def wait(client, url, expected):
    for _ in range(250):
        value = client.get(url).json()["data"]
        if value["status"] == expected:
            return value
        time.sleep(.02)
    raise AssertionError(value)


@pytest.mark.parametrize("failure", ["transaction", "corrupt", "cleanup", "receiver", "model"])
def test_explicit_resume_reuses_prepared_output_without_new_model_request(tmp_path, monkeypatch, failure):
    source = tmp_path / "source"
    source.mkdir()
    (source / "owned.py").write_bytes(b"def owned(): return 1\n")
    runs = []
    resumed_counts = []
    async def fixed(provider, analysis, files, **callbacks):
        runs.append(files)
        resumed_counts.append(len(callbacks['completed_batches']))
        arch = Architecture(modules=[Module(id="leaf", name="验证", summary="返回固定值", code_paths=["owned.py"],
            entity_ids=[entity.id for entity in analysis.entities if entity.visible])],
            overview=Overview(introduction="固定验证", capabilities=["返回固定值"], principle="读取输入", limitations=[]))
        await callbacks["on_batch"](1, 1, arch)
        return arch, {"total_tokens": 7}
    original = Database.publish_initial
    failures = []
    def fail_once(self, *args):
        if not failures:
            failures.append(True)
            raise RuntimeError("发布事务故障注入")
        return original(self, *args)
    monkeypatch.setattr(Database, "publish_initial", fail_once)
    if failure == "cleanup":
        unlink = Path.unlink
        def cleanup_error(path, *args, **kwargs):
            if path.name == "architecture-checkpoint.json":
                raise OSError("发布后清理故障注入")
            return unlink(path, *args, **kwargs)
        monkeypatch.setattr(Path, "unlink", cleanup_error)
    root = tmp_path / "data"
    app = create_app(root, "test", architecture_runner=fixed)
    with TestClient(app) as client:
        client.headers["x-csrf-token"] = client.post("/api/v1/session/exchange", json={"nonce": "test"}).json()["data"]["csrf_token"]
        assert client.put('/api/v1/settings/ai', json={'base_url': 'https://api.deepseek.com',
                                                     'model': '固定模型', 'key': None}).status_code == 200
        imported = client.post("/api/v1/imports", json={"path": str(source)}).json()["data"]
        url = "/api/v1/projects/" + imported["id"]
        old = wait(client, url, "indexed")
        client.post(url + "/consent", json={"accepted": True})
        started = client.post(url + "/analyze", json={}).json()["data"]
        paused = wait(client, url, "paused")
        assert paused["architecture"] is None and len(runs) == 1
        assert "发布事务故障注入" in paused["error"]
    # Restart never resumes paid work; the prepared artifact remains bound to its digest.
    if failure == "corrupt":
        (root / "projects" / old["id"] / f"version-{old['version_id']}.json").write_bytes(b"{}")
    (source / "owned.py").write_bytes(b"raise RuntimeError('not executed')\n")
    with TestClient(create_app(root, "again", architecture_runner=fixed)) as client:
        client.headers["x-csrf-token"] = client.post("/api/v1/session/exchange", json={"nonce": "again"}).json()["data"]["csrf_token"]
        assert client.get(url).json()["data"]["status"] == "paused" and len(runs) == 1
        changed = failure in {'receiver', 'model'}
        if changed:
            config = client.get('/api/v1/settings/ai').json()['data']
            config = {'base_url': 'https://relay.invalid/v1' if failure == 'receiver' else config['base_url'],
                      'model': '另一个模型' if failure == 'model' else config['model'], 'key': None}
            assert client.put('/api/v1/settings/ai', json=config).status_code == 200
            assert client.post(url + '/consent', json={'accepted': True}).status_code == 200
        assert client.post("/api/v1/tasks/" + started["task_id"] + "/start", json={}).status_code == 200
        result = wait(client, url, "paused" if failure == "corrupt" else "ready")
        assert len(runs) == (2 if changed else 1)
        assert resumed_counts == ([0, 0] if changed else [0])
        if failure == "corrupt":
            assert "损坏" in result["error"]
        else:
            assert result["architecture"]["modules"][0]["summary"] == "返回固定值"
            db = Database(root)
            assert db.get_job(started["task_id"])["status"] == "completed"
            assert db.version(old["id"], old["version_id"])["status"] == "published"
            output = root / "projects" / old["id"] / f"version-{old['version_id']}.json"
            assert json.loads(output.read_text(encoding="utf-8"))["usage"]["total_tokens"] == 7
            db.engine.dispose()
