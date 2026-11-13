from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


def now() -> str:
    return datetime.now(UTC).isoformat()


class Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_max_length=30000,
        json_schema_serialization_defaults_required=True,
    )


Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
FileName = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-][a-zA-Z0-9_.-]{0,150}$")]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
Route = Literal["B"]
LegacyRoute = Literal["A", "B"]
Font = Literal["PingFang SC", "Songti SC", "Heiti SC"]
ProductModel = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
PromptStep = Literal[
    "scanned_page_ocr",
    "extract_facts",
    "design_plan",
    "concept",
    "extract_layout",
    "B_regenerate",
    "fit_layout_B",
    "revise_screen_prompts",
]
PromptText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32000)
]


class Rect(Model):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class Asset(Model):
    id: Identifier
    name: str = Field(max_length=180)
    role: Literal["product", "person", "manual", "reference"]
    file: FileName
    preview: FileName | None = None
    mime: str
    width: int | None = None
    height: int | None = None
    sha256: str
    page_count: int = 0


class SourcePage(Model):
    id: Identifier
    asset_id: Identifier
    page: int = Field(ge=1)
    text: str
    preview: FileName


class Fact(Model):
    id: Identifier
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=500)
    model: str = ""
    source_id: Identifier | Literal[""] = ""
    quote: str = Field(default="", max_length=2000)
    is_claim: bool
    condition: str = Field(default="", max_length=2000)
    approved: bool = False


class FactExtraction(Model):
    product_name: str
    target_model: str = ""
    facts: list[Fact] = Field(max_length=60)
    warnings: list[str]


class ArtDirection(Model):
    name: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=2000)
    palette: list[Color] = Field(min_length=2, max_length=8)
    materials: str
    lighting: str
    photography: str
    typography: str
    motifs: str
    rhythm: str
    avoid: list[str]


class TextRun(Model):
    text: str = Field(min_length=1, max_length=2000)
    color: Color
    gradient_end: Color | None = None
    gradient_angle: int = Field(default=180, ge=0, le=360)


class Layer(Rect):
    id: Identifier
    kind: Literal["text", "rect", "line"]
    content: str = Field(default="", max_length=2000)
    color: Color
    font: Font = "PingFang SC"
    font_size: int = Field(default=32, ge=12, le=180)
    weight: Literal[300, 400, 500, 600, 700, 800] = 400
    line_height: float = Field(default=1.35, ge=1, le=2)
    align: Literal["left", "center", "right"] = "left"
    opacity: float = Field(default=1, ge=0, le=1)
    radius: int = Field(default=0, ge=0, le=200)
    gradient_end: Color | None = None
    gradient_angle: int = Field(default=180, ge=0, le=360)
    fact_ids: list[Identifier] = Field(default_factory=list)
    runs: list[TextRun] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def keep_only_matching_runs(cls, data):
        if not isinstance(data, dict):
            return data
        runs = data.get("runs") or []
        content = data.get("content", "")
        joined = "".join(item.get("text", "") for item in runs if isinstance(item, dict))
        if data.get("kind", "text") != "text" or joined != content:
            return {**data, "runs": []}
        return data


class ScreenDesign(Model):
    id: Identifier
    title: str = Field(min_length=1, max_length=100)
    kind: Literal["hero", "feature", "technical", "detail", "parameters", "lifestyle"]
    height: int = Field(ge=480, le=3200)
    purpose: str
    composition: str
    background: Color
    layers: list[Layer] = Field(min_length=1, max_length=50)
    concept_prompt: str = Field(min_length=10)
    regenerate_prompt: str = Field(min_length=10)

    @model_validator(mode="after")
    def unique_layers(self) -> ScreenDesign:
        if len({x.id for x in self.layers}) != len(self.layers):
            raise ValueError("图层 ID 不得重复")
        return self


class ScenePlan(Model):
    width: int = Field(ge=640, le=1600)
    art_direction: ArtDirection
    screens: list[ScreenDesign] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_screens(self) -> ScenePlan:
        if len({x.id for x in self.screens}) != len(self.screens):
            raise ValueError("屏 ID 不得重复")
        return self


class Transcript(Model):
    text: str


class PromptRevision(Model):
    composition: str
    concept_prompt: str
    regenerate_prompt: str


