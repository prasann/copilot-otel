"""Resolve local session metadata and forward attributed OTLP JSON without storing payloads."""

import argparse
import http.client
import json
import logging
import re
import socket
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from tools import native_sessions as native


LOG = logging.getLogger(__name__)
MAX_BODY = 1024 * 1024
MAX_SPANS = 10_000
GATEWAY_PORT = 4319
COLLECTOR_PORT = 4320
REASONS = (
    "resolved", "missing_identity", "invalid_identity", "unknown_session",
    "identity_conflict", "unsupported_source", "lookup_incomplete", "lookup_failed",
)
ATTRIBUTION_KEYS = {"project.id", "attribution.status", "attribution.reason"}
IDENTITY_KEYS = {"gen_ai.conversation.id", "vscode.agent_host.session.uri"}
PROJECT_ALIAS_PATTERN = r"[a-zA-Z0-9._-]{1,63}/[a-zA-Z0-9._-]{1,63}"


@dataclass(frozen=True)
class Policy:
    editor: Path
    projects: dict[str, str] = field(default_factory=dict)
    projects_root: Path | None = None
    excluded_workspaces: tuple[Path, ...] = ()

    @classmethod
    def load(cls, path: Path) -> "Policy":
        value = json.loads(path.read_text())
        if isinstance(value, dict) and type(value.get("version")) is int and value["version"] == 2:
            if (set(value) != {"version", "editor", "projects_root", "excluded_workspaces"}
                    or any(not isinstance(value[key], str) or not Path(value[key]).is_absolute()
                           for key in ("editor", "projects_root"))
                    or not isinstance(value["excluded_workspaces"], list)
                    or any(not isinstance(item, str) or not Path(item).is_absolute()
                           for item in value["excluded_workspaces"])):
                raise ValueError("Invalid discovery policy; use absolute paths and excluded_workspaces")
            root = Path(value["projects_root"]).resolve()
            excluded = tuple(Path(item).resolve() for item in value["excluded_workspaces"])
            if any(not item.is_relative_to(root) for item in excluded):
                raise ValueError("Excluded workspaces must be inside projects_root")
            return cls(Path(value["editor"]).resolve(), projects_root=root,
                       excluded_workspaces=excluded)
        if (not isinstance(value, dict) or type(value.get("version")) is not int
                or value["version"] != 1
                or set(value) != {"version", "editor", "projects"}
                or not isinstance(value["editor"], str)
                or not Path(value["editor"]).is_absolute()
                or not isinstance(value["projects"], dict) or not value["projects"]):
            raise ValueError("Invalid attribution policy; expected version, editor, and projects")
        projects = {}
        for alias, workspace in value["projects"].items():
            if (not re.fullmatch(PROJECT_ALIAS_PATTERN, alias)
                    or alias == "synthetic/demo"
                    or not isinstance(workspace, str) or not Path(workspace).is_absolute()):
                raise ValueError("Use category/name aliases and absolute workspace paths")
            projects[alias] = str(Path(workspace).resolve())
        paths = list(map(Path, projects.values()))
        if any(a.is_relative_to(b) or b.is_relative_to(a)
               for index, a in enumerate(paths) for b in paths[index + 1:]):
            raise ValueError("Overlapping attribution project roots are not supported")
        return cls(Path(value["editor"]).resolve(), projects)


def identifier(value: str) -> str:
    if not re.fullmatch(r"(?:[a-fA-F0-9]{32}|[a-fA-F0-9-]{36})", value):
        raise ValueError("Invalid session identifier")
    return str(uuid.UUID(value))


def sdk_session_id(editor: Path, host_id: str) -> str:
    path = editor / "agentSessionData" / host_id / "session.db"
    with closing(native.readonly(path, 20)) as db:
        rows = db.execute(
            "SELECT json_extract(value, '$.sdkSessionId') FROM session_metadata "
            "WHERE key = 'defaultChatProviderData' LIMIT 2",
        ).fetchall()
    if len(rows) != 1 or not isinstance(rows[0][0], str):
        raise ValueError("Missing or ambiguous SDK mapping")
    return identifier(rows[0][0])


@dataclass(frozen=True)
class Resolution:
    project: str = "unattributed"
    reason: str = "missing_identity"

    def attributes(self) -> list[dict[str, Any]]:
        return [
            {"key": key, "value": {"stringValue": value}}
            for key, value in (
                ("project.id", self.project),
                ("attribution.status", "attributed" if self.reason == "resolved" else "unattributed"),
                ("attribution.reason", self.reason),
            )
        ]


