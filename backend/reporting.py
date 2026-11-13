from __future__ import annotations

import base64
import io
import json
from collections import Counter, defaultdict
from datetime import datetime
from html import escape
from urllib.parse import urlparse

from PIL import Image

from .models import ModelCall, Project, now
from .prompts import TITLES
from .storage import Store


def _json(value) -> str:
    return "<pre>" + escape(json.dumps(value, ensure_ascii=False, indent=2)) + "</pre>"


def tokens(call: ModelCall) -> dict:
    usage = call.usage
    image = call.model.startswith("gpt-image")
    responses = image or call.request.get("api") == "responses" or "input_tokens" in usage
    incoming = usage.get("input_tokens" if responses else "prompt_tokens")
    outgoing = usage.get("output_tokens" if responses else "completion_tokens")
    details = usage.get("input_tokens_details" if responses else "prompt_tokens_details", {})
    return {
        "input": incoming,
        "output": outgoing,
        "cached": details.get("cached_tokens", 0),
        "image_input": details.get("image_tokens") if image else None,
        "text_input": details.get("text_tokens") if image else None,
        "total": usage.get("total_tokens", incoming + outgoing if incoming is not None and outgoing is not None else None),
    }


def call_cost(call: ModelCall, evidence: dict) -> tuple[float | None, str]:
    t = tokens(call)
    if t["input"] is None or t["output"] is None:
        return None, "缺少 usage，不能认定免费"
    model_rates = evidence.get("rates", {}).get(call.model, {})
    resource = (urlparse(call.endpoint).hostname or "").split(".")[0]
    rates = model_rates.get(resource)
    source = "已核实本节点账单价率折算；非逐请求账单"
    if not rates and model_rates:
        rates = next(iter(model_rates.values()))
        source = "参考同模型另一节点价率估算；本节点价格未单独核实"
    if not rates:
        return None, "没有可核实的模型价率"
    if call.model.startswith("gpt-image"):
        if t["image_input"] is None or t["text_input"] is None:
            return None, "图像／文本输入 token 拆分缺失"
        total = t["text_input"] * rates["text_input"] + t["image_input"] * rates["image_input"] + t["output"] * rates["output"]
    else:
        total = (t["input"] - t["cached"]) * rates["input"] + t["cached"] * rates["cached"] + t["output"] * rates["output"]
    return total / 1_000_000, source


def _number(value) -> str:
    return f"{value:,}" if value is not None else "未返回"


def _seconds(start: str, end: str | None) -> str:
    if not end:
        return "未保存完成时间"
    return f"{(datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds():.1f} s"


def _image(store: Store, project: Project, filename: str) -> str:
    path = store.file(project.id, filename)
    if not path.is_file():
        return "<p>历史图片文件未留存</p>"
    with Image.open(path) as image:
        image.thumbnail((360, 700))
        output = io.BytesIO()
        image.convert("RGB").save(output, "WEBP", quality=78)
    return '<img loading="lazy" alt="实际模型输出图片预览" src="data:image/webp;base64,' + base64.b64encode(output.getvalue()).decode() + '">'


