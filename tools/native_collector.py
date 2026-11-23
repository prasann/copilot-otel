"""Install and control a user-owned macOS collector; never configure Copilot clients."""

import argparse
import hashlib
import json
import os
import platform
import plistlib
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tools.azure_pilot import ROOT, azd_value, rest


VERSION = "0.165.0"
ARCHIVES = {
    "arm64": "f9a0ed310e9beeb0fabca9d9c24bd0d3467f6339e3207710dbd66a6adf1298f6",
    "amd64": "7d14e5c14bcb930d55f2bd5d3fbb3734be5fd2889698488e65e488efda5473c9",
}
LABEL = "io.github.prasann.copilot-otel.collector"
HOME = Path.home() / "Library" / "Application Support" / "copilot-otel"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def run(arguments: list[str], env: dict[str, str] | None = None) -> str:
    result = subprocess.run(arguments, capture_output=True, text=True, env=env, timeout=60)
    if result.returncode:
        raise RuntimeError(f"{arguments[0]} failed: {result.stderr.strip()[:2000]}")
    return result.stdout.strip()


def credentials_environment() -> dict[str, str]:
    az = shutil.which("az")
    if not az:
        raise ValueError("Install Azure CLI and run az login first")
    return {
        "HOME": str(Path.home()),
        "PATH": os.pathsep.join([str(Path(az).parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
        # azidentity v1.15 in the pinned collector restricts DefaultAzureCredential
        # to this one type. No environment/MSI/azd credential fallback.
        "AZURE_TOKEN_CREDENTIALS": "AzureCLICredential",
    }


def configuration(endpoint: str, directory: Path) -> dict[str, Any]:
    url = urlsplit(endpoint)
    if (url.scheme != "https" or not url.hostname
            or not url.hostname.endswith(".ingest.monitor.azure.com")
            or url.username or url.password or url.query or url.fragment
            or url.port not in (None, 443)
            or not url.path.endswith("/streams/Microsoft-OTLP-Traces/otlp/v1/traces")):
        raise ValueError("Unexpected native Azure OTLP traces endpoint")
    config = json.loads((ROOT / "collector" / "native-config.json").read_text())
    config["extensions"]["file_storage"]["directory"] = str(directory / "queue")
    config["extensions"]["file_storage"]["compaction"]["directory"] = str(directory / "compaction")
    config["exporters"]["otlp_http/azure"]["traces_endpoint"] = endpoint
    return config


def download(directory: Path) -> Path:
    arch = {"arm64": "arm64", "x86_64": "amd64"}.get(platform.machine())
    if not arch:
        raise ValueError("Supported architectures are macOS arm64 and x86_64")
    binary = directory / "bin" / f"otelcol-contrib-{VERSION}"
    if binary.exists():
        if VERSION not in run([str(binary), "--version"]):
            raise ValueError("Existing collector version does not match the pin")
        return binary
    binary.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = f"otelcol-contrib_{VERSION}_darwin_{arch}.tar.gz"
    url = ("https://github.com/open-telemetry/opentelemetry-collector-releases/"
           f"releases/download/v{VERSION}/{name}")
    with tempfile.TemporaryDirectory(prefix="copilot-otel-download-") as scratch:
        archive = Path(scratch) / name
        with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as target:
            shutil.copyfileobj(response, target)
        with archive.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != ARCHIVES[arch]:
                raise ValueError("Collector archive checksum mismatch; refusing installation")
        with tarfile.open(archive, "r:gz") as bundle:
            member = bundle.getmember("otelcol-contrib")
            if not member.isfile():
                raise ValueError("Expected a regular collector executable in the release archive")
            source = bundle.extractfile(member)
            if source is None:
                raise ValueError("Collector executable is missing from release archive")
            with source, binary.open("xb") as target:
                shutil.copyfileobj(source, target)
        binary.chmod(0o700)
    return binary


def check_owned_plist() -> None:
    if PLIST.exists():
        with PLIST.open("rb") as source:
            existing = plistlib.load(source)
        expected = [str(HOME / "bin" / f"otelcol-contrib-{VERSION}"),
                    "--config", str(HOME / "collector.json")]
        if existing.get("Label") != LABEL or existing.get("ProgramArguments") != expected:
            raise ValueError("Refusing to modify an unrecognized LaunchAgent")


def service_loaded() -> bool:
    result = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 113:
        return False
    if result.returncode:
        raise RuntimeError(f"Cannot inspect launchd service: {result.stderr.strip()[:2000]}")
    return True


def stop() -> None:
    check_owned_plist()
    if service_loaded():
        if not PLIST.exists():
            raise ValueError("Refusing to stop a service without its owned LaunchAgent")
        run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])


def start() -> dict[str, str]:
    check_owned_plist()
    if not PLIST.exists():
        raise ValueError("Collector is not installed")
    if service_loaded():
        raise ValueError("Collector service is already loaded; use status or stop first")
    run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)])
    for _ in range(20):
        try:
            with urllib.request.urlopen("http://127.0.0.1:13133", timeout=1) as response:
                if response.status == 200:
                    launchd = run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"])
                    return {"status": "running", "version": VERSION,
                            "receiver": "http://127.0.0.1:4318",
                            "state_directory": str(HOME), "launchd": launchd}
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.5)
    stop()
    raise RuntimeError(f"Collector failed readiness and was stopped; inspect "
                       f"{HOME / 'logs' / 'collector.stderr.log'}")


