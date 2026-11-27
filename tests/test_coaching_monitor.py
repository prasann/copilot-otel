import json
import os
import subprocess
import unittest
from pathlib import Path

from jsonschema import validate
from jsonschema.exceptions import ValidationError


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = json.loads((ROOT / "monitoring/workflow.json").read_text())
QUERY = (ROOT / "monitoring/status.kql").read_text()
NEW = WORKFLOW["actions"]["Due"]["actions"]["New_email"]["actions"]


class MonitorTests(unittest.TestCase):
    def test_schedule_is_daily_1500_ist_not_machine_time(self):
        recurrence = WORKFLOW["triggers"]["Daily_1500_IST"]["recurrence"]
        self.assertEqual((recurrence["frequency"], recurrence["interval"]), ("Day", 1))
        self.assertEqual(recurrence["timeZone"], "India Standard Time")
        self.assertEqual(recurrence["schedule"], {"hours": [15], "minutes": [0]})
        for action in ("Day", "Start", "Deadline"):
            self.assertIn("India Standard Time", WORKFLOW["actions"][action]["inputs"])
        self.assertIn("T15:00:00", WORKFLOW["actions"]["Deadline"]["inputs"])

    def test_partial_failed_and_unexpected_queries_cannot_claim_success(self):
        schema = NEW["Evaluate"]["actions"]["Validate"]["inputs"]["schema"]
        valid = {"tables": [{"columns": [{"name": "Outcome", "type": "string"}],
                             "rows": [["received"]]}]}
        validate(valid, schema)
        for response in ({}, {"tables": []}, {**valid, "error": {"code": "PartialError"}},
                         {"tables": [{"columns": [], "rows": [["received"]]}]},
                         {"tables": [{"columns": valid["tables"][0]["columns"],
                                      "rows": [["unexpected"]]}]}):
            with self.subTest(response=response), self.assertRaises(ValidationError):
                validate(response, schema)
        self.assertEqual(WORKFLOW["actions"]["Outcome"]["inputs"]["variables"][0]["value"], "unknown")
        self.assertEqual(NEW["Email"]["runAfter"]["Evaluate"], ["Succeeded", "Failed", "TimedOut"])

    def test_duplicate_scheduler_delivery_uses_existing_operation_and_serial_execution(self):
        self.assertEqual(WORKFLOW["triggers"]["Daily_1500_IST"]["runtimeConfiguration"]
                         ["concurrency"]["runs"], 1)
        actions = WORKFLOW["actions"]["Due"]["actions"]
        self.assertNotIn("guid(", actions["Operation"]["inputs"])
        self.assertIn("outputs('Day')", actions["Operation"]["inputs"])
        self.assertEqual(actions["New_email"]["runAfter"], {"Operation": ["Succeeded"]})
        self.assertEqual(NEW["Wait_for_send"]["runAfter"]["Email"],
                         ["Succeeded", "Failed", "TimedOut"])
        self.assertEqual(NEW["Email"]["inputs"]["headers"]["Operation-Id"], "@outputs('Operation')")
        self.assertIn("Succeeded", NEW["Confirm_sent"]["expression"])

    def test_email_address_has_no_default_and_runtime_history_is_private(self):
        self.assertEqual(WORKFLOW["parameters"]["recipient"], {"type": "SecureString"})
        self.assertEqual(NEW["Email"]["runtimeConfiguration"]["secureData"]["properties"],
                         ["inputs", "outputs"])
        self.assertIn("[TEST]", NEW["Email"]["inputs"]["body"]["content"]["subject"])
        self.assertIn("0001", WORKFLOW["actions"]["Due"]["actions"]["Operation"]["inputs"])

    def test_infrastructure_compiles_without_compute_storage_or_connections(self):
        result = subprocess.run(["az", "bicep", "build", "--file",
                                 str(ROOT / "infra/coaching-monitor.bicep"), "--stdout"],
                                capture_output=True, text=True, check=True)
        template = json.loads(result.stdout)
        self.assertEqual(template["parameters"]["recipient"]["type"], "securestring")
        self.assertNotIn("defaultValue", template["parameters"]["recipient"])
        self.assertEqual(template["parameters"]["firstRun"]["defaultValue"],
                         "[utcNow('yyyy-MM-ddT00:00:00')]")
        types = {resource["type"] for resource in template["resources"]}
        self.assertIn("Microsoft.Logic/workflows", types)
        self.assertTrue(types.isdisjoint({"Microsoft.Web/sites", "Microsoft.Web/serverfarms",
                                         "Microsoft.Storage/storageAccounts",
                                         "Microsoft.Web/connections"}))


@unittest.skipUnless(os.environ.get("COACHING_MONITOR_WORKSPACE"),
                     "Set COACHING_MONITOR_WORKSPACE for read-only live KQL tests")
class LiveQueryTests(unittest.TestCase):
    def test_deadline_outcomes_with_synthetic_content_free_rows(self):
        from tools.coaching import rest

        items = "datatable(TimeGenerated:datetime,RunId:string,Kind:string,Rank:int)"
        runs = "datatable(TimeGenerated:datetime,RunId:string,RunDay:string,Status:string,ExpectedItems:int)"
        success = "[datetime(2026-11-27T09:00:00Z),'run','2026-11-27','published',0]"
        cases = [
            ("no-run", "[]", "[]", "missing/failed"),
            ("failed", "[]", success.replace("'published'", "'failed'"), "missing/failed"),
            ("success-zero", "[]", success, "received"),
            ("missing-publication", "[]", success.replace(",0]", ",1]"), "missing/failed"),
            ("success-deduplicated",
             "[datetime(2026-11-27T09:00:00Z),'run','tip',1,"
             "datetime(2026-11-27T09:00:00Z),'run','tip',1]",
             success.replace(",0]", ",1]"), "received"),
            ("wrong-local-day", "[]", success.replace("'2026-11-27'", "'2026-11-26'"), "missing/failed"),
            ("late-completion", "[]", success.replace("T09:00:00", "T09:31:00"), "missing/failed"),
            ("previous-local-day", "[]", success.replace("27T09:00", "26T18:29"), "missing/failed"),
        ]
        for name, itemrows, runrows, expected in cases:
            with self.subTest(case=name):
                query = QUERY.replace("__START__", "2026-11-26T18:30:00Z").replace(
                    "__DEADLINE__", "2026-11-27T09:30:00Z").replace("__DAY__", "2026-11-27")
                query = query.replace("CopilotInsights_CL", items + itemrows).replace(
                    "CopilotCoachingRuns_CL", runs + runrows)
                response = rest(
                    f"https://api.loganalytics.azure.com/v1/workspaces/"
                    f"{os.environ['COACHING_MONITOR_WORKSPACE']}/query",
                    "https://api.loganalytics.io", {"query": query},
                )
                self.assertEqual(response["tables"][0]["rows"], [[expected]])


if __name__ == "__main__":
    unittest.main()
