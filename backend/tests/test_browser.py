import io
import os
import re
import socket
import subprocess
import sys
import time
import zipfile

import httpx
import pytest
from PIL import Image
from playwright.sync_api import expect, sync_playwright

from backend.exporter import package_project
from backend.main import ROOT
from backend.models import FoundrySettings, Job, RouteReview
from backend.pipeline import Pipeline
from backend.storage import Store, uid
from backend.tests.fixtures import FakeFoundry, fixture_longform_project, fixture_project
from backend.tests.test_core import wait_job


@pytest.fixture(scope="module")
def browser_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("browser")
    store = Store(root / "data")
    store.set_settings(
        FoundrySettings(
            planning_endpoint="https://test.cognitiveservices.azure.com",
            planning_deployment="design-test",
            image_endpoint="https://test.cognitiveservices.azure.com",
            image_deployment="image-test",
        )
    )
    p = fixture_project(store)
    pipeline = Pipeline(store, FakeFoundry)
    job = Job(id=uid(), project_id=p.id, kind="test_setup", status="running")
    pipeline.generate(job, [s.id for s in p.screens], "B")

    def unreviewed(current):
        current.facts_confirmed = current.direction_confirmed = False
        for screen in current.screens:
            screen.stale = True
            screen.selected_route = None

    store.mutate(p.id, unreviewed)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = {**os.environ, "STUDIO_DATA_DIR": str(root / "data"), "STUDIO_ORIGIN": origin}
    with (root / "server.log").open("w+") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.tests.fixtures:create_test_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(100):
                try:
                    if httpx.get(origin + "/api/health").status_code == 200:
                        break
                except httpx.ConnectError:
                    time.sleep(0.1)
            else:
                log.seek(0)
                pytest.fail("浏览器测试服务器未就绪：" + log.read())
            yield origin, p.id
        finally:
            process.terminate()
            process.wait(timeout=20)


def test_customer_image_node_editor_is_explicit_and_bounded(browser_server):
    origin, _ = browser_server
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin)
            page.get_by_role("button", name="模型设置", exact=True).click()
            page.get_by_text("可选：添加自己的生图节点，分担限流", exact=True).click()
            assert page.get_by_label("额外节点 1 端点", exact=True).count() == 0
            add = page.get_by_role("button", name="添加额外节点", exact=True)
            add.click()
            page.get_by_label("额外节点 1 端点", exact=True).fill("https://spare.openai.azure.com")
            page.get_by_label("额外节点 1 部署", exact=True).fill("customer-image")
            page.get_by_label("额外节点 1 订阅", exact=True).fill("customer-sub")
            page.get_by_label("额外节点 1 请求上限", exact=True).fill("5")
            assert page.get_by_label("额外节点 1 请求上限", exact=True).input_value() == "5"
            for _ in range(3):
                add.click()
            assert add.is_disabled()
            page.get_by_role("button", name="移除节点 4", exact=True).click()
            assert add.is_enabled()
            assert page.get_by_label("额外节点 1 端点", exact=True).input_value() == "https://spare.openai.azure.com"
            assert not errors
        finally:
            browser.close()


def test_real_browser_edit_reload_and_no_model_call(browser_server):
    origin, pid = browser_server
    calls_before = len(httpx.get(f"{origin}/api/projects/{pid}/calls").json())
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(origin)
        page.get_by_role("heading", name="自动化测试专用项目", exact=True).wait_for()
        page.get_by_role("tab", name="设计工作台").click()
        assert page.get_by_role("button", name="从参考稿生成本屏底图", exact=True).is_enabled()
        assert page.get_by_role("button", name="重新阅读并规划", exact=True).is_enabled()
        assert page.get_by_role("button", name="确认设计方向", exact=True).count() == 0
        page.get_by_role("tab", name="画面编辑").click()
        page.get_by_label("文字内容", exact=True).fill("浏览器中真实修改的标题")
        page.get_by_role("button", name="保存修改", exact=True).click()
        page.get_by_text("修改已保存，不会触发生图调用").wait_for()
        page.reload()
        page.get_by_role("tab", name="画面编辑").click()
        assert page.get_by_label("文字内容", exact=True).input_value() == "浏览器中真实修改的标题"
        current = httpx.get(f"{origin}/api/projects/{pid}").json()
        assert current["screens"][0]["layers"][0]["content"] == "浏览器中真实修改的标题"
        assert len(httpx.get(f"{origin}/api/projects/{pid}/calls").json()) == calls_before
        assert not errors
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate(
            "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth") is True, (
            page.evaluate(
                """() => [...document.querySelectorAll('body *')]
                    .filter(e => e.getBoundingClientRect().right > innerWidth && e.getBoundingClientRect().width > 0)
                    .map(e => ({tag: e.tagName, cls: e.className, right: e.getBoundingClientRect().right})).slice(0, 15)"""
            )
        )
        browser.close()


