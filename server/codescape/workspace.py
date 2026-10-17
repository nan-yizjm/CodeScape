"""本地学习工作区：原始分析保持不可变，所有编辑独立保存。"""
from __future__ import annotations

import copy
import json
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from .migration import identity
from .models import Analysis, Module
from .snapshots import read_snapshot


class WorkspaceError(ValueError):
    def __init__(self, message, code="WORKSPACE_INVALID", status=409):
        super().__init__(message)
        self.code, self.status = code, status


def stamp():
    return datetime.now(UTC).isoformat()


def criteria(table, project, version, kind, key):
    return (table.c.project_id == project, table.c.version_id == version,
            table.c.kind == kind, table.c.key == key)


def document(connection, table, project, version, kind, key):
    row = connection.execute(select(table).where(*criteria(table, project, version, kind, key))).mappings().first()
    return {**copy.deepcopy(row['value']), 'revision': row['revision']} if row else None


def put_document(connection, table, project, version, kind, key, value, expected):
    old = document(connection, table, project, version, kind, key)
    if (old['revision'] if old else 0) != expected:
        raise WorkspaceError('另一页面已更新这项内容，请保留编辑文本并重新载入。', 'WORKSPACE_CONFLICT')
    payload = {k: v for k, v in value.items() if k != 'revision'}
    values = {'revision': expected + 1, 'value': payload}
    where = criteria(table, project, version, kind, key)
    if old:
        connection.execute(table.update().where(*where).values(**values))
    else:
        connection.execute(table.insert().values(project_id=project, version_id=version, kind=kind, key=key, **values))
    return dict(payload, revision=expected + 1)


def validate_modules(project, modules):
    if not project.get('architecture'):
        raise WorkspaceError('架构分析尚未完成，不能编辑。')
    try:
        parsed = [Module.model_validate(item).model_dump() for item in modules]
    except ValueError:
        raise WorkspaceError('模块字段格式无效。', status=422) from None
    if not parsed:
        raise WorkspaceError('架构至少保留一个模块。')
    pool = {item['id']: item for item in parsed}
    if len(pool) != len(parsed) or any(not item['id'] or len(item['id']) > 2048 for item in parsed):
        raise WorkspaceError('模块标识为空或重复。')
    expected = Counter(eid for module in project['architecture']['modules'] for eid in module['entity_ids'])
    actual = Counter(eid for module in parsed for eid in module['entity_ids'])
    if actual != expected or any(n != 1 for n in actual.values()):
        raise WorkspaceError('每个已有函数必须恰好归属一个模块，不能遗漏或重复。')
    known_paths = set(project['snapshot']['files'])
    entities = {item['id']: item for item in (project.get('result') or {}).get('entities', [])}
    for module in parsed:
        if not module['name'].strip() or len(module['name']) > 80 or len(module['summary']) > 600:
            raise WorkspaceError('模块名称需为1至80字，说明最多600字。')
        seen, parent = {module['id']}, module['parent_id']
        while parent:
            if parent in seen or parent not in pool:
                raise WorkspaceError('模块层级存在循环或无效父模块。')
            seen.add(parent)
            parent = pool[parent]['parent_id']
        children = [item for item in parsed if item['parent_id'] == module['id']]
        if children and module['entity_ids']:
            raise WorkspaceError('父模块不能同时直接持有函数，请先建立叶级模块。')
        if set(module['code_paths']) - known_paths:
            raise WorkspaceError('模块引用了快照以外的文件。')
        module['name'] = module['name'].strip()
        module['code_paths'] = sorted(set(module['code_paths']) | {
            entities[eid]['location']['path'] for eid in module['entity_ids']})
        if not children and not module['code_paths']:
            raise WorkspaceError('不能建立没有源码依据的空模块。')
    # Recompute ancestors rather than keeping paths from removed members.
    def paths(identifier):
        item = pool[identifier]
        children = [m for m in parsed if m['parent_id'] == identifier]
        if children:
            item['code_paths'] = sorted({path for child in children for path in paths(child['id'])})
        return item['code_paths']
    for module in parsed:
        paths(module['id'])
    return parsed


