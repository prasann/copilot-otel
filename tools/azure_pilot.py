"""Azure smoke diagnostics and shared request/publishing helpers using CLI identity."""

import argparse
import fcntl
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema.exceptions import ValidationError

from tools.assessment import ROOT, prepare, validate_assessment
from tools.assessment_spike import load_case


STATE = ROOT / ".pilot"
PROVIDER = "azure"


def command_json(arguments: list[str], *, input_text: str | None = None) -> Any:
    result = subprocess.run(
        arguments, input=input_text, capture_output=True, text=True, timeout=180,
    )
    if result.returncode:
        raise RuntimeError(f"{arguments[0]} failed: {result.stderr.strip()[:2000]}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def rest(url: str, resource: str, body: Any) -> Any:
    return command_json([
        "az", "rest", "--method", "post", "--url", url,
        "--resource", resource, "--body", "@/dev/stdin",
        "--only-show-errors", "--output", "json",
    ], input_text=json.dumps(body))


def load_config(path: Path) -> dict[str, str]:
    config: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key.strip() in config:
            raise ValueError("Invalid or duplicate dotenv entry")
        config[key.strip()] = value.strip().strip("\"'")
    if config.get("ASSESSMENT_AZURE_AUTH") != "azure_cli":
        raise ValueError("Only explicit azure_cli authentication is supported")
    for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT",
                "ASSESSMENT_MODEL_ID", "ASSESSMENT_MODEL_VERSION"):
        if not config.get(key):
            raise ValueError(f"Missing configuration: {key}")
    origin = urlsplit(config["AZURE_OPENAI_ENDPOINT"])
    if (origin.scheme != "https" or origin.username or origin.password
            or origin.query or origin.fragment or origin.path not in ("", "/")
            or origin.port not in (None, 443)
            or not origin.hostname
            or not origin.hostname.endswith((".openai.azure.com", ".services.ai.azure.com"))):
        raise ValueError("Endpoint must be an Azure public-cloud resource HTTPS origin")
    return config


