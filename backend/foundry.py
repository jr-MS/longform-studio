from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlparse

import httpx
from PIL import Image
from pydantic import BaseModel, ValidationError

from .errors import AppError
from .models import DeploymentRequest, FoundrySettings, ModelCall, now
from .prompts import structured_system_prompt
from .storage import Store, uid

T = TypeVar("T", bound=BaseModel)
IMAGE_MODELS = ("gpt-image-2.5-sunburst", "gpt-image-2.5-flare")


def _pace_endpoint(endpoint: str) -> bool:
    host = urlparse(endpoint).hostname or ""
    return not host.startswith("test.") and host.endswith((
        ".cognitiveservices.azure.com", ".openai.azure.com", ".services.ai.azure.com",
    ))


class ImageNodePool:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cursor = 0
        self._recent: dict[str, list[float]] = {}

    def order(self, nodes: list[tuple[str, str]]) -> list[tuple[str, str]]:
        if len(nodes) < 2:
            return list(nodes)
        with self._lock:
            start = self._cursor % len(nodes)
            self._cursor += 1
        return nodes[start:] + nodes[:start]

    def acquire(self, endpoint: str, rpm: int = 2) -> None:
        if not _pace_endpoint(endpoint):
            return
        while True:
            with self._lock:
                now = time.monotonic()
                recent = [stamp for stamp in self._recent.get(endpoint, []) if now - stamp < 60]
                if len(recent) < rpm:
                    recent.append(now)
                    self._recent[endpoint] = recent
                    return
                wait = 60 - (now - recent[0]) + 0.3
            time.sleep(min(max(wait, 0.3), 61))


IMAGE_POOL = ImageNodePool()


def _image_status(error: AppError) -> int | None:
    text = str(error)
    for code in (429, 401, 403, 404, 408, 500, 502, 503):
        if f"HTTP {code}" in text:
            return code
    return None


def az_json(args: list[str], subscription: str = ""):
    command = ["az", *args, "--output", "json", "--only-show-errors"]
    if subscription:
        command += ["--subscription", subscription]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except FileNotFoundError as e:
        raise AppError("找不到 Azure CLI，请安装后运行 az login。", 503) from e
    except subprocess.TimeoutExpired as e:
        raise AppError("Azure CLI 请求超时，请检查网络与登录状态。", 504) from e
    if result.returncode:
        raise AppError(f"Azure CLI 失败：{result.stderr.strip()[:1200]}", 502)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise AppError("Azure CLI 未返回有效 JSON", 502) from e


def discover(subscription: str = "") -> dict:
    account = az_json(["account", "show"], subscription)
    resources = az_json(["cognitiveservices", "account", "list"], account["id"])
    result = []
    for resource in resources:
        if resource.get("kind") not in ("AIServices", "OpenAI"):
            continue
        deployments = az_json(
            [
                "cognitiveservices",
                "account",
                "deployment",
                "list",
                "-g",
                resource["resourceGroup"],
                "-n",
                resource["name"],
            ],
            account["id"],
        )
        result.append(
            {
                "name": resource["name"],
                "group": resource["resourceGroup"],
                "location": resource["location"],
                "endpoint": resource.get("properties", {}).get("endpoint", ""),
                "deployments": [
                    {
                        "name": d["name"],
                        "model": d["properties"]["model"]["name"],
                        "version": d["properties"]["model"].get("version", ""),
                        "state": d["properties"].get("provisioningState", ""),
                    }
                    for d in deployments
                ],
            }
        )
    return {
        "subscription": account["id"],
        "subscription_name": account["name"],
        "tenant": account["tenantId"],
        "resources": result,
    }


