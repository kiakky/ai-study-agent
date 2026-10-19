"""Frozen, explicitly approved changes to one verifiably agent-created event.

Original creation intents are immutable. Completed changes form a per-session
history; compare-and-swap Google ETags protect concurrent personal edits.
"""
from contextlib import closing
from datetime import datetime, timedelta
import json
from uuid import uuid4
from zoneinfo import ZoneInfo

from database import get_connection
from services import profiles
from services.calendar_actions import CalendarActions, audit
from services.planning import PlanningError, fingerprint, encoded
from services.scheduling_core import validate_periods, overlaps
from models.planning import BusyPeriod, ProposedSession
from models.preferences import StudyPreferences


class CalendarSessionManagement:
    def __init__(self, actions=None):
        self.actions=actions or CalendarActions()
        self.calendar=self.actions.calendar
        self.planner=self.actions.planner
        self.clock=self.actions.clock

    def session(self,user,action_id,index,db=None):
        if db is None:
            with closing(get_connection()) as connection:return self.session(user,action_id,index,connection)
        action=self.actions.record(user,action_id,db)
        intent=db.execute("SELECT * FROM calendar_event_intents WHERE action_id=? AND session_index=? AND status='created'",(str(action_id),index)).fetchone()
        if not intent:raise LookupError('Confirmed study session not found.')
        current=dict(intent)
        latest=db.execute("SELECT kind,new_payload_json FROM calendar_session_changes WHERE action_id=? AND session_index=? AND status='completed' ORDER BY rowid DESC LIMIT 1",(str(action_id),index)).fetchone()
        current['cancelled']=bool(latest and latest['kind']=='cancel')
        if latest and latest['kind']=='reschedule':current['payload_json']=latest['new_payload_json']
        return current

    def get(self,user,identifier,db=None):
        if db is None:
            with closing(get_connection()) as connection:return self.get(user,identifier,connection)
        row=db.execute('SELECT * FROM calendar_session_changes WHERE id=? AND telegram_user_id=?',(str(identifier),user)).fetchone()
        if not row:raise LookupError('Session change not found.')
        return dict(row)

    def list_sessions(self,user):
        result=[]
        for action in self.actions.list(user):
            proposal=json.loads(self.actions.proposal(user,action['proposal_id'])['proposal_json'])
            for event in action['sessions']:
                if event['status']!='created':continue
                current=self.session(user,action['id'],event['session_index'])
                body=json.loads(current['payload_json'])
                result.append({'action_id':action['id'],'session_index':event['session_index'],
                    'title':body['summary'],'start':body['start']['dateTime'],'end':body['end']['dateTime'],
                    'timezone':proposal['timezone'],'status':'cancelled' if current['cancelled'] else 'scheduled'})
        return result

    def validate_move(self,user,body,original,proposal):
        prefs=profiles.get_profile(user)['preferences']
        data=json.loads(proposal['proposal_json'])
        if fingerprint(prefs)!=data['preferences_digest']:
            raise PlanningError('Preferences changed. Review the new restrictions before rescheduling.')
        session={'title':body['summary'],'start':body['start']['dateTime'],'end':body['end']['dateTime']}
        ProposedSession.model_validate(session)
        start,end=datetime.fromisoformat(session['start']),datetime.fromisoformat(session['end'])
        old_start=datetime.fromisoformat(original['start']['dateTime'])
        old_end=datetime.fromisoformat(original['end']['dateTime'])
        if start<old_end and end>old_start:
            raise PlanningError('Choose a slot outside the original event. Free/busy cannot safely distinguish overlapping personal events.')
        if start<=self.clock() or start-self.clock()>timedelta(days=90):raise PlanningError('Choose a future slot within 90 days.')
        if end-start!=old_end-old_start:raise PlanningError('Keep the approved session duration unchanged.')
        periods,unused,state=self.planner.busy(user,start,end,False)
        constraints=dict(data['constraints'])
        # The exact selected move is a proposal-local exception to soft defaults,
        # validated both now and again by the unchanged approved-change executor.
        zone=ZoneInfo(data['timezone'])
        first=start.astimezone(zone);last=end.astimezone(zone)
        days=list(dict.fromkeys(('mon','tue','wed','thu','fri','sat','sun')[d.weekday()] for d in (first,last-timedelta(microseconds=1))))
        patch={'study_days':days,'study_windows':[{'start':first.strftime('%H:%M'),'end':last.strftime('%H:%M')}],
            'session_minutes':int((end-start).total_seconds()/60)}
        constraints['excluded_days']=[d for d in constraints['excluded_days'] if d not in days]
        constraints['session_overrides']={'0':patch}
        periods += [BusyPeriod.model_validate(p) for p in constraints['excluded_periods']]
        for other in self.list_sessions(user):
            if other['status']=='scheduled':periods.append(BusyPeriod.model_validate(other))
        validate_periods([session],start,end,zone,prefs,int((end-start).total_seconds()/60),constraints['excluded_days'],periods,{0:patch})
        # Preserve target/exam boundaries and other saved proposal constraints.
        synthetic=dict(proposal)
        moved=dict(data)
        moved['sessions']=[session]
        moved['constraints']=constraints
        synthetic['proposal_json']=encoded(moved)
        self.planner.validate_saved(user,synthetic)

    def propose(self,user,action_id,index,kind,start=None,end=None):
        if kind not in {'reschedule','cancel'}:raise PlanningError('Unsupported session change.')
        current=self.session(user,action_id,index)
        if current['cancelled']:raise PlanningError('This study session is already cancelled.')
        original=json.loads(current['payload_json'])
        generation=self.planner.generation(user)
        remote=self.calendar.event_call(user,generation,lambda p,v:p.get_event(v,current['google_event_id']))
        if not self.actions.matches(remote,current) or not remote.get('etag'):
            raise PlanningError('The original agent event cannot be verified or was changed externally. No modification allowed.')
        proposal=self.actions.proposal(user,self.actions.record(user,action_id)['proposal_id'])
        body=None
        if kind=='reschedule':
            body={**original,'start':{'dateTime':start},'end':{'dateTime':end}}
            self.validate_move(user,body,original,proposal)
        identifier=str(uuid4())
        preference_digest=fingerprint(profiles.get_profile(user)['preferences'])
        with closing(get_connection()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            saved=json.loads(db.execute('SELECT preferences_json FROM users WHERE telegram_user_id=?',(user,)).fetchone()[0])
            prefs=StudyPreferences.model_validate({k:v for k,v in saved.items() if k in StudyPreferences.model_fields}).model_dump(mode='json')
            latest=self.session(user,str(action_id),index,db)
            if latest['cancelled'] or json.loads(latest['payload_json'])!=original or fingerprint(prefs)!=preference_digest:
                raise PlanningError('Session or preferences changed during validation; request a fresh review.')
            if db.execute("SELECT 1 FROM calendar_session_changes WHERE telegram_user_id=? AND action_id=? AND session_index=? AND status IN ('approved','processing','uncertain')",(user,str(action_id),index)).fetchone():
                raise PlanningError('An approved or uncertain change for this session must be resolved first.')
            db.execute("UPDATE calendar_session_changes SET status='rejected',error='Superseded by a newer exact session-change review.' WHERE telegram_user_id=? AND action_id=? AND session_index=? AND status='pending'",(user,str(action_id),index))
            db.execute('INSERT INTO calendar_session_changes (id,telegram_user_id,action_id,session_index,kind,old_payload_json,new_payload_json,remote_etag,connection_generation,preferences_digest,timezone,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (identifier,user,str(action_id),index,kind,encoded(original),encoded(body) if body else None,remote['etag'],generation,
                 preference_digest,json.loads(proposal['proposal_json'])['timezone'],(self.clock()+timedelta(minutes=10)).isoformat()))
            audit(db,str(action_id),'session_change_proposed')
        return self.get(user,identifier)

    def decide(self,user,identifier,decision):
        if decision not in {'approve','reject'}:raise PlanningError('Unsupported decision.')
        with closing(get_connection()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=self.get(user,identifier,db)
            if row['status']!='pending':return row
            if self.clock()>=datetime.fromisoformat(row['expires_at']):raise PlanningError('This change expired. Review a new proposal.')
            current=self.session(user,row['action_id'],row['session_index'],db)
            if current['cancelled'] or json.loads(current['payload_json'])!=json.loads(row['old_payload_json']):
                raise PlanningError('This session changed. Review its current details.')
            if decision=='approve' and db.execute("SELECT 1 FROM calendar_session_changes WHERE action_id=? AND session_index=? AND status IN ('approved','processing','uncertain')",(row['action_id'],row['session_index'])).fetchone():
                raise PlanningError('Another change for this session is unresolved. Check its result first.')
            db.execute('UPDATE calendar_session_changes SET status=? WHERE id=?',('approved' if decision=='approve' else 'rejected',identifier))
            audit(db,row['action_id'],'session_change_'+decision)
        return self.execute(user,identifier) if decision=='approve' else self.get(user,identifier)

    def execute(self,user,identifier):
        lease=str(uuid4())
        with closing(get_connection()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=self.get(user,identifier,db)
            if row['status'] not in {'approved','processing','uncertain'}:return row
            if row['lease_until']>self.clock().timestamp():return row
            db.execute("UPDATE calendar_session_changes SET status='processing',lease_token=?,lease_until=? WHERE id=?",(lease,self.clock().timestamp()+120,identifier))
        try:
            current=self.session(user,row['action_id'],row['session_index'])
            if current['cancelled'] or json.loads(current['payload_json'])!=json.loads(row['old_payload_json']):raise PlanningError('The session changed after review.')
            generation=row['connection_generation']
            remote=self.calendar.event_call(user,generation,lambda p,v:p.get_event(v,current['google_event_id']))
            desired={**current,'payload_json':row['new_payload_json']}
            done=bool(row['attempted'] and ((row['kind']=='cancel' and (not remote or remote.get('status')=='cancelled')) or
                      (row['kind']=='reschedule' and self.actions.matches(remote,desired))))
            if not done:
                if self.clock()>=datetime.fromisoformat(row['expires_at']):raise PlanningError('Change approval expired; no new write was made.')
                if not self.actions.matches(remote,current) or remote.get('etag')!=row['remote_etag']:
                    raise PlanningError('The Calendar event changed externally. Review again; no overwrite allowed.')
                if row['kind']=='reschedule':
                    if fingerprint(profiles.get_profile(user)['preferences'])!=row['preferences_digest']:raise PlanningError('Preferences changed after review.')
                    proposal=self.actions.proposal(user,self.actions.record(user,row['action_id'])['proposal_id'])
                    self.validate_move(user,json.loads(row['new_payload_json']),json.loads(row['old_payload_json']),proposal)
                with closing(get_connection()) as db,db:
                    changed=db.execute("UPDATE calendar_session_changes SET attempted=1,lease_until=? WHERE id=? AND lease_token=? AND status='processing'",(self.clock().timestamp()+120,identifier,lease))
                    if not changed.rowcount:raise PlanningError('Another worker owns this change.')
                    audit(db,row['action_id'],'session_'+row['kind']+'_attempt')
                if row['kind']=='reschedule':
                    result=self.calendar.event_call(user,generation,lambda p,v:p.reschedule_event(v,current['google_event_id'],json.loads(row['new_payload_json']),row['remote_etag']))
                    if not self.actions.matches(result,desired):raise RuntimeError('Calendar confirmation incomplete; reconcile before any retry.')
                else:
                    self.calendar.event_call(user,generation,lambda p,v:p.cancel_event(v,current['google_event_id'],row['remote_etag']))
            with closing(get_connection()) as db,db:
                changed=db.execute("UPDATE calendar_session_changes SET status='completed',lease_until=0,error=NULL WHERE id=? AND lease_token=?",(identifier,lease))
                if changed.rowcount:audit(db,row['action_id'],'session_'+row['kind']+'_confirmed')
        except Exception as error:
            # A lost response must be reconciled against this same event/ETag.
            from services.google_oauth import OAuthUnavailable,ReconnectRequired
            from googleapiclient.errors import HttpError
            definite=isinstance(error,(PlanningError,OAuthUnavailable,ReconnectRequired)) or (isinstance(error,HttpError) and error.resp.status in (404,410) and not row['attempted'])
            message=str(error) if isinstance(error,PlanningError) else 'Calendar change could not be confirmed. Check the saved result before retrying.'
            with closing(get_connection()) as db,db:
                changed=db.execute('UPDATE calendar_session_changes SET status=?,lease_until=0,error=? WHERE id=? AND lease_token=?',('blocked' if definite else 'uncertain',message,identifier,lease))
                if changed.rowcount:audit(db,row['action_id'],'session_change_blocked' if definite else 'session_change_uncertain')
        return self.get(user,identifier)
