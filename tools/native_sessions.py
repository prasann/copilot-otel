"""Read only explicitly allowlisted, new VS Code Agent Host sessions."""

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


EDITOR = Path.home() / "Library/Application Support/Code - Insiders"
COPILOT = Path.home() / ".copilot"
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_SESSIONS = 1000


class CaptureError(ValueError):
    """A controlled, content-free reason why capture cannot proceed."""


def readonly(path: Path, version: int) -> sqlite3.Connection:
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    if db.execute("PRAGMA user_version").fetchone()[0] != version:
        db.close()
        raise CaptureError("unsupported_source_schema")
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    return db


def local_path(uri: str) -> Path:
    parsed = urlsplit(uri)
    if (parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment
            or not Path(unquote(parsed.path)).is_absolute()):
        raise CaptureError("unsupported_workspace_uri")
    return Path(unquote(parsed.path)).resolve()


@dataclass(frozen=True)
class Candidate:
    host_id: str
    project_id: str
    workspace: Path
    revision: int
    archived: bool
    started_ms: int
    fingerprint: str
    supported: bool


def discover(
    editor: Path, projects: dict[str, str], since_ms: int,
    projects_root: Path | None = None, root_since_ms: int | None = None,
) -> tuple[list[Candidate], int]:
    candidates = []
    excluded = 0
    catalog = editor / "User/globalStorage/agent-host.db"
    with closing(readonly(catalog, 22)) as db:
        for alias, workspace in projects.items():
            path = Path(workspace).resolve()
            excluded += db.execute(
                "SELECT count(*) FROM sessions_v2 "
                "WHERE provider = 'copilotcli' "
                "AND json_extract(payload, '$.data.project.uri') = ? AND start_time < ?",
                (path.as_uri(), since_ms),
            ).fetchone()[0]
            rows = db.execute(
                "SELECT session_uri, source_revision, start_time, provider, payload_version, payload "
                "FROM sessions_v2 WHERE provider = 'copilotcli' "
                "AND json_extract(payload, '$.data.project.uri') = ? "
                "AND start_time >= ? ORDER BY start_time, session_uri LIMIT ?",
                (path.as_uri(), since_ms, MAX_SESSIONS + 1),
            ).fetchall()
            if len(candidates) + len(rows) > MAX_SESSIONS:
                raise CaptureError("source_session_limit_exceeded")
            candidates.extend(catalog_candidate(row, alias, path) for row in rows)
        if projects_root is not None:
            root = projects_root.resolve()
            prefix = root.as_uri() + "/"
            explicit = json.dumps([Path(path).resolve().as_uri() for path in projects.values()])
            predicate = (
                "provider = 'copilotcli' "
                "AND substr(json_extract(payload, '$.data.project.uri'), 1, ?) = ? "
                "AND json_extract(payload, '$.data.project.uri') "
                "NOT IN (SELECT value FROM json_each(?))"
            )
            cutoff = root_since_ms if root_since_ms is not None else since_ms
            parameters = (len(prefix), prefix, explicit, cutoff)
            excluded += db.execute(
                f"SELECT count(*) FROM sessions_v2 WHERE {predicate} AND start_time < ?",
                parameters,
            ).fetchone()[0]
            rows = db.execute(
                "SELECT session_uri, source_revision, start_time, provider, payload_version, payload "
                f"FROM sessions_v2 WHERE {predicate} AND start_time >= ? "
                "ORDER BY start_time, session_uri LIMIT ?",
                (*parameters, MAX_SESSIONS + 1),
            ).fetchall()
            if len(candidates) + len(rows) > MAX_SESSIONS:
                raise CaptureError("source_session_limit_exceeded")
            candidates.extend(catalog_candidate(row, None, root) for row in rows)
    candidates.sort(key=lambda candidate: (candidate.started_ms, candidate.host_id))
    return candidates, excluded


