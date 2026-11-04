import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]

def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), ROOT / "scripts" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

REWARD = load_script("53_reward_one_c.py")
RUN = load_script("92_corrected_eidetic.py")
SUMMARY = load_script("93_summarize_corrected_run.py")

class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.log = self.root / "api.jsonl"
        REWARD.API_LOG = self.log
        REWARD.API_REQUEST_LIMIT = 10
        REWARD.API_MAX_RETRIES = 0

    def tearDown(self):
        REWARD.API_LOG = None
        REWARD.API_REQUEST_LIMIT = 0
        REWARD.API_MAX_RETRIES = 0

    def events(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_compiler_check_records_error_and_paths(self):
        identity = {"path": "/usr/bin/gcc", "version": "gcc test"}
        proc = subprocess.CompletedProcess([], 1, "", "file.c:1:2: error: invalid\n")
        with patch.object(RUN.subprocess, "run", return_value=proc) as call:
            result = RUN.compiler_check("gcc", identity, self.root / "file.c", self.root,
                                        self.root / "diags", "case")
        self.assertEqual(call.call_args.args[0][:4], ["/usr/bin/gcc", "-std=c11", "-fsyntax-only", "-w"])
        self.assertEqual(result["exit_code"], 1)
        self.assertTrue(result["reports_error"])
        self.assertFalse(result["diagnostic_parse_failed"])
        self.assertEqual(Path(result["stderr_path"]).read_text(), proc.stderr)
        self.assertEqual(result["version"], "gcc test")

    def test_compiler_nonzero_without_error_and_timeout(self):
        identity = {"path": "/usr/bin/clang", "version": "clang test"}
        with patch.object(RUN.subprocess, "run", return_value=subprocess.CompletedProcess([], 2, "", "driver failed")):
            result = RUN.compiler_check("clang", identity, self.root, self.root, self.root, "nonzero")
        self.assertFalse(result["reports_error"])
        self.assertTrue(result["diagnostic_parse_failed"])
        with patch.object(RUN.subprocess, "run", side_effect=subprocess.TimeoutExpired([], 60)):
            timeout = RUN.compiler_check("clang", identity, self.root, self.root, self.root, "timeout")
        self.assertTrue(timeout["timed_out"])
        self.assertIsNone(timeout["exit_code"])

    def test_api_success_usage_retry_and_no_credentials(self):
        REWARD.API_MAX_RETRIES = 1
        secret = "TEST-SECRET-DO-NOT-LOG"
        response = {"data": [{"embedding": [0.1, 0.2, 0.3]}],
                    "usage": {"prompt_tokens": 7, "total_tokens": 7}}
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": secret}):
            with patch.object(REWARD, "http_post_json", side_effect=[URLError(secret), response]):
                vec = REWARD._embed_dashscope_one("program", "test", 2)
        self.assertEqual(len(vec), 3)
        events = self.events()
        self.assertEqual([e["phase"] for e in events], ["start", "finish", "start", "finish"])
        self.assertEqual([e["status"] for e in events if e["phase"] == "finish"], ["failure", "success"])
        self.assertEqual(events[-1]["usage"]["total_tokens"], 7)
        self.assertEqual(events[-1]["retry_index"], 1)
        self.assertEqual(events[-1]["chunk_index"], 2)
        self.assertNotIn(secret, self.log.read_text())
        self.assertEqual(REWARD._api_stats_since(0)["requests"], 2)

    def test_api_recursive_split_timeout_failure_and_hard_cap(self):
        REWARD.API_REQUEST_LIMIT = 3
        response = {"data": [{"embedding": [1.0, 2.0]}]}
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "secret"}):
            with patch.object(REWARD, "http_post_json", side_effect=[
                    RuntimeError("Range of input length should be [1, 8192]"), response, response]) as send:
                result = REWARD._complete_one("x" * 300, "dashscope", "test", 2, 5)
                self.assertEqual(len(result), 2)
                self.assertEqual(send.call_count, 3)
            with patch.object(REWARD, "http_post_json") as send:
                with self.assertRaisesRegex(RuntimeError, "request limit"):
                    REWARD._embed_dashscope_one("another", "test", 6)
                send.assert_not_called()
        events = self.events()
        self.assertEqual(sum(e["phase"] == "start" for e in events), 3)
        self.assertEqual(sum(e["phase"] == "split" for e in events), 1)
        self.assertEqual([e["split_depth"] for e in events if e["phase"] == "start"], [0, 1, 1])
        self.assertEqual(REWARD._api_request_count(), 3)

    def test_api_timeout_without_retry_stays_sanitized(self):
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "secret"}):
            with patch.object(REWARD, "http_post_json", side_effect=TimeoutError("secret")):
                with self.assertRaises(TimeoutError):
                    REWARD._embed_dashscope_one("program", "test", 0)
        self.assertEqual(self.events()[-1]["status"], "failure")
        self.assertNotIn("secret", self.log.read_text())
        self.assertEqual(RUN.clean_stderr("failure secret"), "failure secret")
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "secret"}):
            self.assertEqual(RUN.clean_stderr("failure secret"), "failure [REDACTED]")

    def test_http_error_body_not_logged_or_raised(self):
        error = HTTPError("https://example.test", 429, "busy", {}, io.BytesIO(b"secret body"))
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "secret"}):
            with patch.object(REWARD.ureq, "urlopen", side_effect=error):
                with self.assertRaises(RuntimeError) as caught:
                    REWARD._embed_dashscope_one("text", "test", 0)
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("secret", self.log.read_text())

    def test_summary_separates_fallback_and_calibration(self):
        run = self.root / "run"
        run.mkdir()
        events = [
            {"status": "accepted", "normalization": "small-archive-cosine-range", "reward01": 0.25},
            {"status": "accepted", "normalization": "loo-full-archive-p10-p90", "reward01": 0},
            {"status": "accepted", "normalization": "loo-full-archive-p10-p90", "reward01": 1},
            {"status": "mutation_failed"},
        ]
        (run / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        stats = SUMMARY.summarize(run)
        self.assertEqual(stats["attempts"], 4)
        self.assertEqual(stats["fallback"]["count"], 1)
        self.assertEqual(stats["calibrated"]["mean"], 0.5)
        self.assertEqual(stats["calibrated"]["zero_fraction"], 0.5)
        self.assertEqual(stats["calibrated"]["one_fraction"], 0.5)

if __name__ == "__main__":
    unittest.main()
