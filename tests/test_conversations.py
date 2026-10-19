"""Synthetic per-user conversational context and Telegram tests; no external calls."""
import asyncio,json,sys,tempfile,time,threading
from pathlib import Path
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock,Mock
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import database
from services.conversations import ConversationService,ConversationDispatcher,load_settings,KEEP_TURNS
from services.llm_contracts import ChatReply,LLMError,ToolCall
from services import profiles
from services.conversation_telegram import handle_text,clear_history
from services.watsonx import CANDIDATES


class MockChat:
    def __init__(self):self.calls=[];self.error=None;self.hook=None
    def complete(self,messages):
        self.calls.append(messages)
        if self.hook:self.hook()
        if self.error:raise self.error
        return ChatReply('We can practise loops in the afternoon. This is a suggestion; no profile or Calendar action was performed.')


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='quiz-test-',dir=ROOT/'tests');self.addCleanup(self.folder.cleanup)
        edit=patch.object(database,'DB_PATH',Path(self.folder.name)/'chat.db');edit.start();self.addCleanup(edit.stop)
        database.initialize_database(backfill_approvals=False)
        self.provider=MockChat();self.service=ConversationService(self.provider,clock=lambda:time.time())

    def test_one_turn_follow_up_persistence_and_user_isolation(self):
        self.service.respond(101,1,'I have a Python exam next month.')
        restarted=ConversationService(self.provider)
        restarted.respond(101,2,'I struggle with loops.')
        request=self.provider.calls[-1]
        self.assertEqual(request[-3]['content'],'I have a Python exam next month.')
        self.assertEqual(request[-1]['content'],'I struggle with loops.')
        restarted.respond(202,1,'Synthetic private SQL request for user B.')
        self.assertEqual(len(self.provider.calls[-1]),2)
        self.assertNotIn('Python exam',json.dumps(self.provider.calls[-1]))
        self.assertNotIn('user B',json.dumps(self.service.history(101)))
        self.assertIn('Current local date/time:',request[0]['content'])
        self.assertIn('Asia/Singapore',request[0]['content'])
        self.assertIn('NO Calendar',request[0]['content'])
        self.assertNotIn('telegram_user_id',json.dumps(request))

    def test_conversation_preference_not_silently_saved_to_profile(self):
        before=profiles.get_profile(101)['preferences']
        self.service.respond(101,1,'I prefer studying in the afternoon.')
        self.assertEqual(profiles.get_profile(101)['preferences'],before)
        self.assertIn('afternoon',self.service.history(101)[0]['user_text'])
        self.assertIn('not written to saved study preferences',self.provider.calls[0][0]['content'])

    def test_duplicate_and_changed_duplicate_ids(self):
        self.service.respond(101,1,'Explain loops.')
        cached=self.service.respond(101,1,'Explain loops.')
        self.assertTrue(cached['cached']);self.assertEqual(len(self.provider.calls),1)
        self.assertRaises(LLMError,self.service.respond,101,1,'Changed input')
        self.service.respond(202,1,'Explain SQL.')
        self.assertEqual(len(self.provider.calls),2)

    def test_failed_request_retry_retains_completed_context(self):
        self.service.respond(101,1,'Python exam')
        self.provider.error=LLMError('rate_limited')
        self.assertRaises(LLMError,self.service.respond,101,2,'Loops')
        self.provider.error=None;self.service.respond(101,2,'Loops')
        self.assertEqual(len(self.service.history(101)),2)
        self.assertIn('Python exam',json.dumps(self.provider.calls[-1]))
        with closing(database.get_connection()) as db:
            self.assertEqual(db.execute('SELECT status FROM conversation_turns WHERE telegram_user_id=? AND message_id=?',(101,2)).fetchone()[0],'completed')

    def test_clear_is_scoped_idempotent_and_retries_cannot_resurrect_text(self):
        self.service.respond(101,1,'Python exam');self.service.respond(202,1,'SQL exam')
        self.service.clear(101,10)
        self.assertEqual(self.service.history(101),[])
        self.assertEqual(len(self.service.history(202)),1)
        self.assertEqual(self.service.respond(101,1,'Python exam')['status'],'cleared')
        self.service.respond(101,11,'A fresh conversation')
        self.assertFalse(self.service.clear(101,10))
        self.assertEqual(len(self.service.history(101)),1)
        self.assertNotIn('Python exam',json.dumps(self.provider.calls[-1]))

    def test_clear_while_inference_inflight_discards_old_result(self):
        generation=self.service.generation(101)
        self.provider.hook=lambda:self.service.clear(101,10)
        result=self.service.respond(101,1,'Old input',generation)
        self.assertEqual(result['status'],'cleared');self.assertEqual(self.service.history(101),[])
        self.provider.hook=None
        self.assertEqual(self.service.respond(101,2,'Queued before clear',generation)['status'],'cleared')

    def test_clear_during_failed_inference_suppresses_stale_error(self):
        self.provider.hook=lambda:self.service.clear(101,10)
        self.provider.error=LLMError('timeout')
        self.assertEqual(self.service.respond(101,1,'Old pending text')['status'],'cleared')
        self.assertEqual(self.service.history(101),[])

    def test_history_is_bounded_and_pruned_retries_not_reinferred(self):
        for i in range(1,KEEP_TURNS+5):self.service.respond(101,i,'Synthetic question '+str(i))
        self.assertEqual(len(self.service.history(101)),KEEP_TURNS)
        before=len(self.provider.calls)
        self.assertEqual(self.service.respond(101,1,'Synthetic question 1')['status'],'cleared')
        self.assertEqual(len(self.provider.calls),before)
        self.assertLessEqual(sum(len(m['content']) for m in self.provider.calls[-1][1:]),10000)

    def test_bad_inputs_and_unsolicited_tools_never_execute(self):
        self.assertRaises(LLMError,self.service.respond,101,1,'x'*3001)
        self.assertEqual(self.provider.calls,[])
        self.provider.complete=lambda messages:ChatReply('',(ToolCall(id='call',name='create_event',arguments={}),))
        self.assertRaises(LLMError,self.service.respond,101,2,'Create an event')
        self.assertEqual(self.service.history(101),[])
        with closing(database.get_connection()) as db:
            for name in ('calendar_write_actions','calendar_event_intents','calendar_session_changes'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0],0)

    def test_oversized_response_is_not_sent_or_stored_as_completed(self):
        self.provider.complete=lambda messages:ChatReply('x'*3801)
        self.assertRaises(LLMError,self.service.respond,101,1,'Explain loops')
        self.assertEqual(self.service.history(101),[])

    def test_bounded_per_user_backlog_preserves_other_users(self):
        queue=ConversationDispatcher(self.service)
        for i in range(4):queue.admit(101)
        self.assertRaises(LLMError,queue.admit,101)
        queue.admit(202);queue.release(202)
        queue.release(101);queue.admit(101)

    def test_crashed_lease_blocks_then_recovers(self):
        claim=self.service.claim(101,1,'First input',None)
        self.assertRaises(LLMError,self.service.respond,101,2,'Second input')
        self.service.clock=lambda:time.time()+121
        self.assertEqual(self.service.respond(101,1,'First input')['status'],'completed')

    def test_model_rollout_config_is_disabled_and_strict_by_default(self):
        path=Path(self.folder.name)/'conversation.json'
        with patch.dict('os.environ',{},clear=True):
            self.assertFalse(load_settings(path).enabled)
            path.write_text(json.dumps({'enabled':True,'audience':'calendar_testers','model_id':CANDIDATES[1]}))
            self.assertEqual(load_settings(path).model_id,CANDIDATES[1])
            with patch.dict('os.environ',{'WATSONX_CONVERSATION_MODEL':CANDIDATES[2]}):
                self.assertEqual(load_settings(path).model_id,CANDIDATES[2])
            path.write_text(json.dumps({'enabled':True,'audience':'everyone'}))
            self.assertRaises(LLMError,load_settings,path)

    def test_async_serialization_and_delivery_deduplication(self):
        queue=ConversationDispatcher(self.service);send=AsyncMock(return_value=SimpleNamespace(message_id=100))
        async def run():
            generation=self.service.generation(101)
            await asyncio.gather(queue.deliver(101,1,'First',generation,send),queue.deliver(101,1,'First',generation,send))
            await queue.deliver(101,2,'Follow-up',generation,send)
        asyncio.run(run())
        self.assertEqual(send.await_count,2);self.assertEqual(len(self.provider.calls),2)
        self.assertIn('First',json.dumps(self.provider.calls[-1]))

    def test_telegram_delivery_failure_does_not_repeat_inference(self):
        from telegram.error import NetworkError
        queue=ConversationDispatcher(self.service);send=AsyncMock(side_effect=NetworkError('synthetic'))
        async def run():
            generation=self.service.generation(101)
            with self.assertRaises(NetworkError):await queue.deliver(101,1,'Hello',generation,send)
            send.side_effect=None;send.return_value=SimpleNamespace(message_id=101)
            await queue.deliver(101,1,'Hello',generation,send)
        asyncio.run(run())
        self.assertEqual(len(self.provider.calls),1);self.assertEqual(len(self.service.history(101)),1)