def test_multicolor_edits_gradient_reload_and_export(browser_server):
    origin, pid = browser_server
    with httpx.Client(base_url=origin, timeout=30) as client:
        package = client.get(f"/api/projects/{pid}/package")
        restored = client.post("/api/projects/import", files={"file": ("color.zip", package.content)})
        assert restored.status_code == 200
        project = restored.json()
        pid = project["id"]
        screen = project["screens"][0]
        screen["layers"][0].update(
            content="强劲气流", font_size=60, height=.2, color="#222222",
            runs=[
                {"text": "强劲", "color": "#222222"},
                {"text": "气流", "color": "#b58d61", "gradient_end": "#e5d7b3", "gradient_angle": 90},
            ],
        )
        edit = {key: screen[key] for key in ("layers", "height", "selected_route", "approved", "reviews")}
        edit["version"] = project["version"]
        assert client.patch(f"/api/projects/{pid}/screens/{screen['id']}", json=edit).status_code == 200
        calls_before = client.get(f"/api/projects/{pid}/calls").json()
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.add_init_script(f"localStorage.setItem('studio-project','{pid}')")
            requests = []
            page.on("request", lambda request: requests.append(request.url))
            page.goto(origin)
            page.get_by_label("文字内容", exact=True).wait_for()
            assert not any(url.endswith("/calls") for url in requests)
            assert any("summary=true" in url for url in requests)
            canvas = page.locator("[data-text-layer='headline']").first
            canvas.dblclick()
            page.get_by_label("直接编辑画布文字").fill("强劲柔和气流")
            page.get_by_label("直接编辑画布文字").press("Control+Enter")
            assert canvas.locator("span").all_text_contents() == ["强劲柔和", "气流"]
            assert "linear-gradient" in canvas.locator("span").last.get_attribute("style")
            canvas.dblclick()
            page.get_by_label("直接编辑画布文字").fill("取消的整句")
            page.get_by_label("直接编辑画布文字").press("Escape")
            assert canvas.locator("span").all_text_contents() == ["强劲柔和", "气流"]
            page.get_by_label("文字内容", exact=True).fill("强劲柔和气流更远")
            page.get_by_role("button", name="保存修改", exact=True).click()
            page.get_by_text("修改已保存，不会触发生图调用").wait_for()
            page.reload()
            page.get_by_label("文字内容", exact=True).wait_for()
            current = client.get(f"/api/projects/{pid}").json()
            runs = current["screens"][0]["layers"][0]["runs"]
            assert runs[1]["text"] == "气流更远" and runs[1]["gradient_angle"] == 90
            assert runs[1]["gradient_end"] == "#e5d7b3"
            page.get_by_label("渐变终点", exact=True).fill("#ddaa55")
            page.get_by_role("button", name="保存修改", exact=True).click()
            page.get_by_text("修改已保存，不会触发生图调用").wait_for()
            current = client.get(f"/api/projects/{pid}").json()
            page.goto(f"{origin}/render/{pid}/{screen['id']}?version={current['version']}")
            page.wait_for_selector('[data-artboard][data-ready="true"]')
            span = page.locator("[data-text-layer='headline'] span").last
            assert "linear-gradient" in span.get_attribute("style")
            assert "rgb(221, 170, 85)" in span.get_attribute("style")
            pixels = Image.open(io.BytesIO(canvas.screenshot())).convert("RGB")
            assert sum(1 for r, g, b in pixels.getdata() if r - g > 15 and g - b > 20) > 50
            assert "preview=" not in page.locator(".board-background").get_attribute("src")
            assert client.get(f"/api/projects/{pid}/calls").json() == calls_before
            browser.close()


