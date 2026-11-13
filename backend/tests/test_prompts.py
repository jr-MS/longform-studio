import json

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.models import Job
from backend.pipeline import Pipeline
from backend.prompts import PROJECT_STEPS, SCHEMAS, TITLES, build_prompt, structured_system_prompt
from backend.storage import uid
from backend.tests.fixtures import FakeFoundry, fixture_project
from backend.tests.test_core import wait_job


@pytest.fixture
def prompt_client(tmp_path):
    app = create_app(tmp_path / "prompt-tests", provider_factory=FakeFoundry)
    with TestClient(app) as client:
        yield client, app.state.store


def edit(client, store, pid, step, instructions, screen_id=None, version=None):
    return client.put(
        f"/api/projects/{pid}/prompts",
        json={
            "version": version if version is not None else store.get(pid).version,
            "step": step,
            "instructions": instructions,
            "screen_id": screen_id,
        },
    )


def test_catalog_is_complete_read_only_and_shared_with_execution(prompt_client):
    client, store = prompt_client
    project = fixture_project(store)
    response = client.get(f"/api/projects/{project.id}/prompts", params={"screen_id": "s0"})
    assert response.status_code == 200, response.text
    catalog = response.json()
    project = store.get(project.id)
    assert {entry["step"] for entry in catalog["entries"]} == set(TITLES)
    for entry in catalog["entries"]:
        screen = None if entry["step"] in PROJECT_STEPS else project.screens[0]
        assert entry == build_prompt(project, entry["step"], screen).model_dump()
        assert entry["effective_prompt"] == entry["instructions"] + entry["context"]
        if entry["step"] in SCHEMAS:
            assert entry["system_prompt"] == structured_system_prompt(SCHEMAS[entry["step"]])
    assert store.get(project.id) == project
    assert store.calls(project.id) == [] and store.jobs(project.id) == []
    assert len(client.get(f"/api/projects/{project.id}/prompts").json()["entries"]) == 3


def test_legacy_a_instructions_are_preserved_but_not_exposed_to_active_ai_steps(prompt_client):
    client, store = prompt_client
    project = fixture_project(store)

    def legacy(current):
        current.screens[0].erase_prompt = "旧 A 去字指令"
        current.screens[0].selected_route = "A"
        current.prompt_overrides["s0:A_erase"] = "不应执行的旧提示词"

    project = store.mutate(project.id, legacy)
    entries = client.get(f"/api/projects/{project.id}/prompts", params={"screen_id": "s0"}).json()[
        "entries"
    ]
    assert len(entries) == 8
    revision = next(entry for entry in entries if entry["step"] == "revise_screen_prompts")
    data = json.loads(revision["context"][revision["context"].index("{") :])
    assert not ({"erase_prompt", "a", "mask", "reviews", "selected_route"} & data["screen"].keys())
    assert edit(client, store, project.id, "A_erase", "禁止恢复此步骤", "s0").status_code == 422
    assert store.get(project.id) == project and store.calls(project.id) == []


@pytest.mark.parametrize("step", list(TITLES))
def test_every_ai_step_uses_edited_preview_verbatim(prompt_client, monkeypatch, step):
    client, store = prompt_client
    project = fixture_project(store)
    setup = Job(id=uid(), project_id=project.id, kind="test_setup", status="running")
    Pipeline(store, FakeFoundry).generate(setup, ["s0"], None)
    if step == "scanned_page_ocr":
        store.mutate(project.id, lambda current: setattr(current.pages[0], "text", ""))
    screen_id = None if step in PROJECT_STEPS else "s0"
    marker = f"用户自定义提示词：{step}，请保持本次设计意图。"
    response = edit(client, store, project.id, step, marker, screen_id)
    assert response.status_code == 200, response.text
    instruction = "保持构图，改成午后卧室"
    catalog = client.get(
        f"/api/projects/{project.id}/prompts",
        params={"screen_id": "s0", "instruction": instruction},
    ).json()
    preview = next(entry for entry in catalog["entries"] if entry["step"] == step)
    observed = []
    structured = FakeFoundry.structured
    image = FakeFoundry.image

    def record_structured(self, pid, purpose, prompt, schema, images=None, screen_id=None):
        if purpose == step:
            observed.append((prompt, [path.name for path in images or []]))
        return structured(self, pid, purpose, prompt, schema, images, screen_id)

    def record_image(self, pid, sid, purpose, prompt, size, references):
        if purpose == step:
            observed.append((prompt, [path.name for path in references]))
        return image(self, pid, sid, purpose, prompt, size, references)

    monkeypatch.setattr(FakeFoundry, "structured", record_structured)
    monkeypatch.setattr(FakeFoundry, "image", record_image)
    if step in ("scanned_page_ocr", "extract_facts"):
        response = client.post(f"/api/projects/{project.id}/extract")
    elif step == "design_plan":
        response = client.post(f"/api/projects/{project.id}/plan")
    elif step == "extract_layout":
        response = client.post(
            f"/api/projects/{project.id}/extract-layout",
            json={"screen_ids": ["s0"], "route": "B"},
        )
    elif step.startswith("fit_layout_"):
        response = client.post(
            f"/api/projects/{project.id}/fit-layout",
            json={"screen_ids": ["s0"], "route": step[-1]},
        )
    elif step == "revise_screen_prompts":
        response = client.post(
            f"/api/projects/{project.id}/screens/s0/regenerate",
            json={"instruction": instruction, "route": "B"},
        )
    else:
        response = client.post(
            f"/api/projects/{project.id}/generate",
            params={"concepts_only": step == "concept"},
            json={"screen_ids": ["s0"], "route": "B"},
        )
    assert response.status_code == 200, response.text
    job = wait_job(client, project.id, response.json()["id"])
    assert job["status"] == "succeeded", job
    assert observed == [(preview["effective_prompt"], preview["image_files"])]
    assert (
        next(call for call in store.calls(project.id) if call.purpose == step).prompt
        == preview["effective_prompt"]
    )


