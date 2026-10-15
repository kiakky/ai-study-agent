"""Real Python Calendar workflow with synthetic Google and JSON model responses."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo
import httpx

import test_calendar_actions as fixtures
import test_calendar_conversation as management_fixtures
from services.calendar_agent import CalendarAgent
from services.calendar_conversation import CalendarConversation
from services.calendar_text_decisions import apply_decision, explicit_decision
from services.calendar_telegram import show_change
from services.conversations import ConversationService
from services.orchestrate import OrchestrateProvider
from services.orchestrate_actions import action_prompt, parse_actions, definitions
from services.orchestrate_client import Configuration, HOST
from services.study_tools import SchedulingTools
from services.pending_proposals import PendingProposals
from services.llm_contracts import LLMError
from services import profiles
from models.preferences import StudyPreferences
import database

CONFIG=Configuration('https://'+HOST+'/instances/synthetic','synthetic-key','00000000-0000-0000-0000-000000000001')


def action(name, arguments):
    return {'content':'','tool_calls':[{'name':name,'arguments':arguments}]}


class JSONModel:
    def __init__(self, intent='planning', steps=(), relative='next_week', weekly=None, count=None, clarification=''):
        self.intent=intent;self.steps=list(steps);self.relative=relative;self.requests=[]
        self.weekly=weekly;self.count=count;self.clarification=clarification
    def __call__(self, prompt):
        payload=json.JSONDecoder().raw_decode(prompt.split('\n',1)[1])[0];self.requests.append(payload)
        names=[t['function']['name'] for t in payload['tools']]
        if names==['route_request']:
            value=action('route_request',{'intent':self.intent,'relative_range':self.relative,'reply':'Hello student.','sessions_per_week':self.weekly,'session_count':self.count,'clarification':self.clarification})
        else:
            step=self.steps.pop(0);value=step(payload) if callable(step) else step
        return {},json.dumps(value)


class OrchestrateCalendarTests(unittest.TestCase):
    setUp=fixtures.CalendarActionTests.setUp
    connect=fixtures.CalendarActionTests.connect
    proposal=fixtures.CalendarActionTests.proposal
    approved_action=fixtures.CalendarActionTests.approved_action
    setup_events=management_fixtures.CalendarManagementTests.setup_events

    def provider_for(self, model):
        client=httpx.Client(transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError('No network'))))
        self.addCleanup(client.close)
        provider=OrchestrateProvider(CONFIG,client)
        provider.transport.verify=model
        return provider

    def agent(self,user=101,message=10):
        return CalendarAgent(user,message,SchedulingTools(user,self.planner,self.actions))

    def service(self,provider):
        return ConversationService(provider,clock=lambda:fixtures.NOW.timestamp(),agent_factory=lambda u,m,**kw:CalendarAgent(u,m,SchedulingTools(u,self.planner,self.actions),**kw))

    def range(self,count=3,**patches):
        return {'start':'2026-10-19T00:00:00+08:00','end':'2026-10-26T00:00:00+08:00',
            'timezone':'Asia/Singapore','sessions':count,'temporary_preferences':patches}

    def draft_step(self,payload):
        source=json.loads(next(m['content'] for m in reversed(payload['conversation']) if m['role']=='tool'))
        return action('propose_plan',{'subject':'Python','sessions':source['range']['sessions'],
            'selections':source['slot_references'][:source['range']['sessions']]})

    def plan(self,user=101,count=3,message=10,**patches):
        model=JSONModel(steps=[action('find_study_periods',self.range(count,**patches)),self.draft_step])
        agent=self.agent(user,message)
        reply=agent.complete(self.provider_for(model),[{'role':'system','content':'Study assistant.'},{'role':'user','content':'Schedule Python next week.'}])
        result=next(r for r in agent.references() if r.get('review_kind')=='plan')
        return result,model,reply

    def present_plan(self,user,result):
        service=CalendarConversation(self.actions)
        review,_=service.prepare(user,result['proposal_id']);service.presented(user,review['id'])
        return service,review

    def mark_presented(self,user,message):
        with closing(database.get_connection()) as db,db:
            db.execute('UPDATE conversation_tool_effects SET presented=1 WHERE telegram_user_id=? AND message_id=?',(user,message))

    def test_single_and_multiple_plans_current_overrides_and_exact_approval(self):
        for user,count in [(101,1),(202,3)]:
            before=profiles.get_profile(user)['preferences']
            result,model,reply=self.plan(user,count,study_days=['thu','sat','sun'],study_windows=[{'start':'13:00','end':'18:00'}],session_minutes=20)
            self.assertEqual(len(model.requests),3)
            self.assertEqual(len(result['sessions']),count)
            self.assertTrue(all(datetime.fromisoformat(s['start']).astimezone(ZoneInfo('Asia/Singapore')).hour==13 for s in result['sessions']))
            self.assertEqual(profiles.get_profile(user)['preferences'],before)
            self.assertEqual(len(self.provider.inserts),0 if user==101 else 1)
            service,review=self.present_plan(user,result)
            response=apply_decision(user,20,'That looks good. Approve all '+str(count),service)
            self.assertEqual(response['status'],'completed')
            self.assertEqual(apply_decision(user,20,'That looks good. Approve all '+str(count),service),response)
        self.assertEqual(len(self.provider.inserts),4)
        self.assertEqual([o for o,_ in self.provider.inserts].count('synthetic-access-b'),3)

    def test_selected_pending_amendment_preserves_other_sessions_invalidates_review(self):
        old,_,_=self.plan();service,review=self.present_plan(101,old)
        snapshot=PendingProposals(self.agent().tools).snapshot()
        model=JSONModel('amendment',[action('amend_pending_proposal',{'proposal_id':snapshot['proposal_id'],
            'proposal_digest':snapshot['proposal_digest'],'session_index':2,'local_start':'15:00'})],relative='unspecified')
        agent=self.agent(message=11)
        agent.complete(self.provider_for(model),[{'role':'system','content':'Study'}, {'role':'user','content':'Monday and Wednesday are fine, move Friday to 3 PM.'}])
        new=next(r for r in agent.references() if r.get('review_kind')=='plan')
        self.assertEqual(new['sessions'][:2],old['sessions'][:2])
        self.assertEqual(service.get(101,review['id'])['status'],'blocked')
        self.assertEqual(self.provider.inserts,[])
        self.assertIsNone(explicit_decision('Move Friday to 3 PM and approve all three'))
        self.assertRaises(Exception,apply_decision,101,12,'Approve all three',service)
        self.present_plan(101,new)
        self.assertEqual(apply_decision(101,13,'Approve all three',service)['status'],'completed')

    def test_conflicts_and_disconnected_calendar_never_fall_back_or_write(self):
        self.provider.freebusy=lambda value,start,end:[{'start':'2026-10-19T00:00:00+08:00','end':'2026-10-26T00:00:00+08:00'}]
        model=JSONModel(steps=[action('find_study_periods',self.range())])
        reply=self.service(self.provider_for(model)).respond(101,1,'Schedule next week')
        self.assertIn('not enough',reply['text']);self.assertEqual(len(model.requests),2)
        self.calendar.disconnect(202)
        model=JSONModel()
        service=self.service(self.provider_for(model))
        reply=service.respond(202,1,'Schedule next week')
        self.assertIn('After connecting',reply['text']);self.assertEqual(len(model.requests),1)
        self.assertNotIn('synthetic-access-a',json.dumps(model.requests))
        self.assertEqual(self.provider.inserts,[])
        self.connect(202,'b',True);self.provider.freebusy=lambda *args:[]
        resumed=JSONModel(steps=[action('find_study_periods',self.range(1)),self.draft_step])
        self.service(self.provider_for(resumed)).respond(202,2,'Continue with that study plan')
        self.assertIn('Schedule next week',json.dumps(resumed.requests[0]))
        self.assertEqual(len(self.planner.proposals(202)),1)

    def change(self,kind='cancel',message=30,index=1):
        lookup=action('get_study_sessions',{'position':index+1})
        def propose(payload):
            data=json.loads(payload['conversation'][-1]['content'])['sessions'][0]
            args={'action_id':data['action_id'],'session_index':data['session_index'],'kind':kind}
            if kind=='reschedule':
                start=fixtures.NOW+timedelta(days=3,hours=11)
                args.update(start=start.isoformat(),end=(start+timedelta(minutes=30)).isoformat())
            return action('propose_session_change',args)
        model=JSONModel('calendar',[lookup,propose],relative='unspecified')
        agent=self.agent(message=message)
        agent.complete(self.provider_for(model),[{'role':'system','content':'Study'}, {'role':'user','content':'Change only the second session.'}])
        result=next(r for r in agent.references() if r.get('review_kind')=='change')
        return result

    def test_existing_move_and_cancel_require_exact_presented_human_approval(self):
        self.setup_events()
        moved=self.change('reschedule',30)
        self.assertEqual(self.moves,[])
        self.assertRaises(Exception,apply_decision,101,31,'Approve this move',change_service=self.management)
        self.mark_presented(101,30)
        self.assertEqual(apply_decision(101,31,'Approve this move',change_service=self.management)['status'],'blocked')
        result=apply_decision(101,32,'Approve this move',change_service=self.management)
        self.assertEqual(result['status'],'completed');self.assertEqual(len(self.moves),1)
        self.assertEqual(apply_decision(101,32,'Approve this move',change_service=self.management),result)
        self.change('cancel',33,index=2);self.mark_presented(101,33)
        self.assertRaises(Exception,apply_decision,202,34,'Approve this cancellation',change_service=self.management)
        self.assertEqual(apply_decision(101,34,'Approve this cancellation',change_service=self.management)['status'],'completed')
        self.assertEqual(len(self.deletes),1)

    def test_reject_expire_stale_and_ambiguous_change_approvals_fail_closed(self):
        self.setup_events();change=self.change();self.mark_presented(101,30)
        self.assertEqual(apply_decision(101,31,"Actually, don't change anything",change_service=self.management)['status'],'rejected')
        self.assertEqual(self.deletes,[])
        self.change(message=32);self.mark_presented(101,32)
        self.management.clock=lambda:fixtures.NOW+timedelta(minutes=11)
        self.assertRaises(Exception,apply_decision,101,33,'Approve this cancellation',change_service=self.management)
        self.assertEqual(self.deletes,[])
        self.assertIsNone(explicit_decision('Looks good but move it to Thursday and approve this change'))

    def test_schema_unknown_actions_identity_duplicate_json_and_forced_choice(self):
        from services.llm_contracts import ToolRegistry
        from services.study_tools import EmptyInput
        registry=ToolRegistry();registry.register('connection_status','Read',EmptyInput)
        schemas,forced=definitions(registry.definitions(),None)
        for value in [action('execute_approved_calendar_action',{}), action('connection_status',{'user_id':202}),
                      {'content':'created events','tool_calls':action('connection_status',{})['tool_calls']}]:
            self.assertRaises(LLMError,parse_actions,json.dumps(value),schemas)
        self.assertRaises(LLMError,parse_actions,'{"content":"a","content":"b","tool_calls":[]}',schemas)
        self.assertRaises(LLMError,parse_actions,'```json\n{}\n```',schemas)
        self.assertRaises(LLMError,parse_actions,json.dumps({'content':'Hello','tool_calls':[]}),schemas,'connection_status')
        self.assertEqual(self.provider.inserts,[])

    def test_permanent_preferences_only_prepare_confirmation_and_hide_notes(self):
        before=profiles.get_profile(101)['preferences']
        model=JSONModel('preferences',[action('study_preferences',{}),action('propose_preferences',{'session_minutes':45})],relative='unspecified')
        agent=self.agent()
        agent.complete(self.provider_for(model),[{'role':'system','content':'Study'}, {'role':'user','content':'Save 45-minute sessions from now on.'}])
        self.assertFalse(next(r for r in agent.references() if r.get('review_kind')=='preferences')['saved'])
        self.assertEqual(profiles.get_profile(101)['preferences'],before)
        for request in model.requests:
            for message in request['conversation']:
                if message['role']=='tool':self.assertNotIn('"notes"',message['content'])

    def test_two_concurrent_accounts_different_preferences_isolated(self):
        profiles.save_preferences(101,StudyPreferences(session_minutes=20,study_windows=[{'start':'09:00','end':'11:00'}]))
        profiles.save_preferences(202,StudyPreferences(session_minutes=45,study_windows=[{'start':'18:00','end':'20:00'}]))
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(self.plan,101,1);b=pool.submit(self.plan,202,1)
            first=a.result()[0];second=b.result()[0]
        self.assertEqual(datetime.fromisoformat(first['sessions'][0]['end'])-datetime.fromisoformat(first['sessions'][0]['start']),timedelta(minutes=20))
        self.assertEqual(datetime.fromisoformat(second['sessions'][0]['end'])-datetime.fromisoformat(second['sessions'][0]['start']),timedelta(minutes=45))
        self.assertEqual(self.provider.inserts,[])

    def test_failure_and_repeated_tools_are_bounded_no_execution(self):
        def error(prompt):raise TimeoutError('synthetic private token')
        service=self.service(self.provider_for(error))
        with self.assertRaises(LLMError) as failed:service.respond(101,1,'Schedule sessions')
        self.assertEqual(failed.exception.code,'inference_uncertain')
        self.assertRaises(LLMError,service.respond,101,1,'Schedule sessions')
        model=JSONModel('calendar',[action('connection_status',{})]*5,relative='unspecified')
        self.assertRaises(LLMError,self.agent().complete,self.provider_for(model),[{'role':'system','content':'Study'},{'role':'user','content':'Read my status'}])
        self.assertLessEqual(len(model.requests),6)
        self.assertEqual(self.provider.inserts,[])


    def test_format_repair_once_and_invalid_actions_never_repaired(self):
        from services.llm_contracts import ToolRegistry
        from services.study_tools import EmptyInput
        registry=ToolRegistry();registry.register('connection_status','Read',EmptyInput)
        tools=registry.definitions();choice={'type':'function','function':{'name':'connection_status'}}
        messages=[{'role':'system','content':'Study'},{'role':'user','content':'Check connection'}]
        for bad in [action('unknown',{}),action('connection_status',{'user_id':202}),
                    action('connection_status',{'account_id':'fake'}),
                    {**action('unknown',{}),'extra':'malformed envelope'},
                    {**action('connection_status',{'user_id':202}),'extra':'malformed envelope'}]:
            provider=self.provider_for(JSONModel())
            provider._infer=__import__('unittest.mock',fromlist=['Mock']).Mock(return_value=json.dumps(bad))
            self.assertRaises(LLMError,provider.complete,messages,tools=tools,tool_choice=choice)
            self.assertEqual(provider._infer.call_count,1)
        provider=self.provider_for(JSONModel())
        from unittest.mock import Mock
        provider._infer=Mock(side_effect=['Not JSON',json.dumps(action('connection_status',{}))])
        self.assertEqual(provider.complete(messages,tools=tools,tool_choice=choice).tool_calls[0].name,'connection_status')
        self.assertEqual(provider._infer.call_count,2)
        provider._infer=Mock(return_value='Still not JSON')
        self.assertRaises(LLMError,provider.complete,messages,tools=tools,tool_choice=choice)
        self.assertEqual(provider._infer.call_count,2)

    def test_false_write_success_replaced_by_confirmed_read(self):
        self.setup_events()
        model=JSONModel('calendar',[action('get_study_sessions',{'position':2}),
            {'content':'I deleted your second session.','tool_calls':[]}],relative='unspecified')
        reply=self.agent().complete(self.provider_for(model),[{'role':'system','content':'Study'},{'role':'user','content':'Show my second session'}])
        self.assertIn('matching study sessions',reply.content)
        self.assertNotIn('deleted',reply.content)
        self.assertEqual(self.deletes,[])

    def test_new_change_supersedes_old_and_receipts_cannot_rebind(self):
        self.setup_events();old=self.change(message=30);self.mark_presented(101,30)
        new=self.change(message=31);self.mark_presented(101,31)
        self.assertEqual(self.management.get(101,old['change_id'])['status'],'rejected')
        self.assertEqual(self.management.decide(101,old['change_id'],'approve')['status'],'rejected')
        self.assertRaises(Exception,apply_decision,101,32,'Approve this cancellation',change_service=self.management,reply_review=old['change_id'])
        self.assertEqual(apply_decision(101,32,'Approve this cancellation',change_service=self.management)['status'],'blocked')
        self.assertEqual(self.deletes,[])
        self.assertEqual(apply_decision(101,33,'Approve this cancellation',change_service=self.management)['status'],'completed')
        self.assertEqual(len(self.deletes),1)

    def test_generic_rejection_punctuation_rejects_latest_plan(self):
        plan,_,_=self.plan();service,_=self.present_plan(101,plan)
        result=apply_decision(101,12,"Actually, don't change anything!",service,change_service=self.agent().tools.management())
        self.assertEqual(result['status'],'rejected');self.assertEqual(self.provider.inserts,[])

    def test_disconnected_connection_shows_authenticated_settings_button(self):
        from services.conversation_telegram import present_tools
        from deployment import Deployment
        self.calendar.disconnect(202)
        agent=self.agent(202);agent.invoke(__import__('services.llm_contracts',fromlist=['ToolCall']).ToolCall(id='status',name='connection_status',arguments={}),0)
        message=SimpleNamespace(reply_text=AsyncMock())
        with patch('deployment.load_deployment',return_value=Deployment('https://synthetic.example')):
            asyncio.run(present_tools(message,202,10))
        self.assertEqual(message.reply_text.await_count,1)
        self.assertEqual(message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard[0][0].web_app.url,'https://synthetic.example/setup')



    def test_latest_change_prevents_conversational_approval_of_older_plan(self):
        self.setup_events();plan,_,_=self.plan(message=40);service,_=self.present_plan(101,plan)
        self.change(message=41);self.mark_presented(101,41)
        count=len(self.provider.inserts)
        self.assertRaises(Exception,apply_decision,101,42,'Approve all three',service)
        self.assertEqual(len(self.provider.inserts),count);self.assertEqual(self.deletes,[])

    def test_human_change_decision_bypasses_model(self):
        from services.conversation_telegram import handle_text
        from services.conversations import ConversationSettings
        self.setup_events();self.change(message=30);self.mark_presented(101,30)
        message=SimpleNamespace(text='Approve this cancellation',message_id=31,reply_text=AsyncMock(),reply_to_message=None)
        update=SimpleNamespace(message=message,effective_user=SimpleNamespace(id=101),effective_chat=SimpleNamespace(id=101,type='private'))
        with patch('services.conversation_telegram.load_settings',return_value=ConversationSettings(True)), \
             patch('services.conversation_telegram.configured_testers',return_value={101,202}), \
             patch('services.conversation_telegram.dispatcher',side_effect=AssertionError('No inference on approval')), \
             patch('services.calendar_change_decisions.CalendarSessionManagement',return_value=self.management):
            asyncio.run(handle_text(update,SimpleNamespace(bot_data={})))
        self.assertEqual(len(self.deletes),1)
        self.assertIn('confirmed',message.reply_text.call_args.args[0])

    def test_failed_review_delivery_does_not_allow_text_approval(self):
        from services.conversation_telegram import present_tools
        self.setup_events();self.change(message=30)
        message=SimpleNamespace(reply_text=AsyncMock(side_effect=RuntimeError('synthetic delivery failure')))
        with patch('services.calendar_telegram.show_change',side_effect=RuntimeError('synthetic delivery failure')):
            self.assertRaises(RuntimeError,asyncio.run,present_tools(message,101,30))
        self.assertRaises(Exception,apply_decision,101,31,'Approve this cancellation',change_service=self.management)
        self.assertEqual(self.deletes,[])



    def test_reported_two_week_requests_ask_frequency_without_repeated_tools(self):
        for user,text in [(101,'Can you help me set up study sessions for the next 2 weeks? For studying machine learning'),
                          (202,'Hello can you help me set up study sessions for the next two weeks? On the topic of machine learning.')]:
            model=JSONModel(relative='next_two_weeks')
            reply=self.service(self.provider_for(model)).respond(user,1,text)
            self.assertIn('How many study sessions per week',reply['text'])
            self.assertEqual(len(model.requests),1)
        self.assertEqual(self.provider.reads,[]);self.assertEqual(self.provider.inserts,[])

    def two_week_plan(self,user=101,weekly=3):
        model=JSONModel(relative='next_two_weeks',weekly=weekly,steps=[
            action('find_study_periods',{**self.range(weekly),'end':'2026-10-25T23:59:59+08:00'}),self.draft_step])
        agent=self.agent(user)
        agent.complete(self.provider_for(model),[{'role':'system','content':'Study'},
            {'role':'user','content':'Plan machine learning for the next two weeks.'},
            {'role':'assistant','content':'How many sessions per week?'}, {'role':'user','content':str(weekly)+' per week'}])
        plan=next(r for r in agent.references() if r.get('review_kind')=='plan')
        return plan,model

    def test_two_week_followup_has_exact_boundaries_and_balanced_slots(self):
        plan,model=self.two_week_plan()
        self.assertEqual(len(model.requests),3)
        self.assertEqual(len(plan['sessions']),6)
        dates=[datetime.fromisoformat(item['start']).astimezone(ZoneInfo('Asia/Singapore')).date() for item in plan['sessions']]
        self.assertEqual(sum(day<datetime(2026,10,26).date() for day in dates),3)
        self.assertEqual(sum(day>=datetime(2026,10,26).date() for day in dates),3)
        self.assertEqual(self.provider.inserts,[])

    def test_two_accounts_plan_two_weeks_concurrently_with_own_defaults(self):
        profiles.save_preferences(101,StudyPreferences(session_minutes=20,study_windows=[{'start':'09:00','end':'11:00'}]))
        profiles.save_preferences(202,StudyPreferences(session_minutes=45,study_windows=[{'start':'18:00','end':'20:00'}]))
        with ThreadPoolExecutor(max_workers=2) as pool:
            one=pool.submit(self.two_week_plan,101,2);two=pool.submit(self.two_week_plan,202,2)
            a=one.result()[0];b=two.result()[0]
        self.assertEqual(len(a['sessions']),4);self.assertEqual(len(b['sessions']),4)
        self.assertEqual(datetime.fromisoformat(a['sessions'][0]['start']).astimezone(ZoneInfo('Asia/Singapore')).hour,9)
        self.assertEqual(datetime.fromisoformat(b['sessions'][0]['start']).astimezone(ZoneInfo('Asia/Singapore')).hour,18)
        self.assertEqual(profiles.get_profile(101)['preferences']['session_minutes'],20)
        self.assertEqual(self.provider.inserts,[])

    def test_nonintegral_custom_range_fails_usefully_without_tool_loop(self):
        model=JSONModel(relative='unspecified',steps=[action('find_study_periods',
            {**self.range(),'end':'2026-11-01T23:59:59+08:00'}),self.draft_step])
        reply=self.agent().complete(self.provider_for(model),[{'role':'system','content':'Study'},{'role':'user','content':'Prepare a custom plan'}])
        self.assertIn('exact local-day range boundaries',reply.content)
        self.assertEqual(len(model.requests),3);self.assertEqual(self.provider.inserts,[])

    def test_repeated_successful_action_is_not_executed_again(self):
        model=JSONModel('calendar',[action('connection_status',{})]*5,relative='unspecified')
        agent=self.agent()
        with self.assertRaises(LLMError) as error:
            agent.complete(self.provider_for(model),[{'role':'system','content':'Study'},{'role':'user','content':'Check connection'}])
        self.assertEqual(error.exception.code,'tool_loop');self.assertEqual(len(model.requests),3)
        self.assertEqual(len(agent.references()),1)

    def test_missing_subject_returns_specific_question_without_tools(self):
        model=JSONModel(relative='next_two_weeks',weekly=2,clarification='Which subject should these sessions cover?')
        reply=self.agent().complete(self.provider_for(model),[{'role':'system','content':'Study'},{'role':'user','content':'Plan two sessions a week for two weeks'}])
        self.assertIn('Which subject',reply.content);self.assertEqual(len(model.requests),1)
        self.assertEqual(self.provider.reads,[])

    def telegram_request(self,model,text,side_effect=None):
        from services.conversation_telegram import handle_text
        from services.conversations import ConversationDispatcher,ConversationSettings
        from telegram.error import NetworkError
        service=self.service(self.provider_for(model))
        message=SimpleNamespace(text=text,message_id=901,reply_text=AsyncMock(side_effect=side_effect))
        update=SimpleNamespace(message=message,effective_user=SimpleNamespace(id=101),effective_chat=SimpleNamespace(id=101,type='private'))
        with patch('services.conversation_telegram.load_settings',return_value=ConversationSettings(True)), \
             patch('services.conversation_telegram.configured_testers',return_value={101,202}), \
             patch('services.conversation_telegram.asyncio.sleep',new_callable=AsyncMock):
            asyncio.run(handle_text(update,SimpleNamespace(bot_data={'conversation_dispatcher':ConversationDispatcher(service)})))
        return message,service

    def test_reported_greeting_delivery_failure_retries_without_new_inference(self):
        from telegram.error import NetworkError
        model=JSONModel('chat')
        message,service=self.telegram_request(model,'Hi',[NetworkError('synthetic delivery'),SimpleNamespace(message_id=902)])
        self.assertEqual(message.reply_text.await_count,2);self.assertEqual(len(model.requests),1)
        self.assertTrue(service.respond(101,901,'Hi')['delivered'])

    def test_reported_invalid_action_error_delivery_retries_without_model_replay(self):
        from telegram.error import NetworkError
        model=JSONModel(steps=[action('find_study_periods',{'user_id':202})])
        message,_=self.telegram_request(model,'Plan machine learning',[NetworkError('synthetic delivery'),None])
        self.assertEqual(message.reply_text.await_count,2);self.assertEqual(len(model.requests),2)
        self.assertIn('invalid tool request',message.reply_text.call_args.args[0]);self.assertEqual(self.provider.inserts,[])

    def test_action_limit_exhaustion_still_delivers_clear_error(self):
        steps=[action('connection_status',{}),action('study_preferences',{}),action('get_proposals',{}),
               action('get_study_sessions',{'position':1}),action('get_study_sessions',{'position':2})]
        model=JSONModel('calendar',steps,relative='unspecified')
        message,_=self.telegram_request(model,'Show Calendar information')
        self.assertIn('safe tool-call limit',message.reply_text.call_args.args[0]);self.assertEqual(len(model.requests),6)
        self.assertEqual(self.provider.inserts,[])

    def test_deadline_fences_late_worker_and_other_user_can_finish(self):
        import threading
        from services.conversations import ConversationDispatcher
        from services.llm_contracts import ChatReply
        release=threading.Event();entered=threading.Event()
        class SlowProvider:
            prevent_uncertain_replay=True
            def complete(self,messages):
                if messages[-1]['content']=='slow':entered.set();release.wait(2)
                return ChatReply('Synthetic reply')
        service=ConversationService(SlowProvider(),clock=lambda:fixtures.NOW.timestamp())
        queue=ConversationDispatcher(service,timeout=0.1)
        slow_send=AsyncMock();fast_send=AsyncMock(return_value=SimpleNamespace(message_id=2))
        async def exercise():
            try:
                results=await asyncio.gather(queue.deliver(101,1,'slow',service.generation(101),slow_send),
                    queue.deliver(202,1,'fast',service.generation(202),fast_send),return_exceptions=True)
                self.assertIsInstance(results[0],LLMError);self.assertEqual(results[0].code,'request_timeout')
                self.assertIsNone(results[1]);fast_send.assert_awaited_once();slow_send.assert_not_awaited()
            finally:release.set()
        asyncio.run(exercise())
        self.assertTrue(entered.is_set());self.assertEqual(service.history(101),[])
        with self.assertRaises(LLMError) as error:service.respond(101,1,'slow')
        self.assertEqual(error.exception.code,'inference_uncertain')
        self.assertEqual(service.respond(101,2,'fast')['status'],'completed')

    def test_persistent_delivery_failure_is_bounded_and_reply_retained(self):
        from telegram.error import NetworkError
        model=JSONModel('chat')
        message,service=self.telegram_request(model,'Hi',NetworkError('synthetic outage'))
        self.assertEqual(message.reply_text.await_count,3);self.assertEqual(len(model.requests),1)
        self.assertFalse(service.respond(101,901,'Hi')['delivered']);self.assertEqual(len(service.history(101)),1)



    def test_request_deadline_error_reaches_telegram(self):
        from services.conversations import ConversationDispatcher
        model=JSONModel('chat')
        with patch.object(ConversationDispatcher,'deliver',new_callable=AsyncMock,side_effect=LLMError('request_timeout')):
            message,_=self.telegram_request(model,'Plan sessions')
        self.assertIn('took too long',message.reply_text.call_args.args[0]);self.assertEqual(model.requests,[])

    def test_timed_out_calendar_worker_cannot_run_late_availability_or_proposal(self):
        import threading
        from services.conversations import ConversationDispatcher
        release=threading.Event();entered=threading.Event()
        model=JSONModel(steps=[action('find_study_periods',self.range()),self.draft_step])
        def blocked(prompt):
            if len(model.requests)==1:entered.set();release.wait(2)
            return model(prompt)
        service=self.service(self.provider_for(blocked));queue=ConversationDispatcher(service,timeout=0.1)
        async def exercise():
            try:
                with self.assertRaises(LLMError) as error:
                    await queue.deliver(101,77,'Schedule Python next week',service.generation(101),AsyncMock())
                self.assertEqual(error.exception.code,'request_timeout')
            finally:release.set()
        asyncio.run(exercise())
        self.assertTrue(entered.is_set());self.assertEqual(self.planner.proposals(101),[])
        self.assertEqual(self.provider.reads,[]);self.assertEqual(self.provider.inserts,[])


if __name__=='__main__':unittest.main()
