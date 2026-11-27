"""Read-only VS Code Agent Host session catalog lookups (metadata only, no transcripts)."""

import configparser
import hashlib
import json
import os
import re
import sqlite3
import stat
import uuid
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


EDITOR = Path.home() / "Library/Application Support/Code - Insiders"
MAX_SESSIONS = 1000
MAX_ANCESTORS = 64
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_METADATA_FILE = 64 * 1024


class CaptureError(ValueError):
    """A controlled, content-free reason why capture cannot proceed."""


def resolved_path(path: Path) -> Path:
    try:
        return path.resolve()
    except RuntimeError:
        raise CaptureError("workspace_path_unavailable") from None


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
    return Path(unquote(parsed.path))


def workspace_alias(
    workspace: Path, root: Path, excluded_workspaces: tuple[Path, ...] = (),
) -> str | None:
    workspace, root = resolved_path(workspace), resolved_path(root)
    if (not workspace.is_relative_to(root)
            or any(workspace.is_relative_to(resolved_path(excluded)) for excluded in excluded_workspaces)):
        return None
    relative = workspace.relative_to(root).parts
    if not relative:
        return None
    return f"{relative[0]}/{relative[-1]}" if len(relative) >= 2 else f"projects/{relative[0]}"


@dataclass(frozen=True)
class ProjectWorkspace:
    workspace: Path
    checkout: Path
    canonical: Path
    identity: tuple[str, Path]
    alias: str | None = None
    reason: str = "resolved"


