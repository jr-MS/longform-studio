import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from backend.errors import AppError
from backend.exporter import validate_export
from backend.foundry import Foundry
from backend.ingest import ingest
from backend.main import create_app
from backend.models import (
    ArtEdit,
    FactExtraction,
    FoundrySettings,
    Job,
    Project,
    Rect,
    ScenePlan,
)
from backend.pipeline import image_size
from backend.storage import Store, uid
from backend.tests.fixtures import FakeFoundry, fixture_longform_project, fixture_project


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "data")


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "api", provider_factory=FakeFoundry)
    with TestClient(app) as c:
        yield c, app.state.store


def payload(project, screen):
    return {
        "version": project.version,
        "layers": [layer.model_dump() for layer in screen.layers],
        "height": screen.height,
        "selected_route": screen.selected_route,
        "approved": screen.approved,
        "reviews": screen.reviews,
    }


def wait_job(c, pid, job_id):
    for _ in range(100):
        jobs = c.get(f"/api/projects/{pid}/jobs").json()
        job = next(j for j in jobs if j["id"] == job_id)
        if job["status"] in ("failed", "succeeded"):
            return job
        time.sleep(0.03)
    pytest.fail("测试任务未完成")


def test_customer_onboarding_starts_empty_without_private_sample_assets(client):
    c, _ = client
    assert c.get("/api/projects?summary=true").json() == []
    response = c.post("/api/projects", json={"name": "客户的第一个项目"})
    assert response.status_code == 200
    p = response.json()
    assert p["name"] == "客户的第一个项目"
    assert not p["assets"] and not p["pages"] and not p["screens"] and not p["facts"]
    assert c.post("/api/projects/sample").status_code == 405


def test_project_summaries_and_compressed_cached_previews(client):
    c, store = client
    p = fixture_project(store)
    summary = c.get("/api/projects?summary=true")
    assert summary.json() == [{
        "id": p.id, "name": p.name, "version": p.version,
        "updated_at": p.updated_at, "screen_count": 3,
    }]
    response = c.get(f"/api/projects/{p.id}/files/source.png?preview=160")
    assert response.status_code == 200 and response.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (160, 200)
    assert c.get(
        f"/api/projects/{p.id}/files/source.png?preview=160",
        headers={"If-None-Match": response.headers["etag"]},
    ).status_code == 304
    assert c.get(f"/api/projects/{p.id}/files/source.png?preview=2000").status_code == 422
    assert c.get("/api/projects").headers.get("content-encoding") == "gzip"


def test_model_quote_source_and_claim_conditions_do_not_block_fact_saving(client):
    c, store = client
    p = fixture_project(store)
    facts = [f.model_dump() for f in p.facts]
    facts[0].update(model="OTHER-35", source_id="missing", quote="改写的资料摘要", value="999")
    facts[1].update(approved=True, condition="")
    response = c.put(f"/api/projects/{p.id}/facts", json={"version": p.version, "facts": facts})
    assert response.status_code == 200, response.text
    assert response.json()["facts"] == facts
    assert not store.calls(p.id)


def test_target_mismatch_does_not_block_extraction_or_filter_other_models(client):
    c, store = client
    p = fixture_project(store)
    store.mutate(p.id, lambda current: setattr(current, "target_model", "pl"))
    response = c.post(f"/api/projects/{p.id}/extract")
    assert response.status_code == 200, response.text
    job = wait_job(c, p.id, response.json()["id"])
    assert job["status"] == "succeeded", job
    current = store.get(p.id)
    assert current.target_model == "pl" and current.facts
    assert all(f.model == "TEST-35" and not f.approved for f in current.facts)
    prompt = store.calls(p.id)[0].prompt
    assert "不要核验、匹配或过滤" in prompt and "目标型号严格" not in prompt


def test_project_model_is_optional(client):
    c, _ = client
    response = c.post("/api/projects", json={"name": "不填写型号的 MVP 项目"})
    assert response.status_code == 200, response.text
    assert response.json()["target_model"] == ""


