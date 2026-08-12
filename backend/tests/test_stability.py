from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
for module_dir in (
    ROOT,
    ROOT / "evaluation_agents_delivery",
    ROOT / "audit_agent",
    ROOT / "data_processing",
):
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))

from audit_agent import AuditAgent, SYSTEM_PROMPT
from pipeline.config import AGENTS, EVALUATION_MAX_WORKERS
from pipeline.bailian_client import BailianClient, ModelConfig
from pipeline.orchestrator import EvaluationOrchestrator, build_agent_payload
from pipeline.retry_policy import RetryPolicy
from pipeline.retry_policy import with_retry_feedback
from pipeline.validators import validate_pipeline_output
import academic_api
import data_processor


class QuotaError(Exception):
    status_code = 429
    body = {"error": {"type": "insufficient_quota", "code": "insufficient_quota"}}


class UnsupportedResponseFormatError(Exception):
    status_code = 400


class FailingEvaluationClient:
    def __init__(self):
        self.calls = 0

    def complete_json(self, prompt, case_text):
        self.calls += 1
        raise QuotaError("token-limit: insufficient_quota")


class StabilityTests(unittest.TestCase):
    def test_validator_normalizes_compatible_evidence_shapes(self):
        raw = json.dumps({
            "agent_name": "logical_rigor",
            "dimension_name": AGENTS["logical_rigor"],
            "score": 70,
            "confidence": 0.8,
            "risk_level": "medium",
            "summary": "存在一处证据支撑不足",
            "strengths": [],
            "issues": [{
                "issue_type": "unsupported_claim",
                "severity": "medium",
                "quote": "文章直接断言该政策必然提高治理效率，但未提供比较数据。",
                "suggestion": "补充数据或收敛结论。",
            }],
            "evidence_refs": [{
                "text": "文章直接断言该政策必然提高治理效率，但未提供比较数据。",
                "page": 2,
            }],
            "reasoning_md": "评价依据来自论文结论段的直接断言。该断言缺少比较数据、反事实说明与适用边界，因此将其识别为证据不足，而不是否定文章全部论证。建议补充相应证据并限制结论范围，同时区分政策目标、实施条件与实际效果，避免把规范性主张直接写成已经得到验证的经验事实。",
            "limitations": [],
        }, ensure_ascii=False)

        result = validate_pipeline_output(raw, "logical_rigor", "case", raw)

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(
            result.normalized_value["issues"][0]["evidence"],
            "文章直接断言该政策必然提高治理效率，但未提供比较数据。",
        )
        self.assertEqual(result.normalized_value["evidence_refs"][0]["location"], "第 2 页")

    def test_ethics_unreported_disclosure_becomes_limitation_not_violation(self):
        evidence = "论文未说明伦理审批或知情同意情况。"
        raw = json.dumps({
            "agent_name": "ethics_bias",
            "dimension_name": AGENTS["ethics_bias"],
            "score": 80,
            "confidence": 0.7,
            "risk_level": "low",
            "summary": "未发现可以由原文确认的伦理违规",
            "strengths": [],
            "issues": [{
                "issue_type": "ethics_approval_unclear",
                "severity": "medium",
                "suggestion": "如涉及人类参与者，请补充说明。",
            }],
            "evidence_refs": [evidence],
            "reasoning_md": "文章没有提供足以确认明确伦理违规的事实。缺少披露只能作为信息边界记录，不能把未说明直接等同于未获得审批，因此本维度采用保守判断并保留限制说明。只有原文明确显示涉及人类参与者、隐私数据或未经授权使用材料时，才能进一步形成伦理风险判断；当前证据不足以支持这种结论。",
            "limitations": [],
            "bias_detected": [],
        }, ensure_ascii=False)

        result = validate_pipeline_output(raw, "ethics_bias", "case", raw)

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.normalized_value["issues"], [])
        self.assertTrue(result.normalized_value["limitations"])

    def test_weak_gateway_fenced_alias_output_is_repaired_and_revalidated(self):
        raw = """模型分析如下：
```json
{
  "result": {
    "agent": "logic",
    "dimension": "逻辑分析",
    "rating": "72",
    "confidence_score": "78%",
    "risk": "中风险",
    "conclusion": "论文主要推理链基本成立，但结论范围需要进一步限定。",
    "pros": "研究问题和分析对象较为明确。",
    "findings": [
      {
        "category": "overgeneralization",
        "level": "中等",
        "description": "论文根据局部案例直接推及全部公共管理场景。",
        "recommendation": "收敛结论范围并明确适用条件。"
      },
      "这是一条没有证据结构的候选问题"
    ],
    "citations": ["论文根据局部案例直接推及全部公共管理场景。"],
    "analysis": "结论范围偏宽。",
    "caveats": null
  }
}
```
"""
        case_text = "论文根据局部案例直接推及全部公共管理场景。"

        result = validate_pipeline_output(raw, "logical_rigor", "case", case_text)

        self.assertTrue(result.ok, result.errors)
        normalized = result.normalized_value
        self.assertEqual(normalized["agent_name"], "logical_rigor")
        self.assertEqual(normalized["score"], 72)
        self.assertEqual(normalized["confidence"], 0.78)
        self.assertEqual(normalized["risk_level"], "medium")
        self.assertEqual(len(normalized["issues"]), 1)
        self.assertGreaterEqual(len(normalized["reasoning_md"]), 80)
        self.assertTrue(normalized["limitations"])

    def test_malformed_issue_without_verifiable_evidence_is_not_published(self):
        raw = json.dumps({
            "agent_name": "logical_rigor",
            "dimension_name": AGENTS["logical_rigor"],
            "score": 68,
            "confidence": 0.65,
            "risk_level": "medium",
            "summary": "存在候选逻辑问题，但模型没有提供可核验证据。",
            "strengths": [],
            "issues": [{
                "issue_type": "unsupported_claim",
                "severity": "medium",
                "evidence": "不足",
                "suggestion": "补证据",
            }],
            "evidence_refs": [{"location": "全文", "quote": ""}],
            "reasoning_md": "过短",
            "limitations": [],
        }, ensure_ascii=False)

        result = validate_pipeline_output(raw, "logical_rigor", "case", "论文正文")

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.normalized_value["issues"], [])
        self.assertEqual(result.normalized_value["evidence_refs"], [])
        self.assertTrue(result.normalized_value["limitations"])

    def test_retry_feedback_includes_previous_output_for_targeted_repair(self):
        repaired = with_retry_feedback(
            {"content": {"full_text": "paper"}},
            agent="logical_rigor",
            reason="format_validation_failed",
            problems=["issues[0].evidence 缺失"],
            attempt=1,
            previous_output='{"score": "72", "issues": [{}]}',
        )

        feedback = repaired["review_context"]["retry_feedback"]
        self.assertIn("previous_output", feedback)
        self.assertIn("待修正稿", feedback["instruction"])
        self.assertTrue(any("issues[0].evidence" in problem for problem in feedback["problems"]))

    def test_evaluation_client_falls_back_for_compatible_gateway(self):
        response = Mock()
        response.choices = [Mock(message=Mock(content='{"score":80}'))]
        create = Mock(side_effect=[
            UnsupportedResponseFormatError("unknown parameter: response_format"),
            response,
        ])
        client = object.__new__(BailianClient)
        client._client = Mock()
        client._client.chat.completions.create = create
        client.config = ModelConfig(model="deepseek/deepseek-v4-pro")
        client._rate_lock = threading.Lock()
        client._last_request_started = 0.0

        raw = client.complete_json("prompt", "paper")

        self.assertEqual(raw, '{"score":80}')
        self.assertEqual(create.call_count, 2)
        fallback_kwargs = create.call_args_list[1].kwargs
        self.assertNotIn("response_format", fallback_kwargs)
        self.assertNotIn("extra_body", fallback_kwargs)

    def test_evaluation_quota_error_fails_without_retry(self):
        client = FailingEvaluationClient()
        with tempfile.TemporaryDirectory() as temp:
            prompt = Path(temp) / "agent_v1.0.txt"
            prompt.write_text("prompt", encoding="utf-8")
            orchestrator = EvaluationOrchestrator(
                client=client,
                prompts={"data_reliability": prompt},
                retry_policy=RetryPolicy(api_retries=2, base_delay_seconds=0),
                max_workers=1,
            )
            result = orchestrator.evaluate_data({"content": {"full_text": "paper"}})[0]
        self.assertEqual(client.calls, 1)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.error_type, "insufficient_quota")

    def test_audit_quota_error_fails_without_sdk_or_outer_retry(self):
        create = Mock(side_effect=QuotaError("insufficient_quota"))
        client = Mock()
        client.chat.completions.create = create
        agent = AuditAgent(api_key="test", max_retries=3)
        agent._client = client
        with patch("audit_agent.time.sleep") as sleep:
            with self.assertRaises(RuntimeError):
                agent._call_llm_with_retry("test")
        self.assertEqual(create.call_count, 1)
        sleep.assert_not_called()

    def test_audit_retry_progress_is_specific_and_bounded(self):
        create = Mock(side_effect=ConnectionError("model gateway unavailable"))
        client = Mock()
        client.chat.completions.create = create
        events = []
        agent = AuditAgent(
            api_key="test",
            max_retries=2,
            progress_callback=events.append,
        )
        agent._client = client

        with patch("audit_agent.random.uniform", return_value=0), patch("audit_agent.time.sleep") as sleep:
            with self.assertRaises(RuntimeError):
                agent._call_llm_with_retry("test")

        self.assertEqual(create.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual([event.get("phase") for event in events], [
            "request", "backoff", "request", "fallback",
        ])
        self.assertIn("第 1/2 次请求", events[0]["message"])
        self.assertIn("保留五维原始评价", events[-1]["message"])

    def test_audit_long_timeout_retries_once_for_resilience(self):
        create = Mock(side_effect=TimeoutError("Request timed out"))
        client = Mock()
        client.chat.completions.create = create
        events = []
        agent = AuditAgent(api_key="test", max_retries=2, progress_callback=events.append)
        agent._client = client

        with patch("audit_agent.time.monotonic", side_effect=[0.0, 31.0, 32.0, 63.0]), \
             patch("audit_agent.random.uniform", return_value=0), \
             patch("audit_agent.time.sleep") as sleep:
            with self.assertRaises(RuntimeError):
                agent._call_llm_with_retry("test")

        self.assertEqual(create.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(events[-1]["phase"], "fallback")

    def test_audit_request_disables_thinking_and_caps_output(self):
        response = Mock()
        response.choices = [Mock(message=Mock(content='{"approved":true,"audit_log":{},"changes":[]}'))]
        response.usage = Mock(prompt_tokens=100, completion_tokens=20)
        client = Mock()
        client.chat.completions.create.return_value = response
        agent = AuditAgent(api_key="test", max_output_tokens=1400)
        agent._client = client

        agent._call_llm_with_retry("payload")

        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], 1400)
        self.assertEqual(kwargs["extra_body"], {"enable_thinking": False})

    def test_audit_custom_model_uses_portable_openai_fields(self):
        response = Mock()
        response.choices = [Mock(message=Mock(content='{"approved":true,"audit_log":{},"changes":[]}'))]
        response.usage = Mock(prompt_tokens=100, completion_tokens=20)
        client = Mock()
        client.chat.completions.create.return_value = response
        agent = AuditAgent(
            api_key="test",
            base_url="https://api.example.com/v1",
            model="deepseek/deepseek-v4-pro",
        )
        agent._client = client

        agent._call_llm_with_retry("payload")

        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertNotIn("extra_body", kwargs)
        self.assertEqual(kwargs["model"], "deepseek/deepseek-v4-pro")

    def test_academic_helpers_accept_custom_openai_compatible_model(self):
        self.assertEqual(
            academic_api._assert_qwen_model("deepseek/deepseek-v4-pro"),
            "deepseek/deepseek-v4-pro",
        )

    def test_audit_payload_omits_full_pdf_and_keeps_referenced_anchor(self):
        agent = AuditAgent(api_key="test")
        paper = {
            "paper_info": {"title": "Long paper"},
            "content": {
                "full_text": "X" * 200000,
                "sections": [{"section_title": "方法", "section_text": "M" * 50000}],
                "text_anchors": [
                    {"anchor_id": "keep", "page": 2, "text": "被引用的原文证据"},
                    {"anchor_id": "drop", "page": 3, "text": "无关坐标锚点"},
                ],
            },
        }
        reports = {"methodology": {"evidence_ref": {"anchor_id": "keep"}, "score": 80}}
        message = agent._build_user_message(paper, reports)
        self.assertLess(len(message), 40000)
        self.assertIn("被引用的原文证据", message)
        self.assertNotIn("无关坐标锚点", message)
        self.assertNotIn("X" * 1000, message)

    def test_audit_payload_omits_non_audit_report_prose(self):
        agent = AuditAgent(api_key="test")
        reports = {
            "methodology": {
                "score": 80,
                "summary": "结论",
                "issues": [{"evidence": "证据"}],
                "strengths": ["很长的优点" * 100],
                "actionable_advice": ["很长的建议" * 100],
            }
        }
        message = agent._build_user_message({"content": {}}, reports)
        self.assertIn("结论", message)
        self.assertIn("证据", message)
        self.assertNotIn("很长的优点", message)
        self.assertNotIn("很长的建议", message)

    def test_patch_output_prompt_does_not_request_full_reports(self):
        self.assertIn("只返回修改补丁", SYSTEM_PROMPT)
        self.assertIn("禁止输出 `audited_results`", SYSTEM_PROMPT)

    def test_audit_applies_bounded_patch_without_losing_original_fields(self):
        original = {
            "methodology": {
                "score": 80,
                "summary": "原结论",
                "issues": [
                    {"evidence": "错误证据", "suggestion": "保留建议"},
                    {"evidence": "删除证据"},
                ],
            }
        }
        changed = AuditAgent._apply_changes(original, [
            {
                "engine": "methodology",
                "path": "issues.0.evidence",
                "operation": "replace",
                "value": "已核验证据",
            },
            {
                "engine": "methodology",
                "path": "issues.1",
                "operation": "remove",
            },
            {
                "engine": "methodology",
                "path": "issues.-",
                "operation": "add",
                "value": {"evidence": "新增冲突证据"},
            },
        ])
        self.assertEqual(changed["methodology"]["issues"][0]["evidence"], "已核验证据")
        self.assertEqual(changed["methodology"]["issues"][0]["suggestion"], "保留建议")
        self.assertEqual(changed["methodology"]["issues"][1]["evidence"], "新增冲突证据")
        self.assertEqual(original["methodology"]["issues"][0]["evidence"], "错误证据")

    def test_audit_accepts_patch_response_and_records_output_mode(self):
        raw = json.dumps({
            "approved": True,
            "audit_log": {"fact_check_summary": "通过"},
            "changes": [{
                "engine": "methodology",
                "path": "score",
                "operation": "replace",
                "value": 78,
                "reason": "证据支持程度略低",
            }],
        }, ensure_ascii=False)
        agent = AuditAgent(api_key="test")
        reports = {
            engine: {"score": 80, "summary": "保留"}
            for engine in ("methodology", "logic", "ethics", "innovation", "academic_impact")
        }
        with patch.object(agent, "_call_llm_with_retry", return_value=raw):
            output = agent.audit(
                {"content": {}},
                reports,
            )
        self.assertEqual(output.audited_results["methodology"]["score"], 78)
        self.assertEqual(output.audited_results["methodology"]["summary"], "保留")
        self.assertEqual(output.audit_log["output_mode"], "patch")
        self.assertEqual(output.audit_log["change_count"], 1)

    def test_audit_rejects_patch_to_protected_identity_fields(self):
        with self.assertRaises(ValueError):
            AuditAgent._apply_changes(
                {"methodology": {"agent_name": "data_reliability"}},
                [{
                    "engine": "methodology",
                    "path": "agent_name",
                    "operation": "replace",
                    "value": "other",
                }],
            )

    def test_audit_reports_real_preparation_and_merge_phases(self):
        events = []
        agent = AuditAgent(api_key="test", progress_callback=events.append)
        parsed = {"audit_log": {}, "audited_results": {"methodology": {"score": 80}}}
        with patch.object(agent, "_validate_input"), \
             patch.object(agent, "_build_user_message", return_value="payload"), \
             patch.object(agent, "_call_llm_with_retry", return_value="response"), \
             patch.object(agent, "_parse_response", return_value=parsed), \
             patch.object(agent, "_merge_with_originals", return_value=parsed["audited_results"]):
            agent.audit({}, {"methodology": {"score": 80}})

        phases = [event.get("phase") for event in events]
        self.assertEqual(phases, [
            "input_validation",
            "input_validation_complete",
            "payload_build",
            "payload_ready",
            "response_validation",
            "response_validated",
            "merge_complete",
        ])
        self.assertTrue(all(event.get("progressKey") for event in events))

    def test_default_evaluation_concurrency_is_bounded(self):
        self.assertGreaterEqual(EVALUATION_MAX_WORKERS, 1)
        self.assertLessEqual(EVALUATION_MAX_WORKERS, 2)

    def test_semantic_scholar_result_is_cached(self):
        academic_api._SEMANTIC_CACHE.clear()
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "total": 1,
            "data": [{"title": "Paper", "citationCount": 3, "venue": "Venue", "year": 2025}],
        }
        with patch.object(academic_api.requests, "get", return_value=response) as get:
            first = academic_api.query_semantic_scholar_api("Paper")
            second = academic_api.query_semantic_scholar_api("Paper")
        self.assertEqual(first, second)
        self.assertEqual(get.call_count, 1)

    def test_agent_payload_selects_relevant_sections_and_limits_text(self):
        paper = {
            "content": {
                "full_text": "x" * 100000,
                "sections": [
                    {"section_title": "引言", "section_text": "intro", "source_anchor_ids": ["a1"]},
                    {"section_title": "实验结果", "section_text": "result" * 10000, "source_anchor_ids": ["a2"]},
                    {"section_title": "伦理说明", "section_text": "ethics", "source_anchor_ids": ["a3"]},
                ],
                "text_anchors": [
                    {"anchor_id": "a1", "page": 1},
                    {"anchor_id": "a2", "page": 2},
                    {"anchor_id": "a3", "page": 3},
                ],
                "references_text": "refs",
            },
            "review_context": {},
        }
        scoped = build_agent_payload(paper, "data_reliability", max_chars=12000)
        self.assertIn("实验结果", scoped["content"]["full_text"])
        self.assertNotIn("伦理说明", scoped["content"]["full_text"])
        self.assertLessEqual(len(scoped["content"]["full_text"]), 12020)
        self.assertEqual(scoped["content"]["text_anchors"][0]["anchor_id"], "a2")

    def test_chinese_numbered_chapters_are_split_and_categorized(self):
        text = (
            "第 1 章 绪论\n研究背景。\n"
            "第 2 章 运动学模型构建\n方法正文。\n"
            "第 3 章 精度实验与验证\n实验正文。\n"
            "结论\n最终结论。"
        )
        sections = data_processor._split_sections(text)
        categories = [item["section_category"] for item in sections]
        self.assertEqual(categories, ["引言", "方法", "实验", "结论"])


if __name__ == "__main__":
    unittest.main()
