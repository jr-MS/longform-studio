import base64
import io
import json

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from backend import foundry
from backend.errors import AppError
from backend.foundry import Foundry, create_deployment
from backend.models import DeploymentRequest, FoundrySettings
from backend.pipeline import Transcript
from backend.storage import Store
from backend.tests.fixtures import fixture_project


@pytest.fixture
def configured(tmp_path, monkeypatch):
    store = Store(tmp_path)
    project = fixture_project(store)
    settings = FoundrySettings(
        planning_endpoint="https://test.cognitiveservices.azure.com",
        planning_deployment="design-deployment",
        image_endpoint="https://test.cognitiveservices.azure.com",
        image_deployment="image-deployment",
    )
    provider = Foundry(store, settings)
    monkeypatch.setattr(provider, "verify", lambda purpose: None)
    monkeypatch.setattr(provider, "token", lambda: "unit-test-token")
    return store, project, provider


def install_transport(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(
        foundry.httpx,
        "Client",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_structured_contract_and_bounded_validation_retry(configured, monkeypatch):
    store, project, provider = configured
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == "design-deployment"
        assert request.url.path == "/openai/v1/responses"
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["schema"]["properties"]["text"]["type"] == "string"
        assert body["max_output_tokens"] == 16000 and body["store"] is False
        assert body["input"][0]["content"][0]["type"] == "input_text"
        content = '{"wrong":1}' if len(requests) == 1 else '{"text":"正确内容"}'
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {"type": "reasoning", "summary": []},
                    {"type": "message", "role": "assistant", "status": "completed",
                     "content": [{"type": "output_text", "text": content}]},
                ],
                "usage": {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
            },
        )

    install_transport(monkeypatch, handle)
    result = provider.structured(project.id, "test", "测试结构化结果", Transcript)
    assert result.text == "正确内容" and len(requests) == 2
    calls = store.calls(project.id)
    assert sorted(c.status for c in calls) == ["failed", "succeeded"]
    assert all(c.model == "gpt-6.1-sol" for c in calls)
    assert all("unit-test-token" not in c.model_dump_json() for c in calls)
    for call in calls:
        request = requests[call.request["attempt"] - 1]
        assert call.request["api"] == "responses"
        assert call.request["system_prompt"] == request["instructions"]
        assert call.prompt == request["input"][0]["content"][0]["text"]
        assert call.request["correction_prompt"] == (
            request["input"][1]["content"] if len(request["input"]) > 1 else ""
        )
        assert call.completed_at and call.response["output"][1]["content"]
        assert call.response["usage"] == call.usage


def test_responses_multimodal_input_and_fragmented_text(configured, monkeypatch):
    store, project, provider = configured
    source = store.file(project.id, "source.png")

    def handle(request):
        body = json.loads(request.content)
        image = body["input"][0]["content"][1]
        assert image["type"] == "input_image"
        assert image["image_url"].startswith("data:image/jpeg;base64,")
        return httpx.Response(200, json={
            "status": "completed", "output": [
                {"type": "message", "role": "assistant", "status": "completed",
                 "content": [{"type": "output_text", "text": '{"text":'},
                             {"type": "output_text", "text": '"有图"}'}]},
            ], "usage": {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28},
        })

    install_transport(monkeypatch, handle)
    assert provider.structured(project.id, "test", "看图", Transcript, [source]).text == "有图"
    call = store.calls(project.id)[0]
    assert call.request["references"] == ["source.png"]
    assert len(call.request["reference_sha256"][0]) == 64
    assert "data:image" not in call.model_dump_json()


