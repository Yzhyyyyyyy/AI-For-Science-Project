"""Minimal offline test for the backend-facing service API."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_ROOT = Path(__file__).resolve().parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from service import evaluate_paper


AGENT_DIMENSIONS = {
    "data_reliability": "数据可靠性",
    "ethics_bias": "伦理与偏见",
    "logical_rigor": "逻辑严密性",
    "innovation": "创新性",
    "academic_impact": "学术影响力",
}


class FakeBailianClient:
    """Return schema-valid deterministic JSON without making network calls."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def complete_json(self, prompt: str, case_text: str) -> str:
        agent = next(
            name for name in AGENT_DIMENSIONS
            if f'"agent_name": "{name}"' in prompt
        )
        result = {
            "agent_name": agent,
            "dimension_name": AGENT_DIMENSIONS[agent],
            "score": 85,
            "confidence": 0.9,
            "risk_level": "low",
            "summary": "离线测试评价结果",
            "strengths": [],
            "issues": [],
            "evidence_refs": [],
            "reasoning_md": "该结果由离线测试客户端生成，用于验证统一服务入口、配置化评价维度及返回结构，"
            "不代表真实论文评价结论。此说明保持足够长度，以通过流水线的最小质量检查要求。"
            "测试同时覆盖标准论文 JSON 的读取、并发评价调度、输出校验以及标准化结果汇总。",
            "limitations": ["未调用真实模型"],
        }
        if agent == "ethics_bias":
            result["bias_detected"] = []
        if agent == "innovation":
            result["innovation_types"] = []
        if agent == "academic_impact":
            result["journal_recommendation"] = {
                "recommended_tier": None,
                "alternative_tier": None,
                "confidence": 0.3,
                "rationale": [],
                "readiness_gaps": ["离线测试未提供真实审稿证据"],
                "basis": [],
            }
        return json.dumps(result, ensure_ascii=False)


class EvaluatePaperServiceTest(unittest.TestCase):
    def test_evaluate_paper_returns_configured_dimensions(self) -> None:
        case_path = Path(__file__).resolve().parent / "tests" / "cases" / "test_case_01_typical.json"
        paper_data = json.loads(case_path.read_text(encoding="utf-8"))

        with (
            patch.dict(os.environ, {"DASHSCOPE_API_KEY": "unit-test-key"}),
            patch("service.BailianClient", FakeBailianClient),
        ):
            result = evaluate_paper(paper_data)

        self.assertIsInstance(result, dict)
        self.assertEqual(
            set(result),
            set(AGENT_DIMENSIONS) | {"evaluation_status", "error_summary"},
        )
        self.assertEqual(result["evaluation_status"], "success")
        self.assertEqual(result["error_summary"]["failed_agents"], [])
        for agent in AGENT_DIMENSIONS:
            self.assertEqual(result[agent]["status"], "success")
            self.assertIsInstance(result[agent]["result"], dict)

    def test_evaluate_paper_can_target_failed_dimensions(self) -> None:
        case_path = Path(__file__).resolve().parent / "tests" / "cases" / "test_case_01_typical.json"
        paper_data = json.loads(case_path.read_text(encoding="utf-8"))

        with (
            patch.dict(os.environ, {"DASHSCOPE_API_KEY": "unit-test-key"}),
            patch("service.BailianClient", FakeBailianClient),
        ):
            result = evaluate_paper(paper_data, agent_names=["academic_impact"])

        self.assertEqual(
            set(result),
            {"academic_impact", "evaluation_status", "error_summary"},
        )
        self.assertEqual(result["evaluation_status"], "success")
        self.assertEqual(result["academic_impact"]["status"], "success")


if __name__ == "__main__":
    unittest.main()