class Resolver:
    def __init__(self, policy: Policy):
        self.policy = policy

    def snapshot(self) -> "Snapshot":
        identities: dict[str, set[tuple[str, str]]] = {}
        blocked = set()
        incomplete = False
        mappings = {}
        try:
            candidates, _ = native.discover(
                self.policy.editor, self.policy.projects, 0,
                projects_root=self.policy.projects_root,
                excluded_workspaces=self.policy.excluded_workspaces,
            )
            workspaces: dict[str, set[Path]] = {}
            for candidate in candidates:
                workspaces.setdefault(candidate.project_id, set()).add(candidate.workspace)
            conflicting_projects = {
                project for project, paths in workspaces.items() if len(paths) > 1
            }
            for candidate in candidates:
                if (not candidate.supported
                        or not re.fullmatch(PROJECT_ALIAS_PATTERN, candidate.project_id)
                        or candidate.project_id == "synthetic/demo"):
                    blocked.add(candidate.host_id)
                    incomplete = True
                    continue
                try:
                    sdk_id = sdk_session_id(self.policy.editor, candidate.host_id)
                except (OSError, ValueError, sqlite3.Error):
                    blocked.add(candidate.host_id)
                    incomplete = True
                    continue
                mappings[candidate.host_id] = sdk_id
                for key in (candidate.host_id, sdk_id):
                    identities.setdefault(key, set()).add((candidate.host_id, candidate.project_id))
            current, _ = native.discover(
                self.policy.editor, self.policy.projects, 0,
                projects_root=self.policy.projects_root,
                excluded_workspaces=self.policy.excluded_workspaces,
            )
            if current != candidates:
                raise ValueError("Catalog changed during lookup")
            if any(sdk_session_id(self.policy.editor, host) != sdk
                   for host, sdk in mappings.items()):
                raise ValueError("SDK mapping changed during lookup")
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            LOG.warning("Attribution metadata unavailable; spans remain unattributed (lookup_failed)")
            return Snapshot({}, set(), "lookup_failed")
        if incomplete:
            LOG.warning("Attribution metadata incomplete; unresolved spans remain unattributed")
        if conflicting_projects:
            LOG.warning("Attribution project aliases conflict; affected spans remain unattributed")
        return Snapshot(identities, blocked, "lookup_incomplete" if incomplete else "unknown_session",
                        conflicting_projects)


@dataclass
class Snapshot:
    identities: dict[str, set[tuple[str, str]]]
    blocked: set[str]
    unknown_reason: str
    conflicting_projects: set[str] = field(default_factory=set)

    def resolve(self, attributes: list[dict[str, Any]]) -> Resolution:
        keys = {}
        try:
            for attribute in attributes:
                key = attribute["key"]
                if key not in IDENTITY_KEYS:
                    continue
                if key in keys:
                    raise ValueError("Duplicate identity")
                value = attribute["value"]
                if not isinstance(value, dict) or set(value) != {"stringValue"}:
                    raise ValueError("Identity must be a string")
                text = value["stringValue"]
                if not isinstance(text, str):
                    raise ValueError("Identity must be a string")
                if key == "vscode.agent_host.session.uri":
                    if not text.startswith("copilotcli:/"):
                        raise ValueError("Unsupported session URI")
                    text = text.removeprefix("copilotcli:/")
                keys[key] = identifier(text)
        except (KeyError, TypeError, ValueError):
            return Resolution(reason="invalid_identity")
        if not keys:
            return Resolution()
        matches = []
        for key in keys.values():
            if key in self.blocked:
                return Resolution(reason="unsupported_source")
            found = self.identities.get(key)
            if not found:
                return Resolution(reason=self.unknown_reason)
            if len(found) != 1:
                return Resolution(reason="identity_conflict")
            match = next(iter(found))
            if match[1] in self.conflicting_projects:
                return Resolution(reason="identity_conflict")
            matches.append(match)
        if len(set(matches)) != 1:
            return Resolution(reason="identity_conflict")
        return Resolution(matches[0][1], "resolved")


def spans_in(payload: Any) -> list[dict[str, Any]]:
    spans = []
    if not isinstance(payload, dict) or set(payload) - {"resourceSpans"}:
        raise ValueError("Expected an OTLP trace export")
    resources = payload.get("resourceSpans", [])
    if not isinstance(resources, list):
        raise ValueError("resourceSpans must be an array")
    for resource in resources:
        if not isinstance(resource, dict) or not isinstance(resource.get("scopeSpans", []), list):
            raise ValueError("Invalid scopeSpans")
        for scope in resource.get("scopeSpans", []):
            if not isinstance(scope, dict) or not isinstance(scope.get("spans", []), list):
                raise ValueError("Invalid spans")
            for span in scope.get("spans", []):
                if not isinstance(span, dict) or not isinstance(span.get("attributes", []), list):
                    raise ValueError("Invalid span attributes")
                if any(not isinstance(item, dict) or not isinstance(item.get("key"), str)
                       for item in span.get("attributes", [])):
                    raise ValueError("Invalid attribute")
                spans.append(span)
                if len(spans) > MAX_SPANS:
                    raise ValueError("Span limit exceeded")
    return spans


