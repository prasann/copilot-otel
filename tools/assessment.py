"""Shared offline assessment contract; no source discovery or model calls."""

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "specs" / "session-reflection"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def schema_validator(name: str) -> Draft202012Validator:
    schema = read_json(SPEC / name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_session(session: dict[str, Any]) -> None:
    schema_validator("input.schema.json").validate(session)
    ids = [event["id"] for event in session["events"]]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate event IDs in session")
    if session.get("supersedes_revision", 0) >= session["revision"]:
        raise ValueError("Superseded revision must precede the current revision")


def session_digest(session: dict[str, Any]) -> str:
    encoded = json.dumps(session, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def baseline(session: dict[str, Any]) -> dict[str, Any]:
    validate_session(session)
    return {
        "session_id": session["id"],
        "revision": session["revision"],
        "snapshot_sha256": session_digest(session),
        "archived": session["archived"],
        "supersedes_revision": session.get("supersedes_revision"),
        "coverage": session.get("coverage", "not specified; completeness unverified"),
        "event_counts": {
            kind: {
                "count": sum(event["kind"] == kind for event in session["events"]),
                "evidence_ids": [
                    event["id"] for event in session["events"] if event["kind"] == kind
                ],
            }
            for kind in ("user", "assistant", "tool_result")
        },
        "limitations": [
            "Counts describe visible records, not tasks completed or test outcomes.",
            "No model interpretation has been performed.",
        ],
    }


def prepare(session: dict[str, Any], provider: str, model_id: str) -> dict[str, Any]:
    return {
        "rubric_version": "s4-v1",
        "instructions": (SPEC / "rubric.md").read_text(encoding="utf-8"),
        "output_schema": read_json(SPEC / "assessment.schema.json"),
        "model": {"provider": provider, "id": model_id},
        "session": session,
        "deterministic_baseline": baseline(session),
    }


def validate_assessment(
    assessment: dict[str, Any],
    session: dict[str, Any],
    provider: str,
    model_id: str,
) -> None:
    validate_session(session)
    schema_validator("assessment.schema.json").validate(assessment)
    if (assessment["session_id"], assessment["revision"]) != (
        session["id"], session["revision"]
    ):
        raise ValueError("Assessment does not match the requested session revision")
    if assessment["model"] != {"provider": provider, "id": model_id}:
        raise ValueError("Assessment model provenance differs from the requested model")
    if assessment["snapshot_sha256"] != session_digest(session):
        raise ValueError("Assessment does not match the requested snapshot content")

    positions = {event["id"]: index for index, event in enumerate(session["events"])}
    segment_ids: set[str] = set()
    for segment in assessment["segments"]:
        if segment["id"] in segment_ids:
            raise ValueError("Duplicate segment IDs")
        segment_ids.add(segment["id"])
        references = segment["event_ids"]
        if not set(references) <= positions.keys():
            raise ValueError("Segment cites an event outside the supplied snapshot")
        if references != sorted(references, key=positions.__getitem__):
            raise ValueError("Segment events must follow source order")
        for claim in segment["claims"]:
            if not set(claim["evidence_ids"]) <= set(references):
                raise ValueError("Claim cites evidence outside its segment")
    for recommendation in assessment["recommendations"]:
        if not set(recommendation["evidence_ids"]) <= positions.keys():
            raise ValueError("Recommendation cites an event outside the supplied snapshot")