def test_image_only_extraction_needs_no_pdf_or_bound_source(client):
    c, store = client
    p = fixture_project(store)
    asset, _ = ingest(
        store, p.id, "fixture.png", "product", store.file(p.id, "source.png").read_bytes()
    )

    def image_only(current):
        current.assets = [asset]
        current.pages = []
        current.target_model = ""
        current.facts = []

    store.mutate(p.id, image_only)
    response = c.post(f"/api/projects/{p.id}/extract")
    assert wait_job(c, p.id, response.json()["id"])["status"] == "succeeded"
    current = store.get(p.id)
    assert current.assets == [asset] and current.facts == []
    assert current.facts_confirmed and current.extraction_warnings


def test_empty_facts_can_be_saved(client):
    c, store = client
    p = fixture_project(store)
    response = c.put(f"/api/projects/{p.id}/facts", json={"version": p.version, "facts": []})
    assert response.status_code == 200, response.text
    assert response.json()["facts"] == []


def test_empty_model_result_keeps_design_and_can_continue_planning(client, monkeypatch):
    c, store = client
    p = fixture_project(store)
    asset, _ = ingest(
        store, p.id, "fixture.png", "product", store.file(p.id, "source.png").read_bytes()
    )
    p = store.mutate(p.id, lambda current: current.assets.append(asset))
    structured = FakeFoundry.structured

    def empty_result(self, project_id, purpose, prompt, schema, images=None, screen_id=None):
        result = structured(self, project_id, purpose, prompt, schema, images, screen_id)
        if schema is FactExtraction:
            assert "facts=[]" in prompt and "不做业务审核" in prompt
            result.facts = []
        return result

    monkeypatch.setattr(FakeFoundry, "structured", empty_result)
    response = c.post(f"/api/projects/{p.id}/extract")
    assert response.status_code == 200
    job = wait_job(c, p.id, response.json()["id"])
    assert job["status"] == "succeeded", job
    current = store.get(p.id)
    assert current.facts == [] and current.facts_confirmed
    assert any("未整理出结构化事实" in warning for warning in current.extraction_warnings)
    assert [s.layers for s in current.screens] == [s.layers for s in p.screens]
    response = c.post(f"/api/projects/{p.id}/plan")
    assert wait_job(c, p.id, response.json()["id"])["status"] == "succeeded"


def test_target_edit_only_changes_metadata_and_preserves_design_and_history(client):
    c, store = client
    p = fixture_project(store)
    response = c.patch(
        f"/api/projects/{p.id}/target-model",
        json={"version": p.version, "target_model": " NEW-35 "},
    )
    assert response.status_code == 200, response.text
    current = store.get(p.id)
    assert current.target_model == "NEW-35"
    assert current.facts == p.facts and current.screens == p.screens
    assert current.art_direction == p.art_direction
    assert current.facts_confirmed == p.facts_confirmed
    assert current.direction_confirmed == p.direction_confirmed
    assert current.assets == p.assets and current.pages == p.pages
    assert store.file(p.id, "source.png").is_file()
    assert store.get(p.id, p.version) == p
    assert not store.calls(p.id)
    assert (
        c.patch(
            f"/api/projects/{p.id}/target-model",
            json={"version": p.version, "target_model": "OTHER-35"},
        ).status_code
        == 409
    )
    assert (
        c.patch(
            f"/api/projects/{p.id}/target-model",
            json={"version": current.version, "target_model": "   "},
        ).status_code
        == 200
    )
    assert store.get(p.id).target_model == ""


def test_target_edit_is_blocked_during_an_active_job(client):
    c, store = client
    p = fixture_project(store)
    job = Job(id=uid(), project_id=p.id, kind="extract", status="running")
    store.add_job(job)
    response = c.patch(
        f"/api/projects/{p.id}/target-model",
        json={"version": p.version, "target_model": "NEW-35"},
    )
    assert response.status_code == 409
    assert store.get(p.id) == p


