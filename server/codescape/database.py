from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, create_engine, event, select


def now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{(root / 'app.db').as_posix()}", connect_args={"check_same_thread": False}
        )

        @event.listens_for(self.engine, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=5000")

        metadata = MetaData()
        self.projects = Table(
            "prototype_projects",
            metadata,
            Column("id", String, primary_key=True),
            Column("name", String, unique=True, nullable=False),
            Column("source", String, nullable=False),
            Column("created_at", String, nullable=False),
            Column("status", String, nullable=False),
            Column("snapshot", JSON, nullable=False),
            Column("version_id", String, nullable=False),
            Column("result", JSON),
            Column("architecture", JSON),
            Column("error", String),
            Column("demo", Integer, nullable=False, default=0),
        )
        self.learning = Table(
            "prototype_learning",
            metadata,
            Column("project_id", String, primary_key=True),
            Column("entity_id", String, primary_key=True),
            Column("version_id", String, nullable=False),
            Column("state", String, nullable=False),
            Column("revision", Integer, nullable=False, default=0),
        )
        self.sessions = Table(
            "prototype_sessions",
            metadata,
            Column("project_id", String, primary_key=True),
            Column("value", JSON, nullable=False),
        )
        self.analysis_tasks = Table(
            "prototype_analysis_tasks",
            metadata,
            Column("project_id", String, primary_key=True),
            Column("value", JSON, nullable=False),
        )
        metadata.create_all(self.engine)
        # A crashed preview never silently resumes paid analysis.
        with self.engine.begin() as connection:
            connection.execute(
                self.projects.update()
                .where(self.projects.c.status == "analyzing")
                .values(status="paused", error="上次分析中断，请主动重新分析。")
            )
            connection.execute(
                self.projects.update()
                .where(self.projects.c.status.in_(["indexing", "queued"]))
                .values(status="failed", error="上次静态解析中断，请重新导入。")
            )

    def list_projects(self) -> list[dict]:
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(self.projects).order_by(self.projects.c.created_at.desc())
                ).mappings()
            ]

    def get(self, project_id: str) -> dict | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(self.projects).where(self.projects.c.id == project_id))
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def insert(self, values: dict) -> None:
        with self.engine.begin() as connection:
            connection.execute(self.projects.insert().values(**values))

    def update(self, project_id: str, **values) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                self.projects.update().where(self.projects.c.id == project_id).values(**values)
            )

    def states(self, project_id: str) -> dict:
        with self.engine.connect() as connection:
            return {
                row["entity_id"]: {"state": row["state"], "revision": row["revision"]}
                for row in connection.execute(
                    select(self.learning).where(self.learning.c.project_id == project_id)
                ).mappings()
            }

    def set_learning(self, project_id: str, entity_id: str, version: str, state: str, revision: int) -> dict:
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            query = select(self.learning).where(
                self.learning.c.project_id == project_id, self.learning.c.entity_id == entity_id
            )
            old = connection.execute(query).mappings().first()
            actual = old["revision"] if old else 0
            if actual != revision:
                raise ValueError("学习状态已由另一个界面更新，请刷新后重试。")
            values = {
                "project_id": project_id,
                "entity_id": entity_id,
                "version_id": version,
                "state": state,
                "revision": actual + 1,
            }
            if old:
                connection.execute(
                    self.learning.update()
                    .where(self.learning.c.project_id == project_id, self.learning.c.entity_id == entity_id)
                    .values(**values)
                )
            else:
                connection.execute(self.learning.insert().values(**values))
            return {"state": state, "revision": actual + 1}

    def session(self, project_id: str, value: dict | None = None) -> dict | None:
        with self.engine.begin() as connection:
            query = select(self.sessions).where(self.sessions.c.project_id == project_id)
            old = connection.execute(query).mappings().first()
            if value is not None:
                if old:
                    connection.execute(
                        self.sessions.update()
                        .where(self.sessions.c.project_id == project_id)
                        .values(value=value)
                    )
                else:
                    connection.execute(self.sessions.insert().values(project_id=project_id, value=value))
                return value
            return json.loads(json.dumps(old["value"])) if old else None

    def analysis_progress(self, project_id: str, value: dict | None = None) -> dict | None:
        with self.engine.begin() as connection:
            query = select(self.analysis_tasks).where(self.analysis_tasks.c.project_id == project_id)
            old = connection.execute(query).mappings().first()
            if value is not None:
                if old:
                    connection.execute(
                        self.analysis_tasks.update()
                        .where(self.analysis_tasks.c.project_id == project_id)
                        .values(value=value)
                    )
                else:
                    connection.execute(self.analysis_tasks.insert().values(project_id=project_id, value=value))
                return value
            return json.loads(json.dumps(old["value"])) if old else None