def test_export_downloads_long_image_and_package_remains_reeditable(browser_server):
    origin, pid = browser_server
    with httpx.Client(base_url=origin, timeout=30) as client:
        p = client.get(f"/api/projects/{pid}").json()
        screen = p["screens"][0]
        screen["layers"][0].update(content="溢出文字测试" * 100, height=0.01, font_size=180)
        edit = {
            key: screen[key]
            for key in ("layers", "height", "selected_route", "approved", "reviews")
        }
        edit["version"] = p["version"]
        response = client.patch(f"/api/projects/{pid}/screens/{screen['id']}", json=edit)
        assert response.status_code == 200, response.text
        response = client.post(
            f"/api/projects/{pid}/export",
            json={"mode": "publish", "format": "png"},
        )
        assert response.status_code == 200, response.text
        job_id = response.json()["id"]
        for _ in range(150):
            job = next(
                j for j in client.get(f"/api/projects/{pid}/jobs").json() if j["id"] == job_id
            )
            if job["status"] in ("failed", "succeeded"):
                break
            time.sleep(0.2)
        assert job["status"] == "succeeded", job
        assert job["result"].endswith("_long.png")
        data = client.get(f"/api/projects/{pid}/files/{job['result']}").content
        with Image.open(io.BytesIO(data)) as image:
            assert image.size == (800, 3300)
        package = client.get(f"/api/projects/{pid}/package").content
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            assert "project.json" in archive.namelist()
        restored = client.post("/api/projects/import", files={"file": ("project.zip", package)})
        assert restored.status_code == 200, restored.text
        assert len(restored.json()["screens"]) == 3


