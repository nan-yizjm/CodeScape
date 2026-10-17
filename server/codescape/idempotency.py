"""Durable mutation receipts; uncertain requests are never automatically performed twice."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid

from sqlalchemy import select
from starlette.responses import JSONResponse, Response

from .security import _crypt

PREFIX = "http-request:"
RETENTION_SECONDS = 24 * 3600


def deleted_response():
    return json.dumps({"error": {"code": "PROJECT_DELETED", "message": "该项目已删除；旧请求没有重新创建项目。"}}, ensure_ascii=False).encode()


def redact_project_receipts(connection, table, project_id):
    for row in connection.execute(select(table).where(table.c.key.startswith(PREFIX),
            table.c.value["project_id"].as_string() == project_id)).mappings():
        if row["value"].get("delete_intent"):
            continue
        value = {**row["value"], "status": "complete", "http_status": 410, "response": _crypt(deleted_response()).hex()}
        connection.execute(table.update().where(table.c.key == row["key"]).values(value=value))


def protected_request(method: str, path: str) -> bool:
    if method == "DELETE":
        return bool(re.fullmatch(r"/api/v1/projects/[^/]+(?:/versions/[^/]+)?", path))
    if method == "PUT":
        return path == "/api/v1/tasks/order"
    if method != "POST":
        return False
    return bool(re.fullmatch(r"/api/v1/(?:imports(?:/preview)?|settings/ai/test|"
        r"projects/[^/]+/(?:analyze|index|overview|reanalysis(?:/preview)?)|"
        r"tasks/[^/]+/(?:start|pause|cancel))", path))


def failure(code, message, status=409):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


class MutationReceipts:
    def __init__(self, database, clock=time.time):
        self.database, self.clock = database, clock
        self.table = database.app_metadata
        self.pending: dict[str, asyncio.Event] = {}

    def recover(self):
        with self.database.engine.begin() as connection:
            for row in connection.execute(select(self.table).where(self.table.c.key.startswith(PREFIX))).mappings():
                if not isinstance(row["value"], dict):
                    connection.execute(self.table.update().where(self.table.c.key == row["key"]).values(value={
                        "status": "indeterminate", "digest": None, "expires_at": self.clock() + RETENTION_SECONDS}))
                elif row["value"].get("status") == "processing":
                    connection.execute(self.table.update().where(self.table.c.key == row["key"])
                        .values(value=dict(row["value"], status="indeterminate", expires_at=self.clock() + RETENTION_SECONDS)))

    def claim(self, key, digest):
        moment = self.clock()
        with self.database.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            connection.execute(self.table.delete().where(self.table.c.key.startswith(PREFIX),
                self.table.c.value["expires_at"].as_float() <= moment,
                self.table.c.value["status"].as_string() != "processing"))
            value = connection.execute(select(self.table.c.value).where(self.table.c.key == key)).scalar_one_or_none()
            if value is None:
                value = {"status": "processing", "digest": digest, "created_at": moment,
                    "expires_at": moment + RETENTION_SECONDS}
                connection.execute(self.table.insert().values(key=key, value=value))
                connection.commit()
                return True, value
            connection.commit()
            return False, value

    def update(self, key, **values):
        if values.get("status") in {"complete", "indeterminate"}:
            values["expires_at"] = self.clock() + RETENTION_SECONDS
        with self.database.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            current = connection.execute(select(self.table.c.value).where(self.table.c.key == key)).scalar_one()
            current = {**current, **values}
            project_id = current.get("project_id")
            if (current.get("status") == "complete" and project_id and not current.get("delete_intent")
                    and not connection.execute(select(self.database.projects.c.id).where(self.database.projects.c.id == project_id)).first()):
                current.update(http_status=410, response=_crypt(deleted_response()).hex())
            connection.execute(self.table.update().where(self.table.c.key == key).values(value=current))
            connection.commit()
            return current

    def project_id(self, path, body):
        match = re.match(r"/api/v1/projects/([^/]+)", path)
        identity = match[1] if match else None
        if not identity and path == "/api/v1/imports":
            try:
                identity = json.loads(body).get("data", {}).get("id")
            except (ValueError, TypeError, AttributeError):
                return None
        if not identity:
            match = re.match(r"/api/v1/tasks/([^/]+)", path)
            if match:
                job = self.database.get_job(match[1])
                identity = job["project_id"] if job else None
        try:
            return str(uuid.UUID(identity)) if identity else None
        except (ValueError, TypeError, AttributeError):
            return None

    def replay(self, value):
        if value["status"] != "complete":
            return failure("REQUEST_INDETERMINATE", "该请求的结果需要核对，请查看项目与任务列表；系统没有重复执行操作。")
        body = _crypt(bytes.fromhex(value["response"]), True)
        return Response(body, status_code=value["http_status"], media_type="application/json",
            headers={"Idempotency-Replayed": "true"})

    def uncertain(self, key):
        try:
            self.update(key, status="indeterminate")
        except Exception:
            pass

    async def handle(self, request, call_next):
        supplied = request.headers.get("idempotency-key")
        # Earlier local clients are supported; the current frontend keys every protected action.
        if not supplied or not protected_request(request.method, request.url.path):
            return await call_next(request)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", supplied):
            return failure("IDEMPOTENCY_KEY", "请求标识格式无效。", 422)
        key = PREFIX + hashlib.sha256(supplied.encode()).hexdigest()
        try:
            body = await request.json() if await request.body() else None
            encoded = json.dumps([request.method, request.url.path, str(request.url.query), body],
                ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (ValueError, TypeError):
            return failure("INVALID_JSON", "请求正文不是有效 JSON。", 422)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        try:
            leader, receipt = self.claim(key, digest)
        except Exception:
            return failure("REQUEST_STORAGE", "请求记录无法保存，尚未开始操作；请检查应用数据目录。", 503)
        if not isinstance(receipt, dict) or not isinstance(receipt.get("digest"), str):
            return failure("REQUEST_RECEIPT", "请求记录无法核验，系统没有重复执行；请查看项目与任务状态。", 503)
        if receipt["digest"] != digest:
            return failure("IDEMPOTENCY_CONFLICT", "同一请求标识不能用于不同操作或正文。")
        if not leader:
            pending = self.pending.get(key)
            if receipt.get("status") == "processing" and pending:
                try:
                    await asyncio.wait_for(pending.wait(), timeout=10)
                except TimeoutError:
                    return failure("REQUEST_PENDING", "同一操作仍在处理中，请查看项目与任务状态；没有重复执行。")
                try:
                    _, receipt = self.claim(key, digest)
                except Exception:
                    return failure("REQUEST_RECEIPT", "请求记录无法核验，系统没有重复执行；请查看项目与任务状态。", 503)
            try:
                return self.replay(receipt)
            except Exception:
                return failure("REQUEST_RECEIPT", "请求记录无法核验，系统没有重复执行；请查看项目与任务状态。", 503)
        completed = asyncio.Event()
        self.pending[key] = completed
        try:
            response = await call_next(request)
            chunks = [chunk async for chunk in response.body_iterator]
            body = b"".join(chunk.encode() if isinstance(chunk, str) else chunk for chunk in chunks)
            # Accidental validation values cannot become plaintext receipts either.
            saved = self.update(key, status="complete", http_status=response.status_code, response=_crypt(body).hex(),
                project_id=self.project_id(request.url.path, body), delete_intent=request.method == "DELETE")
            actual_body = _crypt(bytes.fromhex(saved["response"]), True)
            headers = dict(response.headers)
            headers.pop("content-length", None)
            return Response(actual_body, status_code=saved["http_status"], headers=headers, background=response.background)
        except asyncio.CancelledError:
            self.uncertain(key)
            raise
        except Exception:
            self.uncertain(key)
            return failure("REQUEST_INDETERMINATE", "请求结果尚未确认，请查看项目与任务状态；系统不会自动重复操作。", 503)
        finally:
            completed.set()
            self.pending.pop(key, None)
