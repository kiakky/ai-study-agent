"""Regressions for native string arguments, wrong next-week dates and hello."""
from datetime import datetime,timezone,timedelta
from contextlib import closing
import json
import unittest
from unittest.mock import patch

import test_calendar_actions as fixtures
from test_calendar_agent import ScriptedIBM
from services.calendar_agent import CalendarAgent
from services.calendar_tool_inputs import relative_range,normalize_arguments
from services.conversations import ConversationService
from services.study_tools import SchedulingTools
from services.llm_contracts import ToolCall,ChatReply,LLMError
from services import profiles
from models.planning import StudyRange
from services.planning import PlanningError
import database


class ToolRepairTests(unittest.TestCase):
    setUp=fixtures.CalendarActionTests.setUp
    connect=fixtures.CalendarActionTests.connect

    def agent(self,user=101,message=1,**kw):
        return CalendarAgent(user,message,SchedulingTools(user,self.planner,self.actions),**kw)

    def call(self,name,args=None,id='native'):
        return ToolCall(id=id,name=name,arguments=args or {})

    def messages(self,text):return [{'role':'system','content':'Study assistant; Asia/Singapore.'},{'role':'user','content':text}]

    def test_observed_string_shapes_and_wrong_next_week_are_repaired_before_validation(self):
        arguments={'sessions':'3','excluded_days':'null','excluded_periods':'null','reviewed_notes':'null',
                   'restrictions_confirmed':'false','allow_unchecked_calendar':'false','timezone':'Asia/Singapore',
                   'start':'2026-10-15T00:00:00+08:00','end':'2026-10-21T23:59:59+08:00'}
        parsed=StudyRange.model_validate(normalize_arguments(arguments,StudyRange.model_json_schema()))
        self.assertEqual(parsed.sessions,3);self.assertEqual(parsed.excluded_days,[])
        provider=ScriptedIBM([
            ChatReply('',(self.call('find_study_periods',arguments),)),
            ChatReply('',(self.call('propose_plan',{'subject':'Python','sessions':'3','start':arguments['start'],'end':arguments['end'],
                'slot_groups':'[{"source_ordinal":"0","slot_indices":["0","1","2"],"title":"Python revision"}]'},id='draft'),)),
        ],relative_range='next_week')
        agent=self.agent()
        reply=agent.complete(provider,self.messages('Propose three Python revision sessions next week.'))
        plans=[r for r in agent.references() if r.get('review_kind')=='plan']
        self.assertEqual(len(plans),1);self.assertEqual(len(plans[0]['sessions']),3)
        saved=self.planner.proposals(101)[0]
        self.assertEqual(saved['constraints']['start'],'2026-10-19T00:00:00+08:00')
        self.assertEqual(saved['constraints']['days'],7)
        self.assertIn('Nothing is approved',reply.content)
        self.assertEqual(self.provider.inserts,[])

    def test_hello_after_failed_tool_turn_does_not_touch_calendar_or_replay_tools(self):
        failing=ScriptedIBM([ChatReply('',(self.call('find_study_periods',{'sessions':'many'}),)),
            ChatReply('I could not prepare a validated plan. No events were created.')])
        service=ConversationService(failing,clock=lambda:fixtures.NOW.timestamp(),agent_factory=lambda u,m,**kw:self.agent(u,m,**kw))
        service.respond(101,1,'Propose three Python sessions next week.')
        greeting=ScriptedIBM([],intent='chat',routing_reply='Hello! How can I help you study?')
        service=ConversationService(greeting,clock=lambda:fixtures.NOW.timestamp(),agent_factory=lambda u,m,**kw:self.agent(u,m,**kw))
        with patch.object(self.planner,'busy',side_effect=AssertionError('Greeting must not check Calendar')):
            result=service.respond(101,2,'hello')
        self.assertIn('Hello',result['text']);self.assertNotIn('validate',result['text'])
        self.assertEqual(len(greeting.requests),1)
        with closing(database.get_connection()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM conversation_tool_effects WHERE message_id=2').fetchone()[0],0)
        self.assertEqual(self.provider.inserts,[])

    def test_failed_inference_turn_is_excluded_from_following_greeting_history(self):
        provider=ScriptedIBM([],intent='chat',routing_reply='Hello!')
        factory=lambda u,m,**kw:self.agent(u,m,**kw)
        failed=ConversationService(provider,clock=lambda:fixtures.NOW.timestamp(),agent_factory=factory)
        with patch.object(provider,'complete',side_effect=LLMError('invalid_response')):
            self.assertRaises(LLMError,failed.respond,101,1,'Failed scheduling request')
        result=failed.respond(101,2,'hello')
        self.assertEqual(result['text'],'Hello!')
        self.assertNotIn('Failed scheduling request',json.dumps(provider.requests[-1]))

    def test_missing_tool_fields_are_returned_to_model_for_repair_not_user_blame(self):
        provider=ScriptedIBM([ChatReply('',(self.call('find_study_periods',{'sessions':3}),)),
            ChatReply('Which dates should these three sessions cover?')])
        result=self.agent().complete(provider,self.messages('Schedule three sessions.'))
        self.assertIn('Which dates',result.content)
        tool=json.loads(provider.requests[-1][-1]['content'])
        self.assertEqual(tool['error_code'],'invalid_tool_call')
        self.assertFalse(tool['approved']);self.assertEqual(self.provider.inserts,[])

    def test_ambiguous_request_gets_clarification_without_managed_event_lookup(self):
        provider=ScriptedIBM([],intent='chat',routing_reply='Which study session would you like to move?')
        result=self.agent().complete(provider,self.messages('Move it.'))
        self.assertIn('Which study session',result.content);self.assertEqual(len(provider.requests),1)
        self.assertEqual(self.provider.gets,[])

    def test_malformed_and_identity_arguments_are_not_repaired_into_authorization(self):
        for args in ({'sessions':'three'},{'sessions':'3.2'},{'sessions':'3','telegram_user_id':202}):
            self.assertRaises(LLMError,self.agent().invoke,self.call('find_study_periods',args),0)
        self.assertRaises(LLMError,self.agent().invoke,self.call('execute_approved_calendar_action'),0)
        self.assertEqual(self.provider.inserts,[])

    def test_relative_week_boundaries_timezone_and_year_rollover(self):
        now=datetime(2026,10,14,15,tzinfo=timezone.utc)
        start,end=relative_range('next_week',now,'Asia/Singapore')
        self.assertEqual(start.isoformat(),'2026-10-19T00:00:00+08:00')
        self.assertEqual(end.isoformat(),'2026-10-26T00:00:00+08:00')
        start,end=relative_range('next_week',datetime(2026,12,31,tzinfo=timezone.utc),'Asia/Singapore')
        self.assertEqual(start.date().isoformat(),'2027-01-04')
        self.assertEqual(end-start,timedelta(days=7))

    def test_actual_preferences_and_calendar_are_actor_bound_after_argument_repair(self):
        from models.preferences import StudyPreferences
        profiles.save_preferences(202,StudyPreferences(session_minutes=45,study_windows=[{'start':'13:00','end':'15:00'}]))
        for user in (101,202):
            agent=self.agent(user)
            start,end=relative_range('next_week',fixtures.NOW,'Asia/Singapore')
            result=agent.invoke(self.call('find_study_periods',{'start':start.isoformat(),'end':end.isoformat(),'sessions':'3'}),0)
            duration=(datetime.fromisoformat(result['periods'][0]['end'])-datetime.fromisoformat(result['periods'][0]['start'])).total_seconds()/60
            self.assertEqual(duration,30 if user==101 else 45)
        self.assertIn('synthetic-access-a',self.provider.reads);self.assertIn('synthetic-access-b',self.provider.reads)

    def test_constraint_and_network_errors_are_distinct_and_sanitized(self):
        args={'start':(fixtures.NOW+timedelta(days=1)).isoformat(),'end':(fixtures.NOW+timedelta(days=8)).isoformat(),'sessions':3}
        with patch.object(self.planner,'busy',side_effect=PlanningError('Set preferred study time windows first.')):
            result=self.agent(message=1).invoke(self.call('find_study_periods',args),0)
            self.assertEqual(result['error_code'],'scheduling_constraint');self.assertIn('time windows',result['error'])
        with patch.object(self.planner,'busy',side_effect=RuntimeError('PRIVATE-secret-provider-details')):
            result=self.agent(message=2).invoke(self.call('find_study_periods',args),0)
            self.assertEqual(result['error_code'],'calendar_unavailable');self.assertNotIn('PRIVATE',json.dumps(result))

    def test_compact_native_draft_contract_uses_checked_ranges_and_bound_slots(self):
        agent=self.agent()
        start,end=relative_range('next_week',fixtures.NOW,'Asia/Singapore')
        result=agent.invoke(self.call('find_study_periods',{'start':start.isoformat(),'end':end.isoformat(),'sessions':'3'}),0)
        args={'subject':'Python','sessions':'3','selections':json.dumps(result['slot_references']),'topics':None}
        draft=agent.invoke(self.call('propose_plan',args),1)
        self.assertEqual(len(draft['sessions']),3)
        self.assertEqual(self.planner.proposals(101)[0]['constraints']['days'],7)
        foreign=self.agent(202)
        self.assertRaises(LLMError,foreign.invoke,self.call('propose_plan',args),0)
        self.assertEqual(self.provider.inserts,[])
        schema=next(d['function']['parameters'] for d in agent.model_definitions() if d['function']['name']=='propose_plan')
        self.assertIn('selections',schema['properties']);self.assertNotIn('start',schema['properties'])

    def test_optional_absent_arrays_default_but_required_selections_fail_closed(self):
        from services.calendar_tool_inputs import DraftFromSlots
        for empty in (None,'null',''):
            args={'subject':'Python','sessions':'3','selections':'["0:0","0:1","0:2"]','topics':empty}
            parsed=DraftFromSlots.model_validate(normalize_arguments(args,DraftFromSlots.model_json_schema()))
            self.assertEqual(parsed.topics,[])
            args['selections']=empty
            self.assertRaises(ValueError,DraftFromSlots.model_validate,normalize_arguments(args,DraftFromSlots.model_json_schema()))

    def test_model_cannot_confirm_free_text_restrictions_during_availability(self):
        start,end=relative_range('next_week',fixtures.NOW,'Asia/Singapore')
        result=self.agent().invoke(self.call('find_study_periods',{'start':start.isoformat(),'end':end.isoformat(),'restrictions_confirmed':'true'}),0)
        self.assertEqual(result['error_code'],'scheduling_constraint')
        self.assertEqual(self.provider.reads,[])