def test_image_constraints_and_geometry():
    for height in (600, 1100, 1700, 2400, 3200):
        w, h = image_size(1200, height)
        assert w % 16 == h % 16 == 0
        assert 655360 <= w * h <= 8294400
    assert Rect(x=0.9, y=0, width=0.2, height=0.2).x == 0.9
    with pytest.raises(ValidationError):
        Rect(x=2, y=0, width=0.2, height=0.2)
    with pytest.raises(AppError):
        image_size(640, 3200)


def test_numeric_text_missing_fact_bindings_and_empty_text_are_editable(client):
    c, store = client
    p = fixture_project(store)
    edit = payload(p, p.screens[0])
    edit["layers"][0]["content"] = ""
    edit["layers"][1].update(content="2027年 / 60 / 99.9%", fact_ids=["missing"])
    response = c.patch(f"/api/projects/{p.id}/screens/s0", json=edit)
    assert response.status_code == 200, response.text
    assert response.json()["screens"][0]["layers"] == edit["layers"]
    assert not store.calls(p.id)


def test_read_and_plan_extracts_then_designs_without_a_facts_gate(client):
    c, store = client
    p = fixture_project(store)

    def clear(current):
        current.facts = []
        current.facts_confirmed = False

    store.mutate(p.id, clear)
    response = c.post(f"/api/projects/{p.id}/read-and-plan")
    assert wait_job(c, p.id, response.json()["id"])["status"] == "succeeded"
    current = store.get(p.id)
    assert current.screens and current.direction_confirmed
    purposes = [call.purpose for call in reversed(store.calls(p.id))]
    assert purposes.index("extract_facts") < purposes.index("design_plan")


def test_direct_planning_needs_no_facts_or_confirmation(client):
    c, store = client
    p = fixture_project(store)

    def clear(current):
        current.facts = []
        current.facts_confirmed = current.direction_confirmed = False

    store.mutate(p.id, clear)
    response = c.post(f"/api/projects/{p.id}/plan")
    assert wait_job(c, p.id, response.json()["id"])["status"] == "succeeded"
    current = store.get(p.id)
    assert current.screens and current.direction_confirmed
    assert not current.facts and not current.facts_confirmed
    assert p.pages[0].text in store.calls(p.id)[0].prompt


def test_export_uses_available_route_without_explicit_selection(client):
    c, store = client
    p = fixture_project(store)
    response = c.post(f"/api/projects/{p.id}/generate", json={"screen_ids": ["s0"], "route": "B"})
    assert wait_job(c, p.id, response.json()["id"])["status"] == "succeeded"
    p = store.get(p.id)
    p.screens = [p.screens[0]]
    p.screens[0].selected_route = None
    assert p.screens[0].b
    validate_export(p, "draft")
    validate_export(p, "publish")
    p.screens[0].a, p.screens[0].b = p.screens[0].b, None
    with pytest.raises(AppError, match="无字底图"):
        validate_export(p, "publish")
    p.screens[0].a = None
    with pytest.raises(AppError, match="底图"):
        validate_export(p, "publish")


@pytest.mark.parametrize("operation", ["generate", "fit-layout", "screens/s0/regenerate"])
def test_removed_route_is_rejected_without_jobs_or_calls(client, operation):
    c, store = client
    p = fixture_project(store)
    body = (
        {"route": "A", "instruction": "不应执行"}
        if operation.endswith("regenerate")
        else {"screen_ids": ["s0"], "route": "A"}
    )
    assert c.post(f"/api/projects/{p.id}/{operation}", json=body).status_code == 422
    assert store.calls(p.id) == [] and store.jobs(p.id) == []