@pytest.mark.parametrize("result,match", [
    ({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, "未完整"),
    ({"status": "failed", "error": {"message": "policy"}}, "未完整"),
    ({"status": "completed", "output": []}, "未返回结构化"),
    ({"status": "completed", "output": [
        {"type": "message", "role": "assistant", "status": "completed",
         "content": [{"type": "refusal", "refusal": "不支持此请求"}]},
    ]}, "拒绝生成"),
])
def test_responses_failure_never_falls_back_or_retries(configured, monkeypatch, result, match):
    store, project, provider = configured
    install_transport(monkeypatch, lambda request: httpx.Response(200, json=result))
    with pytest.raises(AppError, match=match):
        provider.structured(project.id, "test", "测试", Transcript)
    calls = store.calls(project.id)
    assert len(calls) == 1 and calls[0].status == "failed"
    assert calls[0].response == result and calls[0].completed_at


def test_multipart_image_edit_references_and_usage(configured, monkeypatch):
    store, project, provider = configured
    png = io.BytesIO()
    Image.new("RGB", (800, 1000), "white").save(png, "PNG")

    def handle(request):
        assert request.url.params["api-version"] == "2025-04-01-preview"
        assert request.url.path.endswith("/image-deployment/images/edits")
        assert request.content.count(b'name="image[]"') == 2
        assert b'name="mask"' not in request.content
        return httpx.Response(
            200,
            json={
                "data": [{"b64_json": base64.b64encode(png.getvalue()).decode()}],
                "usage": {"output_tokens": 42},
            },
        )

    install_transport(monkeypatch, handle)
    source = store.file(project.id, "source.png")
    result, call = provider.image(
        project.id, "s0", "test_edit", "测试", (800, 1000), [source, source]
    )
    assert result.is_file() and call.usage["output_tokens"] == 42
    assert store.calls(project.id)[0].status == "succeeded"
    assert call.response["output_file"] == result.name
    assert call.response["width"] == 800
    assert "b64_json" not in json.dumps(call.response)


def test_image_429_fails_over_without_waiting(configured, monkeypatch):
    store, project, provider = configured
    png = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(png, "PNG")
    payload = base64.b64encode(png.getvalue()).decode()
    seen = []

    def handle(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/busy/images/generations"):
            return httpx.Response(429, json={"error": {"message": "rate limit"}})
        return httpx.Response(200, json={"data": [{"b64_json": payload}], "usage": {}})

    install_transport(monkeypatch, handle)
    foundry.IMAGE_POOL._cursor = 0
    monkeypatch.setattr(
        provider,
        "_image_nodes",
        lambda: [
            ("https://test.cognitiveservices.azure.com", "busy"),
            ("https://test.cognitiveservices.azure.com", "spare"),
        ],
    )
    _path, call = provider.image(project.id, "s0", "concept", "测试", (64, 64), [])
    assert call.status == "succeeded" and call.deployment == "spare"
    assert seen[0].endswith("/busy/images/generations")
    assert seen[1].endswith("/spare/images/generations")
    assert [item.status for item in store.calls(project.id)] == ["succeeded", "failed"]


def test_api_failure_is_visible_without_retries_or_fallback(configured, monkeypatch):
    store, project, provider = configured
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(429, json={"error": {"message": "quota exhausted"}}),
    )
    with pytest.raises(AppError, match="429"):
        provider.structured(project.id, "test", "测试", Transcript)
    assert len(store.calls(project.id)) == 1
    assert store.calls(project.id)[0].status == "failed"


def test_deployment_identity_must_match(configured, monkeypatch):
    store, _, _ = configured
    provider = Foundry(
        store,
        FoundrySettings(
            planning_endpoint="https://test.cognitiveservices.azure.com",
            planning_deployment="design",
        ),
    )

    def fake_az(args, subscription=""):
        if args[-1] == "list" and args[:2] == ["cognitiveservices", "account"]:
            return [
                {
                    "name": "test",
                    "resourceGroup": "group",
                    "properties": {"endpoint": "https://test.cognitiveservices.azure.com/"},
                }
            ]
        return [
            {
                "name": "design",
                "properties": {
                    "model": {"name": "different-model"},
                    "provisioningState": "Succeeded",
                },
            }
        ]

    monkeypatch.setattr(foundry, "az_json", fake_az)
    with pytest.raises(AppError, match="未替换"):
        provider.verify("planning")


def test_conditionally_create_only_a_catalog_validated_deployment(monkeypatch):
    commands = []
    monkeypatch.setattr(
        foundry,
        "deployment_options",
        lambda sub, name: {
            "group": "group",
            "endpoint": "https://test.cognitiveservices.azure.com/",
            "options": [
                {
                    "model": "gpt-image-2.5-flare",
                    "version": "2026-09-08",
                    "sku": "GlobalStandard",
                    "capacity": 1,
                    "available": 3,
                }
            ],
        },
    )

    def fake_az(args, subscription=""):
        commands.append(args)
        if "create" in args:
            return {"name": "flare", "properties": {"provisioningState": "Succeeded"}}
        return []

    monkeypatch.setattr(foundry, "az_json", fake_az)
    body = DeploymentRequest(
        subscription="test-sub",
        resource_group="group",
        resource="test",
        model="gpt-image-2.5-flare",
        version="2026-09-08",
        deployment="flare",
        sku="GlobalStandard",
        capacity=1,
        confirmed=True,
    )
    assert create_deployment(body)["state"] == "Succeeded"
    assert len(commands) == 2 and "create" in commands[1]
    with pytest.raises(AppError, match="实时可用"):
        create_deployment(body.model_copy(update={"capacity": 99}))


def test_token_uses_subscription_without_conflicting_tenant_flag(tmp_path, monkeypatch):
    seen = []

    def fake_az(args, subscription=""):
        seen.append((args, subscription))
        return {"accessToken": "test-token", "tenant": "expected-tenant"}

    monkeypatch.setattr(foundry, "az_json", fake_az)
    provider = Foundry(
        Store(tmp_path),
        FoundrySettings(subscription="selected-sub", tenant="expected-tenant"),
    )
    assert provider.token() == "test-token"
    assert seen[0][1] == "selected-sub" and "--tenant" not in seen[0][0]
    provider.settings.subscription = ""
    provider.token()
    assert "--tenant" in seen[1][0] and seen[1][1] == ""
    provider.settings.tenant = "different-tenant"
    with pytest.raises(AppError, match="所属目录"):
        provider.token()


def test_image_nodes_are_explicit_and_verified_in_their_own_subscription(tmp_path, monkeypatch):
    settings = FoundrySettings(
        subscription="primary-sub",
        image_endpoint="https://primary.openai.azure.com",
        image_deployment="main-image",
        image_nodes=[{
            "endpoint": "https://spare.openai.azure.com", "deployment": "spare-image",
            "subscription": "spare-sub", "requests_per_minute": 5,
        }],
    )
    provider = Foundry(Store(tmp_path), settings)
    checked = []
    monkeypatch.setattr(
        provider, "_verify_deployment",
        lambda endpoint, deployment, model, sub: checked.append((endpoint, deployment, model, sub)),
    )
    provider.verify("image")
    assert checked == [
        ("https://primary.openai.azure.com", "main-image", settings.image_model, "primary-sub"),
        ("https://spare.openai.azure.com", "spare-image", settings.image_model, "spare-sub"),
    ]
    assert provider._node_rpm("https://spare.openai.azure.com") == 5
    assert provider._node_subscription("https://spare.openai.azure.com/") == "spare-sub"
    settings.image_nodes = []
    assert provider._image_nodes() == [("https://primary.openai.azure.com", "main-image")]


def test_spare_node_image_uses_spare_subscription_for_auth(configured, monkeypatch):
    store, project, provider = configured
    provider.settings.image_nodes = FoundrySettings(image_nodes=[{
        "endpoint": "https://spare.openai.azure.com", "deployment": "spare-image",
        "subscription": "spare-sub",
    }]).image_nodes
    subscriptions = []
    monkeypatch.setattr(provider, "token", lambda sub=None: subscriptions.append(sub) or "test-token")
    png = io.BytesIO()
    Image.new("RGB", (64, 64)).save(png, "PNG")
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={
        "data": [{"b64_json": base64.b64encode(png.getvalue()).decode()}],
    }))
    provider._image_once(
        project.id, "s0", "concept", "测试", (64, 64), [],
        "https://spare.openai.azure.com", "spare-image",
    )
    assert subscriptions == ["spare-sub"]


def test_reject_duplicate_and_non_azure_image_nodes():
    with pytest.raises(ValidationError, match="重复"):
        FoundrySettings(
            image_endpoint="https://primary.openai.azure.com",
            image_nodes=[{"endpoint": "https://PRIMARY.openai.azure.com/", "deployment": "spare"}],
        )
    with pytest.raises(ValidationError, match="HTTPS 根端点"):
        FoundrySettings(image_nodes=[{"endpoint": "https://api.openai.com", "deployment": "spare"}])
