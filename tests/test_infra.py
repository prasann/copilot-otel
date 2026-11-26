"""Compile the deployment and guard the pilot's resource/authentication boundaries."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dashboard = json.loads((ROOT / "dashboards/copilot-pilot.json").read_text())
        cls.panels = [
            child
            for panel in cls.dashboard["panels"]
            for child in ([panel] + panel.get("panels", []))
        ]

    def test_overview_keeps_details_collapsed_and_layout_non_overlapping(self) -> None:
        overview = self.dashboard["panels"]
        self.assertEqual(sum(panel["type"] == "stat" for panel in overview), 5)
        self.assertTrue({"timeseries", "table"} <= {
            panel["type"] for panel in overview
        })
        self.assertEqual(len({panel["id"] for panel in self.panels}), len(self.panels))
        for panel in self.panels:
            if panel["type"] == "row":
                self.assertTrue(panel["collapsed"])
        for group in [overview] + [
            panel["panels"] for panel in overview if panel["type"] == "row"
        ]:
            for index, panel in enumerate(group):
                position = panel["gridPos"]
                self.assertGreater(position["w"], 0)
                self.assertGreater(position["h"], 0)
                self.assertGreaterEqual(position["x"], 0)
                self.assertLessEqual(position["x"] + position["w"], 24)
                for other in group[index + 1:]:
                    bounds = other["gridPos"]
                    self.assertTrue(
                        position["x"] + position["w"] <= bounds["x"]
                        or bounds["x"] + bounds["w"] <= position["x"]
                        or position["y"] + position["h"] <= bounds["y"]
                        or bounds["y"] + bounds["h"] <= position["y"],
                        f"Panels {panel['id']} and {other['id']} overlap",
                    )

    def test_all_queries_preserve_filtering_and_deduplication(self) -> None:
        for panel in self.panels:
            for target in panel.get("targets", []):
                with self.subTest(panel=panel["id"]):
                    source = target["azureLogAnalytics"]
                    query = source["query"]
                    self.assertEqual(source["resources"], ["__WORKSPACE_RESOURCE_ID__"])
                    self.assertFalse(source["dashboardTime"])
                    self.assertIn("$__timeFilter(TimeGenerated)", query)
                    if query.startswith("OTelSpans"):
                        self.assertIn("${project:singlequote}", query)
                        self.assertIn("ago(30d)", query)
                        self.assertIn(
                            "tostring(Attributes['telemetry.origin']) == 'unverified_client'",
                            query,
                        )
                        deduplication = "summarize arg_max(TimeGenerated, *) by TraceId, SpanId"
                        if "sum(" in query:
                            chat_filter = "where tostring(Attributes['gen_ai.operation.name']) == 'chat'"
                            self.assertIn(chat_filter, query)
                            self.assertLess(query.index(deduplication), query.index(chat_filter))
                            self.assertLess(query.index(chat_filter), query.index("sum("))
                    else:
                        self.assertRegex(query, r"^(let rows = )?CopilotInsights_CL\n")
                        self.assertIn("ago(90d)", query)
                        deduplication = "summarize arg_max(TimeGenerated, *) by RunId, Kind, Rank"
                    self.assertLess(query.index(deduplication), query.index("$__timeFilter"))
                    if source["resultFormat"] == "time_series":
                        self.assertEqual(source["timeColumn"], "TimeGenerated")
                        self.assertIn("by bin(TimeGenerated, $__interval)", query)
                        self.assertTrue(query.endswith("| order by TimeGenerated asc"))

    def test_usage_panels_keep_missing_reporting_distinct_from_zero(self) -> None:
        panels = {panel["id"]: panel for panel in self.panels}
        for panel_id, fields in (
            (6, ("InputTokens",)), (7, ("OutputTokens",)), (8, ("CacheReadTokens",)),
            (10, ("InputTokens", "OutputTokens", "CacheReadTokens")),
            (2, ("InputTokens", "OutputTokens", "CacheReadTokens")),
            (11, ("InputTokens", "OutputTokens", "CacheReadTokens")),
        ):
            query = panels[panel_id]["targets"][0]["azureLogAnalytics"]["query"]
            for field in fields:
                self.assertIn(
                    f"{field}=iff(countif(isnotnull({field})) > 0, sum({field}), long(null))",
                    query,
                )
            if panel_id in (2, 11):
                self.assertIn(
                    "MissingUsage=countif(isnull(InputTokens) or isnull(OutputTokens))", query,
                )
                self.assertIn("MissingCache=countif(isnull(CacheReadTokens))", query)
        project_query = panels[2]["targets"][0]["azureLogAnalytics"]["query"]
        self.assertIn("by ProjectId", project_query)
        self.assertNotIn("!= 'unattributed'", project_query)
        self.assertIn("by Model", panels[11]["targets"][0]["azureLogAnalytics"]["query"])
        self.assertTrue(any(
            child["id"] == 5 for panel in self.dashboard["panels"]
            if panel["type"] == "row" and panel["collapsed"] for child in panel["panels"]
        ))

    def test_coaching_section_is_visible_and_reads_only_derived_fields(self) -> None:
        overview = {panel["id"]: panel for panel in self.dashboard["panels"]}
        self.assertTrue({20, 21, 22, 23, 24} <= overview.keys())
        for panel_id in (22, 24):
            query = overview[panel_id]["targets"][0]["azureLogAnalytics"]["query"]
            self.assertIn("arg_max(TimeGenerated, RunId)", query)
        self.assertIn("Kind == 'standup'", overview[24]["targets"][0]["azureLogAnalytics"]["query"])

    def test_source_filter_preserves_legacy_unknown_and_span_deduplication(self) -> None:
        variables = {item["name"]: item for item in self.dashboard["templating"]["list"]}
        self.assertEqual(variables["source"]["current"]["value"], "*")
        self.assertEqual({item["value"] for item in variables["source"]["options"]},
                         {"*", "vscode", "desktop", "unknown"})
        for panel in self.panels:
            for target in panel.get("targets", []):
                query = target["azureLogAnalytics"]["query"]
                with self.subTest(panel=panel["id"]):
                    if not query.startswith("OTelSpans"):
                        self.assertNotIn("${source:", query)
                        continue
                    dedup = "summarize arg_max(TimeGenerated, *) by TraceId, SpanId"
                    self.assertIn(dedup + "\n", query)
                    self.assertIn(
                        "TelemetrySource=iff(RawSource in ('vscode', 'desktop'), RawSource, 'unknown')",
                        query,
                    )
                    source_filter = ("where ${source:singlequote} == '*' "
                                     "or TelemetrySource == ${source:singlequote}")
                    self.assertIn(source_filter, query)
                    self.assertLess(query.index(dedup), query.index(source_filter))
                    if "sum(" in query:
                        self.assertLess(query.index(source_filter), query.index("sum("))
        panels = {item["id"]: item for item in self.panels}
        self.assertIn("ProjectId, TelemetrySource,",
                      panels[5]["targets"][0]["azureLogAnalytics"]["query"])
        comparison = panels[25]["targets"][0]["azureLogAnalytics"]["query"]
        self.assertIn("by TelemetrySource", comparison)
        self.assertIn("InputTokens=iff(countif(isnotnull(InputTokens)) > 0", comparison)
        self.assertTrue(any(
            child["id"] == 25 for panel in self.dashboard["panels"]
            if panel["type"] == "row" and panel["collapsed"] for child in panel["panels"]
        ))

    def test_latest_coaching_has_priority_and_full_width_reading_space(self) -> None:
        panels = {panel["id"]: panel for panel in self.dashboard["panels"]}
        tips, standup, theme, trend = (panels[item]["gridPos"] for item in (22, 24, 21, 23))
        for report in (tips, standup):
            self.assertEqual((report["x"], report["w"]), (0, 24))
            self.assertGreaterEqual(report["h"], 12)
        self.assertLessEqual(tips["y"] + tips["h"], standup["y"])
        self.assertLessEqual(standup["y"] + standup["h"], theme["y"])
        self.assertEqual(theme["y"], trend["y"])
        self.assertLessEqual(theme["w"] * theme["h"], 24)
        for panel_id in (2, 10, 11):
            self.assertGreaterEqual(panels[panel_id]["gridPos"]["y"],
                                    trend["y"] + trend["h"])

    def test_coaching_tables_leave_detail_width_flexible(self) -> None:
        panels = {panel["id"]: panel for panel in self.dashboard["panels"]}
        for panel_id, metadata_fields in (
            (22, {"Rank", "Theme", "Tip", "Generated"}),
            (24, {"Status", "Repository", "Item"}),
        ):
            with self.subTest(panel=panel_id):
                config = panels[panel_id]["fieldConfig"]
                self.assertTrue(config["defaults"]["custom"]["wrapText"])
                widths = {
                    override["matcher"]["options"]: prop["value"]
                    for override in config["overrides"]
                    if override["matcher"]["id"] == "byName"
                    for prop in override["properties"]
                    if prop["id"] == "custom.width"
                }
                self.assertEqual(set(widths), metadata_fields)
                self.assertTrue(all(0 < width <= 280 for width in widths.values()))
                self.assertLessEqual(sum(widths.values()), 660)

    def test_aiu_estimate_counts_reported_roots_without_inventing_missing_usage(self) -> None:
        panels = {panel["id"]: panel for panel in self.panels}
        panel = panels[26]
        query = panel["targets"][0]["azureLogAnalytics"]["query"]
        operation = "where tostring(Attributes['gen_ai.operation.name']) == 'invoke_agent'"
        self.assertIn(operation, query)
        self.assertIn("IsRoot=tostring(Attributes['accounting.scope']) == 'top_level'", query)
        self.assertLess(query.index(operation), query.index("| summarize EligibleRoots="))
        self.assertIn("EligibleRoots=countif(IsRoot)", query)
        self.assertIn("RootsReportingAIUs=countif(IsRoot and isnotnull(NanoAIU))", query)
        self.assertIn("MissingAIURoots=countif(IsRoot and isnull(NanoAIU))", query)
        self.assertIn("UnclassifiedInvocations=countif(not(IsRoot))", query)
        self.assertIn("sumif(NanoAIU, IsRoot and isnotnull(NanoAIU))", query)
        self.assertIn("isfinite(RawNanoAIU) and RawNanoAIU >= 0", query)
        self.assertIn(
            "iff(RootsReportingAIUs > 0 and isfinite(ReportedNanoAIU), "
            "ReportedNanoAIU / 1000000000.0, real(null))", query,
        )
        self.assertIn("['Estimated usage value (USD)']=ObservedAIUs * 0.01", query)
        self.assertNotIn("github.copilot.cost", query)
        self.assertNotIn("coalesce(", query)
        self.assertNotIn("!= 'unattributed'", query)
        self.assertEqual(panel["fieldConfig"]["defaults"]["noValue"], "Unavailable")
        note = panels[27]["options"]["content"]
        self.assertIn("Assumes 1 AIU approximately equals 1 credit; not billed spend", note)
        self.assertIn("not a verified AIU-to-credit conversion", note)
        self.assertIn("Excludes allowances, discounts", note)


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
            "Microsoft.Insights/dataCollectionEndpoints",
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
        panels = [
            child for panel in dashboard["panels"]
            for child in ([panel] + panel.get("panels", []))
        ]
        missing_telemetry = next(panel for panel in panels if panel["id"] == 4)
        self.assertIn("limits", missing_telemetry["title"])
        for panel in panels:
            for target in panel.get("targets", []):
                query = target["azureLogAnalytics"]["query"]
                self.assertIn("$__timeFilter(TimeGenerated)", query)
                if "OTelSpans" in query:
                    self.assertIn("${project:singlequote}", query)
                self.assertNotIn("join kind=inner", query)

    def test_local_auth_disabled_and_insights_have_separate_retention(self) -> None:
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
        rule = next(resource for resource in self.core["resources"]
                    if resource["type"] == "Microsoft.Insights/dataCollectionRules"
                    and resource.get("kind") == "Direct")
        self.assertEqual(rule["kind"], "Direct")
        flow, = rule["properties"]["dataFlows"]
        self.assertFalse(flow["captureOverflow"])
        self.assertIn("source | project ", flow["transformKql"])
        self.assertNotIn("project-away", flow["transformKql"])
        columns = self.core["variables"]["insightColumns"]
        self.assertTrue(all(column["type"] != "dynamic" for column in columns))
        names = {column["name"] for column in columns}
        self.assertTrue({"TimeGenerated", "RunId", "Kind", "Category", "Title"} <= names)
        self.assertTrue({"Prompt", "Transcript", "Code", "WorkspacePath"}.isdisjoint(names))
        self.assertTrue(rule["dependsOn"])

    def test_roles_are_scoped_and_outputs_do_not_export_credentials(self) -> None:
        roles = [resource for resource in self.core["resources"]
                 if resource["type"] == "Microsoft.Authorization/roleAssignments"]
        self.assertEqual(len(roles), 5)
        for role in roles:
            self.assertIn("scope", role)
            self.assertNotEqual(role["scope"], "[subscription().id]")
            self.assertNotEqual(role["scope"], "[resourceGroup().id]")
            self.assertEqual(role["properties"]["principalType"], "User")
        self.assertNotIn("APPLICATIONINSIGHTS_CONNECTION_STRING", self.template["outputs"])
        self.assertIn("INSIGHTS_LOGS_INGESTION_ENDPOINT", self.template["outputs"])
        self.assertIn("GRAFANA_DASHBOARD_RESOURCE_ID", self.template["outputs"])


if __name__ == "__main__":
    unittest.main()
