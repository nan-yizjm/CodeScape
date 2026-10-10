from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from sqlalchemy import select

from .database import now

request_scope: ContextVar[dict] = ContextVar("request_scope", default={})


@dataclass
class ModelBudget:
    unit_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    attempts: int = 0
    last_request: str | None = None


model_budget: ContextVar[ModelBudget | None] = ContextVar("model_budget", default=None)


@contextmanager
def model_unit():
    previous = model_budget.get()
    if previous is not None:
        yield previous
        return
    budget = ModelBudget()
    token = model_budget.set(budget)
    try:
        yield budget
    finally:
        model_budget.reset(token)


@contextmanager
def ai_scope(**values):
    token = request_scope.set({**request_scope.get(), **values})
    try:
        yield
    finally:
        request_scope.reset(token)


def known_usage(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    clean = {key: amount for key in ("prompt_tokens", "completion_tokens", "total_tokens",
             "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
             if isinstance((amount := value.get(key)), int) and not isinstance(amount, bool) and amount >= 0}
    return clean or None


class RequestLedger:
    def __init__(self, database):
        self.database = database
        self.table = database.requests

    def prepare(self, config, payload, unit_id, attempt):
        scope = request_scope.get()
        identifier = str(uuid.uuid4())
        document = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        record = {"id": identifier, "project_id": scope.get("project_id"), "version_id": scope.get("version_id"),
                  "job_id": scope.get("job_id"), "phase": scope.get("phase", "connection"), "unit_id": unit_id,
                  "attempt": attempt, "recipient": config["base_url"], "model": config["model"],
                  "status": "prepared", "created_at": now(), "payload_hash": hashlib.sha256(document.encode()).hexdigest(),
                  "source_chars": scope.get("source_chars", 0)}
        with self.database.engine.begin() as connection:
            connection.execute(self.table.insert().values(**record))
        return identifier

    def sent(self, identifier):
        self.finish(identifier, "sent", sent_at=now())

    def finish(self, identifier, status, **values):
        if status != "sent":
            values["finished_at"] = now()
        with self.database.engine.begin() as connection:
            connection.execute(self.table.update().where(self.table.c.id == identifier).values(status=status, **values))

    def recover(self):
        with self.database.engine.begin() as connection:
            connection.execute(self.table.update().where(self.table.c.status == "sent").values(
                status="outcome_unknown", error_code="PROCESS_INTERRUPTED", finished_at=now()))
            connection.execute(self.table.update().where(self.table.c.status == "prepared").values(
                status="failed", error_code="NOT_SENT", finished_at=now()))

    def summary(self, project_id=None, offset=0, limit=20):
        query = select(self.table).order_by(self.table.c.created_at.desc())
        if project_id:
            query = query.where(self.table.c.project_id == project_id)
        with self.database.engine.connect() as connection:
            records = [dict(row) for row in connection.execute(query).mappings()]
        known = [item["usage"]["total_tokens"] for item in records if item["usage"] and "total_tokens" in item["usage"]]
        unknown = len(records) - len(known)
        return {"request_count": len(records), "known_total_tokens": sum(known),
                "total_tokens": sum(known) if not unknown else None, "unknown_usage_count": unknown,
                "outcome_unknown_count": sum(item["status"] == "outcome_unknown" for item in records),
                "cost": None if records else "0", "currency": None,
                "records": records[offset:offset + limit], "records_offset": offset,
                "next_offset": offset + limit if offset + limit < len(records) else None}
