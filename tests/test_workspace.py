import copy
import uuid

import pytest
from codescape.app import create_app
from codescape.workspace import migrate_workspace
from codescape.workspace_api import source_changes
from codescape.workspace_compare import compare_versions
from fastapi.testclient import TestClient


@pytest.fixture
def workspace(tmp_path):
    app = create_app(tmp_path / 'data', 'workspace-test')
    with TestClient(app) as client:
        token = client.post('/api/v1/session/exchange', json={'nonce': 'workspace-test'}).json()['data']['csrf_token']
        client.headers['x-csrf-token'] = token
        client.headers['content-type'] = 'application/json'
        project = client.get('/api/v1/projects').json()['data'][0]
        project = client.get('/api/v1/projects/' + project['id']).json()['data']
        yield client, app.state.workspace, project


def url(project):
    return f"/api/v1/projects/{project['id']}/versions/{project['version_id']}"


def note_payload(project, **values):
    entity = next(e for e in project['analysis']['entities'] if e['visible'])
    return {'anchor_version': project['version_id'], 'anchor_kind': 'function', 'anchor_id': entity['id'],
            'text': '先理解输入校验，then transform', 'expected_revision': 0, **values}


def split_modules(project):
    modules = copy.deepcopy(project['architecture']['modules'])
    module = next(m for m in modules if len(m['entity_ids']) > 1)
    left, right = copy.deepcopy(module), copy.deepcopy(module)
    left.update(id='manual-left', parent_id=module['id'], name='输入准备', entity_ids=module['entity_ids'][:1])
    right.update(id='manual-right', parent_id=module['id'], name='结果处理', entity_ids=module['entity_ids'][1:])
    module['entity_ids'] = []
    modules.extend([left, right])
    return modules


def test_search_notes_guide_and_conflicts(workspace):
    client, service, project = workspace
    path = f"/api/v1/projects/{project['id']}/notes"
    payload = note_payload(project)
    saved = client.put(path, json=payload).json()['data']
    assert saved['revision'] == 1
    assert client.get(url(project) + '/search', params={'q': '输入校验'}).json()['data']['items'][0]['kind'] == 'note'
    result = client.get(url(project) + '/search', params={'q': 'ENGINE.PY', 'limit': 1}).json()['data']
    assert result['total'] >= 1 and len(result['items']) == 1
    assert client.get(url(project) + '/search', params={'q': '输入校验', 'offset': 1}).json()['data']['items'] == []
    assert client.put(path, json={**payload, 'id': saved['id']}).status_code == 409
    changed = client.put(path, json={**payload, 'id': saved['id'], 'expected_revision': 1, 'text': '<script>原文</script>'})
    assert changed.status_code == 200
    assert service.notes(project['id'])[0]['text'] == '<script>原文</script>'
    guide = client.get(url(project) + '/guide').json()['data']
    assert guide['items'][0]['kind'] == 'overview'
    assert '不代表真实程序入口' in guide['message']
    assert all(item.get('location') for item in guide['items'][1:])


def test_architecture_preview_apply_undo_redo_and_original_preserved(workspace):
    client, service, project = workspace
    payload = {'action': 'apply', 'modules': split_modules(project), 'expected_revision': 0}
    original = service.db.get(project['id'])['architecture']
    assert client.post(url(project) + '/architecture/preview', json=payload).status_code == 200
    assert service.doc(project['id'], project['version_id'], 'architecture') is None
    result = client.put(url(project) + '/architecture', json=payload)
    assert result.status_code == 200
    assert service.db.get(project['id'])['architecture'] == original
    detail = client.get(url(project)).json()['data']
    assert len(detail['architecture']['modules']) == len(project['architecture']['modules']) + 2
    assert detail['manual_edit']['can_undo'] and detail['raw_architecture'] == original
    assert client.put(url(project) + '/architecture', json=payload).status_code == 409
    assert client.put(url(project) + '/architecture', json={'action': 'undo', 'expected_revision': 1}).status_code == 200
    assert client.get(url(project)).json()['data']['architecture'] == original
    assert client.put(url(project) + '/architecture', json={'action': 'redo', 'expected_revision': 2}).status_code == 200
    assert client.put(url(project) + '/architecture', json={'action': 'restore', 'expected_revision': 3}).status_code == 200
    assert client.get(url(project)).json()['data']['architecture'] == original


