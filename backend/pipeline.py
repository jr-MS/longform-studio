from __future__ import annotations

import logging
import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from .errors import AppError
from .foundry import Foundry
from .ink import apply_reference_ink
from .models import (
    FactExtraction,
    Job,
    Layer,
    LayoutRevision,
    Project,
    PromptRevision,
    PromptStep,
    ScenePlan,
    Screen,
    Transcript,
    Variant,
    now,
)
from .prompts import build_prompt, reference_files
from .storage import Store, uid

logger = logging.getLogger(__name__)


def image_size(width: int, height: int) -> tuple[int, int]:
    ratio = width / height
    if not 1 / 3 <= ratio <= 3:
        raise AppError("画面比例超出模型支持范围，请调整屏高。", 422)
    scale = math.sqrt(1_572_864 / (width * height))
    w, h = round(width * scale / 16) * 16, round(height * scale / 16) * 16
    if max(w, h) > 3840 or not 655360 <= w * h <= 8294400:
        raise AppError("无法为当前画布选择合法生成尺寸", 422)
    return w, h


def align_text_to_reference(screen: Screen, result: LayoutRevision) -> list[Layer]:
    original = {layer.id: layer for layer in screen.layers}
    replacements: dict[str, Layer] = {}
    for layer in result.layers:
        source = original.get(layer.id)
        if not source or source.kind != "text" or layer.id in replacements:
            continue
        replacements[layer.id] = layer.model_copy(
            update={"kind": "text", "content": source.content, "fact_ids": source.fact_ids}
        )
    return [replacements.get(layer.id, layer) for layer in screen.layers]


def screen_of(project: Project, screen_id: str) -> Screen:
    screen = next((s for s in project.screens if s.id == screen_id), None)
    if not screen:
        raise AppError("屏不存在", 404)
    return screen


class Jobs:
    def __init__(self, store: Store):
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="studio")

    def submit(self, project_id: str, kind: str, work: Callable[[Job], str | None]) -> Job:
        self.store.get(project_id)
        job = Job(id=uid(), project_id=project_id, kind=kind, status="queued")
        self.store.add_job(job)

        def run():
            job.status = "running"
            job.message = "任务已开始"
            self.store.save_job(job)
            try:
                job.result = work(job)
                job.status = "succeeded"
                job.progress = 100
                job.message = "处理完成，请检查结果"
            except Exception as e:
                logger.exception("Job %s failed", job.id)
                job.status = "failed"
                job.error = str(e)[:2500]
                job.message = "任务失败；已完成的单屏结果与调用记录已保留"
            self.store.save_job(job)

        self.executor.submit(run)
        return job

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=False)