def enrich(payload: Any, resolver: Resolver) -> dict[str, int]:
    spans = spans_in(payload)
    snapshot = resolver.snapshot() if spans else Snapshot({}, set(), "unknown_session")
    counts: Counter[str] = Counter()
    for span in spans:
        attributes = span.get("attributes", [])
        resolution = snapshot.resolve(attributes)
        span["attributes"] = [
            item for item in attributes if item["key"] not in ATTRIBUTION_KEYS
        ] + resolution.attributes()
        counts[resolution.reason] += 1
    return dict(counts)


class Gateway(HTTPServer):
    def __init__(self, policy: Policy, port: int = GATEWAY_PORT,
                 collector_port: int = COLLECTOR_PORT):
        if port == collector_port:
            raise ValueError("Gateway and collector must use different ports")
        self.resolver = Resolver(policy)
        self.collector_url = f"http://127.0.0.1:{collector_port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        super().__init__(("127.0.0.1", port), Handler)

    def get_request(self) -> tuple[socket.socket, Any]:
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address

    def handle_error(self, request: Any, client_address: Any) -> None:
        LOG.error("Attribution request failed; no payload logged")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


class Handler(BaseHTTPRequestHandler):
    server: Gateway

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self.reply(404, b'{"error":"unknown_endpoint"}')
            return
        try:
            with self.server.opener.open(self.server.collector_url + "/", timeout=2):
                pass
        except urllib.error.HTTPError as error:
            # The OTLP listener returns 404 at /, which still proves it is reachable.
            code = error.code
            error.close()
            if code != 404:
                self.reply(503, b'{"error":"collector_unavailable"}')
                return
        except (OSError, urllib.error.URLError):
            self.reply(503, b'{"error":"collector_unavailable"}')
            return
        self.reply(200, b'{"status":"ok","collector":"reachable","cloud_delivery":"not_checked"}')

    def do_POST(self) -> None:
        if self.path not in ("/v1/traces", "/v1/metrics", "/v1/logs"):
            self.reply(404, b'{"error":"unknown_endpoint"}')
            return
        if (self.headers.get_content_type() != "application/json"
                or self.headers.get("Content-Encoding", "identity") != "identity"
                or self.headers.get("Transfer-Encoding") is not None):
            self.reply(415, b'{"error":"only_uncompressed_otlp_json_supported"}')
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0]):
            self.reply(411, b'{"error":"content_length_required"}')
            return
        if len(lengths[0]) > 7:
            self.reply(413, b'{"error":"body_limit_exceeded"}')
            return
        length = int(lengths[0])
        if not 0 < length <= MAX_BODY:
            self.reply(413, b'{"error":"body_limit_exceeded"}')
            return
        try:
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("Incomplete body")
            if self.path == "/v1/traces":
                payload = json.loads(data)
                counts = enrich(payload, self.server.resolver)
                data = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
                if len(data) > MAX_BODY:
                    self.reply(413, b'{"error":"enriched_body_limit_exceeded"}')
                    return
                LOG.info("Attribution outcomes: %s", json.dumps(counts, sort_keys=True))
        except (ValueError, RecursionError, UnicodeError):
            self.reply(400, b'{"error":"invalid_otlp_json"}')
            return
        except (OSError, TimeoutError):
            self.reply(408, b'{"error":"request_read_failed"}')
            return
        request = urllib.request.Request(
            self.server.collector_url + self.path, data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with self.server.opener.open(request, timeout=10) as response:
                body = response.read(MAX_BODY + 1)
                status = response.status
            if len(body) > MAX_BODY:
                raise ValueError("Collector response too large")
            acknowledgement = json.loads(body)
            if (status != 200 or not isinstance(acknowledgement, dict)
                    or set(acknowledgement) - {"partialSuccess"}
                    or ("partialSuccess" in acknowledgement
                        and not isinstance(acknowledgement["partialSuccess"], dict))):
                raise ValueError("Invalid collector acknowledgement")
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            LOG.error("Collector rejected attributed export (HTTP %s)", code)
            self.reply(code if 400 <= code < 600 else 502,
                       b'{"error":"collector_rejected_export"}')
            return
        except (OSError, urllib.error.URLError, ValueError, http.client.HTTPException):
            LOG.error("Collector forwarding failed; delivery is unconfirmed")
            self.reply(503, b'{"error":"collector_delivery_unconfirmed"}')
            return
        self.reply(status, body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--port", type=int, default=GATEWAY_PORT)
    parser.add_argument("--collector-port", type=int, default=COLLECTOR_PORT)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        policy = Policy.load(args.policy)
        with Gateway(policy, args.port, args.collector_port) as server:
            LOG.info("Attribution gateway listening on loopback")
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError):
        LOG.error("Cannot start attribution gateway; check policy, paths, and loopback ports")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
