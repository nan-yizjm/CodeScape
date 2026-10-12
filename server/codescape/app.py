from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .ai_process import run_architecture
from .analyzers import REPOSITORY, analyze, stable_id
from .call_judgments import reconcile_judgments, validate_judgments
from .context_tools import validate_explanations
from .database import Database, now
from .deletion import ProjectDeletion
from .dependencies import validate_primary_dependencies
from .idempotency import MutationReceipts
from .imports import ImportService, remove_staging
from .index_process import index_snapshot
from .learning import learning_units
from .ledger import RequestLedger, ai_scope
from .lifecycle import ClientLifetime
from .models import (
    AISettings,
    Analysis,
    Architecture,
    ImportRequest,
    LearningRequest,
    Module,
    Overview,
    ProjectDeleteRequest,
    SessionRequest,
)
from .provider import (
    PIPELINE_VERSION,
    SOURCE_PIPELINE_VERSION,
    Provider,
    ProviderError,
    compatible_source_checkpoint,
    validate_architecture,
)
from .publication import load_output, prepare_output, receiver_identity
from .reads import VersionReads
from .security import data_root
from .snapshots import create_snapshot, read_snapshot
from .tasks import TaskQueue


def create_app(root: Path | None = None, nonce: str | None = None, on_idle_exit=None, architecture_runner=None) -> FastAPI:
    root = root or data_root()
    database = Database(root)
    database.recover_interrupted()
    receipts = MutationReceipts(database)
    receipts.recover()
    provider = Provider(root, database)
    ledger = RequestLedger(database)
    ledger.recover()
    imports = ImportService(root)
    bootstrap_nonce = nonce or secrets.token_urlsafe(32)
    session_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    cookie_name = "codescape_" + secrets.token_hex(8)
    nonces = {bootstrap_nonce: time.monotonic() + 300}
    nonce_lock = threading.Lock()
    lifetime = ClientLifetime()
    reads = VersionReads()
    closing = False
    run_lock = asyncio.Lock()
    queue = TaskQueue(database, run_lock)
    deletion = ProjectDeletion(root, database, queue, reads)
    consents_path = root / "consents.json"

    def read_consents():
        try:
            document = json.loads(consents_path.read_text(encoding="utf-8")) if consents_path.exists() else {}
        except (OSError, ValueError):
            raise HTTPException(409, "上传授权记录暂不可用，请检查本地数据后重试。") from None
        if not isinstance(document, dict) or any(not isinstance(value, dict) for value in document.values()):
            raise HTTPException(409, "上传授权记录格式无效，请检查本地数据后重试。")
        return document

    def save_consents(document):
        temporary = consents_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(document, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(consents_path)

    def issue_nonce() -> str:
        if closing:
            raise RuntimeError("应用正在保存并退出，请稍后重新启动。")
        value = secrets.token_urlsafe(32)
        with nonce_lock:
            expired = [key for key, expires in nonces.items() if expires < time.monotonic()]
            for key in expired:
                nonces.pop(key, None)
            if len(nonces) >= 16:
                nonces.pop(next(iter(nonces)))
            nonces[value] = time.monotonic() + 300
        return value

    def get_project(project_id: str) -> dict:
        project = database.get(project_id)
        if not project:
            raise HTTPException(404, "项目不存在。")
        if project.get("lifecycle", "active") != "active":
            raise HTTPException(410, {"code": "PROJECT_DELETING", "message": "项目正在删除或等待重试，不能继续读写。"})
        return project

    def envelope(data):
        return {"data": data, "meta": {"request_id": str(uuid.uuid4())}}

    def public(project: dict, full: bool = False):
        analysis = Analysis.model_validate(project["result"]) if project["result"] else Analysis()
        visible = [entity for entity in analysis.entities if entity.visible]
        architecture = Architecture.model_validate(project["architecture"]) if project["architecture"] else None
        if architecture:
            if architecture.call_judgments:
                analysis = reconcile_judgments(analysis, architecture)
            explanations = {note.entity_id: note for note in architecture.explanations}
            for entity in analysis.entities:
                note = explanations.get(entity.id)
                if note:
                    entity.explanation = note.summary
                    entity.explanation_details = note.details
                    entity.explanation_evidence = [ref.model_dump() for ref in note.evidence]
        units = learning_units(analysis, architecture, project["snapshot"]["files"])
        states = database.states(project["id"])
        total = len(units)
        mastered = sum(states.get(unit["id"], {}).get("state") == "mastered" for unit in units)
        response = {
            "id": project["id"],
            "name": project["name"],
            "source": project["source"],
            "created_at": project["created_at"],
            "status": project.get("lifecycle") if project.get("lifecycle", "active") != "active" else project["status"],
            "version_id": project["version_id"],
            "demo": bool(project["demo"]),
            "error": project["error"],
            "function_count": len(visible),
            "file_count": len(project["snapshot"]["files"]),
            "progress": {"mastered": mastered, "total": total},
            "languages": sorted({entity.language for entity in visible}),
            "diagnostic_count": len(analysis.diagnostics),
            "has_architecture": bool(project["architecture"]),
            "analysis_progress": database.analysis_progress(project["id"]),
            "origin": project["snapshot"].get("origin", {"kind": "local"}),
            "lifecycle": project.get("lifecycle", "active"),
            "delete_operation": database.delete_operation(project["id"]) if project.get("lifecycle", "active") != "active" else None,
        }
        if full:
            candidate = database.candidate(project["id"])
            published = database.version(project["id"], project["version_id"])
            consent_version = candidate["id"] if candidate else project["version_id"]
            consent_manifest = candidate["value"]["snapshot"] if candidate else project["snapshot"]
            try:
                grants = read_consents()
            except HTTPException:
                grants = {}
            grant = grants.get(project["id"] + ":" + consent_version, {} if candidate else grants.get(project["id"], {}))
            response.update(
                {
                    "upload_consent": {"version_id": consent_version, "accepted":
                        isinstance(grant, dict) and grant.get("base_url") == provider.settings()["base_url"]
                        and grant.get("snapshot_hash") == consent_manifest["hash"]},
                    "analysis": analysis.model_dump(),
                    "architecture": project["architecture"],
                    "file_ids": {
                        path: stable_id(project["version_id"], path) for path in project["snapshot"]["files"]
                    },
                    "learning": states,
                    "learning_entities": units,
                    "candidate": {"version_id": candidate["id"], "status": candidate["status"],
                                  "file_count": len(candidate["value"]["snapshot"]["files"]),
                                  "origin": candidate["value"]["snapshot"].get("origin", {}),
                                  "excluded": candidate["value"]["snapshot"]["excluded"]} if candidate else None,
                    "migration_report": published["value"].get("migration_report") if published else None,
                    "scope": {
                        "excluded": project["snapshot"]["excluded"],
                        "snapshot_id": project["snapshot"]["id"],
                        **project["snapshot"].get("scope", {}),
                    },
                }
            )
        return response

    def seed():
        if database.metadata_value("seed_initialized"):
            return
        if any(project["demo"] for project in database.list_projects()):
            database.metadata_value("seed_initialized", True)
            return
        project_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        manifest = create_snapshot(
            REPOSITORY / "tests/fixtures/learning_project", root / "projects" / project_id
        )
        snapshot_root = root / "projects" / project_id / "snapshots" / manifest["id"]
        files = read_snapshot(snapshot_root, manifest)
        result = analyze(snapshot_root, files, version_id)
        labels = [
            ("input.py", "输入与配置", "读取输入并检查配置，为后续分析提供明确边界。"),
            ("engine.py", "分析引擎", "遍历语法结构，组合分析结果与配置。"),
            ("presentation.py", "结果呈现", "将分析结果组织为可阅读、可导出的图形。"),
        ]
        architecture = Architecture(
            modules=[
                Module(
                    id=stable_id(version_id, path),
                    name=name,
                    summary=summary,
                    entity_ids=[
                        entity.id
                        for entity in result.entities
                        if entity.visible and entity.location.path == path
                    ],
                    code_paths=[path],
                )
                for path, name, summary in labels
            ],
            overview=Overview(
                introduction="这是一个用于验证源码学习流程的 Python 预置样例，展示输入、分析和结果呈现之间的协作。",
                capabilities=["读取和校验输入配置", "递归遍历语法节点", "组织和导出分析结果"],
                principle="结果呈现调用分析引擎，分析引擎读取配置并遍历语法。所有画布调用关系都由样例源码静态提取。",
                limitations=[
                    "模块划分和概览为人工预置，尚未经过 AI 生成。",
                    "样例仅用于原型验证，没有执行项目代码。",
                    "动态属性和生成器作用域的解析覆盖仍需完善。",
                ],
            ),
        ).model_dump()
        database.insert(
            {
                "id": project_id,
                "name": "源码学习样例",
                "source": "预置验证样例 · Python",
                "created_at": now(),
                "status": "ready",
                "snapshot": manifest,
                "version_id": version_id,
                "result": result.model_dump(),
                "architecture": architecture,
                "demo": 1,
            }
        )
        database.metadata_value("seed_initialized", True)

    @asynccontextmanager
    async def lifespan(_):
        app.state.loop = asyncio.get_running_loop()
        imports.cleanup()
        seed()
        for operation in database.pending_deletes():
            deletion.start(operation["project_id"])
        app.state.ready_event.set()
        async def watch_clients():
            nonlocal closing
            while True:
                await asyncio.sleep(5)
                cleanup_versions()
                if on_idle_exit and lifetime.should_exit():
                    await request_shutdown()
                    return
        cleanup_versions()
        monitor = asyncio.create_task(watch_clients())
        yield
        closing = True
        if monitor:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        await queue.shutdown()
        await deletion.shutdown()
        database.engine.dispose()

    async def request_shutdown():
        nonlocal closing
        closing = True
        await queue.shutdown()
        await deletion.shutdown()
        if on_idle_exit:
            await on_idle_exit()

    def cleanup_versions():
        for version in database.reclaimable_versions():
            with reads.reclaim(version["project_id"], version["id"]) as available:
                if available:
                    reclaim_version(version)

    def reclaim_version(version):
        project = database.get(version["project_id"])
        if not project or project.get("lifecycle", "active") != "active" or project["version_id"] == version["id"]:
            return
        manifest = version["value"].get("snapshot")
        if not manifest or project["snapshot"]["id"] == manifest["id"]:
            return
        try:
            project_id, snapshot_id = str(uuid.UUID(project["id"])), str(uuid.UUID(manifest["id"]))
            boundary = root / "projects" / project_id
            target = boundary / "snapshots" / snapshot_id
            if target.is_dir():
                remove_staging(target, boundary)
            for name in [f"version-{version['id']}.json", f"checkpoint-{version['id']}.json"]:
                (boundary / name).unlink(missing_ok=True)
            database.mark_purged(version["id"], {"snapshot_id": snapshot_id, "snapshot_hash": manifest["hash"]})
        except (OSError, ValueError):
            # Occupied files are retained and retried on the next cleanup pass.
            return


    app = FastAPI(
        title="CodeScape 本地服务", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.state.nonce = bootstrap_nonce
    app.state.root = root
    app.state.reads = reads
    app.state.cleanup_versions = cleanup_versions
    app.state.issue_nonce = issue_nonce
    app.state.ready_event = threading.Event()
    app.state.lifetime = lifetime
    app.state.request_shutdown = request_shutdown
    app.state.receipts = receipts

    @app.middleware("http")
    async def protect(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            if closing and request.method not in {"GET", "HEAD"}:
                return JSONResponse({"error": {"code": "STOPPING", "message": "应用正在保存并退出。"}}, status_code=503)
            if (
                request.method not in {"GET", "HEAD"}
                and request.headers.get("content-type", "").split(";")[0] != "application/json"
            ):
                return JSONResponse(
                    {"error": {"code": "CONTENT_TYPE", "message": "写入请求需要 JSON。"}}, status_code=415
                )
            hostname = request.url.hostname
            if hostname not in {"127.0.0.1", "localhost", "testserver"}:
                return JSONResponse(
                    {"error": {"code": "INVALID_HOST", "message": "本地服务 Host 不匹配。"}}, status_code=403
                )
            origin = request.headers.get("origin")
            allowed_origins = {
                f"http://127.0.0.1:{request.url.port or 80}",
                f"http://localhost:{request.url.port or 80}",
            }
            if origin and origin not in allowed_origins:
                return JSONResponse(
                    {"error": {"code": "INVALID_ORIGIN", "message": "请求来源不匹配。"}}, status_code=403
                )
            if request.url.path != "/api/v1/session/exchange":
                supplied = request.cookies.get(cookie_name, "")
                if not secrets.compare_digest(supplied, session_token):
                    return JSONResponse(
                        {"error": {"code": "UNAUTHORIZED", "message": "请从启动入口打开应用。"}},
                        status_code=401,
                    )
                if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(
                    request.headers.get("x-csrf-token", ""), csrf_token
                ):
                    return JSONResponse(
                        {"error": {"code": "CSRF", "message": "会话校验失败。"}}, status_code=403
                    )
        response = await receipts.handle(request, call_next)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_, error: HTTPException):
        return JSONResponse(
            {"error": error.detail if isinstance(error.detail, dict) else {"code": str(error.status_code), "message": error.detail}},
            status_code=error.status_code,
        )

    @app.post("/api/v1/session/exchange")
    async def exchange(request: Request):
        body = await request.json()
        supplied = str(body.get("nonce", ""))
        with nonce_lock:
            expires = nonces.pop(supplied, 0)
        if time.monotonic() > expires:
            raise HTTPException(401, "启动凭证已失效，请重新通过启动入口打开。")
        response = JSONResponse(envelope({"csrf_token": csrf_token}))
        response.set_cookie(cookie_name, session_token, httponly=True, samesite="strict")
        return response

    def client_identity(value) -> str:
        try:
            identity = str(uuid.UUID(value))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(422, "页面标识不合法。") from None
        return identity

    @app.post("/api/v1/clients/heartbeat")
    async def client_heartbeat(request: Request):
        body = await request.json()
        identity = client_identity(body.get("client_id"))
        supplied = body.get("version_leases", [])
        if not isinstance(supplied, list) or len(supplied) > 4:
            raise HTTPException(422, "读取租约范围不合法。")
        accepted, changed = [], []
        for item in supplied:
            if not isinstance(item, dict):
                raise HTTPException(422, "读取租约不合法。")
            project_id, version_id = item.get("project_id"), item.get("version_id")
            project = database.get(project_id) if isinstance(project_id, str) else None
            if not project:
                continue
            if version_id == project["version_id"]:
                accepted.append((project_id, version_id, 60))
            else:
                changed.append({"project_id": project_id, "version_id": project["version_id"]})
                old = database.version(project_id, version_id) if isinstance(version_id, str) else None
                if old and old["status"] == "retired" and reads.held(identity, project_id, version_id):
                    remaining = 120 - (datetime.now(UTC) - datetime.fromisoformat(old["created_at"])).total_seconds()
                    accepted.append((project_id, version_id, remaining))
        reads.renew(identity, accepted)
        lifetime.heartbeat(identity)
        return envelope({"connected": True, "version_changes": changed})

    @app.post("/api/v1/clients/disconnect")
    async def client_disconnect(request: Request):
        identity = client_identity((await request.json()).get("client_id"))
        lifetime.disconnect(identity)
        reads.disconnect(identity)
        return envelope({"connected": False})

    @app.get("/api/v1/clients/{client_id}/events")
    async def client_events(client_id: str, request: Request):
        identity = client_identity(client_id)

        async def events():
            connection_id = str(uuid.uuid4())
            lifetime.connect(identity, connection_id)
            try:
                while not closing and not await request.is_disconnected():
                    yield 'event: heartbeat\ndata: {}\n\n'
                    await asyncio.sleep(20)
            finally:
                lifetime.close_connection(connection_id)

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/session")
    async def session_info():
        return envelope({"csrf_token": csrf_token, "stage": "P0 验收中", "version": "0.1.0"})

    @app.get("/api/v1/projects")
    async def projects():
        return envelope([public(project) for project in database.list_projects()])

    @app.get("/api/v1/projects/{project_id}")
    async def project(project_id: str):
        return envelope(public(get_project(project_id), True))

    @app.delete("/api/v1/projects/{project_id}", status_code=202)
    async def delete_project(project_id: str, body: ProjectDeleteRequest):
        if body.confirm is not True:
            raise HTTPException(422, "需要明确确认永久删除软件内项目数据。")
        if not database.get(project_id) and not database.delete_operation(project_id):
            raise HTTPException(404, "项目不存在。")
        try:
            return envelope(deletion.start(project_id))
        except ValueError:
            raise HTTPException(409, "项目删除状态已经改变，请刷新。") from None

    @app.get("/api/v1/operations/{operation_id}")
    async def operation(operation_id: str):
        value = database.operation(operation_id)
        if not value:
            raise HTTPException(404, "操作不存在。")
        return envelope(value)

    async def index_project(project_id: str, candidate_id: str | None = None):
        project = get_project(project_id)
        if candidate_id:
            candidate = database.version(project_id, candidate_id)
            project = {**project, **candidate["value"], "version_id": candidate_id}
        snapshot_root = root / "projects" / project_id / "snapshots" / project["snapshot"]["id"]
        database.update(project_id, status="indexing", error=None)
        try:
            result = await index_snapshot(snapshot_root,project['snapshot'],project['version_id'])
            if candidate_id:
                candidate["value"]["result"] = result.model_dump()
                database.update_candidate(candidate_id, candidate["value"])
            else:
                database.update(project_id, result=result.model_dump(), status="indexed")
        except asyncio.CancelledError:
            database.update(project_id, status="paused", error="静态索引已暂停，可复用当前快照恢复。")
            raise
        except Exception as error:
            database.update(project_id, status="failed", error=str(error)[:300])
            if candidate_id:
                raise

    @app.post("/api/v1/imports/preview")
    async def preview_import(body: ImportRequest):
        try:
            return envelope(await asyncio.to_thread(imports.preview, body))
        except (OSError, ValueError) as error:
            raise HTTPException(400, str(error)) from None

    @app.post("/api/v1/projects/{project_id}/reanalysis/preview")
    async def preview_reanalysis(project_id: str, body: ImportRequest):
        project = get_project(project_id)
        if any(job["project_id"] == project_id and job["status"] in {"running", "waiting", "paused"}
               for job in database.list_jobs()):
            raise HTTPException(409, "项目已有未结束任务，请先完成或取消。")
        origin = project["snapshot"].get("origin", {"kind": "local", "path": project["source"]})
        if not body.path and not body.url:
            body.path = origin.get("path") if origin["kind"] == "local" else None
            body.url = origin.get("url") if origin["kind"] == "git" else None
        return await preview_import(body)

    @app.post("/api/v1/projects/{project_id}/reanalysis")
    async def prepare_reanalysis(project_id: str, body: ImportRequest):
        project = get_project(project_id)
        if not body.preview_id or body.path or body.url or body.includes or body.excludes:
            raise HTTPException(422, "请先扫描并确认新范围。")
        if database.candidate(project_id) or any(job["project_id"] == project_id and
                job["status"] in {"running", "waiting", "paused"} for job in database.list_jobs()):
            raise HTTPException(409, "项目已有未结束候选或任务，请先完成或取消。")
        try:
            imported = await asyncio.to_thread(imports.consume, body.preview_id, project_id, True)
            version_id = str(uuid.uuid4())
            database.insert_candidate(project_id, version_id, project["version_id"],
                                      {"snapshot": imported["manifest"], "result": None})
            job = queue.create(project, "reanalysis")
        except (OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from None
        return envelope({"version_id": version_id, "task_id": job["id"]})

    @app.post("/api/v1/projects/{project_id}/index")
    async def retry_index(project_id: str):
        project = get_project(project_id)
        if project["result"] or project["status"] not in {"failed", "indexed", "paused", "queued", "cancelled"}:
            raise HTTPException(409, "当前项目不需要重新建立静态索引。")
        try:
            job = queue.create(project, "index")
            job = await queue.start(job["id"], lambda: index_project(project_id))
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return envelope(job)

    @app.post("/api/v1/imports")
    async def import_local(body: ImportRequest):
        project_id = str(uuid.uuid4())
        project_root = root / "projects" / project_id
        try:
            if body.preview_id:
                if body.path or body.url or body.excludes or body.includes:
                    raise ValueError("确认导入必须使用已扫描的范围；调整范围后请重新扫描。")
                preview_id = body.preview_id
            else:
                preview_id = (await asyncio.to_thread(imports.preview, body))["preview_id"]
            imported = await asyncio.to_thread(imports.consume, preview_id, project_id)
            manifest = imported["manifest"]
        except (OSError, ValueError) as error:
            if project_root.exists() and project_root.resolve().is_relative_to((root / "projects").resolve()):
                shutil.rmtree(project_root)
            raise HTTPException(400, str(error)) from None
        existing = {item["name"] for item in database.list_projects()}
        name = imported["name"]
        count = 2
        while name in existing:
            name = f"{imported['name']} ({count})"
            count += 1
        database.insert(
            {
                "id": project_id,
                "name": name,
                "source": imported["source"],
                "created_at": now(),
                "status": "queued",
                "snapshot": manifest,
                "version_id": str(uuid.uuid4()),
                "demo": 0,
            }
        )

        job = queue.create(get_project(project_id), "index")
        if not run_lock.locked():
            await queue.start(job["id"], lambda: index_project(project_id))
        return envelope(public(get_project(project_id)))

    def version_error(project, message: str, retired: bool = False):
        return HTTPException(410 if retired else 409, {"code": "VERSION_RETIRED" if retired else "VERSION_CHANGED",
            "message": message, "project_id": project["id"], "current_version_id": project["version_id"]})

    @app.get("/api/v1/projects/{project_id}/files/{file_id}")
    async def file(project_id: str, file_id: str, version_id: str, request: Request):
        if project_id in reads.revoked:
            raise HTTPException(410, {"code": "PROJECT_DELETING", "message": "项目已开始删除，不能继续读取。"})
        with reads.read(project_id, version_id):
            project = get_project(project_id)
            if version_id != project["version_id"]:
                retired = database.version(project_id, version_id)
                if not retired:
                    raise version_error(project, "源码版本已经改变，请刷新。")
                if retired["status"] != "retired":
                    raise version_error(project, "该源码版本已失效，请刷新。", retired=True)
                identity = client_identity(request.headers.get("x-client-id")) if request.headers.get("x-client-id") else ""
                if not reads.held(identity, project_id, version_id) or (
                    datetime.now(UTC) - datetime.fromisoformat(retired["created_at"])).total_seconds() >= 120:
                    raise version_error(project, "旧源码读取租约已结束，请刷新到新版本。", retired=True)
                project = retired["value"]
            paths = {stable_id(version_id, path): path for path in project["snapshot"]["files"]}
            if file_id not in paths:
                raise HTTPException(404, "源码文件不存在。")
            path = paths[file_id]
            snapshot_root = root / "projects" / project_id / "snapshots" / project["snapshot"]["id"]
            files = await asyncio.to_thread(read_snapshot, snapshot_root, project["snapshot"])
            return envelope({"path": path, "text": files[path], "version_id": version_id,
                "hash": project["snapshot"]["files"][path]["hash"]})

    @app.get("/api/v1/projects/{project_id}/learning")
    async def learning_states(project_id: str, version_id: str):
        project = get_project(project_id)
        snapshot = database.learning_snapshot(project_id)
        if snapshot is None:
            get_project(project_id)
            raise HTTPException(410, "项目已失效，请刷新项目列表。")
        if version_id != snapshot["version_id"]:
            raise version_error({**project, "version_id": snapshot["version_id"]}, "学习记录版本已更新，请查看当前架构。")
        return envelope(snapshot)

    @app.put("/api/v1/projects/{project_id}/learning/{entity_id}")
    async def learning(project_id: str, entity_id: str, body: LearningRequest):
        project = get_project(project_id)
        if body.version_id != project["version_id"]:
            raise version_error(project, "学习状态版本已经改变，请刷新后重试本次操作。")
        result = Analysis.model_validate(project["result"] or {})
        architecture = Architecture.model_validate(project["architecture"]) if project["architecture"] else None
        if not any(unit["id"] == entity_id for unit in learning_units(result, architecture, project["snapshot"]["files"])):
            raise HTTPException(422, "该对象不可学习；父模块、空模块和不完整占位不能手动标记。")
        try:
            state = database.set_learning(
                project_id, entity_id, body.version_id, body.state, body.expected_revision
            )
        except ValueError as error:
            latest = get_project(project_id)
            if latest["version_id"] != body.version_id:
                raise version_error(latest, "学习状态版本已经改变，请刷新后重试本次操作。") from None
            raise HTTPException(409, {"code": "LEARNING_CONFLICT", "message": str(error)}) from None
        return envelope(state)

    @app.get("/api/v1/projects/{project_id}/session")
    async def get_session(project_id: str):
        get_project(project_id)
        return envelope(database.session(project_id))

    @app.put("/api/v1/projects/{project_id}/session")
    async def put_session(project_id: str, body: SessionRequest):
        project = get_project(project_id)
        if body.version_id != project["version_id"]:
            raise version_error(project, "探索版本已失效，请刷新到当前架构。")
        from .sessions import validate_session
        try:
            value = body.model_dump()
            validate_session(value, project)
            return envelope(database.session(project_id, value))
        except ValueError as error:
            if str(error) == "VERSION_CHANGED":
                raise version_error(get_project(project_id), "探索版本已失效，请刷新到当前架构。") from None
            if str(error) == "SESSION_CONFLICT":
                raise HTTPException(409, {"code": "SESSION_CONFLICT", "message": "其他页面已保存新的探索记录，请载入已保存记录；本页未覆盖它。"}) from None
            raise HTTPException(422, "探索记录包含不属于当前快照的引用。") from None

    @app.get("/api/v1/settings/ai")
    async def settings():
        return envelope(provider.settings())

    @app.put("/api/v1/settings/ai")
    async def set_settings(body: AISettings):
        if run_lock.locked():
            raise HTTPException(409, "任务运行期间不能切换 AI 接收端或模型。")
        try:
            return envelope(provider.configure(body.base_url, body.model, body.key, body.stream_response))
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    @app.post("/api/v1/settings/ai/test")
    async def test_ai():
        if run_lock.locked():
            raise HTTPException(409, "已有任务运行，请等待后测试连接。")
        try:
            async with run_lock:
                return envelope(await provider.test())
        except ProviderError as error:
            raise HTTPException(503, str(error)) from None

    @app.post("/api/v1/projects/{project_id}/consent")
    async def consent(project_id: str, request: Request):
        project = get_project(project_id)
        body = await request.json()
        if body.get("accepted") is not True:
            raise HTTPException(400, "需要明确同意上传当前范围源码。")
        document = read_consents()
        version_id = body.get("version_id", project["version_id"])
        candidate = database.candidate(project_id)
        if version_id != project["version_id"] and (not candidate or version_id != candidate["id"]):
            raise HTTPException(409, "授权版本已改变，请重新确认。")
        manifest = candidate["value"]["snapshot"] if candidate and version_id == candidate["id"] else project["snapshot"]
        document[project_id + ":" + version_id] = {
            "base_url": provider.settings()["base_url"],
            "snapshot_hash": manifest["hash"],
            "granted_at": now(),
        }
        save_consents(document)
        return envelope({"accepted": True})

    @app.delete("/api/v1/projects/{project_id}/consent")
    async def revoke_consent(project_id: str, version_id: str):
        project = get_project(project_id)
        candidate = database.candidate(project_id)
        if version_id != project["version_id"] and (not candidate or version_id != candidate["id"]):
            raise HTTPException(409, "授权版本已改变，请刷新后重试。")
        document = read_consents()
        document.pop(project_id + ":" + version_id, None)
        if version_id == project["version_id"]:
            document.pop(project_id, None)
        save_consents(document)
        # Revoke first, then stop the associated upload task. An already-sent
        # request may still be processed/charged remotely; receipts remain.
        for job in database.list_jobs():
            target_version = candidate["id"] if job["kind"] == "reanalysis" and candidate else job["version_id"]
            if (job["project_id"] == project_id and target_version == version_id and job["kind"] != "index"
                    and job["status"] in {"waiting", "running", "paused"}):
                await queue.stop(job["id"])
                database.update(project_id, status="paused", error="上传授权已撤销，分析已暂停。")
        return envelope({"accepted": False, "version_id": version_id})

    @app.post("/api/v1/projects/{project_id}/analyze")
    async def ai_analysis(project_id: str):
        project = get_project(project_id)
        candidate = database.candidate(project_id)
        base_project = project
        if (not candidate and project["architecture"] and not any(
                "人工预置" in item for item in project["architecture"]["overview"]["limitations"])):
            raise HTTPException(409, "已有成功版本，请先重新扫描并创建候选，旧结果将保持可读。")
        if candidate:
            if candidate["base_version_id"] != project["version_id"]:
                raise HTTPException(409, "候选基线已失效，请取消并重新扫描。")
            project = {**project, **candidate["value"], "version_id": candidate["id"]}
        consents = read_consents()
        granted = consents.get(project_id + ":" + project["version_id"], {} if candidate else consents.get(project_id, {}))
        if (
            granted.get("base_url") != provider.settings()["base_url"]
            or granted.get("snapshot_hash") != project["snapshot"]["hash"]
        ):
            raise HTTPException(403, "请先授权将当前范围源码发送到所选 AI 接收端。")
        if not project["result"] and not candidate:
            raise HTTPException(409, "请等待静态索引完成。")
        try:
            job = queue.create(base_project, "reanalysis" if candidate else "architecture")
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        if job["status"] == "running":
            raise HTTPException(409, "当前项目的分析已经运行。")
        if run_lock.locked():
            database.update(project_id, status="waiting", error=None)
            return envelope({"status": "waiting", "task_id": job["id"]})

        async def work():
            checkpoint_path = root / "projects" / project_id / (f"checkpoint-{project['version_id']}.json" if candidate else "architecture-checkpoint.json")
            checkpoint: list[Architecture] = []
            published = False
            database.update(project_id, status="analyzing", error=None)
            database.analysis_progress(
                project_id, {"phase": "planning", "completed": 0, "total": 0,
                             "detail": "正在估算源码批次"},
            )
            try:
                await asyncio.sleep(0)
                model_config = receiver_identity(provider.settings())
                if candidate and not project["result"]:
                    await index_project(project_id, candidate["id"])
                    project["result"] = database.version(project_id, candidate["id"])["value"]["result"]
                    database.update(project_id, status="analyzing", error=None)
                snapshot_root = root / "projects" / project_id / "snapshots" / project["snapshot"]["id"]
                if checkpoint_path.exists():
                    try:
                        saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                        if (
                            isinstance(saved, dict)
                            and saved.get("version_id") == project["version_id"]
                            and saved.get("snapshot_hash") == project["snapshot"]["hash"]
                            and compatible_source_checkpoint(saved)
                            and saved.get('provider_config') == model_config
                        ):
                            checkpoint = [Architecture.model_validate(item) for item in saved.get("batches", [])]
                    except (OSError, ValueError, TypeError):
                        checkpoint = []
                if checkpoint:
                    database.analysis_progress(
                        project_id,
                        {
                            "phase": "planning",
                            "completed": len(checkpoint),
                            "total": 0,
                            "detail": f"发现 {len(checkpoint)} 批检查点，准备继续",
                        },
                    )

                async def progress(completed: int, total: int, detail: str):
                    database.analysis_progress(
                        project_id,
                        {
                            "phase": "batching",
                            "completed": completed,
                            "total": total,
                            "detail": detail,
                        },
                    )

                async def save_batch(completed: int, total: int, architecture: Architecture):
                    current_job = database.get_job(job["id"])
                    if not current_job or current_job["status"] != "running":
                        raise asyncio.CancelledError
                    saved_batches = checkpoint + [architecture]
                    temporary = checkpoint_path.with_suffix(".tmp")
                    with temporary.open("w", encoding="utf-8") as handle:
                        json.dump(
                            {
                                "version_id": project["version_id"],
                                "snapshot_hash": project["snapshot"]["hash"],
                                "pipeline_version": PIPELINE_VERSION,
                                "source_pipeline_version": SOURCE_PIPELINE_VERSION,
                                "provider_config": model_config,
                                "batches": [item.model_dump() for item in saved_batches],
                            },
                            handle, ensure_ascii=False,
                        )
                        handle.flush()
                        os.fsync(handle.fileno())
                    temporary.replace(checkpoint_path)
                    checkpoint.clear()
                    checkpoint.extend(saved_batches)

                source_files = read_snapshot(snapshot_root, project["snapshot"])
                source_analysis = Analysis.model_validate(project["result"])
                prepared_version = database.version(project_id, project["version_id"])
                prepared = prepared_version["value"].get("prepared_output") if prepared_version else None
                if (prepared and prepared.get("pipeline_version") == PIPELINE_VERSION
                        and prepared.get('provider_config') == model_config):
                    saved = load_output(root / "projects" / project_id, project["version_id"], prepared)
                    if saved["snapshot_hash"] != project["snapshot"]["hash"] or saved["result"] != project["result"]:
                        raise ValueError("已保存的分析产物与当前快照不一致，未重新请求模型。")
                    architecture, usage = Architecture.model_validate(saved["architecture"]), saved["usage"]
                else:
                    with ai_scope(project_id=project_id, version_id=project["version_id"], job_id=job["id"]):
                        callbacks = {"on_progress": progress, "on_batch": save_batch, "completed_batches": checkpoint}
                        if architecture_runner:
                            architecture, usage = await architecture_runner(provider, source_analysis, source_files, **callbacks)
                        else:
                            architecture, usage = await run_architecture(provider, source_analysis, source_files,
                                project_id=project_id, version_id=project["version_id"], job_id=job["id"], **callbacks)
                current_job = database.get_job(job["id"])
                if not current_job or current_job["status"] != "running":
                    raise asyncio.CancelledError
                validate_architecture(architecture, Analysis.model_validate(project["result"]), set(project["snapshot"]["files"]))
                validate_explanations(architecture, source_analysis, {entity.id for entity in source_analysis.entities if entity.visible},
                                      source_files, {path: [(1, max(1, len(text.splitlines())))] for path, text in source_files.items()})
                validate_judgments(architecture, source_analysis, {call.id for call in source_analysis.calls}, source_files,
                                   {path: [(1, max(1, len(text.splitlines())))] for path, text in source_files.items()})
                validate_primary_dependencies(architecture, source_analysis)
                read_snapshot(snapshot_root, project["snapshot"])
                # Both first analysis and reanalysis save and verify output before a short transaction.
                descriptor = prepare_output(root / "projects" / project_id, project["version_id"],
                    project["snapshot"]["hash"], PIPELINE_VERSION, project["result"], architecture.model_dump(), usage,
                    provider_config=model_config)
                database.prepare_publication(project_id, project["version_id"], job["id"], descriptor, candidate=bool(candidate))
                if candidate:
                    database.publish_candidate(project_id, candidate["id"], job["id"], architecture.model_dump())
                else:
                    database.publish_initial(project_id, project["version_id"], job["id"], architecture.model_dump())
                published = True
                checkpoint_path.unlink(missing_ok=True)
                usage_path = root / "projects" / project_id / "usage.json"
                usage_path.write_text(json.dumps(usage), encoding="utf-8")
            except asyncio.CancelledError:
                if published:
                    return
                database.update(project_id, status="paused", error="分析已中断，请主动重新启动。")
                previous = database.analysis_progress(project_id) or {}
                database.analysis_progress(
                    project_id,
                    {
                        "phase": "paused",
                        "completed": len(checkpoint),
                        "total": previous.get("total", 0),
                        "detail": "分析已中断，请主动重新启动",
                    },
                )
                raise
            except Exception as error:
                if published:
                    # Cleanup failures cannot turn a committed version into a paused task.
                    return
                database.update(project_id, status="paused", error=str(error)[:300])
                previous = database.analysis_progress(project_id) or {}
                database.analysis_progress(
                    project_id,
                    {
                        "phase": "paused",
                        "completed": len(checkpoint),
                        "total": previous.get("total", 0),
                        "detail": str(error)[:300],
                    },
                )
        try:
            await queue.start(job["id"], work)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return envelope({"status": "analyzing", "task_id": job["id"]})

    @app.get("/api/v1/tasks")
    async def task_list():
        names = {project["id"]: project["name"] for project in database.list_projects()}
        return envelope([{**job, "project_name": names.get(job["project_id"], "已删除项目"),
                          "progress": database.analysis_progress(job["project_id"])}
                         for job in database.list_jobs()])

    @app.get("/api/v1/usage")
    async def ai_usage(project_id: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        if project_id:
            get_project(project_id)
        return envelope(ledger.summary(project_id, offset, limit))

    @app.put("/api/v1/tasks/order")
    async def task_order(request: Request):
        body = await request.json()
        ids = body.get("ids")
        if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids):
            raise HTTPException(422, "请提供完整的等待任务顺序。")
        try:
            database.reorder_jobs(ids)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return await task_list()

    @app.post("/api/v1/tasks/{job_id}/start")
    async def task_start(job_id: str):
        job = database.get_job(job_id)
        if not job:
            raise HTTPException(404, "任务不存在。")
        if job["status"] not in {"waiting", "paused"}:
            raise HTTPException(409, "此任务不能启动，请刷新状态。")
        project = get_project(job["project_id"])
        if project["version_id"] != job["version_id"]:
            raise HTTPException(409, "任务源码版本已失效，请创建新的分析。")
        if run_lock.locked():
            raise HTTPException(409, "已有任务运行，请等待后主动启动。")
        if job["kind"] == "index":
            return await retry_index(job["project_id"])
        return await ai_analysis(job["project_id"])

    @app.post("/api/v1/tasks/{job_id}/pause")
    async def task_pause(job_id: str):
        try:
            job = await queue.stop(job_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        database.update(job["project_id"], status="paused", error="任务已暂停，请主动恢复。")
        return envelope(job)

    @app.post("/api/v1/tasks/{job_id}/cancel")
    async def task_cancel(job_id: str):
        try:
            job = await queue.stop(job_id, cancel=True)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        project = get_project(job["project_id"])
        database.update(job["project_id"], status="ready" if project["architecture"] else "cancelled",
                        error=None)
        if job["kind"] == "architecture":
            (root / "projects" / job["project_id"] / "architecture-checkpoint.json").unlink(missing_ok=True)
        if job["kind"] == "reanalysis":
            candidate = database.candidate(job["project_id"])
            if candidate:
                database.update_candidate(candidate["id"], candidate["value"], "cancelled")
                (root / "projects" / job["project_id"] / f"checkpoint-{candidate['id']}.json").unlink(missing_ok=True)
        return envelope(job)

    @app.post("/api/v1/projects/{project_id}/overview")
    async def refresh_overview(project_id: str):
        project = get_project(project_id)
        if not project["architecture"] or project["status"] != "ready":
            raise HTTPException(409, "请先完成架构分析，再归纳项目概览。")
        consents = read_consents()
        granted = consents.get(project_id + ":" + project["version_id"], consents.get(project_id, {}))
        if (
            granted.get("base_url") != provider.settings()["base_url"]
            or granted.get("snapshot_hash") != project["snapshot"]["hash"]
        ):
            raise HTTPException(403, "请先在 AI 架构分析中授权当前快照与接收端，再更新概览。")
        if run_lock.locked():
            raise HTTPException(409, "已有任务运行，请等待后主动启动。")
        async with run_lock:
            architecture = Architecture.model_validate(project["architecture"])
            try:
                with ai_scope(project_id=project_id, version_id=project["version_id"], phase="overview"):
                    overview, usage = await provider.project_overview(
                        architecture, Analysis.model_validate(project["result"])
                    )
            except ProviderError as error:
                raise HTTPException(503, str(error)) from None
            architecture.overview = overview
            database.update(project_id, architecture=architecture.model_dump())
            usage_path = root / "projects" / project_id / "overview-usage.json"
            usage_path.write_text(json.dumps(usage), encoding="utf-8")
        return envelope(public(get_project(project_id), full=True))

    dist = REPOSITORY / "apps/web/dist"

    @app.get("/{path:path}")
    async def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "接口不存在。")
        target = (dist / path).resolve()
        if target.is_relative_to(dist.resolve()) and target.is_file():
            return FileResponse(target)
        if path.startswith('assets/'):
            return JSONResponse({'message':'页面资源已更新，请刷新后重试。'},status_code=404,
                headers={'Cache-Control':'no-store'})
        if (dist / "index.html").exists():
            return FileResponse(dist / "index.html")
        return JSONResponse({"message": "前端尚未构建，请先执行 npm run build。"}, status_code=503)

    return app
