"""Print a synthetic unified data preview; no real client, model, or cloud access."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from tools.assessment_spike import load_case, session_digest, validate_assessment
from tools.project_attribution import project_identity


def usage_records(
    records: list[dict[str, Any]], projects_root: str
) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        event_id = record["event_id"]
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Usage event requires a nonempty stable ID")
        for field in ("input_tokens", "output_tokens"):
            if type(record[field]) is not int or record[field] < 0:
                raise ValueError("Token counts must be nonnegative integers")
        normalized = {
            "event_id": event_id,
            **project_identity(record["workspace"], projects_root),
            "input_tokens": record["input_tokens"],
            "output_tokens": record["output_tokens"],
        }
        if event_id in unique and unique[event_id] != normalized:
            raise ValueError("Conflicting replay for the same usage event ID")
        unique[event_id] = normalized
    return list(unique.values())


def build_demo(projects_root: str) -> dict[str, Any]:
    root = Path(projects_root)
    sources = [
        ("synthetic-usage-1", root / "personal" / "sample" / "src", 100, 20),
        ("synthetic-usage-2", root / "msft" / "sample", 200, 30),
        ("synthetic-usage-3", root, 25, 5),
    ]
    raw = [
        {"event_id": key, "workspace": str(path),
         "input_tokens": inputs, "output_tokens": outputs}
        for key, path, inputs, outputs in sources
    ]
    # A replay demonstrates deduplication, not another accounting layer.
    telemetry = usage_records([*raw, raw[0]], projects_root)
    session = load_case("narrow-test-evidence")
    assessment = {
        "schema_version": 1,
        "rubric_version": "s4-v1",
        "session_id": session["id"],
        "revision": session["revision"],
        "snapshot_sha256": session_digest(session),
        "model": {"provider": "synthetic", "id": "not-executed"},
        "segments": [{
            "id": "parser",
            "task_type": "debugging",
            "event_ids": ["a1", "a2", "a3"],
            "outcome": "partial",
            "claims": [{
                "kind": "observation",
                "text": "Parser unit verification is visible; service verification remains.",
                "evidence_ids": ["a2", "a3"],
                "confidence": "high",
            }],
        }],
        "limitations": ["Authored demo, not model output or proof of assessment quality."],
        "unknown_dimensions": ["time_saved", "satisfaction", "understanding"],
        "recommendations": [],
    }
    validate_assessment(assessment, session, "synthetic", "not-executed")
    identity = project_identity(str(root / "personal" / "sample"), projects_root)
    published = {
        **identity,
        "assessment_id": "synthetic-assessment-1",
        "session_id": session["id"],
        "revision": session["revision"],
        "origin": "hand_authored_synthetic_demo",
        "summary": assessment["segments"][0]["claims"][0]["text"],
        "outcome": "partial",
        "evidence_ids": ["a2", "a3"],
        "limitations": assessment["limitations"],
    }
    projects: dict[str, dict[str, Any]] = {}
    for record in telemetry:
        key = record["project.id"]
        group = projects.setdefault(
            key, {"project_id": key, "input_tokens": 0, "output_tokens": 0,
                  "usage_records": 0, "assessments": []}
        )
        group["input_tokens"] += record["input_tokens"]
        group["output_tokens"] += record["output_tokens"]
        group["usage_records"] += 1
    projects[published["project.id"]]["assessments"].append(published)
    return {
        "mode": "synthetic_only",
        "model_executed": False,
        "real_telemetry_received": False,
        "dashboard_connected": False,
        "telemetry_received_including_replay": len(raw) + 1,
        "telemetry_unique_records": len(telemetry),
        "totals": {
            "input_tokens": sum(row["input_tokens"] for row in telemetry),
            "output_tokens": sum(row["output_tokens"] for row in telemetry),
            "unattributed_records": sum(
                row["attribution.status"] == "unattributed" for row in telemetry
            ),
        },
        "projects": list(projects.values()),
        "limitations": [
            "Project/time aggregation is not proof of per-session telemetry linkage.",
            "Source workspace paths are authored inputs, not verified client attributes.",
            "No worktree, multi-root, lifecycle, time-window, or cloud ingestion proof.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects-root", default=str(Path.home() / "projects"))
    args = parser.parse_args()
    try:
        result = build_demo(args.projects_root)
    except (OSError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
