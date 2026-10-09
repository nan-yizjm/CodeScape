from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .analyzers import REPOSITORY, analyze, stable_id
from .database import Database, now
from .models import (
    AISettings,
    Analysis,
    Architecture,
    ImportRequest,
    LearningRequest,
    Module,
    Overview,
    SessionRequest,
)
from .provider import Provider, ProviderError
from .security import data_root
from .snapshots import create_snapshot, read_snapshot


def create_app(root: Path | None = None, nonce: str | None = None) -> FastAPI:
    root = root or data_root()
    database = Database(root)
    database.recover_interrupted()
    provider = Provider(root)
    bootstrap_nonce = nonce or secrets.token_urlsafe(32)
    session_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    cookie_name = "codescape_" + secrets.token_hex(8)
    nonce_used = False
    nonce_expires = time.monotonic() + 300
    tasks: set[asyncio.Task] = set()
    run_lock = asyncio.Lock()
    consents_path = root / "consents.json"

    def get_project(project_id: str) -> dict:
        project = database.get(project_id)
        if not project:
            raise HTTPException(404, "项目不存在。")
        return project

    def envelope(data):
        return {"data": data, "meta": {"request_id": str(uuid.uuid4())}}

    def public(project: dict, full: bool = False):
        analysis = Analysis.model_validate(project["result"]) if project["result"] else Analysis()
        visible = [entity for entity in analysis.entities if entity.visible]
        states = database.states(project["id"])
        total = len(visible)
        mastered = sum(states.get(entity.id, {}).get("state") == "mastered" for entity in visible)
        response = {
            "id": project["id"],
            "name": project["name"],
            "source": project["source"],
            "created_at": project["created_at"],
            "status": project["status"],
            "version_id": project["version_id"],
            "demo": bool(project["demo"]),
            "error": project["error"],
            "function_count": total,
            "file_count": len(project["snapshot"]["files"]),
            "progress": {"mastered": mastered, "total": total},
            "languages": sorted({entity.language for entity in visible}),
            "diagnostic_count": len(analysis.diagnostics),
            "has_architecture": bool(project["architecture"]),
            "analysis_progress": database.analysis_progress(project["id"]),
        }
        if full:
            response.update(
                {
                    "analysis": analysis.model_dump(),
                    "architecture": project["architecture"],
                    "file_ids": {
                        path: stable_id(project["version_id"], path) for path in project["snapshot"]["files"]
                    },
                    "learning": states,
                    "scope": {
                        "excluded": project["snapshot"]["excluded"],
                        "snapshot_id": project["snapshot"]["id"],
                    },
                }
            )
        return response

    def seed():
        if any(project["demo"] for project in database.list_projects()):
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

    @asynccontextmanager
    async def lifespan(_):
        seed()
        yield
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        database.engine.dispose()

    app = FastAPI(
        title="CodeScape 本地服务", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.state.nonce = bootstrap_nonce
    app.state.root = root

    @app.middleware("http")
    async def protect(request: Request, call_next):
        if request.url.path.startswith("/api/"):
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
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_, error: HTTPException):
        return JSONResponse(
            {"error": {"code": str(error.status_code), "message": error.detail}},
            status_code=error.status_code,
        )

    @app.post("/api/v1/session/exchange")
    async def exchange(request: Request):
        nonlocal nonce_used
        body = await request.json()
        if (
            nonce_used
            or time.monotonic() > nonce_expires
            or not secrets.compare_digest(str(body.get("nonce", "")), bootstrap_nonce)
        ):
            raise HTTPException(401, "启动凭证已失效，请重新通过启动入口打开。")
        nonce_used = True
        response = JSONResponse(envelope({"csrf_token": csrf_token}))
        response.set_cookie(cookie_name, session_token, httponly=True, samesite="strict")
        return response

    @app.get("/api/v1/session")
    async def session_info():
        return envelope({"csrf_token": csrf_token, "stage": "M0", "version": "0.1.0"})

    @app.get("/api/v1/projects")
    async def projects():
        return envelope([public(project) for project in database.list_projects()])

    @app.get("/api/v1/projects/{project_id}")
    async def project(project_id: str):
        return envelope(public(get_project(project_id), True))

    async def index_project(project_id: str):
        project = get_project(project_id)
        snapshot_root = root / "projects" / project_id / "snapshots" / project["snapshot"]["id"]
        database.update(project_id, status="indexing", error=None)
        try:

            def run():
                completed = subprocess.run(
                    [sys.executable, str(REPOSITORY / "scripts/analyze_snapshot.py")],
                    input=json.dumps(
                        {
                            "root": str(snapshot_root),
                            "manifest": project["snapshot"],
                            "version": project["version_id"],
                        }
                    ),
                    encoding="utf-8",
                    text=True,
                    capture_output=True,
                    timeout=120,
                    cwd=REPOSITORY,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if completed.returncode:
                    raise ValueError("静态解析进程失败，原始源码与旧结果未被修改。")
                return Analysis.model_validate_json(completed.stdout)

            result = await asyncio.to_thread(run)
            database.update(project_id, result=result.model_dump(), status="indexed")
        except Exception as error:
            database.update(project_id, status="failed", error=str(error)[:300])

    @app.post("/api/v1/imports")
    async def import_local(body: ImportRequest):
        source = Path(body.path)
        if not source.is_absolute() or not source.is_dir():
            raise HTTPException(400, "请输入存在的本地源码目录绝对路径。")
        project_id = str(uuid.uuid4())
        project_root = root / "projects" / project_id
        try:
            manifest = await asyncio.to_thread(create_snapshot, source, project_root, body.excludes)
            if not manifest["files"]:
                raise ValueError("当前范围没有支持的 UTF-8 源码文件。")
        except (OSError, ValueError) as error:
            if project_root.exists() and project_root.resolve().is_relative_to((root / "projects").resolve()):
                shutil.rmtree(project_root)
            raise HTTPException(400, str(error)) from None
        existing = {item["name"] for item in database.list_projects()}
        name = source.name
        count = 2
        while name in existing:
            name = f"{source.name} ({count})"
            count += 1
        database.insert(
            {
                "id": project_id,
                "name": name,
                "source": str(source),
                "created_at": now(),
                "status": "queued",
                "snapshot": manifest,
                "version_id": str(uuid.uuid4()),
                "demo": 0,
            }
        )

        async def work():
            async with run_lock:
                await index_project(project_id)

        task = asyncio.create_task(work())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return envelope(public(get_project(project_id)))

    @app.get("/api/v1/projects/{project_id}/files/{file_id}")
    async def file(project_id: str, file_id: str, version_id: str):
        project = get_project(project_id)
        if version_id != project["version_id"]:
            raise HTTPException(409, "源码版本已经改变，请刷新。")
        paths = {stable_id(version_id, path): path for path in project["snapshot"]["files"]}
        if file_id not in paths:
            raise HTTPException(404, "源码文件不存在。")
        path = paths[file_id]
        snapshot_root = root / "projects" / project_id / "snapshots" / project["snapshot"]["id"]
        files = read_snapshot(snapshot_root, project["snapshot"])
        return envelope(
            {
                "path": path,
                "text": files[path],
                "version_id": version_id,
                "hash": project["snapshot"]["files"][path]["hash"],
            }
        )

    @app.put("/api/v1/projects/{project_id}/learning/{entity_id}")
    async def learning(project_id: str, entity_id: str, body: LearningRequest):
        project = get_project(project_id)
        if body.version_id != project["version_id"]:
            raise HTTPException(409, "学习状态版本已经改变，请刷新。")
        result = Analysis.model_validate(project["result"] or {})
        if not any(entity.id == entity_id and entity.visible for entity in result.entities):
            raise HTTPException(422, "该对象不是可学习的函数。")
        try:
            state = database.set_learning(
                project_id, entity_id, body.version_id, body.state, body.expected_revision
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return envelope(state)

    @app.get("/api/v1/projects/{project_id}/session")
    async def get_session(project_id: str):
        get_project(project_id)
        return envelope(database.session(project_id))

    @app.put("/api/v1/projects/{project_id}/session")
    async def put_session(project_id: str, body: SessionRequest):
        project = get_project(project_id)
        if body.version_id != project["version_id"]:
            raise HTTPException(409, "探索版本已失效。")
        return envelope(database.session(project_id, body.model_dump()))

    @app.get("/api/v1/settings/ai")
    async def settings():
        return envelope(provider.settings())

    @app.put("/api/v1/settings/ai")
    async def set_settings(body: AISettings):
        if run_lock.locked():
            raise HTTPException(409, "任务运行期间不能切换 AI 接收端或模型。")
        try:
            return envelope(provider.configure(body.base_url, body.model, body.key))
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    @app.post("/api/v1/settings/ai/test")
    async def test_ai():
        try:
            return envelope(await provider.test())
        except ProviderError as error:
            raise HTTPException(503, str(error)) from None

    @app.post("/api/v1/projects/{project_id}/consent")
    async def consent(project_id: str, request: Request):
        project = get_project(project_id)
        body = await request.json()
        if body.get("accepted") is not True:
            raise HTTPException(400, "需要明确同意上传当前范围源码。")
        document = json.loads(consents_path.read_text()) if consents_path.exists() else {}
        document[project_id] = {
            "base_url": provider.settings()["base_url"],
            "snapshot_hash": project["snapshot"]["hash"],
            "granted_at": now(),
        }
        consents_path.write_text(json.dumps(document), encoding="utf-8")
        return envelope({"accepted": True})

    @app.post("/api/v1/projects/{project_id}/analyze")
    async def ai_analysis(project_id: str):
        project = get_project(project_id)
        consents = json.loads(consents_path.read_text()) if consents_path.exists() else {}
        granted = consents.get(project_id, {})
        if (
            granted.get("base_url") != provider.settings()["base_url"]
            or granted.get("snapshot_hash") != project["snapshot"]["hash"]
        ):
            raise HTTPException(403, "请先授权将当前范围源码发送到所选 AI 接收端。")
        if not project["result"]:
            raise HTTPException(409, "请等待静态索引完成。")
        if run_lock.locked():
            raise HTTPException(409, "已有任务运行，请等待后主动启动。")
        await run_lock.acquire()
        database.update(project_id, status="analyzing", error=None)
        database.analysis_progress(
            project_id,
            {"phase": "planning", "completed": 0, "total": 0, "detail": "正在估算源码批次"},
        )

        async def work():
            checkpoint_path = root / "projects" / project_id / "architecture-checkpoint.json"
            try:
                snapshot_root = root / "projects" / project_id / "snapshots" / project["snapshot"]["id"]
                checkpoint: list[Architecture] = []
                if checkpoint_path.exists():
                    try:
                        saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                        if (
                            saved.get("version_id") == project["version_id"]
                            and saved.get("snapshot_hash") == project["snapshot"]["hash"]
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
                    saved_batches = checkpoint + [architecture]
                    temporary = checkpoint_path.with_suffix(".tmp")
                    temporary.write_text(
                        json.dumps(
                            {
                                "version_id": project["version_id"],
                                "snapshot_hash": project["snapshot"]["hash"],
                                "batches": [item.model_dump() for item in saved_batches],
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    temporary.replace(checkpoint_path)
                    checkpoint.clear()
                    checkpoint.extend(saved_batches)

                architecture, usage = await provider.architecture(
                    Analysis.model_validate(project["result"]),
                    read_snapshot(snapshot_root, project["snapshot"]),
                    on_progress=progress,
                    on_batch=save_batch,
                    completed_batches=checkpoint,
                )
                database.update(
                    project_id, architecture=architecture.model_dump(), status="ready", error=None
                )
                database.analysis_progress(
                    project_id,
                    {
                        "phase": "completed",
                        "completed": 1,
                        "total": 1,
                        "detail": "分批分析完成",
                    },
                )
                checkpoint_path.unlink(missing_ok=True)
                usage_path = root / "projects" / project_id / "usage.json"
                usage_path.write_text(json.dumps(usage), encoding="utf-8")
            except asyncio.CancelledError:
                database.update(project_id, status="paused", error="分析已中断，请主动重新启动。")
                database.analysis_progress(
                    project_id,
                    {"phase": "paused", "completed": 0, "total": 0, "detail": "分析已中断，请主动重新启动"},
                )
                raise
            except Exception as error:
                database.update(project_id, status="paused", error=str(error)[:300])
                database.analysis_progress(
                    project_id,
                    {"phase": "paused", "completed": 0, "total": 0, "detail": str(error)[:300]},
                )
            finally:
                run_lock.release()

        task = asyncio.create_task(work())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return envelope({"status": "analyzing"})

    dist = REPOSITORY / "apps/web/dist"

    @app.get("/{path:path}")
    async def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "接口不存在。")
        target = (dist / path).resolve()
        if target.is_relative_to(dist.resolve()) and target.is_file():
            return FileResponse(target)
        if (dist / "index.html").exists():
            return FileResponse(dist / "index.html")
        return JSONResponse({"message": "前端尚未构建，请先执行 npm run build。"}, status_code=503)

    return app