class Pipeline:
    def __init__(self, store: Store, provider_factory=Foundry):
        self.store = store
        self.provider_factory = provider_factory

    def provider(self) -> Foundry:
        return self.provider_factory(self.store, self.store.settings())

    def refs(self, p: Project, person: bool = True) -> list[Path]:
        return [self.store.file(p.id, filename) for filename in reference_files(p, person)]

    def prompt(
        self, p: Project, step: PromptStep, screen: Screen | None = None, instruction=""
    ) -> str:
        return build_prompt(p, step, screen, instruction).effective_prompt

    def report(self, job: Job, progress: int, message: str):
        job.progress, job.message = progress, message
        self.store.save_job(job)

    def extract(self, job: Job) -> None:
        p = self.store.get(job.project_id)
        if not p.pages and not self.refs(p):
            raise AppError("请先上传产品图片或说明书。", 422)
        provider = self.provider()
        for page in p.pages:
            if not page.text.strip():
                transcript = provider.structured(
                    p.id,
                    "scanned_page_ocr",
                    self.prompt(p, "scanned_page_ocr"),
                    Transcript,
                    [self.store.file(p.id, page.preview)],
                )
                page.text = transcript.text
        self.store.mutate(p.id, lambda current: setattr(current, "pages", p.pages))
        self.report(job, 20, "正在使用 gpt-6.1-sol 整理事实与引用")
        result = provider.structured(
            p.id,
            "extract_facts",
            self.prompt(p, "extract_facts"),
            FactExtraction,
            self.refs(p) + [self.store.file(p.id, x.preview) for x in p.pages[:8]],
        )
        for fact in result.facts:
            fact.approved = False
        if not result.facts:
            result.warnings.append("未整理出结构化事实，MVP 仍可直接依据原始素材规划设计。")

        def update(current: Project):
            current.facts = result.facts
            current.extraction_warnings = result.warnings
            current.facts_confirmed = True
            current.direction_confirmed = False
            for screen in current.screens:
                screen.stale = True
                screen.approved = False

        self.store.mutate(p.id, update)

    def plan(self, job: Job) -> None:
        p = self.store.get(job.project_id)
        if not p.assets and not p.pages:
            raise AppError("请先上传用于设计的素材。", 422)
        self.report(job, 10, "gpt-6.1-sol 正在设计视觉气质与多屏布局")
        result = self.provider().structured(
            p.id,
            "design_plan",
            self.prompt(p, "design_plan"),
            ScenePlan,
            self.refs(p) + [self.store.file(p.id, x.preview) for x in p.pages[:8]],
        )

        def update(current: Project):
            current.width = result.width
            current.art_direction = result.art_direction
            current.art_version += 1
            current.screens = [Screen.model_validate(x.model_dump()) for x in result.screens]
            current.direction_confirmed = True

        self.store.mutate(p.id, update)

    def read_and_plan(self, job: Job) -> None:
        p = self.store.get(job.project_id)
        if not p.assets and not p.pages:
            raise AppError("请先上传用于设计的素材。", 422)
        if p.pages or self.refs(p):
            self.report(job, 5, "正在阅读资料并整理产品事实")
            self.extract(job)
        else:
            self.report(job, 5, "没有可整理的产品图或说明书，直接规划设计")
        self.plan(job)

    def variant(self, p: Project, path: Path, call, warnings: list[str] | None = None) -> Variant:
        with Image.open(path) as image:
            width, height = image.size
        return Variant(
            file=path.name,
            generated_at=now(),
            art_version=p.art_version,
            call_id=call.id,
            width=width,
            height=height,
            warnings=warnings or [],
        )

    def concept(self, p: Project, s: Screen, provider: Foundry) -> bool:
        if s.kind == "parameters":
            return False
        if s.concept and s.concept.art_version == p.art_version and not s.stale:
            return False
        path, call = provider.image(
            p.id,
            s.id,
            "concept",
            self.prompt(p, "concept", s),
            image_size(p.width, s.height),
            self.refs(p, s.kind in ("hero", "lifestyle")),
        )
        variant = self.variant(p, path, call)

        def update(current: Project):
            screen = screen_of(current, s.id)
            screen.concept = variant
            screen.b = None
            screen.approved = False
            screen.stale = False
            screen.layout_from_concept = False
            screen.warnings = [item for item in screen.warnings if "已按含字参考稿提取" not in item]

        self.store.mutate(p.id, update)
        return True

    def generate(
        self,
        job: Job,
        screen_ids: list[str],
        route: str | None = None,
        concepts_only=False,
        background_only=False,
    ):
        if route not in (None, "B"):
            raise AppError("只支持参考图重生无字底图。", 422)
        if concepts_only and background_only:
            raise AppError("不能同时只生成参考稿和只生成底图。", 422)
        p = self.store.get(job.project_id)
        provider = self.provider()
        ids = list(dict.fromkeys(screen_ids))
        for index, screen_id in enumerate(ids):
            p = self.store.get(p.id)
            screen = screen_of(p, screen_id)
            if background_only:
                if screen.kind == "parameters":
                    continue
                if not screen.concept:
                    raise AppError(f"「{screen.title}」还没有含字参考稿。", 409)
                if not screen.layout_from_concept:
                    raise AppError(f"请先从「{screen.title}」的参考稿提取文字位置，再生成无字底图。", 409)
                self.report(job, int(index / len(ids) * 100), f"正在生成无字底图：{screen.title}")
                self.generate_background(p, screen, provider)
                continue
            self.report(job, int(index / len(ids) * 100), f"正在生成含字参考稿：{screen.title}")
            self.concept(p, screen, provider)
            p = self.store.get(p.id)
            screen = screen_of(p, screen_id)
            already_extracted = screen.layout_from_concept or any(
                "已按含字参考稿提取" in item for item in screen.warnings
            )
            if screen.kind != "parameters" and screen.concept and not already_extracted:
                self.report(
                    job, int(index / len(ids) * 100), f"正在从参考稿提取文字位置：{screen.title}"
                )
                self.extract_layout(p, screen, provider)
                p = self.store.get(p.id)
                screen = screen_of(p, screen_id)
            if concepts_only or screen.kind == "parameters":
                continue
            self.report(job, int(index / len(ids) * 100), f"正在生成无字底图：{screen.title}")
            self.generate_background(p, screen, provider)

    def extract_layouts(self, job: Job, screen_ids: list[str]) -> None:
        p = self.store.get(job.project_id)
        provider = self.provider()
        ids = list(dict.fromkeys(screen_ids))
        for index, screen_id in enumerate(ids):
            p = self.store.get(p.id)
            screen = screen_of(p, screen_id)
            if screen.kind == "parameters":
                continue
            if not screen.concept:
                raise AppError(f"「{screen.title}」还没有含字参考稿，无法提取文字。", 409)
            self.report(job, int(index / max(len(ids), 1) * 100), f"正在对照参考稿：{screen.title}")
            self.extract_layout(p, screen, provider)

    def extract_layout(self, p: Project, screen: Screen, provider: Foundry) -> None:
        if not screen.concept:
            raise AppError("请先生成含字参考稿", 409)
        result = provider.structured(
            p.id,
            "extract_layout",
            self.prompt(p, "extract_layout", screen),
            LayoutRevision,
            [self.store.file(p.id, screen.concept.file)],
            screen.id,
        )
        fitted = align_text_to_reference(screen, result)
        try:
            with Image.open(self.store.file(p.id, screen.concept.file)) as concept:
                fitted = apply_reference_ink(concept, fitted)
        except OSError:
            logger.exception("Could not sample reference ink colors for %s", screen.id)
        rationale = result.rationale

        def update(current: Project):
            target = screen_of(current, screen.id)
            target.layers = fitted
            target.layout_from_concept = True
            target.approved = False
            target.warnings.append("已按含字参考稿提取文字排版（待人工核对）：" + rationale)

        self.store.mutate(p.id, update)

    def generate_background(self, p: Project, screen: Screen, provider: Foundry):
        if not screen.concept:
            raise AppError("请先生成本屏含字参考稿", 409)
        if not screen.layout_from_concept:
            raise AppError("请先从含字参考稿提取文字位置，再生成无字底图。", 409)
        concept = self.store.file(p.id, screen.concept.file)
        path, call = provider.image(
            p.id,
            screen.id,
            "B_regenerate",
            self.prompt(p, "B_regenerate", screen),
            image_size(p.width, screen.height),
            [concept] + self.refs(p, screen.kind in ("hero", "lifestyle")),
        )
        variant = self.variant(p, path, call)

        def update(current: Project):
            target = screen_of(current, screen.id)
            target.b = variant
            target.approved = False
            target.stale = False
            target.selected_route = "B"

        self.store.mutate(p.id, update)

    def fit_layout(self, job: Job, screen_ids: list[str], route: str | None = None):
        if route not in (None, "B"):
            raise AppError("只支持适配参考重生底图。", 422)
        p = self.store.get(job.project_id)
        ids = list(dict.fromkeys(screen_ids))
        for screen_id in ids:
            screen = screen_of(p, screen_id)
            variant = screen.b
            if screen.kind == "parameters" or not variant:
                raise AppError(f"「{screen.title}」没有可适配的底图。", 409)
        provider = self.provider()
        for index, screen_id in enumerate(ids):
            p = self.store.get(p.id)
            screen = screen_of(p, screen_id)
            variant = screen.b
            self.report(job, int(index / len(ids) * 100), f"正在按实际底图适配排版：{screen.title}")
            images = []
            if screen.concept:
                images.append(self.store.file(p.id, screen.concept.file))
            images.append(self.store.file(p.id, variant.file))
            result = provider.structured(
                p.id,
                "fit_layout_B",
                self.prompt(p, "fit_layout_B", screen),
                LayoutRevision,
                images,
                screen.id,
            )
            original_text = {x.id: x for x in screen.layers if x.kind == "text"}
            fitted_layers: list[Layer] = []
            seen: set[str] = set()
            decorations = 0
            ignored = 0
            for layer in result.layers:
                if layer.id in seen:
                    ignored += 1
                    continue
                original = original_text.get(layer.id)
                if original:
                    layer = layer.model_copy(
                        update={
                            "kind": "text",
                            "content": original.content,
                            "fact_ids": original.fact_ids,
                        }
                    )
                elif layer.kind == "text":
                    ignored += 1
                    continue
                elif decorations >= 50 - len(original_text):
                    ignored += 1
                    continue
                else:
                    decorations += 1
                fitted_layers.append(layer)
                seen.add(layer.id)
            omitted = [x for x in original_text.values() if x.id not in seen]
            fitted_layers.extend(omitted)
            notes = []
            if omitted:
                notes.append(
                    "部分文字未返回定位，已保留原位置：" + "、".join(x.id for x in omitted)
                )
            if ignored:
                notes.append(f"未采用 {ignored} 个重复、新增文案或超出图层数量上限的图层。")
            if not fitted_layers:
                fitted_layers = screen.layers
                notes.append("模型未返回可应用的布局调整，原图层保持不变。")
            if notes:
                logger.warning("Layout fitting %s/%s: %s", p.id, screen_id, "；".join(notes))
                result.rationale += "\n" + "；".join(notes)

            def update(current: Project):
                target = screen_of(current, screen_id)
                target.layers = fitted_layers
                target.selected_route = "B"
                target.approved = False
                target.warnings.append("AI 排版适配（待人工核对）：" + result.rationale)

            self.store.mutate(p.id, update)

    def regenerate(self, job: Job, screen_id: str, instruction: str, route: str = "B"):
        if route != "B":
            raise AppError("只支持参考图重生无字底图。", 422)
        p = self.store.get(job.project_id)
        screen = screen_of(p, screen_id)
        if screen.kind == "parameters":
            raise AppError("参数屏直接编辑，无需重生底图。", 409)
        provider = self.provider()
        result = provider.structured(
            p.id,
            "revise_screen_prompts",
            self.prompt(p, "revise_screen_prompts", screen, instruction),
            PromptRevision,
            self.refs(p),
            screen.id,
        )

        def update(current: Project):
            target = screen_of(current, screen_id)
            for key, value in result.model_dump().items():
                setattr(target, key, value)
            target.concept = target.b = None
            target.layout_from_concept = False
            target.warnings = [item for item in target.warnings if "已按含字参考稿提取" not in item]
            target.selected_route = None
            target.approved = False

        self.store.mutate(p.id, update)
        self.generate(job, [screen_id], route)