@pytest.mark.parametrize('damage', ['cycle', 'missing', 'duplicate', 'foreign', 'parent_member'])
def test_edit_rejects_invalid_trees(workspace, damage):
    client, _, project = workspace
    modules = split_modules(project)
    if damage == 'cycle':
        modules[0]['parent_id'] = modules[0]['id']
    elif damage == 'missing':
        modules[-1]['entity_ids'] = []
    elif damage == 'duplicate':
        modules[-1]['entity_ids'].extend(modules[-2]['entity_ids'])
    elif damage == 'foreign':
        modules[-1]['code_paths'] = ['../private.py']
    else:
        modules[0]['entity_ids'] = modules[-1]['entity_ids']
    assert client.post(url(project) + '/architecture/preview', json={'action': 'apply', 'modules': modules}).status_code == 409


def history(service, project, count=1):
    original = service.db.get(project['id'])
    ids = []
    for index in range(count):
        vid = str(uuid.uuid4())
        old = dict(copy.deepcopy(original), version_id=vid, learning={})
        with service.db.engine.begin() as connection:
            connection.execute(service.db.versions.insert().values(id=vid, project_id=project['id'], status='retired',
                created_at=f'2026-10-{index + 1:02}T00:00:00+00:00', value=old))
        ids.append(vid)
    return ids


def test_history_scoped_files_sessions_and_leases(workspace):
    client, service, project = workspace
    old = history(service, project)[0]
    old_url = url(project).replace(project['version_id'], old)
    detail = client.get(old_url).json()['data']
    assert detail['readonly'] and detail['version_id'] == old
    file_id = next(iter(detail['file_ids'].values()))
    response = client.get(old_url + '/files/' + file_id)
    assert response.status_code == 200 and response.json()['data']['version_id'] == old
    assert client.get(url(project) + '/files/' + file_id).status_code == 404
    for slot, focus in [('compare-left', 'left'), ('compare-right', 'right')]:
        value = {'version_id': old, 'focus_id': focus, 'expected_revision': 0}
        assert client.put(old_url + '/workspaces/' + slot, json=value).status_code == 200
        assert client.get(old_url + '/workspaces/' + slot).json()['data']['focus_id'] == focus
    lease = {'view_id': str(uuid.uuid4())}
    assert client.post(old_url + '/lease', json=lease).status_code == 200
    assert client.delete(old_url).status_code == 409
    client.post(old_url + '/lease', json={**lease, 'active': False})
    assert client.delete(url(project)).status_code == 409
    assert client.put(old_url + '/architecture', json={'action': 'restore'}).status_code == 409


def test_retention_five_pins_and_shared_snapshot_safety(workspace):
    client, service, project = workspace
    ids = history(service, project, 7)
    oldest = ids[0]
    old_url = url(project).replace(project['version_id'], oldest)
    assert client.put(old_url + '/pin', json={'pinned': True}).status_code == 200
    client.app.state.cleanup_versions()
    assert service.db.version(project['id'], oldest)['status'] == 'retired'
    assert len(service.versions(project['id'])) == 6
    assert service.snapshot_path(service.db.get(project['id'])).is_dir()
    assert client.delete(old_url).status_code == 409
    assert client.put(old_url + '/pin', json={'pinned': False, 'expected_revision': 1}).status_code == 200
    assert client.delete(old_url + '?expected_revision=2').status_code == 200
    assert service.snapshot_path(service.db.get(project['id'])).is_dir()