class WorkspaceLookup:
    """One export's bounded metadata reads; never executes Git or reads objects."""

    def __init__(self, root: Path, excluded: tuple[Path, ...] = ()):
        self.root = resolved_path(root)
        self.excluded = tuple(resolved_path(path) for path in excluded)
        self.observed: dict[Path, tuple | None] = {}
        self.files: dict[Path, bytes] = {}
        self.projects: dict[Path, ProjectWorkspace] = {}
        self.bytes_read = 0

    @staticmethod
    def _signature(info: os.stat_result) -> tuple:
        identity = (info.st_dev, info.st_ino, info.st_mode, info.st_uid)
        return identity if stat.S_ISDIR(info.st_mode) else (
            *identity, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
        )

    def _stat(self, path: Path) -> os.stat_result | None:
        try:
            info = path.lstat()
        except FileNotFoundError:
            info = None
        signature = self._signature(info) if info is not None else None
        if path in self.observed and self.observed[path] != signature:
            raise CaptureError("workspace_metadata_changed")
        self.observed[path] = signature
        if len(self.observed) > MAX_SESSIONS * MAX_ANCESTORS:
            raise CaptureError("workspace_metadata_limit_exceeded")
        return info

    def _directory(self, path: Path) -> None:
        info = self._stat(path)
        if (info is None or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid() or info.st_mode & 0o022):
            raise CaptureError("unsupported_git_directory")

    def _read(self, path: Path) -> str:
        if path not in self.files:
            info = self._stat(path)
            if (info is None or not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid() or info.st_mode & 0o022
                    or info.st_size > MAX_METADATA_FILE):
                raise CaptureError("unsupported_git_metadata")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as file:
                if self._signature(os.fstat(file.fileno())) != self.observed[path]:
                    raise CaptureError("workspace_metadata_changed")
                value = file.read(MAX_METADATA_FILE + 1)
                if self._signature(os.fstat(file.fileno())) != self.observed[path]:
                    raise CaptureError("workspace_metadata_changed")
            self.bytes_read += len(value)
            if len(value) > MAX_METADATA_FILE or self.bytes_read > MAX_METADATA_BYTES:
                raise CaptureError("workspace_metadata_limit_exceeded")
            self.files[path] = value
        try:
            return self.files[path].decode("utf-8")
        except UnicodeError:
            raise CaptureError("unsupported_git_metadata") from None

    def _head(self, directory: Path) -> None:
        value = self._read(directory / "HEAD").strip()
        if re.fullmatch(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})", value):
            return
        reference = value.removeprefix("ref: ")
        if (not value.startswith("ref: refs/")
                or re.search(r"[\s\x00-\x1f\x7f~^:?*\[\\]", reference)
                or any(part in reference for part in ("..", "//", "@{"))
                or any(not part or part.startswith(".") or part.endswith((".", ".lock"))
                       for part in reference.split("/"))):
            raise CaptureError("unsupported_git_head")

    def _config(self, directory: Path) -> bool:
        config = configparser.RawConfigParser(
            strict=False, allow_no_value=True, delimiters=("=",),
            inline_comment_prefixes=("#", ";"), empty_lines_in_values=False,
        )
        raw = self._read(directory / "config")
        if "\x00" in raw:
            raise CaptureError("unsupported_git_config")
        try:
            config.read_string(raw)
        except configparser.Error:
            raise CaptureError("unsupported_git_config") from None
        sections = {section.lower(): section for section in config.sections()}
        if (config.defaults()
                or any(sum(section.lower() == name for section in config.sections()) > 1
                       for name in ("core", "extensions"))):
            raise CaptureError("unsupported_git_config")
        if any(section == "include" or section.startswith("includeif") for section in sections):
            return False
        core = dict(config.items(sections["core"])) if "core" in sections else {}
        extensions = dict(config.items(sections["extensions"])) if "extensions" in sections else {}
        values = {key: value for key, value in core.items()
                  if key in ("bare", "repositoryformatversion")}
        values.update({f"extensions.{key}": value for key, value in extensions.items()})
        normalized = {}
        for key, value in values.items():
            text = value.strip() if isinstance(value, str) else "true"
            if text.startswith('"') or text.endswith('"'):
                if len(text) < 2 or not (text.startswith('"') and text.endswith('"')):
                    return False
                text = text[1:-1]
            normalized[key] = text.lower()
        if ("worktree" in core or normalized.get("bare") not in ("false", "no", "off", "0")
                or normalized.get("repositoryformatversion") not in ("0", "1")
                or any(self._stat(directory / name) is not None
                       for name in ("config.worktree", "commondir", "gitdir"))):
            return False
        allowed = {
            "objectformat": {"sha1", "sha256"},
            "refstorage": {"files", "reftable"},
            "worktreeconfig": {"false", "no", "off", "0"},
            "relativeworktrees": {"true", "yes", "on", "1", "false", "no", "off", "0"},
        }
        return all(key in allowed and normalized[f"extensions.{key}"] in allowed[key]
                   for key in extensions)

    @staticmethod
    def _pointer(value: str, base: Path) -> Path:
        value = value.removesuffix("\n")
        if (not value or any(character in value for character in "\x00\r\n")
                or len(Path(value).parts) > MAX_ANCESTORS):
            raise CaptureError("unsupported_git_pointer")
        return base / value

    def _link(self, path: Path, expected: Path) -> None:
        try:
            target = path.resolve(strict=True)
        except RuntimeError:
            raise CaptureError("unsupported_git_pointer") from None
        if target != expected:
            raise CaptureError("workspace_registration_mismatch")
        self._stat(path)

    def _excluded(self, *paths: Path) -> bool:
        return any(path.is_relative_to(excluded) for path in paths for excluded in self.excluded)

    def project(self, path: Path) -> ProjectWorkspace:
        original = path
        lexical = Path(os.path.abspath(path))
        if self._excluded(lexical):
            return ProjectWorkspace(lexical, lexical, lexical, ("path", lexical))
        if self._stat(original) is None:
            return ProjectWorkspace(lexical, lexical, lexical, ("path", lexical),
                                    reason="lookup_incomplete")
        workspace = resolved_path(original)
        if (self._excluded(lexical, workspace)
                or lexical.is_relative_to(self.root) and not workspace.is_relative_to(self.root)):
            return ProjectWorkspace(workspace, workspace, workspace, ("path", workspace))
        if workspace in self.projects:
            return self.projects[workspace]
        result = ProjectWorkspace(workspace, workspace, workspace, ("path", workspace))
        info = self._stat(workspace)
        if info is None:
            return ProjectWorkspace(workspace, workspace, workspace, ("path", workspace),
                                    reason="lookup_incomplete")
        if not stat.S_ISDIR(info.st_mode):
            return ProjectWorkspace(workspace, workspace, workspace, ("path", workspace),
                                    reason="unsupported_source")
        checkout = workspace
        for _ in range(MAX_ANCESTORS):
            marker = checkout / ".git"
            marker_info = self._stat(marker)
            if marker_info is not None:
                break
            if checkout.parent == checkout:
                break
            checkout = checkout.parent
        else:
            raise CaptureError("workspace_ancestor_limit_exceeded")
        if marker_info is None:
            result = ProjectWorkspace(workspace, workspace, workspace, ("path", workspace),
                                      workspace_alias(workspace, self.root, self.excluded))
        elif self._excluded(checkout):
            pass
        elif stat.S_ISDIR(marker_info.st_mode):
            if checkout.is_relative_to(self.root):
                self._directory(checkout)
                self._directory(marker)
                self._head(marker)
                if self._config(marker):
                    result = ProjectWorkspace(
                        workspace, checkout, checkout, ("git", marker),
                        workspace_alias(checkout, self.root, self.excluded),
                    )
                else:
                    result = ProjectWorkspace(workspace, checkout, checkout, ("git", marker),
                                              reason="unsupported_source")
        elif stat.S_ISREG(marker_info.st_mode):
            value = self._read(marker)
            if not value.startswith("gitdir: "):
                raise CaptureError("unsupported_git_pointer")
            pointer = self._pointer(value.removeprefix("gitdir: "), checkout)
            admin = Path(os.path.abspath(pointer))
            common = admin.parent.parent
            canonical = common.parent
            if admin.parent.name != "worktrees" or common.name != ".git":
                result = ProjectWorkspace(workspace, checkout, checkout, ("path", checkout),
                                          reason="unsupported_source")
            elif not canonical.is_relative_to(self.root) or self._excluded(canonical):
                pass
            else:
                # Anchor targets inside an approved conventional primary before reading them.
                if resolved_path(common) != common:
                    raise CaptureError("unsupported_git_directory")
                for directory in (checkout, canonical, common, admin.parent, admin):
                    self._directory(directory)
                self._link(pointer, admin)
                self._link(self._pointer(self._read(admin / "commondir"), admin), common)
                self._link(self._pointer(self._read(admin / "gitdir"), admin), marker)
                self._head(common)
                self._head(admin)
                if self._stat(admin / "config.worktree") is not None or not self._config(common):
                    result = ProjectWorkspace(workspace, checkout, canonical, ("git", common),
                                              reason="unsupported_source")
                else:
                    result = ProjectWorkspace(
                        workspace, checkout, canonical, ("git", common),
                        workspace_alias(canonical, self.root, self.excluded),
                    )
        else:
            raise CaptureError("unsupported_git_marker")
        self.projects[workspace] = result
        return result

    def validate(self) -> None:
        for path in tuple(self.observed):
            self._stat(path)


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
    project_identity: tuple[str, Path] | None = None
    reason: str = "resolved"


