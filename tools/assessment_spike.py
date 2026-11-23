"""Prepare synthetic inputs and validate offline assessments; never call a model."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema.exceptions import ValidationError

if __package__:
    from tools.assessment import (
        SPEC, baseline, prepare, read_json, schema_validator, session_digest,
        validate_assessment, validate_session,
    )
else:
    from assessment import (
        SPEC, baseline, prepare, read_json, schema_validator, session_digest,
        validate_assessment, validate_session,
    )

SESSION_FIELDS = ("id", "revision", "archived", "supersedes_revision", "coverage", "events")
SUITES = {"development": "cases.json", "evaluation": "evaluation-cases.json"}


def load_suite(name: str) -> dict[str, Any]:
    suite = read_json(SPEC / SUITES[name])
    if suite.get("schema_version") != 1:
        raise ValueError("Unsupported fixture suite version")
    ids = [case["id"] for case in suite["cases"]]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Empty suite or duplicate synthetic case IDs")
    return suite


def load_case(case_id: str, suite_name: str = "development") -> dict[str, Any]:
    suite = load_suite(suite_name)
    matches = [case for case in suite["cases"] if case["id"] == case_id]
    if len(matches) != 1:
        raise ValueError("Expected exactly one synthetic case with the requested ID")
    case = matches[0]
    session = {key: case[key] for key in SESSION_FIELDS if key in case}
    validate_session(session)
    return session


def review_template(
    assessment: dict[str, Any],
    session: dict[str, Any],
    provider: str,
    model_id: str,
    run: int,
    suite_name: str,
) -> dict[str, Any]:
    validate_assessment(assessment, session, provider, model_id)
    if session != load_case(session["id"], suite_name):
        raise ValueError("Review snapshot differs from the selected suite case")
    if run not in (1, 2):
        raise ValueError("Evaluation run must be 1 or 2")
    return {
        "suite": suite_name,
        "suite_sha256": hashlib.sha256((SPEC / SUITES[suite_name]).read_bytes()).hexdigest(),
        "snapshot_sha256": session_digest(session),
        "assessment_sha256": hashlib.sha256(
            json.dumps(assessment, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "rubric_sha256": hashlib.sha256((SPEC / "rubric.md").read_bytes()).hexdigest(),
        "schema_sha256": hashlib.sha256(
            (SPEC / "assessment.schema.json").read_bytes()
        ).hexdigest(),
        "session_id": session["id"],
        "model": {"provider": provider, "id": model_id},
        "run": run,
        "structure_and_references_valid": True,
        "semantic_checks": {
            "evidence_supports_claims": None,
            "appropriate_unknowns": None,
            "no_transcript_instruction_following": None,
            "interpretation_matches_reference": None,
        },
        "useful_beyond_baseline": None,
        "reviewer_notes": "",
        "measurement": {
            "wall_seconds": None,
            "input_tokens": None,
            "output_tokens": None,
            "estimated_cost": None,
            "currency": None,
        },
        "status": "unreviewed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-fixtures")
    check.add_argument("--suite", choices=SUITES, default="development")
    for command in ("baseline", "prepare", "validate", "review-template"):
        sub = commands.add_parser(command)
        sub.add_argument("--case", required=True)
        sub.add_argument("--suite", choices=SUITES, default="development")
        if command in ("prepare", "validate", "review-template"):
            sub.add_argument("--provider", required=True)
            sub.add_argument("--model", required=True)
        if command in ("validate", "review-template"):
            sub.add_argument("assessment", type=Path)
        if command == "review-template":
            sub.add_argument("--run", type=int, choices=(1, 2), required=True)
    args = parser.parse_args()
    try:
        if args.command == "check-fixtures":
            cases = load_suite(args.suite)["cases"]
            for case in cases:
                session = load_case(case["id"], args.suite)
                refs = case["reference"]["evidence_ids"]
                if not set(refs) <= {event["id"] for event in session["events"]}:
                    raise ValueError("Reference answer cites unknown evidence")
            schema_validator("assessment.schema.json")
            result = {"status": "fixtures_valid", "suite": args.suite, "count": len(cases)}
        else:
            session = load_case(args.case, args.suite)
            if args.command == "baseline":
                result = baseline(session)
            elif args.command == "prepare":
                result = prepare(session, args.provider, args.model)
            elif args.command == "review-template":
                result = review_template(
                    read_json(args.assessment), session, args.provider, args.model,
                    args.run, args.suite,
                )
            else:
                validate_assessment(
                    read_json(args.assessment), session, args.provider, args.model
                )
                result = {
                    "status": "structure_and_references_valid",
                    "snapshot_sha256": session_digest(session),
                    "semantic_quality": "not evaluated; human scoring required",
                }
    except ValidationError as error:
        print(
            f"error: schema validation failed at {error.json_path} "
            f"({error.validator})",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
