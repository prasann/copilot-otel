import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import coaching


NOW = 1_800_000_000.0
MODEL = {
    "COACHING_AZURE_AUTH": "azure_cli",
    "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
    "AZURE_OPENAI_DEPLOYMENT": "test-deployment",
    "COACHING_MODEL_ID": "test-model",
}
REPORTS = {"tips": "- Define done as an observable result.", "standup": "- Shipped the dashboard."}
ITEMS = [
    {"kind": "tip", "category": "verification", "title": "Define done as an observable result.",
     "detail": "Gateway health passed before Azure attribution was confirmed.", "repository": ""},
    {"kind": "standup", "category": "completed", "title": "Deployed the token dashboard.",
     "detail": "", "repository": "prasann/copilot-otel"},
]


def model_response(items: list[dict]) -> dict:
    return {"status": "completed", "usage": {"input_tokens": 1}, "output": [{
        "type": "message", "content": [{"type": "output_text", "text": json.dumps({"items": items})}],
    }]}


class CoachingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "state"
        self.config = {
            "version": 1, "enabled": True, "env_file": "/unused/.env", "copilot": "/bin/copilot",
            "ingestion_endpoint": "https://example.ingest.monitor.azure.com",
            "rule": "dcr-" + "0" * 32, "workspace": "00000000-0000-0000-0000-000000000000",
        }

    def execute(self, items=ITEMS, reports=REPORTS, now=NOW, **options) -> tuple[dict, list]:
        calls = []

        def send(url, audience, body):
            calls.append((url, body))
            return model_response(items) if audience == "https://ai.azure.com" else None

        with coaching.locked(self.root) as db, \
                patch.object(coaching, "load_config", return_value=MODEL), \
                patch.object(coaching, "rest", side_effect=send):
            result = coaching.run(self.root, db, self.config, now,
                                  collect=lambda kind, *_: (reports[kind], 8.5), **options)
        return result, calls

    def test_only_chronicle_reports_reach_the_model_and_validated_rows_are_published(self) -> None:
        result, calls = self.execute()
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["chronicle_ai_credits"], 17.0)
        (model_url, body), (ingest_url, rows) = calls
        self.assertTrue(model_url.endswith("/openai/v1/responses"))
        self.assertEqual(json.loads(body["input"]), {"reports": REPORTS})
        self.assertFalse(body["store"])
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertIn("/streams/Custom-CopilotInsightInput", ingest_url)
        self.assertEqual([(row["Kind"], row["Rank"], row["Category"]) for row in rows],
                         [("tip", 1, "verification"), ("standup", 1, "completed")])
        self.assertEqual(set(rows[0]), {
            "TimeGenerated", "SchemaVersion", "RunId", "Kind", "Rank", "Category", "Title",
            "Detail", "Repository", "Source", "ModelId",
        })
        report = self.root / "reports" / f"{result['run_id']}-tips.md"
        self.assertEqual(report.stat().st_mode & 0o777, 0o600)

    def test_invalid_items_are_never_published_and_the_day_is_not_retried(self) -> None:
        for field, value in (("detail", "See /Users/me/project/app.py"),
                             ("title", "Rotate ghp_" + "a" * 30),
                             ("category", "completed"),
                             ("repository", "https://github.com/a/b")):
            with self.subTest(field=field):
                self.root = self.root.parent / f"state-{field}"
                result, calls = self.execute([{**ITEMS[0], field: value}])
                self.assertEqual(result["status"], "failed")
                self.assertNotIn(value, json.dumps(result))
                self.assertEqual(len(calls), 1)
                with self.assertRaisesRegex(coaching.CoachingError, "Already attempted"):
                    self.execute()
                self.assertEqual(self.execute(scheduled=True)[0]["status"], "not_due")
                self.assertEqual(self.execute(retry=True)[0]["status"], "published")

    def test_secret_in_chronicle_report_stops_before_the_model(self) -> None:
        secret = "-----BEGIN RSA PRIVATE KEY-----"
        completed = subprocess.CompletedProcess([], 0, stdout=secret, stderr="")
        with patch.object(coaching.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(coaching.CoachingError, "possible_secret"):
                coaching.chronicle("tips", self.root, self.config)

    def test_chronicle_runs_read_only_capped_and_unsynced(self) -> None:
        self.root.mkdir(parents=True)
        (self.root / "cwd").mkdir()
        completed = subprocess.CompletedProcess([], 0, stdout="- A tip.\n", stderr="")
        with patch.object(coaching.subprocess, "run", return_value=completed) as invoke, \
                patch.dict(coaching.os.environ, {"COPILOT_ALLOW_ALL": "true"}):
            self.assertEqual(coaching.chronicle("standup", self.root, self.config), ("- A tip.", None))
        arguments = invoke.call_args.args[0]
        self.assertEqual(arguments[:3], ["/bin/copilot", "-p", "/chronicle standup"])
        for flag in ("-s", "--sync-session=off", "--no-custom-instructions", "--no-ask-user"):
            self.assertIn(flag, arguments)
        self.assertEqual(arguments[arguments.index("--max-ai-credits") + 1], "30")
        self.assertFalse(any(argument.startswith(("--allow-all", "--yolo")) for argument in arguments))
        self.assertNotIn("COPILOT_ALLOW_ALL", invoke.call_args.kwargs["env"])

    def test_failed_upload_is_republished_from_receipt_without_rerunning_chronicle(self) -> None:
        def send(url, audience, body):
            if audience == "https://ai.azure.com":
                return model_response(ITEMS)
            raise RuntimeError("InvalidStream")

        with coaching.locked(self.root) as db, \
                patch.object(coaching, "load_config", return_value=MODEL), \
                patch.object(coaching, "rest", side_effect=send):
            failed = coaching.run(self.root, db, self.config, NOW,
                                  collect=lambda kind, *_: (REPORTS[kind], None))
        self.assertEqual(failed["status"], "failed")
        with coaching.locked(self.root) as db, patch.object(coaching, "rest") as upload:
            result = coaching.republish(self.root, db, self.config, failed["run_id"])
            self.assertEqual(result["items"], 2)
            self.assertEqual(len(upload.call_args.args[2]), 2)
            with self.assertRaisesRegex(coaching.CoachingError, "already accepted"):
                coaching.republish(self.root, db, self.config, failed["run_id"])
            self.assertEqual(db.execute("SELECT status FROM runs").fetchone()[0], "published")

    def test_scheduled_run_waits_for_nine_and_runs_once_per_day(self) -> None:
        self.config["schedule_after"] = NOW + coaching.DAY
        self.assertEqual(self.execute(scheduled=True)[0]["status"], "not_due")
        from datetime import datetime
        morning = datetime.fromtimestamp(NOW + 2 * coaching.DAY).replace(hour=9, minute=5).timestamp()
        self.assertEqual(self.execute(scheduled=True, now=morning - 3600)[0]["status"], "not_due")
        self.assertEqual(self.execute(scheduled=True, now=morning)[0]["status"], "published")
        self.assertEqual(self.execute(scheduled=True, now=morning + 3600)[0]["status"], "not_due")


if __name__ == "__main__":
    unittest.main()