def test_reference_background_pipeline_and_text_changes_do_not_call_models(client):
    c, store = client
    p = fixture_project(store)
    p = store.mutate(
        p.id,
        lambda current: (
            setattr(current, "facts_confirmed", False),
            setattr(current, "direction_confirmed", False),
        ),
    )
    response = c.post(f"/api/projects/{p.id}/generate", json={"screen_ids": ["s0"], "route": None})
    assert response.status_code == 200
    job = wait_job(c, p.id, response.json()["id"])
    assert job["status"] == "succeeded", job
    p = store.get(p.id)
    screen = p.screens[0]
    assert screen.concept and screen.b and screen.layout_from_concept and not screen.a and not screen.mask
    assert len([x for x in store.calls(p.id) if x.purpose == "concept"]) == 1
    assert not any(x.purpose.startswith("audit_") for x in store.calls(p.id))
    assert {x.purpose for x in store.calls(p.id)} == {"concept", "extract_layout", "B_regenerate"}
    concept_calls = [x for x in store.calls(p.id) if x.purpose == "concept"]
    background_calls = [x for x in store.calls(p.id) if x.purpose == "B_regenerate"]
    assert concept_calls and background_calls
    assert all('"copy"' in call.prompt and '"text_regions"' not in call.prompt for call in concept_calls)
    assert all(
        '"text_regions"' in call.prompt and '"text_color"' in call.prompt for call in background_calls
    )
    before_calls = len(store.calls(p.id))
    before_other = p.screens[1].model_dump()
    edit = payload(p, screen)
    edit["layers"][0]["content"] = "可以修改的标题"
    response = c.patch(f"/api/projects/{p.id}/screens/s0", json=edit)
    assert response.status_code == 200, response.text
    assert len(store.calls(p.id)) == before_calls
    p = store.get(p.id)
    assert p.screens[0].layers[0].content == "可以修改的标题"
    assert p.screens[1].model_dump() == before_other
    assert c.get(f"/api/projects/{p.id}?version={p.version - 1}").status_code == 200
    assert c.patch(f"/api/projects/{p.id}/screens/s0", json=edit).status_code == 409


def test_each_content_screen_uses_its_own_reference_and_parameters_skip_images(client, monkeypatch):
    c, store = client
    p = fixture_longform_project(store)
    image = FakeFoundry.image
    backgrounds = {}

    def record(self, pid, sid, purpose, prompt, size, references):
        if purpose == "B_regenerate":
            backgrounds[sid] = references[0].name
        return image(self, pid, sid, purpose, prompt, size, references)

    monkeypatch.setattr(FakeFoundry, "image", record)
    response = c.post(
        f"/api/projects/{p.id}/generate", json={"screen_ids": [s.id for s in p.screens]}
    )
    assert wait_job(c, p.id, response.json()["id"])["status"] == "succeeded"
    current = store.get(p.id)
    assert len(backgrounds) == 7 and len(set(backgrounds.values())) == 7
    assert all(backgrounds[s.id] == s.concept.file for s in current.screens[:7])
    assert not current.screens[7].concept and not current.screens[7].b
    assert len(store.calls(p.id)) == 21
    assert {call.purpose for call in store.calls(p.id)} == {
        "concept",
        "extract_layout",
        "B_regenerate",
    }


