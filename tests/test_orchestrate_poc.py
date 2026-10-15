"""Synthetic responses only; no application/database imports or external calls."""
import json
import io
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from check_orchestrate import Configuration, OrchestratePOC, POCError, parse_events, main, HOST, IAM_URL, PROMPT

AGENT = "00000000-0000-0000-0000-000000000001"
ENVIRONMENT = "00000000-0000-0000-0000-000000000002"
URL = "https://" + HOST + "/instances/synthetic-instance"


def events(content="Synthetic hello.", completed=True):
    result = [{"event": "run.started", "data": {}},
              {"event": "run.step.thinking", "data": {"text": "private trace"}},
              {"event": "message.completed", "data": {"role": "assistant", "content": content}}]
    if completed:
        result.append({"event": "run.completed", "data": {}})
    return "\n\n".join("data: " + json.dumps(e) for e in result) + "\n\n"


class POCTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.status = None
        self.release = {"version_label": 1, "semantic_version": "1.0.0", "llm": "groq/openai/gpt-oss-120b", "tools": [], "collaborators": [], "plugins": {"agent_pre_invoke": None, "agent_post_invoke": None}}
        self.response = events()
        self.client = httpx.Client(transport=httpx.MockTransport(self.respond))
        self.addCleanup(self.client.close)
        self.config = Configuration(URL, "synthetic-key", AGENT)
        self.poc = OrchestratePOC(self.config, self.client)

    def respond(self, request):
        self.requests.append(request)
        if self.status:
            return httpx.Response(self.status, json={"detail": "private-key-not-for-output"})
        if str(request.url) == IAM_URL:
            return httpx.Response(200, json={"access_token": "synthetic-token"})
        if request.url.path.endswith("/environment"):
            return httpx.Response(200, json=[{"name": "live", "id": ENVIRONMENT, "current_version": 1}])
        if request.url.path.endswith("/releases"):
            return httpx.Response(200, json=[self.release])
        return httpx.Response(200, text=self.response, headers={"Content-Type": "text/event-stream"})

    def test_live_selection_auth_and_one_submission(self):
        metadata, reply = self.poc.verify()
        self.assertEqual(reply, "Synthetic hello.")
        self.assertEqual(metadata["environment"], "live")
        self.assertEqual(len(self.requests), 4)
        auth = self.requests[0]
        self.assertIn(b"grant_type=urn%3Aibm%3Aparams%3Aoauth%3Agrant-type%3Aapikey", auth.content)
        self.assertIn(b"apikey=synthetic-key", auth.content)
        for request in self.requests[1:]:
            self.assertEqual(request.headers["Authorization"], "Bearer synthetic-token")
        body = json.loads(self.requests[-1].content)
        self.assertEqual(body["environment_id"], ENVIRONMENT)
        self.assertEqual(body["version"], 1)
        self.assertEqual(body["message"]["content"], PROMPT)
        self.assertNotIn("thread_id", body)
        self.assertNotIn("user_id", body)

    def test_auth_rate_limit_and_api_errors_never_retry_or_disclose(self):
        for status, code in [(401, "authentication"), (403, "access_denied"), (429, "rate_limited"), (404, "agent_or_api_unavailable"), (422, "api_version_or_request"), (503, "provider_unavailable")]:
            self.status = status
            before = len(self.requests)
            with self.assertRaises(POCError) as error:
                self.poc.verify()
            self.assertEqual(error.exception.code, code)
            self.assertEqual(len(self.requests) - before, 1)
            self.assertNotIn("private-key", str(error.exception))

    def test_tools_and_wrong_version_fail_before_inference(self):
        for change in [{"tools": ["calendar"]}, {"collaborators": ["other"]}, {"plugins": {"agent_pre_invoke": ["hook"]}}, {"memory_enabled": True}, {"semantic_version": "2.0.0"}]:
            original = self.release.copy()
            self.release.update(change)
            with self.assertRaises(POCError):
                self.poc.verify()
            self.assertFalse(any(r.url.path.endswith("/runs/stream") for r in self.requests))
            self.release = original

    def test_timeout_is_uncertain_and_not_replayed(self):
        def timeout(request):
            if request.url.path.endswith("/runs/stream"):
                raise httpx.ReadTimeout("synthetic-secret")
            return self.respond(request)
        with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
            poc = OrchestratePOC(self.config, client)
            with self.assertRaises(POCError) as failure:
                poc.verify()
            self.assertEqual(failure.exception.code, "timeout_uncertain_do_not_resend")
            self.assertEqual(poc.calls, 4)

    def test_selective_environment_loading_no_interpolation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests", prefix="quiz-test-") as folder:
            path = Path(folder) / "synthetic.env"
            path.write_text(f"UNRELATED=private\nWXO_API_URL={URL}\nWXO_API_KEY='synthetic-${{UNRELATED}}'\nWXO_AGENT_ID={AGENT}\n", encoding="utf-8")
            config = Configuration.load(path, {})
            self.assertEqual(config.api_key, "synthetic-${UNRELATED}")
            self.assertNotIn("synthetic", repr(config))
            self.assertEqual(Configuration.load(path, {"WXO_API_KEY": "override"}).api_key, "override")

    def test_url_and_agent_validation(self):
        for url in [URL.replace("https:", "http:"), URL + "?query=secret", URL.replace(HOST, "attacker.example"), URL + "/v1", URL.replace(HOST, "user:password@" + HOST)]:
            with self.assertRaises(POCError):
                Configuration(url, "synthetic-key", AGENT)
        with self.assertRaises(POCError):
            Configuration(URL, "key", "../other")

    def test_completed_content_redacts_secrets_and_ignores_traces(self):
        self.response = events("synthetic-key synthetic-token " + AGENT)
        _, reply = self.poc.verify()
        self.assertEqual(reply, "[redacted] [redacted] [redacted]")
        self.assertNotIn("private trace", reply)

    def test_stream_requires_completed_run_and_assistant_message(self):
        for text in [events(completed=False), events(content=[]), 'data: {"event":"run.failed","data":{"secret":"private"}}', "data: invalid-json"]:
            with self.assertRaises(POCError):
                parse_events(text.splitlines(), time.monotonic() + 10)
        self.assertEqual(parse_events(events([{"response_type": "text", "text": "Hello"}]).splitlines(), time.monotonic() + 10), "Hello")

    def test_completion_notification_without_content_uses_deltas(self):
        fixture = [
            {"event": "message.delta", "data": {"delta": {"content": [{"response_type": "text", "text": "Synthetic "}]}}},
            {"event": "message.delta", "data": {"delta": {"content": [{"response_type": "text", "text": "reply."}]}}},
            {"event": "message.completed", "data": {"run_id": "synthetic", "thread_id": "synthetic", "trace_id": "synthetic", "usage": {}}},
            {"event": "run.completed", "data": {}},
            {"event": "done", "data": {}},
        ]
        lines = ["data: " + json.dumps(e) for e in fixture]
        self.assertEqual(parse_events(lines, time.monotonic() + 10), "Synthetic reply.")
        fixture.insert(2, {"event": "message.created", "data": {"message": {"role": "assistant", "content": "Final snapshot."}}})
        lines = ["data: " + json.dumps(e) for e in fixture]
        self.assertEqual(parse_events(lines, time.monotonic() + 10), "Final snapshot.")

    def test_cli_displays_complete_unicode_reply_as_utf8_without_live_calls(self):
        reply = "I\u2019m a synthetic study assistant. \u4f60\u597d!"
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="ascii")
        self.addCleanup(stream.close)
        with patch.object(sys, "stdout", stream), patch.object(sys, "argv", ["check_orchestrate.py", "--verify"]), \
                patch.object(Configuration, "load", return_value=self.config), \
                patch.object(OrchestratePOC, "verify", return_value=({"environment": "live"}, reply)), \
                patch.object(httpx.Client, "request", side_effect=AssertionError("No network in offline tests")):
            self.assertEqual(main(), 0)
            stream.flush()
        output = buffer.getvalue().decode("utf-8", errors="strict")
        self.assertIn("Synthetic response: " + reply, output.splitlines())
        self.assertNotIn("\ufffd", output)


if __name__ == "__main__":
    unittest.main()