def discover(
    editor: Path, projects: dict[str, str], since_ms: int,
    projects_root: Path | None = None, root_since_ms: int | None = None,
    excluded_workspaces: tuple[Path, ...] = (), lookup: WorkspaceLookup | None = None,
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
            lookup = lookup or WorkspaceLookup(root, excluded_workspaces)
            explicit = json.dumps([Path(path).resolve().as_uri() for path in projects.values()])
            predicate = (
                "provider = 'copilotcli' "
                "AND json_extract(payload, '$.data.project.uri') IS NOT NULL "
                "AND substr(json_extract(payload, '$.data.project.uri'), 1, 8) = 'file:///' "
                "AND json_extract(payload, '$.data.project.uri') "
                "NOT IN (SELECT value FROM json_each(?))"
            )
            cutoff = root_since_ms if root_since_ms is not None else since_ms
            parameters = (explicit, cutoff)
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
            for row in rows:
                workspace = local_path(json.loads(row[5])["data"]["project"]["uri"])
                project = lookup.project(workspace)
                if project.alias is None and project.reason == "resolved":
                    excluded += 1
                    continue
                candidate = catalog_candidate(row, project.alias or "unattributed", project.workspace,
                                              support_root=project.checkout)
                candidates.append(replace(candidate, project_identity=project.identity,
                                          reason=project.reason))
    candidates.sort(key=lambda candidate: (candidate.started_ms, candidate.host_id))
    return candidates, excluded


def catalog_candidate(
    row: tuple[Any, ...], alias: str | None, path: Path, support_root: Path | None = None,
) -> Candidate:
    uri, revision, started, provider, version, raw = row
    if not uri.startswith("copilotcli:/"):
        raise CaptureError("unsupported_session_identity")
    host_id = str(uuid.UUID(uri.removeprefix("copilotcli:/")))
    wrapper = json.loads(raw)
    data = wrapper["data"]
    if alias is None:
        workspace = local_path(data["project"]["uri"])
        alias = workspace_alias(workspace, path)
        if alias is None:
            raise CaptureError("workspace_outside_approved_root")
        path = workspace
    if (type(revision) is not int
            or type(data.get("isArchived")) is not bool):
        raise CaptureError("unsupported_catalog_record")
    chats = data.get("chats", [])
    supported = (
        revision >= 1 and provider == "copilotcli" and version == 2
        and wrapper.get("payloadVersion") == 2
        and len(chats) == 1 and chats[0].get("kind") == "default"
        and all(resolved_path(local_path(directory)).is_relative_to(support_root or path)
                for directory in data.get("workingDirectories", []))
    )
    return Candidate(
        host_id, alias, path, revision, data["isArchived"], started,
        hashlib.sha256(raw.encode()).hexdigest(), supported,
    )
