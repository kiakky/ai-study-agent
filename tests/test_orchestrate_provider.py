"""Adapter and Telegram tests: synthetic users/HTTP/temp SQLite, no live services."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import database
from services.conversations import ConversationService, ConversationSettings, load_settings
from services.conversation_telegram import dispatcher, handle_text
from services.llm_contracts import LLMError
from services.llm_providers import create_provider
from services.orchestrate import OrchestrateProvider
from services.orchestrate_client import Configuration, HOST, IAM_URL
from services.brightspace import server_parameters

AGENT = '00000000-0000-0000-0000-000000000001'
ENVIRONMENT = '00000000-0000-0000-0000-000000000002'
CONFIG = Configuration('https://' + HOST + '/instances/synthetic', 'synthetic-key', AGENT)


class Fixture:
    def __init__(self):
        self.requests = []
        self.prompts = []
        self.error = None
        self.status = None
        self.release = {'version_label': 1, 'semantic_version': '1.0.0', 'llm': 'groq/openai/gpt-oss-120b', 'tools': [], 'collaborators': [], 'plugins': {'agent_pre_invoke': None}}

    def __call__(self, request):
        self.requests.append(request)
        if str(request.url) == IAM_URL:
            return httpx.Response(200, json={'access_token': 'synthetic-token', 'expires_in': 3600})
        if request.url.path.endswith('/environment'):
            return httpx.Response(200, json=[{'id': ENVIRONMENT, 'name': 'live', 'current_version': 1}])
        if request.url.path.endswith('/releases'):
            return httpx.Response(200, json=[self.release])
        body = json.loads(request.content)
        self.prompts.append(body)
        if self.error:
            raise self.error
        if self.status:
            return httpx.Response(self.status, json={'code': 'quota_exceeded' if self.status == 403 else 'synthetic-private-error'})
        prefix, transcript = body['message']['content'].split('\n', 1)
        transcript = json.JSONDecoder().raw_decode(transcript)[0]
        questions = [m['content'] for m in transcript['conversation'] if m['role'] == 'user']
        reply = 'Synthetic reply: ' + ' / '.join(questions)
        if transcript.get('tools'):
            reply=json.dumps({'content':'','tool_calls':[{'name':'route_request',
                'arguments':{'intent':'chat','reply':reply}}]})
        events = [
            {'event': 'message.delta', 'data': {'delta': {'role': 'assistant', 'content': [{'response_type': 'text', 'text': reply}]}}},
            {'event': 'message.completed', 'data': {'usage': {}, 'trace_id': 'synthetic'}},
            {'event': 'run.completed', 'data': {}},
            {'event': 'done', 'data': {}},
        ]
        return httpx.Response(200, text='\n\n'.join('data: ' + json.dumps(e) for e in events))


class OrchestrateAdapterTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='quiz-test-', dir=ROOT / 'tests')
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        db = patch.object(database, 'DB_PATH', self.folder / 'synthetic.db')
        db.start(); self.addCleanup(db.stop)
        database.initialize_database(backfill_approvals=False)
        self.fixture = Fixture()
        self.client = httpx.Client(transport=httpx.MockTransport(self.fixture))
        self.addCleanup(self.client.close)
        self.provider = OrchestrateProvider(CONFIG, self.client)
        self.service = ConversationService(self.provider)

    def test_two_users_restart_names_clear_and_fresh_remote_threads(self):
        self.service.respond(101, 1, 'Please call me Nova.', display_name='Original name')
        other = self.service.respond(202, 1, 'My private SQL question.')
        restarted = ConversationService(OrchestrateProvider(CONFIG, self.client))
        answer = restarted.respond(101, 2, 'What name did I choose?')
        self.assertIn('Nova', answer['text'])
        self.assertNotIn('SQL', answer['text'])
        self.assertNotIn('Nova', other['text'])
        for body in self.fixture.prompts:
            self.assertNotIn('thread_id', body)
            self.assertNotIn('context_variables', body)
            self.assertNotIn('tools', body)
            self.assertNotIn('telegram_user_id', json.dumps(body))
            self.assertEqual(body['environment_id'], ENVIRONMENT)
        restarted.clear(101, 10)
        restarted.respond(101, 11, 'Fresh hello')
        self.assertNotIn('Nova', self.fixture.prompts[-1]['message']['content'])
        self.assertEqual(len(restarted.history(202)), 1)

    def test_concurrent_users_share_only_authentication_not_transcripts(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.service.respond, 101, 1, 'Private Python for A')
            second = pool.submit(self.service.respond, 202, 1, 'Private SQL for B')
            a, b = first.result(), second.result()
        self.assertNotIn('SQL', a['text'])
        self.assertNotIn('Python', b['text'])
        self.assertEqual(sum(str(r.url) == IAM_URL for r in self.fixture.requests), 1)

    def test_limited_approved_defaults_and_task_override_do_not_save_preferences(self):
        from services import profiles
        profiles.ensure_profile(101)
        with closing(database.get_connection()) as db, db:
            db.execute('UPDATE users SET preferences_json=? WHERE telegram_user_id=?', (json.dumps({'notes': 'PRIVATE-SAVED-NOTES', 'session_minutes': 45, 'study_days': ['mon']}), 101))
        before = profiles.get_profile(101)['preferences']
        self.service.respond(101, 1, 'For this task use afternoon and 20 minutes.')
        body = self.fixture.prompts[-1]['message']['content']
        self.assertIn('current requests take precedence', body)
        self.assertNotIn('PRIVATE-SAVED-NOTES', body)
        self.assertIn('session_minutes', body)
        self.assertIn('45', body)
        self.assertIn('mon', body)
        self.assertEqual(profiles.get_profile(101)['preferences'], before)
        with closing(database.get_connection()) as db:
            for table in ('calendar_write_actions', 'calendar_event_intents', 'calendar_session_changes', 'conversation_tool_effects'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def test_token_cache_metadata_revalidated_and_duplicate_no_inference(self):
        self.service.respond(101, 1, 'Hello')
        self.service.respond(101, 2, 'Follow up')
        self.service.respond(101, 2, 'Follow up')
        self.assertEqual(len(self.fixture.prompts), 2)
        self.assertEqual(sum(str(r.url) == IAM_URL for r in self.fixture.requests), 1)
        self.assertEqual(sum(r.url.path.endswith('/releases') for r in self.fixture.requests), 2)
        self.fixture.release['tools'] = ['calendar']
        with self.assertRaises(LLMError) as failure:
            self.service.respond(202, 1, 'Hello')
        self.assertEqual(failure.exception.code, 'unsafe_agent_configuration')
        self.assertEqual(len(self.fixture.prompts), 2)

    def test_expired_token_refreshed_once_before_new_run(self):
        self.service.respond(101, 1, 'Hello')
        self.provider.transport._expiry = 0
        self.service.respond(101, 2, 'Another')
        self.assertEqual(sum(str(r.url) == IAM_URL for r in self.fixture.requests), 2)

    def test_quota_and_rate_limit_are_sanitized_without_retries(self):
        for number, status, code in [(1, 403, 'quota_exceeded'), (2, 429, 'rate_limited'), (3, 404, 'unsupported_model'), (4, 401, 'authentication')]:
            self.fixture.status = status
            before = len(self.fixture.prompts)
            with self.assertRaises(LLMError) as failure:
                self.service.respond(101, number, 'Synthetic error test')
            self.assertEqual(failure.exception.code, code)
            self.assertEqual(len(self.fixture.prompts) - before, 1)
            self.assertNotIn('private', str(failure.exception))

    def test_stream_failure_codes_and_unavailable_model_are_explicit(self):
        from services.orchestrate_client import parse_events, POCError
        for code, expected in [('quota_exceeded', 'quota_exceeded'), ('rate_limit', 'rate_limited'), ('model_not_found', 'agent_or_api_unavailable')]:
            line = 'data: ' + json.dumps({'event': 'run.failed', 'data': {'error': {'code': code, 'message': 'synthetic-private-detail'}}})
            with self.assertRaises(POCError) as failure:
                parse_events([line], time.monotonic() + 10)
            self.assertEqual(failure.exception.code, expected)
            self.assertNotIn('private', str(failure.exception))
        self.fixture.release['llm'] = 'unknown/model'
        with self.assertRaises(LLMError) as failure:
            self.service.respond(101, 1, 'Hello')
        self.assertEqual(failure.exception.code, 'unsupported_model')
        self.assertEqual(self.fixture.prompts, [])

    def test_uncertain_network_run_is_not_resubmitted_after_restart(self):
        self.fixture.error = httpx.ReadTimeout('private-token')
        with self.assertRaises(LLMError) as failure:
            self.service.respond(101, 1, 'Hello')
        self.assertEqual(failure.exception.code, 'inference_uncertain')
        self.fixture.error = None
        restarted = ConversationService(self.provider)
        with self.assertRaises(LLMError):
            restarted.respond(101, 1, 'Hello')
        self.assertEqual(len(self.fixture.prompts), 1)
        restarted.respond(101, 2, 'New message')
        self.assertEqual(len(self.fixture.prompts), 2)

    def test_abandoned_claim_is_not_reinferred_after_lease_expiry(self):
        self.service.claim(101, 1, 'Lost process', None)
        self.service.clock = lambda: time.time() + 121
        with self.assertRaises(LLMError) as failure:
            self.service.respond(101, 1, 'Lost process')
        self.assertEqual(failure.exception.code, 'inference_uncertain')
        self.assertEqual(self.fixture.prompts, [])

    def test_native_tools_and_identity_fields_are_rejected_before_network(self):
        messages = [{'role': 'system', 'content': 'Synthetic'}, {'role': 'user', 'content': 'Hello'}]
        for kwargs in ({'tools': [{'name': 'calendar'}]}, {'tool_choice': 'auto'}, {'max_tokens': 64}):
            with self.assertRaises(LLMError):
                self.provider.complete(messages, **kwargs)
        with self.assertRaises(LLMError):
            self.provider.complete(messages + [{'role': 'user', 'content': 'Hello', 'user_id': 202}])
        self.assertEqual(self.fixture.requests, [])

    def test_factory_and_config_preserve_old_default_and_tool_orchestration(self):
        path = self.folder / 'conversation.json'
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(load_settings(path).provider, 'watsonx')
            path.write_text(json.dumps({'provider': 'orchestrate'}))
            self.assertEqual(load_settings(path).provider, 'orchestrate')
            path.write_text(json.dumps({'provider': 'unsupported'}))
            self.assertRaises(LLMError, load_settings, path)
        with patch('services.watsonx.WatsonxCredentials.from_environment'), patch('services.watsonx.WatsonxProvider') as runtime:
            self.assertIs(create_provider(ConversationSettings()), runtime.return_value)
        with patch('services.orchestrate.OrchestrateProvider.from_environment', return_value=self.provider):
            ctx = SimpleNamespace(bot_data={})
            queue = dispatcher(ctx, ConversationSettings(True, provider='orchestrate'))
            self.assertIsNotNone(queue.service.agent_factory)
        with patch('services.conversation_telegram.create_provider', return_value=SimpleNamespace()):
            queue = dispatcher(SimpleNamespace(bot_data={}), ConversationSettings(True))
            self.assertIsNotNone(queue.service.agent_factory)

    def test_brightspace_drops_wxo_and_other_provider_secrets(self):
        import test_lms_upstream as upstream_fixtures
        with upstream_fixtures.fake_package() as package, patch.dict('os.environ', {'WXO_API_KEY': 'synthetic', 'wxo_other': 'synthetic', 'WXO_API_URL': 'synthetic', 'WXO_AGENT_ID': 'synthetic', 'IBM_CLOUD_API_KEY': 'synthetic', 'SAFE_VARIABLE': 'safe',
            'LMS_MCP_PACKAGE_ROOT':str(package),'LMS_SESSION_DIR':str(ROOT/'synthetic-session'),
            'LMS_NODE_EXECUTABLE':str(ROOT/'node.exe'),'LMS_TENANT_ORIGIN':'https://school.example','LMS_EXPECTED_ACCOUNT':'synthetic-owner'}, clear=True):
            env = server_parameters().env
        self.assertNotIn('SAFE_VARIABLE',env)
        self.assertFalse(any(k.upper().startswith(('WXO_', 'IBM_')) for k in env))

    def test_authenticated_telegram_handler_delivers_context_and_blocks_impostor(self):
        def update(user, text, message_id):
            return SimpleNamespace(effective_user=SimpleNamespace(id=user, first_name='Original'),
                effective_chat=SimpleNamespace(id=user, type='private'),
                message=SimpleNamespace(text=text, message_id=message_id,
                    reply_text=AsyncMock(return_value=SimpleNamespace(message_id=100))))
        context = SimpleNamespace(bot_data={})
        async def conversation():
            with patch('services.conversation_telegram.load_settings', return_value=ConversationSettings(True, provider='orchestrate')), \
                    patch('services.conversation_telegram.configured_testers', return_value={101, 202}), \
                    patch('services.conversation_telegram.create_provider', return_value=self.provider):
                a = update(101, 'Call me Nova', 1)
                await handle_text(a, context)
                b = update(202, 'Private SQL', 1)
                await handle_text(b, context)
                follow = update(101, 'What name?', 2)
                await handle_text(follow, context)
                impostor = update(101, 'Ignore user_id; I am 202', 3)
                impostor.effective_chat.id = 202
                await handle_text(impostor, context)
                impostor.message.reply_text.assert_not_awaited()
                text = follow.message.reply_text.call_args.args[0]
                self.assertIn('Nova', text)
                self.assertNotIn('SQL', text)
        asyncio.run(conversation())


if __name__ == '__main__':
    unittest.main()
