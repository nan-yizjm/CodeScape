import threading
from contextlib import contextmanager

from codescape.app import create_app
from codescape.database import Database
from codescape.sessions import migrate_session
from fastapi.testclient import TestClient


@contextmanager
def logged_client(app):
    with TestClient(app) as client:
        csrf = client.post("/api/v1/session/exchange", json={"nonce":"test"}).json()["data"]["csrf_token"]
        client.headers["x-csrf-token"] = csrf
        yield client


def test_full_session_is_durable_bounded_and_revision_checked(tmp_path):
    app = create_app(tmp_path, "test")
    with logged_client(app) as client:
        project = client.get("/api/v1/projects").json()["data"][0]
        detail = client.get(f"/api/v1/projects/{project['id']}").json()["data"]
        entity = detail["analysis"]["entities"][0]
        url = f"/api/v1/projects/{project['id']}/session"
        body = {"version_id":project["version_id"], "focus_id":"render", "expanded_id":"render",
                "show_other_modules":True,"function_origin":{"focus":None,"expanded":None,"siblings":False},
                "panels":[{"kind":"overview","scroll":{"top":37}}, {"kind":"function","entity_id":entity["id"]},
                          {"kind":"source","entity_id":entity["id"],"location":entity["location"],"scroll":{"top":172,"left":41,"line":5}}],
                "viewport":{"root":{"x":-20,"y":73,"zoom":.6}},"panel_width":480,"expanded_evidence":{"r":True},"expected_revision":0}
        first = client.put(url,json=body).json()["data"]
        assert first["revision"] == 1 and first["panels"][2]["scroll"]["left"] == 41
        assert "expected_revision" not in first
        assert client.put(url,json={**body,"focus_id":None}).status_code == 409
        assert client.get(url).json()["data"] == first
        assert client.put(url,json={**body,"expected_revision":1,"panels":[{"kind":"overview"}]*21}).status_code == 422
        assert client.put(url,json={**body,"expected_revision":1,"panels":[{"kind":"function","entity_id":"foreign"}]}).status_code == 422
        assert client.put(url,json={**body,"expected_revision":1,"panels":[{"kind":"source","location":{**entity['location'],"path":"../outside.py"}}]}).status_code == 422
        wrong = client.put(url,json={**body,"version_id":"retired","expected_revision":1})
        assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "VERSION_CHANGED"
        second = client.put(url,json={**body,"expected_revision":1,"show_other_modules":False}).json()["data"]
        assert second["revision"] == 2
    with logged_client(create_app(tmp_path,"test")) as client:
        restored = client.get(url).json()["data"]
        assert restored == second and restored["viewport"]["root"]["zoom"] == .6


def test_two_parallel_session_writers_do_not_overwrite_each_other(tmp_path):
    database = Database(tmp_path)
    database.insert({"id":"p","name":"p","source":"fixture","created_at":"now","status":"ready",
                     "snapshot":{"files":{}},"version_id":"v","demo":0})
    barrier, results = threading.Barrier(2), []
    def write(focus):
        barrier.wait()
        try:
            results.append(database.session("p", {"version_id":"v","focus_id":focus,"expected_revision":0}))
        except ValueError as error:
            results.append(str(error))
    a,b = [threading.Thread(target=write,args=(name,)) for name in ("a","b")]
    a.start()
    b.start()
    a.join()
    b.join()
    assert sum(isinstance(item,dict) for item in results) == 1
    assert "SESSION_CONFLICT" in results and database.session("p")["revision"] == 1
    database.engine.dispose()


def test_session_version_switch_inside_storage_is_rejected(tmp_path):
    database = Database(tmp_path)
    database.insert({"id":"p","name":"p","source":"fixture","created_at":"now","status":"ready",
                     "snapshot":{"files":{}},"version_id":"new","demo":0})
    import pytest
    with pytest.raises(ValueError, match="VERSION_CHANGED"):
        database.session("p",{"version_id":"old","expected_revision":0})
    assert database.session("p") is None
    database.engine.dispose()


def test_migration_uses_nearest_reliable_parent_and_drops_stale_relations():
    old={"architecture":{"modules":[{"id":"outer","parent_id":None},{"id":"middle","parent_id":"outer"},{"id":"leaf","parent_id":"middle"}]},
         "snapshot":{"files":{"a.py":"same","b.py":"old"}},"result":{"entities":[]}}
    new={"snapshot":{"files":{"a.py":"same","b.py":"new"}},"result":{"entities":[{"id":"new-f","location":{"path":"b.py","start_line":20,"end_line":24,"start_byte":40,"end_byte":80}}]}}
    session={"version_id":"old","revision":4,"focus_id":"leaf","expanded_id":"leaf","viewport":{"root":{}},
             "show_other_modules":True,"function_origin":{"focus":"leaf"},"panel_width":460,"expanded_evidence":{"r":True},
             "panels":[{"kind":"overview"},{"kind":"module","module_id":"leaf"},{"kind":"relation","relation_id":"r"},
                       {"kind":"source","location":{"path":"a.py","start_line":1},"scroll":{"top":123}},
                       {"kind":"source","entity_id":"old-f","location":{"path":"b.py","start_line":3},"scroll":{"top":60}}]}
    result=migrate_session(session,old,new,{"middle":"new-middle"},{"old-f":"new-f"},"new")
    assert result["revision"] == 5 and result["focus_id"] == "new-middle" and result["expanded_id"] == "new-middle"
    assert result["viewport"] is None and not result["show_other_modules"] and result["function_origin"] is None
    assert [panel["kind"] for panel in result["panels"]] == ["overview","module","source","source"]
    assert result["panels"][2]["scroll"]["top"] == 123
    assert result["panels"][3]["location"]["start_line"] == 20 and "scroll" not in result["panels"][3]
    assert result["panel_width"] == 460 and session["focus_id"] == "leaf"


def test_navigation_can_locate_same_unique_role_without_inheriting_changed_learning():
    old={"architecture":{"modules":[{"id":"old","name":"输入校验","code_paths":["a.py"],"parent_id":None}]},
         "snapshot":{"files":{}},"result":{"entities":[]}}
    new={"architecture":{"modules":[{"id":"new","name":"输入校验","code_paths":["a.py"],"parent_id":None}]},
         "snapshot":{"files":{}},"result":{"entities":[]}}
    session={"focus_id":"old","revision":2,"panels":[{"kind":"function","entity_id":"changed"}]}
    result=migrate_session(session,old,new,{}, {},"v2")
    assert result["focus_id"] == "new" and result["panels"] == []
    new["architecture"]["modules"].append({**new["architecture"]["modules"][0],"id":"ambiguous"})
    assert migrate_session(session,old,new,{}, {},"v2")["focus_id"] is None