def test_real_browser_optional_model_edit_without_clearing_or_model_calls(browser_server):
    origin, _ = browser_server
    response = httpx.post(
        origin + "/api/projects",
        json={"name": "型号修正浏览器测试", "target_model": "pl"},
    )
    assert response.status_code == 200
    pid = response.json()["id"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.add_init_script(f"localStorage.setItem('studio-project', '{pid}')")
        page.goto(origin)
        page.get_by_role("button", name="修改型号", exact=True).click()
        dialog = page.get_by_role("dialog", name="修改目标型号")
        assert "历史版本保留" in dialog.inner_text()
        dialog.get_by_label("产品型号（选填）", exact=True).fill("")
        dialog.get_by_role("button", name="保存型号备注", exact=True).click()
        page.get_by_text("型号备注已保存，现有素材、事实与设计均不变").wait_for()
        page.reload()
        page.get_by_role("button", name="修改型号", exact=True).wait_for()
        assert "未填写型号 · 不影响生成" in page.locator(".workspace-heading").inner_text()
        assert httpx.get(f"{origin}/api/projects/{pid}").json()["target_model"] == ""
        assert httpx.get(f"{origin}/api/projects/{pid}/calls").json() == []
        browser.close()


def test_real_browser_creating_project_without_model(browser_server):
    origin, _ = browser_server
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(origin)
        page.get_by_role("button", name="新建项目", exact=True).click()
        dialog = page.get_by_role("dialog", name="新建项目")
        dialog.get_by_label("项目名称", exact=True).fill("无型号 MVP 浏览器测试")
        assert dialog.get_by_label("产品型号（选填）", exact=True).input_value() == ""
        dialog.get_by_role("button", name="创建本地项目").click()
        page.get_by_role("heading", name="无型号 MVP 浏览器测试", exact=True).wait_for()
        assert "未填写型号 · 不影响生成" in page.locator(".workspace-heading").inner_text()
        browser.close()


@pytest.fixture
def prompt_browser_project(browser_server):
    origin, pid = browser_server
    package = httpx.get(f"{origin}/api/projects/{pid}/package", timeout=30)
    response = httpx.post(
        origin + "/api/projects/import",
        files={"file": ("prompt-browser.zip", package.content)},
        timeout=30,
    )
    assert response.status_code == 200, response.text
    return origin, response.json()["id"]


def test_browser_prompt_save_reload_reset_dirty_protection_and_mobile(prompt_browser_project):
    origin, pid = prompt_browser_project
    before = len(httpx.get(f"{origin}/api/projects/{pid}/calls").json())
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(f"localStorage.setItem('studio-project', '{pid}')")
        page.goto(origin)
        page.get_by_role("button", name="查看或编辑 AI 提示词").click()
        dialog = page.get_by_role("dialog", name="AI 提示词工作台")
        expect(
            dialog.get_by_role("navigation", name="AI 步骤").get_by_role("button")
        ).to_have_count(8)
        dialog.get_by_role("button", name=re.compile("参考重生无字底图")).click()
        textarea = dialog.get_by_label("给 AI 的提示词", exact=True)
        marker = "浏览器自定义提示词：保留主体，让文字区融入构图。"
        textarea.fill(marker)
        dialog.get_by_text("查看实际发送的完整用户 prompt", exact=True).click()
        expect(dialog.locator("[data-effective-prompt]")).to_contain_text(marker)
        expect(dialog.locator("[data-effective-prompt]")).to_contain_text("canvas_width")
        dialog.get_by_role("button", name="保存提示词", exact=True).click()
        dialog.get_by_text("提示词已保存，未调用模型。", exact=True).wait_for()
        assert len(httpx.get(f"{origin}/api/projects/{pid}/calls").json()) == before
        page.reload()
        page.get_by_role("button", name="查看或编辑 AI 提示词").click()
        dialog.get_by_role("button", name=re.compile("参考重生无字底图")).click()
        expect(textarea).to_have_value(marker)

        textarea.fill("不应默默丢失的未保存修改")
        page.once("dialog", lambda confirm: confirm.dismiss())
        dialog.get_by_role("button", name=re.compile("生成本屏含字参考稿")).click()
        expect(textarea).to_have_value("不应默默丢失的未保存修改")
        page.once("dialog", lambda confirm: confirm.accept())
        dialog.get_by_role("button", name=re.compile("生成本屏含字参考稿")).click()
        expect(textarea).not_to_have_value(marker)
        close = dialog.get_by_role("button", name="关闭提示词工作台")
        close.focus()
        page.keyboard.press("Shift+Tab")
        expect(dialog.get_by_role("button", name="继续下一步", exact=True)).to_be_focused()
        page.keyboard.press("Tab")
        expect(close).to_be_focused()
        dialog.get_by_role("button", name=re.compile("参考重生无字底图")).click()
        dialog.get_by_role("button", name="恢复默认", exact=True).click()
        dialog.get_by_text("已恢复默认提示词，未调用模型。", exact=True).wait_for()
        expect(textarea).not_to_have_value(marker)
        assert (
            "s0:B_regenerate"
            not in httpx.get(f"{origin}/api/projects/{pid}").json()["prompt_overrides"]
        )
        page.set_viewport_size({"width": 390, "height": 844})
        expect(dialog).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert dialog.evaluate("(element) => element.scrollWidth <= element.clientWidth")
        assert textarea.evaluate("(element) => element.getBoundingClientRect().right <= innerWidth")
        page.wait_for_function(
            """() => {
                const active = document.querySelector('.prompt-stage-nav [aria-current="step"]');
                const nav = document.querySelector('.prompt-stage-nav');
                return active.getBoundingClientRect().left >= nav.getBoundingClientRect().left
                    && active.getBoundingClientRect().right <= nav.getBoundingClientRect().right;
            }"""
        )

        textarea.fill("版本冲突时也必须保留的修改")
        current = httpx.get(f"{origin}/api/projects/{pid}").json()
        response = httpx.put(
            f"{origin}/api/projects/{pid}/prompts",
            json={
                "version": current["version"],
                "step": "design_plan",
                "instructions": "另一个会话的新设计要求",
            },
        )
        assert response.status_code == 200, response.text
        dialog.get_by_role("button", name="保存提示词", exact=True).click()
        expect(dialog.get_by_role("alert")).to_contain_text("项目已被其他操作更新")
        expect(textarea).to_have_value("版本冲突时也必须保留的修改")
        page.once("dialog", lambda confirm: confirm.dismiss())
        close.click()
        expect(dialog).to_be_visible()
        page.once("dialog", lambda confirm: confirm.accept())
        close.click()
        expect(dialog).not_to_be_visible()
        assert len(httpx.get(f"{origin}/api/projects/{pid}/calls").json()) == before
        assert not errors
        browser.close()


def test_browser_prompt_continue_uses_current_instruction_and_defaults(prompt_browser_project):
    origin, pid = prompt_browser_project
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(f"localStorage.setItem('studio-project', '{pid}')")
        page.goto(origin)
        page.get_by_role("button", name="查看或编辑 AI 提示词").click()
        dialog = page.get_by_role("dialog", name="AI 提示词工作台")
        dialog.get_by_role("button", name=re.compile("按要求修订本屏提示词")).click()
        marker = "用户编辑：仅调整光线和构图，不改文字与产品。"
        dialog.get_by_label("给 AI 的提示词", exact=True).fill(marker)
        instruction = "保持产品位置，改为有自然光的午后卧室"
        dialog.get_by_label("本次重生要求", exact=True).fill(instruction)
        dialog.get_by_text("查看实际发送的完整用户 prompt", exact=True).click()
        expect(dialog.locator("[data-effective-prompt]")).to_contain_text(instruction)
        expect(dialog.get_by_label("给 AI 的提示词", exact=True)).to_have_value(marker)
        expected = dialog.locator("[data-effective-prompt]").inner_text()
        with page.expect_response(
            lambda response: response.url.endswith("/regenerate")
        ) as submitted:
            dialog.get_by_role("button", name="保存并继续下一步", exact=True).click()
        job_id = submitted.value.json()["id"]
        with httpx.Client(base_url=origin) as client:
            job = wait_job(client, pid, job_id)
        assert job["status"] == "succeeded", job
        expect(dialog).not_to_be_visible()
        calls = httpx.get(f"{origin}/api/projects/{pid}/calls").json()
        actual = next(call for call in calls if call["purpose"] == "revise_screen_prompts")
        assert actual["prompt"] == expected
        jobs = httpx.get(f"{origin}/api/projects/{pid}/jobs").json()
        assert next(job for job in jobs if job["id"] == job_id)["status"] == "succeeded"

        page.get_by_role("button", name="查看或编辑 AI 提示词").click()
        dialog.get_by_role("button", name=re.compile("整理产品资料")).click()
        dialog.get_by_text("查看实际发送的完整用户 prompt", exact=True).click()
        expected_default = dialog.locator("[data-effective-prompt]").inner_text()
        writes = []
        page.on(
            "request",
            lambda request: writes.append(request.url) if request.method == "PUT" else None,
        )
        with page.expect_response(lambda response: response.url.endswith("/extract")) as submitted:
            dialog.get_by_role("button", name="继续下一步", exact=True).click()
        job_id = submitted.value.json()["id"]
        with httpx.Client(base_url=origin) as client:
            job = wait_job(client, pid, job_id)
        assert job["status"] == "succeeded", job
        actual = next(
            call
            for call in httpx.get(f"{origin}/api/projects/{pid}/calls").json()
            if call["purpose"] == "extract_facts"
        )
        assert actual["prompt"] == expected_default
        assert not writes
        assert not errors
        browser.close()


@pytest.fixture
def continuous_browser_project(browser_server, tmp_path):
    origin, _ = browser_server
    store = Store(tmp_path / "longform")
    project = fixture_longform_project(store)
    Pipeline(store, FakeFoundry).generate(
        Job(id=uid(), project_id=project.id, kind="test_setup", status="running"),
        [screen.id for screen in project.screens],
        "B",
    )
    Image.new("RGBA", (800, 800), "white").save(store.file(project.id, "legacy-mask.png"))

    def legacy(current):
        screen = current.screens[0]
        screen.a = screen.b.model_copy(update={"file": "source.png"})
        screen.mask = "legacy-mask.png"
        screen.erase_prompt = "旧去字指令，仅保留历史。"
        screen.selected_route = "A"
        screen.reviews["A"] = RouteReview(
            composition=3, cleanliness=3, consistency=3, typography=3, notes="旧路线评分"
        )
        current.prompt_overrides["e0:A_erase"] = "不应再执行的历史指令"

    project = store.mutate(project.id, legacy)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        package_project(store, project, archive)
    response = httpx.post(
        origin + "/api/projects/import",
        files={"file": ("continuous-editor.zip", data.getvalue())},
        timeout=30,
    )
    assert response.status_code == 200, response.text
    return origin, response.json()["id"]


def test_eight_screen_continuous_inline_edit_drag_atomic_save_and_reload(
    continuous_browser_project,
):
    origin, pid = continuous_browser_project
    before = httpx.get(f"{origin}/api/projects/{pid}").json()
    calls = httpx.get(f"{origin}/api/projects/{pid}/calls").json()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(f"localStorage.setItem('studio-project', '{pid}')")
        page.goto(origin)
        frames = page.locator("[data-screen-id]")
        expect(frames).to_have_count(8)
        page.wait_for_function(
            """() => [...document.querySelectorAll('[data-screen-id]')].every(
                element => Math.abs(element.getBoundingClientRect().height
                    / element.getBoundingClientRect().width
                    - Number(element.querySelector('[data-artboard]').style.height.replace('px','')) / 800) < .01)"""
        )
        bounds = frames.evaluate_all(
            "(elements) => elements.map(element => { const r = element.getBoundingClientRect(); return {top:r.top, bottom:r.bottom, width:r.width}; })"
        )
        assert all(abs(bounds[i]["bottom"] - bounds[i + 1]["top"]) < 1 for i in range(7))
        assert len({round(rect["width"], 2) for rect in bounds}) == 1
        zoom = page.get_by_label("长图缩放")
        zoom.select_option("overview")
        page.wait_for_function(
            """() => {
                const frames = [...document.querySelectorAll('[data-screen-id]')];
                return frames[0].getBoundingClientRect().top >= 0
                    && frames[7].getBoundingClientRect().bottom <= innerHeight;
            }"""
        )
        frames.nth(4).click(position={"x": 5, "y": 5})
        inspector = page.get_by_role("complementary", name="当前分屏属性")
        expect(inspector.get_by_role("heading")).to_have_text("连续长图第 5 屏")
        zoom.select_option("fit")
        first = frames.nth(0).locator('[data-text-layer="headline"]')
        first.dblclick()
        inline = page.get_by_label("直接编辑画布文字")
        inline.fill("第一屏直接修改")
        inline.press("End")
        inline.press("Enter")
        page.keyboard.insert_text("第二行，中文与 <b>纯文本</b>")
        expected_first = "第一屏直接修改\n第二行，中文与 <b>纯文本</b>"
        expect(inspector.get_by_label("文字内容", exact=True)).to_have_value(expected_first)
        inline.dispatch_event("keydown", {"key": "Escape", "isComposing": True})
        expect(inline).to_be_visible()
        inline.press("Control+Enter")
        expect(inline).to_have_count(0)
        second = frames.nth(1).locator('[data-text-layer="headline"]')
        second.dblclick()
        inline.fill("另一屏也保留草稿")
        inline.press("Tab")
        expect(inline).to_have_count(0)
        expect(first).to_have_text(expected_first)
        expect(second).to_have_text("另一屏也保留草稿")
        page.get_by_role("tab", name="含字参考稿", exact=True).click()
        page.get_by_role("tab", name="可编辑成图", exact=True).click()
        expect(first).to_have_text(expected_first)
        save = page.get_by_role("button", name="保存修改", exact=True)
        with page.expect_response(
            lambda response: (
                response.request.method == "PATCH" and response.url.endswith("/screens")
            )
        ) as saved:
            save.click()
        assert saved.value.status == 200
        assert set(saved.value.request.post_data_json["screens"]) == {"e0", "e1"}
        after = httpx.get(f"{origin}/api/projects/{pid}").json()
        assert after["version"] == before["version"] + 1
        assert after["screens"][2:] == before["screens"][2:]
        assert after["screens"][0]["layers"][0]["content"] == expected_first
        assert after["screens"][0]["a"] == before["screens"][0]["a"]
        assert after["screens"][0]["mask"] == "legacy-mask.png"
        expect(save).to_be_disabled()
        first.dblclick()
        inline.fill("Esc 只取消这次改字")
        inline.press("Escape")
        expect(first).to_have_text(expected_first)
        expect(save).to_be_disabled()
        first.scroll_into_view_if_needed()
        rect = first.bounding_box()
        artboard_width = frames.nth(0).locator("[data-artboard]").bounding_box()["width"]
        x, y = rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 24, y + 18, steps=5)
        page.mouse.up()
        expect(save).to_be_enabled()
        save.click()
        expect(save).to_be_disabled()
        after_drag = httpx.get(f"{origin}/api/projects/{pid}").json()
        assert after_drag["screens"][0]["layers"][0]["x"] == pytest.approx(
            0.1 + 24 / artboard_width, abs=0.005
        )
        zoom.select_option("overview")
        frames.nth(7).click(position={"x": 5, "y": 5})
        expect(inspector.get_by_role("heading")).to_have_text("连续长图第 8 屏")
        inspector.get_by_label("画布高度 / px", exact=True).fill("1440")
        save.click()
        expect(save).to_be_disabled()
        expect(zoom).to_have_value("overview")
        page.reload()
        expect(frames).to_have_count(8)
        expect(first).to_have_text(expected_first)
        expect(second).to_have_text("另一屏也保留草稿")
        current = httpx.get(f"{origin}/api/projects/{pid}").json()
        assert current["screens"][7]["height"] == 1440
        assert current["facts"] == before["facts"]
        assert httpx.get(f"{origin}/api/projects/{pid}/calls").json() == calls
        assert not errors
        browser.close()