class LayoutRevision(Model):
    layers: list[Layer] = Field(min_length=1, max_length=50)
    rationale: str = Field(min_length=1, max_length=2000)


class Variant(Model):
    file: FileName
    generated_at: str
    art_version: int
    call_id: str
    width: int
    height: int
    warnings: list[str] = Field(default_factory=list)


class RouteReview(Model):
    composition: int = Field(ge=1, le=5)
    cleanliness: int = Field(ge=1, le=5)
    consistency: int = Field(ge=1, le=5)
    typography: int = Field(ge=1, le=5)
    notes: str = ""


class Screen(ScreenDesign):
    concept: Variant | None = None
    # Kept only for reading old projects and preserving their historical assets.
    erase_prompt: str = ""
    a: Variant | None = None
    b: Variant | None = None
    mask: FileName | None = None
    selected_route: LegacyRoute | None = None
    approved: bool = False
    stale: bool = False
    layout_from_concept: bool = False
    reviews: dict[LegacyRoute, RouteReview] = Field(default_factory=dict)

    @model_validator(mode="after")
    def remember_extracted_layout(self) -> Screen:
        if not self.layout_from_concept and any("已按含字参考稿提取" in item for item in self.warnings):
            self.layout_from_concept = True
        return self
    warnings: list[str] = Field(default_factory=list)


class Project(Model):
    id: Identifier
    name: str = Field(min_length=1, max_length=100)
    target_model: ProductModel = ""
    locale: Literal["zh-CN"] = "zh-CN"
    version: int = 1
    width: int = Field(default=1200, ge=640, le=1600)
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    assets: list[Asset] = Field(default_factory=list, max_length=12)
    pages: list[SourcePage] = Field(default_factory=list, max_length=288)
    facts: list[Fact] = Field(default_factory=list, max_length=60)
    facts_confirmed: bool = False
    extraction_warnings: list[str] = Field(default_factory=list)
    art_direction: ArtDirection | None = None
    art_version: int = 0
    direction_confirmed: bool = False
    screens: list[Screen] = Field(default_factory=list, max_length=16)
    prompt_overrides: dict[str, PromptText] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_ids(self) -> Project:
        for collection in (self.assets, self.pages, self.facts, self.screens):
            if len({item.id for item in collection}) != len(collection):
                raise ValueError("素材、来源、事实和分屏的 ID 必须在各自列表中唯一")
        return self


def validate_azure_endpoint(endpoint: str) -> None:
    u = urlparse(endpoint)
    host = u.hostname or ""
    if (
        u.scheme != "https"
        or not any(
            host.endswith("." + suffix)
            for suffix in (
                "cognitiveservices.azure.com", "openai.azure.com", "services.ai.azure.com",
            )
        )
        or u.username or u.password or u.port not in (443, None)
        or u.query or u.fragment or u.path not in ("", "/")
    ):
        raise ValueError("请填写 Azure Foundry 资源的 HTTPS 根端点，不含路径或密钥")


def validate_deployment(deployment: str) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", deployment):
        raise ValueError("部署名称只允许字母、数字、点、横线与下划线")


class ImageNode(Model):
    endpoint: str
    deployment: str
    subscription: str = ""
    requests_per_minute: int = Field(default=2, ge=1, le=120)

    @model_validator(mode="after")
    def connection(self) -> ImageNode:
        validate_azure_endpoint(self.endpoint)
        validate_deployment(self.deployment)
        return self


class FoundrySettings(Model):
    planning_endpoint: str = ""
    planning_deployment: str = ""
    planning_model: Literal["gpt-6.1-sol"] = "gpt-6.1-sol"
    image_endpoint: str = ""
    image_deployment: str = ""
    image_model: Literal["gpt-image-2.5-sunburst", "gpt-image-2.5-flare"] = "gpt-image-2.5-sunburst"
    subscription: str = ""
    tenant: str = ""
    quality: Literal["low", "medium", "high"] = "medium"
    image_requests_per_minute: int = Field(default=2, ge=1, le=120)
    image_nodes: list[ImageNode] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def endpoints(self) -> FoundrySettings:
        for endpoint in [self.planning_endpoint, self.image_endpoint]:
            if not endpoint:
                continue
            validate_azure_endpoint(endpoint)
        for deployment in [self.planning_deployment, self.image_deployment]:
            if deployment:
                validate_deployment(deployment)
        endpoints = [self.image_endpoint, *(node.endpoint for node in self.image_nodes)]
        nonempty = [endpoint.rstrip("/").lower() for endpoint in endpoints if endpoint]
        if len(nonempty) != len(set(nonempty)):
            raise ValueError("生图节点必须使用不同的资源端点，重复节点不会增加额度")
        return self


