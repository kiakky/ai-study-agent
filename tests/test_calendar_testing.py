from contextlib import closing
import json
from pathlib import Path
from unittest.mock import patch
import unittest

import test_calendar_actions as action_tests
from services.calendar_testing import CalendarTestService,CalendarTestUnavailable,configured_testers
from services import profiles
from models.preferences import StudyPreferences
from uuid import uuid4
import database


class CalendarTestingTests(unittest.TestCase):
    setUp = action_tests.CalendarActionTests.setUp
    connect = action_tests.CalendarActionTests.connect

    def service(self):
        return CalendarTestService(self.planner,{101,202})

    def test_allowlist_denies_before_calendar_access_and_fails_closed(self):
        self.assertRaises(CalendarTestUnavailable,self.service().prepare,303)
        self.assertEqual(self.provider.reads,[])
        path=Path(self.folder.name)/'testers.json'
        with patch('services.calendar_testing.SETTINGS_PATH',path):
            self.assertEqual(configured_testers(),set())
            path.write_text(json.dumps({'enabled':False,'telegram_user_ids':[101]}))
            self.assertEqual(configured_testers(),set())
            path.write_text(json.dumps({'enabled':True,'telegram_user_ids':[101]}))
            self.assertEqual(configured_testers(),{101})
            path.write_text(json.dumps({'enabled':True,'telegram_user_ids':[True]}))
            self.assertEqual(configured_testers(),set())

    def test_two_testers_prepare_separate_drafts_no_approval_or_event(self):
        a=self.service().prepare(101);b=self.service().prepare(202)
        self.assertNotEqual(a['id'],b['id'])
        self.assertEqual(a['status'],'draft')
        self.assertEqual(len(a['sessions']),1)
        self.assertIn('TEST',a['sessions'][0]['title'])
        self.assertEqual(a['calendar_availability'],'checked')
        self.assertEqual(self.service().prepare(101)['id'],a['id'])
        self.assertEqual(self.provider.inserts,[])
        self.assertEqual(self.actions.list(101),[])
        self.assertEqual(len(self.planner.proposals(101)),1)

    def test_notes_are_not_silently_understood(self):
        profiles.save_preferences(101,StudyPreferences(study_windows=[{'start':'18:00','end':'20:00'}],notes='No mornings'))
        self.assertRaises(CalendarTestUnavailable,self.service().prepare,101)
        self.assertEqual(self.provider.reads,[])
        proposal=self.service().prepare(101,True)
        self.assertEqual(proposal['constraints']['reviewed_notes'],'No mornings')
        self.assertTrue(proposal['constraints']['restrictions_confirmed'])

    def test_consent_requires_new_draft_and_completed_test_is_reused(self):
        self.connect(101,'a',False)
        first=self.service().prepare(101)
        self.planner.decide(101,first['id'],'approve')
        self.connect(101,'a',True)
        second=self.service().prepare(101)
        self.assertNotEqual(first['id'],second['id'])
        second=self.planner.decide(101,second['id'],'approve')
        action=self.actions.approve(101,second['id'],second['proposal_digest'],uuid4())
        self.actions.execute(101,action['id'])
        self.assertEqual(self.service().prepare(101)['id'],second['id'])
        self.assertEqual(len(self.provider.inserts),1)
        self.assertEqual(self.actions.execute(101,action['id'])['status'],'completed')
        self.assertEqual(len(self.provider.inserts),1)

    def test_calendar_unavailable_never_falls_back_or_saves_a_draft(self):
        self.calendar.disconnect(101)
        self.assertRaises(Exception,self.service().prepare,101)
        self.assertEqual(self.planner.proposals(101),[])
        self.assertEqual(self.provider.inserts,[])


if __name__=='__main__':unittest.main()
