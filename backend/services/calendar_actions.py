"""Per-user, approval-bound creation outbox. No owner-token fallback."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
from uuid import uuid4

from googleapiclient.errors import HttpError
from database import get_connection
from models.planning import BusyPeriod
from models.preferences import StudyPreferences
from services.google_oauth import GoogleOAuthService, OAuthUnavailable, ReconnectRequired
from services.planning import PlanningService, PlanningError, fingerprint, encoded
from services.scheduling_core import utc, overlaps


def audit(db,action_id,event):
    db.execute('INSERT INTO calendar_action_audit (action_id,event) VALUES (?,?)',(action_id,event))


class CalendarActions:
    def __init__(self,calendar=None,planner=None,clock=None):
        self.calendar=calendar or GoogleOAuthService()
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.planner=planner or PlanningService(self.calendar,self.clock)

    def proposal(self,user_id,proposal_id,db=None):
        if db is None:
            with closing(get_connection()) as connection:
                return self.proposal(user_id,proposal_id,connection)
        row=db.execute('SELECT * FROM study_proposals WHERE id=? AND telegram_user_id=?',(str(proposal_id),user_id)).fetchone()
        if not row: raise LookupError('Proposal not found.')
        return row

    def permission_link(self,user_id,proposal_id):
        row=self.proposal(user_id,proposal_id)
        if row['status']!='approved' or row['approved_digest']!=fingerprint(json.loads(row['proposal_json'])):
            raise PlanningError('Review and approve the exact proposal before requesting event permission.')
        return self.calendar.begin(user_id,write_access=True)

    def guard(self,db,user_id,row,digest):
        data=json.loads(row['proposal_json'])
        if row['status']!='approved' or row['approved_digest']!=digest or fingerprint(data)!=digest:
            raise PlanningError('The exact proposal is not approved or was changed.')
        connection=db.execute('SELECT generation,status FROM google_connections WHERE telegram_user_id=?',(user_id,)).fetchone()
        if not connection or connection['generation']!=row['calendar_generation'] or connection['status'] not in {'connected','expired'}:
            raise PlanningError('Calendar connection changed; review a fresh proposal after consent.')
        stored=db.execute('SELECT preferences_json FROM users WHERE telegram_user_id=?',(user_id,)).fetchone()
        values=json.loads(stored[0])
        known={key:value for key,value in values.items() if key in StudyPreferences.model_fields}
        if fingerprint(StudyPreferences.model_validate(known).model_dump(mode='json'))!=data['preferences_digest']:
            raise PlanningError('Preferences changed; a fresh proposal approval is required.')
        if not data['sessions'] or any(not s.get('title','').strip() for s in data['sessions']):
            raise PlanningError('Review a new proposal that includes exact event titles.')
        return data

    def approve(self,user_id,proposal_id,proposal_digest,request_id,approval_deadline=None):
        row=self.proposal(user_id,proposal_id)
        with closing(get_connection()) as db:
            data=self.guard(db,user_id,row,proposal_digest)
            previous=db.execute('SELECT * FROM calendar_write_actions WHERE proposal_id=? AND telegram_user_id=?',(str(proposal_id),user_id)).fetchone()
            if previous and (str(request_id) in json.loads(previous['approval_request_ids_json']) or previous['status']=='completed'):
                return self.record(user_id,previous['id'],db)
        now=utc(self.clock())
        if now>=datetime.fromisoformat(data['expires_at']) or now>=datetime.fromisoformat(data['sessions'][0]['start']):
            raise PlanningError('Proposal expired or already began; request a fresh draft.')
        # Consent and actual token permission are checked; no write occurs here.
        self.calendar.event_credentials(user_id,row['calendar_generation'])
        periods,generation,unused=self.planner.busy(user_id,datetime.fromisoformat(data['sessions'][0]['start']),datetime.fromisoformat(data['sessions'][-1]['end']),False)
        with closing(get_connection()) as db:
            old=db.execute('SELECT id FROM calendar_write_actions WHERE proposal_id=? AND telegram_user_id=?',(str(proposal_id),user_id)).fetchone()
            created=set()
            if old:
                created={r[0] for r in db.execute("SELECT session_index FROM calendar_event_intents WHERE action_id=? AND status='created'",(old['id'],))}
        remaining=[s for i,s in enumerate(data['sessions']) if i not in created]
        if any(overlaps(datetime.fromisoformat(s['start']),datetime.fromisoformat(s['end']),periods) for s in remaining):
            raise PlanningError('Calendar availability changed; review an adjustment before approving writes.')
        self.planner.validate_saved(user_id,row)
        expiry=min(now+timedelta(minutes=10),datetime.fromisoformat(data['expires_at']),datetime.fromisoformat(data['sessions'][0]['start']))
        if approval_deadline is not None:
            expiry=min(expiry,approval_deadline)
            if expiry<=now:raise PlanningError('Exact Calendar approval expired.')
        with closing(get_connection()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            current=self.proposal(user_id,proposal_id,db)
            self.guard(db,user_id,current,proposal_digest)
            action=db.execute('SELECT * FROM calendar_write_actions WHERE proposal_id=? AND telegram_user_id=?',(str(proposal_id),user_id)).fetchone()
            if action:
                requests=json.loads(action['approval_request_ids_json'])
                if str(request_id) in requests:return self.record(user_id,action['id'],db)
                if action['status']=='completed': return self.record(user_id,action['id'],db)
                if action['lease_until']>now.timestamp(): raise PlanningError('Calendar action is already executing. Check its result.')
                requests.append(str(request_id))
                db.execute("UPDATE calendar_write_actions SET approval_expires_at=?,approval_request_ids_json=?,status='ready',lease_until=0 WHERE id=?",(expiry.isoformat(),encoded(requests),action['id']))
                audit(db,action['id'],'approval_renewed')
                return self.record(user_id,action['id'],db)
            identifier=str(uuid4())
            db.execute('INSERT INTO calendar_write_actions (id,proposal_id,telegram_user_id,proposal_digest,connection_generation,approval_expires_at,approval_request_ids_json) VALUES (?,?,?,?,?,?,?)',
                       (identifier,str(proposal_id),user_id,proposal_digest,row['calendar_generation'],expiry.isoformat(),encoded([str(request_id)])))
            for index,session in enumerate(data['sessions']):
                event_id=hashlib.sha256(f'{user_id}:{proposal_id}:{proposal_digest}:{index}'.encode()).hexdigest()
                payload={'summary':session['title'],'start':{'dateTime':session['start']},'end':{'dateTime':session['end']},
                         'extendedProperties':{'private':{'study_action':identifier,'study_digest':proposal_digest,'study_owner':hashlib.sha256(str(user_id).encode()).hexdigest()}}}
                db.execute('INSERT INTO calendar_event_intents (action_id,session_index,google_event_id,payload_json) VALUES (?,?,?,?)',(identifier,index,event_id,encoded(payload)))
            audit(db,identifier,'explicit_calendar_approval')
            return self.record(user_id,identifier,db)

    def record(self,user_id,action_id,db=None):
        if db is None:
            with closing(get_connection()) as connection:return self.record(user_id,action_id,connection)
        row=db.execute('SELECT * FROM calendar_write_actions WHERE id=? AND telegram_user_id=?',(str(action_id),user_id)).fetchone()
        if not row: raise LookupError('Calendar action not found.')
        events=db.execute('SELECT session_index,status FROM calendar_event_intents WHERE action_id=? ORDER BY session_index',(str(action_id),)).fetchall()
        return {'id':row['id'],'proposal_id':row['proposal_id'],'status':row['status'],'approval_expires_at':row['approval_expires_at'],
                'sessions':[dict(event) for event in events], 'calendar_target':'primary'}

    def list(self,user_id):
        with closing(get_connection()) as db:
            identifiers=[row[0] for row in db.execute('SELECT id FROM calendar_write_actions WHERE telegram_user_id=? ORDER BY created_at DESC LIMIT 100',(user_id,))]
            return [self.record(user_id,identifier,db) for identifier in identifiers]

    @staticmethod
    def matches(remote,intent):
        if not remote or remote.get('status')=='cancelled' or remote.get('id')!=intent['google_event_id']: return False
        expected=json.loads(intent['payload_json'])
        try:
            return (remote.get('summary')==expected['summary'] and
                    remote.get('extendedProperties',{}).get('private')==expected['extendedProperties']['private'] and
                    datetime.fromisoformat(remote['start']['dateTime'].replace('Z','+00:00'))==datetime.fromisoformat(expected['start']['dateTime']) and
                    datetime.fromisoformat(remote['end']['dateTime'].replace('Z','+00:00'))==datetime.fromisoformat(expected['end']['dateTime']))
        except (KeyError,ValueError,TypeError): return False

    def execute(self,user_id,action_id):
        now=utc(self.clock())
        lease_token=str(uuid4())
        with closing(get_connection()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            action=db.execute('SELECT * FROM calendar_write_actions WHERE id=? AND telegram_user_id=?',(str(action_id),user_id)).fetchone()
            if not action: raise LookupError('Calendar action not found.')
            if action['status']=='completed': return self.record(user_id,action_id,db)
            if action['lease_until']>now.timestamp(): return self.record(user_id,action_id,db)
            self.guard(db,user_id,self.proposal(user_id,action['proposal_id'],db),action['proposal_digest'])
            db.execute("UPDATE calendar_write_actions SET status='processing',lease_until=?,lease_token=? WHERE id=?",(now.timestamp()+120,lease_token,str(action_id)))
            audit(db,str(action_id),'execution_claimed')
        try:
            self.calendar.event_credentials(user_id,action['connection_generation'])
            with closing(get_connection()) as db:
                intents=db.execute('SELECT * FROM calendar_event_intents WHERE action_id=? ORDER BY session_index',(str(action_id),)).fetchall()
            # Reconcile known stable IDs before considering any insertion. This is
            # read-only remotely and may finish a lost-response action after expiry.
            for intent in intents:
                if intent['status']=='created': continue
                remote=self.calendar.event_call(user_id,action['connection_generation'],lambda provider,value:provider.get_event(value,intent['google_event_id']))
                if remote:
                    if not self.matches(remote,intent): raise PlanningError('Existing event differs from the approved payload. No overwrite permitted.')
                    self.mark_created(str(action_id),intent['session_index'])
            with closing(get_connection()) as db:
                remaining=db.execute("SELECT * FROM calendar_event_intents WHERE action_id=? AND status!='created' ORDER BY session_index",(str(action_id),)).fetchall()
            if remaining:
                if utc(self.clock())>=datetime.fromisoformat(action['approval_expires_at']):
                    raise PlanningError('Calendar approval expired. Review and explicitly approve creation again.')
                row=self.proposal(user_id,action['proposal_id'])
                data=json.loads(row['proposal_json'])
                self.planner.validate_saved(user_id,row)
                periods,generation,unused=self.planner.busy(user_id,datetime.fromisoformat(data['sessions'][0]['start']),datetime.fromisoformat(data['sessions'][-1]['end']),False)
                for intent in remaining:
                    expected=json.loads(intent['payload_json'])
                    a,b=datetime.fromisoformat(expected['start']['dateTime']),datetime.fromisoformat(expected['end']['dateTime'])
                    if overlaps(a,b,periods): raise PlanningError('Calendar conflict detected; no further events created.')
                    with closing(get_connection()) as db,db:
                        self.guard(db,user_id,self.proposal(user_id,action['proposal_id'],db),action['proposal_digest'])
                        claim=db.execute('SELECT lease_token,status FROM calendar_write_actions WHERE id=?',(str(action_id),)).fetchone()
                        if claim['lease_token']!=lease_token or claim['status']!='processing': raise PlanningError('Another worker owns this action. Check its result.')
                        db.execute('UPDATE calendar_write_actions SET lease_until=? WHERE id=? AND lease_token=?',(utc(self.clock()).timestamp()+120,str(action_id),lease_token))
                    if utc(self.clock())>=datetime.fromisoformat(action['approval_expires_at']): raise PlanningError('Calendar approval expired.')
                    try:
                        remote=self.calendar.event_call(user_id,action['connection_generation'],lambda provider,value:provider.create_event(value,intent['google_event_id'],expected))
                    except HttpError as error:
                        if error.resp.status!=409: raise
                        remote=self.calendar.event_call(user_id,action['connection_generation'],lambda provider,value:provider.get_event(value,intent['google_event_id']))
                    if not self.matches(remote,intent): raise PlanningError('Calendar did not confirm the approved event. No overwrite permitted.')
                    self.mark_created(str(action_id),intent['session_index'])
            with closing(get_connection()) as db,db:
                changed=db.execute("UPDATE calendar_write_actions SET status='completed',lease_until=0 WHERE id=? AND lease_token=?",(str(action_id),lease_token))
                if changed.rowcount:audit(db,str(action_id),'execution_completed')
        except (PlanningError,OAuthUnavailable,ReconnectRequired):
            with closing(get_connection()) as db,db:
                changed=db.execute("UPDATE calendar_write_actions SET status='blocked',lease_until=0 WHERE id=? AND lease_token=?",(str(action_id),lease_token))
                if changed.rowcount:audit(db,str(action_id),'execution_blocked')
            raise
        except Exception:
            # The remote result may be unknown. Preserve IDs/payloads for explicit
            # retry/reconciliation; never manufacture a replacement action/event.
            with closing(get_connection()) as db,db:
                changed=db.execute("UPDATE calendar_write_actions SET status='partial',lease_until=0 WHERE id=? AND lease_token=?",(str(action_id),lease_token))
                if changed.rowcount:
                    db.execute("UPDATE calendar_event_intents SET status='uncertain' WHERE action_id=? AND status='pending'",(str(action_id),))
                    audit(db,str(action_id),'network_or_recording_uncertain')
        return self.record(user_id,action_id)

    def mark_created(self,action_id,index):
        with closing(get_connection()) as db,db:
            db.execute("UPDATE calendar_event_intents SET status='created' WHERE action_id=? AND session_index=?",(action_id,index))
            audit(db,action_id,'event_confirmed')
