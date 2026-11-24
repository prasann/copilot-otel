"""Read-only VS Code Agent Host session catalog lookups (metadata only, no transcripts)."""

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


EDITOR = Path.home() / "Library/Application Support/Code - Insiders"
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
