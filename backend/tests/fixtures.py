import os
from pathlib import Path

from PIL import Image, ImageDraw

from backend.models import (
    ArtDirection,
    Fact,
    FactExtraction,
    Layer,
    ModelCall,
    Project,
    ScenePlan,
    Screen,
    ScreenDesign,
    SourcePage,
    Transcript,
)
from backend.storage import Store, uid


def fixture_project(store: Store) -> Project:
    project = Project(id=uid(), name="自动化测试专用项目", target_model="TEST-35")
    image = store.file(project.id, "source.png")
    Image.new("RGB", (800, 1000), "#e8efe7").save(image)
    project.pages = [
        SourcePage(
            id="p1",
            asset_id="manual",
            page=1,
            text="TEST-35 airflow 760. Saving 25% under laboratory conditions.",
            preview="source.png",
        )
    ]
    project.facts = [
        Fact(
            id="f1",
            label="风量",
            value="760",
            model="TEST-35",
            source_id="p1",
            quote="airflow 760",
            is_claim=False,
            condition="",
            approved=True,
        ),
        Fact(
            id="f2",
            label="省电",
            value="25%",
            model="TEST-35",
            source_id="p1",
            quote="Saving 25% under laboratory conditions.",
            is_claim=True,
            condition="under laboratory conditions",
            approved=False,
        ),
    ]
    project.facts_confirmed = True
    art = ArtDirection(
        name="测试用艺术指导",
        rationale="测试数据，不是模型生成",
        palette=["#e8efe7", "#26483f"],
        materials="测试材质",
        lighting="测试光线",
        photography="测试摄影",
        typography="测试字级",
        motifs="测试图形",
        rhythm="测试节奏",
        avoid=["虚构参数"],
    )
    project.art_direction = art
    project.art_version = 1
    project.direction_confirmed = True
    project.width = 800
    for i, kind in enumerate(("hero", "technical", "lifestyle")):
        screen = Screen(
            id=f"s{i}",
            title=f"测试屏 {i}",
            kind=kind,
            height=1000 + i * 100,
            purpose="自动化验证",
            composition="左上留白，右侧主物体",
            background="#e8efe7",
            layers=[
                Layer(
                    id="headline",
                    kind="text",
                    content="空气，恰到好处。",
                    x=0.1,
                    y=0.1,
                    width=0.8,
                    height=0.15,
                    color="#26483f",
                    font_size=48,
                    font="Songti SC",
                ),
                Layer(
                    id="metric",
                    kind="text",
                    content="760",
                    fact_ids=["f1"],
                    x=0.1,
                    y=0.3,
                    width=0.5,
                    height=0.1,
                    color="#26483f",
                    font_size=40,
                ),
            ],
            concept_prompt="测试专用的含字样稿提示，不是线上生成结果。",
            regenerate_prompt="测试专用的无字重生提示，保留文字安全区。",
        )
        project.screens.append(screen)
    return store.create(project)


def fixture_longform_project(store: Store) -> Project:
    project = fixture_project(store)
    screens = []
    for index in range(8):
        screen = project.screens[index % 3].model_copy(deep=True)
        screen.id = f"e{index}"
        screen.title = f"连续长图第 {index + 1} 屏"
        screen.height = 800 + index * 80
        if index == 7:
            screen.kind = "parameters"
        screens.append(screen)
    return store.mutate(project.id, lambda current: setattr(current, "screens", screens))


class FakeFoundry:
    """Only injected by tests; never selected by the application."""

    def __init__(self, store, settings):
        self.store = store

    def verify(self, purpose):
        return None

    def record(self, project_id, purpose, prompt, screen_id=None):
        call = ModelCall(
            id=uid(),
            project_id=project_id,
            screen_id=screen_id,
            purpose=purpose,
            model="unit-test-fixture",
            deployment="not-a-live-deployment",
            endpoint="https://test.invalid",
            prompt=prompt,
            request={},
            usage={"test_only": True},
            status="succeeded",
        )
        self.store.save_call(call)
        return call

    def structured(self, project_id, purpose, prompt, schema, images=None, screen_id=None):
        self.record(project_id, purpose, prompt, screen_id)
        p = self.store.get(project_id)
        if schema is Transcript:
            return Transcript(text="TEST-35 airflow 760. Scanned test-only source.")
        if schema is FactExtraction:
            return FactExtraction(
                product_name=p.name,
                target_model=p.target_model,
                facts=p.facts,
                warnings=["测试模型，不用于线上"],
            )
        if schema is ScenePlan:
            return ScenePlan(
                width=p.width,
                art_direction=p.art_direction,
                screens=[
                    ScreenDesign.model_validate(
                        s.model_dump(include=set(ScreenDesign.model_fields))
                    )
                    for s in p.screens
                ],
            )
        if purpose.startswith("extract_layout"):
            screen = next(s for s in p.screens if s.id == screen_id)
            layers = [x.model_copy(deep=True) for x in screen.layers]
            layers[0].align = "center"
            layers[0].font_size = 72
            return schema(layers=layers, rationale="自动化测试：按参考稿对齐文字，待人工核对。")
        if purpose.startswith("fit_layout_"):
            screen = next(s for s in p.screens if s.id == screen_id)
            layers = [x.model_copy(deep=True) for x in screen.layers]
            layers[0].y = 0.13
            return schema(layers=layers, rationale="自动化测试：保留文案并微调位置，待人工核对。")
        return schema(
            composition="修订后的测试构图",
            concept_prompt="修订后的测试样稿 prompt，保留工程文字。",
            regenerate_prompt="修订后的测试底图 prompt，保留安全区。",
        )

    def image(self, project_id, screen_id, purpose, prompt, size, references):
        path: Path = self.store.file(project_id, uid() + ".png")
        image = Image.new("RGB", size, "#e8efe7")
        if purpose == "concept":
            ImageDraw.Draw(image).text((size[0] * 0.12, size[1] * 0.12), "TEST", fill="#26483f")
        image.save(path)
        return path, self.record(project_id, purpose, prompt, screen_id)


def create_test_app():
    from backend.main import create_app

    frontend = os.environ.get("STUDIO_TEST_FRONTEND_DIR")
    return create_app(
        Path(os.environ["STUDIO_DATA_DIR"]),
        provider_factory=FakeFoundry,
        frontend_root=Path(frontend) if frontend else None,
    )
