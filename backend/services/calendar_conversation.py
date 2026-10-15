"""Exact Telegram reviews. OAuth alone is never an approval.

Only a review explicitly approved for the account selected in its bound consent
flow can continue after that flow. Generic reconnections never rebind actions.
"""
from contextlib import closing
from datetime import datetime, timedelta
import json
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from database import get_connection
from services.calendar_actions import CalendarActions
from services.google_oauth import digest
from services.planning import PlanningError, fingerprint


def session_text(session, zone):
    start = datetime.fromisoformat(session['start'].replace('Z', '+00:00')).astimezone(ZoneInfo(zone))
    end = datetime.fromisoformat(session['end'].replace('Z', '+00:00')).astimezone(ZoneInfo(zone))
    return (f"{session['title']}\n{start:%a %d %b %Y, %H:%M} to {end:%d %b %Y, %H:%M} "
            f"({zone}) - {int((end-start).total_seconds()/60)} minutes")


class CalendarConversation:
    def __init__(self, actions=None):
        self.actions = actions or CalendarActions()
        self.calendar = self.actions.calendar
        self.planner = self.actions.planner
        self.clock = self.actions.clock

    def get(self, user, identifier, db=None):
        if db is None:
            with closing(get_connection()) as connection:
                return self.get(user, identifier, connection)
        row = db.execute('SELECT * FROM calendar_reviews WHERE id=? AND telegram_user_id=?', (str(identifier), user)).fetchone()
        if not row:
            raise LookupError('Calendar review not found.')
        return dict(row)

    def prepare(self, user, proposal_id):
        row = self.actions.proposal(user, proposal_id)
        data = json.loads(row['proposal_json'])
        if row['status'] not in {'draft', 'approved'}:
            raise PlanningError('This proposal is no longer available.')
        if row['calendar_generation'] is None:
            raise PlanningError('Connect your own Google Calendar before approving Calendar events.')
        if row['calendar_generation'] != self.planner.generation(user):
            raise PlanningError('The connected account changed. Request a fresh review before creating events.')
        expiry = min(self.clock()+timedelta(minutes=10), datetime.fromisoformat(data['expires_at']),
                     datetime.fromisoformat(data['sessions'][0]['start']))
        if expiry <= self.clock():
            raise PlanningError('This proposal expired. Request a new proposal.')
        identifier = str(uuid4())
        with closing(get_connection()) as db, db:
            db.execute('INSERT INTO calendar_reviews (id,telegram_user_id,proposal_id,proposal_digest,connection_generation,expires_at) VALUES (?,?,?,?,?,?)',
                       (identifier,user,str(proposal_id),fingerprint(data),row['calendar_generation'],expiry.isoformat()))
        return self.get(user, identifier), data

    def presented(self, user, identifier):
        with closing(get_connection()) as db, db:
            db.execute("UPDATE calendar_reviews SET presented=1 WHERE id=? AND telegram_user_id=? AND status='pending'", (identifier,user))

    def validate(self, user, review):
        row = self.actions.proposal(user,review['proposal_id'])
        if fingerprint(json.loads(row['proposal_json'])) != review['proposal_digest']:
            raise PlanningError('The proposal changed. A fresh review and approval are required.')
        if self.clock() >= datetime.fromisoformat(review['expires_at']):
            raise PlanningError('This exact-action approval expired. Open /studyplan to review again.')
        if row['status'] not in {'draft','approved'}:
            raise PlanningError('This proposal is no longer active.')
        self.planner.validate_saved(user,row)
        return row

    def decide(self, user, identifier, decision):
        if decision not in {'approve','reject','changes'}:
            raise PlanningError('Unsupported decision.')
        review = self.get(user, identifier)
        if review['status'] != 'pending':
            # A repeated button cannot extend approval or create a second action.
            return review
        row = self.validate(user,review)
        if not review['presented']:
            raise PlanningError('Review every exact session before approving.')
        if self.planner.generation(user) != review['connection_generation']:
            raise PlanningError('Calendar connection changed. Review the current connection first.')
        if decision != 'approve':
            with closing(get_connection()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                action = db.execute('SELECT status FROM calendar_write_actions WHERE proposal_id=? AND telegram_user_id=?', (row['id'],user)).fetchone()
                if action:
                    raise PlanningError('This plan already has a Calendar action. Manage individual sessions; rejection cannot undo events.')
                db.execute("UPDATE calendar_reviews SET status=?,notified=1 WHERE proposal_id=? AND telegram_user_id=? AND status IN ('pending','approved','consent')",
                           ('rejected' if decision=='reject' else 'changes',row['id'],user))
                db.execute("UPDATE study_proposals SET status='rejected',approved_digest=NULL WHERE id=? AND telegram_user_id=?", (row['id'],user))
            return self.get(user,identifier)
        status=self.calendar.status(user)
        needs_consent=not status['event_permission_granted'] or status['status'] not in {'connected','expired'}
        if not needs_consent:
            self.planner.decide(user,review['proposal_id'],'approve')
        with closing(get_connection()) as db, db:
            db.execute("UPDATE calendar_reviews SET status='approved',approved_at=? WHERE id=? AND telegram_user_id=? AND status='pending'", (self.clock().isoformat(),identifier,user))
        if needs_consent:
            link = self.calendar.begin(user,write_access=True)
            ticket = parse_qs(urlsplit(link['authorization_start_url']).query)['ticket'][0]
            with closing(get_connection()) as db, db:
                request = db.execute('SELECT state_hash FROM google_oauth_requests WHERE ticket_hash=? AND telegram_user_id=?', (digest(ticket),user)).fetchone()
                db.execute("UPDATE calendar_reviews SET status='consent',oauth_state_hash=? WHERE id=? AND telegram_user_id=? AND status='approved'", (request['state_hash'],identifier,user))
            return {**self.get(user,identifier), **link}
        return self.execute(user,identifier)

    def execute(self, user, identifier):
        review = self.get(user,identifier)
        if review['status'] not in {'approved','partial','processing'}:
            return review
        if review['action_id']:
            # Outbox reconciliation can confirm a lost response after expiry;
            # its immutable approval deadline still blocks every new write.
            action=self.actions.record(user,review['action_id'])
        else:
            self.validate(user,review)
            self.planner.decide(user,review['proposal_id'],'approve')
            action = self.actions.approve(user,review['proposal_id'],review['proposal_digest'],identifier,
                                          approval_deadline=datetime.fromisoformat(review['expires_at']))
            with closing(get_connection()) as db, db:
                db.execute('UPDATE calendar_reviews SET action_id=? WHERE id=? AND telegram_user_id=?', (action['id'],identifier,user))
        result = self.actions.execute(user,action['id'])
        with closing(get_connection()) as db, db:
            db.execute('UPDATE calendar_reviews SET status=? WHERE id=? AND telegram_user_id=?', (result['status'],identifier,user))
        return self.get(user,identifier)

    def resume_consent(self, user, identifier):
        review = self.get(user,identifier)
        if review['status'] != 'consent':
            return review
        self.validate(user,review)
        with closing(get_connection()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            request = db.execute('SELECT phase,generation FROM google_oauth_requests WHERE state_hash=? AND telegram_user_id=?', (review['oauth_state_hash'],user)).fetchone()
            connection = db.execute('SELECT generation,status FROM google_connections WHERE telegram_user_id=?',(user,)).fetchone()
            if not request or request['phase']=='failed':
                db.execute("UPDATE calendar_reviews SET status='cancelled',error='Google consent was cancelled or failed. Previous access was retained.' WHERE id=?", (identifier,))
                return self.get(user,identifier,db)
            if request['phase'] != 'completed':
                return review
            if (request['generation'] != review['connection_generation'] or connection['generation'] != request['generation']+1
                    or connection['status']!='connected'):
                raise PlanningError('A different connection replaced the approved consent flow. Fresh approval is required.')
            row = self.actions.proposal(user,review['proposal_id'],db)
            if (row['status'] not in {'draft','approved'} or (row['status']=='approved' and row['approved_digest']!=review['proposal_digest'])
                    or fingerprint(json.loads(row['proposal_json']))!=review['proposal_digest']):
                raise PlanningError('Proposal changed during consent.')
            # No already-dispatched action may be transferred to a different grant.
            if db.execute('SELECT 1 FROM calendar_write_actions WHERE proposal_id=?',(row['id'],)).fetchone():
                raise PlanningError('An existing Calendar action cannot be transferred after consent.')
            db.execute('UPDATE study_proposals SET calendar_generation=? WHERE id=? AND telegram_user_id=?', (connection['generation'],row['id'],user))
            db.execute("UPDATE calendar_reviews SET status='approved',connection_generation=? WHERE id=? AND status='consent'", (connection['generation'],identifier))
        self.planner.decide(user,review['proposal_id'],'approve')
        return self.execute(user,identifier)

    def fail(self, user, identifier, message):
        with closing(get_connection()) as db, db:
            db.execute("UPDATE calendar_reviews SET status='blocked',error=? WHERE id=? AND telegram_user_id=? AND status NOT IN ('completed','rejected','changes')",(message,identifier,user))

    def pending_consent(self):
        with closing(get_connection()) as db:
            return [dict(row) for row in db.execute("SELECT id,telegram_user_id FROM calendar_reviews WHERE status='consent' OR (status IN ('completed','blocked','cancelled','partial') AND notified=0)")]

    def notified(self,user,identifier):
        with closing(get_connection()) as db,db:
            db.execute('UPDATE calendar_reviews SET notified=1 WHERE id=? AND telegram_user_id=?',(identifier,user))

    def consume_adjustment(self,user,text):
        with closing(get_connection()) as db,db:
            row=db.execute("SELECT id FROM calendar_reviews WHERE telegram_user_id=? AND status='changes' AND adjustment_text IS NULL ORDER BY rowid DESC LIMIT 1",(user,)).fetchone()
            if not row:return False
            if not isinstance(text,str) or not text.strip() or len(text)>2000:
                raise PlanningError('Describe the requested change in 1 to 2,000 characters.')
            db.execute('UPDATE calendar_reviews SET adjustment_text=? WHERE id=? AND telegram_user_id=?',(text.strip(),row['id'],user))
        return True

    def adjustments(self,user):
        with closing(get_connection()) as db:
            return [dict(row) for row in db.execute("SELECT proposal_id,adjustment_text FROM calendar_reviews WHERE telegram_user_id=? AND status='changes' AND adjustment_text IS NOT NULL ORDER BY rowid DESC LIMIT 100",(user,))]