class Job(Model):
    id: Identifier
    project_id: Identifier
    kind: str
    status: Literal["queued", "running", "succeeded", "failed"]
    progress: int = 0
    message: str = ""
    error: str | None = None
    result: str | None = None
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class ModelCall(Model):
    id: Identifier
    project_id: Identifier
    screen_id: str | None = None
    purpose: str
    model: str
    deployment: str
    endpoint: str
    prompt: str = Field(max_length=2_032_000)
    request: dict
    usage: dict = Field(default_factory=dict)
    status: Literal["running", "succeeded", "failed"]
    error: str | None = None
    created_at: str = Field(default_factory=now)
    completed_at: str | None = None
    response: dict = Field(default_factory=dict)


class ProjectSummary(Model):
    id: Identifier
    name: str
    version: int
    updated_at: str
    screen_count: int


class CreateProject(Model):
    name: str = Field(min_length=1, max_length=100)
    target_model: ProductModel = ""


class TargetModelEdit(Model):
    version: int
    target_model: ProductModel


class FactReview(Model):
    version: int
    facts: list[Fact]


class VersionRequest(Model):
    version: int


class PromptEdit(Model):
    version: int
    step: PromptStep
    screen_id: Identifier | None = None
    instructions: PromptText | None


class PromptPreview(Model):
    step: PromptStep
    screen_id: Identifier | None = None
    title: str
    model: str
    instructions: PromptText
    default_instructions: PromptText
    context: str = Field(max_length=2_000_000)
    effective_prompt: str = Field(max_length=2_032_000)
    system_prompt: str = Field(max_length=100000)
    customized: bool
    image_files: list[FileName]
    notes: list[str]


class PromptCatalog(Model):
    project_id: Identifier
    version: int
    entries: list[PromptPreview]


class GenerationRequest(Model):
    screen_ids: list[Identifier] = Field(min_length=1, max_length=16)
    route: Route | None = None


class ScreenContentEdit(Model):
    layers: list[Layer] = Field(min_length=1, max_length=50)
    height: int = Field(ge=480, le=3200)
    selected_route: Route | None
    approved: bool
    reviews: dict[LegacyRoute, RouteReview] = Field(default_factory=dict)


class ScreenEdit(ScreenContentEdit):
    version: int


class ScreensEdit(Model):
    version: int
    screens: dict[Identifier, ScreenContentEdit] = Field(min_length=1, max_length=16)


class ArtEdit(Model):
    version: int
    art_direction: ArtDirection
    invalidate_images: bool = True


class RegenerateRequest(Model):
    instruction: str = Field(min_length=1, max_length=2000)
    route: Route = "B"


class ExportRequest(Model):
    mode: Literal["draft", "publish"] = "draft"
    format: Literal["png", "jpeg"] = "png"
    slice_height: int | None = Field(default=None, ge=500, le=10000)


class DeploymentRequest(Model):
    subscription: str
    resource_group: str
    resource: str
    model: Literal["gpt-image-2.5-sunburst", "gpt-image-2.5-flare"]
    version: str
    deployment: Identifier
    sku: str
    capacity: int = Field(ge=1, le=100)
    confirmed: Literal[True]


class ApiTypes(Model):
    project: Project
    project_summary: ProjectSummary
    job: Job
    settings: FoundrySettings
    call: ModelCall
    target_model_edit: TargetModelEdit
    create_project: CreateProject
    screen_edit: ScreenEdit
    screens_edit: ScreensEdit
    art_edit: ArtEdit
    generation_request: GenerationRequest
    export_request: ExportRequest
    deployment_request: DeploymentRequest
    prompt_edit: PromptEdit
    prompt_catalog: PromptCatalog