def test_batch_screen_edits_are_atomic_versioned_and_do_not_call_models(client):
    c, store = client
    p = fixture_project(store)
    updates = {}
    for screen in p.screens[:2]:
        update = payload(p, screen)
        update.pop("version")
        update["layers"][0]["content"] = f"批量编辑：{screen.id}\n可换行"
        updates[screen.id] = update
    response = c.patch(
        f"/api/projects/{p.id}/screens", json={"version": p.version, "screens": updates}
    )
    assert response.status_code == 200, response.text
    current = store.get(p.id)
    assert current.version == p.version + 1
    assert all(s.layers[0].content.startswith("批量编辑") for s in current.screens[:2])
    assert current.screens[2] == p.screens[2] and current.facts == p.facts
    assert (
        c.patch(
            f"/api/projects/{p.id}/screens", json={"version": p.version, "screens": updates}
        ).status_code
        == 409
    )
    updates["missing"] = updates.pop("s1")
    assert (
        c.patch(
            f"/api/projects/{p.id}/screens", json={"version": current.version, "screens": updates}
        ).status_code
        == 404
    )
    assert store.get(p.id) == current
    updates["s1"] = updates.pop("missing")
    updates["s1"]["layers"].append(updates["s1"]["layers"][0])
    assert (
        c.patch(
            f"/api/projects/{p.id}/screens", json={"version": current.version, "screens": updates}
        ).status_code
        == 422
    )
    assert store.get(p.id) == current and store.calls(p.id) == []
    store.add_job(Job(id=uid(), project_id=p.id, kind="test", status="running"))
    assert (
        c.patch(
            f"/api/projects/{p.id}/screens",
            json={"version": current.version, "screens": {"s0": updates["s0"]}},
        ).status_code
        == 409
    )


@pytest.mark.parametrize("ids", [[], [""], [f"screen{i}" for i in range(17)]])
def test_batch_screen_edits_keep_resource_and_identifier_limits(client, ids):
    c, store = client
    p = fixture_project(store)
    edit = payload(p, p.screens[0])
    edit.pop("version")
    response = c.patch(
        f"/api/projects/{p.id}/screens",
        json={"version": p.version, "screens": {screen_id: edit for screen_id in ids}},
    )
    assert response.status_code == 422
    assert store.get(p.id) == p and store.calls(p.id) == []


def test_regeneration_preserves_human_layers_and_other_screens(client):
    c, store = client
    p = fixture_project(store)
    p.screens[0].layers[0].content = "人工修改后的标题"
    store.mutate(p.id, lambda current: setattr(current, "screens", p.screens))
    original = store.get(p.id)
    response = c.post(
        f"/api/projects/{p.id}/screens/s0/regenerate",
        json={"instruction": "改为明亮卧室场景", "route": "B"},
    )
    assert wait_job(c, p.id, response.json()["id"])["status"] == "succeeded"
    current = store.get(p.id)
    assert [(x.id, x.content, x.fact_ids) for x in current.screens[0].layers] == [
        (x.id, x.content, x.fact_ids) for x in original.screens[0].layers
    ]
    assert current.screens[0].layers[0].content == "人工修改后的标题"
    assert current.screens[1:] == original.screens[1:]
    assert current.art_direction == original.art_direction
    assert current.screens[0].b


def test_layout_fitting_preserves_text_sources_images_and_other_screens(client):
    c, store = client
    p = fixture_project(store)
    response = c.post(f"/api/projects/{p.id}/generate", json={"screen_ids": ["s0"], "route": "B"})
    assert wait_job(c, p.id, response.json()["id"])["status"] == "succeeded"
    original = store.get(p.id)
    calls = len(store.calls(p.id))
    response = c.post(f"/api/projects/{p.id}/fit-layout", json={"screen_ids": ["s0"], "route": "B"})
    assert wait_job(c, p.id, response.json()["id"])["status"] == "succeeded"
    current = store.get(p.id)
    old, fitted = original.screens[0], current.screens[0]
    assert fitted.layers != old.layers
    assert [(x.id, x.content, x.fact_ids) for x in fitted.layers] == [
        (x.id, x.content, x.fact_ids) for x in old.layers
    ]
    assert (fitted.concept, fitted.a, fitted.b) == (old.concept, old.a, old.b)
    assert current.screens[1:] == original.screens[1:]
    assert not fitted.approved and "待人工核对" in fitted.warnings[-1]
    assert len(store.calls(p.id)) == calls + 1
    assert store.calls(p.id)[0].purpose == "fit_layout_B"


