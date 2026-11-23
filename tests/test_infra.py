"""Compile the deployment and guard the pilot's resource/authentication boundaries."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("az"), "Azure CLI is required for infrastructure tests")
class InfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        result = subprocess.run(
            ["az", "bicep", "build", "--file", str(ROOT / "infra" / "main.bicep"),
             "--stdout"],
            capture_output=True, text=True, check=True,
        )
        cls.template = json.loads(result.stdout)
        deployment = next(
            resource for resource in cls.template["resources"]
            if resource["type"] == "Microsoft.Resources/deployments"
        )
        cls.core = deployment["properties"]["template"]
        cls.resources = {
            resource["type"]: resource for resource in cls.core["resources"]
            if resource["type"] != "Microsoft.Authorization/roleAssignments"
        }

    def test_no_dedicated_grafana_compute_or_model(self) -> None:
        self.assertEqual(set(self.resources), {
            "Microsoft.OperationalInsights/workspaces",
            "Microsoft.Insights/components",
            "Microsoft.OperationalInsights/workspaces/tables",
            "Microsoft.Insights/dataCollectionRules",
            "Microsoft.Dashboard/dashboards",
            "Microsoft.Dashboard/dashboards/dashboardDefinitions",
        })
        dashboard = self.resources["Microsoft.Dashboard/dashboards"]
        self.assertNotIn("sku", dashboard)
        self.assertNotIn("identity", dashboard)

    def test_dashboard_definition_is_repo_backed_and_labels_missing_telemetry(self) -> None:
        definition = self.resources["Microsoft.Dashboard/dashboards/dashboardDefinitions"]
        self.assertIn("serializedData", definition["properties"])
        dashboard = json.loads((ROOT / "dashboards/copilot-pilot.json").read_text())
        self.assertIn("not collecting", dashboard["panels"][-1]["title"])
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                query = target["azureLogAnalytics"]["query"]
                self.assertIn("$__timeFilter(TimeGenerated)", query)
                self.assertIn("${project:singlequote}", query)
                self.assertNotIn("join kind=inner", query)

    def test_local_auth_disabled_and_assessments_have_separate_retention(self) -> None:
        workspace = self.resources["Microsoft.OperationalInsights/workspaces"]["properties"]
        self.assertTrue(workspace["features"]["disableLocalAuth"])
        self.assertFalse(workspace["features"]["enableLogAccessUsingOnlyResourcePermissions"])
        self.assertEqual(workspace["retentionInDays"], 30)
        self.assertTrue(
            self.resources["Microsoft.Insights/components"]["properties"]["DisableLocalAuth"]
        )
        table = self.resources["Microsoft.OperationalInsights/workspaces/tables"]["properties"]
        self.assertEqual(table["retentionInDays"], 90)
        self.assertEqual(table["totalRetentionInDays"], 90)

    def test_direct_ingestion_projects_explicit_columns_without_overflow(self) -> None:
        rule = self.resources["Microsoft.Insights/dataCollectionRules"]
        self.assertEqual(rule["kind"], "Direct")
        flow, = rule["properties"]["dataFlows"]
        self.assertFalse(flow["captureOverflow"])
        self.assertIn("source | project ", flow["transformKql"])
        self.assertNotIn("project-away", flow["transformKql"])
        columns = self.core["variables"]["assessmentColumns"]
        self.assertTrue(all(column["type"] != "dynamic" for column in columns))
        names = {column["name"] for column in columns}
        self.assertTrue({"TimeGenerated", "SessionEndedAt", "ProjectId", "Summary"} <= names)
        self.assertTrue({"Prompt", "Transcript", "Code", "WorkspacePath"}.isdisjoint(names))
        self.assertTrue(rule["dependsOn"])

    def test_roles_are_scoped_and_outputs_do_not_export_credentials(self) -> None:
        roles = [resource for resource in self.core["resources"]
                 if resource["type"] == "Microsoft.Authorization/roleAssignments"]
        self.assertEqual(len(roles), 4)
        for role in roles:
            self.assertIn("scope", role)
            self.assertNotEqual(role["scope"], "[subscription().id]")
            self.assertNotEqual(role["scope"], "[resourceGroup().id]")
            self.assertEqual(role["properties"]["principalType"], "User")
        self.assertNotIn("APPLICATIONINSIGHTS_CONNECTION_STRING", self.template["outputs"])
        self.assertIn("ASSESSMENT_LOGS_INGESTION_ENDPOINT", self.template["outputs"])
        self.assertIn("GRAFANA_DASHBOARD_RESOURCE_ID", self.template["outputs"])


if __name__ == "__main__":
    unittest.main()