def entity_mapping(old, new):
    previous, following = defaultdict(list), defaultdict(list)
    for entity in Analysis.model_validate(old.get('result') or {}).entities:
        if entity.visible:
            previous[identity(entity)].append(entity)
    for entity in Analysis.model_validate(new.get('result') or {}).entities:
        if entity.visible:
            following[identity(entity)].append(entity)
    return {before[0].id: following[key][0].id for key, before in previous.items()
            if len(before) == len(following.get(key, [])) == 1}


def migrate_workspace(connection, table, old, new):
    """Called inside publication's transaction, so edits cannot race publication."""
    pid, old_id, new_id = old['id'], old['version_id'], new['version_id']
    mapping = entity_mapping(old, new)
    old_raw = (old.get('architecture') or {}).get('modules', [])
    new_raw = (new.get('architecture') or {}).get('modules', [])
    edit = document(connection, table, pid, old_id, 'architecture', 'main')
    old_modules = edit['history'][edit['position']] if edit else old_raw
    new_by_members = defaultdict(list)
    for module in new_raw:
        if module['entity_ids']:
            new_by_members[frozenset(module['entity_ids'])].append(module['id'])
    module_map = {}
    for module in old_modules:
        members = module['entity_ids']
        if members and all(eid in mapping for eid in members):
            matches = new_by_members[frozenset(mapping[eid] for eid in members)]
            if len(matches) == 1:
                module_map[module['id']] = matches[0]
    if edit:
        try:
            edited = copy.deepcopy(old_modules)
            if any(eid not in mapping for m in edited for eid in m['entity_ids']):
                raise WorkspaceError('部分函数身份不能唯一匹配。')
            for module in edited:
                module['entity_ids'] = [mapping[eid] for eid in module['entity_ids']]
                module['code_paths'] = [p for p in module['code_paths'] if p in new['snapshot']['files']]
            edited = validate_modules(new, edited)
            put_document(connection, table, pid, new_id, 'architecture', 'main',
                {'history': [new_raw, edited], 'position': 1, 'events': ['自动迁移人工调整'],
                 'conflicts': edit.get('conflicts', [])}, 0)
            module_map.update({m['id']: m['id'] for m in edited})
        except (WorkspaceError, ValueError, KeyError):
            # Keep uniquely matched textual edits even if structural edits need review.
            patched = copy.deepcopy(new_raw)
            old_by_id = {m['id']: m for m in old_raw}
            raw_map = {}
            for previous in old_raw:
                members = previous['entity_ids']
                if members and all(eid in mapping for eid in members):
                    matches = new_by_members[frozenset(mapping[eid] for eid in members)]
                    if len(matches) == 1:
                        raw_map[previous['id']] = matches[0]
            for modified in old_modules:
                before = old_by_id.get(modified['id'])
                target = next((m for m in patched if m['id'] == raw_map.get(modified['id'])), None)
                if before and target:
                    for field in ['name', 'summary']:
                        if modified[field] != before[field]:
                            target[field] = modified[field]
            histories = [new_raw, patched] if patched != new_raw else [new_raw]
            put_document(connection, table, pid, new_id, 'architecture', 'main',
                {'history': histories, 'position': len(histories) - 1, 'events': ['可靠文字修改已迁移'],
                 'conflicts': edit.get('conflicts', []) + [{'from_version_id': old_id, 'message': '已保留可靠匹配的文字修改；其余人工架构未完整匹配，原版本仍保留，可预览后重新调整。'}]}, 0)
    rows = connection.execute(select(table).where(table.c.project_id == pid, table.c.kind == 'note')).mappings().all()
    old_entities = {e['id']: e for e in (old.get('result') or {}).get('entities', [])}
    new_entities = {e['id']: e for e in (new.get('result') or {}).get('entities', [])}
    for row in rows:
        note = copy.deepcopy(row['value'])
        if note.get('anchor_version') != old_id:
            continue
        anchor = note['anchor_id']
        target = mapping.get(anchor) if note['anchor_kind'] == 'function' else module_map.get(anchor)
        note.setdefault('previous_anchors', []).append({
            'version_id': old_id, 'id': anchor, 'status': note.get('status', 'linked'), 'text': note['text']})
        if target:
            note.update(anchor_version=new_id, anchor_id=target, status='linked')
            note['implementation_changed'] = bool(note.get('implementation_changed') or (note['anchor_kind'] == 'function' and
                old_entities[anchor]['implementation_hash'] != new_entities[target]['implementation_hash']))
        else:
            note['status'] = 'unlinked'
        put_document(connection, table, pid, '', 'note', row['key'], note, row['revision'])
    from .sessions import migrate_session
    old_effective = dict(old, architecture=dict(old['architecture'], modules=old_modules)) if old.get('architecture') else old
    new_edit = document(connection, table, pid, new_id, 'architecture', 'main')
    new_effective = dict(new, architecture=dict(new['architecture'], modules=new_edit['history'][new_edit['position']])) if new_edit else new
    sessions = connection.execute(select(table).where(table.c.project_id == pid,
        table.c.version_id == old_id, table.c.kind == 'session')).mappings().all()
    for row in sessions:
        value = migrate_session(row['value'], old_effective, new_effective, module_map, mapping, new_id)
        put_document(connection, table, pid, new_id, 'session', row['key'], value, 0)


