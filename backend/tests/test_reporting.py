from backend.models import ModelCall
from backend.reporting import call_cost, tokens, workflow_report
from backend.storage import Store
from backend.tests.fixtures import fixture_project


def call(**kwargs):
    kwargs.setdefault("prompt", "测试调用")
    return ModelCall(
        id="actual-call", project_id="p", purpose="design_plan",
        model="gpt-6.1-sol", deployment="design",
        endpoint="https://test.cognitiveservices.azure.com", request={},
        status="succeeded", **kwargs,
    )


def test_cost_uses_cached_input_and_does_not_double_count_reasoning():
    entry = call(usage={
        "prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200,
        "prompt_tokens_details": {"cached_tokens": 600},
        "completion_tokens_details": {"reasoning_tokens": 150},
    })
    evidence = {"rates": {"gpt-6.1-sol": {"test": {"input": 2, "cached": .1, "output": 10}}}}
    amount, note = call_cost(entry, evidence)
    assert amount == (400 * 2 + 600 * .1 + 200 * 10) / 1_000_000
    assert tokens(entry)["total"] == 1200 and "非逐请求账单" in note
    assert call_cost(call(), evidence)[0] is None


def test_responses_usage_keeps_raw_fields_and_supports_historical_cost_comparison():
    entry = call(usage={
        "input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200,
        "input_tokens_details": {"cached_tokens": 600},
        "output_tokens_details": {"reasoning_tokens": 150},
    })
    entry.request = {"api": "responses"}
    evidence = {"rates": {"gpt-6.1-sol": {"test": {"input": 2, "cached": .1, "output": 10}}}}
    assert tokens(entry)["cached"] == 600
    assert tokens(entry)["input"] == 1000 and tokens(entry)["output"] == 200
    assert call_cost(entry, evidence)[0] == .00286
    assert entry.usage["output_tokens_details"]["reasoning_tokens"] == 150


def test_report_escapes_actual_inputs_and_labels_missing_historical_output(tmp_path):
    store = Store(tmp_path)
    project = fixture_project(store)
    entry = call(prompt="<script>alert('not executable')</script>", usage={})
    entry.project_id = project.id
    store.save_call(entry)
    html = workflow_report(store, project.id)
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert "历史调用没有保存原始响应正文" in html
    assert "金额未知" in html