def catalog_candidate(row: tuple[Any, ...], alias: str | None, path: Path) -> Candidate:
    uri, revision, started, provider, version, raw = row
    if not uri.startswith("copilotcli:/"):
        raise CaptureError("unsupported_session_identity")
    host_id = str(uuid.UUID(uri.removeprefix("copilotcli:/")))
    wrapper = json.loads(raw)
    data = wrapper["data"]
    if alias is None:
        workspace = local_path(data["project"]["uri"])
        if not workspace.is_relative_to(path):
            raise CaptureError("workspace_escaped_approved_root")
        relative = workspace.relative_to(path).parts
        if not relative:
            raise CaptureError("missing_project_folder")
        alias = "/".join(relative[:2]) if len(relative) >= 2 else f"projects/{relative[0]}"
        path = workspace
    if (type(revision) is not int or revision < 1
            or type(data.get("isArchived")) is not bool):
        raise CaptureError("unsupported_catalog_record")
    chats = data.get("chats", [])
    supported = (
        provider == "copilotcli" and version == 2
        and wrapper.get("payloadVersion") == 2
        and len(chats) == 1 and chats[0].get("kind") == "default"
        and all(local_path(directory).is_relative_to(path)
                for directory in data.get("workingDirectories", []))
    )
    return Candidate(
        host_id, alias, path, revision, data["isArchived"], started,
        hashlib.sha256(raw.encode()).hexdigest(), supported,
    )


def source_bytes(path: Path) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise CaptureError("source_size_limit_exceeded")
    if not raw or not raw.endswith(b"\n"):
        raise CaptureError("incomplete_source_stream")
    return raw


def message_text(content: Any) -> str:
    if not isinstance(content, str):
        raise CaptureError("unsupported_message_content")
    # Omit fenced code and injected context blocks, not just attachment fields.
    content = re.sub(r"(?ms)^(`{3,}|~{3,}).*?^\1[ \t]*$", "[code block omitted]", content)
    if re.search(r"(?m)^\s*(`{3,}|~{3,})", content):
        raise CaptureError("unclosed_code_block")
    for tag in ("system_reminder", "skill-context", "tagged_files"):
        content = re.sub(rf"(?s)<{tag}\b[^>]*>.*?</{tag}>", "[context omitted]", content)
    if re.search(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
        r"AKIA[A-Z0-9]{16})\b",
        content,
    ):
        raise CaptureError("possible_secret_requires_review")
    return content.strip()