def test_cross_project_and_corrupt_history_are_rejected(workspace):
    client, service, project = workspace
    old = history(service, project)[0]
    assert client.get(url(project).replace(project['id'], str(uuid.uuid4()))).status_code == 404
    row = service.db.version(project['id'], old)
    row['value']['snapshot']['id'] = str(uuid.uuid4())
    with service.db.engine.begin() as connection:
        connection.execute(service.db.versions.update().where(service.db.versions.c.id == old).values(value=row['value']))
    response = client.get(url(project).replace(project['version_id'], old))
    assert response.status_code == 409 and response.json()['error']['code'] == 'HISTORY_INCOMPLETE'


def test_notes_migrate_changed_implementations_and_keep_unmatched(workspace):
    client, service, project = workspace
    saved = client.put(f"/api/v1/projects/{project['id']}/notes", json=note_payload(project)).json()['data']
    old = service.db.get(project['id'])
    new = copy.deepcopy(old)
    new['version_id'] = str(uuid.uuid4())
    for entity in new['result']['entities']:
        previous = entity['id']
        entity['id'] += '-new'
        entity['implementation_hash'] = 'changed'
        for module in new['architecture']['modules']:
            module['entity_ids'] = [entity['id'] if eid == previous else eid for eid in module['entity_ids']]
    with service.db.engine.begin() as connection:
        migrate_workspace(connection, service.db.workspace, old, new)
    note = service.notes(project['id'])[0]
    assert note['id'] == saved['id'] and note['text'] == saved['text']
    assert note['anchor_id'].endswith('-new') and note['implementation_changed']
    missing = copy.deepcopy(new)
    missing['version_id'] = str(uuid.uuid4())
    missing['result']['entities'] = []
    with service.db.engine.begin() as connection:
        migrate_workspace(connection, service.db.workspace, new, missing)
    assert service.notes(project['id'])[0]['status'] == 'unlinked'


def test_change_detection_is_read_only_and_respects_scope(workspace, tmp_path):
    _, service, project = workspace
    original = service.db.get(project['id'])
    from codescape.snapshots import create_snapshot
    source = tmp_path / 'owned-source'
    source.mkdir()
    (source / 'a.py').write_text('def f(): return 1', encoding='utf-8')
    manifest = create_snapshot(source, tmp_path / 'owned-data')
    original['snapshot'] = manifest
    assert source_changes(original)['status'] == 'unchanged'
    (source / 'tests').mkdir()
    (source / 'tests/new.py').write_text('raise RuntimeError("不能执行")', encoding='utf-8')
    (source / '.env.py').write_text('不应读取', encoding='utf-8')
    assert source_changes(original)['status'] == 'unchanged'
    (source / 'a.py').write_text('def f(): return 2', encoding='utf-8')
    assert source_changes(original)['changed'] == ['a.py']
    assert (source / 'a.py').read_text(encoding='utf-8') == 'def f(): return 2'
    original['snapshot']['origin'] = {'kind': 'git', 'url': 'https://github.com/example/owned', 'commit': 'a' * 40}
    assert source_changes(original)['status'] == 'manual'


def test_compare_implementation_membership_and_no_name_guessing(workspace):
    _, service, project = workspace
    old = service.db.get(project['id'])
    new = copy.deepcopy(old)
    new['version_id'] = str(uuid.uuid4())
    target = next(e for e in new['result']['entities'] if e['visible'])
    target['implementation_hash'] = 'changed'
    for module in new['architecture']['modules']:
        if target['id'] in module['entity_ids']:
            module['name'] = '调整后的职责'
    result = compare_versions(old, new)
    assert len(result['functions']['changed']) == 1
    assert result['modules']['membership_changes']
    new['result']['entities'].append(copy.deepcopy(target))
    result = compare_versions(old, new)
    assert result['functions']['ambiguous'] and not result['functions']['changed']
