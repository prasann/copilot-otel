import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from jsonschema.exceptions import ValidationError

from tools import assessment_spike as spike


class AssessmentSpikeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = spike.load_case("narrow-test-evidence")
        self.assessment = {
            "schema_version": 1,
            "rubric_version": "s4-v1",
            "session_id": self.session["id"],
            "revision": self.session["revision"],
            "snapshot_sha256": spike.session_digest(self.session),
            "model": {"provider": "synthetic", "id": "test-model"},
            "segments": [{
                "id": "parser",
                "task_type": "debugging",
                "event_ids": ["a1", "a2", "a3"],
                "outcome": "partial",
                "claims": [{
                    "kind": "observation",
                    "text": "Parser unit tests passed; service tests were not run.",
                    "evidence_ids": ["a2"],
                    "confidence": "high",
                }],
            }],
            "limitations": ["The service goal was not fully verified."],
            "unknown_dimensions": ["time_saved", "satisfaction", "understanding"],
            "recommendations": [{
                "text": "Verify the remaining service behavior.",
                "evidence_ids": ["a1", "a2"],
            }],
        }

    def validate(self, assessment: dict) -> None:
        spike.validate_assessment(assessment, self.session, "synthetic", "test-model")

    def test_prepare_strips_reference_answers_for_all_development_cases(self) -> None:
        suite = spike.read_json(spike.SPEC / "cases.json")
        for case in suite["cases"]:
            with self.subTest(case=case["id"]):
                prepared = spike.prepare(spike.load_case(case["id"]), "synthetic", "test")
                self.assertNotIn("reference", prepared["session"])
                self.assertNotIn("forbidden_claims", json.dumps(prepared))
                self.assertEqual(prepared["session"]["events"], case["events"])
                self.assertNotIn(case["reference"]["expected"], json.dumps(prepared))

    def test_evaluation_inputs_are_separate_and_exclude_answer_keys(self) -> None:
        development = spike.load_suite("development")
        evaluation = spike.load_suite("evaluation")
        self.assertTrue(
            {case["id"] for case in development["cases"]}.isdisjoint(
                case["id"] for case in evaluation["cases"]
            )
        )
        for case in evaluation["cases"]:
            with self.subTest(case=case["id"]):
                prepared = spike.prepare(
                    spike.load_case(case["id"], "evaluation"), "synthetic", "test"
                )
                self.assertEqual(prepared["session"]["events"], case["events"])
                self.assertNotIn("reference", prepared["session"])
                self.assertNotIn(case["reference"]["expected"], json.dumps(prepared))
                self.assertNotIn("must_pass", json.dumps(prepared))
        with self.assertRaises(ValueError):
            spike.load_case(evaluation["cases"][0]["id"])

    def test_review_template_is_ungraded_and_bound_to_output_and_suite(self) -> None:
        review = spike.review_template(
            self.assessment, self.session, "synthetic", "test-model", 1, "development"
        )
        self.assertEqual(review["status"], "unreviewed")
        self.assertTrue(review["structure_and_references_valid"])
        self.assertTrue(all(value is None for value in review["semantic_checks"].values()))
        self.assertIsNone(review["useful_beyond_baseline"])
        changed = copy.deepcopy(self.assessment)
        changed["limitations"].append("Another limitation.")
        changed_review = spike.review_template(
            changed, self.session, "synthetic", "test-model", 2, "development"
        )
        self.assertNotEqual(review["assessment_sha256"],
                            changed_review["assessment_sha256"])
        self.assertEqual(review["snapshot_sha256"], changed_review["snapshot_sha256"])
        with self.assertRaises(ValueError):
            spike.review_template(
                self.assessment, self.session, "synthetic", "test-model", 3, "development"
            )
        with self.assertRaises(ValueError):
            spike.review_template(
                self.assessment, self.session, "synthetic", "test-model", 1, "evaluation"
            )
        invalid = copy.deepcopy(self.assessment)
        invalid["segments"][0]["claims"][0]["evidence_ids"] = ["invented"]
        with self.assertRaises(ValueError):
            spike.review_template(
                invalid, self.session, "synthetic", "test-model", 1, "development"
            )

    def test_baseline_counts_records_without_interpreting_success(self) -> None:
        session = spike.load_case("unsupported-success")
        result = spike.baseline(session)
        self.assertEqual(result["event_counts"]["user"],
                         {"count": 1, "evidence_ids": ["b1"]})
        self.assertEqual(result["event_counts"]["tool_result"],
                         {"count": 0, "evidence_ids": []})
        self.assertNotIn("Fixed successfully", json.dumps(result))
        resumed = spike.baseline(spike.load_case("resumed-revision"))
        self.assertEqual(resumed["supersedes_revision"], 1)
        partial = spike.baseline(spike.load_case("missing-evidence"))
        self.assertIn("partial", partial["coverage"])

    def test_valid_assessment_and_recommendation_references(self) -> None:
        self.validate(self.assessment)

    def test_rejects_stale_identity_revision_digest_and_model(self) -> None:
        changes = {
            "session_id": "another-session",
            "revision": 2,
            "snapshot_sha256": "0" * 64,
            "model": {"provider": "other", "id": "other-model"},
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                output = copy.deepcopy(self.assessment)
                output[field] = value
                with self.assertRaises(ValueError):
                    self.validate(output)

    def test_rejects_invented_out_of_segment_and_unordered_evidence(self) -> None:
        for location in ("segment", "claim", "recommendation", "order", "scope"):
            with self.subTest(location=location):
                output = copy.deepcopy(self.assessment)
                segment = output["segments"][0]
                if location == "segment":
                    segment["event_ids"] = ["invented"]
                elif location == "claim":
                    segment["claims"][0]["evidence_ids"] = ["invented"]
                elif location == "recommendation":
                    output["recommendations"][0]["evidence_ids"] = ["invented"]
                elif location == "order":
                    segment["event_ids"] = ["a3", "a2", "a1"]
                else:
                    segment["event_ids"] = ["a1", "a3"]
                with self.assertRaises(ValueError):
                    self.validate(output)

    def test_rejects_empty_extra_and_malformed_output(self) -> None:
        for defect in ("empty", "extra", "unknown", "citation", "rubric", "duplicate"):
            with self.subTest(defect=defect):
                output = copy.deepcopy(self.assessment)
                if defect == "empty":
                    output["segments"] = []
                elif defect == "extra":
                    output["tools"] = ["shell"]
                elif defect == "unknown":
                    output["unknown_dimensions"] = ["time_saved"]
                elif defect == "citation":
                    output["segments"][0]["claims"][0]["evidence_ids"] = []
                elif defect == "rubric":
                    output["rubric_version"] = "unrecognized"
                else:
                    output["segments"].append(copy.deepcopy(output["segments"][0]))
                with self.assertRaises((ValueError, ValidationError)):
                    self.validate(output)

    def test_input_guards_and_digest_changes(self) -> None:
        for defect in ("duplicate", "unarchived", "revision", "reference"):
            with self.subTest(defect=defect):
                session = copy.deepcopy(self.session)
                if defect == "duplicate":
                    session["events"][1]["id"] = session["events"][0]["id"]
                elif defect == "unarchived":
                    session["archived"] = False
                elif defect == "revision":
                    session["supersedes_revision"] = session["revision"]
                else:
                    session["reference"] = {"expected": "must not reach model"}
                with self.assertRaises((ValueError, ValidationError)):
                    spike.baseline(session)
        changed = copy.deepcopy(self.session)
        changed["events"][0]["text"] += " Additional goal."
        self.assertNotEqual(spike.session_digest(self.session), spike.session_digest(changed))

    def test_validation_does_not_claim_semantic_correctness(self) -> None:
        # A valid citation cannot prove the accompanying prose is truthful.
        self.assessment["segments"][0]["claims"][0]["text"] = "All service work succeeded."
        self.validate(self.assessment)

    def test_cli_errors_are_nonzero_and_do_not_echo_assessment_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "assessment.json"
            path.write_text('{"private_marker": "DO_NOT_ECHO"}', encoding="utf-8")
            args = ["assessment_spike", "validate", "--case", self.session["id"],
                    "--provider", "synthetic", "--model", "test-model", str(path)]
            stderr = io.StringIO()
            stdout = io.StringIO()
            with patch("sys.argv", args), redirect_stderr(stderr), redirect_stdout(stdout):
                status = spike.main()
            self.assertEqual(status, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("schema validation failed", stderr.getvalue())
            self.assertNotIn("DO_NOT_ECHO", stderr.getvalue())

    def test_cli_preparation_and_successful_validation_remain_offline(self) -> None:
        stdout = io.StringIO()
        args = ["assessment_spike", "prepare", "--case", self.session["id"],
                "--provider", "synthetic", "--model", "test-model"]
        with patch("sys.argv", args), redirect_stdout(stdout):
            self.assertEqual(spike.main(), 0)
        prepared = json.loads(stdout.getvalue())
        self.assertEqual(prepared["session"], self.session)
        self.assertEqual(prepared["deterministic_baseline"]["snapshot_sha256"],
                         self.assessment["snapshot_sha256"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "assessment.json"
            path.write_text(json.dumps(self.assessment), encoding="utf-8")
            args = ["assessment_spike", "validate", "--case", self.session["id"],
                    "--provider", "synthetic", "--model", "test-model", str(path)]
            stdout = io.StringIO()
            with patch("sys.argv", args), redirect_stdout(stdout):
                self.assertEqual(spike.main(), 0)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["status"], "structure_and_references_valid")
            self.assertIn("not evaluated", result["semantic_quality"])
            args[1] = "review-template"
            args.extend(["--run", "1"])
            stdout = io.StringIO()
            with patch("sys.argv", args), redirect_stdout(stdout):
                self.assertEqual(spike.main(), 0)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "unreviewed")

    def test_cli_checks_evaluation_suite_and_prepares_its_input(self) -> None:
        for command in (
            ["check-fixtures", "--suite", "evaluation"],
            ["prepare", "--suite", "evaluation", "--case", "evaluation-ci-scope",
             "--provider", "synthetic", "--model", "not-executed"],
        ):
            with self.subTest(command=command):
                stdout = io.StringIO()
                with patch("sys.argv", ["assessment_spike", *command]), redirect_stdout(stdout):
                    self.assertEqual(spike.main(), 0)
                result = json.loads(stdout.getvalue())
                if command[0] == "check-fixtures":
                    self.assertEqual(result["count"], 8)
                else:
                    self.assertEqual(result["session"]["id"], "evaluation-ci-scope")
                    self.assertNotIn("reference", result["session"])


if __name__ == "__main__":
    unittest.main()