def workflow_report(store: Store, project_id: str) -> str:
    project = store.get(project_id)
    calls = sorted(store.calls(project_id), key=lambda call: call.created_at)
    pricing_file = store.root / "model-pricing-evidence.json"
    evidence = json.loads(pricing_file.read_text()) if pricing_file.is_file() else {}
    with store.connect() as c:
        versions = [
            Project.model_validate_json(row["body"])
            for row in c.execute("SELECT body FROM revisions WHERE project_id=? ORDER BY version", (project_id,))
        ] + [project]
    artifacts: dict[str, set[str]] = defaultdict(set)
    for version in versions:
        for screen in version.screens:
            for variant in (screen.concept, screen.a, screen.b):
                if variant:
                    artifacts[variant.call_id].add(variant.file)
    count = Counter(call.purpose for call in calls)
    totals = {key: sum(tokens(call)[key] or 0 for call in calls) for key in ("input", "output", "cached", "total")}
    missing_usage = sum(tokens(call)["total"] is None for call in calls)
    costs = [call_cost(call, evidence) for call in calls]
    cost_sum = sum(amount for amount, _ in costs if amount is not None)
    cost_headline = f"${cost_sum:.4f}" if any(amount is not None for amount, _ in costs) else "金额未知"
    missing_cost = sum(amount is None for amount, _ in costs)
    cross_node = sum(note.startswith("参考") for _, note in costs)
    purposes = "".join(
        f"<tr><td>{escape(TITLES.get(purpose, purpose))}</td><td>{amount}</td>"
        f"<td>{'历史已移除路径' if purpose.startswith(('audit_', 'A_', 'locate_')) else '当前支持步骤'}</td></tr>"
        for purpose, amount in count.items()
    )
    screen_count = sum(screen.kind != "parameters" for screen in project.screens)
    screen_table = "".join(
        f"<tr><td>{i + 1:02d}</td><td>{escape(screen.title)}</td><td>{screen.height}px</td>"
        f"<td>{'工程参数表，0 次图像调用' if screen.kind == 'parameters' else '参考稿 → 提取排版 → 无字底图，3 次'}</td></tr>"
        for i, screen in enumerate(project.screens)
    )
    details = []
    for index, (call, (amount, note)) in enumerate(zip(calls, costs), 1):
        usage = tokens(call)
        amount_text = f"${amount:.6f}" if amount is not None else "金额未知"
        output = _json(call.response) if call.response else (
            '<p class="notice">这次历史调用没有保存原始响应正文，无法补造。下面只展示可追溯的真实生成文件及调用后的应用状态；不将后续状态冒充原始回复。</p>'
        )
        for filename in sorted(artifacts.get(call.id, set())):
            output += "<figure>" + _image(store, project, filename) + f"<figcaption>{escape(filename)} · 与 call_id 精确关联</figcaption></figure>"
        if not call.response and call.status == "succeeded" and call.purpose in ("design_plan", "extract_facts", "extract_layout", "fit_layout_B"):
            version = next((v for v in versions if v.updated_at > call.created_at), None)
            if version:
                state = (
                    {"facts": [f.model_dump() for f in version.facts]} if call.purpose == "extract_facts"
                    else {"art_direction": version.art_direction.model_dump() if version.art_direction else None,
                          "screens": [s.model_dump() for s in version.screens if call.screen_id is None or s.id == call.screen_id]}
                )
                output += f"<details><summary>调用后保存的应用状态 v{version.version}（非原始模型回复）</summary>{_json(state)}</details>"
        refs = call.request.get("references")
        input_notice = "" if refs is not None else '<p class="notice">历史记录只保存了 image_count，未保存输入图片文件名或指纹；不推测具体图片。</p>'
        title = TITLES.get(call.purpose, call.purpose)
        details.append(
            f'<details class="call"><summary><span class="index">{index:02d}</span><b>{escape(title)}</b>'
            f'<span>{escape(call.model)}</span><em>{escape(call.status)}</em><span>{amount_text}</span></summary>'
            f'<div class="call-body"><p class="meta">ID {escape(call.id)} · 屏 {escape(call.screen_id or "项目级")}<br>'
            f'{escape(call.created_at)} · 实际耗时 {_seconds(call.created_at, call.completed_at)}<br>'
            f'部署 {escape(call.deployment)} · {escape(call.endpoint)}</p>'
            f'<div class="metrics"><span>输入 {_number(usage["input"])}</span><span>输出 {_number(usage["output"])}</span>'
            f'<span>缓存 {_number(usage["cached"])}</span><span>{amount_text}</span></div><p>{escape(note)}</p>'
            f'<details><summary>真实输入 · 完整 prompt／系统指令／参数</summary>{input_notice}'
            f'<h4>实际发送的用户 prompt</h4><pre>{escape(call.prompt)}</pre><h4>记录的请求元数据</h4>{_json(call.request)}</details>'
            f'<details><summary>真实输出与落盘结果</summary>{output}</details>'
            f'<details><summary>原始 usage（含 image／text、reasoning 明细）</summary>{_json(call.usage)}</details>'
            + (f'<p class="notice">{escape(call.error)}</p>' if call.error else "")
            + "</div></details>"
        )
    jobs = store.jobs(project_id)
    model_jobs = [
        job for job in jobs
        if any(job.created_at <= call.created_at <= job.updated_at for call in calls)
    ]
    active_seconds = sum(
        (datetime.fromisoformat(job.updated_at) - datetime.fromisoformat(job.created_at)).total_seconds()
        for job in model_jobs
    )
    elapsed = _seconds(
        min(job.created_at for job in model_jobs), max(job.updated_at for job in model_jobs),
    ) if model_jobs else "历史任务不足，未知"
    timing = f"包含本样本调用的任务累计运行 <b>{active_seconds:.1f} s</b>；首个相关任务开始到最后一个相关任务结束为 <b>{elapsed}</b>（包括中间等待确认／操作的时间，不包含之后重复导出）。"
    job_rows = "".join(
        f"<tr><td>{escape(job.kind)}</td><td>{escape(job.status)}</td><td>{escape(job.created_at)}</td>"
        f"<td>{_seconds(job.created_at, job.updated_at)}</td><td>{escape(job.error or job.result or '')}</td></tr>"
        for job in reversed(jobs)
    )
    all_projects = []
    for item in store.summaries():
        other_calls = store.calls(item.id)
        other_costs = [call_cost(call, evidence)[0] for call in other_calls]
        all_projects.append(
            f"<tr><td>{escape(item.name)}</td><td>{item.screen_count}</td><td>{len(other_calls)}</td>"
            f"<td>{sum(tokens(call)['total'] or 0 for call in other_calls):,}</td>"
            f"<td>${sum(x for x in other_costs if x is not None):.6f}（未知 {sum(x is None for x in other_costs)} 笔）</td></tr>"
        )
    rates_rows = ""
    for model, resources in evidence.get("rates", {}).items():
        for resource, rates in resources.items():
            for category, rate in rates.items():
                rates_rows += f"<tr><td>{escape(model)}</td><td>{escape(resource)}</td><td>{escape(category)}</td><td>${rate:g} / 1M tokens</td></tr>"
    billing_rows = "".join(
        f"<tr><td>{escape(str(row['UsageDate']))}</td><td>{escape(row['Meter'])}</td>"
        f"<td>{row['UsageQuantity']:.9f}</td><td>${row['Cost']:.9f}</td></tr>"
        for row in evidence.get("billing_rows", [])
    )
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>长图工坊 · 流程与真实调用审计报告</title>
<style>
:root{{--paper:#f5f3ea;--ink:#1f3a32;--muted:#69786d;--line:#cdd4c8;--accent:#b08c54}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.85 "Avenir Next","PingFang SC","Microsoft YaHei",sans-serif}}
header,main,footer{{max-width:1180px;margin:auto;padding:36px 5%}}header{{padding-top:72px;border-bottom:1px solid var(--ink)}}
.eyebrow{{font-size:11px;letter-spacing:3px;text-transform:uppercase;color:var(--accent)}}h1{{font:clamp(34px,5vw,64px)/1.3 "Songti SC","Noto Serif CJK SC",serif;margin:22px 0}}h2{{font:30px/1.5 "Songti SC",serif}}h3{{font-size:18px}}
.intro{{max-width:780px}}nav{{display:flex;gap:22px;flex-wrap:wrap;margin-top:30px}}a{{color:inherit;text-underline-offset:5px}}
section{{padding:36px 0;border-bottom:1px solid var(--line);scroll-margin-top:20px}}.kpis{{display:grid;grid-template-columns:repeat(4,1fr);margin:30px 0;gap:1px;background:var(--line);border:1px solid var(--line)}}
.kpis>div{{background:var(--paper);padding:24px 18px}}.kpis strong{{display:block;font:32px/1.5 "Avenir Next",sans-serif}}.kpis small,.meta{{color:var(--muted);font-size:12px}}
.notice{{padding:14px 18px;background:#e8e4d7;border-left:3px solid var(--accent);font-size:13px}}.flow{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:26px 0}}
.flow article{{padding:18px;background:#e4e9e0;border-top:2px solid var(--ink)}}.flow b{{display:block}}table{{border-collapse:collapse;width:100%;font-size:13px}}th{{text-align:left;color:var(--muted);font-weight:500}}td,th{{border-bottom:1px solid var(--line);padding:12px 10px;vertical-align:top}}.table-wrap{{overflow-x:auto}}
details{{margin:10px 0}}summary{{cursor:pointer;padding:12px 0}}.call{{border-top:1px solid var(--line)}}.call>summary{{display:flex;gap:15px;align-items:center;flex-wrap:wrap}}.call>summary span:not(.index),em{{font-size:12px;color:var(--muted)}}.index{{color:var(--accent);font-size:20px}}.call-body{{padding:10px 20px 24px;border-left:1px solid var(--line)}}.metrics{{display:flex;gap:25px;flex-wrap:wrap;font-size:13px}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;max-height:560px;overflow:auto;background:#e8ebe3;padding:18px;font:12px/1.75 Menlo,Consolas,monospace}}figure{{display:inline-block;margin:15px 20px 15px 0;max-width:360px;vertical-align:top}}img{{max-width:100%;height:auto}}figcaption{{font-size:10px;color:var(--muted);overflow-wrap:anywhere}}.footnote{{font-size:12px;color:var(--muted)}}@media(max-width:700px){{.kpis{{grid-template-columns:repeat(2,1fr)}}.flow{{grid-template-columns:1fr}}header{{padding-top:36px}}.call-body{{padding-left:10px}}}}@media print{{header,main,footer{{max-width:none}}pre{{max-height:none}}details{{break-inside:avoid}}}}
</style><header><div class="eyebrow">LONGFORM STUDIO / ENGINEERING & COST AUDIT / 2026</div>
<h1>从产品资料，<br>到一条可编辑的长图。</h1><p class="intro">流程、模型分工与真实环境证据报告。主样本：<b>{escape(project.name)}</b> · 版本 {project.version} · {len(project.screens)} 屏 · {project.width} × {sum(s.height for s in project.screens)}。快照生成：{escape(now())}。</p>
<nav><a href="#pipeline">01 当前逻辑</a><a href="#usage">02 Token 与成本</a><a href="#screens">03 分屏</a><a href="#calls">04 完整输入输出</a><a href="#timing">05 耗时</a><a href="#billing">06 账单证据</a></nav></header><main>
<div class="kpis"><div><strong>{len(calls)}</strong><small>真实 HTTP 调用记录 · 含失败尝试</small></div><div><strong>{totals['total']:,}</strong><small>已返回的总 token · 缺失 {missing_usage} 笔</small></div><div><strong>{cost_headline}</strong><small>已知 usage 成本折算 · 未知 {missing_cost} 笔</small></div><div><strong>{screen_count * 3 + 2}</strong><small>当前流程无重试时的标准调用数</small></div></div>
<p class="notice">审计口径：主样本汇总为该项目保留的全部调用历史，不把它误称为“只生成一条长图的单次成本”。当前标准流程是 2 + 3 × 内容屏数 + 扫描页 OCR 次数；旧 A 去字、自动审图与反复重生若出现在历史中，仍计入真实历史但不代表当前还在执行。价格由 Azure 实际账单 Cost ÷ UsageQuantity 核实，不采用搜索引擎给出的替代模型价格。</p>
<section id="pipeline"><div class="eyebrow">01 / CURRENT IMPLEMENTATION</div><h2>先生图，再读图排字。</h2><div class="flow">
<article><b>01 阅读资料</b>gpt-6.1-sol 整理事实；只有扫描页缺少文本时逐页 OCR。</article>
<article><b>02 设计叙事</b>gpt-6.1-sol 决定屏数、文案、气质与构图。此时文字坐标不是最终排版。</article>
<article><b>03 含字参考稿</b>GPT Image 2.5 自主设计字体、大小与落位；不把规划坐标发给参考稿生图。</article>
<article><b>04 提取 → 底图</b>gpt-6.1-sol 读参考稿提取文字；再由 GPT Image 2.5 按真实文字避让区重生无字底图。</article>
<article><b>05 工程叠字</b>React/CSS 叠字、分色、渐变；Playwright 全分辨率导出。编辑与导出均 0 次模型调用。</article></div>
<p>“阅读资料并规划”一个按钮，背后仍是两个独立 LLM 调用。生成逐屏执行；参数屏直接工程排版，不调用图像模型。没有型号／来源审核门禁，也没有自动审图。可选“适配底图排版”每屏另加 1 次；“只重生这一屏”另含提示词修订与生图调用。结构失败最多再调用一次；HTTP 错误不会触发结构重试。图像仅使用用户配置的节点；默认单节点 2 RPM，可按实际额度调整。额外节点轮询；429 后切换，最多 3 轮，失败尝试也有记录。</p>
<p>输入传递：资料步骤附产品／人物参考和前 8 页说明书图；上下文含全部已提取页文本。参考稿附文案及艺术指导；提取步骤附当前参考稿；底图步骤附参考稿、产品／人物原始参考和实际提取的 text_regions。当前结构化调用使用 Responses v1、instructions、input_text/input_image、text.format JSON schema（strict=false，由 Pydantic 严格校验应用结构）、max_output_tokens=16000、store=false。图像调用仍使用 images/edits 或 generations，API 2025-04-01-preview，单次 n=1。历史 Chat Completions 调用原样保留；usage 同时兼容新旧字段。</p>
<h3>本地运行与文字编辑</h3><p>页面使用轻量项目目录、按需读取调用记录、GZip、WebP 预览与屏外懒加载；导出仍用原始高清图。项目、素材、历史和调用记录保存在本地工作空间，不由 GitHub 同步。请备份整个数据目录；客户版不包含云端容器部署。</p>
<p>修改文字时用 Unicode 字符匹配保留未改文字的分色，替换段继承原段颜色，插入文字继承邻近样式；支持整层／每段双颜色线性渐变与 0–360°方向。预览、编辑中的视觉文字、保存和高清导出使用同一渲染逻辑。任意复杂渐变、透视字和精确字体复刻仍不等同于原始图片的像素级还原。</p></section>
<section id="usage"><div class="eyebrow">02 / METERED USAGE</div><h2>Token 不猜，费用分清口径。</h2><p>主样本输入 <b>{totals['input']:,}</b>，输出 <b>{totals['output']:,}</b>，其中已缓存输入 <b>{totals['cached']:,}</b>。reasoning_tokens 是输出 token 的子集，不能重复累加。usage 未返回的调用不按 0 处理。图像输入分 text/image 分别计价；图片输出 token 不是图片像素数。</p>
<p>费用公式（USD）：规划 = [(输入−缓存)×输入价 + 缓存×缓存价 + 输出×输出价] ÷ 1,000,000；生图 = [文本输入×文本价 + 图像输入×图像价 + 输出×输出价] ÷ 1,000,000。主样本有 {cross_node} 笔跨节点价格仅参考已核实节点折算。每笔详情均注明来源；税费、汇率、折扣、未回传 usage 的失败尝试和基础设施费用不包含在项目折算总额中。</p>
<div class="table-wrap"><table><tr><th>用途</th><th>真实调用次数</th><th>路径</th></tr>{purposes}</table></div>
<h3>工作空间其他项目 · 各自独立的历史口径</h3><div class="table-wrap"><table><tr><th>项目</th><th>屏数</th><th>调用</th><th>已知 token</th><th>折算金额</th></tr>{''.join(all_projects)}</table></div>
<p class="footnote">项目导入可能复制历史记录，不能把各项目相加后当成订阅新增账单。模型返回用量不包含 Copilot 开发会话的 token。</p></section>
<section id="screens"><div class="eyebrow">03 / SCREEN PLAN</div><h2>这条长图的实际分屏</h2><div class="table-wrap"><table><tr><th>#</th><th>屏幕</th><th>高度</th><th>当前标准调用链</th></tr>{screen_table}</table></div>
<details><summary>完整当前规划、事实与工程图层（真实应用状态，不冒充原始模型回复）</summary>{_json(project.model_dump())}</details></section>
<section id="calls"><div class="eyebrow">04 / REQUEST-BY-REQUEST EVIDENCE</div><h2>每一次调用，都可以展开。</h2><p>按创建时间排列；prompt 是记录中的真实发送内容，不是现在重新生成的预览。结构化调用的新记录包含原始响应 JSON；旧记录未保存的正文明确标为不可恢复。图片不将 base64 塞入记录，而保存文件名／SHA-256／尺寸及可见预览。没有凭据或访问令牌。</p>{''.join(details)}</section>
<section id="timing"><div class="eyebrow">05 / ELAPSED TIME</div><h2>模型耗时，不等于端到端耗时。</h2><p>{timing}</p><p>任务时间含网络、限速等待、图像处理与落盘；模型 usage.latency_checkpoint 是服务端时间。历史调用未存完成时间时，不用相邻调用开始时间冒充真实时长。任务列表最多 50 笔；重复任务逐笔列出，不能把所有任务时长相加称为一次全流程。</p><div class="table-wrap"><table><tr><th>任务</th><th>状态</th><th>开始 UTC</th><th>真实墙钟时间</th><th>结果</th></tr>{job_rows}</table></div></section>
<section id="billing"><div class="eyebrow">06 / VERIFIED BILLING EVIDENCE</div><h2>真实账单与单次折算，分别展示。</h2><p>来源：Azure Cost Management ActualCost API（2025-03-01），查询时间 {escape(evidence.get('queried_at', '未提供'))}，窗口 {escape(evidence.get('window', '未提供'))}。UsageQuantity 的计量单位来自 meter 名称中的 1M Tokens。已入账数据可能延迟；账单是共享资源上的全部应用用量，不能归因成这个项目的专属费用。</p>
<div class="table-wrap"><table><tr><th>模型</th><th>已核实节点</th><th>类型</th><th>有效价率 USD</th></tr>{rates_rows or '<tr><td colspan="4">未提供可核实价率，金额未知，不展示虚构价格。</td></tr>'}</table></div>
<h3>已入账模型费用行 · 非该项目独占账单</h3><div class="table-wrap"><table><tr><th>日期</th><th>真实计费 meter</th><th>用量（百万 token）</th><th>ActualCost USD</th></tr>{billing_rows}</table></div>
<details><summary>原始模型账单证据与公式参数</summary>{_json(evidence)}</details></section>
</main><footer><div class="eyebrow">LONGFORM STUDIO · TRACEABLE BY DESIGN</div><p>完整离线 HTML · 内嵌样本图片 · 不依赖第三方字体或脚本 · 请按内部资料管理，其中包含真实产品文案和提示词。</p></footer></html>"""