def test_layout_fitting_applies_style_but_retains_original_text(client, monkeypatch):
    c, store = client
    p = fixture_project(store)
    response = c.post(f"/api/projects/{p.id}/generate", json={"screen_ids": ["s0"], "route": "B"})
    assert wait_job(c, p.id, response.json()["id"])["status"] == "succeeded"
    original = store.get(p.id)
    structured = FakeFoundry.structured

    def changed(self, project_id, purpose, prompt, schema, images=None, screen_id=None):
        result = structured(self, project_id, purpose, prompt, schema, images, screen_id)
        if purpose.startswith("fit_layout_"):
            result.layers[0].content = "模型擅自改写的文案"
        return result

    monkeypatch.setattr(FakeFoundry, "structured", changed)
    response = c.post(f"/api/projects/{p.id}/fit-layout", json={"screen_ids": ["s0"], "route": "B"})
    job = wait_job(c, p.id, response.json()["id"])
    assert job["status"] == "succeeded", job
    current = store.get(p.id)
    assert current.screens[0].layers[0].content == original.screens[0].layers[0].content
    assert current.screens[0].layers[0].y == 0.13
    assert (
        c.post(
            f"/api/projects/{p.id}/fit-layout", json={"screen_ids": ["s0"], "route": None}
        ).status_code
        == 200
    )


def test_layout_fitting_reuses_old_background_and_preserves_omitted_text(client, monkeypatch):
    c, store = client
    p = fixture_project(store)
    response = c.post(f"/api/projects/{p.id}/generate", json={"screen_ids": ["s0"], "route": "B"})
    assert wait_job(c, p.id, response.json()["id"])["status"] == "succeeded"

    def change_direction(current):
        current.facts_confirmed = current.direction_confirmed = False
        current.art_version += 1
        current.screens[0].stale = True

    original = store.mutate(p.id, change_direction)
    structured = FakeFoundry.structured

    def incomplete(self, project_id, purpose, prompt, schema, images=None, screen_id=None):
        result = structured(self, project_id, purpose, prompt, schema, images, screen_id)
        if purpose.startswith("fit_layout_"):
            result.layers = [result.layers[1], result.layers[1].model_copy(deep=True)]
        return result

    monkeypatch.setattr(FakeFoundry, "structured", incomplete)
    response = c.post(f"/api/projects/{p.id}/fit-layout", json={"screen_ids": ["s0"], "route": "B"})
    job = wait_job(c, p.id, response.json()["id"])
    assert job["status"] == "succeeded", job
    screen = store.get(p.id).screens[0]
    assert screen.b == original.screens[0].b
    assert len({layer.id for layer in screen.layers}) == len(screen.layers)
    assert {layer.id: (layer.content, layer.fact_ids) for layer in screen.layers} == {
        layer.id: (layer.content, layer.fact_ids) for layer in original.screens[0].layers
    }
    assert "已保留原位置" in screen.warnings[-1] and "重复" in screen.warnings[-1]


def test_style_revision_invalidates_assets(client):
    c, store = client
    p = fixture_project(store)
    art = p.art_direction.model_copy(update={"lighting": "暖阳光"})
    response = c.put(
        f"/api/projects/{p.id}/direction",
        json=ArtEdit(version=p.version, art_direction=art).model_dump(),
    )
    assert response.status_code == 200
    p = store.get(p.id)
    assert p.art_version == 2 and p.direction_confirmed
    assert all(s.stale for s in p.screens)


def test_plan_text_edit_does_not_invalidate_images(client):
    c, store = client
    p = fixture_project(store)
    art = p.art_direction.model_copy(update={"lighting": "只改说明"})
    response = c.put(
        f"/api/projects/{p.id}/direction",
        json=ArtEdit(
            version=p.version, art_direction=art, invalidate_images=False
        ).model_dump(),
    )
    assert response.status_code == 200, response.text
    current = store.get(p.id)
    assert current.art_version == 1
    assert current.art_direction.lighting == "只改说明"
    assert not any(screen.stale for screen in current.screens)