def test_eight_screen_source_stages_and_legacy_assets(continuous_browser_project):
    origin, pid = continuous_browser_project
    project = httpx.get(f"{origin}/api/projects/{pid}").json()
    assert len({screen["concept"]["file"] for screen in project["screens"][:7]}) == 7
    assert not project["screens"][7]["concept"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.add_init_script(f"localStorage.setItem('studio-project', '{pid}')")
        page.goto(origin)
        first = page.locator('[data-screen-id="e0"]')
        background = first.locator(".board-background")
        expect(background).to_have_attribute(
            "src", re.compile(re.escape(project["screens"][0]["b"]["file"]) + r"\?preview=960$")
        )
        for title, field in (("含字参考稿", "concept"), ("无字底图", "b")):
            page.get_by_role("tab", name=title, exact=True).click()
            expect(background).to_have_attribute(
                "src", re.compile(re.escape(project["screens"][0][field]["file"]) + r"\?preview=960$")
            )
            expect(first.locator(".board-layer")).to_have_count(0)
            expect(page.locator('[data-screen-id="e7"] .board-layer')).to_have_count(2)
        inspector = page.get_by_role("complementary", name="当前分屏属性")
        expect(inspector.get_by_role("link", name="本屏参考原图")).to_have_attribute(
            "href", re.compile(re.escape(project["screens"][0]["concept"]["file"]) + "$")
        )
        assert page.get_by_text("事实来源绑定", exact=True).count() == 0
        assert page.get_by_role("button", name=re.compile(r"采用 A|生成 A|A／B|去字")).count() == 0
        page.get_by_role("tab", name="设计工作台", exact=True).click()
        expect(page.locator(".comparison-card")).to_have_count(3)
        expect(page.locator(".comparison-card").nth(0).locator(".board-layer")).to_have_count(0)
        expect(page.locator(".comparison-card").nth(1).locator(".board-layer")).to_have_count(0)
        expect(page.locator(".comparison-card").nth(2).locator(".board-layer")).to_have_count(2)
        page.goto(f"{origin}/render/{pid}/e0?version={project['version']}&mode=publish")
        expect(page.locator(".board-background")).to_have_attribute(
            "src", re.compile(re.escape(project["screens"][0]["b"]["file"]) + "$")
        )
        browser.close()
    package = httpx.get(f"{origin}/api/projects/{pid}/package", timeout=30)
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert "files/legacy-mask.png" in archive.namelist()
        assert "files/source.png" in archive.namelist()
    restored = httpx.post(
        origin + "/api/projects/import", files={"file": ("legacy.zip", package.content)}, timeout=30
    ).json()
    assert restored["screens"][0]["selected_route"] == "A"
    assert restored["screens"][0]["erase_prompt"] == "旧去字指令，仅保留历史。"
    assert restored["screens"][0]["reviews"]["A"]["notes"] == "旧路线评分"
    assert restored["prompt_overrides"]["e0:A_erase"] == "不应再执行的历史指令"


def test_continuous_editor_conflict_keeps_all_drafts_and_mobile_inspector(
    continuous_browser_project,
):
    origin, pid = continuous_browser_project
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(f"localStorage.setItem('studio-project', '{pid}')")
        page.goto(origin)
        frames = page.locator("[data-screen-id]")
        for index in (0, 1):
            frames.nth(index).locator('[data-text-layer="headline"]').dblclick()
            inline = page.get_by_label("直接编辑画布文字")
            inline.fill(f"冲突也不能丢失的第 {index + 1} 屏")
            inline.press("Control+Enter")
        page.once("dialog", lambda confirm: confirm.dismiss())
        page.get_by_role("tab", name="设计工作台", exact=True).click()
        expect(page.get_by_role("tab", name="画面编辑", exact=True)).to_have_attribute(
            "aria-selected", "true"
        )
        project = httpx.get(f"{origin}/api/projects/{pid}").json()
        last = project["screens"][-1]
        response = httpx.patch(
            f"{origin}/api/projects/{pid}/screens/{last['id']}",
            json={
                "version": project["version"],
                "layers": last["layers"],
                "height": 1400,
                "selected_route": None,
                "approved": False,
                "reviews": last["reviews"],
            },
        )
        assert response.status_code == 200, response.text
        page.get_by_role("button", name="保存修改", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("项目已被其他操作更新")
        for index in (0, 1):
            expect(frames.nth(index).locator('[data-text-layer="headline"]')).to_have_text(
                f"冲突也不能丢失的第 {index + 1} 屏"
            )
        assert (
            httpx.get(f"{origin}/api/projects/{pid}").json()["screens"][0]["layers"][0]["content"]
            == project["screens"][0]["layers"][0]["content"]
        )
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.get_by_role("button", name="本屏属性", exact=True)).to_be_visible()
        page.get_by_role("button", name="本屏属性", exact=True).click()
        inspector = page.get_by_role("complementary", name="当前分屏属性")
        expect(inspector).to_be_visible()
        expect(inspector.get_by_label("文字内容", exact=True)).to_have_value(
            "冲突也不能丢失的第 2 屏"
        )
        page.get_by_role("button", name="关闭分屏属性").click()
        expect(inspector).not_to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.get_by_label("长图缩放").select_option("overview")
        page.wait_for_function(
            """() => {
                const screens = [...document.querySelectorAll('[data-screen-id]')];
                return screens[0].getBoundingClientRect().top >= document.querySelector('.longform-controls').getBoundingClientRect().bottom
                    && screens[7].getBoundingClientRect().bottom <= innerHeight;
            }"""
        )
        assert not errors
        browser.close()
