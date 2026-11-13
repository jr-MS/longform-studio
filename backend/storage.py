from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import AppError
from .models import FoundrySettings, Job, ModelCall, Project, ProjectSummary, now


def uid() -> str:
    return uuid.uuid4().hex


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "studio.sqlite"
        with self.connect() as c:
            c.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions(
                    project_id TEXT, version INTEGER, body TEXT NOT NULL,
                    PRIMARY KEY(project_id,version));
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, project_id TEXT, status TEXT, body TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_job
                    ON jobs(project_id) WHERE status IN ('queued','running');
                CREATE TABLE IF NOT EXISTS calls(
                    id TEXT PRIMARY KEY, project_id TEXT, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY, body TEXT NOT NULL);
                """
            )
            for row in c.execute(
                "SELECT body FROM jobs WHERE status IN ('queued','running')"
            ).fetchall():
                job = Job.model_validate_json(row["body"])
                job.status = "failed"
                job.error = "服务重启导致任务中断。未自动重试，请检查已有结果后手动继续。"
                job.updated_at = now()
                c.execute(
                    "UPDATE jobs SET status=?,body=? WHERE id=?",
                    (job.status, job.model_dump_json(), job.id),
                )
            for row in c.execute("SELECT id,body FROM calls").fetchall():
                call = ModelCall.model_validate_json(row["body"])
                if call.status == "running":
                    call.status = "failed"
                    call.error = "服务重启，调用结果未知；请检查用量后再决定是否重试。"
                    c.execute(
                        "UPDATE calls SET body=? WHERE id=?",
                        (call.model_dump_json(), call.id),
                    )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.db, timeout=20)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def files(self, project_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", project_id):
            raise AppError("无效项目 ID", 404)
        folder = self.root / "projects" / project_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def file(self, project_id: str, filename: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-][a-zA-Z0-9_.-]{0,150}", filename):
            raise AppError("无效文件名", 404)
        return self.files(project_id) / filename

    def create(self, project: Project, calls: list[ModelCall] | None = None) -> Project:
        with self.connect() as c:
            c.execute(
                "INSERT INTO projects VALUES (?,?)",
                (project.id, project.model_dump_json()),
            )
            for call in calls or []:
                c.execute(
                    "INSERT INTO calls VALUES (?,?,?)",
                    (call.id, call.project_id, call.model_dump_json()),
                )
        return project

    def list(self) -> list[Project]:
        with self.connect() as c:
            return [
                Project.model_validate_json(r["body"])
                for r in c.execute("SELECT body FROM projects ORDER BY rowid DESC")
            ]

    def summaries(self) -> list[ProjectSummary]:
        with self.connect() as c:
            return [
                ProjectSummary(**dict(row))
                for row in c.execute(
                    """SELECT id, json_extract(body, '$.name') AS name,
                    json_extract(body, '$.version') AS version,
                    json_extract(body, '$.updated_at') AS updated_at,
                    json_array_length(body, '$.screens') AS screen_count
                    FROM projects ORDER BY rowid DESC"""
                )
            ]

    def get(self, project_id: str, version: int | None = None) -> Project:
        with self.connect() as c:
            if version is None:
                row = c.execute("SELECT body FROM projects WHERE id=?", (project_id,)).fetchone()
            else:
                row = c.execute(
                    "SELECT body FROM revisions WHERE project_id=? AND version=?",
                    (project_id, version),
                ).fetchone()
                if not row:
                    current = c.execute(
                        "SELECT body FROM projects WHERE id=?", (project_id,)
                    ).fetchone()
                    if current and json.loads(current["body"])["version"] == version:
                        row = current
        if not row:
            raise AppError("项目或版本不存在", 404)
        return Project.model_validate_json(row["body"])

    def mutate(
        self,
        project_id: str,
        change: Callable[[Project], None],
        version: int | None = None,
        require_idle: bool = False,
    ) -> Project:
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if (
                require_idle
                and c.execute(
                    "SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running')",
                    (project_id,),
                ).fetchone()
            ):
                raise AppError("项目生成中，请等待当前任务完成后再修改。", 409)
            row = c.execute("SELECT body FROM projects WHERE id=?", (project_id,)).fetchone()
            if not row:
                raise AppError("项目不存在", 404)
            project = Project.model_validate_json(row["body"])
            if version is not None and version != project.version:
                raise AppError("项目已被其他操作更新，请重新载入后再保存。", 409)
            original = project.model_dump_json()
            change(project)
            project.version += 1
            project.updated_at = now()
            project = Project.model_validate(project.model_dump())
            c.execute(
                "INSERT OR IGNORE INTO revisions VALUES (?,?,?)",
                (project_id, project.version - 1, original),
            )
            c.execute(
                "UPDATE projects SET body=? WHERE id=?",
                (project.model_dump_json(), project_id),
            )
        return project

    def settings(self) -> FoundrySettings:
        with self.connect() as c:
            row = c.execute("SELECT body FROM settings WHERE id=1").fetchone()
        return FoundrySettings.model_validate_json(row["body"]) if row else FoundrySettings()

    def set_settings(self, settings: FoundrySettings) -> FoundrySettings:
        with self.connect() as c:
            c.execute(
                "INSERT OR REPLACE INTO settings VALUES (1,?)",
                (settings.model_dump_json(),),
            )
        return settings

    def add_job(self, job: Job) -> None:
        try:
            with self.connect() as c:
                c.execute(
                    "INSERT INTO jobs VALUES (?,?,?,?)",
                    (job.id, job.project_id, job.status, job.model_dump_json()),
                )
        except sqlite3.IntegrityError as e:
            raise AppError("这个项目已有任务进行中，请等待任务完成。", 409) from e

    def save_job(self, job: Job) -> None:
        job.updated_at = now()
        with self.connect() as c:
            c.execute(
                "UPDATE jobs SET status=?,body=? WHERE id=?",
                (job.status, job.model_dump_json(), job.id),
            )

    def jobs(self, project_id: str) -> list[Job]:
        with self.connect() as c:
            return [
                Job.model_validate_json(r["body"])
                for r in c.execute(
                    "SELECT body FROM jobs WHERE project_id=? ORDER BY rowid DESC LIMIT 50",
                    (project_id,),
                )
            ]

    def ensure_idle(self, project_id: str) -> None:
        if any(j.status in ("queued", "running") for j in self.jobs(project_id)):
            raise AppError("项目生成中，暂不能修改设计。请等待当前任务完成。", 409)

    def save_call(self, call: ModelCall) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT OR REPLACE INTO calls VALUES (?,?,?)",
                (call.id, call.project_id, call.model_dump_json()),
            )

    def calls(self, project_id: str) -> list[ModelCall]:
        with self.connect() as c:
            return [
                ModelCall.model_validate_json(r["body"])
                for r in c.execute(
                    "SELECT body FROM calls WHERE project_id=? ORDER BY rowid DESC",
                    (project_id,),
                )
            ]

    def revisions(self, project_id: str) -> list[dict]:
        with self.connect() as c:
            return [
                {
                    "version": r["version"],
                    "updated_at": json.loads(r["body"])["updated_at"],
                }
                for r in c.execute(
                    "SELECT version,body FROM revisions WHERE project_id=? ORDER BY version DESC LIMIT 30",
                    (project_id,),
                )
            ]
