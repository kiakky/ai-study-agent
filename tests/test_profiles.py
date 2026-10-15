from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import database
from models.exams import ExamInformation
from models.preferences import StudyPreferences
from services import profiles


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='quiz-test-', dir=ROOT / 'tests')
        self.addCleanup(self.folder.cleanup)
        self.database_patch = patch.object(database, 'DB_PATH', Path(self.folder.name) / 'profile.db')
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        database.initialize_database(backfill_approvals=False)

    def test_first_start_resume_and_completion_are_persistent(self):
        self.assertTrue(profiles.ensure_profile(101))
        self.assertFalse(profiles.ensure_profile(101))
        self.assertEqual(profiles.get_profile(101)['onboarding']['stage'], 'google')
        profiles.choose_integration(101, 'google', 'skipped')
        self.assertEqual(profiles.get_profile(101)['onboarding']['stage'], 'brightspace')
        profiles.choose_integration(101, 'brightspace', 'skipped')
        saved = profiles.save_preferences(101, StudyPreferences(study_days=['mon'], session_minutes=15))
        self.assertEqual(saved['onboarding']['stage'], 'completed')
        completion = saved['onboarding']['completed_at']
        database.initialize_database(backfill_approvals=False)
        profiles.choose_integration(101, 'google', 'skipped')
        profiles.choose_integration(101, 'brightspace', 'skipped')
        self.assertEqual(profiles.get_profile(101)['onboarding']['completed_at'], completion)

    def test_legacy_preferences_and_student_isolation(self):
        profiles.ensure_profile(101)
        with closing(database.get_connection()) as connection, connection:
            connection.execute('UPDATE users SET preferences_json=? WHERE telegram_user_id=101', (json.dumps({'legacy_preference': 'preserve me'}),))
        profiles.save_preferences(101, StudyPreferences(notes='Short weekday practice', question_count=12))
        with closing(database.get_connection()) as connection:
            saved = json.loads(connection.execute('SELECT preferences_json FROM users WHERE telegram_user_id=101').fetchone()[0])
            self.assertEqual(saved['legacy_preference'], 'preserve me')
        self.assertEqual(profiles.get_profile(202)['preferences']['notes'], '')
        self.assertEqual(profiles.get_profile(101)['preferences']['question_count'], 12)

    def test_preference_validation_and_no_false_plan(self):
        for invalid in [{'study_days': ['mon','mon']}, {'session_minutes': 0}, {'question_count': True},
                        {'practice_format': 'invalid'}, {'study_windows': [{'start': '18:00','end':'18:00'}]}]:
            with self.subTest(invalid=invalid):
                self.assertRaises(ValueError, StudyPreferences.model_validate, invalid)
        self.assertEqual(profiles.current_study_plan(101), {'plans': [], 'state': 'not_generated', 'planning_available': True})
        self.assertFalse(profiles.get_profile(101)['brightspace']['available'])

    def test_exam_information_requires_sources_and_keeps_unknown_fields_missing(self):
        exam = ExamInformation(course_id='sample', course_name='Synthetic course')
        self.assertEqual(exam.confirmed_start.status, 'missing')
        evidence = {'source_reference': 'synthetic-syllabus', 'excerpt': 'Assessment is 40 percent.',
                    'retrieved_at': datetime.now(timezone.utc).isoformat()}
        known = ExamInformation(course_id='sample', course_name='Synthetic course',
                                module_weight_percent={'value':40,'status':'confirmed','evidence':[evidence]})
        self.assertEqual(known.module_weight_percent.value, 40)
        for invalid in [{'value':False,'status':'confirmed'}, {'value':True,'status':'missing'},
                        {'status':'conflicting','evidence':[evidence],'uncertainty':'Two sources disagree'}]:
            self.assertRaises(ValueError, ExamInformation, course_id='sample', course_name='Synthetic course', final_exam_exists=invalid)
        self.assertRaises(ValueError, ExamInformation, course_id='sample', course_name='Synthetic course',
                          module_weight_percent={'value':200,'status':'confirmed','evidence':[evidence]})


if __name__ == '__main__':
    unittest.main()