def test_overrides_are_scoped_resettable_and_round_trip_in_package(prompt_client):
    client, store = prompt_client
    project = fixture_project(store)
    for step, sid in (("design_plan", None), ("B_regenerate", "s0")):
        response = edit(client, store, project.id, step, "本步骤的自定义视觉设计指令", sid)
        assert response.status_code == 200, response.text
    current = store.get(project.id)
    assert current.screens == project.screens and current.facts == project.facts
    assert build_prompt(current, "B_regenerate", current.screens[0]).customized
    assert not build_prompt(current, "B_regenerate", current.screens[1]).customized
    package = client.get(f"/api/projects/{project.id}/package")
    restored = client.post("/api/projects/import", files={"file": ("prompts.zip", package.content)})
    assert restored.status_code == 200, restored.text
    assert restored.json()["prompt_overrides"] == current.prompt_overrides
    response = edit(client, store, project.id, "B_regenerate", None, "s0")
    assert response.status_code == 200, response.text
    after = store.get(project.id)
    assert not build_prompt(after, "B_regenerate", after.screens[0]).customized
    assert build_prompt(after, "design_plan").customized
    assert store.calls(project.id) == [] and store.jobs(project.id) == []


def test_prompt_saves_obey_version_and_job_protection(prompt_client):
    client, store = prompt_client
    project = fixture_project(store)
    assert edit(client, store, project.id, "design_plan", "保存有效的设计指令").status_code == 200
    assert (
        edit(
            client, store, project.id, "design_plan", "旧版本不应覆盖", version=project.version
        ).status_code
        == 409
    )
    assert edit(client, store, project.id, "concept", "缺少分屏").status_code == 422
    assert (
        edit(client, store, project.id, "design_plan", "项目级不可绑定分屏", "s0").status_code
        == 422
    )
    assert edit(client, store, project.id, "concept", "不存在的分屏", "missing").status_code == 404
    store.add_job(Job(id=uid(), project_id=project.id, kind="test", status="running"))
    before = store.get(project.id)
    assert edit(client, store, project.id, "design_plan", "运行中不应覆盖").status_code == 409
    assert store.get(project.id) == before


def test_concept_prompt_change_keeps_old_images_and_updates_on_next_generation(prompt_client):
    client, store = prompt_client
    project = fixture_project(store)
    setup = Job(id=uid(), project_id=project.id, kind="test_setup", status="running")
    pipeline = Pipeline(store, FakeFoundry)
    pipeline.generate(setup, ["s0"], "B")
    before = store.get(project.id)
    calls = len(store.calls(project.id))
    response = edit(client, store, project.id, "concept", "请使用新的留白、光线与构图。", "s0")
    assert response.status_code == 200
    saved = store.get(project.id)
    assert saved.screens[0].concept == before.screens[0].concept
    assert saved.screens[0].b == before.screens[0].b and saved.screens[0].stale
    assert len(store.calls(project.id)) == calls
    pipeline.generate(setup, ["s0"], "B", concepts_only=True)
    assert store.get(project.id).screens[0].concept != before.screens[0].concept
    assert store.get(project.id).screens[1:] == before.screens[1:]


def test_route_prompt_change_reuses_current_concept_and_survives_replanning(prompt_client):
    client, store = prompt_client
    project = fixture_project(store)
    setup = Job(id=uid(), project_id=project.id, kind="test_setup", status="running")
    pipeline = Pipeline(store, FakeFoundry)
    pipeline.generate(setup, ["s0"], "B")
    concept = store.get(project.id).screens[0].concept
    marker = "请根据当前含字样稿生成无字底图，保持构图。"
    assert edit(client, store, project.id, "B_regenerate", marker, "s0").status_code == 200
    assert not store.get(project.id).screens[0].stale
    calls = len(store.calls(project.id))
    pipeline.generate(setup, ["s0"], "B")
    assert len(store.calls(project.id)) == calls + 1
    assert store.get(project.id).screens[0].concept == concept
    pipeline.plan(setup)
    assert store.get(project.id).prompt_overrides["s0:B_regenerate"] == marker


def test_full_length_generated_prompt_can_be_previewed_and_edited(prompt_client):
    client, store = prompt_client
    project = fixture_project(store)
    store.mutate(
        project.id, lambda current: setattr(current.screens[0], "concept_prompt", "字" * 30000)
    )
    response = client.get(f"/api/projects/{project.id}/prompts", params={"screen_id": "s0"})
    assert response.status_code == 200, response.text
    entry = next(item for item in response.json()["entries"] if item["step"] == "concept")
    assert len(entry["instructions"]) > 30000
    assert (
        edit(
            client, store, project.id, "concept", entry["instructions"] + " 微调", "s0"
        ).status_code
        == 200
    )
