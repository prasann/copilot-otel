import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import azure_pilot as pilot
from tests import test_assessment_spike


class AzurePilotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "ASSESSMENT_AZURE_AUTH": "azure_cli",
            "AZURE_OPENAI_ENDPOINT": "https://example.services.ai.azure.com",
            "AZURE_OPENAI_DEPLOYMENT": "test",
            "ASSESSMENT_MODEL_ID": "test-model",
            "ASSESSMENT_MODEL_VERSION": "test-version",
            "ASSESSMENT_ALLOW_SYNTHETIC_INFERENCE": "true",
            "ASSESSMENT_MAX_REQUESTS": "1",
            "ASSESSMENT_MAX_OUTPUT_TOKENS": "2000",
        }

    def test_config_rejects_non_origin_and_foreign_host(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            for endpoint in ("https://example.com", "http://example.openai.azure.com",
                             "https://example.openai.azure.com/openai",
                             "https://example.openai.azure.com?token=bad",
                             "https://user@example.openai.azure.com"):
                with self.subTest(endpoint=endpoint):
                    config = {**self.config, "AZURE_OPENAI_ENDPOINT": endpoint}
                    path.write_text("\n".join(f"{key}={value}" for key, value in config.items()))
                    with self.assertRaises(ValueError):
                        pilot.load_config(path)

    def test_failed_attempt_uses_budget_and_does_not_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(pilot, "rest", side_effect=RuntimeError("unauthorized")) as send:
                with self.assertRaisesRegex(RuntimeError, "unauthorized"):
                    pilot.assess(self.config, "narrow-test-evidence", Path(directory))
                with self.assertRaisesRegex(ValueError, "budget exhausted"):
                    pilot.assess(self.config, "narrow-test-evidence", Path(directory))
                self.assertEqual(send.call_count, 1)
                body = send.call_args.args[2]
                self.assertFalse(body["store"])
                self.assertNotIn("reference", json.loads(body["input"])["session"])

    def test_disabled_gate_makes_no_request(self) -> None:
        with patch.object(pilot, "rest") as send:
            with self.assertRaises(ValueError):
                pilot.assess({**self.config, "ASSESSMENT_ALLOW_SYNTHETIC_INFERENCE": "false"},
                             "narrow-test-evidence", Path("/unused"))
            send.assert_not_called()

    def test_incomplete_or_refused_response_fails(self) -> None:
        for response in (
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
            {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "refusal", "refusal": "no"}]}]},
        ):
            with self.subTest(response=response), self.assertRaises(ValueError):
                pilot.response_assessment(response)

    def test_publisher_does_not_copy_model_prose_or_transcripts(self) -> None:
        fixture = test_assessment_spike.AssessmentSpikeTests()
        fixture.setUp()
        assessment = copy.deepcopy(fixture.assessment)
        assessment["model"]["provider"] = "azure"
        assessment["segments"][0]["claims"][0]["text"] = "MODEL PROSE MUST STAY LOCAL"
        run = {"origin": "synthetic_azure_model_smoke", "case": "narrow-test-evidence",
               "requested_model": "test-model", "assessed_at": "2026-11-23T07:00:00+00:00",
               "assessment": assessment}
        record = pilot.assessment_record(run, "Synthetic check found limited verification.")
        self.assertNotIn("MODEL PROSE MUST STAY LOCAL", json.dumps(record))
        self.assertNotIn("events", record)
        self.assertEqual(record["ProjectId"], "synthetic/demo")
        with self.assertRaises(ValueError):
            pilot.assessment_record(run, fixture.session["events"][0]["text"])
        with self.assertRaises(ValueError):
            pilot.assessment_record({**run, "origin": "real"}, "Not allowed.")

    def test_query_requires_matching_stored_fields_and_never_republishes(self) -> None:
        record = {"AssessmentId": "386c89a7-ce2b-429d-a6c8-f3b6a4d12997",
                  "TimeGenerated": "2026-11-23T07:00:00+00:00",
                  "SessionEndedAt": "2026-11-23T07:00:00+00:00",
                  "ProjectId": "synthetic/demo", "Summary": "Reviewed summary"}
        stored = {**record, "TimeGenerated": "2026-11-23T07:00:00Z", "Type": "test"}
        response = {"tables": [{"columns": [{"name": key} for key in stored],
                                "rows": [list(stored.values())]}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_text(json.dumps({"status": "accepted", "record": record}))
            with patch.object(pilot, "azd_value",
                              return_value="00000000-0000-0000-0000-000000000001"), \
                    patch.object(pilot, "rest", return_value=response):
                result = pilot.query(path)
                self.assertEqual(result["status"], "query_verified")
                response["tables"][0]["rows"][0][4] = "Different stored summary"
                with self.assertRaisesRegex(ValueError, "field differs"):
                    pilot.query(path)


if __name__ == "__main__":
    unittest.main()
