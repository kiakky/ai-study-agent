"""Native tool loop with temporary SQLite, synthetic actors and mocked Google/IBM."""
from contextlib import closing
import asyncio
from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import httpx
import test_calendar_actions as fixtures
import test_calendar_conversation as management_fixtures
from test_orchestrate_calendar import CONFIG
import database
from services.calendar_native import NativeCalendarAgent
from services.orchestrate_native import OrchestrateNativeProvider, MODEL, inline_parameters
from services.study_tools import SchedulingTools
from services.llm_contracts import ChatReply, ToolCall, LLMError
from services.conversations import ConversationService
from services.calendar_conversation import CalendarConversation
from services.calendar_text_decisions import apply_decision
from services import profiles
from models.preferences import StudyPreferences


def call(name, **arguments):
    if name=='check_practice_delivery' and 'clarification_kind' not in arguments:
        arguments['clarification_kind']='topic' if arguments.get('outcome')=='clarification' else 'none'
    return ChatReply('', (ToolCall(id='call-'+name, name=name, arguments=arguments),))


class ScriptedModel:
    native_calendar_tools = True
    prevent_uncertain_replay = True
    def __init__(self, steps):
        self.steps = list(steps)
        self.requests = []
    def complete(self, messages, **kwargs):
        self.requests.append(json.loads(json.dumps(messages)))
        if kwargs.get('tools',[{}])[0].get('function',{}).get('name')=='classify_practice_scope':
            return call('classify_practice_scope',mode='concept_practice')
        if kwargs.get('tools',[{}])[0].get('function',{}).get('name')=='qualify_practice_sources':
            data=json.loads(messages[-1]['content']);item=data['evidence'][0]
            return call('qualify_practice_sources',learning_passages=[item['reference']],assessment_passages=[],focus=data['topic'])
        value = self.steps.pop(0)
        reply = value(messages) if callable(value) else value
        # Actual providers issue fresh call IDs for every completion.
        return ChatReply(reply.content, tuple(c.model_copy(update={'id': c.id+str(len(self.requests))}) for c in reply.tool_calls))


