from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError
from httplib2 import Response
from test_api import api,database,signed_data,TEST_TOKEN
from test_google_oauth import SyntheticCipher,MockGoogle
from models.planning import StudyGoal,ProposalInput
from models.preferences import StudyPreferences
from services import profiles
from services.google_oauth import GoogleOAuthService,OAuthSettings,EVENT_SCOPE,OAuthUnavailable,ReconnectRequired
from services.planning import PlanningService,PlanningError
from services.calendar_actions import CalendarActions
from services.study_tools import SchedulingTools
from routes import planning as routes

ROOT=Path(__file__).resolve().parents[1]
NOW=datetime(2026,10,12,tzinfo=timezone.utc)


class EventGoogle(MockGoogle):
    def __init__(self):
        super().__init__();self.events={};self.inserts=[];self.gets=[];self.lose_response=False;self.fail_before=False;self.denied=False;self.create_hook=None

    def freebusy(self,value,start,end):
        self.reads.append(value['token'])
        return [{'start':event['start']['dateTime'],'end':event['end']['dateTime']} for (owner,key),event in self.events.items() if owner==value['token']]

    def get_event(self,value,event_id):
        self.gets.append((value['token'],event_id))
        if self.denied: raise HttpError(Response({'status':'403'}),b'{}')
        return self.events.get((value['token'],event_id))

    def create_event(self,value,event_id,body):
        if self.fail_before:
            self.fail_before=False;raise TimeoutError('Synthetic failure before remote success')
        self.inserts.append((value['token'],event_id))
        if self.create_hook: self.create_hook()
        key=(value['token'],event_id)
        if key in self.events: raise HttpError(Response({'status':'409'}),b'{}')
        event={'id':event_id,'status':'confirmed',**body}
        self.events[key]=event
        if self.lose_response:
            self.lose_response=False;raise TimeoutError('Synthetic lost remote response')
        return event


class CalendarActionTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='quiz-test-',dir=ROOT/'tests');self.addCleanup(self.folder.cleanup)
        db=patch.object(database,'DB_PATH',Path(self.folder.name)/'actions.db');db.start();self.addCleanup(db.stop)
        database.initialize_database(backfill_approvals=False)
        self.provider=EventGoogle()
        self.calendar=GoogleOAuthService(OAuthSettings(True,'https://study.example',event_writes_enabled=True),self.provider,SyntheticCipher())
        self.planner=PlanningService(self.calendar,lambda:NOW)
        self.actions=CalendarActions(self.calendar,self.planner,lambda:NOW)
        for user in (101,202):
            profiles.save_preferences(user,StudyPreferences(study_windows=[{'start':'18:00','end':'20:00'}]))
            self.connect(user,'a' if user==101 else 'b',True)
        self.goal=self.planner.save_goal(101,StudyGoal(subject='Python'),uuid4())

    def connect(self,user,account,writing):
        # Explicitly seed an availability-only fixture; ordinary reconnect retains grants.
        if not writing:self.calendar.disconnect(user)
        link=self.calendar.begin(user,write_access=writing)['authorization_start_url']
        ticket=parse_qs(urlsplit(link).query)['ticket'][0]
        url,cookie=self.calendar.start_browser(ticket)
        state=parse_qs(urlsplit(url).query)['state'][0]
        self.calendar.callback(state,cookie,code=account)

    def proposal(self,sessions=1):
        request=ProposalInput(request_id=uuid4(),start=NOW,sessions=sessions)
        proposal=self.planner.propose(101,self.goal['id'],request)
        return self.planner.decide(101,proposal['id'],'approve')

    def approved_action(self,sessions=1):
        proposal=self.proposal(sessions)
        return proposal,self.actions.approve(101,proposal['id'],proposal['proposal_digest'],uuid4())

    def test_exact_approval_is_required_and_cross_user_actions_are_hidden(self):
        draft=self.planner.propose(101,self.goal['id'],ProposalInput(request_id=uuid4(),start=NOW,sessions=1))
        self.assertRaises(PlanningError,self.actions.approve,101,draft['id'],draft['proposal_digest'],uuid4())
        proposal,action=self.approved_action()
        self.assertEqual(action['status'],'ready');self.assertEqual(self.provider.inserts,[])
        self.assertRaises(LookupError,self.actions.execute,202,action['id'])
        self.assertRaises(LookupError,self.actions.record,202,action['id'])
        self.assertRaises(LookupError,self.actions.approve,202,proposal['id'],proposal['proposal_digest'],uuid4())
        self.assertRaises(PlanningError,self.actions.approve,101,proposal['id'],'0'*64,uuid4())
        complete=self.actions.execute(101,action['id'])
        self.assertEqual(complete['status'],'completed')
        self.assertEqual(self.provider.inserts[0][0],'synthetic-access-a')
        event=next(iter(self.provider.events.values()))
        self.assertEqual(event['summary'],proposal['sessions'][0]['title'])
        self.assertNotIn('attendees',event)
        self.assertEqual(self.actions.execute(101,action['id'])['status'],'completed')
        self.assertEqual(len(self.provider.inserts),1)

    def test_two_students_create_only_in_their_own_connected_calendars(self):
        proposal,action=self.approved_action()
        self.actions.execute(101,action['id'])
        goal=self.planner.save_goal(202,StudyGoal(subject='SQL'),uuid4())
        second=self.planner.propose(202,goal['id'],ProposalInput(request_id=uuid4(),start=NOW,sessions=1))
        second=self.planner.decide(202,second['id'],'approve')
        action_b=self.actions.approve(202,second['id'],second['proposal_digest'],uuid4())
        self.assertEqual(self.actions.execute(202,action_b['id'])['status'],'completed')
        self.assertEqual([owner for owner,event_id in self.provider.inserts],['synthetic-access-a','synthetic-access-b'])
        self.assertNotEqual(self.provider.inserts[0][1],self.provider.inserts[1][1])

    def test_network_lost_response_recovers_same_event_after_expiry(self):
        proposal,action=self.approved_action()
        self.provider.lose_response=True
        first=self.actions.execute(101,action['id'])
        self.assertEqual(first['status'],'partial')
        self.actions.clock=lambda:NOW+timedelta(minutes=11)
        recovered=self.actions.execute(101,action['id'])
        self.assertEqual(recovered['status'],'completed')
        self.assertEqual(len(self.provider.inserts),1)
        self.assertEqual(len(self.provider.events),1)

    def test_expired_approval_blocks_new_writes_and_requires_explicit_renewal(self):
        proposal,action=self.approved_action()
        self.actions.clock=lambda:NOW+timedelta(minutes=11)
        self.assertRaises(PlanningError,self.actions.execute,101,action['id'])
        self.assertEqual(self.provider.inserts,[])
        renewed=self.actions.approve(101,proposal['id'],proposal['proposal_digest'],uuid4())
        self.assertEqual(renewed['id'],action['id'])
        self.assertEqual(self.actions.execute(101,action['id'])['status'],'completed')

    def test_partial_execution_recovers_without_recreating_prior_events(self):
        proposal,action=self.approved_action(3)
        original=self.provider.create_event
        calls=[]
        def fail_second(value,event_id,body):
            calls.append(event_id)
            if len(calls)==2: raise TimeoutError('Synthetic failure')
            return original(value,event_id,body)
        self.provider.create_event=fail_second
        partial=self.actions.execute(101,action['id'])
        self.assertEqual(partial['status'],'partial')
        self.assertEqual(sum(s['status']=='created' for s in partial['sessions']),1)
        self.provider.create_event=original
        self.assertEqual(self.actions.execute(101,action['id'])['status'],'completed')
        self.assertEqual(len(self.provider.events),3)
        self.assertEqual(len(self.provider.inserts),3)

    def test_permission_generation_revocation_and_global_flag_fail_closed(self):
        proposal,action=self.approved_action()
        self.calendar.settings=OAuthSettings(True,'https://study.example',event_writes_enabled=False)
        self.assertRaises(OAuthUnavailable,self.actions.execute,101,action['id'])
        self.assertEqual(self.provider.inserts,[])
        self.calendar.settings=OAuthSettings(True,'https://study.example',event_writes_enabled=True)
        self.provider.denied=True
        self.assertRaises(ReconnectRequired,self.actions.execute,101,action['id'])
        self.assertEqual(self.calendar.status(101)['status'],'reconnect_required')
        self.assertEqual(self.provider.inserts,[])

    def test_oauth_upgrade_never_writes_and_requires_fresh_generation_approval(self):
        self.connect(101,'a',False)
        proposal=self.proposal()
        self.assertRaises(ReconnectRequired,self.actions.approve,101,proposal['id'],proposal['proposal_digest'],uuid4())
        link=self.actions.permission_link(101,proposal['id'])
        ticket=parse_qs(urlsplit(link['authorization_start_url']).query)['ticket'][0]
        url,cookie=self.calendar.start_browser(ticket)
        state=parse_qs(urlsplit(url).query)['state'][0]
        self.calendar.callback(state,cookie,code='different-own-account')
        self.assertEqual(self.provider.inserts,[])
        self.assertTrue(self.calendar.status(101)['event_permission_granted'])
        self.assertRaises(PlanningError,self.actions.approve,101,proposal['id'],proposal['proposal_digest'],uuid4())
        self.assertRaises(LookupError,self.actions.permission_link,202,proposal['id'])

    def test_freebusy_only_users_and_cancelled_upgrade_keep_availability(self):
        self.connect(101,'a',False)
        self.assertFalse(self.calendar.status(101)['event_permission_granted'])
        generation=self.planner.generation(101)
        self.assertRaises(ReconnectRequired,self.calendar.event_credentials,101,generation)
        link=self.calendar.begin(101,write_access=True)['authorization_start_url']
        ticket=parse_qs(urlsplit(link).query)['ticket'][0]
        url,cookie=self.calendar.start_browser(ticket)
        state=parse_qs(urlsplit(url).query)['state'][0]
        self.assertFalse(self.calendar.callback(state,cookie,error='access_denied'))
        self.assertEqual(self.calendar.availability(101,NOW,NOW+timedelta(hours=1)),{'busy':[]})
        self.assertEqual(self.provider.inserts,[])

    def test_busy_conflicts_payload_tampering_and_disconnection_block_writes(self):
        proposal,action=self.approved_action()
        self.provider.freebusy=lambda value,a,b:[proposal['sessions'][0]]
        self.assertRaises(PlanningError,self.actions.execute,101,action['id'])
        self.assertEqual(self.provider.inserts,[])
        self.provider.freebusy=lambda value,a,b:[]
        with closing(database.get_connection()) as db,db:
            changed=json.loads(db.execute('SELECT proposal_json FROM study_proposals WHERE id=?',(proposal['id'],)).fetchone()[0])
            changed['sessions'][0]['title']='Unapproved title'
            db.execute('UPDATE study_proposals SET proposal_json=? WHERE id=?',(json.dumps(changed),proposal['id']))
        self.assertRaises(PlanningError,self.actions.execute,101,action['id'])
        self.calendar.disconnect(101)
        self.assertRaises(PlanningError,self.actions.execute,101,action['id'])
        self.assertEqual(self.provider.inserts,[])

    def test_duplicate_inflight_execution_does_not_insert_again(self):
        proposal,action=self.approved_action()
        nested=[]
        self.provider.create_hook=lambda:nested.append(self.actions.execute(101,action['id'])['status'])
        self.assertEqual(self.actions.execute(101,action['id'])['status'],'completed')
        self.assertEqual(nested,['processing'])
        self.assertEqual(len(self.provider.inserts),1)

    def test_retried_approval_does_not_extend_expiry_without_new_user_request(self):
        proposal=self.proposal()
        request_id=uuid4()
        first=self.actions.approve(101,proposal['id'],proposal['proposal_digest'],request_id)
        self.actions.clock=lambda:NOW+timedelta(minutes=11)
        retried=self.actions.approve(101,proposal['id'],proposal['proposal_digest'],request_id)
        self.assertEqual(first['approval_expires_at'],retried['approval_expires_at'])
        self.assertRaises(PlanningError,self.actions.execute,101,first['id'])
        renewed=self.actions.approve(101,proposal['id'],proposal['proposal_digest'],uuid4())
        self.assertNotEqual(first['approval_expires_at'],renewed['approval_expires_at'])

    def test_conflicting_insert_is_reconciled_and_grant_survives_restart(self):
        proposal,action=self.approved_action()
        original=self.provider.create_event
        def raced(value,event_id,body):
            self.provider.events[(value['token'],event_id)]={'id':event_id,'status':'confirmed',**body}
            return original(value,event_id,body)
        self.provider.create_event=raced
        restarted=CalendarActions(self.calendar,self.planner,lambda:NOW)
        self.assertEqual(restarted.execute(101,action['id'])['status'],'completed')
        self.assertEqual(len(self.provider.events),1)

    def test_written_events_cannot_be_silently_replaced_by_adjustment(self):
        proposal,action=self.approved_action()
        self.actions.execute(101,action['id'])
        replacement=self.planner.adjust(101,proposal['id'],ProposalInput(request_id=uuid4(),start=NOW,sessions=1,excluded_days=['mon']))
        self.assertRaises(PlanningError,self.planner.decide,101,replacement['id'],'approve')
        self.assertEqual(self.actions.record(101,action['id'])['status'],'completed')
        self.assertEqual(len(self.provider.events),1)

    def test_existing_mismatched_event_is_not_overwritten_or_recreated(self):
        proposal,action=self.approved_action()
        with closing(database.get_connection()) as db:
            intent=db.execute('SELECT * FROM calendar_event_intents WHERE action_id=?',(action['id'],)).fetchone()
        self.provider.events[('synthetic-access-a',intent['google_event_id'])]={'id':intent['google_event_id'],'summary':'Different event'}
        self.assertRaises(PlanningError,self.actions.execute,101,action['id'])
        self.assertEqual(self.provider.inserts,[])

    def test_recording_failure_recovers_remote_success(self):
        proposal,action=self.approved_action()
        with patch.object(self.actions,'mark_created',side_effect=RuntimeError('Synthetic SQLite failure')):
            self.assertEqual(self.actions.execute(101,action['id'])['status'],'partial')
        self.assertEqual(self.actions.execute(101,action['id'])['status'],'completed')
        self.assertEqual(len(self.provider.inserts),1)

    def test_editing_replaces_approval_but_never_changes_written_events(self):
        proposal,action=self.approved_action()
        replacement=self.planner.adjust(101,proposal['id'],ProposalInput(request_id=uuid4(),start=NOW,sessions=1,excluded_days=['mon']))
        self.assertEqual(self.actions.record(101,action['id'])['status'],'blocked')
        self.assertRaises(PlanningError,self.actions.execute,101,action['id'])
        self.planner.decide(101,replacement['id'],'approve')
        self.assertEqual(self.actions.record(101,action['id'])['status'],'blocked')
        self.assertRaises(PlanningError,self.actions.execute,101,action['id'])

    def test_tool_interface_has_no_actor_override_or_model_approval_tool(self):
        tools=SchedulingTools(101,self.planner,self.actions)
        arguments={'start':NOW.isoformat(),'end':(NOW+timedelta(days=2)).isoformat(),'timezone':'Asia/Singapore'}
        found=tools.invoke('find_study_periods',arguments)
        self.assertTrue(found['periods'])
        names={spec['name'] for spec in tools.specifications()}
        self.assertNotIn('approve_calendar_creation',names)
        self.assertRaises(LookupError,tools.invoke,'approve_calendar_creation',{})
        from pydantic import ValidationError
        self.assertRaises(ValidationError,tools.invoke,'find_study_periods',{**arguments,'user_id':202})
        exact={'request_id':str(uuid4()),'goal_id':self.goal['id'],'start':NOW.isoformat(),'sessions':1,
               'proposed_sessions':[{'start':found['periods'][0]['start'],'end':found['periods'][0]['end'],'title':'Practise Python loops','topic':'Loops'}]}
        draft=tools.invoke('create_draft',exact)
        self.assertEqual(draft['sessions'][0]['title'],'Practise Python loops')
        self.assertEqual(draft['status'],'draft')
        self.assertEqual(self.provider.inserts,[])
        self.assertRaises(LookupError,SchedulingTools(202,self.planner,self.actions).invoke,'create_draft',exact)

    def test_model_tool_arguments_cannot_claim_human_restriction_confirmation(self):
        profiles.save_preferences(101,StudyPreferences(study_windows=[{'start':'18:00','end':'20:00'}],notes='No study after dinner.'))
        arguments={'start':NOW.isoformat(),'end':(NOW+timedelta(days=2)).isoformat(),'reviewed_notes':'No study after dinner.','restrictions_confirmed':True}
        tools=SchedulingTools(101,self.planner,self.actions)
        self.assertRaises(PlanningError,tools.invoke,'find_study_periods',arguments)


