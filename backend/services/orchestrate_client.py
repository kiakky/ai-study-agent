"""Shared verified LIVE Orchestrate transport; no database or executable tools.

Official schemas: https://developer.watson-orchestrate.ibm.com/apis/server_openapi.json
Authentication: /environment/production_import and /environment/initiate_environment
on the same IBM documentation site. This POC supports the Toronto IBM Cloud
instance only, not MCSP/AWS or CPD credentials. See docs/orchestrate-poc.md.
"""
from dataclasses import dataclass, field
import io
import json
import os
from pathlib import Path
import re
import time
import threading
from urllib.parse import urlsplit
from uuid import UUID

from dotenv import dotenv_values
import httpx

ROOT = Path(__file__).resolve().parents[2]
IAM_URL = "https://iam.cloud.ibm.com/identity/token"
HOST = "api.ca-tor.watson-orchestrate.cloud.ibm.com"
PROMPT = "Hello, introduce yourself in one sentence."
NAMES = ("WXO_API_URL", "WXO_API_KEY", "WXO_AGENT_ID")


class POCError(Exception):
    def __init__(self, code, stage, status=None):
        self.code, self.stage, self.status = code, stage, status
        super().__init__(f"{stage}: {code}" + (f" (HTTP {status})" if status else ""))


@dataclass(frozen=True)
class Configuration:
    url: str = field(repr=False)
    api_key: str = field(repr=False)
    agent_id: str = field(repr=False)

    def __post_init__(self):
        try:
            u = urlsplit(self.url)
            if (u.scheme != "https" or u.hostname != HOST or u.port is not None
                    or u.username or u.password or u.query or u.fragment
                    or not re.fullmatch(r"/instances/[a-zA-Z0-9-]+/?", u.path)
                    or not self.api_key or self.api_key != self.api_key.strip()
                    or any(ord(c) < 32 for c in self.api_key)):
                raise ValueError()
            if str(UUID(self.agent_id)) != self.agent_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise POCError("configuration", "configuration") from None
        object.__setattr__(self, "url", self.url.rstrip("/"))

    @classmethod
    def load(cls, path=ROOT / ".env", environ=None):
        env = os.environ if environ is None else environ
        selected = []
        # Parse only the three authorized settings; never load unrelated secrets
        # or interpolate references to other environment variables.
        if not all(env.get(n) for n in NAMES):
            try:
                with path.open(encoding="utf-8-sig") as source:
                    for line in source:
                        name = line.split("=", 1)[0].strip().removeprefix("export ").strip()
                        if name in NAMES:
                            selected.append(line)
            except OSError:
                raise POCError("configuration", "configuration") from None
        values = dotenv_values(stream=io.StringIO("".join(selected)), interpolate=False)
        return cls(*(env.get(n) or values.get(n) or "" for n in NAMES))


def check_status(response, stage):
    status = response.status_code
    if status == 200:
        return
    # Inspect error text only in memory for classification; never display bodies.
    body = response.text[:32000].lower()
    if any(word in body for word in ("quota_exceeded", "quota exhausted", "token_quota", "quota limit", "quota exceeded")):
        code = "quota_exceeded"
    elif status == 401:
        code = "authentication"
    elif status == 403:
        code = "access_denied"
    elif status == 429:
        code = "rate_limited"
    elif status in (400, 404, 405, 422):
        code = "agent_or_api_unavailable" if status == 404 else "api_version_or_request"
    elif 300 <= status < 400:
        code = "redirect_rejected"
    else:
        code = "provider_unavailable"
    raise POCError(code, stage, status)


def text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(item.get("text", "") for item in content
                       if isinstance(item, dict) and item.get("response_type") == "text"
                       and isinstance(item.get("text"), str))
    return ""


def parse_events(lines, deadline):
    """Read final assistant content only; no thinking, tool traces or raw events."""
    chunks, deltas, complete, seen, size = [], [], False, [], 0
    shapes = []
    for line in lines:
        if time.monotonic() > deadline:
            raise POCError("timeout_uncertain_do_not_resend", "inference")
        size += len(line)
        if size > 1_000_000:
            raise POCError("response_too_large", "inference")
        if not line.strip() or line.startswith(("event:", "id:", ":")):
            continue
        value = line.removeprefix("data:").strip()
        if value == "[DONE]":
            continue
        try:
            event = json.loads(value)
            name, data = event["event"], event.get("data", {})
            if not isinstance(data, dict):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise POCError("api_version_or_response", "inference") from None
        if name not in seen:
            seen.append(name)
        if name == "message.completed":
            shapes.append({"data_fields": sorted(data), "content_type": type(data.get("content")).__name__})
        if name in ("run.failed", "run.expired", "run.cancelled", "error", "message.interrupt"):
            detail = json.dumps(data).lower()
            code = 'run_failed_or_interrupted'
            if any(word in detail for word in ('quota_exceeded', 'quota exceeded', 'token_quota', 'quota exhausted')):
                code = 'quota_exceeded'
            elif any(word in detail for word in ('rate_limit', 'rate limit', 'too many requests')):
                code = 'rate_limited'
            elif any(word in detail for word in ('model_not_found', 'model unavailable', 'unsupported_model')):
                code = 'agent_or_api_unavailable'
            raise POCError(code, "inference")
        message = data.get("message", data)
        if name in ("message.created", "message.completed") and isinstance(message, dict) and message.get("role") == "assistant":
            text = text_content(message.get("content"))
            if text:
                # Snapshot events can repeat the same final assistant message.
                chunks = [text]
        if name == "message.delta":
            delta = data.get("delta", data)
            if isinstance(delta, dict) and delta.get("role", "assistant") == "assistant":
                text = text_content(delta.get("content"))
                if text:
                    deltas.append(text)
        if name == "run.completed":
            complete = True
    reply = ("\n".join(chunks) if chunks else "".join(deltas)).strip()
    if not complete or not reply or len(reply) > 8000:
        # Event names only are safe diagnostics for schema differences.
        error = POCError("incomplete_or_changed_response", "inference")
        error.diagnostics = {"events": seen, "completed_message_shapes": shapes}
        raise error
    return reply


