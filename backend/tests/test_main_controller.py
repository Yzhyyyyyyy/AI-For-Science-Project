from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "main_controller"))

import main


def agent(status: str, score: int = 80) -> dict:
    return {
        "status": status,
        "result": {"score": score, "summary": "ok"} if status == "success" else None,
        "errors": [] if status == "success" else ["timeout"],
    }


class MainControllerTests(unittest.TestCase):
    def test_parse_visual_pages(self):
        self.assertEqual(main.parse_visual_pages("1,3,5-7"), [1, 3, 5, 6, 7])
        self.assertIsNone(main.parse_visual_pages(""))
        with self.assertRaises(ValueError):
            main.parse_visual_pages("0")

    def test_process_document_defaults_to_text_only(self):
        import inspect
        default = inspect.signature(main.process_document).parameters["extract_visuals"].default
        self.assertFalse(default)

    def test_pipeline_reuses_completed_stage_cache(self):
        paper = {
            "paper_info": {"title": "Test Paper"},
            "content": {
                "full_text": "paper",
                "text_anchors": [{"anchor_id": "a1", "char_start": 0, "char_end": 5}],
            },
        }
        response = {name: agent("success") for name in main.AGENT_TO_AUDIT}
        response["evaluation_status"] = "success"
        response["error_summary"] = {}
        audited = {
            main.AGENT_TO_AUDIT[name]: item["result"]
            for name, item in response.items()
            if name in main.AGENT_TO_AUDIT
        }
        audit_output = Mock(audit_log={"status": "ok"}, audited_results=audited)
        audit_instance = Mock()
        audit_instance.audit.return_value = audit_output
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "paper.pdf"
            source.write_bytes(b"%PDF-cache")
            with patch.object(main, "CACHE_ROOT", Path(temp) / "cache"), patch.dict(
                main.os.environ, {"DASHSCOPE_API_KEY": "test"}
            ), patch.object(main, "process_document", return_value=paper) as process, patch.object(
                main, "evaluate_paper", return_value=response
            ) as evaluate, patch.object(main, "AuditAgent", return_value=audit_instance):
                first = main.main_pipeline(str(source), extract_visuals=False)
                second = main.main_pipeline(str(source), extract_visuals=False)
                refreshed = main.main_pipeline(str(source), extract_visuals=False, force_refresh=True)
        self.assertFalse(first["stage_metrics"]["parsing"]["cached"])
        self.assertTrue(second["stage_metrics"]["parsing"]["cached"])
        self.assertTrue(second["stage_metrics"]["evaluation"]["cached"])
        self.assertTrue(second["stage_metrics"]["audit"]["cached"])
        self.assertFalse(refreshed["stage_metrics"]["parsing"]["cached"])
        self.assertFalse(refreshed["stage_metrics"]["evaluation"]["cached"])
        self.assertEqual(process.call_count, 2)
        self.assertEqual(evaluate.call_count, 2)
        self.assertEqual(audit_instance.audit.call_count, 2)

    def test_degraded_parse_is_not_cacheable(self):
        self.assertFalse(main._paper_data_cacheable({
            "paper_info": {"title": "Unknown Title"},
            "content": {"full_text": "paper", "text_anchors": [{"anchor_id": "a1"}]},
        }))

    def test_page_number_tolerates_null_and_invalid_values(self):
        self.assertEqual(main._safe_page_number(None), 0)
        self.assertEqual(main._safe_page_number(""), 0)
        self.assertEqual(main._safe_page_number("not-a-page"), 0)
        self.assertEqual(main._safe_page_number("7"), 7)

    def test_attach_risk_locations(self):
        paper = {
            "content": {
                "full_text": "开头。样本量缺乏充分论证。结尾。",
                "text_anchors": [{
                    "anchor_id": "pdf_p0002_b0003",
                    "page": 2,
                    "paragraph_index": 3,
                    "char_start": 3,
                    "char_end": 15,
                    "bbox": [1, 2, 3, 4],
                    "bbox_norm": [10, 20, 30, 40],
                }],
            }
        }
        results = {"data_reliability": {
            "issues": [{"evidence": "样本量缺乏充分论证"}],
            "evidence_refs": [{"location": "方法", "quote": "样本量缺乏充分论证"}],
        }}
        main.attach_risk_locations(paper, results)
        self.assertEqual(results["data_reliability"]["issues"][0]["location"]["page"], 2)
        self.assertEqual(
            results["data_reliability"]["evidence_refs"][0]["block_id"],
            "pdf_p0002_b0003",
        )

    def test_location_matching_tolerates_spacing_and_links_issue_to_reference(self):
        paper = {
            "content": {
                "full_text": "实验结果表明，均方根误差为 0.72 mm，系统运行稳定。",
                "text_anchors": [{
                    "anchor_id": "pdf_p0008_b0002",
                    "page": 8,
                    "paragraph_index": 2,
                    "char_start": 0,
                    "char_end": 30,
                }],
            }
        }
        results = {"data_reliability": {
            "issues": [{"evidence": "论文只报告0.72mm，未报告重复实验方差。"}],
            "evidence_refs": [{"quote": "均方根误差为0.72mm", "page": 8}],
        }}
        main.attach_risk_locations(paper, results)
        result = results["data_reliability"]
        self.assertEqual(result["evidence_refs"][0]["coordinates"]["page"], 8)
        self.assertEqual(result["issues"][0]["location"]["page"], 8)

    def test_all_failed_skips_audit(self):
        response = {name: agent("failed") for name in main.AGENT_TO_AUDIT}
        response["evaluation_status"] = "failed"
        response["error_summary"] = {"failed_agents": list(main.AGENT_TO_AUDIT)}
        with patch.object(main, "evaluate_paper", return_value=response), patch.object(main, "AuditAgent") as audit:
            result = main.run_evaluation_and_audit({}, "key")
        self.assertEqual(result["evaluation_status"], "failed")
        self.assertEqual(result["final_results"], {})
        audit.assert_not_called()

    def test_invalid_key_failure_has_actionable_error(self):
        response = {name: agent("failed") for name in main.AGENT_TO_AUDIT}
        response["evaluation_status"] = "failed"
        response["error_summary"] = {
            "failed_agents": list(main.AGENT_TO_AUDIT),
            "error_types": ["invalid_api_key"],
        }
        with patch.object(main, "evaluate_paper", return_value=response):
            result = main.run_evaluation_and_audit({}, "key")
        self.assertEqual(result["error"]["code"], "INVALID_API_KEY")

    def test_partial_failure_recovers_failed_dimension_before_audit(self):
        response = {name: agent("success") for name in main.AGENT_TO_AUDIT}
        response["academic_impact"] = agent("failed")
        response["evaluation_status"] = "partial_failure"
        response["error_summary"] = {
            "failed_agents": ["academic_impact"],
            "error_types": ["validation_error"],
        }
        recovery = {
            "academic_impact": agent("success", score=76),
            "evaluation_status": "success",
            "error_summary": {"failed_agents": [], "error_types": []},
        }
        recovered_response = dict(response)
        recovered_response["academic_impact"] = recovery["academic_impact"]
        audit_output = Mock(
            audit_log={"fact_check_summary": "通过"},
            audited_results={
                main.AGENT_TO_AUDIT[name]: item["result"]
                for name, item in recovered_response.items()
                if name in main.AGENT_TO_AUDIT and item["status"] == "success"
            },
        )
        audit_instance = Mock()
        audit_instance.audit.return_value = audit_output
        with patch.object(main, "evaluate_paper", side_effect=[response, recovery]) as evaluate, patch.object(
            main, "AuditAgent", return_value=audit_instance
        ):
            result = main.run_evaluation_and_audit({}, "key", model_name="qwen-turbo")
        sent = audit_instance.audit.call_args.kwargs["preliminary_reports"]
        self.assertEqual(set(sent), set(main.AUDIT_TO_AGENT))
        self.assertEqual(result["evaluation_status"], "success")
        self.assertIn("academic_impact", result["final_results"])
        self.assertEqual(evaluate.call_args_list[1].kwargs["agent_names"], ["academic_impact"])
        self.assertEqual(evaluate.call_args_list[1].kwargs["model_name"], "qwen-turbo")
        self.assertEqual(evaluate.call_args_list[1].kwargs["max_workers"], 1)

    def test_partial_failure_still_incomplete_skips_final_audit(self):
        response = {name: agent("success") for name in main.AGENT_TO_AUDIT}
        response["academic_impact"] = agent("failed")
        response["evaluation_status"] = "partial_failure"
        response["error_summary"] = {
            "failed_agents": ["academic_impact"],
            "error_types": ["validation_error"],
        }
        recovery = {
            "academic_impact": agent("failed"),
            "evaluation_status": "failed",
            "error_summary": {
                "failed_agents": ["academic_impact"],
                "error_types": ["validation_error"],
            },
        }
        with patch.object(main, "evaluate_paper", side_effect=[response, recovery]), patch.object(
            main, "AuditAgent"
        ) as audit:
            result = main.run_evaluation_and_audit({}, "key")
        audit.assert_not_called()
        self.assertEqual(result["evaluation_status"], "partial_failure")
        self.assertFalse(result["audit_passed"])
        self.assertEqual(result["error"]["code"], "EVALUATION_INCOMPLETE")

    def test_audit_failure_preserves_successful_results(self):
        response = {name: agent("success") for name in main.AGENT_TO_AUDIT}
        response["evaluation_status"] = "success"
        response["error_summary"] = {}
        audit_instance = Mock()
        audit_instance.audit.side_effect = TimeoutError("audit timeout")
        with patch.object(main, "evaluate_paper", return_value=response), patch.object(
            main, "AuditAgent", return_value=audit_instance
        ):
            result = main.run_evaluation_and_audit({}, "key")
        self.assertFalse(result["audit_passed"])
        self.assertEqual(set(result["final_results"]), set(main.AGENT_TO_AUDIT))


if __name__ == "__main__":
    unittest.main()