def install() -> dict[str, str]:
    if platform.system() != "Darwin":
        raise ValueError("Native installation currently supports macOS only")
    check_owned_plist()
    if service_loaded():
        raise ValueError("Stop the existing collector before reinstalling")
    for port in (4318, 13133):
        with socket.socket() as check:
            if check.connect_ex(("127.0.0.1", port)) == 0:
                raise ValueError(f"Port {port} is already in use; stop/review the existing listener")
    environment = credentials_environment()
    run(["az", "account", "show", "--query", "id", "-o", "tsv"], env={**os.environ, **environment})
    HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    HOME.chmod(0o700)
    binary = download(HOME)
    config = configuration(azd_value("TELEMETRY_TRACES_ENDPOINT"), HOME)
    config_path = HOME / "collector.json"
    config_path.touch(mode=0o600, exist_ok=True)
    config_path.chmod(0o600)
    config_path.write_text(json.dumps(config, indent=2))
    for name in ("logs", "queue", "compaction"):
        (HOME / name).mkdir(mode=0o700, exist_ok=True)
    run([str(binary), "validate", "--config", str(config_path)],
        env={**os.environ, **environment})
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    launch = {
        "Label": LABEL,
        "ProgramArguments": [str(binary), "--config", str(config_path)],
        "EnvironmentVariables": environment,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "StandardOutPath": str(HOME / "logs" / "collector.stdout.log"),
        "StandardErrorPath": str(HOME / "logs" / "collector.stderr.log"),
        "Umask": 0o077,
    }
    with PLIST.open("wb") as target:
        plistlib.dump(launch, target)
    PLIST.chmod(0o600)
    return start()


def smoke() -> dict[str, Any]:
    trace_id = uuid.uuid4().hex
    now = time.time_ns()
    span = {
        "traceId": trace_id, "spanId": uuid.uuid4().hex[:16],
        "name": "SYNTHETIC_CONTENT_IN_SPAN_NAME_MUST_DISAPPEAR",
        "kind": 3, "startTimeUnixNano": str(now), "endTimeUnixNano": str(now + 100_000_000),
        "status": {"code": 1, "message": "SYNTHETIC_STATUS_CONTENT_MUST_DISAPPEAR"},
        "attributes": [
            {"key": "telemetry.origin", "value": {"stringValue": "synthetic_collector_smoke"}},
            {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "100"}},
            {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "20"}},
            {"key": "gen_ai.usage.cache_read.input_tokens", "value": {"intValue": "10"}},
            {"key": "gen_ai.response.model", "value": {"stringValue": "synthetic-not-a-model-call"}},
            {"key": "gen_ai.input.messages", "value": {"stringValue": "SYNTHETIC_PROMPT_MUST_DISAPPEAR"}},
        ],
        "events": [{"name": "SYNTHETIC_EVENT_MUST_DISAPPEAR", "timeUnixNano": str(now)}],
        "links": [{"traceId": uuid.uuid4().hex, "spanId": uuid.uuid4().hex[:16],
                   "attributes": [{"key": "content", "value": {"stringValue": "SYNTHETIC_LINK_MUST_DISAPPEAR"}}]}],
    }
    invalid_span = {
        **span, "spanId": uuid.uuid4().hex[:16],
        "attributes": [
            {"key": "telemetry.origin", "value": {"stringValue": "synthetic_collector_smoke"}},
            {"key": "gen_ai.usage.input_tokens", "value": {"stringValue": "SYNTHETIC_TOKEN_CONTENT_MUST_DISAPPEAR"}},
            {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "-1"}},
            {"key": "gen_ai.usage.cache_read.input_tokens", "value": {"arrayValue": {"values": [
                {"stringValue": "SYNTHETIC_ARRAY_CONTENT_MUST_DISAPPEAR"}]}}},
            {"key": "gen_ai.response.model", "value": {"stringValue": "/SYNTHETIC_PATH_MUST_DISAPPEAR"}},
        ],
    }
    payload = {"resourceSpans": [{
        "schemaUrl": "https://SYNTHETIC_RESOURCE_SCHEMA_MUST_DISAPPEAR",
        "resource": {"attributes": [{"key": "host.name", "value": {"stringValue": "SYNTHETIC_HOST_MUST_DISAPPEAR"}}]},
        "scopeSpans": [{
            "schemaUrl": "https://SYNTHETIC_SCOPE_SCHEMA_MUST_DISAPPEAR",
            "scope": {"name": "SYNTHETIC_SCOPE_MUST_DISAPPEAR",
                      "version": "SYNTHETIC_SCOPE_VERSION_MUST_DISAPPEAR",
                      "attributes": [{"key": "content", "value": {"stringValue": "SYNTHETIC_SCOPE_ATTRIBUTE_MUST_DISAPPEAR"}}]},
            "spans": [span, invalid_span],
        }],
    }]}
    request = urllib.request.Request(
        "http://127.0.0.1:4318/v1/traces", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.load(response)
        if body.get("partialSuccess"):
            raise ValueError(f"Collector reported partial success: {body['partialSuccess']}")
    receipt = {"status": "collector_accepted_not_cloud_verified", "trace_id": trace_id,
               "span_ids": [span["spanId"], invalid_span["spanId"]]}
    path = ROOT / ".pilot" / f"collector-smoke-{trace_id}.json"
    path.parent.mkdir(mode=0o700, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2))
    return {**receipt, "receipt_file": str(path)}