class OrchestrateClient:
    def __init__(self, config, client):
        self.config, self.client = config, client
        self.token = None
        self.calls = 0
        self._expiry = 0
        self._auth_lock = threading.Lock()
        self._deadline = threading.local()

    def request(self, method, url, stage, **kwargs):
        # Deliberately zero retries: especially never replay an uncertain run POST.
        self.calls += 1
        try:
            remaining = getattr(self._deadline, 'until', time.monotonic() + 90) - time.monotonic()
            if remaining <= 0:
                raise POCError('timeout', stage)
            response = self.client.request(method, url, timeout=httpx.Timeout(min(15, remaining), connect=min(5, remaining)), **kwargs)
            if response.status_code == 401:
                self.token = None
                self._expiry = 0
            check_status(response, stage)
            if len(response.content) > 1_000_000:
                raise POCError("response_too_large", stage)
            return response.json()
        except httpx.TimeoutException:
            raise POCError("timeout", stage) from None
        except httpx.TransportError:
            raise POCError("network_or_tls", stage) from None
        except ValueError:
            raise POCError("api_version_or_response", stage) from None

    def authenticate(self):
        with self._auth_lock:
            if self.token and self._expiry > time.time() + 60:
                return self.token
            value = self.request("POST", IAM_URL, "authentication", data={
                "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                "apikey": self.config.api_key,
            }, headers={"Accept": "application/json"})
            try:
                token = value["access_token"]
                lifetime = float(value.get("expires_in", 120))
                if not isinstance(token, str) or not token or any(ord(c) < 32 for c in token) or lifetime <= 60:
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise POCError("invalid_token_response", "authentication") from None
            self.token = token
            self._expiry = time.time() + min(lifetime, 3600)
            return token

    def verify(self, prompt=PROMPT):
        self._deadline.until = time.monotonic() + 90
        try:
            return self._verify(prompt)
        finally:
            del self._deadline.until

    def _verify(self, prompt):
        token = self.authenticate()
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        base = self.config.url + "/v1/orchestrate"
        agent = base + "/agents/" + self.config.agent_id
        environments = self.request("GET", agent + "/environment", "live_environment", headers=headers)
        if not isinstance(environments, list):
            raise POCError("api_version_or_response", "live_environment")
        live = [e for e in environments if isinstance(e, dict) and e.get("name", "").lower() == "live"]
        if len(live) != 1 or type(live[0].get("current_version")) is not int:
            raise POCError("live_deployment_unavailable", "live_environment")
        environment = live[0]
        try:
            UUID(environment["id"])
        except (KeyError, ValueError, TypeError):
            raise POCError("api_version_or_response", "live_environment") from None
        releases = self.request("GET", agent + "/releases", "live_release", headers=headers)
        if not isinstance(releases, list):
            raise POCError("api_version_or_response", "live_release")
        matches = [r for r in releases if isinstance(r, dict) and r.get("version_label") == environment["current_version"]]
        if len(matches) != 1:
            raise POCError("live_release_unavailable", "live_release")
        release = matches[0]
        # Fail closed: a hello must not run any external tools or collaborators.
        plugins = release.get("plugins") or {}
        if (not isinstance(plugins, dict)
                or set(plugins) - {"agent_pre_invoke", "agent_post_invoke"}
                or any(plugins.values())
                or any(release.get(n) for n in ("tools", "collaborators", "toolkits", "knowledge_base", "custom_join_tool"))
                or release.get("memory_enabled")):
            raise POCError("agent_has_tools_or_memory", "live_release")
        if release.get("semantic_version") != "1.0.0":
            raise POCError("unexpected_live_version", "live_release")
        if release.get('llm') != 'groq/openai/gpt-oss-120b':
            raise POCError('agent_or_api_unavailable', 'live_release')
        metadata = {"environment": "live", "version": "1.0.0", "model": release.get("llm")}
        body = {"agent_id": self.config.agent_id, "environment_id": environment["id"],
                "version": environment["current_version"],
                "message": {"role": "user", "content": prompt}}
        self.calls += 1
        deadline = min(time.monotonic() + 60, self._deadline.until)
        try:
            with self.client.stream("POST", base + "/runs/stream", headers=headers,
                                    params={"stream_timeout": 45000}, json=body,
                                    timeout=httpx.Timeout(min(50, max(0.1, deadline-time.monotonic())), connect=5)) as response:
                if response.status_code != 200:
                    response.read()
                    if response.status_code == 401:
                        self.token = None
                        self._expiry = 0
                    check_status(response, "inference")
                reply = parse_events(response.iter_lines(), deadline)
        except httpx.TimeoutException:
            raise POCError("timeout_uncertain_do_not_resend", "inference") from None
        except httpx.TransportError:
            raise POCError("network_uncertain_do_not_resend", "inference") from None
        # Defense against credential echo. Never display a raw provider payload.
        for secret in (self.config.api_key, token, self.config.agent_id, self.config.url):
            reply = reply.replace(secret, "[redacted]")
        reply = "".join(c for c in reply if c in "\n\t" or ord(c) >= 32)
        return metadata, reply