def test_confirming_claim_does_not_invalidate_backdrops(client):
    c, store = client
    p = fixture_project(store)
    facts = [f.model_copy(update={"approved": True}) for f in p.facts]
    response = c.put(
        f"/api/projects/{p.id}/facts",
        json={"version": p.version, "facts": [f.model_dump() for f in facts]},
    )
    assert response.status_code == 200
    current = store.get(p.id)
    assert current.direction_confirmed and not any(s.stale for s in current.screens)


def test_unapproved_claims_stale_screens_and_unconfirmed_direction_do_not_block_export(store):
    p = fixture_project(store)
    p.screens = [p.screens[0]]
    p.screens[0].kind = "parameters"
    p.screens[0].approved = False
    p.screens[0].stale = True
    p.facts_confirmed = p.direction_confirmed = False
    p.art_direction = None
    p.facts[1].condition = ""
    p.screens[0].layers[1].content = "25%"
    p.screens[0].layers[1].fact_ids = ["f2"]
    validate_export(p, "draft")
    validate_export(p, "publish")


def test_legacy_approval_metadata_does_not_gate_saving(client):
    c, store = client
    p = fixture_project(store)
    edit = payload(p, p.screens[0])
    edit["approved"] = True
    assert c.patch(f"/api/projects/{p.id}/screens/s0", json=edit).status_code == 200


def test_store_blocks_parallel_jobs_and_marks_restart(store):
    p = fixture_project(store)
    first = Job(id=uid(), project_id=p.id, kind="test", status="running")
    store.add_job(first)
    with pytest.raises(AppError, match="进行中"):
        store.add_job(Job(id=uid(), project_id=p.id, kind="test", status="queued"))
    with pytest.raises(AppError, match="生成中"):
        store.mutate(p.id, lambda p: None, require_idle=True)
    reopened = Store(store.root)
    assert reopened.jobs(p.id)[0].status == "failed"
    assert "未自动重试" in reopened.jobs(p.id)[0].error


def test_failed_job_is_visible_and_never_uses_placeholder(client):
    c, store = client
    p = store.create(Project(id=uid(), name="缺少素材", target_model="TEST"))
    response = c.post(f"/api/projects/{p.id}/extract")
    job = wait_job(c, p.id, response.json()["id"])
    assert job["status"] == "failed" and "说明书" in job["error"]
    assert not store.get(p.id).screens


def test_export_package_round_trip_and_zip_traversal_rejected(client):
    c, store = client
    p = fixture_project(store)
    Image.new("RGB", (10, 10), "white").save(store.file(p.id, "old_export.png"))
    package = c.get(f"/api/projects/{p.id}/package")
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert "files/source.png" in archive.namelist()
        assert "files/old_export.png" not in archive.namelist()
    response = c.post(
        "/api/projects/import", files={"file": ("project.zip", package.content, "application/zip")}
    )
    assert response.status_code == 200, response.text
    restored = response.json()
    assert (
        restored["id"] != p.id
        and restored["screens"][0]["layers"][0]["content"] == p.screens[0].layers[0].content
    )
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("project.json", p.model_dump_json())
        archive.writestr("files/../../escape.png", "bad")
    response = c.post("/api/projects/import", files={"file": ("bad.zip", bad.getvalue())})
    assert response.status_code == 400 and "不安全" in response.json()["error"]


def test_foundry_endpoint_and_cross_site_guards(client):
    c, _ = client
    with pytest.raises(ValidationError):
        FoundrySettings(planning_endpoint="https://evil.example.com")
    with pytest.raises(ValidationError):
        FoundrySettings(image_endpoint="http://127.0.0.1:8000")
    assert (
        c.post(
            "/api/projects",
            headers={"Origin": "https://evil.example"},
            json={"name": "no", "target_model": "X"},
        ).status_code
        == 403
    )
    assert c.post("/api/foundry/deploy", json={"confirmed": False}).status_code == 422