def deployment_options(subscription: str, resource: str) -> dict:
    accounts = az_json(["cognitiveservices", "account", "list"], subscription)
    account = next((a for a in accounts if a["name"] == resource), None)
    if not account:
        raise AppError("所选订阅中不存在该资源", 404)
    models = az_json(
        ["cognitiveservices", "model", "list", "--location", account["location"]],
        subscription,
    )
    usage = az_json(
        ["cognitiveservices", "usage", "list", "--location", account["location"]],
        subscription,
    )
    options = []
    for item in models:
        model = item.get("model", item)
        name = model.get("name", "")
        if name not in IMAGE_MODELS:
            continue
        for sku in model.get("skus", item.get("skus", [])):
            sku_name = sku["name"]
            matching = [
                u
                for u in usage
                if name.lower() in u["name"]["value"].lower()
                and sku_name.lower() in u["name"]["value"].lower()
            ]
            if not matching:
                continue
            available = min(float(u["limit"]) - float(u["currentValue"]) for u in matching)
            minimum = sku.get("capacity", {}).get("minimum", 1)
            if available >= minimum:
                options.append(
                    {
                        "model": name,
                        "version": model["version"],
                        "sku": sku_name,
                        "capacity": int(minimum),
                        "available": available,
                    }
                )
    return {
        "subscription": subscription,
        "resource": account["name"],
        "group": account["resourceGroup"],
        "location": account["location"],
        "endpoint": account["properties"]["endpoint"],
        "options": options,
        "notice": "仅列出目录支持且有可识别剩余额度的配置。调用按 Azure 账单计费。",
    }


def create_deployment(request: DeploymentRequest) -> dict:
    catalog = deployment_options(request.subscription, request.resource)
    if catalog["group"] != request.resource_group:
        raise AppError("资源组与实际资源不匹配")
    option = next(
        (
            o
            for o in catalog["options"]
            if o["model"] == request.model
            and o["version"] == request.version
            and o["sku"] == request.sku
            and o["capacity"] <= request.capacity <= o["available"]
        ),
        None,
    )
    if not option:
        raise AppError("配置不在实时可用目录或剩余额度中，请重新检查。", 409)
    existing = az_json(
        [
            "cognitiveservices",
            "account",
            "deployment",
            "list",
            "-g",
            request.resource_group,
            "-n",
            request.resource,
        ],
        request.subscription,
    )
    if any(d["name"] == request.deployment for d in existing):
        raise AppError("同名部署已存在，未覆盖，请选择复用或更换名称。", 409)
    result = az_json(
        [
            "cognitiveservices",
            "account",
            "deployment",
            "create",
            "-g",
            request.resource_group,
            "-n",
            request.resource,
            "--deployment-name",
            request.deployment,
            "--model-format",
            "OpenAI",
            "--model-name",
            request.model,
            "--model-version",
            request.version,
            "--sku-name",
            request.sku,
            "--sku-capacity",
            str(request.capacity),
        ],
        request.subscription,
    )
    return {
        "name": result["name"],
        "state": result["properties"].get("provisioningState"),
        "endpoint": catalog["endpoint"],
    }