class NativeCalendarTests(unittest.TestCase):
    setUp = fixtures.CalendarActionTests.setUp
    connect = fixtures.CalendarActionTests.connect
    proposal = fixtures.CalendarActionTests.proposal
    approved_action = fixtures.CalendarActionTests.approved_action
    setup_events = management_fixtures.CalendarManagementTests.setup_events

    def agent(self, user=101, message=10, **kwargs):
        return NativeCalendarAgent(user, message, SchedulingTools(user, self.planner, self.actions), **kwargs)

    def service(self, model):
        return ConversationService(model, clock=lambda: fixtures.NOW.timestamp(),
            agent_factory=lambda u,m,**kw: self.agent(u,m,**kw))

    def slots(self, **kwargs):
        return call('find_calendar_slots', start_date='2026-10-19', end_date='2026-11-02', **kwargs)

    @staticmethod
    def create(messages, indices=(0,2,7,9)):
        result = next(json.loads(m['content']) for m in reversed(messages) if m['role']=='tool' and 'slots' in json.loads(m['content']))
        return call('create_calendar_events', subject='Machine Learning', events=[
            {'slot_reference': result['slots'][i]['slot_reference'], 'title': 'Machine Learning revision'} for i in indices])

    def plan(self, user=101, message=10, indices=(0,2,7,9)):
        model = ScriptedModel([call('calendar_context'), self.slots(),
            lambda m:self.create(m,indices), ChatReply('Review the proposed sessions.')])
        agent = self.agent(user,message)
        agent.complete(model,[{'role':'system','content':'Current date 12 October 2026, timezone Asia/Singapore.'},
                              {'role':'user','content':'Plan two Machine Learning sessions per week over the next two weeks.'}])
        result = next(r for r in reversed(agent.references()) if r.get('review_kind')=='plan')
        return result,agent,model

    def present(self, result, user=101):
        service = CalendarConversation(self.actions)
        review,_ = service.prepare(user,result['proposal_id'])
        service.presented(user,review['id'])
        return service,review

    def test_model_selects_count_dates_and_tools_without_router(self):
        result,agent,model = self.plan()
        self.assertEqual(len(result['sessions']),4)
        dates=[datetime.fromisoformat(s['start']).date() for s in result['sessions']]
        self.assertEqual(sum(d<datetime(2026,10,26).date() for d in dates),2)
        self.assertEqual(sum(d>=datetime(2026,10,26).date() for d in dates),2)
        self.assertEqual(self.provider.inserts,[])
        self.assertNotIn('route_request',json.dumps(model.requests))
        service,_=self.present(result)
        self.assertEqual(apply_decision(101,20,'Approve all four',service)['status'],'completed')
        self.assertEqual(len(self.provider.inserts),4)
        self.assertEqual(apply_decision(101,20,'Approve all four',service)['status'],'completed')
        self.assertEqual(len(self.provider.inserts),4)

    def test_free_wording_and_frequency_answers_are_sent_to_model_unchanged(self):
        model=ScriptedModel([ChatReply('Would you prefer two or three per week?'),ChatReply('I can prepare two per week.')])
        service=self.service(model)
        service.respond(101,1,'I think 2-3 sessions per week should be enough')
        self.service(model).respond(101,2,'two sounds good, cheers')
        self.assertEqual(model.requests[0][-1]['content'],'I think 2-3 sessions per week should be enough')
        self.assertEqual(model.requests[1][-1]['content'],'two sounds good, cheers')
        self.assertIn('Would you prefer two or three per week?',json.dumps(model.requests[1]))
        self.assertEqual(self.provider.reads,[])

    def test_fresh_conversation_keeps_latest_message_and_preserves_pending_state(self):
        result, _, _ = self.plan(message=80)
        before = next(row for row in self.planner.proposals(101) if row['id']==result['proposal_id'])
        preferences = profiles.get_profile(101)['preferences']
        reads = len(self.provider.reads)
        model = ScriptedModel([ChatReply('Which weekdays would you prefer?'),
            ChatReply('Sure, what would you like to talk about?'),
            ChatReply('I can help with your study questions and planning.')])
        self.service(model).respond(101,81,'Two sessions each week for Machine Learning')
        self.service(model).respond(101,82,'new convo')
        self.service(model).respond(101,83,'How can you help me?')
        reset = model.requests[1]
        self.assertEqual(reset[-1],{'role':'user','content':'new convo'})
        self.assertTrue(any(m.get('content')=='Two sessions each week for Machine Learning' for m in reset))
        self.assertEqual(model.requests[2][-1]['content'],'How can you help me?')
        after = next(row for row in self.planner.proposals(101) if row['id']==result['proposal_id'])
        self.assertEqual(before,after)
        self.assertEqual(self.provider.inserts,[])
        self.assertEqual(profiles.get_profile(101)['preferences'],preferences)
        self.assertEqual(len(self.provider.reads),reads)

    def test_repeated_creation_calls_append_and_present_only_latest_complete_plan(self):
        model=ScriptedModel([call('calendar_context'),self.slots(),lambda m:self.create(m,(0,)),
            lambda m:self.create(m,(7,)),ChatReply('Two proposed sessions.')])
        agent=self.agent()
        agent.complete(model,[{'role':'system','content':'Study'},{'role':'user','content':'One in each week please'}])
        results=[r for r in agent.references() if r.get('review_kind')=='plan']
        self.assertEqual([len(r['sessions']) for r in results],[1,2])
        with closing(database.get_connection()) as db:
            count=db.execute("SELECT COUNT(*) FROM conversation_tool_effects WHERE telegram_user_id=101 AND message_id=10 AND presented=0 AND result_json LIKE '%review_kind%' ").fetchone()[0]
        self.assertEqual(count,1)
        self.assertEqual(self.provider.inserts,[])

    def test_task_overrides_preserve_saved_defaults(self):
        before=profiles.get_profile(101)['preferences']
        model=ScriptedModel([call('calendar_context'),self.slots(session_minutes=20,
            study_windows=[{'start':'13:00','end':'17:00'}]),lambda m:self.create(m,(0,7)),ChatReply('Review')])
        agent=self.agent()
        agent.complete(model,[{'role':'system','content':'Study'},{'role':'user','content':'Afternoons, twenty minutes this time'}])
        result=agent.references()[-1]
        for s in result['sessions']:
            self.assertEqual(datetime.fromisoformat(s['end'])-datetime.fromisoformat(s['start']),timedelta(minutes=20))
        self.assertEqual(profiles.get_profile(101)['preferences'],before)

    def test_owned_pending_amendment_preserves_other_sessions_and_invalidates_review(self):
        plan,_,_=self.plan()
        service,old=self.present(plan)
        def move(messages):
            context=json.loads(messages[-1]['content'])
            return call('change_calendar_event',session_reference=context['pending_plan']['sessions'][0]['session_reference'],
                        operation='move',local_time='19:00')
        model=ScriptedModel([call('calendar_context'),move,ChatReply('Review the amendment')])
        agent=self.agent(message=11)
        agent.complete(model,[{'role':'system','content':'Study'},{'role':'user','content':'Only move the first one to seven in the evening'}])
        revised=agent.references()[-1]
        # Opaque references change with the immutable proposal version.
        for old_s,new_s in zip(plan['sessions'][1:],revised['sessions'][1:]):
            self.assertEqual({k:v for k,v in old_s.items() if k!='session_reference'},
                             {k:v for k,v in new_s.items() if k!='session_reference'})
        self.assertEqual(service.get(101,old['id'])['status'],'blocked')
        self.assertEqual(self.provider.inserts,[])

    def test_cross_user_and_fabricated_slot_references_do_not_create_or_modify_events(self):
        plan,_,_=self.plan()
        model=ScriptedModel([call('change_calendar_event',session_reference=plan['sessions'][0]['session_reference'],operation='move',local_time='19:00'),ChatReply('That reference is unavailable')])
        agent=self.agent(202)
        agent.complete(model,[{'role':'system','content':'Study'},{'role':'user','content':'Move it'}])
        self.assertEqual(self.planner.proposals(202),[])
        model=ScriptedModel([call('create_calendar_events',subject='ML',events=[{'slot_reference':'invented','title':'ML'}]),ChatReply('Need availability')])
        self.agent(message=12).complete(model,[{'role':'system','content':'Study'},{'role':'user','content':'Add one'}])
        self.assertEqual(self.provider.inserts,[])

    def test_invalid_known_types_are_returned_for_model_repair_without_repeating_user_question(self):
        model=ScriptedModel([call('find_calendar_slots',start_date='not-a-date',end_date='2026-11-02'),
            self.slots(),ChatReply('Availability checked')])
        agent=self.agent()
        agent.complete(model,[{'role':'system','content':'Study'},{'role':'user','content':'Check next two weeks'}])
        feedback=json.loads(model.requests[1][-1]['content'])
        self.assertEqual(feedback['error_code'],'argument_format')
        self.assertEqual(feedback['fields'][0]['field'],'start_date')
        self.assertEqual(len(self.provider.reads),1)

    def test_forged_identity_and_unknown_operations_fail_closed(self):
        for request in [call('calendar_context',user_id=202),call('execute_calendar_events')]:
            with self.subTest(name=request.tool_calls[0].name):
                model=ScriptedModel([request])
                with self.assertRaises(LLMError):
                    self.agent(message=40).complete(model,[{'role':'system','content':'Study'},{'role':'user','content':'Hi'}])
        self.assertEqual(self.provider.inserts,[])

    def test_repeated_identical_calls_are_bounded_before_second_operation(self):
        model=ScriptedModel([call('calendar_context'),call('calendar_context')])
        agent=self.agent()
        with self.assertRaises(LLMError) as error:
            agent.complete(model,[{'role':'system','content':'Study'},{'role':'user','content':'Check'}])
        self.assertEqual(error.exception.code,'tool_loop')
        self.assertEqual(len(agent.references()),2)  # Initial server context plus first explicit refresh.

    def test_existing_cancellation_proposes_only_and_requires_human_approval(self):
        self.setup_events()
        def cancel(messages):
            result=json.loads(messages[-1]['content'])
            return call('change_calendar_event',session_reference=result['scheduled_sessions'][1]['session_reference'],operation='cancel')
        model=ScriptedModel([call('calendar_context'),cancel,ChatReply('Review')])
        agent=self.agent(message=50)
        agent.complete(model,[{'role':'system','content':'Study'},{'role':'user','content':'Cancel only my second session'}])
        result=agent.references()[-1]
        self.assertEqual(result['review_kind'],'change')
        self.assertEqual(self.deletes,[])
        with closing(database.get_connection()) as db,db:
            db.execute('UPDATE conversation_tool_effects SET presented=1 WHERE telegram_user_id=101 AND message_id=50')
        self.assertEqual(apply_decision(101,51,'Reject this cancellation',change_service=self.management)['status'],'rejected')
        self.assertEqual(self.deletes,[])

    def test_cached_delivery_does_not_repeat_model_or_plan_operations(self):
        model=ScriptedModel([self.slots(),lambda m:self.create(m,(0,7)),ChatReply('Review')])
        service=self.service(model)
        first=service.respond(101,60,'Two sessions please')
        second=self.service(model).respond(101,60,'Two sessions please')
        self.assertEqual(first['text'],second['text'])
        self.assertTrue(second['cached'])
        self.assertEqual(len(model.requests),3)
        self.assertEqual(len(self.planner.proposals(101)),1)
        self.assertEqual(self.provider.inserts,[])

    def test_refresh_after_amendment_can_select_another_session_in_same_turn(self):
        plan,_,_=self.plan()
        def move(index,at):
            def choose(messages):
                value=json.loads(messages[-1]['content'])
                return call('change_calendar_event',session_reference=value['pending_plan']['sessions'][index]['session_reference'],operation='move',local_time=at)
            return choose
        model=ScriptedModel([call('calendar_context'),move(0,'19:00'),call('calendar_context'),move(1,'19:00'),ChatReply('Review')])
        agent=self.agent(message=70)
        agent.complete(model,[{'role':'system','content':'Study'},{'role':'user','content':'Move the first two to 7pm on their own days'}])
        revised=next(r for r in reversed(agent.references()) if r.get('review_kind')=='plan')
        for index in (0,1):
            self.assertEqual(datetime.fromisoformat(revised['sessions'][index]['start'])-
                             datetime.fromisoformat(plan['sessions'][index]['start']),timedelta(hours=1))
        self.assertEqual(revised['sessions'][2]['start'],plan['sessions'][2]['start'])
        self.assertEqual(self.provider.inserts,[])

    def test_whole_plan_replacement_requires_explicit_flag(self):
        old,_,_=self.plan()
        model=ScriptedModel([self.slots(),lambda m:self.create(m,(0,7)),ChatReply('Need selected change')])
        agent=self.agent(message=80)
        agent.complete(model,[{'role':'system','content':'Study'},{'role':'user','content':'Change just the first one'}])
        self.assertEqual(len(self.planner.proposals(101)),1)
        self.assertEqual(self.planner.proposals(101)[0]['id'],old['proposal_id'])
        self.assertEqual(agent.references()[-1]['error_code'],'scheduling_constraint')

    def test_pending_cancellation_retains_other_exact_sessions(self):
        old,_,_=self.plan()
        def cancel(messages):
            value=json.loads(messages[-1]['content'])
            return call('change_calendar_event',session_reference=value['pending_plan']['sessions'][1]['session_reference'],operation='cancel')
        model=ScriptedModel([call('calendar_context'),cancel,ChatReply('Review')])
        agent=self.agent(message=90)
        agent.complete(model,[{'role':'system','content':'Study'},{'role':'user','content':'Remove just the second from this draft'}])
        new=agent.references()[-1]
        self.assertEqual([s['start'] for s in new['sessions']], [s['start'] for i,s in enumerate(old['sessions']) if i!=1])
        self.assertEqual(self.provider.inserts,[])

    def test_concurrent_actors_keep_their_own_defaults_and_private_context(self):
        profiles.save_preferences(101,StudyPreferences(session_minutes=20,study_windows=[{'start':'09:00','end':'11:00'}]))
        profiles.save_preferences(202,StudyPreferences(session_minutes=45,study_windows=[{'start':'18:00','end':'20:00'}]))
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.plan,101,100,(0,7))
            second=pool.submit(self.plan,202,100,(0,7))
            a,_,ma=first.result();b,_,mb=second.result()
        for result,minutes in ((a,20),(b,45)):
            self.assertEqual(datetime.fromisoformat(result['sessions'][0]['end'])-
                datetime.fromisoformat(result['sessions'][0]['start']),timedelta(minutes=minutes))
        self.assertNotIn('synthetic-access',json.dumps(ma.requests+mb.requests))
        self.assertEqual(self.provider.inserts,[])

    def test_conflict_between_availability_and_drafting_stops_plan_creation(self):
        def become_busy(messages):
            self.provider.freebusy=lambda *args:[{'start':'2026-10-19T00:00:00+08:00','end':'2026-11-02T00:00:00+08:00'}]
            return self.create(messages,(0,7))
        model=ScriptedModel([self.slots(),become_busy,ChatReply('These slots became busy')])
        agent=self.agent()
        agent.complete(model,[{'role':'system','content':'Study'},{'role':'user','content':'Two sessions'}])
        self.assertEqual(self.planner.proposals(101),[])
        self.assertEqual(self.provider.inserts,[])

    def test_checked_slots_skip_full_days_and_fit_around_partial_busy_windows(self):
        busy=[{'start':'2026-10-19T00:00:00+08:00','end':'2026-10-20T00:00:00+08:00'},
              {'start':'2026-10-20T18:00:00+08:00','end':'2026-10-20T19:00:00+08:00'},
              {'start':'2026-10-21T18:00:00+08:00','end':'2026-10-21T18:10:00+08:00'},
              {'start':'2026-10-21T18:30:00+08:00','end':'2026-10-21T19:05:00+08:00'}]
        self.provider.freebusy=lambda *args:busy
        agent=self.agent()
        model=ScriptedModel([call('find_calendar_slots',start_date='2026-10-19',end_date='2026-10-22'),
            ChatReply('These are the available options.')])
        agent.complete(model,[{'role':'system','content':'Study'},{'role':'user','content':'What times are free? Just show options.'}])
        slots=next(r['slots'] for r in agent.references() if 'slots' in r)
        from zoneinfo import ZoneInfo
        starts=[datetime.fromisoformat(s['start']).astimezone(ZoneInfo('Asia/Singapore')).isoformat() for s in slots]
        self.assertEqual(starts,['2026-10-20T19:00:00+08:00','2026-10-21T19:05:00+08:00'])
        self.assertEqual(self.planner.proposals(101),[])
        self.assertEqual(self.provider.inserts,[])

    def test_new_conflict_after_review_blocks_approval_without_writes(self):
        from services.planning import PlanningError
        plan,_,_=self.plan()
        service,review=self.present(plan)
        self.provider.freebusy=lambda *args:[{'start':plan['sessions'][0]['start'],'end':plan['sessions'][0]['end']}]
        with self.assertRaises(PlanningError):
            service.decide(101,review['id'],'approve')
        self.assertEqual(self.provider.inserts,[])

    def test_disconnected_state_is_available_without_model_guessing_or_shared_account(self):
        self.calendar.disconnect(202)
        model=ScriptedModel([ChatReply('Connect your own Calendar in Settings')])
        self.service(model).respond(202,1,'Help me plan')
        self.assertIn('"status": "disconnected"',model.requests[0][0]['content'])
        self.assertNotIn('synthetic-access',json.dumps(model.requests))
        self.assertEqual(self.provider.reads,[])
        self.assertEqual(self.provider.inserts,[])

    def test_actual_telegram_dispatcher_uses_native_agent_and_presents_exact_approval(self):
        from services.conversation_telegram import dispatcher,handle_text
        from services.conversations import ConversationSettings
        model=ScriptedModel([ChatReply('How many per week?'),ChatReply('Two or three per week?'),
            self.slots(),lambda m:self.create(m),ChatReply('Review')])
        context=SimpleNamespace(bot_data={})
        settings=ConversationSettings(True,provider='orchestrate')
        review_service=CalendarConversation(self.actions)
        with patch('services.conversation_telegram.create_provider',return_value=model), \
             patch('services.conversation_telegram.load_settings',return_value=settings), \
             patch('services.conversation_telegram.configured_testers',return_value={101,202}), \
             patch('services.calendar_agent.SchedulingTools',side_effect=lambda user:SchedulingTools(user,self.planner,self.actions)), \
             patch('services.calendar_telegram.CalendarConversation',return_value=review_service):
            queue=dispatcher(context,settings)
            self.assertIs(queue.service.agent_factory,NativeCalendarAgent)
            queue.service.clock=lambda:fixtures.NOW.timestamp()
            for index,text in enumerate(('Plan Machine Learning for the next two weeks',
                'I think 2-3 sessions per week should be enough','2'),1):
                message=SimpleNamespace(text=text,message_id=index,reply_to_message=None,
                    reply_text=AsyncMock(return_value=SimpleNamespace(message_id=100+index)))
                update=SimpleNamespace(message=message,effective_user=SimpleNamespace(id=101,first_name='Synthetic'),
                    effective_chat=SimpleNamespace(id=101,type='private'))
                asyncio.run(handle_text(update,context))
            delivered='\n'.join(c.args[0] for c in message.reply_text.await_args_list)
            self.assertIn('Review 4 exact study event(s)',delivered)
            self.assertIn('Review all sessions above, then choose',delivered)
            self.assertNotIn('Nothing has been written',delivered)
            self.assertIn('check its current status',delivered)
            buttons=[c.kwargs['reply_markup'] for c in message.reply_text.await_args_list if c.kwargs.get('reply_markup')]
            self.assertEqual(buttons[0].inline_keyboard[0][0].text,'Approve and create events')
            update.effective_chat.id=202
            message.reply_text.reset_mock()
            asyncio.run(handle_text(update,context))
            message.reply_text.assert_not_awaited()
        self.assertEqual(len(model.requests),5)
        self.assertEqual(self.provider.inserts,[])