@pytest.mark.parametrize(
    "filename", ["files/view.html", "files//source.png", "files/./source.png", "files\\source.png"]
)
def test_import_rejects_active_content_and_ambiguous_paths(client, filename):
    c, store = client
    p = fixture_project(store)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("project.json", p.model_dump_json())
        archive.writestr(filename, "<html>unsafe</html>")
    response = c.post("/api/projects/import", files={"file": ("bad.zip", data.getvalue())})
    assert response.status_code == 400
    assert len(store.list()) == 1


def test_import_rejects_duplicate_entries(client):
    c, store = client
    p = fixture_project(store)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("project.json", p.model_dump_json())
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("project.json", p.model_dump_json())
    response = c.post("/api/projects/import", files={"file": ("bad.zip", data.getvalue())})
    assert response.status_code == 400 and "重复" in response.json()["error"]


def test_missing_settings_is_explicit_and_never_calls_http(store):
    provider = Foundry(store, FoundrySettings())
    with pytest.raises(AppError, match="模型设置"):
        provider.verify("planning")
    assert not store.list()


def test_file_paths_are_flat(store):
    with pytest.raises(AppError):
        store.file("project", "../secret")
    with pytest.raises(AppError):
        store.files("../../project")


def test_unknown_format_and_size_errors(store):
    with pytest.raises(AppError):
        ingest(store, "pid", "x.png", "product", b"not an image")
    with pytest.raises(AppError):
        ingest(store, "pid", "x.png", "product", b"x" * (25 * 1024 * 1024 + 1))


def test_current_and_historical_versions_are_available(store):
    p = fixture_project(store)
    original = p.model_dump()
    assert store.get(p.id, 1) == p
    current = store.mutate(p.id, lambda p: setattr(p, "name", "新名称"), 1)
    assert store.get(p.id, 1).model_dump() == original
    assert store.get(p.id, current.version).name == "新名称"
    assert store.revisions(p.id)[0]["version"] == 1


def test_screen_kinds_are_flexible_but_canvas_memory_bounds_are_enforced(store):
    p = fixture_project(store)
    with pytest.raises(ValidationError, match="less than or equal to 1600"):
        Project.model_validate({**p.model_dump(), "width": 100000})
    p.screens[2].kind = "technical"
    result = ScenePlan(width=p.width, art_direction=p.art_direction, screens=[p.screens[2]])
    assert len(result.screens) == 1 and result.screens[0].kind == "technical"


def test_import_preserves_call_provenance_without_overwriting_original(client):
    c, store = client
    p = fixture_project(store)
    response = c.post(f"/api/projects/{p.id}/generate", json={"screen_ids": ["s0"], "route": "B"})
    assert wait_job(c, p.id, response.json()["id"])["status"] == "succeeded"
    originals = [call.model_dump() for call in store.calls(p.id)]
    package = c.get(f"/api/projects/{p.id}/package")
    response = c.post("/api/projects/import", files={"file": ("project.zip", package.content)})
    assert response.status_code == 200, response.text
    restored = store.get(response.json()["id"])
    calls = store.calls(restored.id)
    assert len(calls) == len(originals) > 0
    assert all(call.project_id == restored.id for call in calls)
    assert set(call.request["imported_call_id"] for call in calls) == {
        call["id"] for call in originals
    }
    assert restored.screens[0].b.call_id in {call.id for call in calls}
    assert [call.model_dump() for call in store.calls(p.id)] == originals


def test_restore_retains_nested_models_and_current_identity(client):
    c, store = client
    p = fixture_project(store)
    current = store.mutate(p.id, lambda p: setattr(p.screens[0].layers[0], "content", "新标题"))
    response = c.post(
        f"/api/projects/{p.id}/revisions/1/restore", json={"version": current.version}
    )
    assert response.status_code == 200, response.text
    restored = store.get(p.id)
    assert restored.id == p.id and restored.version == current.version + 1
    assert restored.screens[0].layers[0].content == p.screens[0].layers[0].content
    assert c.get(f"/api/projects/{p.id}").status_code == 200
