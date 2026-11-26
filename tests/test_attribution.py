import copy
import http.client
import json
import logging
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from tools import attribution
from tools import native_collector as collector


class AttributionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.desktop = self.root / "desktop/session-store.db"
        self.desktop.parent.mkdir()
        with closing(sqlite3.connect(self.desktop)) as db:
            db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT, host_type TEXT)")
        desktop_path = patch.object(attribution, "DESKTOP_CATALOG", self.desktop)
        desktop_path.start()
        self.addCleanup(desktop_path.stop)
        self.editor = self.root / "editor"
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
        self.projects = {
            "personal/alpha": str(self.root / "alpha/shared"),
            "work/beta": str(self.root / "beta/shared"),
        }
        for workspace in self.projects.values():
            Path(workspace).mkdir(parents=True)
        self.policy_file = self.root / "policy.json"
        self.write_policy()
        self.policy = attribution.Policy.load(self.policy_file)
        self.alpha = self.add_session("personal/alpha")
        self.beta = self.add_session("work/beta")
        self.resolver = attribution.Resolver(self.policy)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def write_policy(self) -> None:
        self.policy_file.write_text(json.dumps({
            "version": 1, "editor": str(self.editor), "projects": self.projects,
        }))

    def add_session(self, alias: str, sdk: str | None = None) -> tuple[str, str]:
        host, sdk = str(uuid.uuid4()), sdk or str(uuid.uuid4())
        workspace = Path(self.projects[alias])
        payload = {"payloadVersion": 2, "data": {
            "isArchived": False, "project": {"uri": workspace.as_uri()},
            "workingDirectories": [workspace.as_uri()], "chats": [{"kind": "default"}],
        }}
        with closing(sqlite3.connect(self.catalog)) as db, db:
            db.execute("INSERT INTO sessions_v2 VALUES (?, 1, 1, 'copilotcli', 2, ?)",
                       (f"copilotcli:/{host}", json.dumps(payload)))
        path = self.editor / "agentSessionData" / host / "session.db"
        path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("PRAGMA user_version=20; CREATE TABLE session_metadata (key TEXT, value TEXT);")
            db.execute("INSERT INTO session_metadata VALUES ('defaultChatProviderData', ?)",
                       (json.dumps({"sdkSessionId": sdk}),))
        return host, sdk

    def attributes(self, conversation: str | None = None,
                   host: str | None = None) -> list[dict]:
        values = {}
        if conversation is not None:
            values["gen_ai.conversation.id"] = conversation
        if host is not None:
            values["vscode.agent_host.session.uri"] = f"copilotcli:/{host}"
        return [{"key": key, "value": {"stringValue": value}} for key, value in values.items()]

    def payload(self, attributes: list[dict]) -> dict:
        return {"resourceSpans": [{"scopeSpans": [{"spans": [{
            "traceId": "a" * 32, "spanId": "b" * 16,
            "name": "CONTENT_MUST_DISAPPEAR", "attributes": attributes,
        }]}]}]}

    def test_exact_host_sdk_mapping_concurrency_and_duplicate_folder_names(self) -> None:
        snapshot = self.resolver.snapshot()
        for identity in self.alpha:
            self.assertEqual(snapshot.resolve(self.attributes(identity)).project, "personal/alpha")
        self.assertEqual(snapshot.resolve(self.attributes(
            self.alpha[1], self.alpha[0])).project, "personal/alpha")
        for identity in self.beta:
            self.assertEqual(snapshot.resolve(self.attributes(identity)).project, "work/beta")
        self.assertEqual(snapshot.resolve(self.attributes(
            self.alpha[1], self.beta[0])).reason, "identity_conflict")

    def test_missing_unknown_malformed_and_duplicate_identity_never_guess(self) -> None:
        snapshot = self.resolver.snapshot()
        cases = [
            ([], "missing_identity"),
            (self.attributes(str(uuid.uuid4())), "unknown_session"),
            (self.attributes("workspace/path"), "invalid_identity"),
            (self.attributes(self.alpha[0]) * 2, "invalid_identity"),
            ([{"key": "gen_ai.conversation.id", "value": {"intValue": "10"}}], "invalid_identity"),
            (self.attributes(str(uuid.uuid4()), self.alpha[0]), "unknown_session"),
        ]
        for attributes, reason in cases:
            with self.subTest(reason=reason, attributes=attributes):
                result = snapshot.resolve(attributes)
                self.assertEqual(result.project, "unattributed")
                self.assertEqual(result.reason, reason)

    def test_ambiguous_sdk_or_host_sdk_collision_is_not_attributed(self) -> None:
        self.add_session("work/beta", sdk=self.alpha[1])
        self.add_session("work/beta", sdk=self.alpha[0])
        snapshot = self.resolver.snapshot()
        for identifier in self.alpha:
            self.assertEqual(snapshot.resolve(self.attributes(identifier)).reason, "identity_conflict")

    def test_unapproved_project_is_not_resolved(self) -> None:
        policy = attribution.Policy(self.editor, {"personal/alpha": self.projects["personal/alpha"]})
        snapshot = attribution.Resolver(policy).snapshot()
        self.assertEqual(snapshot.resolve(self.attributes(self.beta[0])).project, "unattributed")
        self.assertEqual(snapshot.resolve(self.attributes(self.alpha[0])).project, "personal/alpha")

    def discovery_policy(self, excluded: list[str] | None = None) -> attribution.Policy:
        self.policy_file.write_text(json.dumps({
            "version": 2, "editor": str(self.editor),
            "projects_root": str(self.root / "projects"),
            "excluded_workspaces": excluded or [],
        }))
        return attribution.Policy.load(self.policy_file)

    def add_desktop(self, folder: str | None, host: str = "github",
                    identity: str | None = None) -> str:
        identity = identity or str(uuid.uuid4())
        with closing(sqlite3.connect(self.desktop)) as db, db:
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (identity, folder, host))
        return identity

    def test_desktop_exact_id_and_shared_workspace_with_vscode(self) -> None:
        policy = self.discovery_policy()
        workspace = policy.projects_root / "personal/alpha"
        desktop = self.add_desktop(str(workspace))
        self.projects["personal/alpha"] = str(workspace)
        vscode = self.add_session("personal/alpha")
        snapshot = attribution.Resolver(policy).snapshot()
        self.assertEqual(snapshot.resolve(self.attributes(desktop), "desktop").project, "personal/alpha")
        self.assertEqual(snapshot.resolve(self.attributes(desktop)).project, "personal/alpha")
        self.assertEqual(snapshot.resolve(self.attributes(vscode[1]), "vscode").project, "personal/alpha")
        self.assertEqual(snapshot.resolve(self.attributes(desktop), "vscode").reason, "unknown_session")
        self.assertEqual(snapshot.resolve(self.attributes(vscode[1]), "desktop").reason, "unknown_session")
        self.assertEqual(snapshot.resolve(self.attributes(desktop, vscode[0]), "desktop").reason,
                         "unsupported_source")
        with patch.object(attribution, "desktop_rows", side_effect=AssertionError("not v1")):
            self.assertEqual(self.resolver.snapshot().resolve(
                self.attributes(self.alpha[1])).project, "personal/alpha")

    def test_desktop_host_paths_exclusions_and_names_follow_discovery_rules(self) -> None:
        policy = self.discovery_policy([str(self.root / "projects/private")])
        root = policy.projects_root
        root.mkdir()
        (root / "linked").symlink_to(self.root / "alpha", target_is_directory=True)
        cases = [
            (str(root / "personal/deep/alpha"), "github", "personal/alpha", "resolved"),
            (str(root / "single"), "github", "projects/single", "resolved"),
            (str(root / "private/alpha"), "github", "unattributed", "unknown_session"),
            (str(root / "private/child/alpha"), "github", "unattributed", "unknown_session"),
            (str(self.root / "outside"), "github", "unattributed", "unknown_session"),
            (str(root / "linked/shared"), "github", "unattributed", "unknown_session"),
            (str(root), "github", "unattributed", "unknown_session"),
            (str(root / "personal/remote"), "ssh", "unattributed", "unknown_session"),
            (str(root / "personal/bad name"), "github", "unattributed", "unsupported_source"),
            ("relative", "github", "unattributed", "unsupported_source"),
            ("", "github", "unattributed", "unsupported_source"),
            (None, "github", "unattributed", "unsupported_source"),
        ]
        identities = [self.add_desktop(folder, host) for folder, host, _, _ in cases]
        with self.assertLogs(attribution.LOG, logging.WARNING):
            snapshot = attribution.Resolver(policy).snapshot()
        for identity, (_, _, project, reason) in zip(identities, cases):
            with self.subTest(identity=identity):
                result = snapshot.resolve(self.attributes(identity), "desktop")
                expected_reason = "lookup_incomplete" if reason == "unknown_session" else reason
                self.assertEqual((result.project, result.reason), (project, expected_reason))

    def test_desktop_source_uuid_and_global_alias_collisions(self) -> None:
        policy = self.discovery_policy()
        root = policy.projects_root
        self.projects["personal/alpha"] = str(root / "personal/alpha")
        vscode = self.add_session("personal/alpha")
        self.add_desktop(str(root / "personal/alpha"), identity=vscode[1])
        other = self.add_desktop(str(root / "work/two/shared"))
        self.projects["work/beta"] = str(root / "work/one/shared")
        conflict = self.add_session("work/beta")
        with self.assertLogs(attribution.LOG, logging.WARNING):
            snapshot = attribution.Resolver(policy).snapshot()
        attributes = self.attributes(vscode[1])
        self.assertEqual(snapshot.resolve(attributes).reason, "identity_conflict")
        for source in ("vscode", "desktop"):
            self.assertEqual(snapshot.resolve(attributes, source).project, "personal/alpha")
        for identity, source in ((other, "desktop"), (conflict[1], "vscode")):
            self.assertEqual(snapshot.resolve(self.attributes(identity), source).reason,
                             "identity_conflict")

    def test_desktop_missing_schema_lock_and_overflow_fail_without_creating_db(self) -> None:
        policy = self.discovery_policy()
        self.desktop.unlink()
        with self.assertLogs(attribution.LOG, logging.WARNING) as logs:
            snapshot = attribution.Resolver(policy).snapshot()
        self.assertFalse(self.desktop.exists())
        self.assertEqual(snapshot.resolve(self.attributes(self.alpha[1])).reason, "lookup_failed")
        self.assertNotIn(str(self.root), "".join(logs.output))
        with closing(sqlite3.connect(self.desktop)) as db:
            db.execute("CREATE TABLE sessions (id TEXT, cwd TEXT, host_type TEXT)")
        with self.assertLogs(attribution.LOG, logging.WARNING):
            snapshot = attribution.Resolver(policy).snapshot()
        self.assertEqual(snapshot.unknown_reason, "lookup_failed")
        with closing(sqlite3.connect(self.desktop)) as db:
            db.execute("PRAGMA user_version=999")
        with self.assertLogs(attribution.LOG, logging.WARNING):
            snapshot = attribution.Resolver(policy).snapshot()
        self.assertEqual(snapshot.unknown_reason, "lookup_failed")
        for failure in (sqlite3.OperationalError("locked PRIVATE_PATH"), ValueError("schema")):
            with patch.object(attribution, "desktop_rows", side_effect=failure):
                with self.assertLogs(attribution.LOG, logging.WARNING) as logs:
                    snapshot = attribution.Resolver(policy).snapshot()
                self.assertEqual(snapshot.unknown_reason, "lookup_failed")
                self.assertNotIn("PRIVATE_PATH", "".join(logs.output))
        with closing(sqlite3.connect(self.desktop)) as db:
            db.execute("PRAGMA user_version=0")
            db.execute("DROP TABLE sessions")
            db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT, host_type TEXT)")
        for _ in range(2):
            self.add_desktop(str(policy.projects_root / "personal/alpha"))
        with patch.object(attribution.native, "MAX_SESSIONS", 1):
            with self.assertLogs(attribution.LOG, logging.WARNING):
                snapshot = attribution.Resolver(policy).snapshot()
        self.assertEqual(snapshot.unknown_reason, "lookup_failed")

    def test_desktop_metadata_is_reloaded_and_malformed_ids_are_not_guessed(self) -> None:
        policy = self.discovery_policy()
        identity = self.add_desktop(str(policy.projects_root / "personal/alpha"))
        self.add_desktop(str(policy.projects_root / "personal/alpha"), identity="not-a-uuid")
        resolver = attribution.Resolver(policy)
        with self.assertLogs(attribution.LOG, logging.WARNING):
            snapshot = resolver.snapshot()
        self.assertEqual(snapshot.resolve(self.attributes(identity)).project, "personal/alpha")
        self.assertEqual(snapshot.resolve(self.attributes(str(uuid.uuid4()))).reason, "lookup_incomplete")
        with closing(sqlite3.connect(self.desktop)) as db, db:
            db.execute("UPDATE sessions SET cwd=? WHERE id=?",
                       (str(policy.projects_root / "work/beta"), identity))
        with self.assertLogs(attribution.LOG, logging.WARNING):
            snapshot = resolver.snapshot()
        self.assertEqual(snapshot.resolve(self.attributes(identity)).project, "work/beta")

    def test_discovery_includes_new_projects_but_excludes_directories_and_outside_root(self) -> None:
        root = self.root / "projects"
        excluded = root / "msft/private"
        resolver = attribution.Resolver(self.discovery_policy([str(excluded)]))
        included = None
        self.assertEqual(resolver.snapshot().resolve(
            self.attributes(self.alpha[0])).project, "unattributed")
        for alias, folder, expected in (
            ("personal/new", "personal/new", "personal/new"),
            ("msft/public", "msft/public", "msft/public"),
            ("personal/nested", "personal/new/subfolder", "personal/subfolder"),
            ("msft/nested", "msft/scb/COIN_AI_SDK", "msft/COIN_AI_SDK"),
            ("personal/deep", "personal/experiments/tools/my-app", "personal/my-app"),
            ("standalone", "standalone", "projects/standalone"),
            ("msft/private", "msft/private", "unattributed"),
            ("msft/child", "msft/private/child", "unattributed"),
            ("msft/private-other", "msft/private-other", "msft/private-other"),
        ):
            self.projects[alias] = str(root / folder)
            identities = self.add_session(alias)
            if alias == "personal/new":
                included = identities[0]
            for identity in identities:
                with self.subTest(folder=folder, identity=identity):
                    self.assertEqual(resolver.snapshot().resolve(
                        self.attributes(identity)).project, expected)
        excluded_policy = self.discovery_policy([str(root / "personal")])
        snapshot = attribution.Resolver(excluded_policy).snapshot()
        self.assertEqual(snapshot.resolve(
            self.attributes(included)).project, "unattributed")
        self.assertEqual(snapshot.resolve(
            self.attributes(identities[0])).project, "msft/private-other")

    def test_discovery_rejects_alias_collisions_without_blocking_other_projects(self) -> None:
        policy = self.discovery_policy()
        root = self.root / "projects"
        identities = {}
        for alias, folder in (
            ("first", "msft/team-one/shared"),
            ("second", "msft/team-two/shared"),
            ("other-category", "personal/shared"),
            ("unrelated", "msft/unique"),
        ):
            self.projects[alias] = str(root / folder)
            identities[alias] = self.add_session(alias)
        duplicate_session = self.add_session("other-category")
        with self.assertLogs(attribution.LOG, logging.WARNING) as logs:
            snapshot = attribution.Resolver(policy).snapshot()
        for alias in ("first", "second"):
            for identity in identities[alias]:
                with self.subTest(alias=alias, identity=identity):
                    result = snapshot.resolve(self.attributes(identity))
                    self.assertEqual(result.project, "unattributed")
                    self.assertEqual(result.reason, "identity_conflict")
        for identity in (*identities["other-category"], *duplicate_session):
            self.assertEqual(snapshot.resolve(self.attributes(identity)).project, "personal/shared")
        self.assertEqual(snapshot.resolve(
            self.attributes(identities["unrelated"][1])).project, "msft/unique")
        self.assertNotIn(str(root), "".join(logs.output))
        snapshot = attribution.Resolver(self.discovery_policy(
            [str(root / "msft/team-two")])).snapshot()
        for identity in identities["first"]:
            self.assertEqual(snapshot.resolve(self.attributes(identity)).project, "msft/shared")
        self.assertEqual(snapshot.resolve(
            self.attributes(identities["second"][1])).project, "unattributed")

    def test_discovery_rejects_unsafe_alias_and_symlink_escape(self) -> None:
        root = self.root / "projects"
        root.mkdir()
        (root / "linked").symlink_to(self.root / "alpha", target_is_directory=True)
        self.projects["personal/linked"] = str(root / "linked/shared")
        linked = self.add_session("personal/linked")
        self.projects["personal/invalid"] = str(root / "personal/name with spaces")
        invalid = self.add_session("personal/invalid")
        resolver = attribution.Resolver(self.discovery_policy())
        with self.assertLogs(attribution.LOG, logging.WARNING):
            snapshot = resolver.snapshot()
        self.assertEqual(snapshot.resolve(self.attributes(linked[0])).project, "unattributed")
        self.assertEqual(snapshot.resolve(self.attributes(invalid[0])).reason, "unsupported_source")

    def test_discovery_policy_rejects_relative_paths_and_outside_exclusions(self) -> None:
        for key, value in (
            ("projects_root", "relative"),
            ("excluded_workspaces", ["relative"]),
            ("excluded_workspaces", [str(self.root / "outside")]),
            ("excluded_workspaces", "not-an-array"),
        ):
            self.discovery_policy()
            policy = json.loads(self.policy_file.read_text())
            policy[key] = value
            self.policy_file.write_text(json.dumps(policy))
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                attribution.Policy.load(self.policy_file)

    def test_uninitialized_catalog_revision_does_not_block_other_discovered_projects(self) -> None:
        policy = self.discovery_policy()
        self.projects["personal/ready"] = str(self.root / "projects/personal/ready")
        ready = self.add_session("personal/ready")
        pending = self.add_session("personal/ready")
        with closing(sqlite3.connect(self.catalog)) as db, db:
            db.execute("UPDATE sessions_v2 SET source_revision = 0 WHERE session_uri = ?",
                       (f"copilotcli:/{pending[0]}",))
        with self.assertLogs(attribution.LOG, logging.WARNING):
            snapshot = attribution.Resolver(policy).snapshot()
        self.assertEqual(snapshot.resolve(self.attributes(ready[0])).project, "personal/ready")
        self.assertEqual(snapshot.resolve(self.attributes(pending[0])).reason, "unsupported_source")

    def test_policy_rejects_overlaps_bad_aliases_and_relative_paths(self) -> None:
        for projects in (
            {"bad/alias/extra": "/tmp/example"},
            {"personal/a": "relative"},
            {"synthetic/demo": "/tmp/example"},
            {"personal/a": "/tmp/example", "work/b": "/tmp/example/sub"},
            {"personal/a": "/tmp/example", "work/b": "/tmp/example"},
        ):
            with self.subTest(projects=projects):
                self.projects = projects
                self.write_policy()
                with self.assertRaises(ValueError):
                    attribution.Policy.load(self.policy_file)

    def test_schema_failure_and_missing_mapping_are_explicit_and_content_free(self) -> None:
        mapping = self.editor / "agentSessionData" / self.alpha[0] / "session.db"
        mapping.unlink()
        with self.assertLogs(attribution.LOG, logging.WARNING) as logs:
            snapshot = self.resolver.snapshot()
        self.assertEqual(snapshot.resolve(self.attributes(self.alpha[0])).reason, "unsupported_source")
        self.assertEqual(snapshot.resolve(self.attributes(self.beta[0])).project, "work/beta")
        self.assertNotIn(str(self.root), "".join(logs.output))
        self.assertFalse(mapping.exists())
        with closing(sqlite3.connect(self.catalog)) as db:
            db.execute("PRAGMA user_version=999")
        with self.assertLogs(attribution.LOG, logging.WARNING):
            snapshot = self.resolver.snapshot()
        self.assertEqual(snapshot.resolve(self.attributes(self.beta[0])).reason, "lookup_failed")

    def test_changed_catalog_is_rejected_and_next_request_reloads_mapping(self) -> None:
        original = attribution.native.discover
        with patch.object(attribution.native, "discover",
                          side_effect=[original(self.editor, self.projects, 0), ([], 0)]):
            with self.assertLogs(attribution.LOG, logging.WARNING):
                snapshot = self.resolver.snapshot()
        self.assertEqual(snapshot.resolve(self.attributes(self.alpha[0])).reason, "lookup_failed")
        mapping = self.editor / "agentSessionData" / self.alpha[0] / "session.db"
        new_sdk = str(uuid.uuid4())
        with closing(sqlite3.connect(mapping)) as db, db:
            db.execute("UPDATE session_metadata SET value=?", (json.dumps({"sdkSessionId": new_sdk}),))
        snapshot = self.resolver.snapshot()
        self.assertEqual(snapshot.resolve(self.attributes(new_sdk)).project, "personal/alpha")
        self.assertEqual(snapshot.resolve(self.attributes(self.alpha[1])).project, "unattributed")

    def test_mapping_change_during_lookup_is_rejected(self) -> None:
        original = attribution.sdk_session_id
        calls = 0

        def changing(editor: Path, host: str) -> str:
            nonlocal calls
            calls += 1
            return str(uuid.uuid4()) if calls > 2 else original(editor, host)

        with patch.object(attribution, "sdk_session_id", side_effect=changing):
            with self.assertLogs(attribution.LOG, logging.WARNING):
                snapshot = self.resolver.snapshot()
        self.assertEqual(snapshot.resolve(self.attributes(self.alpha[0])).reason, "lookup_failed")

    def test_trace_structure_and_span_limit_are_enforced_before_lookup(self) -> None:
        payload = self.payload([])
        spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
        spans[:] = [{}] * attribution.MAX_SPANS
        self.assertEqual(len(attribution.spans_in(payload)), attribution.MAX_SPANS)
        spans.append({})
        with patch.object(self.resolver, "snapshot") as snapshot:
            with self.assertRaisesRegex(ValueError, "Span limit"):
                attribution.enrich(payload, self.resolver)
            snapshot.assert_not_called()
        for invalid in (
            {"resourceSpans": [{"scopeSpans": None}]},
            {"resourceSpans": [{"scopeSpans": [{"spans": [{"attributes": [{}]}]}]}]},
            {"resourceSpans": [{"scopeSpans": [{"spans": None}]}]},
        ):
            with self.subTest(payload=invalid):
                with self.assertRaises(ValueError):
                    attribution.spans_in(invalid)

    def test_enrichment_overwrites_spoofed_labels_without_changing_span_identity_or_count(self) -> None:
        payload = self.payload(self.attributes(self.alpha[1]) + [
            {"key": "project.id", "value": {"stringValue": "spoofed/project"}},
            {"key": "attribution.reason", "value": {"stringValue": "spoofed reason"}},
        ])
        spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
        spans.extend([
            {**copy.deepcopy(spans[0]), "spanId": "c" * 16, "attributes": self.attributes(self.beta[0])},
            {**copy.deepcopy(spans[0]), "spanId": "d" * 16, "attributes": []},
        ])
        before = [(span["traceId"], span["spanId"]) for span in spans]
        for _ in range(2):
            self.assertEqual(attribution.enrich(payload, self.resolver),
                             {"resolved": 2, "missing_identity": 1})
            self.assertEqual([(span["traceId"], span["spanId"]) for span in spans], before)
            self.assertEqual([
                next(item["value"]["stringValue"] for item in span["attributes"]
                     if item["key"] == "project.id") for span in spans
            ], ["personal/alpha", "work/beta", "unattributed"])
            self.assertNotIn("spoofed", json.dumps(payload))

    def start_server(self, server: HTTPServer) -> HTTPServer:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def cleanup() -> None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.addCleanup(cleanup)
        return server

    def gateway(self, status: int = 200, body: bytes = b'{"partialSuccess":{"rejectedSpans":"1"}}'
                ) -> tuple[str, list[bytes], HTTPServer]:
        received = []

        class Backend(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if self.path not in ("/v1/traces", "/v1/metrics", "/v1/logs"):
                    self.send_response(404)
                    self.end_headers()
                    return
                received.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(status)
                if 300 <= status < 400:
                    self.send_header("Location", "https://example.invalid/MUST_NOT_FOLLOW")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                self.send_response(404)
                self.end_headers()

            def log_message(self, *_args) -> None:
                pass

        backend = self.start_server(HTTPServer(("127.0.0.1", 0), Backend))
        gateway = self.start_server(attribution.Gateway(
            self.policy, 0, backend.server_address[1],
        ))
        return f"http://127.0.0.1:{gateway.server_address[1]}", received, backend

    def test_http_forwarding_preserves_partial_success_and_checks_health(self) -> None:
        base, received, _ = self.gateway()
        with self.opener.open(base + "/health", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.load(response), {
                "status": "ok", "collector": "reachable", "cloud_delivery": "not_checked",
            })
        request = urllib.request.Request(
            base + "/v1/traces", data=json.dumps(self.payload(self.attributes(self.alpha[1]))).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=5) as response:
            self.assertEqual(json.load(response), {"partialSuccess": {"rejectedSpans": "1"}})
        self.assertEqual(len(received), 1)
        self.assertIn(b"personal/alpha", received[0])

    def test_source_routes_overwrite_labels_even_for_unresolved_spans_and_rewrite_path(self) -> None:
        self.policy = self.discovery_policy()
        desktop = self.add_desktop(str(self.policy.projects_root / "personal/desktop"))
        base, received, _ = self.gateway()
        for source, identity, reason in (
            ("desktop", desktop, "resolved"),
            ("desktop", str(uuid.uuid4()), "unknown_session"),
            ("vscode", str(uuid.uuid4()), "unknown_session"),
            ("unknown", desktop, "resolved"),
            ("desktop", None, "missing_identity"),
        ):
            payload = self.payload(self.attributes(identity) + [
                {"key": "telemetry.source", "value": {"stringValue": "spoofed"}},
            ])
            route = "" if source == "unknown" else f"/{source}"
            request = urllib.request.Request(
                base + route + "/v1/traces", data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with self.opener.open(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
            attributes = json.loads(received[-1])["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
            values = {item["key"]: item["value"]["stringValue"] for item in attributes}
            self.assertEqual(values["telemetry.source"], source)
            self.assertEqual(values["attribution.reason"], reason)
            self.assertNotIn("spoofed", json.dumps(attributes))
        for signal in ("metrics", "logs"):
            request = urllib.request.Request(
                base + f"/desktop/v1/{signal}", data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            with self.opener.open(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
        self.desktop.unlink()
        request = urllib.request.Request(
            base + "/desktop/v1/traces",
            data=json.dumps(self.payload(self.attributes(desktop))).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.assertLogs(attribution.LOG, logging.WARNING):
            with self.opener.open(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
        attributes = json.loads(received[-1])["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
        values = {item["key"]: item["value"]["stringValue"] for item in attributes}
        self.assertEqual(values["telemetry.source"], "desktop")
        self.assertEqual(values["attribution.reason"], "lookup_failed")
        request = urllib.request.Request(base + "/other/v1/traces", data=b"{}",
                                         headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.opener.open(request, timeout=5)
        self.assertEqual(error.exception.code, 404)
        error.exception.close()

    def test_http_invalid_inputs_and_unsupported_compression_are_not_forwarded(self) -> None:
        base, received, _ = self.gateway()
        for body, headers, expected in (
            (b"[]", {"Content-Type": "application/json"}, 400),
            (b'{"resourceSpans":{}}', {"Content-Type": "application/json"}, 400),
            (b"not-json", {"Content-Type": "application/json"}, 400),
            (b"{}", {"Content-Type": "application/x-protobuf"}, 415),
            (b"{}", {"Content-Type": "application/json", "Content-Encoding": "gzip"}, 415),
        ):
            with self.subTest(expected=expected, headers=headers):
                request = urllib.request.Request(base + "/v1/traces", data=body, headers=headers)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.opener.open(request, timeout=5)
                self.assertEqual(error.exception.code, expected)
                error.exception.close()
        connection = http.client.HTTPConnection(base.removeprefix("http://"), timeout=5)
        try:
            connection.putrequest("POST", "/v1/traces")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(attribution.MAX_BODY + 1))
            connection.endheaders()
            with connection.getresponse() as response:
                self.assertEqual(response.status, 413)
        finally:
            connection.close()
        self.assertEqual(received, [])

    def test_enrichment_expansion_cannot_exceed_collector_body_limit(self) -> None:
        base, received, _ = self.gateway()
        body = json.dumps(self.payload(self.attributes(self.alpha[0]))).encode()
        request = urllib.request.Request(
            base + "/v1/traces", data=body, headers={"Content-Type": "application/json"},
        )
        with patch.object(attribution, "MAX_BODY", len(body)):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.opener.open(request, timeout=5)
        self.assertEqual(error.exception.code, 413)
        error.exception.close()
        self.assertEqual(received, [])

    def test_downstream_rejections_and_redirects_do_not_leak_response_bodies(self) -> None:
        for status, expected in ((400, 400), (429, 429), (503, 503), (302, 502)):
            with self.subTest(status=status):
                base, received, _ = self.gateway(status, b"PRIVATE_DOWNSTREAM_RESPONSE")
                request = urllib.request.Request(
                    base + "/v1/traces", data=json.dumps(self.payload([])).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with self.assertLogs(attribution.LOG, logging.ERROR) as logs:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        self.opener.open(request, timeout=5)
                self.assertEqual(error.exception.code, expected)
                self.assertNotIn(b"PRIVATE", error.exception.read())
                error.exception.close()
                self.assertEqual(len(received), 1)
                self.assertNotIn("PRIVATE", "".join(logs.output))

    def test_invalid_collector_acknowledgement_does_not_report_success(self) -> None:
        for body in (b"not-json", b'{"error":"PRIVATE"}', b'{"partialSuccess":false}'):
            with self.subTest(body=body):
                base, _, _ = self.gateway(body=body)
                request = urllib.request.Request(
                    base + "/v1/traces", data=json.dumps(self.payload([])).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with self.assertLogs(attribution.LOG, logging.ERROR):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        self.opener.open(request, timeout=5)
                self.assertEqual(error.exception.code, 503)
                error.exception.close()

    def test_downstream_failure_is_not_acknowledged_as_success(self) -> None:
        base, received, backend = self.gateway()
        backend.shutdown()
        backend.server_close()
        with self.assertRaises(urllib.error.HTTPError) as health_error:
            self.opener.open(base + "/health", timeout=5)
        self.assertEqual(health_error.exception.code, 503)
        self.assertEqual(json.load(health_error.exception), {"error": "collector_unavailable"})
        health_error.exception.close()
        request = urllib.request.Request(
            base + "/v1/traces", data=json.dumps(self.payload([])).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.assertLogs(attribution.LOG, logging.ERROR):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.opener.open(request, timeout=5)
        self.assertEqual(error.exception.code, 503)
        error.exception.close()
        self.assertEqual(received, [])

    def test_gateway_to_pinned_collector_filters_content_and_preserves_exact_attribution(self) -> None:
        self.check_gateway_to_pinned_collector()

    def test_discovery_gateway_to_pinned_collector_accepts_new_aliases_without_reinstall(self) -> None:
        self.policy = self.discovery_policy()
        for alias in self.projects:
            self.projects[alias] = str(self.policy.projects_root / alias)
        self.alpha = self.add_session("personal/alpha")
        self.beta = self.add_session("work/beta")
        self.check_gateway_to_pinned_collector(discover_projects=True)

    def check_gateway_to_pinned_collector(self, discover_projects: bool = False) -> None:
        binary = collector.HOME / collector.BINARY_PATH
        if not binary.exists():
            self.skipTest("Pinned collector not installed; no download performed")
        ports = []
        for _ in range(2):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                ports.append(listener.getsockname()[1])
        endpoint = ("https://synthetic.southindia-1.ingest.monitor.azure.com/"
                    "datacollectionRules/dcr-test/streams/Microsoft-OTLP-Traces/otlp/v1/traces")
        config = collector.configuration(
            endpoint, self.root, () if discover_projects else tuple(self.projects),
            discover_projects=discover_projects,
        )
        for name, port in zip(("otlp", "otlp/attributed"), ports):
            config["receivers"][name]["protocols"]["http"]["endpoint"] = f"127.0.0.1:{port}"
        config["extensions"] = {}
        config["service"]["extensions"] = []
        config["service"]["telemetry"]["metrics"]["level"] = "none"
        config["exporters"] = {"file": {"path": str(self.root / "output.json")}}
        for pipeline in config["service"]["pipelines"].values():
            pipeline["exporters"] = ["file"]
        path = self.root / "collector.json"
        path.write_text(json.dumps(config))
        process = subprocess.Popen(
            [str(binary), "--config", str(path)], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 10
            for port in ports:
                while True:
                    if process.poll() is not None:
                        self.fail(f"Collector failed startup: {process.communicate()[1]}")
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            break
                    except OSError:
                        if time.monotonic() >= deadline:
                            self.fail("Collector test receiver did not start")
                        time.sleep(0.1)
            gateway = self.start_server(attribution.Gateway(self.policy, 0, ports[1]))
            payload = self.payload([])
            spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
            template = copy.deepcopy(spans[0])
            identities = [
                self.attributes(self.alpha[1]), self.attributes(self.beta[0]), [],
                self.attributes(self.alpha[1], self.beta[0]),
            ]
            spans[:] = [
                {**copy.deepcopy(template), "spanId": f"{index + 1:016x}", "attributes": attributes + [
                    {"key": "gen_ai.input.messages", "value": {"stringValue": "PROMPT_MUST_DISAPPEAR"}},
                    {"key": "enduser.pseudo.id", "value": {"stringValue": "PSEUDO_MUST_DISAPPEAR"}},
                    {"key": "process.user.name", "value": {"stringValue": "USER_MUST_DISAPPEAR"}},
                    {"key": "telemetry.source", "value": {"stringValue": "SOURCE_MUST_DISAPPEAR"}},
                    {"key": "project.id", "value": {"stringValue": "spoofed/project"}},
                ]} for index, attributes in enumerate(identities)
            ]
            payload["resourceSpans"][0]["resource"] = {"attributes": [{
                "key": "workspace.path", "value": {"stringValue": "PATH_MUST_DISAPPEAR"},
            }]}
            for export_index in range(2):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{gateway.server_address[1]}/vscode/v1/traces",
                    data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"},
                )
                with self.opener.open(request, timeout=10) as response:
                    self.assertEqual(response.status, 200)
                if discover_projects and export_index == 0:
                    for project in (
                        {"stringValue": "/Users/private/workspace"},
                        {"stringValue": "personal/name with spaces"},
                        {"stringValue": "synthetic/demo"},
                        {"intValue": "42"},
                    ):
                        invalid = self.payload([
                            {"key": "project.id", "value": project},
                            {"key": "attribution.status", "value": {"stringValue": "attributed"}},
                            {"key": "attribution.reason", "value": {"stringValue": "resolved"}},
                            {"key": "telemetry.source", "value": {"stringValue": "SOURCE_MUST_DISAPPEAR"}},
                        ])
                        request = urllib.request.Request(
                            f"http://127.0.0.1:{ports[1]}/v1/traces",
                            data=json.dumps(invalid).encode(),
                            headers={"Content-Type": "application/json"},
                        )
                        with self.opener.open(request, timeout=10) as response:
                            self.assertEqual(response.status, 200)
            if discover_projects:
                desktop = self.add_desktop(self.projects["personal/alpha"])
                request = urllib.request.Request(
                    f"http://127.0.0.1:{gateway.server_address[1]}/desktop/v1/traces",
                    data=json.dumps(self.payload(self.attributes(desktop))).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with self.opener.open(request, timeout=10) as response:
                    self.assertEqual(response.status, 200)
            direct = self.payload(self.attributes(self.alpha[0]) + [
                {"key": "project.id", "value": {"stringValue": "personal/alpha"}},
                {"key": "attribution.status", "value": {"stringValue": "attributed"}},
                {"key": "attribution.reason", "value": {"stringValue": "resolved"}},
            ])
            request = urllib.request.Request(
                f"http://127.0.0.1:{ports[0]}/v1/traces", data=json.dumps(direct).encode(),
                headers={"Content-Type": "application/json"},
            )
            with self.opener.open(request, timeout=10) as response:
                self.assertEqual(response.status, 200)
        finally:
            process.terminate()
            _, errors = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, errors)
        records = [json.loads(line) for line in (self.root / "output.json").read_text().splitlines()]
        self.assertNotIn("MUST_DISAPPEAR", json.dumps(records))
        self.assertNotIn("vscode.agent_host.session.uri", json.dumps(records))
        self.assertNotIn(str(self.root), json.dumps(records))
        exported = [span for record in records for resource in record["resourceSpans"]
                    for scope in resource["scopeSpans"] for span in scope["spans"]]
        self.assertNotIn("/Users/private", json.dumps(records))
        self.assertNotIn("name with spaces", json.dumps(records))
        self.assertEqual(len(exported), 14 if discover_projects else 9)
        projects = []
        reasons = []
        sources = []
        for span in exported:
            attributes = {item["key"]: item["value"] for item in span["attributes"]}
            projects.append(attributes["project.id"]["stringValue"])
            sources.append(attributes["telemetry.source"]["stringValue"])
            reasons.append(attributes.get("attribution.reason", {}).get("stringValue"))
            self.assertEqual(attributes["accounting.verified"], {"boolValue": False})
            self.assertEqual(span["traceId"], "a" * 32)
        self.assertEqual(projects.count("personal/alpha"), 3 if discover_projects else 2)
        self.assertEqual(sources.count("vscode"), 8)
        self.assertEqual(sources.count("desktop"), 1 if discover_projects else 0)
        self.assertEqual(sources.count("unknown"), 5 if discover_projects else 1)
        self.assertEqual(projects.count("work/beta"), 2)
        self.assertEqual(projects.count("unattributed"), 9 if discover_projects else 5)
        self.assertEqual(reasons.count("missing_identity"), 2)
        self.assertEqual(reasons.count("identity_conflict"), 2)
        self.assertEqual(reasons.count(None), 1)
        if discover_projects:
            self.assertEqual(reasons.count("invalid_gateway_metadata"), 4)


if __name__ == "__main__":
    unittest.main()
