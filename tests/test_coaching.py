import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

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

    def test_model_schema_enforces_local_item_constraints(self) -> None:
        validator = Draft202012Validator(coaching.MODEL_SCHEMA)
        validator.check_schema(coaching.MODEL_SCHEMA)
        validator.validate({"items": ITEMS})
        boundary = [{**ITEMS[0], "title": "x" * 160, "detail": "x" * 400,
                     "repository": "x" * 100}] * coaching.MAX_ITEMS
        validator.validate({"items": boundary})
        coaching.ITEMS.validate(boundary)
        invalid = [
            ([ITEMS[0]] * (coaching.MAX_ITEMS + 1), "maxItems"),
            ([{**ITEMS[1], "repository": "Project label"}], "pattern"),
            ([{**ITEMS[0], "title": ""}], "minLength"),
            ([{**ITEMS[0], "title": "x" * 161}], "maxLength"),
            ([{**ITEMS[0], "detail": "x" * 401}], "maxLength"),
            ([{**ITEMS[1], "repository": "x" * 101}], "maxLength"),
        ]
        for items, rule in invalid:
            with self.subTest(rule=rule, count=len(items)):
                for validate, value in ((validator.validate, {"items": items}),
                                        (coaching.ITEMS.validate, items)):
                    with self.assertRaises(ValidationError) as caught:
                        validate(value)
                    self.assertEqual(caught.exception.validator, rule)

    def test_schema_failure_diagnostics_are_saved_without_rejected_content(self) -> None:
        rejected = "Private project label"
        result, calls = self.execute([{**ITEMS[1], "repository": rejected}])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"],
                         "Item schema validation failed at items[0].repository (pattern)")
        saved = self.root / "results" / f"{result['run_id']}.json"
        diagnostic = json.loads(saved.read_text())
        self.assertEqual(diagnostic["reason"], result["reason"])
        self.assertEqual(diagnostic["chronicle_ai_credits"], 17.0)
        self.assertNotIn(rejected, saved.read_text())
        self.assertNotIn("items", diagnostic)
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        self.assertFalse(any((self.root / "receipts").iterdir()))
        self.assertEqual(len(calls), 1)

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

    def test_azure_tls_configuration_is_scoped_and_honors_explicit_configuration(self) -> None:
        for configured in (None, "/custom/openssl.cnf"):
            with self.subTest(configured=configured), \
                    patch.dict(coaching.os.environ, {"PATH": "/usr/bin"}, clear=True), \
                    patch.object(coaching, "azure_rest", return_value={"ok": True}) as send:
                if configured:
                    coaching.os.environ["OPENSSL_CONF"] = configured
                before = dict(coaching.os.environ)
                self.assertEqual(coaching.rest("url", "resource", {"input": "report"}), {"ok": True})
                send.assert_called_once_with(
                    "url", "resource", {"input": "report"},
                    env={**before, "OPENSSL_CONF": configured or str(
                        coaching.ROOT / "tools" / "coaching-openssl.cnf")},
                )
                self.assertEqual(dict(coaching.os.environ), before)

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

    def test_zero_items_is_a_success_with_explicit_completion_and_no_empty_upload(self):
        self.config.update(status_endpoint=self.config["ingestion_endpoint"],
                           status_rule=self.config["rule"])
        result, calls = self.execute([])
        self.assertEqual((result["status"], result["items"]), ("published", 0))
        self.assertEqual(len(calls), 2)
        self.assertIn(coaching.STATUS_STREAM, calls[1][0])
        row, = calls[1][1]
        self.assertEqual((row["Status"], row["ExpectedItems"]), ("published", 0))
        from datetime import datetime
        self.assertEqual(row["RunDay"],
                         datetime.fromtimestamp(NOW, coaching.COACHING_ZONE).date().isoformat())

    def test_publication_failure_never_signals_success(self):
        self.config.update(status_endpoint=self.config["ingestion_endpoint"],
                           status_rule=self.config["rule"])
        calls = []

        def send(url, audience, body):
            calls.append((url, body))
            if audience == "https://ai.azure.com":
                return model_response(ITEMS)
            if coaching.STATUS_STREAM in url:
                return None
            raise RuntimeError("Upload failed")

        with coaching.locked(self.root) as db, \
                patch.object(coaching, "load_config", return_value=MODEL), \
                patch.object(coaching, "rest", side_effect=send):
            result = coaching.run(self.root, db, self.config, NOW,
                                  collect=lambda kind, *_: (REPORTS[kind], None))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(calls[-1][1][0]["Status"], "failed")

    def test_failed_completion_signal_preserves_publication_and_surfaces_warning(self):
        self.config.update(status_endpoint=self.config["ingestion_endpoint"],
                           status_rule=self.config["rule"])

        def send(url, audience, body):
            if audience == "https://ai.azure.com":
                return model_response(ITEMS)
            if coaching.STATUS_STREAM in url:
                raise RuntimeError("Signal unavailable")

        with coaching.locked(self.root) as db, \
                patch.object(coaching, "load_config", return_value=MODEL), \
                patch.object(coaching, "rest", side_effect=send):
            result = coaching.run(self.root, db, self.config, NOW,
                                  collect=lambda kind, *_: (REPORTS[kind], None))
        self.assertEqual(result["status"], "published")
        self.assertIn("pending", result["status_warning"])
        receipt = json.loads((self.root / "signals" / f"{result['run_id']}.json").read_text())
        self.assertEqual(receipt["status"], "pending")

    def test_monitor_install_uses_stable_snapshot_without_launching_paid_catch_up(self):
        import plistlib

        with coaching.locked(self.root):
            plist = self.root / "agent.plist"
            plist.write_bytes(plistlib.dumps({
                "ProgramArguments": ["/python", "-m", "tools.coaching", "--state-dir",
                                     str(self.root), "run", "--execute", "--scheduled"],
                "WorkingDirectory": "/old", "RunAtLoad": True,
                "StartCalendarInterval": {"Hour": 9, "Minute": 0}, "StartInterval": 3600,
            }))
            with patch.object(coaching, "PLIST", plist), \
                    patch.object(coaching.platform, "system", return_value="Darwin"), \
                    patch.object(coaching.subprocess, "run") as invoke:
                coaching.install_monitor(self.root, self.config,
                                         self.config["ingestion_endpoint"], self.config["rule"])
            installed = plistlib.loads(plist.read_bytes())
            self.assertFalse(installed["RunAtLoad"])
            self.assertEqual(installed["WorkingDirectory"], str(self.root / "runtime"))
            self.assertEqual(installed["StartInterval"], 3600)
            self.assertTrue((self.root / "runtime/tools/coaching.py").exists())
            self.assertFalse(any("--execute" in call.args[0] for call in invoke.call_args_list))

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