class CalendarActionAPITests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='quiz-test-',dir=ROOT/'tests');self.addCleanup(self.folder.cleanup)
        db=patch.object(database,'DB_PATH',Path(self.folder.name)/'api.db');db.start();self.addCleanup(db.stop)
        token=patch.object(api,'BOT_TOKEN',TEST_TOKEN);token.start();self.addCleanup(token.stop)
        self.client=TestClient(api.app,base_url='http://localhost');self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)

    def test_mini_app_cannot_replace_explicit_telegram_event_approval(self):
        with patch.object(routes,'calendar_actions') as factory:
            response=self.client.post('/api/study/proposals/'+str(uuid4())+'/calendar-approval',
                headers={'X-Telegram-Init-Data':signed_data(101)},
                json={'request_id':str(uuid4()),'proposal_digest':'0'*64})
            self.assertEqual(response.status_code,409)
            factory.assert_not_called()

    def test_new_write_and_tool_endpoints_require_signed_identity(self):
        identifier=str(uuid4())
        for method,path in [('GET','/api/study/tool-schemas'),('POST','/api/study/tools/find_study_periods'),('GET','/api/study/calendar-actions'),('GET','/api/study/sessions'),('POST',f'/api/study/proposals/{identifier}/event-permission'),('POST',f'/api/study/proposals/{identifier}/calendar-approval'),('POST',f'/api/study/calendar-actions/{identifier}/execute')]:
            self.assertEqual(self.client.request(method,path,json={}).status_code,401)
        result=self.client.get('/api/study/tool-schemas',headers={'X-Telegram-Init-Data':signed_data(101)})
        self.assertEqual(result.status_code,200)
        self.assertNotIn('token',json.dumps(result.json()).lower())


if __name__=='__main__':unittest.main()
