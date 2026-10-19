"""Synthetic two-user orchestration; all Google and IBM operations mocked."""
import asyncio
from contextlib import closing
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import unittest

import test_calendar_actions as fixtures
from services.calendar_agent import CalendarAgent
from services.study_tools import SchedulingTools
from services.calendar_conversation import CalendarConversation
from services.conversations import ConversationService
from services.conversation_telegram import preference_confirmation, present_tools
from services.llm_contracts import ChatReply,ToolCall,LLMError
from services.watsonx import validate_messages
from services import profiles
import database
import test_calendar_conversation as management_fixtures


class ScriptedIBM:
    def __init__(self,replies,intent='calendar',routing_reply='',relative_range='unspecified'):
        self.replies=list(replies);self.requests=[];self.intent=intent;self.routing_reply=routing_reply;self.relative_range=relative_range
    def complete(self,messages,**kwargs):
        validate_messages(messages,kwargs.get('tools'))
        self.requests.append(json.loads(json.dumps(messages)))
        choice=kwargs.get('tool_choice',{})
        if isinstance(choice,dict) and choice.get('function',{}).get('name')=='route_request':
            return ChatReply('',(ToolCall(id='route',name='route_request',arguments={'intent':self.intent,'reply':self.routing_reply,'relative_range':self.relative_range}),))
        return self.replies.pop(0)


