from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, create_engine, event, select

from .idempotency import redact_project_receipts
from .learning import learning_units, module_learning_mapping
from .migration import learning_mapping, module_mapping
from .models import Analysis, Architecture
from .storage_schema import migrate_database


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
            Column("lifecycle", String, nullable=False, default="active", server_default="active"),
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
        self.operations = Table("operations", metadata,
            Column("id", String, primary_key=True), Column("project_id", String, nullable=False),
            Column("kind", String, nullable=False), Column("status", String, nullable=False),
            Column("phase", String, nullable=False), Column("detail", String, nullable=False),
            Column("created_at", String, nullable=False), Column("updated_at", String, nullable=False))
        self.app_metadata = Table("application_metadata", metadata, Column("key", String, primary_key=True),
                                  Column("value", JSON, nullable=False))
        self.requests = Table("ai_requests", metadata,
            Column("id", String, primary_key=True), Column("project_id", String), Column("version_id", String),
            Column("job_id", String), Column("unit_id", String, nullable=False), Column("attempt", Integer, nullable=False),
            Column("phase", String, nullable=False), Column("recipient", String, nullable=False),
            Column("model", String, nullable=False), Column("status", String, nullable=False),
            Column("created_at", String, nullable=False), Column("sent_at", String), Column("finished_at", String),
            Column("payload_hash", String, nullable=False), Column("source_chars", Integer, nullable=False),
            Column("usage", JSON), Column("cost", String), Column("currency", String),
            Column("price_snapshot", JSON), Column("http_status", Integer), Column("error_code", String))
        try:
            migrate_database(root, metadata)
        except Exception:
            self.engine.dispose()
            raise

    def candidate(self, project_id: str) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.versions).where(
                self.versions.c.project_id == project_id,
                self.versions.c.base_version_id.is_not(None),
                self.versions.c.status.in_(["draft", "paused", "failed"])
            ).order_by(self.versions.c.created_at.desc())).mappings().first()
            return dict(row) if row else None

    def metadata_value(self, key: str, value=None):
        with self.engine.begin() as connection:
            previous = connection.execute(select(self.app_metadata.c.value).where(self.app_metadata.c.key == key)).first()
            if value is None:
                return previous[0] if previous else None
            if previous:
                connection.execute(self.app_metadata.update().where(self.app_metadata.c.key == key).values(value=value))
            else:
                connection.execute(self.app_metadata.insert().values(key=key, value=value))
            return value

    def operation(self, operation_id: str) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.operations).where(self.operations.c.id == operation_id)).mappings().first()
            return dict(row) if row else None

    def delete_operation(self, project_id: str) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.operations).where(self.operations.c.project_id == project_id,
                self.operations.c.kind == "project_delete").order_by(self.operations.c.created_at.desc())).mappings().first()
            return dict(row) if row else None

    def begin_delete(self, project_id: str) -> dict:
        import uuid
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            project = connection.execute(select(self.projects).where(self.projects.c.id == project_id)).mappings().one_or_none()
            previous = connection.execute(select(self.operations).where(self.operations.c.project_id == project_id,
                self.operations.c.kind == "project_delete")).mappings().first()
            if previous:
                return dict(previous)
            if not project:
                raise ValueError("项目不存在。")
            value = {"id": str(uuid.uuid4()), "project_id": project_id, "kind": "project_delete", "status": "pending",
                     "phase": "cancel", "detail": "准备停止本项目任务", "created_at": now(), "updated_at": now()}
            connection.execute(self.projects.update().where(self.projects.c.id == project_id).values(lifecycle="deleting"))
            connection.execute(self.operations.insert().values(**value))
            return value

    def update_operation(self, operation_id: str, **value):
        with self.engine.begin() as connection:
            connection.execute(self.operations.update().where(self.operations.c.id == operation_id).values(**value, updated_at=now()))

    def pending_deletes(self) -> list[dict]:
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(self.operations).where(
                self.operations.c.kind == "project_delete", self.operations.c.status != "completed")).mappings()]

    def finish_delete(self, operation_id: str, project_id: str):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            for table in [self.learning, self.sessions, self.analysis_tasks, self.jobs, self.versions, self.requests]:
                connection.execute(table.delete().where(table.c.project_id == project_id))
            connection.execute(self.projects.delete().where(self.projects.c.id == project_id))
            redact_project_receipts(connection, self.app_metadata, project_id)
            connection.execute(self.operations.update().where(self.operations.c.id == operation_id).values(
                status="completed", phase="complete", detail="软件内项目数据已永久删除", updated_at=now()))

    def reclaimable_versions(self) -> list[dict]:
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(self.versions).where(
                self.versions.c.status.in_(["retired", "cancelled"]),
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
                self.versions.c.base_version_id.is_not(None),
                self.versions.c.status.in_(["draft", "paused", "failed"])
            )).first():
                raise ValueError("项目版本或候选已经改变，请刷新后重试。")
            connection.execute(self.versions.insert().values(id=version_id, project_id=project_id,
                               base_version_id=base, status="draft", created_at=now(), value=value))

    def update_candidate(self, version_id: str, value: dict, status: str = "draft") -> None:
        with self.engine.begin() as connection:
            connection.execute(self.versions.update().where(self.versions.c.id == version_id)
                               .values(value=value, status=status))

    def _publication_inputs(self, connection, project_id, version_id, job_id, *, candidate):
        project = connection.execute(select(self.projects).where(self.projects.c.id == project_id)).mappings().first()
        job = connection.execute(select(self.jobs).where(self.jobs.c.id == job_id)).mappings().first()
        version = connection.execute(select(self.versions).where(
            self.versions.c.id == version_id, self.versions.c.project_id == project_id)).mappings().first()
        if (not project or project["lifecycle"] != "active" or not job or job["project_id"] != project_id
                or job["status"] != "running" or job["version_id"] != project["version_id"]
                or job["kind"] != ("reanalysis" if candidate else "architecture")):
            raise ValueError("项目生命周期、版本基线或任务状态已改变，分析产物未发布。")
        if candidate:
            if not version or version["status"] != "draft" or version["base_version_id"] != project["version_id"]:
                raise ValueError("候选基线已改变，分析产物未发布。")
        elif (project["version_id"] != version_id or (version and version["status"] != "draft")
              or (project["architecture"] and not any("人工预置" in text for text in
                    project["architecture"].get("overview", {}).get("limitations", [])))):
            raise ValueError("当前版本已有成功结果或基线已改变，分析产物未发布。")
        return dict(project), dict(version) if version else None

    def prepare_publication(self, project_id: str, version_id: str, job_id: str, descriptor: dict, *, candidate: bool):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            project, version = self._publication_inputs(connection, project_id, version_id, job_id, candidate=candidate)
            value = dict(version["value"] if version else project)
            if descriptor["snapshot_hash"] != value["snapshot"]["hash"]:
                raise ValueError("源码快照已改变，分析产物未发布。")
            value["prepared_output"] = descriptor
            if version:
                connection.execute(self.versions.update().where(self.versions.c.id == version_id).values(value=value))
            else:
                connection.execute(self.versions.insert().values(id=version_id, project_id=project_id,
                    status="draft", created_at=now(), value=value))

    def _publication_event(self, connection, project_id, version_id, job_id):
        connection.execute(self.operations.insert().values(id="publish-" + job_id, project_id=project_id,
            kind="version_publish", status="completed", phase="published", detail=version_id,
            created_at=now(), updated_at=now()))
        connection.execute(self.jobs.update().where(self.jobs.c.id == job_id)
            .values(status="completed", detail="版本已原子发布", updated_at=now()))
        value = {"phase": "completed", "completed": 1, "total": 1, "detail": "分批分析完成"}
        if connection.execute(select(self.analysis_tasks.c.project_id).where(
                self.analysis_tasks.c.project_id == project_id)).first():
            connection.execute(self.analysis_tasks.update().where(self.analysis_tasks.c.project_id == project_id).values(value=value))
        else:
            connection.execute(self.analysis_tasks.insert().values(project_id=project_id, value=value))

    def publish_initial(self, project_id: str, version_id: str, job_id: str, architecture: dict) -> None:
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            project, version = self._publication_inputs(connection, project_id, version_id, job_id, candidate=False)
            if not version or not version["value"].get("prepared_output"):
                raise ValueError("分析产物尚未安全保存，未发布。")
            value = {**version["value"], "architecture": architecture, "status": "ready", "error": None}
            connection.execute(self.versions.update().where(self.versions.c.id == version_id).values(status="published", value=value))
            connection.execute(self.projects.update().where(self.projects.c.id == project_id).values(
                architecture=architecture, status="ready", error=None))
            self._publication_event(connection, project_id, version_id, job_id)

    def publish_candidate(self, project_id: str, version_id: str, job_id: str, architecture: dict) -> dict:
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            old, candidate = self._publication_inputs(connection, project_id, version_id, job_id, candidate=True)
            if not candidate["value"].get("prepared_output"):
                raise ValueError("候选产物尚未安全保存，未发布。")
            value = candidate["value"]
            mapping, report = learning_mapping(Analysis.model_validate(old["result"] or {}),
                                               Analysis.model_validate(value["result"]))
            old_architecture = Architecture.model_validate(old["architecture"]) if old["architecture"] else None
            new_architecture = Architecture.model_validate(architecture)
            modules = module_mapping(old_architecture, new_architecture, mapping)
            module_map = module_learning_mapping(old_architecture, new_architecture,
                Analysis.model_validate(old["result"] or {}), Analysis.model_validate(value["result"]),
                old["snapshot"], value["snapshot"])
            previous_modules = [unit for unit in learning_units(Analysis.model_validate(old["result"] or {}),
                                old_architecture, old["snapshot"]["files"]) if unit["kind"] == "module"]
            new_modules = [unit for unit in learning_units(Analysis.model_validate(value["result"]),
                           new_architecture, value["snapshot"]["files"]) if unit["kind"] == "module"]
            mapping.update(module_map)
            report["module_learning"] = {"kept_count": len(module_map),
                "reset_or_added_count": len(new_modules) - len(module_map),
                "removed_or_changed_count": len(previous_modules) - len(module_map)}
            old_leaf_by_id = {module.id: module for module in old_architecture.modules} if old_architecture else {}
            new_leaf_by_id = {module.id: module for module in new_architecture.modules}
            matched_old = set(module_map)
            for unit in new_modules:
                module = new_leaf_by_id[unit["source_id"]]
                candidates = [before for before in previous_modules
                              if old_leaf_by_id[before["source_id"]].name == module.name and
                              set(old_leaf_by_id[before["source_id"]].code_paths) == set(module.code_paths)]
                old_id = candidates[0]["id"] if len(candidates) == 1 else None
                kept = unit["id"] in module_map.values()
                report["mapping"].append({"new_id": unit["id"], "old_id": old_id, "kept": kept,
                    "reason": "代码集合、职责归属与指纹一致" if kept else "模块内容、职责归属变化或匹配不唯一"})
                if old_id:
                    matched_old.add(old_id)
                report["kept_count"] += int(kept)
                report["reset_count"] += int(not kept and old_id is not None)
                report["added_count"] += int(old_id is None)
            report["removed_count"] += len({unit["id"] for unit in previous_modules} - matched_old)
            modules.update({old_id.removeprefix("module:"): new_id.removeprefix("module:") for old_id, new_id in module_map.items()})
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
                from .sessions import migrate_session
                session = migrate_session(session, old, value, modules, mapping, version_id)
                connection.execute(self.sessions.update().where(self.sessions.c.project_id == project_id).values(value=session))
            self._publication_event(connection, project_id, version_id, job_id)
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

    def learning_snapshot(self, project_id: str) -> dict | None:
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN")
            project = connection.execute(select(self.projects.c.version_id, self.projects.c.lifecycle)
                                         .where(self.projects.c.id == project_id)).mappings().first()
            if not project or project["lifecycle"] != "active":
                return None
            states = {row["entity_id"]: {"state": row["state"], "revision": row["revision"]}
                      for row in connection.execute(select(self.learning)
                          .where(self.learning.c.project_id == project_id)).mappings()}
            return {"version_id": project["version_id"], "states": states}

    def set_learning(self, project_id: str, entity_id: str, version: str, state: str, revision: int) -> dict:
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            project = connection.execute(select(self.projects.c.version_id, self.projects.c.lifecycle)
                                         .where(self.projects.c.id == project_id)).mappings().first()
            if not project or project["lifecycle"] != "active":
                raise ValueError("项目已经删除或正在清理。")
            if project["version_id"] != version:
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
            if value is not None:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            query = select(self.sessions).where(self.sessions.c.project_id == project_id)
            old = connection.execute(query).mappings().first()
            if value is not None:
                version = connection.execute(select(self.projects.c.version_id).where(self.projects.c.id == project_id)).scalar()
                if version != value["version_id"]:
                    raise ValueError("VERSION_CHANGED")
                actual = old["value"].get("revision", 0) if old else 0
                if value.get("expected_revision", 0) != actual:
                    raise ValueError("SESSION_CONFLICT")
                value = {key: item for key, item in value.items() if key != "expected_revision"}
                value["revision"] = actual + 1
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
