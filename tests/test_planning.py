from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_api import api, database, signed_data, TEST_TOKEN
from fastapi.testclient import TestClient
from models.planning import StudyGoal, GoalInput, ProposalInput
from models.preferences import StudyPreferences
from models.exams import ExamInformation, ExamFact, Evidence
from services import profiles
from services.planning import PlanningService, PlanningError
from services.google_oauth import ReconnectRequired
from routes import planning as planning_routes

ROOT=Path(__file__).resolve().parents[1]
NOW=datetime(2026,10,12,tzinfo=timezone.utc)


class MockCalendar:
    def __init__(self): self.connected=False;self.periods=[];self.calls=[];self.failure=None
    def status(self,user): return {'available':True,'status':'connected' if self.connected else 'disconnected'}
    def availability(self,user,start,end):
        self.calls.append((user,start,end))
        if self.failure: raise self.failure
        return {'busy':self.periods}


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='quiz-test-',dir=ROOT/'tests');self.addCleanup(self.folder.cleanup)
        self.db=patch.object(database,'DB_PATH',Path(self.folder.name)/'plans.db');self.db.start();self.addCleanup(self.db.stop)
        database.initialize_database(backfill_approvals=False)
        profiles.save_preferences(101,StudyPreferences(study_days=['mon','tue','wed'],study_windows=[{'start':'18:00','end':'20:00'}]))
        self.calendar=MockCalendar();self.service=PlanningService(self.calendar,lambda:NOW)
        self.goal=self.service.save_goal(101,StudyGoal(subject='Python',topics=['Loops','Functions']),uuid4())

    def request(self,**changes):
        return ProposalInput(request_id=uuid4(),start=NOW,allow_unchecked_calendar=True,**changes)

    def connect(self):
        self.calendar.connected=True
        with closing(database.get_connection()) as db,db: db.execute("INSERT INTO google_connections (telegram_user_id,status,generation) VALUES (101,'connected',1)")

    def test_constraints_timezone_and_saved_preferences(self):
        plan=self.service.propose(101,self.goal['id'],self.request())
        self.assertEqual(len(plan['sessions']),3)
        self.assertEqual(plan['sessions'][0]['start'],'2026-10-12T10:00:00+00:00')
        self.assertEqual(plan['practice_format'],'mcq')
        self.assertFalse(plan['event_creation_enabled'])
        self.assertEqual(plan['calendar_availability'],'not_checked')
        for session in plan['sessions']:
            self.assertEqual(datetime.fromisoformat(session['end'])-datetime.fromisoformat(session['start']),timedelta(minutes=30))

    def test_busy_intervals_user_isolation_and_exclusions(self):
        self.connect()
        self.calendar.periods=[{'start':'2026-10-12T09:45:00Z','end':'2026-10-12T10:20:00Z'}]
        plan=self.service.propose(101,self.goal['id'],self.request(excluded_days=['tue']))
        self.assertEqual(plan['sessions'][0]['start'],'2026-10-12T10:20:00+00:00')
        self.assertEqual(len(plan['sessions']),2)
        self.assertTrue(all(call[0]==101 for call in self.calendar.calls))
        self.assertEqual(self.service.proposals(202),[])
        self.assertRaises(LookupError,self.service.propose,202,self.goal['id'],self.request())
        self.assertRaises(LookupError,self.service.decide,202,plan['id'],'approve')

    def test_optional_calendar_is_explicit_and_errors_do_not_fall_back(self):
        request=self.request();request.allow_unchecked_calendar=False
        self.assertRaises(PlanningError,self.service.propose,101,self.goal['id'],request)
        self.connect();self.calendar.failure=ReconnectRequired()
        self.assertRaises(ReconnectRequired,self.service.propose,101,self.goal['id'],self.request())
        self.assertEqual(self.service.proposals(101),[])

    def test_free_text_must_be_reviewed_and_mapped(self):
        prefs=StudyPreferences(study_windows=[{'start':'18:00','end':'20:00'}],notes='No Mondays; rest after work.')
        profiles.save_preferences(101,prefs)
        self.assertRaises(PlanningError,self.service.propose,101,self.goal['id'],self.request())
        plan=self.service.propose(101,self.goal['id'],self.request(reviewed_notes=prefs.notes,restrictions_confirmed=True,excluded_days=['mon']))
        self.assertNotEqual(datetime.fromisoformat(plan['sessions'][0]['start']).weekday(),0)

    def test_empty_windows_and_no_available_slots_fail_without_saving(self):
        profiles.save_preferences(101,StudyPreferences())
        self.assertRaises(PlanningError,self.service.propose,101,self.goal['id'],self.request())
        profiles.save_preferences(101,StudyPreferences(study_windows=[{'start':'18:00','end':'18:15'}]))
        self.assertRaises(PlanningError,self.service.propose,101,self.goal['id'],self.request())
        self.assertEqual(self.service.proposals(101),[])

    def test_goal_and_proposal_retry_idempotency(self):
        key=uuid4();goal=StudyGoal(subject='SQL')
        a=self.service.save_goal(101,goal,key);b=self.service.save_goal(101,goal,key)
        self.assertEqual(a['id'],b['id'])
        self.assertRaises(PlanningError,self.service.save_goal,101,StudyGoal(subject='Other'),key)
        request=self.request();a=self.service.propose(101,self.goal['id'],request);b=self.service.propose(101,self.goal['id'],request)
        self.assertEqual(a['id'],b['id']);request.sessions=1
        self.assertRaises(PlanningError,self.service.propose,101,self.goal['id'],request)

    def test_approval_is_app_only_and_adjustment_does_not_change_approved_plan(self):
        first=self.service.propose(101,self.goal['id'],self.request())
        approved=self.service.decide(101,first['id'],'approve')
        self.assertEqual(approved['status'],'approved')
        self.assertEqual(self.service.decide(101,first['id'],'approve')['id'],first['id'])
        self.assertEqual(profiles.current_study_plan(101)['state'],'available')
        adjustment=self.service.adjust(101,first['id'],self.request(excluded_days=['mon']))
        states={p['id']:p['status'] for p in self.service.proposals(101)}
        self.assertEqual(states[first['id']],'approved')
        self.service.decide(101,adjustment['id'],'reject')
        self.assertEqual({p['id']:p['status'] for p in self.service.proposals(101)}[first['id']],'approved')
        replacement=self.service.adjust(101,first['id'],self.request(excluded_days=['tue']))
        self.service.decide(101,replacement['id'],'approve')
        self.assertEqual({p['id']:p['status'] for p in self.service.proposals(101)}[first['id']],'superseded')
        with closing(database.get_connection()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM calendar_schedule_requests').fetchone()[0],0)
            self.assertIsNotNone(db.execute('SELECT approved_digest FROM study_proposals WHERE id=?',(replacement['id'],)).fetchone()[0])

    def test_changed_calendar_preferences_connection_and_expiry_prevent_approval(self):
        self.connect()
        plan=self.service.propose(101,self.goal['id'],self.request())
        self.calendar.periods=[plan['sessions'][0]]
        self.assertRaises(PlanningError,self.service.decide,101,plan['id'],'approve')
        self.calendar.periods=[]
        with closing(database.get_connection()) as db,db:db.execute('UPDATE google_connections SET generation=2 WHERE telegram_user_id=101')
        self.assertRaises(PlanningError,self.service.decide,101,plan['id'],'approve')
        second=self.service.propose(101,self.goal['id'],self.request())
        profiles.save_preferences(101,StudyPreferences(study_windows=[{'start':'18:00','end':'20:00'}],session_minutes=15))
        self.assertRaises(PlanningError,self.service.decide,101,second['id'],'approve')
        third=self.service.propose(101,self.goal['id'],self.request())
        self.service.clock=lambda:NOW+timedelta(days=2)
        self.assertRaises(PlanningError,self.service.decide,101,third['id'],'approve')

    def test_concurrent_drafts_cannot_both_approve_overlapping_slots(self):
        a=self.service.propose(101,self.goal['id'],self.request());b=self.service.propose(101,self.goal['id'],self.request())
        self.service.decide(101,a['id'],'approve')
        self.assertRaises(PlanningError,self.service.decide,101,b['id'],'approve')

    def test_overnight_selected_days_and_dst_fail_closed(self):
        profiles.save_preferences(101,StudyPreferences(study_days=['mon'],study_windows=[{'start':'23:30','end':'01:00'}],session_minutes=60))
        self.assertRaises(PlanningError,self.service.propose,101,self.goal['id'],self.request())
        profiles.save_preferences(101,StudyPreferences(study_windows=[{'start':'02:00','end':'03:00'}]))
        self.service.clock=lambda:datetime(2026,3,8,tzinfo=timezone.utc)
        request=ProposalInput(request_id=uuid4(),start=self.service.clock(),days=1,timezone='America/New_York',allow_unchecked_calendar=True)
        self.assertRaises(PlanningError,self.service.propose,101,self.goal['id'],request)

    def test_target_date_and_source_grounded_exam_gate(self):
        goal=self.service.save_goal(101,StudyGoal(subject='SQL',target_date=NOW.date()),uuid4())
        plan=self.service.propose(101,goal['id'],self.request())
        self.assertEqual(len(plan['sessions']),1)
        evidence=Evidence(source_reference='authorized-lms/doc',excerpt='Exam on October 13 at noon.',retrieved_at=NOW)
        exam=ExamInformation(course_id='course',course_name='Course',confirmed_start=ExamFact(value='2026-10-13T04:00:00+00:00',status='confirmed',evidence=[evidence]))
        goal=StudyGoal(subject='Course',exam=exam)
        self.assertRaises(PlanningError,self.service.save_goal,202,goal,uuid4(),'brightspace',101)
        self.assertRaises(PlanningError,self.service.save_goal,101,goal,uuid4())
        saved=self.service.save_goal(101,goal,uuid4(),'brightspace',101)
        plan=self.service.propose(101,saved['id'],self.request())
        self.assertEqual(len(plan['sessions']),1)
        self.assertRaises(ValueError,GoalInput,request_id=uuid4(),subject='Invented',exam=exam)


class PlanningAPITests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='quiz-test-',dir=ROOT/'tests');self.addCleanup(self.folder.cleanup)
        db=patch.object(database,'DB_PATH',Path(self.folder.name)/'api.db');db.start();self.addCleanup(db.stop)
        token=patch.object(api,'BOT_TOKEN',TEST_TOKEN);token.start();self.addCleanup(token.stop)
        self.service=PlanningService(MockCalendar(),lambda:NOW)
        service=patch.object(planning_routes,'planning_service',return_value=self.service);service.start();self.addCleanup(service.stop)
        self.client=TestClient(api.app,base_url='http://localhost');self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
        self.headers={'X-Telegram-Init-Data':signed_data(101)};self.other={'X-Telegram-Init-Data':signed_data(202)}

    def test_routes_require_telegram_identity_and_isolate_ownership(self):
        goal_id=str(uuid4());proposal_id=str(uuid4())
        for method,path in [('GET','/api/study/goals'),('POST','/api/study/goals'),('GET','/api/study/proposals'),('POST',f'/api/study/goals/{goal_id}/proposals'),('POST',f'/api/study/proposals/{proposal_id}/decision'),('POST',f'/api/study/proposals/{proposal_id}/adjust')]:
            self.assertEqual(self.client.request(method,path,json={}).status_code,401)
        self.assertEqual(self.client.get('/study').status_code,200)
        self.assertEqual(self.client.get('/static/study.js').status_code,200)
        response=self.client.post('/api/study/goals',headers=self.headers,json={'request_id':str(uuid4()),'subject':'Python'})
        self.assertEqual(response.status_code,200);identifier=response.json()['id']
        self.assertEqual(self.client.get('/api/study/goals',headers=self.other).json(),[])
        request={'request_id':str(uuid4()),'start':NOW.isoformat(),'allow_unchecked_calendar':True}
        self.assertEqual(self.client.post(f'/api/study/goals/{identifier}/proposals',headers=self.other,json=request).status_code,404)
        profiles.save_preferences(101,StudyPreferences(study_windows=[{'start':'18:00','end':'20:00'}]))
        plan=self.client.post(f'/api/study/goals/{identifier}/proposals',headers=self.headers,json=request).json()
        self.assertEqual(plan['status'],'draft')
        self.assertEqual(self.client.post(f"/api/study/proposals/{plan['id']}/decision",headers=self.other,json={'action':'approve'}).status_code,404)
        approved=self.client.post(f"/api/study/proposals/{plan['id']}/decision",headers=self.headers,json={'action':'approve'})
        self.assertEqual(approved.status_code,200)
        self.assertFalse(approved.json()['event_creation_enabled'])

    def test_invalid_timezone_and_fabricated_lms_metadata_are_rejected(self):
        response=self.client.post('/api/study/goals',headers=self.headers,json={'request_id':str(uuid4()),'subject':'Course','resources':[{'title':'fake','reference':'fake','source':'brightspace'}]})
        self.assertEqual(response.status_code,422)
        self.assertEqual(self.client.post('/api/study/goals/'+str(uuid4())+'/proposals',headers=self.headers,json={'request_id':str(uuid4()),'start':NOW.isoformat(),'timezone':'not/a/zone'}).status_code,422)


if __name__=='__main__':unittest.main()