def query_smoke(receipt: Path) -> dict[str, str]:
    envelope = json.loads(receipt.read_text())
    trace_id = uuid.UUID(envelope["trace_id"]).hex
    span_ids = envelope["span_ids"]
    workspace = str(uuid.UUID(azd_value("LOG_ANALYTICS_WORKSPACE_CUSTOMER_ID")))
    result = rest(
        f"https://api.loganalytics.azure.com/v1/workspaces/{workspace}/query",
        "https://api.loganalytics.io",
        {"query": "union isfuzzy=true OTelSpans, OTelEvents "
                  f"| where TimeGenerated > ago(1d) and TraceId == '{trace_id}' | take 10",
         "timespan": "P1D"},
    )
    if result.get("error"):
        raise ValueError(f"Logs query returned an error: {result['error']}")
    table = result["tables"][0]
    rows = [dict(zip((column["name"] for column in table["columns"]), row))
            for row in table["rows"]]
    if not rows:
        raise ValueError("Smoke spans not queryable yet; retry this read later, not smoke")
    if len(rows) != 2 or {row["SpanId"] for row in rows} != set(span_ids):
        raise ValueError("Expected exactly two smoke spans without event rows or duplicates")
    expected_metadata: dict[str, Any] = {
        "telemetry.origin": "synthetic_collector_smoke", "project.id": "synthetic/demo",
        "attribution.status": "synthetic", "accounting.verified": False,
    }
    for row in rows:
        if "MUST_DISAPPEAR" in json.dumps(row):
            raise ValueError("Prohibited synthetic canary reached Azure")
        attributes = row["Attributes"]
        if isinstance(attributes, str):
            attributes = json.loads(attributes)
        expected = dict(expected_metadata)
        if row["SpanId"] == span_ids[0]:
            expected.update({"gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 20,
                             "gen_ai.usage.cache_read.input_tokens": 10,
                             "gen_ai.response.model": "synthetic-not-a-model-call"})
        if attributes != expected or row["Type"] != "OTelSpans":
            raise ValueError("Stored smoke metadata differs from the exact allowlist")
        if (row["Name"] != "copilot.invocation" or row["ScopeName"] != "copilot-otel.metadata"
                or row["ScopeVersion"] != "1" or row["ServiceName"] != "copilot-otel"):
            raise ValueError("Stored smoke span/resource/scope normalization differs")
    envelope["status"] = "query_verified"
    receipt.write_text(json.dumps(envelope, indent=2))
    return {"status": "query_verified", "trace_id": trace_id,
            "privacy": "exact_metadata_matched_no_canaries_or_event_rows"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "status", "stop", "start", "uninstall", "smoke", "query-smoke"))
    parser.add_argument("receipt", type=Path, nargs="?")
    args = parser.parse_args()
    if (args.command == "query-smoke") != (args.receipt is not None):
        parser.error("query-smoke requires a receipt file; other commands take no receipt")
    try:
        if args.command == "install":
            result = install()
        elif args.command == "smoke":
            result = smoke()
        elif args.command == "query-smoke":
            result = query_smoke(args.receipt)
        elif args.command == "status":
            result = {"launchd": run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"])}
        elif args.command in ("stop", "uninstall"):
            stop()
            if args.command == "uninstall":
                PLIST.unlink(missing_ok=True)
            result = {"status": args.command, "retained_state": str(HOME)}
        else:
            result = start()
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
