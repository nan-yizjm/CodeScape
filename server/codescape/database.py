from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, create_engine, event, select

from .migration import learning_mapping, module_mapping
from .models import Analysis, Architecture


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
        self.jobs = Table(
            "analysis_jobs", metadata,
            Column("id", String, primary_key=True),
            Column("project_id", String, nullable=False),
            Column("kind", String, nullable=False),
            Column("status", String, nullable=False),
            Column("position", Integer, nullable=False),
            Column("version_id", String, nullable=False),
            Column("created_at", String, nullable=False),
            Column("updated_at", String, nullable=False),
            Column("detail", String, nullable=False),
        )
        self.versions = Table(
            "project_versions", metadata,
            Column("id", String, primary_key=True),
            Column("project_id", String, nullable=False),
            Column("base_version_id", String),
            Column("status", String, nullable=False),
            Column("created_at", String, nullable=False),
            Column("value", JSON, nullable=False),
        )
        metadata.create_all(self.engine)

    def candidate(self, project_id: str) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.versions).where(
                self.versions.c.project_id == project_id,
                self.versions.c.status.in_(["draft", "paused", "failed"])
            ).order_by(self.versions.c.created_at.desc())).mappings().first()
            return dict(row) if row else None

    def reclaimable_versions(self) -> list[dict]:
        cutoff = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(self.versions).where(
                self.versions.c.status.in_(["retired", "cancelled"]),
                self.versions.c.created_at < cutoff
            )).mappings()]

    def mark_purged(self, version_id: str, value: dict) -> None:
        with self.engine.begin() as connection:
            connection.execute(self.versions.update().where(self.versions.c.id == version_id)
                               .values(status="purged", value=value))

    def version(self, project_id: str, version_id: str) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.versions).where(
                self.versions.c.project_id == project_id, self.versions.c.id == version_id
            )).mappings().first()
            return dict(row) if row else None

    def insert_candidate(self, project_id: str, version_id: str, base: str, value: dict) -> None:
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            current = connection.execute(select(self.projects.c.version_id).where(self.projects.c.id == project_id)).scalar()
            if current != base or connection.execute(select(self.versions.c.id).where(
                self.versions.c.project_id == project_id,
                self.versions.c.status.in_(["draft", "paused", "failed"])
            )).first():
                raise ValueError("项目版本或候选已经改变，请刷新后重试。")
            connection.execute(self.versions.insert().values(id=version_id, project_id=project_id,
                               base_version_id=base, status="draft", created_at=now(), value=value))

    def update_candidate(self, version_id: str, value: dict, status: str = "draft") -> None:
        with self.engine.begin() as connection:
            connection.execute(self.versions.update().where(self.versions.c.id == version_id)
                               .values(value=value, status=status))

    def publish_candidate(self, project_id: str, version_id: str, job_id: str, architecture: dict) -> dict:
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            old = dict(connection.execute(select(self.projects).where(self.projects.c.id == project_id)).mappings().one())
            candidate = dict(connection.execute(select(self.versions).where(
                self.versions.c.id == version_id, self.versions.c.project_id == project_id
            )).mappings().one())
            job = connection.execute(select(self.jobs).where(self.jobs.c.id == job_id)).mappings().one()
            if (old["version_id"] != candidate["base_version_id"] or job["status"] != "running"
                    or candidate["status"] != "draft"):
                raise ValueError("版本基线或任务状态已经改变，候选未发布。")
            value = candidate["value"]
            mapping, report = learning_mapping(Analysis.model_validate(old["result"] or {}),
                                               Analysis.model_validate(value["result"]))
            modules = module_mapping(Architecture.model_validate(old["architecture"]) if old["architecture"] else None,
                                     Architecture.model_validate(architecture), mapping)
            # Read the latest learning state inside the publication transaction.
            states = list(connection.execute(select(self.learning).where(self.learning.c.project_id == project_id)).mappings())
            old["learning"] = {row["entity_id"]: {"state": row["state"], "revision": row["revision"]} for row in states}
            if not connection.execute(select(self.versions.c.id).where(self.versions.c.id == old["version_id"])).first():
                connection.execute(self.versions.insert().values(id=old["version_id"], project_id=project_id,
                    status="retired", created_at=now(), value=old))
            else:
                connection.execute(self.versions.update().where(self.versions.c.id == old["version_id"])
                                   .values(status="retired", created_at=now(), value=old))
            connection.execute(self.learning.delete().where(self.learning.c.project_id == project_id))
            for row in states:
                if row["entity_id"] in mapping:
                    connection.execute(self.learning.insert().values(project_id=project_id, version_id=version_id,
                        entity_id=mapping[row["entity_id"]], state=row["state"], revision=1))
            report.update(from_version_id=old["version_id"], to_version_id=version_id)
            value.update(architecture=architecture, migration_report=report)
            connection.execute(self.versions.update().where(self.versions.c.id == version_id)
                               .values(status="published", value=value))
            connection.execute(self.projects.update().where(self.projects.c.id == project_id).values(
                version_id=version_id, snapshot=value["snapshot"], result=value["result"],
                architecture=architecture, status="ready", error=None))
            session = connection.execute(select(self.sessions.c.value).where(self.sessions.c.project_id == project_id)).scalar()
            if session:
                session.update(version_id=version_id, focus_id=modules.get(session.get("focus_id")),
                               viewport=None, panel=None)
                connection.execute(self.sessions.update().where(self.sessions.c.project_id == project_id).values(value=session))
            connection.execute(self.jobs.update().where(self.jobs.c.id == job_id)
                               .values(status="completed", detail="新版本已发布，学习状态已迁移", updated_at=now()))
            return report

    def recover_interrupted(self) -> None:
        # A crashed preview never silently resumes paid analysis.
        with self.engine.begin() as connection:
            connection.execute(self.jobs.update().where(self.jobs.c.status == "running")
                               .values(status="paused", updated_at=now(), detail="上次任务中断，请主动恢复。"))
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

    def list_jobs(self) -> list[dict]:
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(
                select(self.jobs).order_by(self.jobs.c.position, self.jobs.c.created_at)
            ).mappings()]

    def get_job(self, job_id: str) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.jobs).where(self.jobs.c.id == job_id)).mappings().first()
            return dict(row) if row else None

    def insert_job(self, values: dict) -> None:
        with self.engine.begin() as connection:
            connection.execute(self.jobs.insert().values(**values))

    def update_job(self, job_id: str, **values) -> None:
        with self.engine.begin() as connection:
            connection.execute(self.jobs.update().where(self.jobs.c.id == job_id)
                               .values(**values, updated_at=now()))

    def reorder_jobs(self, ids: list[str]) -> None:
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            waiting = set(connection.execute(select(self.jobs.c.id)
                          .where(self.jobs.c.status == "waiting")).scalars())
            if len(ids) != len(set(ids)) or set(ids) != waiting:
                raise ValueError("等待队列已经改变，请刷新后重新排序。")
            for position, job_id in enumerate(ids):
                connection.execute(self.jobs.update().where(self.jobs.c.id == job_id)
                                   .values(position=position, updated_at=now()))

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
            current = connection.execute(select(self.projects.c.version_id).where(self.projects.c.id == project_id)).scalar()
            if current is not None and current != version:
                raise ValueError("学习状态版本已经改变，请刷新后重试。")
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
