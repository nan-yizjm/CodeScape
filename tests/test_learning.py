from codescape.analyzers import python_analysis
from codescape.app import create_app
from codescape.database import Database
from codescape.learning import learning_units, module_learning_mapping
from codescape.models import Analysis, Architecture, Module, Overview
from codescape.provider import validate_architecture
from fastapi.testclient import TestClient


def module(module_id="configuration", **kwargs):
    return Module(id=module_id, name="配置读取", summary="声明默认设置与运行参数。", code_paths=["config.py"], **kwargs)


def architecture(modules):
    return Architecture(modules=modules, overview=Overview(introduction="配置样例。", capabilities=["声明配置"],
                        principle="提供运行配置。", limitations=[]))


def test_learning_units_exclude_unknown_empty_and_parent():
    result = python_analysis({"config.py": "VALUE = 1\n", "comment.py": "# no implementation\n"}, "v")
    files = {"config.py": {"size": 10}, "comment.py": {"size": 20}, "empty.py": {"size": 0}}
    modules = [module("parent"), module("leaf", parent_id="parent"), module("unknown", incomplete=True),
               Module(id="comments", name="注释", summary="只有注释", code_paths=["comment.py"]),
               Module(id="empty", name="空", summary="没有内容", code_paths=["empty.py"])]
    units = learning_units(result, architecture(modules), files)
    assert units == [{"id": "module:leaf", "kind": "module", "source_id": "leaf"}]
    assert not learning_units(Analysis(), architecture(modules), files)


def test_functionless_module_migration_format_content_and_responsibility():
    old = python_analysis({"config.py": "VALUE=1\n"}, "old")
    new = python_analysis({"config.py": "# 注释\nVALUE = 1\n"}, "new")
    before, after = architecture([module()]), architecture([module("new-configuration")])
    old_snapshot = {"files": {"config.py": {"hash": "old", "size": 10}}}
    new_snapshot = {"files": {"config.py": {"hash": "new", "size": 20}}}
    mapping = module_learning_mapping(before, after, old, new, old_snapshot, new_snapshot)
    assert mapping == {"module:configuration": "module:new-configuration"}
    changed = python_analysis({"config.py": "VALUE=2\n"}, "changed")
    assert not module_learning_mapping(before, after, old, changed, old_snapshot, new_snapshot)
    after.modules[0].summary = "职责改变"
    assert not module_learning_mapping(before, after, old, new, old_snapshot, new_snapshot)
    before = architecture([module("parent"), module(parent_id="parent")])
    after = architecture([module("new-parent"), module("new-configuration", parent_id="new-parent")])
    assert module_learning_mapping(before, after, old, new, old_snapshot, new_snapshot)
    after.modules[0].summary = "重新分组为另一种职责"
    assert not module_learning_mapping(before, after, old, new, old_snapshot, new_snapshot)


def test_module_learning_api_denominator_revision_and_readable_source(tmp_path):
    root = tmp_path / "data"
    with TestClient(create_app(root, "test")) as client:
        client.headers["x-csrf-token"] = client.post("/api/v1/session/exchange", json={"nonce": "test"}).json()["data"]["csrf_token"]
        db = Database(root)
        project = db.list_projects()[0]
        # Application-owned synthetic snapshot, never execute its top-level code.
        path = root / "projects" / project["id"] / "snapshots" / project["snapshot"]["id"] / "config.py"
        path.write_bytes(b"VALUE = 1\n")
        from codescape.analyzers import digest
        manifest = dict(project["snapshot"], files={"config.py": {"hash": digest(path.read_bytes()), "size": path.stat().st_size}})
        result = python_analysis({"config.py": "VALUE = 1\n"}, project["version_id"])
        arch = architecture([module("parent"), module(parent_id="parent"), module("unknown", incomplete=True)])
        validate_architecture(arch, result, {"config.py"}, expected_entity_ids=set())
        db.update(project["id"], result=result.model_dump(), architecture=arch.model_dump(), snapshot=manifest)
        url = f"/api/v1/projects/{project['id']}"
        detail = client.get(url).json()["data"]
        assert detail["function_count"] == 0 and detail["progress"] == {"mastered": 0, "total": 1}
        payload = {"version_id": project["version_id"], "state": "mastered", "expected_revision": 0}
        assert client.put(url + "/learning/module:configuration", json=payload).status_code == 200
        assert client.put(url + "/learning/module:configuration", json=payload).status_code == 409
        assert client.put(url + "/learning/module:parent", json=payload).status_code == 422
        assert client.put(url + "/learning/module:unknown", json=payload).status_code == 422
        assert client.get(url).json()["data"]["progress"] == {"mastered": 1, "total": 1}
        file_id = detail["file_ids"]["config.py"]
        assert client.get(f"{url}/files/{file_id}?version_id={project['version_id']}").json()["data"]["text"] == "VALUE = 1\n"
        db.update(project["id"], architecture=architecture([module("unknown", incomplete=True)]).model_dump())
        assert client.get(url).json()["data"]["progress"] == {"mastered": 0, "total": 0}
        db.engine.dispose()
