import json
import tempfile
import unittest
from pathlib import Path

from tools.local_demo import build_demo, usage_records
from tools.project_attribution import project_identity


class LocalDemoTests(unittest.TestCase):
    def test_nested_folders_and_category_collisions(self) -> None:
        root = "/synthetic/projects"
        personal = project_identity(root + "/personal/sample/src", root)
        work = project_identity(root + "/msft/sample", root)
        self.assertEqual(personal["project.name"], work["project.name"])
        self.assertEqual(personal["project.id"], "personal/sample")
        self.assertEqual(work["project.id"], "msft/sample")
        self.assertNotIn(root, json.dumps(personal))

    def test_unsupported_layout_and_outside_root_remain_unattributed(self) -> None:
        root = "/synthetic/projects"
        for path in (root, root + "/personal", root + "/other/sample",
                     root + "-other/personal/sample", "/outside/sample"):
            with self.subTest(path=path):
                result = project_identity(path, root)
                self.assertEqual(result["attribution.status"], "unattributed")
        with self.assertRaises(ValueError):
            project_identity("relative/project", root)

    def test_symlink_resolves_to_canonical_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "personal" / "sample"
            project.mkdir(parents=True)
            link = root / "alias"
            link.symlink_to(project, target_is_directory=True)
            self.assertEqual(project_identity(str(link), str(root))["project.id"],
                             "personal/sample")

    def test_replays_deduplicate_and_conflicts_fail(self) -> None:
        record = {"event_id": "u1", "workspace": "/synthetic/projects/personal/sample",
                  "input_tokens": 10, "output_tokens": 2}
        rows = usage_records([record, record], "/synthetic/projects")
        self.assertEqual(len(rows), 1)
        self.assertNotIn("workspace", rows[0])
        with self.assertRaises(ValueError):
            usage_records([record, {**record, "input_tokens": 11}], "/synthetic/projects")
        for invalid in (-1, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                usage_records([{**record, "input_tokens": invalid}], "/synthetic/projects")

    def test_demo_reconciles_and_excludes_raw_paths_and_transcripts(self) -> None:
        result = build_demo("/synthetic/projects")
        self.assertEqual(result["totals"],
                         {"input_tokens": 325, "output_tokens": 55,
                          "unattributed_records": 1})
        self.assertEqual(result["telemetry_unique_records"], 3)
        self.assertEqual(result["telemetry_received_including_replay"], 4)
        projects = {row["project_id"]: row for row in result["projects"]}
        self.assertEqual(projects["personal/sample"]["input_tokens"], 100)
        self.assertEqual(projects["msft/sample"]["input_tokens"], 200)
        self.assertEqual(len(projects["personal/sample"]["assessments"]), 1)
        self.assertEqual(projects["msft/sample"]["assessments"], [])
        self.assertFalse(result["model_executed"])
        self.assertFalse(result["real_telemetry_received"])
        encoded = json.dumps(result)
        self.assertNotIn("/synthetic/projects", encoded)
        self.assertNotIn("Fix the parser and verify the whole service.", encoded)


if __name__ == "__main__":
    unittest.main()