def response_assessment(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("status") != "completed":
        raise ValueError(
            f"Model response is not complete: {response.get('status')}; "
            f"details={response.get('incomplete_details')}"
        )
    text = [
        content["text"]
        for item in response.get("output", []) if item.get("type") == "message"
        for content in item.get("content", []) if content.get("type") == "output_text"
    ]
    if not text:
        raise ValueError("Model returned no assessment text (possibly a refusal)")
    assessment = json.loads("".join(text))
    if not isinstance(assessment, dict):
        raise ValueError("Assessment must be a JSON object")
    return assessment


def assess(config: dict[str, str], case: str, state: Path) -> Path:
    if config.get("ASSESSMENT_ALLOW_SYNTHETIC_INFERENCE") != "true":
        raise ValueError("Set ASSESSMENT_ALLOW_SYNTHETIC_INFERENCE=true for synthetic requests")
    limit = int(config["ASSESSMENT_MAX_REQUESTS"])
    output_tokens = int(config["ASSESSMENT_MAX_OUTPUT_TOKENS"])
    if not 1 <= limit <= 16 or not 1 <= output_tokens <= 2000:
        raise ValueError("Pilot limits are 1-16 attempts and 1-2000 output tokens")
    session = load_case(case)
    prepared = prepare(session, PROVIDER, config["ASSESSMENT_MODEL_ID"])
    instructions = prepared.pop("instructions")
    body = {
        "model": config["AZURE_OPENAI_DEPLOYMENT"],
        "instructions": instructions,
        "input": json.dumps(prepared),
        "store": False,
        "max_output_tokens": output_tokens,
        "reasoning": {"effort": "low"},
        "text": {"format": {"type": "json_object"}},
    }
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_id = str(uuid.uuid4())
    directory = state / run_id
    directory.mkdir(mode=0o700)
    # Reserve before network access; failures/timeouts count and are never retried.
    with (state / "model-attempts.jsonl").open("a+", encoding="utf-8") as ledger:
        fcntl.flock(ledger, fcntl.LOCK_EX)
        ledger.seek(0)
        if len(ledger.readlines()) >= limit:
            raise ValueError("Synthetic request budget exhausted; inspect the local attempt ledger")
        ledger.write(json.dumps({"run_id": run_id, "case": case,
                                 "started_at": datetime.now(timezone.utc).isoformat()}) + "\n")
        ledger.flush()
    response = rest(
        config["AZURE_OPENAI_ENDPOINT"].rstrip("/") + "/openai/v1/responses",
        "https://ai.azure.com", body,
    )
    (directory / "response.json").write_text(json.dumps(response, indent=2), encoding="utf-8")
    assessment = response_assessment(response)
    validate_assessment(assessment, session, PROVIDER, config["ASSESSMENT_MODEL_ID"])
    result = {
        "origin": "synthetic_azure_model_smoke",
        "case": case,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "requested_model": config["ASSESSMENT_MODEL_ID"],
        "configured_model_version": config["ASSESSMENT_MODEL_VERSION"],
        "returned_model": response.get("model"),
        "usage": response.get("usage"),
        "assessment": assessment,
    }
    path = directory / "assessment.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return path


def assessment_record(run: dict[str, Any], summary: str) -> dict[str, Any]:
    if run["origin"] != "synthetic_azure_model_smoke":
        raise ValueError("Only the synthetic smoke run can be published by this tool")
    session = load_case(run["case"])
    assessment = run["assessment"]
    validate_assessment(assessment, session, PROVIDER, run["requested_model"])
    if not summary.strip() or len(summary) > 1000 or "\n" in summary:
        raise ValueError("Provide one reviewed derived summary, at most 1000 characters")
    if any(event["text"] in summary for event in session["events"]):
        raise ValueError("Publish a derived summary, not a complete fixture event quotation")
    segments = assessment["segments"]
    if len(segments) != 1:
        raise ValueError("This basic smoke publisher supports one assessment segment only")
    return {
        "TimeGenerated": run["assessed_at"],
        # Fixtures have no source timestamps; this is explicitly a synthetic value.
        "SessionEndedAt": run["assessed_at"],
        "SchemaVersion": 1,
        "AssessmentId": str(uuid.uuid4()),
        "SessionId": assessment["session_id"],
        "Revision": assessment["revision"],
        "SnapshotSha256": assessment["snapshot_sha256"],
        "ProjectId": "synthetic/demo",
        "ProjectName": "demo",
        "ProjectCategory": "synthetic",
        "Origin": run["origin"],
        "Outcome": segments[0]["outcome"],
        "Summary": summary,
        "Limitations": "Synthetic smoke only; source time is synthetic; summary manually reviewed. "
                        "No real work, productivity, satisfaction, or time saved measured.",
    }


def azd_value(name: str) -> str:
    result = subprocess.run(
        ["azd", "env", "get-value", name], capture_output=True, text=True, timeout=30,
    )
    if result.returncode or not result.stdout.strip():
        raise RuntimeError(f"Cannot read azd output {name}: {result.stderr.strip()[:1000]}")
    return result.stdout.strip()


def publish(path: Path, summary: str) -> Path:
    run = json.loads(path.read_text(encoding="utf-8"))
    record = assessment_record(run, summary)
    receipt = path.parent / f"publish-{record['AssessmentId']}.json"
    return publish_record(record, receipt)


def publish_record(record: dict[str, Any], receipt: Path) -> Path:
    endpoint = azd_value("ASSESSMENT_LOGS_INGESTION_ENDPOINT").rstrip("/")
    host = urlsplit(endpoint)
    if (host.scheme != "https" or not host.hostname
            or not host.hostname.endswith(".ingest.monitor.azure.com")
            or host.username or host.password or host.query or host.fragment
            or host.path or host.port not in (None, 443)):
        raise ValueError("Unexpected public-cloud ingestion endpoint")
    rule = azd_value("ASSESSMENT_DCR_IMMUTABLE_ID")
    if not re.fullmatch(r"dcr-[a-f0-9]{32}", rule):
        raise ValueError("Unexpected DCR immutable ID")
    stream = azd_value("ASSESSMENT_STREAM_NAME")
    if stream != "Custom-CopilotAssessmentInput":
        raise ValueError("Unexpected assessment stream")
    envelope = {"status": "attempting", "record": record}
    receipt.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    rest(f"{endpoint}/dataCollectionRules/{rule}/streams/{stream}?api-version=2023-01-01",
         "https://monitor.azure.com", [record])
    envelope["status"] = "accepted_not_yet_query_verified"
    receipt.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    return receipt


def query(receipt: Path) -> dict[str, Any]:
    envelope = json.loads(receipt.read_text(encoding="utf-8"))
    record = envelope["record"]
    assessment_id = str(uuid.UUID(record["AssessmentId"]))
    workspace = str(uuid.UUID(azd_value("LOG_ANALYTICS_WORKSPACE_CUSTOMER_ID")))
    result = rest(
        f"https://api.loganalytics.azure.com/v1/workspaces/{workspace}/query",
        "https://api.loganalytics.io",
        {"query": "CopilotAssessments_CL | where TimeGenerated > ago(1d) "
                  f"| where AssessmentId == '{assessment_id}' | take 2", "timespan": "P1D"},
    )
    if result.get("error"):
        raise ValueError(f"Logs query returned an error: {result['error']}")
    table = result["tables"][0]
    if not table["rows"]:
        raise ValueError("Expected one stored row; ingestion may still be propagating. "
                         "Query again later; do not republish.")
    if len(table["rows"]) != 1:
        raise ValueError("Duplicate stored assessment ID; investigate before continuing")
    row = dict(zip((column["name"] for column in table["columns"]), table["rows"][0]))
    for key, value in record.items():
        if key in ("TimeGenerated", "SessionEndedAt") and value is not None:
            if datetime.fromisoformat(row[key].replace("Z", "+00:00")) != datetime.fromisoformat(value):
                raise ValueError(f"Stored timestamp differs: {key}")
        elif row.get(key) != value:
            raise ValueError(f"Stored assessment field differs: {key}")
    additional = set(row) - record.keys()
    system_columns = {"TenantId", "Type", "_ResourceId", "_SubscriptionId", "_BilledSize",
                      "_IsBillable", "_TimeReceived", "TimeReceived"}
    if additional - system_columns:
        raise ValueError(f"Unexpected stored columns: {sorted(additional - system_columns)}")
    envelope["status"] = "query_verified"
    receipt.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    return {"status": "query_verified", "assessment_id": assessment_id,
            "project_id": row["ProjectId"], "summary": row["Summary"],
            "stored_columns": list(row)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    infer = sub.add_parser("assess")
    infer.add_argument("--env-file", type=Path, default=ROOT / ".env")
    infer.add_argument("--case", default="narrow-test-evidence")
    infer.add_argument("--execute", action="store_true", required=True)
    upload = sub.add_parser("publish")
    upload.add_argument("assessment", type=Path)
    upload.add_argument("--summary", required=True, help="Manually reviewed, derived synthetic summary")
    upload.add_argument("--execute", action="store_true", required=True)
    check = sub.add_parser("query")
    check.add_argument("receipt", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "assess":
            result = {"assessment_file": str(assess(load_config(args.env_file), args.case, STATE))}
        elif args.command == "publish":
            result = {"receipt_file": str(publish(args.assessment, args.summary))}
        else:
            result = query(args.receipt)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired,
            ValidationError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
