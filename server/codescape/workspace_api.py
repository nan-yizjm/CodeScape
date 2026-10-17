"""版本化工作区接口；复用主应用的会话、CSRF和错误封装。"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .analyzers import digest, stable_id
from .imports import run_git, validate_git_url
from .models import SessionRequest
from .sessions import validate_session
from .snapshots import (
    EXCLUDED_DIRECTORIES,
    METADATA_NAMES,
    OPTIONAL_DIRECTORIES,
    SOURCE_EXTENSIONS,
    is_link,
    matches_scope,
    read_snapshot,
)
from .workspace import WorkspaceError
from .workspace_compare import compare_versions


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid')


class NotePayload(Payload):
    id: str | None = Field(default=None, max_length=100)
    anchor_version: str
    anchor_kind: Literal['module', 'function']
    anchor_id: str = Field(max_length=2048)
    text: str = Field(max_length=50000)
    expected_revision: int = Field(default=0, ge=0)
    merge_target_id: str | None = None
    merge_target_revision: int | None = Field(default=None, ge=0)


class EditPayload(Payload):
    action: Literal['apply', 'undo', 'redo', 'restore']
    modules: list[dict] = Field(default_factory=list, max_length=10000)
    expected_revision: int = Field(default=0, ge=0)


class PinPayload(Payload):
    pinned: bool
    expected_revision: int = Field(default=0, ge=0)


class ViewPayload(Payload):
    view_id: UUID
    active: bool = True


def source_changes(project, remote=False):
    manifest = project['snapshot']
    if manifest.get('scope', {}).get('matcher_version', 1) != 1:
        return {'status': 'scope_changed', 'message': '扫描范围规则版本已变化，请先预览范围，再决定是否重分析。'}
    origin = manifest.get('origin', {'kind': 'local'})
    if origin['kind'] == 'git':
        if not remote:
            return {'status': 'manual', 'message': '远程仓库仅在点击检查时联网。'}
        try:
            url = validate_git_url(origin['url'])
            refs = run_git('ls-remote', url, 'HEAD').decode('utf-8')
            commit = re.search(r'^([0-9a-f]{40})\s+HEAD$', refs, re.MULTILINE)
            if not commit:
                raise ValueError('无法读取远程默认分支。')
            changed = commit[1] != origin.get('commit')
            return {'status': 'changed' if changed else 'unchanged', 'message': '远程版本已有变化，是否重分析由你决定。' if changed else '远程默认分支没有变化。'}
        except (OSError, ValueError, RuntimeError):
            return {'status': 'error', 'message': '远程检查失败，请检查网络与仓库地址。'}
    source = Path(origin.get('path') or manifest.get('source') or project['source'])
    if not source.is_dir():
        return {'status': 'unavailable', 'message': '原目录不可用；已保存的快照仍可阅读。'}
    if is_link(source):
        return {'status': 'error', 'message': '原目录已变为链接，未跟随读取。'}
    includes = manifest.get('scope', {}).get('includes', [])
    excludes = manifest.get('scope', {}).get('excludes', [])
    files = {}
    try:
        for directory, folders, names in os.walk(source, followlinks=False):
            parent = Path(directory)
            folders[:] = sorted(name for name in folders if name not in EXCLUDED_DIRECTORIES
                                and not is_link(parent / name))
            for name in sorted(names):
                path = parent / name
                relative = path.relative_to(source).as_posix()
                if name.startswith('.env') or is_link(path) or (path.suffix.lower() not in SOURCE_EXTENSIONS and name not in METADATA_NAMES):
                    continue
                if matches_scope(relative, excludes):
                    continue
                if any(part in OPTIONAL_DIRECTORIES for part in path.relative_to(source).parts[:-1]) and not matches_scope(relative, includes):
                    continue
                if any(item.get('reason') == '默认排除目录或链接' and (
                        relative == item['path'] or relative.startswith(item['path'].rstrip('/') + '/'))
                        for item in manifest.get('excluded', [])):
                    continue
                before = path.stat()
                raw = path.read_bytes()
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError('检查期间文件发生变化')
                try:
                    raw.decode('utf-8')
                except UnicodeDecodeError:
                    continue
                files[relative] = digest(raw)
        old = {path: info['hash'] for path, info in manifest['files'].items()}
        added = sorted(files.keys() - old.keys())
        removed = sorted(old.keys() - files.keys())
        changed = sorted(path for path in files.keys() & old.keys() if files[path] != old[path])
        dirty = bool(added or removed or changed)
        return {'status': 'changed' if dirty else 'unchanged', 'added': added, 'removed': removed, 'changed': changed,
                'message': '源码已有变化，可预览范围后重新分析。' if dirty else '当前扫描范围内源码没有变化。'}
    except (OSError, ValueError):
        return {'status': 'error', 'message': '检查未完成；原文件可能正在变动或无法读取，请稍后重试。'}


def register_workspace(app, service, get_project, public, envelope, cleanup, reclaim):
    def load(pid, vid):
        get_project(pid)
        return service.load(pid, vid)

    @app.get('/api/v1/projects/{pid}/versions')
    def versions(pid: str):
        get_project(pid)
        return envelope(service.versions(pid))

    @app.get('/api/v1/projects/{pid}/versions/{vid}')
    def detail(pid: str, vid: str):
        with service.reads.read(pid, vid):
            return envelope(public(load(pid, vid), full=True))

    @app.post('/api/v1/projects/{pid}/versions/{vid}/lease')
    def lease(pid: str, vid: str, body: ViewPayload):
        get_project(pid)
        current = service.db.get(pid)
        row = service.db.version(pid, vid)
        if current['version_id'] != vid and (not row or row['status'] not in {'published', 'retired'}):
            raise WorkspaceError('版本已经清理。', status=404)
        service.reads.renew('workspace:' + str(body.view_id), [(pid, vid, 60)] if body.active else [])
        return envelope({'active': body.active})

    @app.get('/api/v1/projects/{pid}/versions/{vid}/files/{file_id}')
    def source(pid: str, vid: str, file_id: str):
        with service.reads.read(pid, vid):
            project = load(pid, vid)
            paths = {stable_id(vid, path): path for path in project['snapshot']['files']}
            if file_id not in paths:
                raise WorkspaceError('文件不属于该版本。', status=404)
            path = paths[file_id]
            try:
                files = read_snapshot(service.snapshot_path(project), project['snapshot'])
            except (OSError, ValueError):
                raise WorkspaceError('源码快照读取或校验失败，请保留数据并检查磁盘；没有改读原目录。', 'SNAPSHOT_INVALID') from None
            return envelope({'path': path, 'text': files[path], 'version_id': vid,
                             'hash': project['snapshot']['files'][path]['hash']})

    @app.put('/api/v1/projects/{pid}/versions/{vid}/pin')
    def pin(pid: str, vid: str, body: PinPayload):
        with service.reads.read(pid, vid):
            project = get_project(pid)
            if body.pinned:
                load(pid, vid)
            elif vid != project['version_id'] and not service.db.version(pid, vid):
                raise WorkspaceError('版本不存在。', status=404)
            return envelope(service.write(pid, vid, 'retention', 'main',
                {'pinned': body.pinned, 'delete_requested': False}, body.expected_revision))

    @app.delete('/api/v1/projects/{pid}/versions/{vid}')
    def delete_version(pid: str, vid: str, request: Request, expected_revision: int = 0):
        project = get_project(pid)
        if project['version_id'] == vid:
            raise WorkspaceError('当前版本不能删除。')
        version = service.db.version(pid, vid)
        if not version or version['status'] not in {'published', 'retired'}:
            raise WorkspaceError('历史版本不存在或已清理。', status=404)
        pref = service.doc(pid, vid, 'retention') or {}
        if pref.get('pinned'):
            raise WorkspaceError('请先取消固定，再删除历史版本。')
        if vid in service.protected_versions(pid):
            raise WorkspaceError('该版本包含待复核人工修改，请先在当前架构处理待复核项。', 'VERSION_REVIEW_REQUIRED')
        with service.reads.reclaim(pid, vid) as available:
            if not available:
                raise WorkspaceError('该版本正在阅读或对比，请关闭后再删除。', 'VERSION_BUSY')
            service.write(pid, vid, 'retention', 'main', {'pinned': False, 'delete_requested': True}, expected_revision)
            reclaim(service.db.version(pid, vid))
            row = service.db.version(pid, vid)
            if row and row['status'] != 'purged':
                raise WorkspaceError('文件尚未释放，已保留清理请求，稍后重试。', 'CLEANUP_PENDING')
        return envelope({'deleted': True})

    @app.get('/api/v1/projects/{pid}/versions/{vid}/search')
    def search(pid: str, vid: str, q: str = Query('', max_length=200), offset: int = Query(0, ge=0),
               limit: int = Query(20, ge=1, le=100)):
        return envelope(service.search(service.effective(load(pid, vid)), q, offset, limit))

    @app.get('/api/v1/projects/{pid}/versions/{vid}/guide')
    def guide(pid: str, vid: str):
        return envelope(service.guide(service.effective(load(pid, vid))))

    @app.get('/api/v1/projects/{pid}/notes')
    def notes(pid: str, version_id: str | None = None):
        get_project(pid)
        if version_id:
            load(pid, version_id)
        return envelope(service.notes(pid, version_id))

    @app.put('/api/v1/projects/{pid}/notes')
    def note(pid: str, body: NotePayload):
        get_project(pid)
        return envelope(service.save_note(pid, body.model_dump()))

    @app.post('/api/v1/projects/{pid}/versions/{vid}/architecture/preview')
    def preview(pid: str, vid: str, body: EditPayload):
        get_project(pid)
        return envelope(service.edit(pid, vid, body.model_dump(), preview=True))

    @app.put('/api/v1/projects/{pid}/versions/{vid}/architecture')
    def edit(pid: str, vid: str, body: EditPayload):
        get_project(pid)
        return envelope(service.edit(pid, vid, body.model_dump()))

    @app.get('/api/v1/projects/{pid}/versions/{vid}/workspaces/{slot}')
    def session(pid: str, vid: str, slot: str):
        load(pid, vid)
        saved = service.doc(pid, vid, 'session', slot)
        legacy = service.db.session(pid) if slot == 'single' and service.db.get(pid)['version_id'] == vid else None
        return envelope(saved or (dict(legacy, revision=0) if legacy else None))

    @app.put('/api/v1/projects/{pid}/versions/{vid}/workspaces/{slot}')
    def save_session(pid: str, vid: str, slot: str, body: SessionRequest):
        if not re.fullmatch(r'[a-z0-9_-]{1,80}', slot):
            raise WorkspaceError('工作区标识无效。')
        project = service.effective(load(pid, vid))
        value = body.model_dump()
        if value['version_id'] != vid:
            raise WorkspaceError('工作区版本不一致。')
        try:
            validate_session(value, project)
        except ValueError:
            raise WorkspaceError('探索记录引用了当前版本以外的对象。', status=422) from None
        expected = value.pop('expected_revision')
        return envelope(service.write(pid, vid, 'session', slot, value, expected))

    @app.get('/api/v1/projects/{pid}/compare')
    def compare(pid: str, before: str, after: str):
        with service.reads.read(pid, before), service.reads.read(pid, after):
            return envelope(compare_versions(service.effective(load(pid, before)), service.effective(load(pid, after))))

    @app.get('/api/v1/projects/{pid}/changes')
    def changes(pid: str, remote: bool = False):
        project = get_project(pid)
        result = source_changes(project, remote)
        candidate = service.db.candidate(pid)
        if candidate and candidate['value']['snapshot'].get('scope_hash') != project['snapshot'].get('scope_hash'):
            result['scope_difference'] = True
            result['message'] += ' 独立候选的扫描范围不同，当前提示仍按已发布范围检查。'
        return envelope(result)