class WorkspaceService:
    def __init__(self, root, database, reads):
        self.root, self.db, self.reads = root, database, reads

    def doc(self, pid, vid, kind, key='main'):
        with self.db.engine.connect() as connection:
            return document(connection, self.db.workspace, pid, vid, kind, key)

    def write(self, pid, vid, kind, key, value, expected):
        with self.db.engine.begin() as connection:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
            project = connection.execute(select(self.db.projects).where(self.db.projects.c.id == pid)).mappings().first()
            if not project or project['lifecycle'] != 'active':
                raise WorkspaceError('项目不存在或正在删除。', status=410)
            if kind in {'note', 'architecture'} and vid and project['version_id'] != vid:
                raise WorkspaceError('版本已更新，请先载入当前版本。', 'VERSION_CHANGED')
            return put_document(connection, self.db.workspace, pid, vid, kind, key, value, expected)

    def effective(self, project):
        result = dict(project)
        edit = self.doc(project['id'], project['version_id'], 'architecture')
        result['manual_edit'] = {'revision': edit['revision'] if edit else 0,
            'modified': bool(edit and project.get('architecture') and edit['history'][edit['position']] != project['architecture']['modules']),
            'can_undo': bool(edit and edit['position'] > 0),
            'can_redo': bool(edit and edit['position'] < len(edit['history']) - 1),
            'conflicts': edit.get('conflicts', []) if edit else []}
        if edit and result.get('architecture'):
            result['architecture'] = dict(result['architecture'], modules=edit['history'][edit['position']])
        return result

    def load(self, pid, vid):
        current = self.db.get(pid)
        if not current or current.get('lifecycle') != 'active':
            raise WorkspaceError('项目不存在或正在删除。', status=404)
        if current['version_id'] == vid:
            return current
        row = self.db.version(pid, vid)
        if not row or row['status'] not in {'published', 'retired'} or not row['value'].get('architecture'):
            raise WorkspaceError('历史版本不存在、已清理或尚未完成。', status=404)
        result = dict(row['value'], id=pid, version_id=vid, readonly=True, status='ready')
        try:
            read_snapshot(self.snapshot_path(result), result['snapshot'])
        except (OSError, ValueError):
            raise WorkspaceError('历史源码快照不完整，不能用于阅读或对比。', 'HISTORY_INCOMPLETE') from None
        return result

    def snapshot_path(self, project):
        pid = str(uuid.UUID(project['id']))
        sid = str(uuid.UUID(project['snapshot']['id']))
        target = self.root / 'projects' / pid / 'snapshots' / sid
        if not target.resolve().is_relative_to(self.root.resolve()) or target.is_symlink() or target.is_junction():
            raise WorkspaceError('源码快照目录校验失败。')
        return target

    def notes(self, pid, vid=None):
        with self.db.engine.connect() as connection:
            rows = connection.execute(select(self.db.workspace).where(
                self.db.workspace.c.project_id == pid, self.db.workspace.c.kind == 'note')).mappings()
            result = [dict(row['value'], id=row['key'], revision=row['revision']) for row in rows]
        if vid:
            historical = []
            for note in result:
                if note['anchor_version'] == vid and note['status'] != 'merged':
                    historical.append(note)
                else:
                    previous = next((a for a in reversed(note.get('previous_anchors', [])) if a['version_id'] == vid), None)
                    if previous:
                        historical.append(dict(note, anchor_id=previous['id'], anchor_version=vid,
                                               status=previous['status'], text=previous['text']))
            return historical
        return result

    def save_note(self, pid, payload):
        vid = payload['anchor_version']
        project = self.effective(self.load(pid, vid))
        if project.get('readonly'):
            raise WorkspaceError('历史版本只读，请在当前版本关联笔记。')
        kind, anchor = payload['anchor_kind'], payload['anchor_id']
        pool = project['architecture']['modules'] if kind == 'module' and project.get('architecture') else (
            project.get('result') or {}).get('entities', []) if kind == 'function' else []
        if not any(item['id'] == anchor and (kind != 'function' or item['visible']) for item in pool):
            raise WorkspaceError('笔记目标不属于当前架构。')
        key = payload.get('id') or str(uuid.uuid4())
        # Serialize with publication and verify the anchor version again inside the transaction.
        with self.db.engine.begin() as connection:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
            current = connection.execute(select(self.db.projects).where(self.db.projects.c.id == pid)).mappings().one()
            if current['version_id'] != vid or current['lifecycle'] != 'active':
                raise WorkspaceError('版本已更新，请保留笔记并重新选择目标。', 'VERSION_CHANGED')
            edit = document(connection, self.db.workspace, pid, vid, 'architecture', 'main')
            if kind == 'module' and edit and anchor not in {m['id'] for m in edit['history'][edit['position']]}:
                raise WorkspaceError('模块已被另一页面调整，请重新选择。')
            old = document(connection, self.db.workspace, pid, '', 'note', key)
            value = {**(old or {}), **payload, 'id': key, 'status': 'linked', 'updated_at': stamp(), 'implementation_changed': False}
            for field in ['expected_revision', 'merge_target_id', 'merge_target_revision']:
                value.pop(field, None)
            target = next((dict(row['value'], id=row['key'], revision=row['revision']) for row in connection.execute(
                select(self.db.workspace).where(self.db.workspace.c.project_id == pid,
                    self.db.workspace.c.kind == 'note')).mappings()
                if row['value'].get('status') == 'linked' and row['value']['anchor_kind'] == kind
                and row['value']['anchor_id'] == anchor and row['value']['anchor_version'] == vid and row['key'] != key), None)
            if target:
                if not old or payload.get('merge_target_id') != target['id'] or payload.get('merge_target_revision') != target['revision']:
                    raise WorkspaceError('目标已有笔记。草稿保留；请明确合并最新内容后再保存。', 'NOTE_TARGET_EXISTS')
                if old['revision'] != payload['expected_revision']:
                    raise WorkspaceError('原笔记已变化，请重新载入后合并。', 'WORKSPACE_CONFLICT')
                combined = target['text'] if value['text'] in target['text'] else '\n\n'.join(filter(None, [target['text'], value['text']]))
                if len(combined) > 50000:
                    raise WorkspaceError('合并后超过50000字，请先整理笔记；原内容未修改。')
                merged = put_document(connection, self.db.workspace, pid, '', 'note', target['id'],
                    dict(target, text=combined, updated_at=stamp()), target['revision'])
                archived = dict(old, text='', status='merged', merged_into=target['id'])
                archived.setdefault('previous_anchors', []).append({'version_id': old['anchor_version'],
                    'id': old['anchor_id'], 'text': old['text'], 'status': 'linked'})
                put_document(connection, self.db.workspace, pid, '', 'note', key, archived, old['revision'])
                return merged
            return put_document(connection, self.db.workspace, pid, '', 'note', key, value, payload['expected_revision'])

    def edit(self, pid, vid, payload, preview=False):
        project = self.load(pid, vid)
        if not project.get('architecture') or project.get('readonly'):
            raise WorkspaceError('仅当前已完成的架构支持编辑。')
        old = self.doc(pid, vid, 'architecture')
        history = copy.deepcopy(old['history']) if old else [project['architecture']['modules']]
        position = old['position'] if old else 0
        action = payload['action']
        if action == 'apply':
            modules = validate_modules(project, payload['modules'])
            history = history[:position + 1] + [modules]
            position += 1
        elif action in {'undo', 'redo'}:
            position += -1 if action == 'undo' else 1
            if position < 0 or position >= len(history):
                raise WorkspaceError('没有可撤销或重做的修改。')
        elif action == 'restore':
            history = history[:position + 1] + [history[0]]
            position += 1
        else:
            raise WorkspaceError('未知编辑操作。')
        result = {'modules': history[position], 'module_count': len(history[position]),
                  'function_count': sum(len(m['entity_ids']) for m in history[position])}
        if preview:
            return result
        value = {'history': history, 'position': position, 'events': (old or {}).get('events', []) + [action],
                 'conflicts': []}
        with self.db.engine.begin() as connection:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
            current = connection.execute(select(self.db.projects).where(self.db.projects.c.id == pid)).mappings().one()
            if current['version_id'] != vid or current['lifecycle'] != 'active':
                raise WorkspaceError('版本已更新，请重新载入。', 'VERSION_CHANGED')
            saved = put_document(connection, self.db.workspace, pid, vid, 'architecture', 'main', value,
                                 payload['expected_revision'])
            valid = {m['id'] for m in history[position]}
            for row in connection.execute(select(self.db.workspace).where(
                    self.db.workspace.c.project_id == pid, self.db.workspace.c.kind == 'note')).mappings().all():
                note = row['value']
                if note['anchor_kind'] == 'module' and note['anchor_version'] == vid:
                    status = 'linked' if note['anchor_id'] in valid else 'unlinked'
                    if note['status'] != status:
                        put_document(connection, self.db.workspace, pid, '', 'note', row['key'],
                                     dict(note, status=status), row['revision'])
        return dict(result, revision=saved['revision'])

    def versions(self, pid):
        project = self.db.get(pid)
        protected = self.protected_versions(pid)
        values = {}
        with self.db.engine.connect() as connection:
            for row in connection.execute(select(self.db.versions).where(
                    self.db.versions.c.project_id == pid, self.db.versions.c.status.in_(['published', 'retired']))).mappings():
                if row['value'].get('architecture'):
                    values[row['id']] = (row['value'], row['created_at'])
        if project and project.get('architecture'):
            values[project['version_id']] = (project, values.get(project['version_id'], ({}, project['created_at']))[1])
        result = []
        for vid, (value, created) in values.items():
            pref = self.doc(pid, vid, 'retention') or {}
            manifest = value['snapshot']
            available = self.snapshot_path(dict(value, id=pid)).is_dir()
            source_bytes = sum(item.get('size', 0) for item in manifest['files'].values())
            record_bytes = len(json.dumps(value, ensure_ascii=False, default=str).encode('utf-8'))
            artifact = self.root / 'projects' / pid / f'version-{vid}.json'
            boundary = self.root / 'projects' / pid
            if artifact.resolve().is_relative_to(boundary.resolve()) and artifact.is_file() and not artifact.is_symlink():
                record_bytes += artifact.stat().st_size
            result.append({'id': vid, 'created_at': created, 'current': project['version_id'] == vid,
                'pinned': pref.get('pinned', False), 'revision': pref.get('revision', 0),
                'delete_requested': pref.get('delete_requested', False), 'available': available,
                'protected_reason': '包含待复核的人工修改' if vid in protected else None,
                'bytes': source_bytes + record_bytes, 'source_bytes': source_bytes, 'record_bytes': record_bytes,
                'file_count': len(manifest['files']), 'manual': bool(self.doc(pid, vid, 'architecture')),
                'origin': manifest.get('origin', {'kind': 'local'})})
        return sorted(result, key=lambda item: (item['current'], item['created_at']), reverse=True)

    def protected_versions(self, pid):
        with self.db.engine.connect() as connection:
            current = connection.execute(select(self.db.projects.c.version_id).where(self.db.projects.c.id == pid)).scalar()
            raw = connection.execute(select(func.json_extract(self.db.workspace.c.value, '$.conflicts')).where(
                *criteria(self.db.workspace, pid, current, 'architecture', 'main'))).scalar()
        return {item['from_version_id'] for item in (json.loads(raw) if raw else [])}

    def retain(self, row):
        if row['status'] != 'retired':
            return False
        pid = row['project_id']
        with self.db.engine.connect() as connection:
            current = connection.execute(select(self.db.projects.c.version_id).where(self.db.projects.c.id == pid)).scalar()
            entries = connection.execute(select(self.db.versions.c.id, self.db.versions.c.created_at).where(
                self.db.versions.c.project_id == pid, self.db.versions.c.status.in_(['published', 'retired']),
                func.json_type(self.db.versions.c.value, '$.architecture') == 'object')).all()
            prefs = {r['version_id']: r['value'] for r in connection.execute(select(self.db.workspace).where(
                self.db.workspace.c.project_id == pid, self.db.workspace.c.kind == 'retention')).mappings()}
        ids = [identifier for identifier, _ in sorted(entries, key=lambda r: r[1], reverse=True) if identifier != current]
        if current:
            ids.insert(0, current)
        eligible = [vid for vid in ids if not prefs.get(vid, {}).get('delete_requested')][:5]
        pref = prefs.get(row['id'], {})
        return not pref.get('delete_requested') and (pref.get('pinned') or row['id'] in eligible or row['id'] in self.protected_versions(pid))

    def search(self, project, query, offset=0, limit=20):
        query = query.strip().casefold()
        if not query:
            return {'items': [], 'total': 0}
        rows = []
        for module in (project.get('architecture') or {}).get('modules', []):
            rows.append({'kind': 'module', 'id': module['id'], 'title': module['name'],
                         'text': module['summary'], 'path': ', '.join(module['code_paths'][:3])})
        explanations = {item['entity_id']: item for item in (project.get('architecture') or {}).get('explanations', [])}
        for entity in (project.get('result') or {}).get('entities', []):
            if not entity['visible']:
                continue
            note = explanations.get(entity['id'], {})
            rows.append({'kind': 'function', 'id': entity['id'], 'title': entity['qualified_name'],
                'text': '\n'.join([note.get('summary', ''), note.get('details', ''), entity['signature']]),
                'path': entity['location']['path'], 'location': entity['location']})
        for path in project['snapshot']['files']:
            rows.append({'kind': 'file', 'id': path, 'title': Path(path).name, 'text': path, 'path': path,
                         'location': {'path': path, 'start_line': 1, 'end_line': 1}})
        for note in self.notes(project['id']):
            if not note['text'].strip() or note['status'] == 'merged':
                continue
            if note['anchor_version'] == project['version_id'] or note['status'] == 'unlinked':
                rows.append({'kind': 'note', 'id': note['id'], 'title': '学习笔记', 'text': note['text'], 'path': '',
                    'anchor_kind': note['anchor_kind'], 'anchor_id': note['anchor_id'], 'status': note['status']})
        matched = []
        for row in rows:
            title = row['title'].casefold()
            haystack = '\n'.join([row['title'], row['path'], row['text']]).casefold()
            if query in haystack or all(token in haystack for token in query.split()):
                score = 0 if title == query else 1 if title.startswith(query) else 2 if query in title else 3
                matched.append(dict(row, score=score, text=row['text'][:400], version_id=project['version_id']))
        matched.sort(key=lambda row: (row['score'], row['title'], row['kind'], row['id']))
        return {'items': matched[offset:offset + limit], 'total': len(matched)}

    def guide(self, project):
        architecture = project.get('architecture')
        if not architecture:
            return {'items': [], 'message': '架构尚未完成，先完成分析再生成阅读建议。'}
        states = project.get('learning', self.db.states(project['id']))
        from .call_judgments import reconcile_judgments
        from .models import Architecture
        analysis = Analysis.model_validate(project['result'])
        if architecture.get('call_judgments'):
            analysis = reconcile_judgments(analysis, Architecture.model_validate(architecture))
        entities = {e.id: e.model_dump() for e in analysis.entities if e.visible}
        scope_ids = {e['scope_id']: e['id'] for e in entities.values()}
        incoming, outgoing = Counter(), Counter()
        confirmed = []
        for record in analysis.calls:
            call = record.model_dump()
            if (call['resolution'] == 'confirmed' and call['kind'] == 'direct'
                    and call['target_id'] in entities and call['caller_scope_id'] in scope_ids):
                outgoing[scope_ids[call['caller_scope_id']]] += 1
                incoming[call['target_id']] += 1
                confirmed.append(call)
        candidates = sorted(entities.values(), key=lambda e: (
            states.get(e['id'], {}).get('state') == 'mastered', outgoing[e['id']] == 0,
            incoming[e['id']] != 0, -outgoing[e['id']], e['qualified_name']))
        modules = {m['id']: m for m in architecture['modules']}
        owners = {eid: module['id'] for module in modules.values() for eid in module['entity_ids']}
        children = defaultdict(list)
        for module in modules.values():
            children[module['parent_id']].append(module['id'])
        def members(mid):
            return modules[mid]['entity_ids'] + [eid for child in children[mid] for eid in members(child)]
        roots = [m for m in modules.values() if not m['parent_id'] and m['code_paths'] and not m['incomplete']]
        roots.sort(key=lambda m: (-sum(states.get(eid, {}).get('state') != 'mastered' for eid in members(m['id'])), m['name']))
        items = [{'kind': 'overview', 'id': 'overview', 'title': '先看项目概览', 'reason': '先理解项目用途与主要分工。'}]
        if roots:
            module = roots[0]
            path = module['code_paths'][0]
            items.append({'kind': 'module', 'id': module['id'], 'title': '理解模块：' + module['name'],
                'reason': module['summary'][:140] + ' 先明确这部分分工，再深入尚未掌握的内容。',
                'location': {'path': path, 'start_line': 1, 'end_line': 1}})
        selected = next((c for c in confirmed if owners.get(scope_ids[c['caller_scope_id']]) != owners.get(c['target_id'])), None)
        if selected:
            primary = next((d['reason'] for d in architecture.get('primary_dependencies', []) if selected['id'] in d['call_ids']), '')
            items.append({'kind': 'relation', 'id': selected['id'], 'title': '理解一条确认协作', 'location': selected['location'],
                'reason': primary[:160] or '沿这条有源码证据的调用，理解两个职责如何配合；它不是完整运行流程。'})
        items.extend([
            {'kind': 'function', 'id': e['id'], 'title': e['qualified_name'], 'location': e['location'],
             'reason': ('已掌握，可回顾。' if states.get(e['id'], {}).get('state') == 'mastered' else '尚未掌握，可按需阅读。')
             + f"已确认向内调用 {outgoing[e['id']]} 次；" + (
                 '当前快照未发现内部调用方，可作为候选阅读起点。' if not incoming[e['id']] else '可结合调用证据理解模块协作。')}
            for e in candidates[:3]])
        return {'items': items, 'message': '本地阅读建议，可自由跳过；不代表真实程序入口或完整运行顺序。'}