class Foundry:
    def __init__(self, store: Store, settings: FoundrySettings):
        self.store = store
        self.settings = settings
        self._verified: set[str] = set()

    def verify(self, purpose: str) -> None:
        if purpose in self._verified:
            return
        endpoint = (
            self.settings.planning_endpoint
            if purpose == "planning"
            else self.settings.image_endpoint
        )
        deployment = (
            self.settings.planning_deployment
            if purpose == "planning"
            else self.settings.image_deployment
        )
        expected = (
            self.settings.planning_model if purpose == "planning" else self.settings.image_model
        )
        if not endpoint or not deployment:
            raise AppError("请先在模型设置中选择并保存 Foundry 端点与部署。", 409)
        if purpose == "planning":
            self._verify_deployment(endpoint, deployment, expected, self.settings.subscription)
        else:
            for node_endpoint, node_deployment in self._image_nodes():
                self._verify_deployment(
                    node_endpoint, node_deployment, expected,
                    self._node_subscription(node_endpoint),
                )
        self._verified.add(purpose)

    def _verify_deployment(
        self, endpoint: str, deployment: str, expected: str, subscription: str,
    ) -> None:
        resources = az_json(["cognitiveservices", "account", "list"], subscription)
        resource = next(
            (
                a
                for a in resources
                if endpoint.rstrip("/").lower()
                in {
                    value.rstrip("/").lower()
                    for value in [
                        a.get("properties", {}).get("endpoint", ""),
                        *a.get("properties", {}).get("endpoints", {}).values(),
                    ]
                    if isinstance(value, str)
                }
            ),
            None,
        )
        if not resource:
            raise AppError("当前订阅中未找到配置的资源端点，请检查订阅与资源。", 409)
        deployments = az_json(
            [
                "cognitiveservices",
                "account",
                "deployment",
                "list",
                "-g",
                resource["resourceGroup"],
                "-n",
                resource["name"],
            ],
            subscription,
        )
        match = next((d for d in deployments if d["name"] == deployment), None)
        if (
            not match
            or match["properties"]["model"]["name"] != expected
            or match["properties"].get("provisioningState") != "Succeeded"
        ):
            raise AppError(f"部署尚未就绪或不是要求的模型 {expected}；未替换为其他模型。", 409)

    def token(self, subscription: str | None = None) -> str:
        selected_subscription = self.settings.subscription if subscription is None else subscription
        args = [
            "account",
            "get-access-token",
            "--resource",
            "https://cognitiveservices.azure.com",
        ]
        if self.settings.tenant and not selected_subscription:
            args += ["--tenant", self.settings.tenant]
        result = az_json(args, selected_subscription)
        if self.settings.tenant and result.get("tenant") != self.settings.tenant:
            raise AppError("Azure CLI 令牌所属目录与配置不符，请检查订阅和租户。", 409)
        return result["accessToken"]

    def _call(
        self,
        project_id: str,
        screen_id: str | None,
        purpose: str,
        model: str,
        deployment: str,
        endpoint: str,
        prompt: str,
        request: dict,
    ) -> ModelCall:
        call = ModelCall(
            id=uid(),
            project_id=project_id,
            screen_id=screen_id,
            purpose=purpose,
            model=model,
            deployment=deployment,
            endpoint=endpoint,
            prompt=prompt,
            request=request,
            status="running",
        )
        self.store.save_call(call)
        return call

    @staticmethod
    def image_data(path: Path) -> str:
        with Image.open(path) as raw:
            image = raw.convert("RGB")
            image.thumbnail((1440, 1800))
            output = io.BytesIO()
            image.save(output, "JPEG", quality=90)
        return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()

    @staticmethod
    def response_text(result: dict) -> str:
        if result.get("status") != "completed" or result.get("error"):
            reason = result.get("error") or result.get("incomplete_details") or result.get("status")
            raise AppError(f"Responses 规划模型未完整返回：{reason}。请检查输出长度或内容策略。", 502)
        output = result.get("output")
        if not isinstance(output, list):
            raise AppError("Responses 未返回有效 output 数组", 502)
        text = []
        for item in output:
            if not isinstance(item, dict):
                raise AppError("Responses 返回了无效输出项", 502)
            if item.get("type") != "message":
                continue
            if item.get("role") != "assistant" or item.get("status") != "completed":
                raise AppError("Responses 返回的消息未完成或角色无效", 502)
            content = item.get("content")
            if not isinstance(content, list):
                raise AppError("Responses 返回了无效消息内容", 502)
            for part in content:
                if not isinstance(part, dict):
                    raise AppError("Responses 返回了无效消息片段", 502)
                if part.get("type") == "refusal":
                    raise AppError(f"Responses 拒绝生成：{part.get('refusal', '内容策略限制')}", 502)
                if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                    raise AppError("Responses 返回了非文本消息片段", 502)
                text.append(part["text"])
        if not text or not "".join(text).strip():
            raise AppError("Responses 未返回结构化文本，未使用占位或旧 API 回退", 502)
        return "".join(text)

    def structured(
        self,
        project_id: str,
        purpose: str,
        prompt: str,
        schema: type[T],
        images: list[Path] | None = None,
        screen_id: str | None = None,
    ) -> T:
        self.verify("planning")
        s = self.settings
        system = structured_system_prompt(schema)
        content = [{"type": "input_text", "text": prompt}]
        for image in images or []:
            content.append({"type": "input_image", "image_url": self.image_data(image)})
        # Dynamic maps in application schemas require local Pydantic validation.
        text_format = {
            "type": "json_schema",
            "name": schema.__name__,
            "schema": schema.model_json_schema(),
            "strict": False,
        }
        correction = ""
        for attempt in range(2):
            payload = {
                "model": s.planning_deployment,
                "instructions": system,
                "input": [
                    {"role": "user", "content": content},
                    *([{"role": "user", "content": correction}] if correction else []),
                ],
                "text": {"format": text_format},
                "max_output_tokens": 16000,
                "store": False,
            }
            call = self._call(
                project_id,
                screen_id,
                purpose,
                s.planning_model,
                s.planning_deployment,
                s.planning_endpoint,
                prompt,
                {
                    "schema": schema.__name__,
                    "api": "responses",
                    "path": "/openai/v1/responses",
                    "image_count": len(images or []),
                    "attempt": attempt + 1,
                    "system_prompt": system,
                    "correction_prompt": correction,
                    "references": [image.name for image in images or []],
                    "reference_sha256": [
                        hashlib.sha256(image.read_bytes()).hexdigest() for image in images or []
                    ],
                    "image_encoding": "JPEG quality=90, thumbnail<=1440x1800",
                    "max_output_tokens": 16000,
                    "text": {"format": text_format},
                    "store": False,
                },
            )
            try:
                with httpx.Client(timeout=httpx.Timeout(360, connect=30)) as client:
                    response = client.post(
                        s.planning_endpoint.rstrip("/") + "/openai/v1/responses",
                        headers={"Authorization": "Bearer " + self.token()},
                        json=payload,
                    )
                    self._check(response)
                    result = response.json()
                if not isinstance(result, dict):
                    raise AppError("Responses 未返回有效 JSON 对象", 502)
                call.usage = result.get("usage", {})
                call.response = result
                call.completed_at = now()
                output_text = self.response_text(result)
                try:
                    parsed = schema.model_validate_json(output_text)
                except (ValidationError, TypeError) as e:
                    call.status = "failed"
                    call.error = f"结构验证未通过：{str(e)[:1500]}"
                    self.store.save_call(call)
                    if attempt == 0:
                        correction = (
                            "上一次结构验证失败，请重新生成完整对象。错误：" + str(e)[:3000]
                        )
                        continue
                    raise AppError(call.error, 502) from e
                call.status = "succeeded"
                self.store.save_call(call)
                return parsed
            except (httpx.HTTPError, AppError, ValueError, KeyError) as e:
                call.status = "failed"
                call.error = str(e)[:1800]
                call.completed_at = now()
                self.store.save_call(call)
                if isinstance(e, AppError):
                    raise
                raise AppError(f"Foundry 规划调用失败：{call.error}", 502) from e
        raise AppError("规划模型结构验证失败", 502)

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if response.is_error:
            try:
                body = response.json()
                error = body.get("error", {})
                message = (
                    error.get("message", str(error)) if isinstance(error, dict) else str(error)
                )
            except ValueError:
                message = response.text[:800]
            raise AppError(f"Foundry HTTP {response.status_code}：{message[:1200]}", 502)

    def _image_nodes(self) -> list[tuple[str, str]]:
        settings = self.settings
        return [
            (settings.image_endpoint.rstrip("/"), settings.image_deployment),
            *((node.endpoint.rstrip("/"), node.deployment) for node in settings.image_nodes),
        ]

    def _node_subscription(self, endpoint: str) -> str:
        node = next(
            (node for node in self.settings.image_nodes
             if node.endpoint.rstrip("/").lower() == endpoint.rstrip("/").lower()),
            None,
        )
        return (node.subscription if node else "") or self.settings.subscription

    def _node_rpm(self, endpoint: str) -> int:
        node = next(
            (node for node in self.settings.image_nodes
             if node.endpoint.rstrip("/").lower() == endpoint.rstrip("/").lower()),
            None,
        )
        return node.requests_per_minute if node else self.settings.image_requests_per_minute

    def image(
        self,
        project_id: str,
        screen_id: str,
        purpose: str,
        prompt: str,
        size: tuple[int, int],
        references: list[Path],
    ) -> tuple[Path, ModelCall]:
        self.verify("image")
        nodes = self._image_nodes()
        last_error: AppError | None = None
        rounds = 3 if any(_pace_endpoint(endpoint) for endpoint, _ in nodes) else 1
        for round_index in range(rounds):
            saw_429 = False
            for endpoint, deployment in IMAGE_POOL.order(nodes):
                IMAGE_POOL.acquire(endpoint, self._node_rpm(endpoint))
                try:
                    return self._image_once(
                        project_id, screen_id, purpose, prompt, size, references, endpoint, deployment
                    )
                except AppError as error:
                    last_error = error
                    status = _image_status(error)
                    if status == 429:
                        saw_429 = True
                        continue
                    if len(nodes) > 1 and (
                        status in (401, 403, 404, 408, 500, 502, 503) or "生图调用失败" in str(error)
                    ):
                        continue
                    raise
            if not saw_429 or round_index == rounds - 1:
                break
            time.sleep(min(15 * (round_index + 1), 30))
        if last_error:
            raise last_error
        raise AppError("没有可用的生图节点", 502)

    def _image_once(
        self,
        project_id: str,
        screen_id: str,
        purpose: str,
        prompt: str,
        size: tuple[int, int],
        references: list[Path],
        endpoint: str,
        deployment: str,
    ) -> tuple[Path, ModelCall]:
        settings = self.settings
        fields = {
            "prompt": prompt,
            "size": f"{size[0]}x{size[1]}",
            "quality": settings.quality,
            "n": "1",
            "output_format": "png",
        }
        operation = "edits" if references else "generations"
        call = self._call(
            project_id,
            screen_id,
            purpose,
            settings.image_model,
            deployment,
            endpoint,
            prompt,
            {
                **fields, "references": [p.name for p in references], "node": endpoint,
                "reference_sha256": [hashlib.sha256(p.read_bytes()).hexdigest() for p in references],
                "operation": operation,
                "api_version": "2025-04-01-preview",
            },
        )
        url = endpoint.rstrip("/") + f"/openai/deployments/{deployment}/images/{operation}"
        try:
            with httpx.Client(timeout=httpx.Timeout(600, connect=30)) as client:
                kwargs: dict = {}
                if references:
                    files = [
                        (
                            "image" if len(references) == 1 else "image[]",
                            (p.name, p.read_bytes(), "image/png"),
                        )
                        for p in references
                    ]
                    kwargs = {"data": fields, "files": files}
                else:
                    kwargs = {"json": {**fields, "n": 1}}
                response = client.post(
                    url,
                    params={"api-version": "2025-04-01-preview"},
                    headers={"Authorization": "Bearer " + (
                        self.token()
                        if self._node_subscription(endpoint) == self.settings.subscription
                        else self.token(self._node_subscription(endpoint))
                    )},
                    **kwargs,
                )
                self._check(response)
                result = response.json()
            call.usage = result.get("usage", {})
            data = result.get("data", [])
            if not data or not data[0].get("b64_json"):
                raise AppError("生图服务没有返回图片，可能被内容策略拦截；未生成占位图。", 502)
            raw = base64.b64decode(data[0]["b64_json"], validate=True)
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 8_294_400:
                    raise AppError("模型返回图片超过允许像素上限", 502)
                path = self.store.file(project_id, uid() + ".png")
                image.convert("RGB").save(path)
            call.status = "succeeded"
            call.completed_at = now()
            call.response = {
                **{key: value for key, value in result.items() if key != "data"},
                "data": [
                    {key: value for key, value in item.items() if key not in ("b64_json", "url")}
                    for item in data
                ],
                "output_file": path.name,
                "output_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "width": image.width,
                "height": image.height,
            }
            self.store.save_call(call)
            return path, call
        except (httpx.HTTPError, AppError, ValueError, KeyError, OSError) as e:
            call.status = "failed"
            call.error = str(e)[:1800]
            call.completed_at = now()
            self.store.save_call(call)
            if isinstance(e, AppError):
                raise
            raise AppError(f"Foundry 生图调用失败：{call.error}", 502) from e
