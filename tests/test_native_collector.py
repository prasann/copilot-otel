import json
import plistlib
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from tools import native_collector as collector


ENDPOINT = ("https://synthetic.southindia-1.ingest.monitor.azure.com/"
            "datacollectionRules/dcr-test/streams/Microsoft-OTLP-Traces/otlp/v1/traces")


class NativeCollectorTests(unittest.TestCase):
    def test_existing_install_migrates_to_friendly_name_without_redownload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "bin" / f"otelcol-contrib-{collector.VERSION}"
            legacy.parent.mkdir()
            legacy.write_bytes(b"existing-pinned-collector")
            legacy.chmod(0o700)
            plist = root / "collector.plist"
            launch = {"Label": collector.LABEL,
                      "ProgramArguments": [str(legacy), "--config", str(root / "collector.json")]}
            plist.write_bytes(plistlib.dumps(launch))
            with patch.object(collector, "HOME", root), patch.object(collector, "PLIST", plist), \
                    patch.object(collector.platform, "machine", return_value="arm64"), \
                    patch.object(collector, "run", return_value=f"otelcol-contrib {collector.VERSION}"), \
                    patch.object(collector.urllib.request, "urlopen") as download:
                collector.check_owned_plist()
                binary = collector.download(root)
                self.assertEqual(binary.name, "Copilot Telemetry Collector")
                self.assertEqual(binary.read_bytes(), b"existing-pinned-collector")
                self.assertEqual(binary.stat().st_mode & 0o777, 0o700)
                self.assertFalse(legacy.exists())
                launch["ProgramArguments"][0] = str(binary)
                plist.write_bytes(plistlib.dumps(launch))
                collector.check_owned_plist()
                self.assertEqual(collector.download(root), binary)
                download.assert_not_called()

    def test_unrecognized_launchagent_is_rejected_before_install_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plist = Path(directory) / "existing.plist"
            original = plistlib.dumps({"Label": collector.LABEL, "ProgramArguments": ["/unrelated"]})
            plist.write_bytes(original)
            with patch.object(collector, "PLIST", plist), \
                    patch.object(collector.platform, "system", return_value="Darwin"), \
                    patch.object(collector, "download") as download, \
                    patch.object(collector, "service_loaded") as loaded:
                with self.assertRaisesRegex(ValueError, "unrecognized LaunchAgent"):
                    collector.install()
                download.assert_not_called()
                loaded.assert_not_called()
                self.assertEqual(plist.read_bytes(), original)

    def test_failed_readiness_unloads_service_instead_of_leaving_restart_loop(self) -> None:
        with patch.object(collector, "check_owned_plist"), \
                patch.object(collector, "PLIST") as plist, \
                patch.object(collector, "service_loaded", return_value=False), \
                patch.object(collector, "run") as run, \
                patch.object(collector, "stop") as stop, \
                patch.object(collector.time, "sleep"), \
                patch.object(collector.urllib.request, "urlopen",
                             side_effect=urllib.error.URLError("not listening")):
            plist.exists.return_value = True
            with self.assertRaisesRegex(RuntimeError, "failed readiness and was stopped"):
                collector.start()
            stop.assert_called_once()
            self.assertEqual(run.call_args.args[0][:2], ["launchctl", "bootstrap"])

    def test_stop_waits_for_launchd_to_finish_unloading(self) -> None:
        with patch.object(collector, "check_owned_plist"), \
                patch.object(collector, "PLIST") as plist, \
                patch.object(collector, "service_loaded", side_effect=[True, True, False]), \
                patch.object(collector, "run") as run, \
                patch.object(collector.time, "sleep") as sleep:
            plist.exists.return_value = True
            collector.stop()
            self.assertEqual(run.call_args.args[0][:2], ["launchctl", "bootout"])
            sleep.assert_called_once_with(0.25)

    def test_launchd_permission_error_is_not_treated_as_missing_service(self) -> None:
        result = subprocess.CompletedProcess(["launchctl"], 1, "", "permission denied")
        with patch.object(collector.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                collector.service_loaded()

    def test_query_back_requires_exact_metadata_and_no_content(self) -> None:
        metadata = {
            "telemetry.origin": "synthetic_collector_smoke", "project.id": "synthetic/demo",
            "attribution.status": "synthetic", "accounting.verified": False,
        }
        spans = ["a" * 16, "b" * 16]
        rows = [{
            "SpanId": span_id, "Attributes": dict(metadata), "Type": "OTelSpans",
            "Name": "copilot.invocation", "ScopeName": "copilot-otel.metadata",
            "ScopeVersion": "1", "ServiceName": "copilot-otel",
        } for span_id in spans]
        rows[0]["Attributes"].update({
            "gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 20,
            "gen_ai.usage.cache_read.input_tokens": 10,
            "gen_ai.response.model": "synthetic-not-a-model-call",
        })
        columns = list(rows[0])

        def response(*_):
            return {"tables": [{"columns": [{"name": name} for name in columns],
                                "rows": [[row[name] for name in columns] for row in rows]}]}

        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            receipt.write_text(json.dumps({"trace_id": "0" * 32, "span_ids": spans}))
            with patch.object(collector, "azd_value", return_value="0" * 32), \
                    patch.object(collector, "rest", side_effect=response):
                self.assertEqual(collector.query_smoke(receipt)["status"], "query_verified")
                rows[1]["Attributes"]["unexpected"] = "MUST_DISAPPEAR"
                with self.assertRaisesRegex(ValueError, "canary reached Azure"):
                    collector.query_smoke(receipt)
                rows[1]["Attributes"]["unexpected"] = "not-allowed"
                with self.assertRaisesRegex(ValueError, "exact allowlist"):
                    collector.query_smoke(receipt)

    def test_pinned_binary_removes_content_and_invalid_metadata_values(self) -> None:
        binary = collector.HOME / collector.BINARY_PATH
        if not binary.exists():
            self.skipTest("Pinned native collector is not installed; no download performed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            config = collector.configuration(ENDPOINT, root)
            config["receivers"]["otlp"]["protocols"]["http"]["endpoint"] = f"127.0.0.1:{port}"
            config["extensions"] = {}
            config["service"]["extensions"] = []
            config["service"]["telemetry"]["metrics"]["level"] = "none"
            config["exporters"] = {"file": {"path": str(root / "output.json")}}
            config["service"]["pipelines"]["traces"]["exporters"] = ["file"]
            config_path = root / "collector.json"
            config_path.write_text(json.dumps(config))
            opener = urllib.request.urlopen

            def redirect(request: urllib.request.Request, timeout: int):
                return opener(urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/traces", data=request.data,
                    headers={"Content-Type": "application/json"}, method="POST",
                ), timeout=timeout)

            process = subprocess.Popen(
                [str(binary), "--config", str(config_path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                deadline = time.monotonic() + 10
                while True:
                    if process.poll() is not None:
                        self.fail(f"Collector exited during startup: {process.communicate()[1]}")
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            break
                    except OSError:
                        if time.monotonic() >= deadline:
                            self.fail("Collector did not start its synthetic test receiver")
                        time.sleep(0.1)
                with patch.object(collector, "ROOT", root), \
                        patch.object(collector.urllib.request, "urlopen", side_effect=redirect):
                    receipt = collector.smoke()
            finally:
                process.terminate()
                _, errors = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, errors)
            records = [json.loads(line) for line in (root / "output.json").read_text().splitlines()]
            self.assertNotIn("MUST_DISAPPEAR", json.dumps(records))
            spans = [span for record in records for resource in record["resourceSpans"]
                     for scope in resource["scopeSpans"] for span in scope["spans"]]
            self.assertEqual({span["spanId"] for span in spans}, set(receipt["span_ids"]))
            for span in spans:
                attributes = {item["key"]: item["value"] for item in span["attributes"]}
                self.assertEqual(span["name"], "copilot.invocation")
                self.assertFalse(span.get("events"))
                self.assertFalse(span.get("links"))
                if span["spanId"] == receipt["span_ids"][0]:
                    self.assertEqual(attributes["gen_ai.usage.input_tokens"], {"intValue": "100"})
                    self.assertEqual(attributes["gen_ai.usage.output_tokens"], {"intValue": "20"})
                else:
                    self.assertFalse(any(key.startswith("gen_ai.") for key in attributes))


if __name__ == "__main__":
    unittest.main()