class NativeProviderTests(unittest.TestCase):
    def test_inlined_schemas_preserve_title_arguments_and_nested_validation(self):
        from services.calendar_native import CreateEventsInput, SlotsInput
        from jsonschema import Draft202012Validator
        schema=inline_parameters(CreateEventsInput.model_json_schema())
        self.assertIn('title',schema['properties']['events']['items']['properties'])
        self.assertTrue(Draft202012Validator(schema).is_valid({'subject':'ML',
            'events':[{'slot_reference':'1:0','title':'Revision'}]}))
        self.assertFalse(Draft202012Validator(schema).is_valid({'subject':'ML',
            'events':[{'slot_reference':'1:0'}]}))
        schema=inline_parameters(SlotsInput.model_json_schema())
        self.assertTrue(Draft202012Validator(schema).is_valid({'start_date':'2026-10-19',
            'end_date':'2026-10-26','study_windows':[{'start':'13:00','end':'18:00'}]}))

    def provider(self, responses):
        requests=[]
        def transport(request):
            requests.append(request)
            if request.url.host=='iam.cloud.ibm.com':
                return httpx.Response(200,json={'access_token':'synthetic-iam','expires_in':3600})
            value=responses.pop(0)
            if isinstance(value,Exception):raise value
            return value
        client=httpx.Client(transport=httpx.MockTransport(transport))
        self.addCleanup(client.close)
        return OrchestrateNativeProvider(CONFIG,client),requests

    @staticmethod
    def response(content='',calls=(),reason='stop'):
        return httpx.Response(200,json={'model':MODEL,'choices':[{'finish_reason':reason,
            'message':{'role':'assistant','content':content,'tool_calls':list(calls)}}]})

    def test_native_protocol_and_tool_result_continuation(self):
        tool={'type':'function','function':{'name':'calendar_context','description':'Read',
            'parameters':{'type':'object','properties':{},'additionalProperties':False}}}
        raw={'id':'native-id','type':'function','function':{'name':'calendar_context','arguments':'{}'}}
        provider,requests=self.provider([self.response(calls=[raw],reason='tool_calls'),self.response('Hello')])
        messages=[{'role':'system','content':'Study'},{'role':'user','content':'Check my Calendar'}]
        reply=provider.complete(messages,tools=[tool])
        self.assertEqual(reply.tool_calls[0].name,'calendar_context')
        messages += [{'role':'assistant','content':'','tool_calls':[raw]},
                     {'role':'tool','tool_call_id':'native-id','content':'{"status":"connected"}'}]
        self.assertEqual(provider.complete(messages,tools=[tool]).content,'Hello')
        body=json.loads(requests[-1].content)
        self.assertIsInstance(body['tools'],list)
        self.assertEqual(body['messages'][-1]['role'],'tool')
        self.assertNotIn('response_schema',body)
        self.assertIn('/gateway/model/chat/completions',str(requests[-1].url))

    def test_tool_result_continuation_excludes_private_backend_fields(self):
        tool={'type':'function','function':{'name':'calendar_context','parameters':{'type':'object','properties':{},'additionalProperties':False}}}
        raw={'id':'native-id','type':'function','function':{'name':'calendar_context','arguments':'{}'}}
        provider,requests=self.provider([self.response('Done')])
        messages=[{'role':'system','content':'Study'},{'role':'user','content':'Check'},
            {'role':'assistant','content':'','tool_calls':[raw]},
            {'role':'tool','tool_call_id':'native-id','content':json.dumps({'old':{'notes':'private-note-sentinel'},
                'google_event_id':'private-event-sentinel','encrypted_credentials':'private-credential-sentinel','status':'connected'})}]
        provider.complete(messages,tools=[tool])
        outbound=requests[-1].content.decode()
        self.assertNotIn('private-note-sentinel',outbound)
        self.assertNotIn('private-event-sentinel',outbound)
        self.assertNotIn('private-credential-sentinel',outbound)
        self.assertIn('connected',outbound)

    def test_network_uncertainty_is_not_replayed_and_credentials_are_redacted(self):
        provider,requests=self.provider([httpx.ReadTimeout('synthetic')])
        with self.assertRaises(LLMError) as error:
            provider.complete([{'role':'system','content':'Study'},{'role':'user','content':'Hi'}])
        self.assertEqual(error.exception.code,'inference_uncertain')
        self.assertEqual(len(requests),2)
        provider,_=self.provider([self.response('synthetic-key synthetic-iam')])
        text=provider.complete([{'role':'system','content':'Study'},{'role':'user','content':'Hi'}]).content
        self.assertNotIn('synthetic-key',text)
        self.assertNotIn('synthetic-iam',text)

    def test_unknown_tool_and_truncated_response_fail_before_execution(self):
        raw={'id':'native-id','type':'function','function':{'name':'approve_all','arguments':'{}'}}
        for response in [self.response(calls=[raw]),self.response('Incomplete',reason='length')]:
            provider,_=self.provider([response])
            with self.assertRaises(LLMError):
                provider.complete([{'role':'system','content':'Study'},{'role':'user','content':'Hi'}])


if __name__=='__main__':unittest.main()