def project_events(
    raw: bytes, candidate: Candidate, sdk_id: str, since_ms: int,
) -> dict[str, Any]:
    events = [json.loads(line) for line in raw.splitlines()]
    first = events[0]
    start = first["data"]
    cwd = Path(start["context"]["cwd"])
    if (first["type"] != "session.start" or start.get("version") != 1
            or start.get("sessionId") != sdk_id
            or not cwd.is_absolute() or not cwd.resolve().is_relative_to(candidate.workspace)):
        raise CaptureError("unsupported_runtime_identity")
    started = datetime.fromisoformat(first["timestamp"].replace("Z", "+00:00"))
    if started.tzinfo is None or started.timestamp() * 1000 < since_ms:
        raise CaptureError("preexisting_runtime_excluded")

    pending: dict[str, set[str]] = {kind: set() for kind in ("turn", "tool", "hook", "external")}
    starts = {
        "assistant.turn_start": ("turn", "turnId"),
        "tool.execution_start": ("tool", "toolCallId"),
        "hook.start": ("hook", "hookInvocationId"),
        "external_tool.requested": ("external", "requestId"),
    }
    ends = {
        "assistant.turn_end": ("turn", "turnId"),
        "tool.execution_complete": ("tool", "toolCallId"),
        "hook.end": ("hook", "hookInvocationId"),
        "external_tool.completed": ("external", "requestId"),
    }
    ignored = {
        "session.start", "session.resume", "session.shutdown", "session.error", "abort",
        "session.permissions_changed", "session.model_change", "session.usage_record",
        "session.usage_checkpoint", "system.message", "instruction.update",
        "skill.invoked", "skill.invoked_ref", "skill.context_delivered_ref",
        "human_response.recorded",
    }
    projected = []
    seen = set()
    waiting_for_response = False
    ended_turn = False
    for event in events:
        kind, data, event_id = event["type"], event["data"], event["id"]
        if not isinstance(event_id, str) or not event_id or event_id in seen:
            raise CaptureError("invalid_or_duplicate_event_id")
        seen.add(event_id)
        if kind in starts:
            group, field = starts[kind]
            identifier = data[field]
            if not isinstance(identifier, str) or not identifier or identifier in pending[group]:
                raise CaptureError("ambiguous_lifecycle")
            pending[group].add(identifier)
            if group == "turn":
                waiting_for_response = False
        elif kind in ends:
            group, field = ends[kind]
            identifier = data[field]
            if identifier not in pending[group]:
                raise CaptureError("unmatched_lifecycle_end")
            pending[group].remove(identifier)
            if group == "turn":
                ended_turn = True
            elif group == "tool":
                success = data.get("success")
                if type(success) is not bool:
                    raise CaptureError("unsupported_tool_metadata")
                projected.append({
                    "id": event_id, "kind": "tool_result",
                    "text": f"Tool execution completed; success={str(success).lower()}. "
                            "Arguments, tool name, and output omitted. "
                            "This does not verify tests or task success.",
                })
        elif kind in ("user.message", "assistant.message"):
            if data.get("parentAgentTaskId"):
                raise CaptureError("subagent_coverage_unsupported")
            if kind == "user.message":
                waiting_for_response = True
            if kind == "assistant.message" and data.get("phase") == "analysis":
                continue
            text = message_text(data["content"])
            if text:
                projected.append({
                    "id": event_id,
                    "kind": "user" if kind == "user.message" else "assistant",
                    "text": text,
                })
        elif kind not in ignored:
            raise CaptureError("unsupported_runtime_event")
    if any(pending.values()) or waiting_for_response or not ended_turn:
        raise CaptureError("active_or_uncertain_session")
    if not any(event["kind"] == "user" for event in projected):
        raise CaptureError("no_user_messages")
    return {
        "id": sdk_id, "revision": candidate.revision, "archived": True,
        "coverage": "Partial: user/assistant text and tool success flags only. "
                    "No tool arguments/results, attachments, file reads, reasoning, "
                    "injected context, or fenced code. Tool success is not test verification.",
        "events": projected,
    }


@dataclass(frozen=True)
class Snapshot:
    candidate: Candidate
    session: dict[str, Any]
    source_sha256: str


def capture(
    candidate: Candidate, editor: Path, copilot: Path, since_ms: int,
) -> Snapshot:
    if not candidate.supported:
        raise CaptureError("unsupported_chat_coverage")
    if not candidate.archived:
        raise CaptureError("not_archived")
    mapping = editor / "agentSessionData" / candidate.host_id / "session.db"
    if not mapping.resolve().is_relative_to(editor.resolve() / "agentSessionData" / candidate.host_id):
        raise CaptureError("source_path_escaped")
    with closing(readonly(mapping, 20)) as db:
        row = db.execute(
            "SELECT value FROM session_metadata WHERE key = 'defaultChatProviderData'",
        ).fetchone()
        if row is None:
            raise CaptureError("missing_sdk_mapping")
        sdk_id = str(uuid.UUID(json.loads(row[0])["sdkSessionId"]))
    path = copilot / "session-state" / sdk_id / "events.jsonl"
    if not path.resolve().is_relative_to(copilot.resolve() / "session-state" / sdk_id):
        raise CaptureError("source_path_escaped")
    raw = source_bytes(path)
    session = project_events(raw, candidate, sdk_id, since_ms)
    current, _ = discover(editor, {candidate.project_id: str(candidate.workspace)}, since_ms)
    if candidate not in current or source_bytes(path) != raw:
        raise CaptureError("source_changed_during_capture")
    return Snapshot(candidate, session, hashlib.sha256(raw).hexdigest())


def recheck(snapshot: Snapshot, editor: Path, copilot: Path, since_ms: int) -> None:
    current = capture(snapshot.candidate, editor, copilot, since_ms)
    if current.source_sha256 != snapshot.source_sha256:
        raise CaptureError("source_changed_after_capture")
