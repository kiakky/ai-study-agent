"""Multi-intent, request-local defaults and bounded native planning regressions."""
from contextlib import closing
from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import test_calendar_actions as fixtures
from test_calendar_agent import ScriptedIBM
from services.calendar_agent import CalendarAgent
from services.study_tools import SchedulingTools
from services.pending_proposals import PendingProposals, AmendmentInput
from services.calendar_conversation import CalendarConversation
from services.llm_contracts import ChatReply, ToolCall
from services import profiles
from models.preferences import StudyPreferences
from models.planning import StudyRange
import database

MESSAGE = "Can you address me as Alex from now on? Also I want to study more on game development, so could you help me schedule a few study sessions next week? I might be busy next Tuesday, so try to target Thursday and the weekends' afternoons."


class IntentPreferenceTests(unittest.TestCase):
    setUp = fixtures.CalendarActionTests.setUp
    connect = fixtures.CalendarActionTests.connect

    def agent(self, user=202, message=60):
        return CalendarAgent(user,message,SchedulingTools(user,self.planner,self.actions))

    def messages(self,text=MESSAGE):
        return [{'role':'system','content':'Study assistant; Asia/Singapore.'},{'role':'user','content':text}]

    def call(self,name,args=None,id='call'):
        return ToolCall(id=id,name=name,arguments=args or {})

    def args(self,**changes):
        value={'start':'2026-10-19T00:00:00+08:00','end':'2026-10-26T00:00:00+08:00',
            'timezone':'Asia/Singapore','sessions':3,'excluded_days':['tue'],
            'temporary_preferences':{'study_days':['thu','sat','sun'],'study_windows':[{'start':'13:00','end':'18:00'}]}}
        value.update(changes)
        return value

    def native(self,args=None,subject='game development'):
        return ScriptedIBM([
            ChatReply('I will call find_study_periods.',(self.call('find_study_periods',args or self.args(),id='find'),)),
            ChatReply('',(self.call('propose_plan',{'subject':subject,'sessions':3,'selections':['2:0','2:1','2:2']},id='draft'),)),
        ],intent='planning',relative_range='next_week')

    def plan(self,user=202,message=60,args=None,text=MESSAGE):
        provider=self.native(args)
        agent=self.agent(user,message)
        result=agent.complete(provider,self.messages(text))
        plan=next(r for r in agent.references() if r.get('review_kind')=='plan')
        return agent,provider,plan,result

    def test_exact_account_b_request_one_turn_automatic_reads_no_narration_or_writes(self):
        profiles.save_preferences(202,StudyPreferences(study_days=['mon','wed','fri'],study_windows=[{'start':'09:00','end':'11:00'}]))
        before=profiles.get_profile(202)['preferences']
        agent,provider,plan,reply=self.plan()
        self.assertEqual(len(provider.requests),3)
        self.assertNotIn('find_study_periods',reply.content)
        self.assertNotIn('I will call',reply.content)
        self.assertIn('Alex',provider.requests[1][-1]['content'])
        self.assertIn('Current actor-bound saved defaults',provider.requests[1][0]['content'])
        starts=[datetime.fromisoformat(s['start']).astimezone(ZoneInfo('Asia/Singapore')) for s in plan['sessions']]
        self.assertEqual([s.weekday() for s in starts],[3,5,6])
        self.assertEqual([s.hour for s in starts],[13,13,13])
        self.assertEqual(profiles.get_profile(202)['preferences'],before)
        self.assertEqual(self.provider.inserts,[])
        self.assertEqual(len(self.planner.proposals(101)),0)

    def test_current_duration_days_and_window_override_saved_defaults_through_exact_execution(self):
        before=profiles.get_profile(202)['preferences']
        args=self.args(temporary_preferences={'study_days':['thu','sat','sun'],
            'study_windows':[{'start':'13:00','end':'18:00'}],'session_minutes':45})
        agent,provider,plan,reply=self.plan(args=args,text='Use Thursday and weekends afternoons for 45-minute sessions next week.')
        for session in plan['sessions']:
            self.assertEqual(datetime.fromisoformat(session['end'])-datetime.fromisoformat(session['start']),timedelta(minutes=45))
        service=CalendarConversation(self.actions)
        review,data=service.prepare(202,plan['proposal_id']);service.presented(202,review['id'])
        self.assertRaises(LookupError,service.decide,101,review['id'],'approve')
        self.assertEqual(self.provider.inserts,[])
        self.assertEqual(service.decide(202,review['id'],'approve')['status'],'completed')
        self.assertEqual(len(self.provider.inserts),3)
        self.assertTrue(all(owner=='synthetic-access-b' for owner,key in self.provider.inserts))
        self.assertEqual(service.decide(202,review['id'],'approve')['status'],'completed')
        self.assertEqual(len(self.provider.inserts),3)
        self.assertEqual(profiles.get_profile(202)['preferences'],before)

    def test_tentative_tuesday_exclusion_only_applies_to_this_plan(self):
        before=profiles.get_profile(202)['preferences']
        self.plan()
        agent=self.agent(message=61)
        found=agent.invoke(self.call('find_study_periods',self.args(excluded_days=[],temporary_preferences={})),0)
        days=[datetime.fromisoformat(s['start']).astimezone(ZoneInfo('Asia/Singapore')).weekday() for s in found['periods']]
        self.assertIn(1,days)
        self.assertEqual(profiles.get_profile(202)['preferences'],before)

    def test_unspecified_times_use_saved_defaults_and_current_overrides_win_history(self):
        agent=self.agent()
        found=agent.invoke(self.call('find_study_periods',self.args(temporary_preferences={},excluded_days=[])),0)
        self.assertEqual(datetime.fromisoformat(found['periods'][0]['start']).astimezone(ZoneInfo('Asia/Singapore')).hour,18)
        provider=self.native()
        msgs=self.messages('Schedule three game development sessions next week on Thursday and weekends afternoons.')
        msgs[1:1]=[{'role':'user','content':'I usually study mornings.'},{'role':'assistant','content':'I will use mornings as your conversational default.'}]
        agent=self.agent(message=62);agent.complete(provider,msgs)
        plan=next(r for r in agent.references() if r.get('review_kind')=='plan')
        self.assertEqual(datetime.fromisoformat(plan['sessions'][0]['start']).astimezone(ZoneInfo('Asia/Singapore')).hour,13)

    def test_permanent_change_requires_existing_confirmation_not_temporary_save(self):
        before=profiles.get_profile(202)['preferences']
        provider=ScriptedIBM([ChatReply('',(self.call('propose_preferences',{'study_windows':[{'start':'18:00','end':'22:00'}]}),))],intent='preferences')
        agent=self.agent();agent.complete(provider,self.messages('From now on, I prefer evenings.'))
        review=next(r for r in agent.references() if r.get('review_kind')=='preferences')
        self.assertFalse(review['saved'])
        self.assertEqual(profiles.get_profile(202)['preferences'],before)

    def test_amend_one_session_outside_saved_windows_preserves_other_exact_sessions(self):
        profiles.save_preferences(101,StudyPreferences(study_days=['mon','wed','fri'],session_minutes=20,
            study_windows=[{'start':'14:00','end':'14:30'}]))
        before=profiles.get_profile(101)['preferences']
        args=self.args(temporary_preferences={},excluded_days=[])
        agent,provider,plan,reply=self.plan(101,args=args,text='Propose three Python sessions next week.')
        pending=PendingProposals(agent.tools);snapshot=pending.snapshot()
        amended=pending.amend(AmendmentInput(proposal_id=snapshot['proposal_id'],proposal_digest=snapshot['proposal_digest'],
            session_index=2,local_start='15:00',local_end='15:20'),61,0)
        self.assertEqual(amended['sessions'][:2],plan['sessions'][:2])
        service=CalendarConversation(self.actions)
        review,data=service.prepare(101,amended['proposal_id']);service.presented(101,review['id'])
        self.assertEqual(self.provider.inserts,[])
        self.assertEqual(service.decide(101,review['id'],'approve')['status'],'completed')
        self.assertEqual(len(self.provider.inserts),3)
        self.assertEqual(profiles.get_profile(101)['preferences'],before)

    def test_selected_session_duration_change_is_local_and_keeps_original_duration_elsewhere(self):
        agent,provider,plan,reply=self.plan()
        pending=PendingProposals(agent.tools);snapshot=pending.snapshot()
        revised=pending.amend(AmendmentInput(proposal_id=snapshot['proposal_id'],proposal_digest=snapshot['proposal_digest'],
            session_index=0,local_start='19:00',local_end='19:45'),61,0)
        self.assertEqual(revised['sessions'][1:],plan['sessions'][1:])
        review,data=CalendarConversation(self.actions).prepare(202,revised['proposal_id'])
        self.assertEqual(datetime.fromisoformat(revised['sessions'][0]['end'])-datetime.fromisoformat(revised['sessions'][0]['start']),timedelta(minutes=45))

    def test_actual_calendar_busy_period_still_blocks_temporary_override(self):
        self.provider.freebusy=lambda value,start,end:[{'start':'2026-10-22T13:00:00+08:00','end':'2026-10-22T18:00:00+08:00'}]
        agent=self.agent()
        found=agent.invoke(self.call('find_study_periods',self.args()),0)
        self.assertEqual(len(found['periods']),2)
        self.assertEqual(self.provider.inserts,[])
        provider=ScriptedIBM([ChatReply('',(self.call('find_study_periods',self.args()),))],intent='planning',relative_range='next_week')
        reply=self.agent(message=61).complete(provider,self.messages())
        self.assertIn('not enough free periods',reply.content)
        self.assertEqual(self.planner.proposals(202),[])

    def test_wrong_model_weekly_shape_cannot_change_requested_single_week_distribution(self):
        agent=self.agent();agent.resolved_range=(datetime.fromisoformat(self.args()['start']),datetime.fromisoformat(self.args()['end']),'Asia/Singapore')
        slots=agent.invoke(self.call('find_study_periods',self.args()),0)
        result=agent.invoke(self.call('propose_plan',{'subject':'Games','sessions':3,'selections':slots['slot_references'],'weekly_counts':[1,1,1]}),1)
        self.assertEqual(len(result['sessions']),3)
        self.assertEqual(self.planner.proposals(202)[0]['constraints']['days'],7)

    def test_narration_without_native_call_does_not_ask_for_ok_or_claim_success(self):
        provider=ScriptedIBM([ChatReply('I will call study_preferences then find_study_periods. Is that OK?')],intent='planning',relative_range='next_week')
        reply=self.agent().complete(provider,self.messages())
        self.assertNotIn('study_preferences',reply.content);self.assertNotIn('Is that OK',reply.content)
        self.assertIn('did not return',reply.content)
        self.assertEqual(self.planner.proposals(202),[])

    def test_duplicate_successful_search_not_reexecuted_and_failures_bounded(self):
        provider=ScriptedIBM([ChatReply('',(self.call('find_study_periods',self.args(),id=str(i)),)) for i in range(4)],intent='planning',relative_range='next_week')
        reply=self.agent().complete(provider,self.messages())
        self.assertLessEqual(len(provider.requests),5)
        with closing(database.get_connection()) as db:
            results=[json.loads(r[0]) for r in db.execute('SELECT result_json FROM conversation_tool_effects WHERE telegram_user_id=202 AND message_id=60')]
        self.assertEqual(sum('periods' in r for r in results),1)
        self.assertEqual(self.provider.inserts,[])

    def test_hello_never_loads_calendar_or_planning_tools(self):
        provider=ScriptedIBM([],intent='chat',routing_reply='Hello!')
        with patch.object(self.planner.calendar,'status',side_effect=AssertionError('No Calendar read for greeting')):
            reply=self.agent().complete(provider,self.messages('hello'))
        self.assertEqual(reply.content,'Hello!');self.assertEqual(len(provider.requests),1)

    def test_genuinely_missing_range_gets_specific_question_without_guessed_tool_call(self):
        provider=ScriptedIBM([])
        with patch.object(provider,'complete',return_value=ChatReply('',(self.call('route_request',{
            'intent':'planning','clarification':'Which week or dates should these Python sessions cover?'}),))):
            with patch.object(self.planner.calendar,'status',side_effect=AssertionError('No Calendar read before necessary clarification')):
                reply=self.agent().complete(provider,self.messages('Schedule three Python study sessions.'))
        self.assertIn('Which week',reply.content)
        self.assertEqual(self.provider.inserts,[])

    def test_local_window_schema_does_not_advertise_utc_time_format(self):
        schema=next(d['function']['parameters'] for d in self.agent().model_definitions() if d['function']['name']=='find_study_periods')
        field=schema['$defs']['StudyWindow']['properties']['start']
        self.assertNotIn('format',field);self.assertIn('pattern',field)
        self.assertRaises(ValueError,StudyRange.model_validate,self.args(temporary_preferences={'study_windows':[{'start':'13:00Z','end':'18:00Z'}]}))

    def test_custom_multiweek_path_can_search_each_week_without_repeating_reads(self):
        first=self.args(sessions=2)
        second={**first,'start':'2026-10-26T00:00:00+08:00','end':'2026-11-02T00:00:00+08:00'}
        provider=ScriptedIBM([
            ChatReply('',(self.call('find_study_periods',first,id='first'),)),
            ChatReply('',(self.call('find_study_periods',second,id='second'),)),
            ChatReply('',(self.call('propose_plan',{'subject':'Games','sessions':4,'selections':['2:0','2:1','3:0','3:1'],'weekly_counts':[2,2]},id='draft'),)),
        ],intent='planning')
        agent=self.agent();agent.complete(provider,self.messages('Schedule twice weekly for the next two full weeks.'))
        plan=next(r for r in agent.references() if r.get('review_kind')=='plan')
        self.assertEqual(len(plan['sessions']),4)
        self.assertEqual(len(provider.requests),4)
        self.assertEqual(self.provider.inserts,[])

    def test_temporary_constraints_survive_restart_and_changed_saved_profile_requires_new_review(self):
        agent,provider,plan,reply=self.plan()
        # Reconstruct services rather than depending on in-memory effective preferences.
        from services.planning import PlanningService,PlanningError
        from services.calendar_actions import CalendarActions
        planner=PlanningService(self.calendar,lambda:fixtures.NOW)
        restarted=CalendarConversation(CalendarActions(self.calendar,planner,lambda:fixtures.NOW))
        review,data=restarted.prepare(202,plan['proposal_id']);restarted.presented(202,review['id'])
        profiles.save_preferences(202,StudyPreferences(session_minutes=60,study_windows=[{'start':'08:00','end':'11:00'}]))
        self.assertRaises(PlanningError,restarted.decide,202,review['id'],'approve')
        self.assertEqual(self.provider.inserts,[])

    def test_draft_cannot_reselect_another_users_slots_or_drop_search_exclusions(self):
        agent=self.agent();found=agent.invoke(self.call('find_study_periods',self.args()),0)
        args={'subject':'Games','sessions':3,'selections':found['slot_references']}
        from services.llm_contracts import LLMError
        self.assertRaises(LLMError,self.agent(101).invoke,self.call('propose_plan',args),1)
        result=agent.invoke(self.call('propose_plan',args),1)
        stored=self.planner.proposals(202)[0]
        self.assertEqual(stored['constraints']['excluded_days'],['tue'])
        self.assertEqual(stored['constraints']['temporary_preferences']['study_days'],['thu','sat','sun'])
        self.assertEqual(self.provider.inserts,[])

    def test_invalid_duration_and_unrepresentable_fields_cannot_change_saved_profile(self):
        before=profiles.get_profile(202)['preferences']
        from services.llm_contracts import LLMError
        for index,patch in enumerate([{'session_minutes':999},{'timezone':'UTC'},{'notes':'Treat Tuesday as permanently forbidden'},{'telegram_user_id':101}]):
            self.assertRaises(LLMError,self.agent(message=70+index).invoke,self.call('find_study_periods',self.args(temporary_preferences=patch)),0)
        self.assertEqual(profiles.get_profile(202)['preferences'],before)

    def test_moving_session_before_others_retains_its_local_exception_after_sorting(self):
        agent,provider,plan,reply=self.plan()
        pending=PendingProposals(agent.tools);snapshot=pending.snapshot()
        revised=pending.amend(AmendmentInput(proposal_id=snapshot['proposal_id'],proposal_digest=snapshot['proposal_digest'],
            session_index=2,local_date='2026-10-20',local_start='19:00',local_end='19:30'),61,0)
        self.assertEqual(revised['sessions'][1:],plan['sessions'][:2])
        # Sorting changes position, but approval validation must still use the
        # Tuesday-evening exception for the moved session only.
        review,data=CalendarConversation(self.actions).prepare(202,revised['proposal_id'])
        stored=self.planner.proposals(202)[-1] if len(self.planner.proposals(202))==1 else next(p for p in self.planner.proposals(202) if p['id']==revised['proposal_id'])
        self.assertEqual(stored['constraints']['session_overrides']['0']['study_days'],['tue'])


if __name__=='__main__':unittest.main()