class AgentTests(unittest.TestCase):
    setUp=fixtures.CalendarActionTests.setUp
    connect=fixtures.CalendarActionTests.connect

    def agent(self,user=101,message=10,**kwargs):
        return CalendarAgent(user,message,SchedulingTools(user,self.planner,self.actions),**kwargs)

    def call(self,name,args=None):return ToolCall(id='call',name=name,arguments=args or {})

    def range(self,count=3):
        return {'start':(fixtures.NOW+timedelta(days=1)).isoformat(),
                'end':(fixtures.NOW+timedelta(days=8)).isoformat(),'timezone':'Asia/Singapore','sessions':count}

    def plan(self,agent,count=3):
        args=self.range(count)
        found=agent.invoke(self.call('find_study_periods',args),0)
        args.update(subject='Python revision',proposed_sessions=[{**p,'title':'Python loops'} for p in found['periods']])
        return args

    def approve(self,user,result):
        service=CalendarConversation(self.actions)
        review,data=service.prepare(user,result['proposal_id']);service.presented(user,review['id'])
        return service,review

    def test_two_user_batch_tool_proposal_then_exact_approval(self):
        for user in (101,202):
            agent=self.agent(user)
            args=self.plan(agent)
            result=agent.invoke(self.call('propose_plan',args),1)
            self.assertFalse(result['approved'])
            self.assertEqual(len(self.provider.inserts),0 if user==101 else 3)
            service,review=self.approve(user,result)
            self.assertRaises(LookupError,service.decide,202 if user==101 else 101,review['id'],'approve')
            self.assertEqual(service.decide(user,review['id'],'approve')['status'],'completed')
            service.decide(user,review['id'],'approve')
        self.assertEqual(len(self.provider.inserts),6)
        self.assertEqual([owner for owner,_ in self.provider.inserts].count('synthetic-access-a'),3)
        self.assertEqual([owner for owner,_ in self.provider.inserts].count('synthetic-access-b'),3)

    def test_ordinary_native_loop_and_follow_up_persist_without_start(self):
        provider=ScriptedIBM([ChatReply('',(self.call('study_preferences'),)),ChatReply('Your saved session duration is 30 minutes.'),ChatReply('We can practise loops.')])
        service=ConversationService(provider,clock=lambda:fixtures.NOW.timestamp(),agent_factory=lambda u,m,**kw:self.agent(u,m,**kw))
        result=service.respond(101,1,'What are my study preferences?',display_name='Synthetic learner')
        self.assertIn('30',result['text'])
        self.assertIn('Synthetic learner',provider.requests[0][0]['content'])
        self.assertEqual(provider.requests[2][-1]['role'],'tool')
        restarted=ConversationService(provider,agent_factory=lambda u,m,**kw:self.agent(u,m,**kw))
        restarted.respond(101,2,'Explain loops')
        self.assertIn('saved session duration',json.dumps(provider.requests[-1]))
        service.respond(101,1,'What are my study preferences?')
        self.assertEqual(len(provider.requests),5)
        self.assertEqual(self.provider.inserts,[])

    def test_retry_same_effect_returns_persisted_proposal_and_changed_retry_fails(self):
        agent=self.agent();args=self.plan(agent)
        result=agent.invoke(self.call('propose_plan',args),1)
        restarted=self.agent()
        self.assertEqual(restarted.invoke(self.call('propose_plan',args),1),result)
        self.assertEqual(len(self.planner.proposals(101)),1)
        args['subject']='Different'
        self.assertRaises(LLMError,restarted.invoke,self.call('propose_plan',args),1)

    def test_unknown_executor_impersonation_and_extra_fields_fail_closed(self):
        agent=self.agent()
        for name,args in [('execute_approved_calendar_action',{}),('study_preferences',{'telegram_user_id':202}),('calendar_delete',{})]:
            self.assertRaises(LLMError,agent.invoke,self.call(name,args),0)
        names=[d['function']['name'] for d in agent.registry.definitions()]
        self.assertFalse(any('execute' in n or 'approve' in n for n in names))
        self.assertEqual(self.provider.inserts,[])

    def test_wrong_count_past_weekday_and_timezone_not_submitted(self):
        for number,change in enumerate(({'sessions':2},{'start':(fixtures.NOW-timedelta(days=1)).isoformat()},
                       {'expected_weekdays':['tue']*3},{'timezone':'UTC'}, {'weekly_counts':[2]})):
            agent=self.agent(message=20+number)
            args=self.plan(agent);args.update(change)
            result=agent.invoke(self.call('propose_plan',args),1)
            self.assertIn('error',result)
        self.assertEqual(self.planner.proposals(101),[])

    def test_busy_and_excluded_days_are_checked_by_existing_validator(self):
        agent=self.agent();args=self.plan(agent)
        args['excluded_days']=['tue','wed','thu','fri','sat','sun','mon']
        self.assertIn('error',agent.invoke(self.call('propose_plan',args),1))
        self.assertEqual(self.provider.inserts,[])

    def test_disconnected_availability_is_not_fabricated(self):
        self.calendar.disconnect(202)
        agent=self.agent(202)
        self.assertEqual(agent.invoke(self.call('connection_status'),0)['status'],'disconnected')
        self.assertIn('error',agent.invoke(self.call('find_study_periods',self.range()),1))

    def test_tool_loop_limit_and_clear_fence(self):
        provider=ScriptedIBM([ChatReply('',(ToolCall(id=str(n),name='study_preferences',arguments={}),)) for n in range(5)])
        self.assertRaises(LLMError,self.agent().complete,provider,[{'role':'system','content':'Study'},{'role':'user','content':'Read'}])
        self.assertEqual(len(provider.requests),3)
        self.assertRaises(LLMError,self.agent(fence=lambda:False).invoke,self.call('study_preferences'),8)

    def test_preference_change_requires_user_confirmation_and_repeated_click_noop(self):
        agent=self.agent();before=profiles.get_profile(101)['preferences']
        result=agent.invoke(self.call('propose_preferences',{'study_days':['mon','wed'],'session_minutes':45}),0)
        self.assertFalse(result['saved']);self.assertEqual(profiles.get_profile(101)['preferences'],before)
        message=SimpleNamespace(reply_text=AsyncMock())
        asyncio.run(present_tools(message,101,10))
        def update(user):return SimpleNamespace(effective_user=SimpleNamespace(id=user),effective_chat=SimpleNamespace(id=user,type='private'),
            callback_query=SimpleNamespace(data='ap:approve:10:0',answer=AsyncMock(),message=message))
        from services.conversations import ConversationSettings
        with patch('services.conversation_telegram.load_settings',return_value=ConversationSettings(True,'onboarded_users')),patch('services.conversation_telegram.configured_testers',return_value=set()):
            asyncio.run(preference_confirmation(update(202),None))
            self.assertEqual(profiles.get_profile(101)['preferences'],before)
            asyncio.run(preference_confirmation(update(101),None))
            asyncio.run(preference_confirmation(update(101),None))
        saved=profiles.get_profile(101)['preferences']
        self.assertEqual(saved['session_minutes'],45);self.assertEqual(saved['practice_format'],before['practice_format'])
        self.assertEqual(profiles.get_profile(202)['preferences']['session_minutes'],30)

    def test_clear_invalidates_preference_keyboard_and_scrubs_tool_content(self):
        self.agent().invoke(self.call('propose_preferences',{'session_minutes':45}),0)
        ConversationService(None).clear(101,11)
        with closing(database.get_connection()) as db:
            row=db.execute('SELECT result_json,decided FROM conversation_tool_effects WHERE telegram_user_id=101').fetchone()
            self.assertIsNone(row[0]);self.assertEqual(row[1],1)

    def test_additive_migration_preserves_existing_data(self):
        import sqlite3
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript(database.SCHEMA.replace(database.CALENDAR_AGENT_SCHEMA,''))
            db.execute("INSERT INTO users (telegram_user_id,preferences_json) VALUES (?,?)",(101,'{"notes":"Synthetic preserved"}'))
            db.commit()
            before=db.execute('SELECT * FROM users').fetchall()
            schema=db.execute("SELECT name,sql FROM sqlite_master").fetchall()
            db.executescript(database.CALENDAR_AGENT_SCHEMA)
            db.executescript(database.CALENDAR_AGENT_SCHEMA)
            self.assertEqual(before,db.execute('SELECT * FROM users').fetchall())
            after=dict(db.execute("SELECT name,sql FROM sqlite_master"))
            for name,sql in schema:self.assertEqual(after[name],sql)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_native_message_pairing_rejects_fabricated_or_missing_results(self):
        tools=self.agent().registry.definitions()
        self.assertRaises(LLMError,validate_messages,[{'role':'tool','content':'made up','tool_call_id':'unknown'}],tools)
        call={'role':'assistant','content':'','tool_calls':[{'id':'a','type':'function','function':{'name':'study_preferences','arguments':'{}'}}]}
        self.assertRaises(LLMError,validate_messages,[call],tools)
        validate_messages([call,{'role':'tool','content':'{}','tool_call_id':'a'}],tools)

    def test_compact_selected_slots_are_server_bound_and_exact(self):
        agent=self.agent()
        slots=agent.invoke(self.call('find_study_periods',self.range()),0)
        args={**self.range(),'subject':'Python','weekly_counts':[3],
              'selected_slots':[{'source_ordinal':0,'slot_index':i,'title':'Study loops'} for i in range(3)]}
        result=agent.invoke(self.call('propose_plan',args),1)
        self.assertEqual([s['start'].replace('Z','+00:00') for s in result['sessions']],[s['start'] for s in slots['periods']])
        foreign=self.agent(202)
        self.assertIn('error',foreign.invoke(self.call('propose_plan',args),1))
        self.assertEqual(self.provider.inserts,[])

    def test_tool_plan_rejection_and_expiration_never_write(self):
        agent=self.agent();result=agent.invoke(self.call('propose_plan',self.plan(agent,1)),1)
        service,review=self.approve(101,result)
        self.assertEqual(service.decide(101,review['id'],'reject')['status'],'rejected')
        second=self.agent(message=11);result=second.invoke(self.call('propose_plan',self.plan(second,1)),1)
        service,review=self.approve(101,result)
        service.clock=lambda:fixtures.NOW+timedelta(minutes=11)
        self.assertRaises(ValueError,service.decide,101,review['id'],'approve')
        self.assertEqual(self.provider.inserts,[])

    def test_session_tools_reuse_selected_event_management_and_preserve_others(self):
        self.approved_action=fixtures.CalendarActionTests.approved_action.__get__(self)
        self.proposal=fixtures.CalendarActionTests.proposal.__get__(self)
        proposal,action=management_fixtures.CalendarManagementTests.setup_events(self)
        agent=self.agent()
        listed=agent.invoke(self.call('get_study_sessions'),0)['sessions']
        self.assertEqual(len(listed),3)
        before=json.loads(json.dumps(list(self.provider.events.values())))
        start=fixtures.NOW+timedelta(days=3,hours=11)
        result=agent.invoke(self.call('propose_session_change',{'action_id':action['id'],'session_index':0,'kind':'reschedule',
            'start':start.isoformat(),'end':(start+timedelta(minutes=30)).isoformat()}),1)
        self.assertEqual(self.moves,[])
        self.assertEqual(self.management.decide(101,result['change_id'],'approve')['status'],'completed')
        self.assertEqual(len(self.moves),1)
        remaining=[e for e in self.provider.events.values() if e['id']!=self.moves[0][1]]
        self.assertEqual(remaining,[e for e in before if e['id']!=self.moves[0][1]])
        foreign=self.agent(202)
        self.assertIn('error',foreign.invoke(self.call('propose_session_change',{'action_id':action['id'],'session_index':1,'kind':'cancel'}),0))
        result=agent.invoke(self.call('propose_session_change',{'action_id':action['id'],'session_index':1,'kind':'cancel'}),2)
        self.assertEqual(self.deletes,[])
        self.assertEqual(self.management.decide(101,result['change_id'],'approve')['status'],'completed')
        self.management.decide(101,result['change_id'],'approve')
        self.assertEqual(len(self.deletes),1)
        self.assertEqual(len(self.provider.events),2)

    def test_ambiguous_matches_are_reported_and_position_resolves_chronologically(self):
        self.approved_action=fixtures.CalendarActionTests.approved_action.__get__(self)
        self.proposal=fixtures.CalendarActionTests.proposal.__get__(self)
        management_fixtures.CalendarManagementTests.setup_events(self)
        agent=self.agent()
        result=agent.invoke(self.call('get_study_sessions',{'title_contains':'Python'}),0)
        self.assertTrue(result['multiple_matches'])
        second=agent.invoke(self.call('get_study_sessions',{'title_contains':'Python','position':2}),1)
        self.assertFalse(second['multiple_matches'])
        self.assertEqual(second['sessions'],[result['sessions'][1]])
        missing=agent.invoke(self.call('get_study_sessions',{'title_contains':'Not a real subject'}),2)
        self.assertEqual(missing['sessions'],[])
        self.assertEqual(self.moves,[]);self.assertEqual(self.deletes,[])

    def test_native_proposal_finishes_without_extra_inference_or_write(self):
        agent=self.agent();args=self.plan(agent,1)
        # Reserve zero for the existing availability result, then run a one-call request.
        agent=self.agent(message=15)
        provider=ScriptedIBM([ChatReply('I created it!',(self.call('propose_plan',args),))])
        reply=agent.complete(provider,[{'role':'system','content':'Study safely.'},{'role':'user','content':'Propose one Python session.'}])
        self.assertIn('Nothing is approved or changed',reply.content)
        self.assertNotIn('I created it',reply.content)
        self.assertEqual(len(provider.requests),2)
        self.assertEqual(self.provider.inserts,[])

    def test_uncertain_tool_checkpoint_is_not_blindly_replayed(self):
        agent=self.agent()
        with closing(database.get_connection()) as db,db:
            db.execute("INSERT INTO conversation_tool_effects (telegram_user_id,message_id,ordinal,request_hash,status) VALUES (101,10,0,'uncertain','pending')")
        self.assertRaises(LLMError,agent.invoke,self.call('study_preferences'),0)
        self.assertEqual(self.provider.inserts,[])

    def test_consent_upgrade_from_conversational_plan_uses_existing_continuation(self):
        from urllib.parse import parse_qs,urlsplit
        self.connect(202,'b',False)
        agent=self.agent(202);result=agent.invoke(self.call('propose_plan',self.plan(agent,1)),1)
        service,review=self.approve(202,result)
        consent=service.decide(202,review['id'],'approve')
        self.assertEqual(consent['status'],'consent');self.assertEqual(self.provider.inserts,[])
        ticket=parse_qs(urlsplit(consent['authorization_start_url']).query)['ticket'][0]
        url,cookie=self.calendar.start_browser(ticket)
        state=parse_qs(urlsplit(url).query)['state'][0]
        self.calendar.callback(state,cookie,code='b')
        self.assertEqual(service.resume_consent(202,review['id'])['status'],'completed')
        self.assertEqual(self.provider.inserts[0][0],'synthetic-access-b')

    def test_six_week_compact_plan_preserves_three_sessions_each_week(self):
        agent=self.agent();start=fixtures.NOW+timedelta(days=1)
        groups=[]
        for week in range(6):
            first=start+timedelta(days=week*7)
            result=agent.invoke(self.call('find_study_periods',{'start':first.isoformat(),'end':(first+timedelta(days=7)).isoformat(),
                'sessions':3,'timezone':'Asia/Singapore'}),week)
            self.assertEqual(len(result['periods']),3)
            groups.append({'source_ordinal':week,'slot_indices':[0,1,2],'topic':'Python revision'})
        result=agent.invoke(self.call('propose_plan',{'start':start.isoformat(),'end':(start+timedelta(days=42)).isoformat(),
            'sessions':18,'timezone':'Asia/Singapore','subject':'Python','weekly_counts':[3]*6,'slot_groups':groups}),6)
        self.assertEqual(len(result['sessions']),18)
        self.assertEqual(self.provider.inserts,[])
