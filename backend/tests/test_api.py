from __future__ import annotations

import sys
import tempfile
import unittest
import json
import hashlib
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import api


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        runtime = Path(cls.temp_dir.name)
        api.DB_PATH = runtime / "test.db"
        api.UPLOAD_DIR = runtime / "uploads"
        api.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_frontend_contract_is_exposed(self):
        required_paths = {
            "/api/review",
            "/api/feedback",
            "/api/reports",
            "/api/reports/{report_id}",
            "/api/reports/{report_id}/export",
            "/api/reports/{report_id}/deep-diagnosis",
            "/api/releases",
            "/api/tickets",
            "/api/metrics",
            "/api/health",
        }
        with TestClient(api.app) as client:
            root = client.get("/")
            self.assertEqual(root.status_code, 200)
            if root.headers.get("content-type", "").startswith("application/json"):
                self.assertFalse(root.json()["frontend_included"])
            else:
                self.assertIn("text/html", root.headers.get("content-type", ""))
            schema = client.get("/openapi.json")
            self.assertEqual(schema.status_code, 200)
            self.assertTrue(required_paths.issubset(schema.json()["paths"]))

    def test_health_and_feedback(self):
        with TestClient(api.app) as client:
            health = client.get("/api/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["status"], "ok")
            self.assertEqual(health.json()["version"], "4.8")

            feedback = client.post(
                "/api/feedback",
                json={"rating": 5, "category": "suggestion", "content": "测试反馈"},
            )
            self.assertEqual(feedback.status_code, 200)
            self.assertFalse(feedback.json()["unlocked"])

    def test_rejects_unsupported_upload(self):
        with TestClient(api.app) as client:
            response = client.post(
                "/api/review",
                files={"file": ("malware.exe", b"MZ", "application/octet-stream")},
            )
            self.assertEqual(response.status_code, 415)

    def test_visual_mode_defaults_to_text_and_validates_input(self):
        with TestClient(api.app) as client:
            schema = client.get("/openapi.json").json()
            body_schema = schema["components"]["schemas"][
                schema["paths"]["/api/review"]["post"]["requestBody"]["content"][
                    "multipart/form-data"
                ]["schema"]["$ref"].split("/")[-1]
            ]
            self.assertEqual(body_schema["properties"]["visual_mode"]["default"], "text")
            response = client.post(
                "/api/review",
                data={"visual_mode": "invalid"},
                files={"file": ("paper.pdf", b"%PDF-test", "application/pdf")},
            )
            self.assertEqual(response.status_code, 422)

    def test_feedback_unlocks_real_backend_services(self):
        with TestClient(api.app) as client:
            report_id = str(uuid.uuid4())
            report = {
                "paperTitle": "测试论文",
                "overallScore": 80,
                "evaluationStatus": "success",
                "engines": {
                    "logic": {
                        "score": 80,
                        "confidence": 0.8,
                        "issues": [{"evidence": "证据", "suggestion": "补充论证"}],
                        "limitations": ["离线测试"],
                        "actionable_advice": ["补充论证"],
                    }
                },
            }
            with api.db() as connection:
                connection.execute(
                    "INSERT INTO reports(id,filename,file_hash,report_data,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (report_id, "paper.pdf", "hash-" + report_id,
                     json.dumps(report, ensure_ascii=False), api.utc_now()),
                )
            unlocked = client.post(
                "/api/feedback",
                json={"report_id": report_id, "rating": 5, "content": "测试反馈"},
            )
            self.assertEqual(unlocked.status_code, 200)
            token = unlocked.json()["entitlementToken"]

            export = client.get(
                f"/api/reports/{report_id}/export",
                params={"entitlement_token": token},
            )
            self.assertEqual(export.status_code, 200)
            self.assertNotIn("普通版水印", export.text)

            diagnosis = client.post(
                f"/api/reports/{report_id}/deep-diagnosis",
                params={"entitlement_token": token},
            )
            self.assertEqual(diagnosis.status_code, 200)
            self.assertEqual(diagnosis.json()["priorityActions"], ["补充论证"])

            releases = client.get("/api/releases", params={"entitlement_token": token})
            self.assertEqual(releases.status_code, 200)

    def test_identical_upload_uses_stability_cache(self):
        content = b"%PDF-offline-cache-test"
        digest = hashlib.sha256(content).hexdigest()
        report_id = str(uuid.uuid4())
        cached = {
            "paperTitle": "缓存测试",
            "overallScore": 88,
            "evaluationStatus": "success",
            "auditPassed": True,
            "engines": {},
        }
        with TestClient(api.app) as client:
            with api.db() as connection:
                connection.execute(
                    "INSERT INTO reports(id,filename,file_hash,report_data,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (report_id, "cached.pdf", digest,
                     json.dumps(cached, ensure_ascii=False), api.utc_now()),
                )
            response = client.post(
                "/api/review",
                files={"file": ("cached.pdf", content, "application/pdf")},
            )
            self.assertEqual(response.status_code, 200)
            events = [json.loads(line) for line in response.text.splitlines()]
            self.assertTrue(events[-1]["data"]["cached"])
            self.assertEqual(events[-1]["data"]["reportId"], report_id)

    def test_unaudited_result_is_not_published_or_saved(self):
        pipeline_result = {
            "evaluation_status": "success",
            "audit_passed": False,
            "stage_metrics": {},
            "error_summary": {},
        }
        with TestClient(api.app) as client, patch.object(api, "main_pipeline", return_value=pipeline_result):
            response = client.post(
                "/api/review",
                data={"force_refresh": "true"},
                files={"file": ("unaudited.pdf", b"%PDF-unaudited", "application/pdf")},
            )
            self.assertEqual(response.status_code, 200)
            events = [json.loads(line) for line in response.text.splitlines()]
            self.assertEqual(events[-1]["type"], "error")
            self.assertEqual(events[-1]["code"], "FINAL_AUDIT_INCOMPLETE")
            self.assertFalse(any(event.get("type") == "result" for event in events))
            with api.db() as connection:
                saved = connection.execute(
                    "SELECT COUNT(*) AS count FROM reports WHERE filename=?",
                    ("unaudited.pdf",),
                ).fetchone()["count"]
            self.assertEqual(saved, 0)

    def test_incomplete_evaluation_reports_failed_agents_and_skips_publish(self):
        pipeline_result = {
            "evaluation_status": "partial_failure",
            "audit_passed": False,
            "stage_metrics": {},
            "error": {
                "code": "EVALUATION_INCOMPLETE",
                "message": "五维评价未全部完成：理论增量与前瞻性评估引擎",
            },
            "error_summary": {
                "failed_agents": ["理论增量与前瞻性评估引擎"],
            },
        }
        with TestClient(api.app) as client, patch.object(
            api, "main_pipeline", return_value=pipeline_result
        ):
            response = client.post(
                "/api/review",
                data={"force_refresh": "true"},
                files={"file": ("partial.pdf", b"%PDF-partial", "application/pdf")},
            )
            self.assertEqual(response.status_code, 200)
            events = [json.loads(line) for line in response.text.splitlines()]
            self.assertEqual(events[-1]["type"], "error")
            self.assertEqual(events[-1]["code"], "EVALUATION_INCOMPLETE")
            self.assertEqual(
                events[-1]["failedAgents"], ["理论增量与前瞻性评估引擎"]
            )
            self.assertFalse(any(event.get("type") == "result" for event in events))
            with api.db() as connection:
                saved = connection.execute(
                    "SELECT COUNT(*) AS count FROM reports WHERE filename=?",
                    ("partial.pdf",),
                ).fetchone()["count"]
            self.assertEqual(saved, 0)

    def test_custom_openai_compatible_config_is_forwarded_unchanged(self):
        captured = {}

        def fake_pipeline(*args, **kwargs):
            captured.update(kwargs)
            return {
                "evaluation_status": "failed",
                "audit_passed": False,
                "stage_metrics": {},
                "error": {"code": "MODEL_SERVICE_UNAVAILABLE", "message": "测试结束"},
                "error_summary": {"failed_agents": [], "error_types": []},
            }

        with TestClient(api.app) as client, patch.object(
            api, "main_pipeline", side_effect=fake_pipeline
        ):
            response = client.post(
                "/api/review",
                data={
                    "force_refresh": "true",
                    "api_key": "custom-test-key",
                    "base_url": "https://api.example.com/openai/v1",
                    "model_name": "deepseek/deepseek-v4-pro",
                },
                files={"file": ("custom.pdf", b"%PDF-custom", "application/pdf")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["api_key"], "custom-test-key")
        self.assertEqual(captured["base_url"], "https://api.example.com/openai/v1")
        self.assertEqual(captured["model_name"], "deepseek/deepseek-v4-pro")


if __name__ == "__main__":
    unittest.main()
