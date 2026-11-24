import copy
import io
import json
import plistlib
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tools import native_sessions as native
from tools import session_reflection as worker
from tools.assessment import session_digest


NOW = 1_800_000_000.0
MODEL = {
    "ASSESSMENT_AZURE_AUTH": "azure_cli",
    "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
    "AZURE_OPENAI_DEPLOYMENT": "test-deployment",
    "ASSESSMENT_MODEL_ID": "test-model",
    "ASSESSMENT_MODEL_VERSION": "test-version",
}


class ReflectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.editor = self.root / "editor"
        self.copilot = self.root / "copilot"
        self.state = self.root / "state"
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.catalog = self.editor / "User/globalStorage/agent-host.db"
        self.catalog.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.catalog)) as db, db:
            db.executescript("""
                PRAGMA user_version=22;
                CREATE TABLE sessions_v2 (
                    session_uri TEXT PRIMARY KEY, source_revision INTEGER, start_time INTEGER,
                    provider TEXT, payload_version INTEGER, payload TEXT
                );
            """)
        with worker.locked(self.state):
            worker.initialize(
                self.state, [f"personal/test={self.workspace}"],
                self.editor, self.copilot, NOW,
            )
        self.config = worker.policy(self.state)
        self.host_id = ""
        self.sdk_id = ""
        self.events: list[dict] = []
        self.path = self.root / "unused"

    def event(self, kind: str, data: dict) -> dict:
        return {
            "id": str(uuid.uuid4()), "type": kind, "data": data,
            "timestamp": datetime.fromtimestamp(NOW + 1, timezone.utc).isoformat(),
        }

    def add_session(self, archived: bool = True, started: float = NOW + 1) -> native.Candidate:
        self.host_id, self.sdk_id = str(uuid.uuid4()), str(uuid.uuid4())
        payload = {
            "payloadVersion": 2,
            "data": {
                "isArchived": archived, "project": {"uri": self.workspace.as_uri()},
                "workingDirectories": [self.workspace.as_uri()],
                "chats": [{"kind": "default"}],
            },
        }
        with closing(sqlite3.connect(self.catalog)) as db, db:
            db.execute("INSERT INTO sessions_v2 VALUES (?, 1, ?, 'copilotcli', 2, ?)", (
                f"copilotcli:/{self.host_id}", int(started * 1000), json.dumps(payload),
            ))
        mapping = self.editor / "agentSessionData" / self.host_id / "session.db"
        mapping.parent.mkdir(parents=True)
        with closing(sqlite3.connect(mapping)) as db, db:
            db.executescript("PRAGMA user_version=20; CREATE TABLE session_metadata (key TEXT, value TEXT);")
            db.execute("INSERT INTO session_metadata VALUES ('defaultChatProviderData', ?)",
                       (json.dumps({"sdkSessionId": self.sdk_id}),))
        self.events = [
            self.event("session.start", {
                "version": 1, "sessionId": self.sdk_id,
                "context": {"cwd": str(self.workspace)},
            }),
            self.event("user.message", {
                "content": "Investigate the example parser.",
                "attachments": [{"content": "ATTACHMENT_MUST_NOT_LEAVE"}],
            }),
            self.event("assistant.turn_start", {"turnId": "turn-1"}),
            self.event("tool.execution_start", {
                "toolCallId": "tool-1", "toolName": "read_file",
                "arguments": {"path": "ARGUMENTS_MUST_NOT_LEAVE"},
            }),
            self.event("tool.execution_complete", {
                "toolCallId": "tool-1", "success": True,
                "result": {"content": "FILE_CONTENT_MUST_NOT_LEAVE"},
            }),
            self.event("assistant.message", {"content": "I propose a parser correction; it is unverified."}),
            self.event("assistant.turn_end", {"turnId": "turn-1"}),
        ]
        self.events[0]["timestamp"] = datetime.fromtimestamp(started, timezone.utc).isoformat()
        self.path = self.copilot / "session-state" / self.sdk_id / "events.jsonl"
        self.path.parent.mkdir(parents=True)
        self.write_events()
        candidates, _ = worker.sources(self.config)
        return next((item for item in candidates if item.host_id == self.host_id), native.Candidate(
            self.host_id, "personal/test", self.workspace, 1, archived,
            int(started * 1000), "", True,
        ))

    def write_events(self) -> None:
        self.path.write_text("".join(json.dumps(event) + "\n" for event in self.events))

    def modify_catalog(self, **changes) -> None:
        with closing(sqlite3.connect(self.catalog)) as db, db:
            raw, revision = db.execute(
                "SELECT payload, source_revision FROM sessions_v2 WHERE session_uri=?",
                (f"copilotcli:/{self.host_id}",),
            ).fetchone()
            payload = json.loads(raw)
            payload["data"].update(changes)
            db.execute("UPDATE sessions_v2 SET payload=?, source_revision=? WHERE session_uri=?",
                       (json.dumps(payload), revision + 1, f"copilotcli:/{self.host_id}"))

    def response(self, _url: str, _audience: str, body: dict) -> dict:
        session = json.loads(body["input"])["session"]
        event_ids = [event["id"] for event in session["events"]]
        assessment = {
            "schema_version": 1, "rubric_version": "s4-v1",
            "session_id": session["id"], "revision": session["revision"],
            "snapshot_sha256": session_digest(session),
            "model": {"provider": "azure", "id": "test-model"},
            "segments": [{
                "id": "parser", "task_type": "debugging", "event_ids": event_ids,
                "outcome": "unknown", "claims": [{
                    "kind": "unknown", "text": "No independent verification is visible.",
                    "evidence_ids": [event_ids[-1]], "confidence": "high",
                }],
            }],
            "limitations": ["Tool output is omitted."],
            "unknown_dimensions": ["time_saved", "satisfaction", "understanding"],
            "recommendations": [],
        }
        return {
            "status": "completed", "model": "test-model", "usage": {"input_tokens": 1},
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(assessment)},
            ]}],
        }

    def execute(self, sender=None, now: float = NOW + 60) -> dict:
        config = {**self.config, "enabled": True}
        with worker.locked(self.state) as db, \
                patch.object(worker, "load_config", return_value=MODEL), \
                patch.object(worker, "rest", side_effect=sender or self.response):
            return worker.run(self.state, db, config, now, clock=lambda: now)

    def test_preview_reads_only_new_allowlisted_sessions_and_prints_no_payload(self) -> None:
        self.add_session(started=NOW - 60)
        old_path = self.path
        old_path.write_text("INVALID_OLD_HISTORY_MUST_NOT_BE_PARSED")
        self.add_session()
        with closing(sqlite3.connect(self.catalog)) as db, db:
            db.execute("INSERT INTO sessions_v2 VALUES ('unrelated', 1, ?, 'other', 999, ?)", (
                int((NOW + 1) * 1000),
                json.dumps({"data": {"project": {"uri": (self.root / "other").as_uri()}}}),
            ))
        with worker.locked(self.state) as db, patch.object(worker, "rest") as send:
            result = worker.preview(self.config, db)
        self.assertEqual(result["preexisting_sessions_excluded"], 1)
        self.assertEqual([row["status"] for row in result["sessions"]], ["capture_ready"])
        self.assertNotIn("parser", json.dumps(result))
        self.assertFalse(result["model_called"])
        send.assert_not_called()
        self.assertEqual(old_path.read_text(), "INVALID_OLD_HISTORY_MUST_NOT_BE_PARSED")

    def test_capture_omits_payloads_reasoning_context_and_code(self) -> None:
        candidate = self.add_session()
        self.events.insert(-1, self.event("assistant.message", {
            "phase": "analysis", "content": "REASONING_MUST_NOT_LEAVE",
        }))
        self.events[1]["data"]["content"] += (
            "\n```python\nCODE_MUST_NOT_LEAVE\n```\n"
            "<system_reminder>CONTEXT_MUST_NOT_LEAVE</system_reminder>"
        )
        self.write_events()
        before = self.path.read_bytes()
        captured = worker.snapshot(candidate, self.config)
        serialized = json.dumps(captured.session)
        self.assertNotIn("MUST_NOT_LEAVE", serialized)
        self.assertIn("success=true", serialized)
        self.assertIn("not test verification", serialized)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(captured.session["id"], self.sdk_id)
        self.assertNotEqual(captured.session["id"], self.host_id)

    def test_root_scope_finds_future_projects_without_backfill_or_prefix_leakage(self) -> None:
        projects = self.root / "projects"
        projects.mkdir()
        self.config.update({"projects_root": str(projects), "root_since_ms": int((NOW + 10) * 1000)})
        original_workspace = self.workspace
        self.workspace = projects / "personal" / "old"
        self.workspace.mkdir(parents=True)
        self.add_session(started=NOW + 5)
        self.path.write_text("OLD_ROOT_CONTENT_MUST_NOT_BE_READ")
        for category in ("personal", "work"):
            self.workspace = projects / category / "same-name"
            self.workspace.mkdir(parents=True)
            self.add_session(started=NOW + 20)
        with closing(sqlite3.connect(self.catalog)) as db, db:
            db.execute("INSERT INTO sessions_v2 VALUES ('other-agent:/unrelated', 1, ?, 'other', 2, ?)", (
                int((NOW + 20) * 1000),
                json.dumps({"data": {"project": {"uri": self.workspace.as_uri()}}}),
            ))
        self.workspace = self.root / "projects-other" / "personal" / "outside"
        self.workspace.mkdir(parents=True)
        self.add_session(started=NOW + 20)
        self.path.write_text("OUTSIDE_ROOT_CONTENT_MUST_NOT_BE_READ")
        self.workspace = original_workspace
        with worker.locked(self.state) as db:
            result = worker.preview(self.config, db)
        self.assertEqual(result["preexisting_sessions_excluded"], 1)
        self.assertEqual({row["project_id"] for row in result["sessions"]},
                         {"personal/same-name", "work/same-name"})
        self.assertTrue(all(row["status"] == "capture_ready" for row in result["sessions"]))
        self.assertEqual(self.execute(now=NOW + 60)["model_requests"], 2)

    def test_root_configuration_preserves_cutoff_budget_and_explicit_projects(self) -> None:
        projects = self.root / "projects"
        projects.mkdir()
        self.add_session()
        self.execute()
        for now in (NOW + 10, NOW + 100):
            output = io.StringIO()
            with patch("sys.argv", [
                "reflection", "--state-dir", str(self.state), "configure-root",
                "--projects-root", str(projects), "--execute",
            ]), patch.object(worker.time, "time", return_value=now), redirect_stdout(output):
                self.assertEqual(worker.main(), 0)
        config = worker.policy(self.state)
        self.assertEqual(config["root_since_ms"], int((NOW + 10) * 1000))
        self.assertEqual(config["since_ms"], self.config["since_ms"])
        self.assertEqual(config["projects"], self.config["projects"])
        self.assertFalse(config["enabled"])
        with worker.locked(self.state) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)

    def test_nonarchive_and_incomplete_activity_are_deferred(self) -> None:
        candidate = self.add_session(archived=False)
        with self.assertRaisesRegex(native.CaptureError, "not_archived"):
            worker.snapshot(candidate, self.config)
        self.modify_catalog(isArchived=True)
        candidate = worker.sources(self.config)[0][0]
        self.events.pop()
        self.write_events()
        with self.assertRaisesRegex(native.CaptureError, "active_or_uncertain"):
            worker.snapshot(candidate, self.config)
        self.events.append(self.event("session.shutdown", {}))
        self.write_events()
        with self.assertRaisesRegex(native.CaptureError, "active_or_uncertain"):
            worker.snapshot(candidate, self.config)

    def test_rejects_unknown_schemas_partial_stream_mapping_and_coverage(self) -> None:
        candidate = self.add_session()
        original = self.path.read_bytes()
        self.path.write_bytes(original.rstrip(b"\n"))
        with self.assertRaisesRegex(native.CaptureError, "incomplete_source"):
            worker.snapshot(candidate, self.config)
        self.path.write_bytes(original)
        self.events.append(self.event("session.compaction_complete", {}))
        self.write_events()
        with self.assertRaisesRegex(native.CaptureError, "unsupported_runtime_event"):
            worker.snapshot(candidate, self.config)
        self.events.pop()
        self.write_events()
        self.modify_catalog(chats=[{"kind": "default"}, {"kind": "other"}])
        candidate = worker.sources(self.config)[0][0]
        with self.assertRaisesRegex(native.CaptureError, "unsupported_chat_coverage"):
            worker.snapshot(candidate, self.config)
        with closing(sqlite3.connect(self.catalog)) as db, db:
            db.execute("PRAGMA user_version=999")
        with self.assertRaisesRegex(native.CaptureError, "unsupported_source_schema"):
            worker.sources(self.config)

    def test_pending_tools_unanswered_users_and_secret_markers_are_deferred(self) -> None:
        candidate = self.add_session()
        original = copy.deepcopy(self.events)
        for defect, reason in (
            ("tool", "active_or_uncertain"), ("user", "active_or_uncertain"),
            ("secret", "possible_secret"), ("id", "invalid_or_duplicate"),
        ):
            with self.subTest(defect=defect):
                self.events = copy.deepcopy(original)
                if defect == "tool":
                    self.events = [event for event in self.events if event["type"] != "tool.execution_complete"]
                elif defect == "user":
                    self.events.append(self.event("user.message", {"content": "One more question."}))
                elif defect == "secret":
                    self.events[1]["data"]["content"] = "-----BEGIN RSA PRIVATE KEY-----"
                else:
                    self.events[-1]["id"] = self.events[0]["id"]
                self.write_events()
                with self.assertRaisesRegex(native.CaptureError, reason):
                    worker.snapshot(candidate, self.config)

    def test_disabled_gate_calls_no_model_and_reserves_no_attempts(self) -> None:
        with worker.locked(self.state) as db, patch.object(worker, "rest") as send:
            with self.assertRaisesRegex(worker.ReflectionError, "disabled"):
                worker.run(self.state, db, self.config, NOW)
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0], 0)
            send.assert_not_called()

    def test_success_is_local_idempotent_private_and_respects_request_shape(self) -> None:
        self.add_session()
        bodies = []

        def send(url, audience, body):
            self.assertEqual(audience, "https://ai.azure.com")
            self.assertFalse(body["store"])
            self.assertNotIn("tools", body)
            self.assertEqual(body["max_output_tokens"], 2000)
            self.assertLessEqual(len(json.dumps(body)), 20_000)
            self.assertNotIn("MUST_NOT_LEAVE", json.dumps(body))
            bodies.append(body)
            return self.response(url, audience, body)

        result = self.execute(send)
        self.assertEqual(result["model_requests"], 1)
        identifier = result["sessions"][0]["result_id"]
        again = self.execute(send)
        self.assertEqual(again["model_requests"], 0)
        self.assertEqual(len(bodies), 1)
        for directory in ("snapshots", "results"):
            path = self.state / directory / f"{identifier}.json"
            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertEqual(list((self.state / "receipts").iterdir()), [])

    def test_daily_budget_counts_failures_and_never_automatically_retries(self) -> None:
        for _ in range(6):
            self.add_session()
        sent = []

        def fail(*args):
            sent.append(args)
            raise RuntimeError("PRIVATE_PROVIDER_ERROR_MUST_NOT_BE_LOGGED")

        result = self.execute(fail)
        self.assertEqual(result["model_requests"], 5)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(sum(row["status"] == "daily_budget_deferred" for row in result["sessions"]), 1)
        self.assertNotIn("PRIVATE_PROVIDER", json.dumps(result))
        self.assertEqual(self.execute(fail)["model_requests"], 0)
        self.assertEqual(self.execute(fail, now=NOW + worker.DAY)["model_requests"], 1)
        self.assertEqual(len(sent), 6)

    def test_oversized_input_is_deferred_without_truncation_or_budget_use(self) -> None:
        self.add_session()
        self.events[1]["data"]["content"] = "x" * 20_000
        self.write_events()
        result = self.execute()
        self.assertEqual(result["model_requests"], 0)
        self.assertEqual(result["sessions"][0]["status"], "input_limit_exceeded")
        with worker.locked(self.state) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0], 0)

    def test_exact_input_character_boundary_and_reservation_day(self) -> None:
        candidate = self.add_session()
        session = worker.snapshot(candidate, self.config).session
        session["events"][0]["text"] = "x"
        size = len(json.dumps(worker.model_body(session, MODEL)))
        session["events"][0]["text"] = "x" * (1 + 20_000 - size)
        self.assertEqual(len(json.dumps(worker.model_body(session, MODEL))), 20_000)
        session["events"][0]["text"] += "x"
        with self.assertRaisesRegex(native.CaptureError, "input_limit_exceeded"):
            worker.model_body(session, MODEL)
        request_time = NOW + worker.DAY
        with worker.locked(self.state) as db, \
                patch.object(worker, "load_config", return_value=MODEL), \
                patch.object(worker, "rest", side_effect=self.response):
            worker.run(self.state, db, {**self.config, "enabled": True}, NOW,
                       clock=lambda: request_time)
            row = db.execute("SELECT created_at, local_day FROM attempts").fetchone()
            self.assertEqual(row["created_at"], request_time)
            self.assertEqual(row["local_day"],
                             datetime.fromtimestamp(request_time).astimezone().date().isoformat())

    def test_invalid_model_output_is_not_saved_or_retried(self) -> None:
        self.add_session()
        invalid = lambda *_: {"status": "completed", "output": [{
            "type": "message", "content": [{"type": "output_text", "text": '{"private":"DO_NOT_ECHO"}'}],
        }]}
        result = self.execute(invalid)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("DO_NOT_ECHO", json.dumps(result))
        self.assertEqual(list((self.state / "results").iterdir()), [])
        self.assertEqual(self.execute(invalid)["model_requests"], 0)

    def test_stale_source_after_inference_never_creates_current_result(self) -> None:
        self.add_session()

        def changed(url, audience, body):
            response = self.response(url, audience, body)
            self.modify_catalog(isArchived=False)
            return response

        result = self.execute(changed)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(list((self.state / "results").iterdir()), [])
        with worker.locked(self.state) as db:
            self.assertIsNone(db.execute("SELECT result_id FROM sessions").fetchone()[0])

    def test_resume_supersedes_and_later_archive_produces_new_revision(self) -> None:
        self.add_session()
        first = self.execute()
        identifier = first["sessions"][0]["result_id"]
        self.modify_catalog(isArchived=False)
        with worker.locked(self.state) as db:
            worker.preview(self.config, db)
            row = db.execute("SELECT state, result_id FROM sessions").fetchone()
            self.assertEqual(tuple(row), ("superseded", None))
        self.modify_catalog(isArchived=True)
        second = self.execute()
        self.assertNotEqual(identifier, second["sessions"][0]["result_id"])
        self.assertEqual(second["model_requests"], 1)

    def test_interrupted_attempt_is_visible_and_not_retried(self) -> None:
        self.add_session()
        result = self.execute()
        identifier = result["sessions"][0]["result_id"]
        with worker.locked(self.state) as db:
            db.execute("UPDATE attempts SET state='attempting' WHERE id=?", (identifier,))
            db.execute("UPDATE sessions SET state='attempting', result_id=NULL")
            db.commit()
        result = self.execute()
        self.assertEqual(result["model_requests"], 0)
        self.assertEqual(result["sessions"][0]["status"], "interrupted")
        self.assertEqual(result["status"], "failed")

    def test_retention_uses_creation_time_and_keeps_budget_tombstones(self) -> None:
        self.add_session()
        result = self.execute()
        identifier = result["sessions"][0]["result_id"]
        snapshot = self.state / "snapshots" / f"{identifier}.json"
        assessment = self.state / "results" / f"{identifier}.json"
        snapshot.touch()
        with worker.locked(self.state) as db:
            worker.prune(self.state, db, NOW + 60 + 30 * worker.DAY)
            self.assertFalse(snapshot.exists())
            self.assertTrue(assessment.exists())
            worker.prune(self.state, db, NOW + 60 + 90 * worker.DAY)
            self.assertFalse(assessment.exists())
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT state FROM sessions").fetchone()[0], "expired")
        self.assertTrue(self.path.exists())
        rerun = self.execute(now=NOW + 60 + 90 * worker.DAY)
        self.assertEqual(rerun["model_requests"], 0)
        self.assertEqual(rerun["sessions"][0]["status"], "expired")

    def test_worker_lock_and_quota_are_explicit_failures(self) -> None:
        with worker.locked(self.state):
            with self.assertRaisesRegex(worker.ReflectionError, "already running"):
                with worker.locked(self.state):
                    self.fail("Overlapping worker acquired the lock")
        with patch.object(worker, "DISK_BYTES", 1):
            with self.assertRaisesRegex(worker.ReflectionError, "quota exceeded"):
                worker.quota(self.state)

    def test_reinitialization_never_resets_cutoff_or_ledger(self) -> None:
        before = (self.state / "policy.json").read_bytes()
        with worker.locked(self.state):
            with self.assertRaisesRegex(worker.ReflectionError, "already exists"):
                worker.initialize(self.state, [], self.editor, self.copilot, NOW + 1)
        self.assertEqual(before, (self.state / "policy.json").read_bytes())

    def test_scheduling_is_explicit_gated_and_ownership_checked(self) -> None:
        plist = self.root / "LaunchAgents" / "reflection.plist"
        with patch.object(worker, "PLIST", plist), \
                patch.object(worker.platform, "system", return_value="Darwin"), \
                patch.object(worker.shutil, "which", return_value="/usr/local/bin/az"), \
                patch.object(worker.subprocess, "run") as process:
            with self.assertRaisesRegex(worker.ReflectionError, "enable inference"):
                worker.schedule(self.state, self.config, True)
            process.assert_not_called()
            result = worker.schedule(self.state, {**self.config, "enabled": True}, True)
            self.assertEqual(result["status"], "schedule_installed")
            launch = plistlib.loads(plist.read_bytes())
            self.assertEqual(launch["StartCalendarInterval"], {"Hour": 9, "Minute": 0})
            self.assertIn("--scheduled", launch["ProgramArguments"])
            self.assertGreater(worker.policy(self.state)["schedule_after"], worker.time.time())
            worker.service_owned(self.state)
            plist.write_bytes(plistlib.dumps({"Label": worker.LABEL, "ProgramArguments": ["/unrelated"]}))
            with self.assertRaisesRegex(worker.ReflectionError, "unrecognized"):
                worker.schedule(self.state, self.config, False)

    def test_scheduled_catchup_runs_once_per_day_after_nine(self) -> None:
        config = {**self.config, "enabled": True}
        # Use local wall time to make the scheduling contract timezone independent.
        local = datetime.fromtimestamp(NOW).replace(hour=8, minute=0)
        with worker.locked(self.state) as db, patch.object(worker, "load_config", return_value=MODEL):
            self.assertEqual(worker.run(self.state, db, config, local.timestamp(), True)["status"], "not_due")
            late = local.replace(hour=10).timestamp()
            self.assertEqual(worker.run(self.state, db, config, late, True)["status"], "no_new_sessions")
            self.assertEqual(worker.run(self.state, db, config, late + 60, True)["status"], "not_due")

    def test_installation_gate_prevents_immediate_assessment(self) -> None:
        self.add_session()
        config = {**self.config, "enabled": True, "schedule_after": NOW + worker.DAY}
        with worker.locked(self.state) as db, \
                patch.object(worker, "load_config") as model, patch.object(worker, "rest") as send:
            result = worker.run(self.state, db, config, NOW + 60, scheduled=True)
        self.assertEqual(result["status"], "not_due")
        model.assert_not_called()
        send.assert_not_called()

    def test_failed_schedule_verification_unloads_before_removing_plist(self) -> None:
        plist = self.root / "LaunchAgents" / "reflection.plist"
        import subprocess

        with patch.object(worker, "PLIST", plist), \
                patch.object(worker.platform, "system", return_value="Darwin"), \
                patch.object(worker.shutil, "which", return_value="/usr/local/bin/az"), \
                patch.object(worker.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0),
                    subprocess.CalledProcessError(1, ["launchctl", "print"]),
                    subprocess.CompletedProcess([], 0),
                    subprocess.CompletedProcess([], 0),
                ]) as process:
            with self.assertRaises(subprocess.CalledProcessError):
                worker.schedule(self.state, {**self.config, "enabled": True}, True)
            self.assertEqual(process.call_args.args[0][:2], ["launchctl", "bootout"])
            self.assertFalse(plist.exists())

    def test_manual_publishing_uses_reviewed_summary_and_blocks_replay_and_staleness(self) -> None:
        self.add_session()
        result = self.execute()
        identifier = result["sessions"][0]["result_id"]
        # Usage bookkeeping does not change the assessed conversation projection.
        self.events.append(self.event("session.usage_checkpoint", {"usage": "omitted"}))
        self.write_events()
        uploaded = []

        def upload(record, receipt):
            uploaded.append(record)
            worker.atomic_json(receipt, {"status": "accepted_not_yet_query_verified", "record": record})
            return receipt

        with worker.locked(self.state) as db, patch.object(worker, "publish_record", side_effect=upload):
            receipt = worker.publish(self.state, db, self.config, identifier, "Review found incomplete verification.")
            self.assertTrue(receipt.exists())
            with self.assertRaisesRegex(worker.ReflectionError, "already attempted"):
                worker.publish(self.state, db, self.config, identifier, "Review found incomplete verification.")
        record = uploaded[0]
        self.assertEqual(record["Origin"], "archived_session_assessment")
        self.assertEqual(record["Summary"], "Review found incomplete verification.")
        self.assertIsNone(record["SessionEndedAt"])
        self.assertNotIn("No independent verification is visible.", json.dumps(record))
        receipt.unlink()
        self.modify_catalog(isArchived=False)
        with worker.locked(self.state) as db, patch.object(worker, "publish_record") as upload:
            with self.assertRaises((worker.ReflectionError, native.CaptureError)):
                worker.publish(self.state, db, self.config, identifier, "Review found incomplete verification.")
            upload.assert_not_called()

    def test_cli_preview_never_calls_model_installs_jobs_or_echoes_session_content(self) -> None:
        self.add_session()
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.argv", [
            "reflection", "--state-dir", str(self.state), "preview",
        ]), redirect_stdout(output), redirect_stderr(errors), \
                patch.object(worker, "rest") as send, patch.object(worker, "schedule") as install:
            self.assertEqual(worker.main(), 0)
        self.assertFalse(json.loads(output.getvalue())["model_called"])
        self.assertNotIn("parser", output.getvalue())
        self.assertEqual(errors.getvalue(), "")
        send.assert_not_called()
        install.assert_not_called()


if __name__ == "__main__":
    unittest.main()