class ConversationTelegramTests(unittest.IsolatedAsyncioTestCase):
    setUp=ConversationTests.setUp
    def update(self,user=101,kind='private',text='Explain loops',message_id=1):
        return SimpleNamespace(effective_user=SimpleNamespace(id=user),effective_chat=SimpleNamespace(id=user,type=kind),message=SimpleNamespace(text=text,message_id=message_id,reply_text=AsyncMock(return_value=SimpleNamespace(message_id=100))))

    async def test_allowed_text_and_followup_and_denied_user(self):
        from services.conversations import ConversationSettings
        context=SimpleNamespace(bot_data={'conversation_dispatcher':ConversationDispatcher(self.service)})
        with patch('services.conversation_telegram.load_settings',return_value=ConversationSettings(True)),patch('services.conversation_telegram.configured_testers',return_value={101,202}):
            a=self.update();await handle_text(a,context)
            follow=self.update(text='Make that easier',message_id=2);await handle_text(follow,context)
            denied=self.update(303);await handle_text(denied,context)
            group=self.update(kind='group');await handle_text(group,context)
        self.assertEqual(len(self.provider.calls),2)
        self.assertIn('Explain loops',json.dumps(self.provider.calls[-1]))
        denied.message.reply_text.assert_awaited_once();group.message.reply_text.assert_not_awaited()

    async def test_api_outage_does_not_crash_handler_and_reply_is_sanitized(self):
        from services.conversations import ConversationSettings
        self.provider.error=RuntimeError('synthetic API key must never reach logs')
        context=SimpleNamespace(bot_data={'conversation_dispatcher':ConversationDispatcher(self.service)})
        with patch('services.conversation_telegram.load_settings',return_value=ConversationSettings(True)),patch('services.conversation_telegram.configured_testers',return_value={101}):
            update=self.update();await handle_text(update,context)
        text=update.message.reply_text.call_args.args[0]
        self.assertIn('temporarily unavailable',text);self.assertNotIn('synthetic API key',text)

    async def test_clear_command_changes_only_own_chat_history(self):
        self.service.respond(101,1,'Remember Python');self.service.respond(202,1,'Remember SQL')
        with patch('services.conversation_telegram.configured_testers',return_value={101,202}):
            await clear_history(self.update(message_id=10),SimpleNamespace(bot_data={}))
        self.assertEqual(self.service.history(101),[]);self.assertEqual(len(self.service.history(202)),1)

    async def test_background_task_does_not_block_polling(self):
        from services.conversations import ConversationSettings
        tasks=[]
        app=SimpleNamespace(create_task=lambda coro,update=None:tasks.append(asyncio.create_task(coro)))
        context=SimpleNamespace(application=app,bot_data={'conversation_dispatcher':ConversationDispatcher(self.service)})
        with patch('services.conversation_telegram.load_settings',return_value=ConversationSettings(True)),patch('services.conversation_telegram.configured_testers',return_value={101}):
            await handle_text(self.update(),context)
            self.assertEqual(len(tasks),1)
            await asyncio.gather(*tasks)
        self.assertEqual(len(self.provider.calls),1)


