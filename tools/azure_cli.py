"""Shared Azure helpers using the signed-in Azure CLI identity."""

import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]


def command_json(
    arguments: list[str], *, input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> Any:
    result = subprocess.run(
        arguments, input=input_text, capture_output=True, text=True, timeout=180, env=env,
    )
    if result.returncode:
        raise RuntimeError(f"{arguments[0]} failed: {result.stderr.strip()[:2000]}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def rest(
    url: str, resource: str, body: Any, *, env: dict[str, str] | None = None,
) -> Any:
    return command_json([
        "az", "rest", "--method", "post", "--url", url,
        "--resource", resource, "--body", "@/dev/stdin",
        "--only-show-errors", "--output", "json",
    ], input_text=json.dumps(body), env=env)


def load_config(path: Path) -> dict[str, str]:
    config: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key.strip() in config:
            raise ValueError("Invalid or duplicate dotenv entry")
        config[key.strip()] = value.strip().strip("\"'")
    if config.get("COACHING_AZURE_AUTH") != "azure_cli":
        raise ValueError("Only explicit azure_cli authentication is supported")
    for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT",
                "COACHING_MODEL_ID"):
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


def response_text(response: dict[str, Any]) -> str:
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
        raise ValueError("Model returned no text (possibly a refusal)")
    return "".join(text)


def azd_value(name: str) -> str:
    result = subprocess.run(
        ["azd", "env", "get-value", name], capture_output=True, text=True, timeout=30,
    )
    if result.returncode or not result.stdout.strip():
        raise RuntimeError(f"Cannot read azd output {name}: {result.stderr.strip()[:1000]}")
    return result.stdout.strip()
