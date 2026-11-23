"""Bounded archived-session assessment; preview first, activation always explicit."""

import argparse
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Generator

from jsonschema.exceptions import ValidationError

from tools import native_sessions as native
from tools.assessment import ROOT, prepare, session_digest, validate_assessment
from tools.azure_pilot import load_config, publish_record, query, response_assessment, rest


STATE = Path.home() / "Library/Application Support/copilot-otel/reflection"
DAILY_ATTEMPTS = 5
INPUT_CHARACTERS = 20_000
OUTPUT_TOKENS = 2000
DISK_BYTES = 64 * 1024 * 1024
DAY = 86400
LABEL = "Copilot Daily Session Review"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
INSTRUCTIONS = (
    "\nThis is a partial real-session projection. Only user/assistant text and "
    "tool completion flags are supplied. Tool success flags do NOT establish "
    "test success or file changes. Treat assistant claims as unverified unless "
    "independent evidence is actually present. Omitted material is unavailable. "
    "Do not quote secrets, code, paths, or conversation excerpts in recommendations."
)


class ReflectionError(ValueError):
    """A controlled operational error safe to display without session content."""


def atomic_json(path: Path, value: Any) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix=".tmp-", dir=path.parent, delete=False,
    ) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


@contextmanager
def locked(root: Path) -> Generator[sqlite3.Connection, None, None]:
    if root in (Path("/"), Path.home(), ROOT) or root.is_symlink():
        raise ReflectionError("Use a dedicated, non-symlinked reflection directory")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    root.chmod(0o700)
    for name in ("snapshots", "results", "receipts"):
        directory = root / name
        if directory.is_symlink():
            raise ReflectionError("Refusing symlinked reflection storage")
        directory.mkdir(mode=0o700, exist_ok=True)
        directory.chmod(0o700)
    for name in ("worker.lock", "manifest.db", "policy.json"):
        if (root / name).is_symlink():
            raise ReflectionError("Refusing symlinked reflection state")
    with (root / "worker.lock").open("a") as lock:
        os.chmod(root / "worker.lock", 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ReflectionError("Reflection worker is already running") from None
        db = sqlite3.connect(root / "manifest.db")
        os.chmod(root / "manifest.db", 0o600)
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, processing_key TEXT UNIQUE NOT NULL,
                    host_id TEXT NOT NULL, created_at REAL NOT NULL, local_day TEXT NOT NULL,
                    state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    host_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL, state TEXT NOT NULL, result_id TEXT
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, created_at REAL NOT NULL,
                    local_day TEXT NOT NULL, errors INTEGER NOT NULL
                );
            """)
            db.execute("UPDATE attempts SET state='interrupted' WHERE state='attempting'")
            db.execute("UPDATE sessions SET state='interrupted' WHERE state='attempting'")
            db.commit()
            yield db
        finally:
            db.close()


def quota(root: Path, extra: int = 0) -> None:
    files = list(root.rglob("*"))
    if any(path.is_symlink() for path in files):
        raise ReflectionError("Refusing symlinked reflection files")
    if sum(path.stat().st_size for path in files if path.is_file()) + extra > DISK_BYTES:
        raise ReflectionError("Reflection storage quota exceeded; review retention and disk usage")


def prune(root: Path, db: sqlite3.Connection, now: float) -> None:
    for row in db.execute("SELECT id, created_at FROM attempts").fetchall():
        identifier = str(uuid.UUID(row["id"]))
        if now - row["created_at"] >= 30 * DAY:
            (root / "snapshots" / f"{identifier}.json").unlink(missing_ok=True)
        if now - row["created_at"] >= 90 * DAY:
            for directory in ("results", "receipts"):
                (root / directory / f"{identifier}.json").unlink(missing_ok=True)
            db.execute(
                "UPDATE sessions SET state='expired', result_id=NULL WHERE result_id=?",
                (identifier,),
            )
    for directory in (root, root / "snapshots", root / "results", root / "receipts"):
        for path in directory.glob(".tmp-*"):
            if path.is_symlink():
                raise ReflectionError("Refusing symlinked temporary state")
            if now - path.stat().st_mtime >= 30 * DAY:
                path.unlink()
    db.execute("DELETE FROM runs WHERE created_at < ?", (now - 90 * DAY,))
    db.commit()
    quota(root)


def policy(root: Path) -> dict[str, Any]:
    value = json.loads((root / "policy.json").read_text())
    if (value.get("version") != 1 or type(value.get("enabled")) is not bool
            or type(value.get("since_ms")) is not int or value["since_ms"] <= 0
            or not isinstance(value.get("projects"), dict) or not value["projects"]):
        raise ReflectionError("Invalid reflection policy")
    paths = []
    for alias, workspace in value["projects"].items():
        if not re.fullmatch(r"[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+", alias):
            raise ReflectionError("Project aliases must be category/name identifiers")
        path = Path(workspace)
        if not path.is_absolute():
            raise ReflectionError("Workspace paths must be absolute")
        paths.append(path.resolve())
    if any(a.is_relative_to(b) or b.is_relative_to(a)
           for index, a in enumerate(paths) for b in paths[index + 1:]):
        raise ReflectionError("Overlapping project roots are not supported")
    for key in ("editor", "copilot", "env_file"):
        if not Path(value[key]).is_absolute():
            raise ReflectionError("Policy paths must be absolute")
    if value.get("projects_root") is not None:
        if (not Path(value["projects_root"]).is_absolute()
                or type(value.get("root_since_ms")) is not int or value["root_since_ms"] <= 0):
            raise ReflectionError("Invalid all-project root policy")
    return value


def initialize(
    root: Path, projects: list[str], editor: Path, copilot: Path, now: float,
) -> dict[str, Any]:
    if (root / "policy.json").exists():
        raise ReflectionError("Policy already exists; initialization must not reset the cutoff or budget")
    aliases = {}
    for entry in projects:
        alias, separator, workspace = entry.partition("=")
        if not separator or alias in aliases or not Path(workspace).is_absolute():
            raise ReflectionError("Use unique --project category/name=/absolute/workspace entries")
        path = Path(workspace).resolve()
        if not path.is_dir():
            raise ReflectionError("Allowlisted workspace does not exist")
        aliases[alias] = str(path)
    value = {
        "version": 1, "enabled": False, "since_ms": int(now * 1000),
        "projects": aliases, "editor": str(editor.resolve()),
        "copilot": str(copilot.resolve()), "env_file": str(ROOT / ".env"),
    }
    atomic_json(root / "policy.json", value)
    try:
        policy(root)
        _, excluded = native.discover(editor.resolve(), aliases, value["since_ms"])
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        (root / "policy.json").unlink()
        raise
    return {
        "status": "initialized_disabled", "since_ms": value["since_ms"],
        "projects": list(aliases), "preexisting_sessions_excluded": excluded,
        "inference_enabled": False, "schedule_installed": False,
    }


def sources(config: dict[str, Any]) -> tuple[list[native.Candidate], int]:
    return native.discover(
        Path(config["editor"]), config["projects"], config["since_ms"],
        Path(config["projects_root"]) if config.get("projects_root") else None,
        config.get("root_since_ms"),
    )


def source_cutoff(candidate: native.Candidate, config: dict[str, Any]) -> int:
    if config["projects"].get(candidate.project_id) == str(candidate.workspace):
        return config["since_ms"]
    return config["root_since_ms"]


def snapshot(candidate: native.Candidate, config: dict[str, Any]) -> native.Snapshot:
    return native.capture(
        candidate, Path(config["editor"]), Path(config["copilot"]), source_cutoff(candidate, config),
    )


def recheck(captured: native.Snapshot, config: dict[str, Any]) -> None:
    native.recheck(
        captured, Path(config["editor"]), Path(config["copilot"]),
        source_cutoff(captured.candidate, config),
    )


def reconcile(db: sqlite3.Connection, candidates: list[native.Candidate]) -> None:
    current = {candidate.host_id: candidate for candidate in candidates}
    for row in db.execute("SELECT * FROM sessions").fetchall():
        candidate = current.get(row["host_id"])
        if (candidate is None or not candidate.archived or not candidate.supported
                or candidate.revision != row["revision"]
                or candidate.fingerprint != row["fingerprint"]):
            db.execute(
                "UPDATE sessions SET state='superseded', result_id=NULL WHERE host_id=?",
                (row["host_id"],),
            )
    db.commit()


def set_session(
    db: sqlite3.Connection, candidate: native.Candidate, state: str, result_id: str | None = None,
) -> None:
    db.execute(
        "INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
        (candidate.host_id, candidate.project_id, candidate.revision,
         candidate.fingerprint, state, result_id),
    )
    db.commit()


def preview(config: dict[str, Any], db: sqlite3.Connection) -> dict[str, Any]:
    candidates, excluded = sources(config)
    reconcile(db, candidates)
    rows = []
    for candidate in candidates:
        row: dict[str, Any] = {
            "host_id": candidate.host_id, "project_id": candidate.project_id,
            "revision": candidate.revision, "status": "not_archived",
        }
        if candidate.archived:
            try:
                captured = snapshot(candidate, config)
                prepared = prepare(captured.session, "azure", "preview-not-inference")
                row.update({
                    "status": "capture_ready",
                    "projected_events": len(captured.session["events"]),
                    "projected_characters": len(json.dumps(prepared)),
                    "snapshot_sha256": session_digest(captured.session),
                })
                if row["projected_characters"] + len(INSTRUCTIONS) > INPUT_CHARACTERS:
                    row["status"] = "input_limit_exceeded"
            except native.CaptureError as error:
                row["status"] = str(error)
        rows.append(row)
    return {
        "status": "preview_only", "inference_enabled": config["enabled"],
        "projects_root": config.get("projects_root"),
        "model_called": False, "preexisting_sessions_excluded": excluded, "sessions": rows,
        "limits": {"daily_attempts": DAILY_ATTEMPTS, "input_characters": INPUT_CHARACTERS,
                   "output_tokens": OUTPUT_TOKENS, "disk_bytes": DISK_BYTES},
        "coverage": "Partial message text + tool success flags; no payload text is printed.",
    }


def model_body(session: dict[str, Any], model: dict[str, str]) -> dict[str, Any]:
    prepared = prepare(session, "azure", model["ASSESSMENT_MODEL_ID"])
    instructions = prepared.pop("instructions") + INSTRUCTIONS
    body = {
        "model": model["AZURE_OPENAI_DEPLOYMENT"], "instructions": instructions,
        "input": json.dumps(prepared), "store": False,
        "max_output_tokens": OUTPUT_TOKENS, "reasoning": {"effort": "low"},
        "text": {"format": {"type": "json_object"}},
    }
    if len(json.dumps(body)) > INPUT_CHARACTERS:
        raise native.CaptureError("input_limit_exceeded")
    return body


def run(
    root: Path, db: sqlite3.Connection, config: dict[str, Any], now: float,
    scheduled: bool = False, clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    if not config["enabled"]:
        raise ReflectionError("Inference is disabled; inspect preview before explicit enable")
    local = datetime.fromtimestamp(now).astimezone()
    day = local.date().isoformat()
    if scheduled and (now < config.get("schedule_after", 0) or local.hour < 9 or db.execute(
        "SELECT 1 FROM runs WHERE local_day=?", (day,),
    ).fetchone()):
        return {"status": "not_due", "model_requests": 0}
    model = load_config(Path(config["env_file"]))
    candidates, excluded = sources(config)
    reconcile(db, candidates)
    outcomes = []
    calls = 0
    errors = 0
    for candidate in candidates:
        if not candidate.archived:
            continue
        identifier = None
        try:
            captured = snapshot(candidate, config)
            body = model_body(captured.session, model)
            key = hashlib.sha256(json.dumps({
                "project": candidate.project_id, "host": candidate.host_id,
                "request": body, "endpoint": model["AZURE_OPENAI_ENDPOINT"],
                "model_id": model["ASSESSMENT_MODEL_ID"],
                "model_version": model["ASSESSMENT_MODEL_VERSION"],
            }, sort_keys=True).encode()).hexdigest()
            previous = db.execute(
                "SELECT id, state, created_at FROM attempts WHERE processing_key=?", (key,),
            ).fetchone()
            if previous:
                # Never turn an ambiguous/failed request into an automatic retry.
                status = previous["state"]
                if status == "succeeded" and not (root / "results" / f"{previous['id']}.json").exists():
                    status = "expired" if now - previous["created_at"] >= 90 * DAY else "missing_result"
                    set_session(db, candidate, status)
                if status in ("failed", "interrupted"):
                    errors += 1
                elif status == "missing_result":
                    errors += 1
                outcomes.append({"host_id": candidate.host_id, "status": status})
                continue
            attempt_time = clock()
            attempt_day = datetime.fromtimestamp(attempt_time).astimezone().date().isoformat()
            if db.execute(
                "SELECT count(*) FROM attempts WHERE local_day=?", (attempt_day,),
            ).fetchone()[0] >= DAILY_ATTEMPTS:
                set_session(db, candidate, "daily_budget_deferred")
                outcomes.append({"host_id": candidate.host_id, "status": "daily_budget_deferred"})
                continue
            quota(root, 1024 * 1024)
            recheck(captured, config)
            identifier = str(uuid.uuid4())
            db.execute(
                "INSERT INTO attempts VALUES (?, ?, ?, ?, ?, 'attempting')",
                (identifier, key, candidate.host_id, attempt_time, attempt_day),
            )
            set_session(db, candidate, "attempting")
            atomic_json(root / "snapshots" / f"{identifier}.json", {
                "captured_at": attempt_time, "session": captured.session, "request": body,
            })
            calls += 1
            response = rest(
                model["AZURE_OPENAI_ENDPOINT"].rstrip("/") + "/openai/v1/responses",
                "https://ai.azure.com", body,
            )
            if len(json.dumps(response)) > 256 * 1024:
                raise ReflectionError("Model response exceeds the local storage bound")
            assessment = response_assessment(response)
            validate_assessment(assessment, captured.session, "azure", model["ASSESSMENT_MODEL_ID"])
            recheck(captured, config)
            atomic_json(root / "results" / f"{identifier}.json", {
                "id": identifier, "created_at": attempt_time, "host_id": candidate.host_id,
                "project_id": candidate.project_id, "source_sha256": captured.source_sha256,
                "assessment": assessment, "configured_model_version": model["ASSESSMENT_MODEL_VERSION"],
                "returned_model": response.get("model"), "usage": response.get("usage"),
                "request_sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
                "rubric_sha256": hashlib.sha256(body["instructions"].encode()).hexdigest(),
                "status": "local_unreviewed_not_published",
            })
            db.execute("UPDATE attempts SET state='succeeded' WHERE id=?", (identifier,))
            set_session(db, candidate, "succeeded", identifier)
            outcomes.append({"host_id": candidate.host_id, "status": "succeeded", "result_id": identifier})
        except native.CaptureError as error:
            status = str(error)
            if identifier:
                db.execute("UPDATE attempts SET state='failed' WHERE id=?", (identifier,))
                errors += 1
            set_session(db, candidate, status)
            outcomes.append({"host_id": candidate.host_id, "status": status})
        except (OSError, ValueError, KeyError, TypeError, RuntimeError,
                subprocess.TimeoutExpired, ValidationError) as error:
            if identifier:
                db.execute("UPDATE attempts SET state='failed' WHERE id=?", (identifier,))
            set_session(db, candidate, "failed")
            errors += 1
            outcomes.append({
                "host_id": candidate.host_id, "status": "failed",
                "error_type": type(error).__name__,
                "detail": "Request/capture failed; no automatic retry and no payload logged.",
            })
    db.execute("INSERT INTO runs (created_at, local_day, errors) VALUES (?, ?, ?)", (now, day, errors))
    db.commit()
    if errors:
        status = "failed"
    elif not candidates:
        status = "no_new_sessions"
    elif not outcomes:
        status = "no_eligible_archived_sessions"
    elif any(row["status"] != "succeeded" for row in outcomes):
        status = "deferred"
    else:
        status = "completed"
    return {
        "status": status,
        "model_requests": calls, "preexisting_sessions_excluded": excluded, "sessions": outcomes,
        "publishing": "disabled_automatic_publishing_not_supported",
    }


def publish(
    root: Path, db: sqlite3.Connection, config: dict[str, Any], result_id: str, summary: str,
) -> Path:
    identifier = str(uuid.UUID(result_id))
    receipt = root / "receipts" / f"{identifier}.json"
    if receipt.exists():
        raise ReflectionError("Publication already attempted; query its receipt instead of resending")
    if (not summary.strip() or len(summary) > 1000 or "\n" in summary or "\r" in summary
            or native.message_text(summary) != summary):
        raise ReflectionError("Provide one reviewed derived summary; no code or context blocks")
    result = json.loads((root / "results" / f"{identifier}.json").read_text())
    current = db.execute(
        "SELECT * FROM sessions WHERE result_id=? AND state='succeeded'", (identifier,),
    ).fetchone()
    candidates, _ = sources(config)
    reconcile(db, candidates)
    if current is None:
        raise ReflectionError("Result is not a current successful assessment")
    candidate = next((item for item in candidates if item.host_id == current["host_id"]), None)
    if candidate is None:
        raise ReflectionError("Source session is no longer available")
    captured = snapshot(candidate, config)
    assessment = result["assessment"]
    validate_assessment(assessment, captured.session, "azure", assessment["model"]["id"])
    if any(event["text"] in summary for event in captured.session["events"]):
        raise ReflectionError("Publish derived findings, not complete source-event quotations")
    outcomes = {segment["outcome"] for segment in assessment["segments"]}
    alias = candidate.project_id
    record = {
        "TimeGenerated": datetime.fromtimestamp(result["created_at"]).astimezone().isoformat(),
        "SessionEndedAt": None, "SchemaVersion": 1, "AssessmentId": identifier,
        "SessionId": assessment["session_id"], "Revision": assessment["revision"],
        "SnapshotSha256": assessment["snapshot_sha256"],
        "ProjectId": alias, "ProjectName": alias.split("/", 1)[1],
        "ProjectCategory": alias.split("/", 1)[0], "Origin": "archived_session_assessment",
        "Outcome": next(iter(outcomes)) if len(outcomes) == 1 else "partial",
        "Summary": summary,
        "Limitations": "Partial user/assistant projection; no tool output or independent test proof. "
                        "Summary manually reviewed. SessionEndedAt is unknown. Historical archived "
                        "revision; later source eligibility changes are not synchronized to Azure.",
    }
    recheck(captured, config)
    return publish_record(record, receipt)


def service_owned(root: Path) -> None:
    import plistlib

    if PLIST.exists():
        launch = plistlib.loads(PLIST.read_bytes())
        arguments = launch.get("ProgramArguments", [])
        if (launch.get("Label") != LABEL or len(arguments) != 8
                or arguments[1:] != [
                    "-m", "tools.session_reflection", "--state-dir", str(root),
                    "run", "--execute", "--scheduled",
                ] or launch.get("WorkingDirectory") != str(ROOT)):
            raise ReflectionError("Refusing to modify an unrecognized reflection LaunchAgent")


def schedule(root: Path, config: dict[str, Any], install: bool) -> dict[str, str]:
    import plistlib

    if platform.system() != "Darwin":
        raise ReflectionError("Scheduling currently supports macOS only")
    service_owned(root)
    target = f"gui/{os.getuid()}/{LABEL}"
    if not install:
        if not PLIST.exists():
            return {"status": "not_installed", "retained_state": str(root)}
        loaded = subprocess.run(["launchctl", "print", target], capture_output=True, timeout=10)
        if loaded.returncode not in (0, 113):
            raise RuntimeError("Cannot inspect the reflection LaunchAgent")
        if loaded.returncode == 0:
            subprocess.run(["launchctl", "bootout", target], check=True, capture_output=True, timeout=10)
        PLIST.unlink()
        return {"status": "uninstalled", "retained_state": str(root)}
    if not config["enabled"]:
        raise ReflectionError("Inspect preview and explicitly enable inference before installing the schedule")
    if PLIST.exists():
        raise ReflectionError("Reflection LaunchAgent already exists; uninstall before updating")
    az = shutil.which("az")
    if not az:
        raise ReflectionError("Azure CLI is required; run az login before scheduling")
    now = datetime.fromtimestamp(time.time())
    first_run = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if first_run <= now:
        first_run += timedelta(days=1)
    config["schedule_after"] = first_run.timestamp()
    atomic_json(root / "policy.json", config)
    launch = {
        "Label": LABEL,
        "Comment": "Reviews newly archived Copilot sessions at 09:00. "
                   "Results stay local until a summary is manually reviewed and published.",
        "ProgramArguments": [str(Path(sys.executable).absolute()), "-m", "tools.session_reflection",
                             "--state-dir", str(root), "run", "--execute", "--scheduled"],
        "WorkingDirectory": str(ROOT), "RunAtLoad": True,
        "StartCalendarInterval": {"Hour": 9, "Minute": 0},
        "StartInterval": 3600, "ProcessType": "Background", "Umask": 0o077,
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            "PATH": os.pathsep.join([str(Path(az).parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
        },
        "StandardOutPath": "/dev/null", "StandardErrorPath": str(root / "worker.stderr.log"),
    }
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    with PLIST.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        plistlib.dump(launch, stream)
    try:
        subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)],
                       check=True, capture_output=True, timeout=15)
        subprocess.run(["launchctl", "print", target],
                       check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        loaded = subprocess.run(["launchctl", "print", target], capture_output=True, timeout=10)
        if loaded.returncode not in (0, 113):
            raise ReflectionError("Schedule verification failed; plist retained for explicit uninstall") from None
        if loaded.returncode == 0:
            subprocess.run(["launchctl", "bootout", target],
                           check=True, capture_output=True, timeout=10)
        PLIST.unlink()
        raise
    return {
        "status": "schedule_installed", "job_name": LABEL, "plist_file": str(PLIST),
        "daily_time": "09:00 local; hourly wake/login catch-up",
        "first_scheduled_run": first_run.astimezone().isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=STATE)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize_parser = commands.add_parser("init")
    initialize_parser.add_argument("--project", action="append", required=True)
    initialize_parser.add_argument("--editor-data", type=Path, default=native.EDITOR)
    initialize_parser.add_argument("--copilot-home", type=Path, default=native.COPILOT)
    commands.add_parser("preview")
    commands.add_parser("status")
    broaden = commands.add_parser("configure-root")
    broaden.add_argument("--projects-root", type=Path, required=True)
    broaden.add_argument("--execute", action="store_true", required=True)
    for name in ("enable", "disable", "install", "uninstall", "run"):
        sub = commands.add_parser(name)
        sub.add_argument("--execute", action="store_true", required=True)
        if name == "run":
            sub.add_argument("--scheduled", action="store_true")
    upload = commands.add_parser("publish")
    upload.add_argument("result_id")
    upload.add_argument("--summary", required=True)
    upload.add_argument("--reviewed", action="store_true", required=True)
    upload.add_argument("--execute", action="store_true", required=True)
    check = commands.add_parser("query")
    check.add_argument("result_id")
    args = parser.parse_args()
    root = args.state_dir.expanduser().absolute()
    os.umask(0o077)
    try:
        with locked(root) as db:
            now = time.time()
            prune(root, db, now)
            if args.command == "init":
                result = initialize(root, args.project, args.editor_data, args.copilot_home, now)
            else:
                config = policy(root)
                if args.command == "preview":
                    result = preview(config, db)
                elif args.command == "status":
                    candidates, excluded = sources(config)
                    reconcile(db, candidates)
                    result = {
                        "inference_enabled": config["enabled"], "since_ms": config["since_ms"],
                        "projects": list(config["projects"]), "schedule_file_exists": PLIST.exists(),
                        "job_name": LABEL,
                        "projects_root": config.get("projects_root"),
                        "root_since_ms": config.get("root_since_ms"),
                        "first_scheduled_run": config.get("schedule_after"),
                        "preexisting_sessions_excluded": excluded,
                        "sessions": [dict(row) for row in db.execute(
                            "SELECT host_id, project_id, revision, state, result_id FROM sessions",
                        )],
                        "last_run": dict(row) if (row := db.execute(
                            "SELECT created_at, errors FROM runs ORDER BY id DESC LIMIT 1",
                        ).fetchone()) else None,
                    }
                elif args.command == "configure-root":
                    approved = args.projects_root.expanduser().resolve()
                    if not approved.is_dir():
                        raise ReflectionError("Approved projects root must exist")
                    if config.get("projects_root") not in (None, str(approved)):
                        raise ReflectionError("Changing an approved root requires a reviewed policy migration")
                    if config.get("projects_root") is None:
                        config["projects_root"] = str(approved)
                        config["root_since_ms"] = int(now * 1000)
                        atomic_json(root / "policy.json", config)
                    result = {
                        "status": "root_scope_configured", "projects_root": str(approved),
                        "since_ms": config["root_since_ms"], "inference_enabled": config["enabled"],
                    }
                elif args.command in ("enable", "disable"):
                    config["enabled"] = args.command == "enable"
                    if config["enabled"]:
                        load_config(Path(config["env_file"]))
                    atomic_json(root / "policy.json", config)
                    result = {"inference_enabled": config["enabled"], "schedule_changed": False}
                elif args.command in ("install", "uninstall"):
                    result = schedule(root, config, args.command == "install")
                elif args.command == "run":
                    result = run(root, db, config, now, args.scheduled)
                elif args.command == "publish":
                    result = {"receipt_file": str(publish(
                        root, db, config, args.result_id, args.summary,
                    ))}
                else:
                    receipt = root / "receipts" / f"{str(uuid.UUID(args.result_id))}.json"
                    result = {"status": query(receipt)["status"]}
            print(json.dumps(result, indent=2))
            return 1 if result.get("status") == "failed" else 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error,
            subprocess.SubprocessError, ValidationError) as error:
        # Native/model exception messages can contain conversation text.
        detail = str(error) if isinstance(error, (ReflectionError, native.CaptureError)) else type(error).__name__
        print(f"error: reflection operation failed ({detail}); no payload logged", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
