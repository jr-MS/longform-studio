import json
import os
import zipfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

from .errors import AppError
from .models import ExportRequest, Job, Project
from .storage import Store, uid


def validate_export(project: Project, mode: str) -> None:
    if not project.screens:
        raise AppError("还没有可导出的多屏设计", 409)
    for screen in project.screens:
        if screen.kind != "parameters":
            if not screen.b:
                raise AppError(f"「{screen.title}」还没有可导出的无字底图，请从参考稿生成。", 409)


def project_files(project: Project) -> set[str]:
    required = {a.file for a in project.assets} | {a.preview for a in project.assets if a.preview}
    required |= {page.preview for page in project.pages}
    required |= {v.file for s in project.screens for v in (s.concept, s.a, s.b) if v}
    required |= {s.mask for s in project.screens if s.mask}
    return required


def package_project(store: Store, project: Project, archive: zipfile.ZipFile):
    archive.writestr("project.json", project.model_dump_json(indent=2))
    archive.writestr(
        "calls.json",
        json.dumps(
            [c.model_dump() for c in store.calls(project.id)],
            ensure_ascii=False,
            indent=2,
        ),
    )
    archive.writestr(
        "font-info.json",
        json.dumps(
            {
                "families": sorted({x.font for s in project.screens for x in s.layers}),
                "notice": "字体使用本机已安装字体，项目包不包含字体文件；换机需安装相同字体并确认授权。",
            },
            ensure_ascii=False,
        ),
    )
    for filename in sorted(project_files(project)):
        path = store.file(project.id, filename)
        if not path.is_file() or path.is_symlink():
            raise AppError("项目引用的文件缺失或不是普通文件：" + filename, 409)
        archive.write(path, "files/" + path.name)


def export_project(store: Store, job: Job, request: ExportRequest) -> str:
    project = store.get(job.project_id)
    validate_export(project, request.mode)
    total_height = sum(s.height for s in project.screens)
    if project.width * total_height > 65_000_000:
        raise AppError("长图超过原型内存保护上限，请减少屏数或屏高后导出。", 422)
    origin = os.environ.get("STUDIO_ORIGIN", "http://127.0.0.1:8765").rstrip("/")
    if not origin.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise AppError("导出渲染源必须是本地工作台", 500)
    images: list[Path] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for i, screen in enumerate(project.screens):
                job.message = f"正在渲染第 {i + 1} 屏：{screen.title}"
                job.progress = int(i / len(project.screens) * 85)
                store.save_job(job)
                page = browser.new_page(
                    viewport={"width": project.width, "height": screen.height},
                    device_scale_factor=1,
                )
                try:
                    page.route(
                        "**/*",
                        lambda route: (
                            route.continue_()
                            if route.request.url.startswith(origin + "/")
                            else route.abort()
                        ),
                    )
                    page.goto(
                        f"{origin}/render/{project.id}/{screen.id}?version={project.version}&mode={request.mode}",
                        wait_until="networkidle",
                    )
                    page.wait_for_selector('[data-artboard][data-ready="true"]', timeout=30000)
                    page.evaluate("document.fonts.ready")
                    path = store.file(project.id, f"{uid()}_screen_{i + 1}.png")
                    page.locator("[data-artboard]").screenshot(
                        path=str(path), animations="disabled"
                    )
                    with Image.open(path) as image:
                        if image.size != (project.width, screen.height):
                            raise AppError("渲染尺寸不正确，已终止导出。", 500)
                    images.append(path)
                finally:
                    page.close()
        finally:
            browser.close()
    long_image = Image.new("RGB", (project.width, total_height), "white")
    y = 0
    for path in images:
        with Image.open(path) as image:
            long_image.paste(image.convert("RGB"), (0, y))
            y += image.height
    ext = "jpg" if request.format == "jpeg" else "png"
    long_path = store.file(project.id, f"{uid()}_long.{ext}")
    long_image.save(long_path, quality=95) if ext == "jpg" else long_image.save(long_path)
    return long_path.name