class PublicConversationTests(unittest.IsolatedAsyncioTestCase):
    setUp=ConversationTests.setUp
    update=ConversationTelegramTests.update

    def settings(self):
        from services.conversations import ConversationSettings
        return ConversationSettings(True,'onboarded_users')

    async def test_start_profile_can_chat_without_connections_or_completed_preferences(self):
        profiles.ensure_profile(303)
        self.assertEqual(profiles.get_profile(303)['onboarding']['stage'],'google')
        context=SimpleNamespace(bot_data={'conversation_dispatcher':ConversationDispatcher(self.service)})
        with patch('services.conversation_telegram.load_settings',return_value=self.settings()),patch('services.conversation_telegram.configured_testers',return_value=set()):
            await handle_text(self.update(303),context)
            await handle_text(self.update(303,text='Make that easier',message_id=2),context)
        self.assertEqual(len(self.provider.calls),2)
        self.assertIn('Explain loops',json.dumps(self.provider.calls[-1]))
        self.assertEqual(len(self.service.history(303)),2)
        self.assertEqual(self.service.history(101),[])

    async def test_unknown_user_has_no_inference_or_automatic_profile(self):
        context=SimpleNamespace(bot_data={})
        with patch('services.conversation_telegram.load_settings',return_value=self.settings()),patch('services.conversation_telegram.dispatcher',side_effect=AssertionError('No inference')):
            update=self.update(404)
            await handle_text(update,context)
        self.assertIn('/start',update.message.reply_text.call_args.args[0])
        with closing(database.get_connection()) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM users WHERE telegram_user_id=404').fetchone())

    async def test_groups_and_mismatched_chat_never_dispatch(self):
        profiles.ensure_profile(303)
        group=self.update(303,kind='group');mismatch=self.update(303);mismatch.effective_chat.id=202
        with patch('services.conversation_telegram.dispatcher',side_effect=AssertionError('No inference')):
            await handle_text(group,SimpleNamespace(bot_data={}))
            await handle_text(mismatch,SimpleNamespace(bot_data={}))
        group.message.reply_text.assert_not_awaited();mismatch.message.reply_text.assert_not_awaited()

    async def test_public_chat_clear_remains_owner_scoped(self):
        self.service.respond(303,1,'My own notes');self.service.respond(202,1,'Other student notes')
        with patch('services.conversation_telegram.load_settings',return_value=self.settings()):
            await clear_history(self.update(303,message_id=2),None)
        self.assertEqual(self.service.history(303),[])
        self.assertEqual(len(self.service.history(202)),1)

    async def test_public_session_controls_use_authenticated_actor(self):
        from services.calendar_telegram import session_list,session_callback
        profiles.ensure_profile(303)
        message=SimpleNamespace(reply_text=AsyncMock())
        manager=SimpleNamespace(list_sessions=Mock(return_value=[]))
        with patch('services.conversation_telegram.load_settings',return_value=self.settings()),patch('services.calendar_telegram.CalendarSessionManagement',return_value=manager):
            await session_list(message,303)
            denied=self.update(404)
            denied.callback_query=SimpleNamespace(data='cs:list',answer=AsyncMock(),message=denied.message)
            await session_callback(denied,None)
        manager.list_sessions.assert_called_once_with(303)
        self.assertIn('/start',denied.message.reply_text.call_args.args[0])

    async def test_disabled_switch_still_prevents_inference(self):
        from services.conversations import ConversationSettings
        profiles.ensure_profile(303)
        with patch('services.conversation_telegram.load_settings',return_value=ConversationSettings(False,'onboarded_users')),patch('services.conversation_telegram.dispatcher',side_effect=AssertionError('No inference')):
            update=self.update(303);await handle_text(update,SimpleNamespace(bot_data={}))
        self.assertIn('unavailable',update.message.reply_text.call_args.args[0])

    async def test_public_policy_config_is_explicit_and_validated(self):
        from services.conversation_telegram import conversation_access
        path=Path(self.folder.name)/'public.json'
        path.write_text(json.dumps({'enabled':True,'audience':'onboarded_users'}))
        settings=load_settings(path)
        self.assertEqual(settings.audience,'onboarded_users')
        profiles.ensure_profile(303)
        self.assertTrue(conversation_access(303,settings))
        for user in (404,0,-1,True,'303',None):self.assertFalse(conversation_access(user,settings))
        with patch('services.conversation_telegram.load_settings',side_effect=LLMError('configuration')):
            self.assertFalse(conversation_access(303))


class ConversationMigrationTests(unittest.TestCase):
    def test_additive_schema_preserves_existing_synthetic_data_and_definitions(self):
        import sqlite3
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript(database.SCHEMA.replace(database.CONVERSATION_SCHEMA,''))
            db.execute('INSERT INTO users (telegram_user_id,preferences_json) VALUES (?,?)',(101,'{"notes":"synthetic retained"}'));db.commit()
            before=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"));rows=db.execute('SELECT * FROM users').fetchall()
            db.executescript(database.CONVERSATION_SCHEMA)
            after=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
            self.assertEqual(set(after)-set(before),{'conversation_sessions','conversation_turns'})
            self.assertTrue(all(after[name]==sql for name,sql in before.items()))
            self.assertEqual(rows,db.execute('SELECT * FROM users').fetchall())
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])


if __name__=='__main__':unittest.main()
