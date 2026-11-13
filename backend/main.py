from __future__ import annotations

import hashlib
import io
import logging
import os
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import TypeAdapter, ValidationError
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .errors import AppError
from .exporter import export_project, package_project, project_files, validate_export
from .foundry import Foundry, create_deployment, deployment_options, discover
from .ingest import MAX_UPLOAD, ingest
from .models import (
    ApiTypes,
    ArtEdit,
    CreateProject,
    DeploymentRequest,
    ExportRequest,
    FactReview,
    FoundrySettings,
    GenerationRequest,
    ModelCall,
    Project,
    PromptCatalog,
    PromptEdit,
    RegenerateRequest,
    ScreenContentEdit,
    ScreenEdit,
    ScreensEdit,
    TargetModelEdit,
    VersionRequest,
)
from .pipeline import Jobs, Pipeline, screen_of
from .prompts import PROJECT_STEPS, TITLES, build_prompt, prompt_key
from .reporting import workflow_report
from .storage import Store, uid

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


def create_app(
    data_root: Path | None = None,
    provider_factory=Foundry,
    frontend_root: Path | None = None,
) -> FastAPI:
    store = Store(data_root or Path(os.environ.get("STUDIO_DATA_DIR", ROOT / ".data")))
    jobs = Jobs(store)
    pipeline = Pipeline(store, provider_factory)

    @asynccontextmanager
    async def lifespan(app):
        yield
        jobs.close()

    app = FastAPI(title="长图工坊", lifespan=lifespan)
    app.state.store, app.state.jobs, app.state.pipeline = store, jobs, pipeline
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    extra_hosts = [
        h.strip()
        for h in os.environ.get("STUDIO_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    ]
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            "127.0.0.1",
            "localhost",
            "[::1]",
            "testserver",
            *extra_hosts,
        ],
    )

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.url.path == "/api/health" and request.method in ("GET", "HEAD"):
            return JSONResponse(
                {"status": "ok", "planning_model": "gpt-6.1-sol"},
                headers={
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer",
                },
            )
        if int(request.headers.get("content-length", "0")) > 110 * 1024 * 1024:
            return JSONResponse({"error": "请求体超过 110 MB"}, status_code=413)
        if request.url.path.startswith("/api") and request.method not in (
            "GET",
            "HEAD",
            "OPTIONS",
        ):
            origin = request.headers.get("origin")
            if request.headers.get("sec-fetch-site") == "cross-site" or (
                origin
                and urlparse(origin).netloc
                not in (request.url.netloc, "127.0.0.1:5173", "localhost:5173")
            ):
                return JSONResponse({"error": "拒绝非本地工作台的修改请求"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(AppError)
    async def application_error(request: Request, error: AppError):
        logger.warning("%s: %s", request.url.path, error.message)
        return JSONResponse({"error": error.message}, status_code=error.status)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "planning_model": "gpt-6.1-sol"}

    @app.get("/api/schema")
    def schema():
        return ApiTypes.model_json_schema()

    @app.get("/api/settings")
    def get_settings():
        return store.settings()

    @app.put("/api/settings")
    def settings(body: FoundrySettings):
        return store.set_settings(body)

    @app.post("/api/settings/validate")
    def check_settings():
        provider = provider_factory(store, store.settings())
        provider.verify("planning")
        provider.verify("image")
        return {"message": "部署模型和就绪状态已验证；尚未进行付费生图调用。"}

    @app.get("/api/foundry/discover")
    def resources(subscription: str = ""):
        return discover(subscription)

    @app.get("/api/foundry/options")
    def model_options(subscription: str, resource: str):
        return deployment_options(subscription, resource)

    @app.post("/api/foundry/deploy")
    def deploy(body: DeploymentRequest):
        return create_deployment(body)

    @app.get("/api/projects")
    def projects(summary: bool = False):
        return store.summaries() if summary else store.list()

    @app.post("/api/projects")
    def new_project(body: CreateProject):
        return store.create(Project(id=uid(), name=body.name, target_model=body.target_model))

    @app.post("/api/projects/import")
    def import_project(file: Annotated[UploadFile, File()]):
        data = file.file.read(100 * 1024 * 1024 + 1)
        if len(data) > 100 * 1024 * 1024:
            raise AppError("项目包不能超过 100 MB", 413)
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise AppError("不是有效的项目 ZIP") from e
        with archive:
            entries = archive.infolist()
            if len(entries) > 600 or sum(i.file_size for i in entries) > 300 * 1024 * 1024:
                raise AppError("项目包展开后过大")
            names = [entry.filename for entry in entries]
            if len(set(names)) != len(names):
                raise AppError("项目包包含重复的文件路径")
            for entry in entries:
                path = Path(entry.filename)
                if (
                    not path.parts
                    or "\\" in entry.filename
                    or entry.orig_filename != entry.filename
                    or path.is_absolute()
                    or ".." in path.parts
                    or path.as_posix() != entry.filename.rstrip("/")
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000
                    or entry.flag_bits & 1
                ):
                    raise AppError("项目包包含不安全的路径、加密文件或符号链接")
                if path.parts[0] == "files":
                    if len(path.parts) != 2 or entry.is_dir():
                        raise AppError("素材必须是 files 中的普通文件")
                    if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".pdf"):
                        raise AppError("项目包素材仅支持 PNG、JPEG、WebP 和 PDF")
            if "project.json" not in archive.namelist():
                raise AppError("项目包缺少 project.json")

            def read_entry(name):
                try:
                    return archive.read(name)
                except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as e:
                    raise AppError("项目包文件无法解压：" + str(e)[:300]) from e

            try:
                p = Project.model_validate_json(read_entry("project.json"))
                calls = (
                    TypeAdapter(list[ModelCall]).validate_json(read_entry("calls.json"))
                    if "calls.json" in names
                    else []
                )
            except ValidationError as e:
                raise AppError("项目结构不符合当前版本：" + str(e)[:800], 422) from e
            if any(call.project_id != p.id for call in calls):
                raise AppError("项目包中的模型调用记录属于其他项目")
            if len({call.id for call in calls}) != len(calls):
                raise AppError("项目包中的模型调用 ID 重复")
            p.id, p.version = uid(), 1
            call_ids = {call.id: uid() for call in calls}
            for call in calls:
                call.request = {**call.request, "imported_call_id": call.id}
                call.id, call.project_id = call_ids[call.id], p.id
                if call.status == "running":
                    call.status = "failed"
                    call.error = "导入包中的调用尚未完成；结果未知，未自动重试。"
            for screen in p.screens:
                for variant in (screen.concept, screen.a, screen.b):
                    if variant and variant.call_id in call_ids:
                        variant.call_id = call_ids[variant.call_id]
            for entry in entries:
                path = Path(entry.filename)
                if path.parts[0] == "files":
                    store.file(p.id, path.name)
            files = {Path(i.filename).name for i in entries if i.filename.startswith("files/")}
            required = project_files(p)
            if not required.issubset(files):
                raise AppError("项目包缺少引用的素材或底图")
            for filename in required:
                store.file(p.id, filename)
            for entry in entries:
                path = Path(entry.filename)
                if path.parts[0] == "files":
                    store.file(p.id, path.name).write_bytes(read_entry(entry.filename))
        return store.create(p, calls)

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str, version: int | None = None):
        return store.get(project_id, version)

    @app.get("/api/projects/{project_id}/report")
    def report(project_id: str):
        return Response(
            workflow_report(store, project_id),
            media_type="text/html",
            headers={"Content-Disposition": 'attachment; filename="workflow-audit.html"'},
        )

    @app.get("/api/projects/{project_id}/prompts")
    def prompt_catalog(
        project_id: str,
        screen_id: str | None = None,
        instruction: Annotated[str, Query(max_length=2000)] = "",
    ):
        project = store.get(project_id)
        screen = screen_of(project, screen_id) if screen_id else None
        image_model = store.settings().image_model
        return PromptCatalog(
            project_id=project.id,
            version=project.version,
            entries=[
                build_prompt(
                    project,
                    step,
                    None if step in PROJECT_STEPS else screen,
                    instruction,
                    image_model,
                )
                for step in TITLES
                if step in PROJECT_STEPS or screen
            ],
        )

    @app.put("/api/projects/{project_id}/prompts")
    def edit_prompt(project_id: str, body: PromptEdit):
        key = prompt_key(body.step, body.screen_id)

        def update(current: Project):
            screen = screen_of(current, body.screen_id) if body.screen_id else None
            previous = build_prompt(current, body.step, screen).instructions
            if body.instructions is None:
                current.prompt_overrides.pop(key, None)
            else:
                current.prompt_overrides[key] = body.instructions
            if screen and body.step == "concept":
                if previous != build_prompt(current, body.step, screen).instructions:
                    screen.stale = True

        return store.mutate(project_id, update, body.version, require_idle=True)

    @app.patch("/api/projects/{project_id}/target-model")
    def edit_target_model(project_id: str, body: TargetModelEdit):
        def update(current: Project):
            if current.target_model == body.target_model:
                return
            current.target_model = body.target_model

        return store.mutate(project_id, update, body.version, require_idle=True)

    @app.get("/api/projects/{project_id}/files/{filename}")
    def file(
        project_id: str, filename: str, request: Request,
        preview: Annotated[int | None, Query(ge=80, le=1600)] = None,
    ):
        store.get(project_id)
        path = store.file(project_id, filename)
        if not path.is_file() or path.is_symlink():
            raise AppError("文件不存在", 404)
        stat = path.stat()
        etag = '"' + hashlib.sha256(
            f"{filename}:{stat.st_mtime_ns}:{stat.st_size}:{preview}".encode()
        ).hexdigest() + '"'
        headers = {"Cache-Control": "private, max-age=86400", "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        if preview is None:
            return FileResponse(path, headers=headers)
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            raise AppError("仅图片支持预览", 422)
        cache = store.root / "previews"
        cache.mkdir(exist_ok=True)
        target = cache / (etag.strip('"') + ".webp")
        if not target.is_file():
            with Image.open(path) as image:
                image.thumbnail((preview, 4800))
                output = io.BytesIO()
                image.convert("RGB").save(output, "WEBP", quality=82)
            # Concurrent readers must never see a partially written preview.
            temporary = cache / (uid() + ".tmp")
            try:
                temporary.write_bytes(output.getvalue())
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        return FileResponse(target, media_type="image/webp", headers=headers)

    @app.post("/api/projects/{project_id}/assets")
    def upload(
        project_id: str,
        file: Annotated[UploadFile, File()],
        role: Annotated[str, Form()],
    ):
        store.ensure_idle(project_id)
        p = store.get(project_id)
        if len(p.assets) >= 12:
            raise AppError("单项目最多 12 份素材")
        asset, pages = ingest(
            store,
            project_id,
            file.filename or "素材",
            role,
            file.file.read(MAX_UPLOAD + 1),
        )

        def update(current: Project):
            current.assets.append(asset)
            current.pages.extend(pages)
            current.facts_confirmed = current.direction_confirmed = False
            for s in current.screens:
                s.stale, s.approved = True, False

        return store.mutate(project_id, update, p.version, require_idle=True)

    @app.post("/api/projects/{project_id}/extract")
    def extract(project_id: str):
        return jobs.submit(project_id, "extract", pipeline.extract)

    @app.put("/api/projects/{project_id}/facts")
    def review_facts(project_id: str, body: FactReview):
        def update(current: Project):
            changed = [f.model_dump(exclude={"approved", "condition"}) for f in current.facts] != [
                f.model_dump(exclude={"approved", "condition"}) for f in body.facts
            ]
            current.facts = body.facts
            current.facts_confirmed = True
            if changed and current.screens:
                current.direction_confirmed = False
                for s in current.screens:
                    s.stale, s.approved = True, False

        return store.mutate(project_id, update, body.version, require_idle=True)

    @app.post("/api/projects/{project_id}/plan")
    def plan(project_id: str):
        return jobs.submit(project_id, "plan", pipeline.plan)

    @app.post("/api/projects/{project_id}/read-and-plan")
    def read_and_plan(project_id: str):
        return jobs.submit(project_id, "read_and_plan", pipeline.read_and_plan)

    @app.put("/api/projects/{project_id}/direction")
    def edit_direction(project_id: str, body: ArtEdit):
        def update(current: Project):
            if not current.art_direction:
                raise AppError("请先生成设计大纲")
            current.art_direction = body.art_direction
            current.direction_confirmed = True
            if body.invalidate_images:
                current.art_version += 1
                for s in current.screens:
                    s.stale = s.kind != "parameters"
                    s.approved = False

        return store.mutate(project_id, update, body.version, require_idle=True)

    @app.post("/api/projects/{project_id}/direction/confirm")
    def confirm_direction(project_id: str, body: VersionRequest):
        def update(current: Project):
            current.direction_confirmed = True

        return store.mutate(project_id, update, body.version, require_idle=True)

    def apply_screen_edit(current: Project, screen_id: str, body: ScreenContentEdit):
        screen = screen_of(current, screen_id)
        resized = screen.height != body.height
        screen.layers, screen.height = body.layers, body.height
        screen.selected_route, screen.reviews = body.selected_route, body.reviews
        if len({layer.id for layer in screen.layers}) != len(screen.layers):
            raise AppError("图层 ID 不得重复", 422)
        if resized and screen.kind != "parameters":
            screen.stale = True
        screen.approved = body.approved

    @app.patch("/api/projects/{project_id}/screens/{screen_id}")
    def edit_screen(project_id: str, screen_id: str, body: ScreenEdit):
        return store.mutate(
            project_id,
            lambda current: apply_screen_edit(current, screen_id, body),
            body.version,
            require_idle=True,
        )

    @app.patch("/api/projects/{project_id}/screens")
    def edit_screens(project_id: str, body: ScreensEdit):
        def update(current: Project):
            for screen_id, item in body.screens.items():
                apply_screen_edit(current, screen_id, item)

        return store.mutate(project_id, update, body.version, require_idle=True)

    @app.post("/api/projects/{project_id}/generate")
    def generate(
        project_id: str,
        body: GenerationRequest,
        concepts_only: bool = False,
        background_only: bool = False,
    ):
        p = store.get(project_id)
        for screen_id in body.screen_ids:
            screen_of(p, screen_id)
        kind = "concept" if concepts_only else "background" if background_only else "generate"
        return jobs.submit(
            project_id,
            kind,
            lambda job: pipeline.generate(
                job, body.screen_ids, body.route, concepts_only, background_only
            ),
        )

    @app.post("/api/projects/{project_id}/screens/{screen_id}/regenerate")
    def regenerate(project_id: str, screen_id: str, body: RegenerateRequest):
        screen_of(store.get(project_id), screen_id)
        return jobs.submit(
            project_id,
            "regenerate",
            lambda job: pipeline.regenerate(job, screen_id, body.instruction, body.route),
        )

    @app.post("/api/projects/{project_id}/extract-layout")
    def extract_layout(project_id: str, body: GenerationRequest):
        for screen_id in body.screen_ids:
            screen_of(store.get(project_id), screen_id)
        return jobs.submit(
            project_id,
            "extract_layout",
            lambda job: pipeline.extract_layouts(job, body.screen_ids),
        )

    @app.post("/api/projects/{project_id}/fit-layout")
    def fit_layout(project_id: str, body: GenerationRequest):
        for screen_id in body.screen_ids:
            screen_of(store.get(project_id), screen_id)
        return jobs.submit(
            project_id,
            "fit_layout",
            lambda job: pipeline.fit_layout(job, body.screen_ids, body.route),
        )

    @app.get("/api/projects/{project_id}/jobs")
    def job_list(project_id: str):
        store.get(project_id)
        return store.jobs(project_id)

    @app.get("/api/projects/{project_id}/calls")
    def call_list(project_id: str):
        store.get(project_id)
        return store.calls(project_id)

    @app.get("/api/projects/{project_id}/revisions")
    def revisions(project_id: str):
        store.get(project_id)
        return store.revisions(project_id)

    @app.post("/api/projects/{project_id}/revisions/{version}/restore")
    def restore(project_id: str, version: int, body: VersionRequest):
        previous = store.get(project_id, version)

        def update(current: Project):
            for key in Project.model_fields:
                if key not in ("id", "version", "created_at", "updated_at"):
                    setattr(current, key, getattr(previous, key))

        return store.mutate(project_id, update, body.version, require_idle=True)

    @app.get("/api/projects/{project_id}/package")
    def package(project_id: str):
        p = store.get(project_id)
        path = store.file(project_id, uid() + "_project.zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            package_project(store, p, archive)
        return FileResponse(path, filename="可编辑项目.zip")

    @app.post("/api/projects/{project_id}/export")
    def export(project_id: str, body: ExportRequest):
        validate_export(store.get(project_id), body.mode)
        return jobs.submit(project_id, "export", lambda job: export_project(store, job, body))

    dist = frontend_root or ROOT / "frontend/dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="web-assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("api/") or not (dist / "index.html").is_file():
            raise AppError("页面不存在；请先构建前端。", 404)
        return FileResponse(dist / "index.html")

    return app
