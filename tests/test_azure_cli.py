import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import azure_cli as helpers


class AzureCliTests(unittest.TestCase):
    def test_request_body_is_not_exposed_in_process_arguments(self) -> None:
        body = {"input": "PRIVATE REPORT MARKER"}
        response = subprocess.CompletedProcess(args=[], returncode=0, stdout='{"ok": true}')
        with patch.object(helpers.subprocess, "run", return_value=response) as send:
            self.assertEqual(helpers.rest("https://example.test", "audience", body), {"ok": True})
        send.assert_called_once()
        self.assertNotIn(body["input"], " ".join(send.call_args.args[0]))
        self.assertEqual(json.loads(send.call_args.kwargs["input"]), body)

    def test_config_rejects_non_origin_and_foreign_host(self) -> None:
        config = {
            "COACHING_AZURE_AUTH": "azure_cli",
            "AZURE_OPENAI_DEPLOYMENT": "test",
            "COACHING_MODEL_ID": "test-model",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            for endpoint in ("https://example.com", "http://example.openai.azure.com",
                             "https://example.openai.azure.com/openai",
                             "https://example.openai.azure.com?token=bad",
                             "https://user@example.openai.azure.com"):
                with self.subTest(endpoint=endpoint):
                    values = {**config, "AZURE_OPENAI_ENDPOINT": endpoint}
                    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()))
                    with self.assertRaises(ValueError):
                        helpers.load_config(path)

    def test_incomplete_or_refused_response_fails(self) -> None:
        for response in (
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
            {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "refusal", "refusal": "no"}]}]},
        ):
            with self.subTest(response=response), self.assertRaises(ValueError):
                helpers.response_text(response)


if __name__ == "__main__":
    unittest.main()
