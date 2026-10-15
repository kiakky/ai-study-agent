"""Temporary tester-only draft preparation; no consent, approval or event execution."""
from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL

from database import get_connection
from models.planning import StudyGoal, ProposalInput
from services import profiles
from services.planning import PlanningService, fingerprint, PlanningError

SETTINGS_PATH = Path(__file__).resolve().parents[2] / 'config' / 'calendar_test.json'
TEST_SUBJECT = 'AI Study Agent Calendar TEST'


class CalendarTestUnavailable(ValueError):
    pass


def configured_testers():
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding='utf-8-sig'))
        if set(data) != {'enabled', 'telegram_user_ids'} or type(data['enabled']) is not bool:
            raise ValueError()
        identities = data['telegram_user_ids']
        if not isinstance(identities, list) or not 1 <= len(identities) <= 20 or any(type(value) is not int or value <= 0 for value in identities):
            raise ValueError()
        return set(identities) if data['enabled'] else set()
    except (OSError, ValueError, TypeError, KeyError):
        return set()


class CalendarTestService:
    def __init__(self, planner=None, testers=None):
        self.planner = planner or PlanningService()
        self.testers = configured_testers() if testers is None else set(testers)

    def prepare(self, user_id, notes_confirmed=False, sessions=1):
        if sessions not in (1,3): raise CalendarTestUnavailable("Test batches contain exactly three sessions.")
        if type(user_id) is not int or user_id not in self.testers:
            raise CalendarTestUnavailable('Temporary Calendar testing is unavailable for this account.')
        profile = profiles.get_profile(user_id)
        prefs = profile['preferences']
        if prefs['notes'].strip() and not notes_confirmed:
            raise CalendarTestUnavailable('Free-text notes are not interpreted automatically. First map all restrictions into your saved days/time windows. Only if fully covered, use /calendartest confirm-notes; otherwise do not proceed.')
        generation = self.planner.generation(user_id)
        if generation is None:
            raise CalendarTestUnavailable('Connect your own Google Calendar in Setup first.')
        goal = self.planner.save_goal(user_id, StudyGoal(subject=TEST_SUBJECT), uuid5(NAMESPACE_URL, 'calendar-test-goal:'+str(user_id)+(':'+str(sessions) if sessions!=1 else '')))
        now = self.planner.clock()
        with closing(get_connection()) as db:
            row = db.execute('SELECT * FROM study_proposals WHERE telegram_user_id=? AND goal_id=? ORDER BY rowid DESC LIMIT 1', (user_id,goal['id'])).fetchone()
            if row:
                action = db.execute('SELECT status FROM calendar_write_actions WHERE proposal_id=? AND telegram_user_id=?', (row['id'],user_id)).fetchone()
                if action and action['status'] in {'processing','partial','completed'}:
                    return self.planner.proposal_record(row)
                data = json.loads(row['proposal_json'])
                if (row['status'] in {'draft','approved'} and row['calendar_generation']==generation
                        and data['preferences_digest']==fingerprint(prefs)
                        and all(s.get('title')=='Study: '+TEST_SUBJECT for s in data['sessions'])
                        and datetime.fromisoformat(data['expires_at'])>now and datetime.fromisoformat(data['sessions'][0]['start'])>now):
                    return self.planner.proposal_record(row)
        # Deterministic retry key; conflicting concurrent drafts fail closed.
        key = f"calendar-test:{user_id}:{generation}:{fingerprint(prefs)}:{row['id'] if row else 'first'}"
        request = ProposalInput(request_id=uuid5(NAMESPACE_URL,key), start=now+timedelta(hours=1),
                                sessions=sessions,timezone=profile['timezone'],reviewed_notes=prefs['notes'],
                                restrictions_confirmed=notes_confirmed)
        replacement = row['id'] if row and row['status'] in {'draft','approved'} else None
        return self.planner.propose(user_id,goal['id'],request,replacement)
