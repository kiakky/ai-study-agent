"""Unified proposal storage using reusable scheduling constraint tools."""
from contextlib import closing
from datetime import datetime, timedelta, time, timezone
import hashlib
import json
from uuid import uuid4
from zoneinfo import ZoneInfo

from database import get_connection
from models.planning import BusyPeriod, StudyGoal
from services import profiles
from services.google_oauth import GoogleOAuthService, ReconnectRequired
from services.scheduling_core import PlanningError, overlaps, utc, local_instant, find_periods, validate_periods
from models.preferences import effective_preferences


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class PlanningService:
    def __init__(self, calendar=None, clock=None):
        self.calendar = calendar or GoogleOAuthService()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def save_goal(self, user_id, goal, request_id, source='user', authorized_brightspace_user=None):
        if source == 'brightspace':
            if user_id != authorized_brightspace_user or goal.exam is None:
                raise PlanningError('An authorized account-specific Brightspace connector is required.')
        elif goal.exam or any(r.source != 'user' for r in goal.resources):
            raise PlanningError('Exam evidence cannot be supplied as general-topic input.')
        profiles.ensure_profile(user_id)
        data = goal.model_dump(mode='json')
        digest = fingerprint({'source': source, 'goal': data})
        with closing(get_connection()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM learning_goals WHERE telegram_user_id=? AND request_id=?', (user_id,str(request_id))).fetchone()
            if old:
                if old['request_hash'] != digest: raise PlanningError('Request ID was already used for different content.')
                return self.goal_record(old)
            identifier = str(uuid4())
            db.execute('INSERT INTO learning_goals (id,telegram_user_id,request_id,request_hash,source,goal_json) VALUES (?,?,?,?,?,?)',
                       (identifier,user_id,str(request_id),digest,source,encoded(data)))
            return self.goal_record(db.execute('SELECT * FROM learning_goals WHERE id=?', (identifier,)).fetchone())

    @staticmethod
    def goal_record(row):
        return {'id':row['id'], 'source':row['source'], **json.loads(row['goal_json'])}

    def goals(self, user_id):
        with closing(get_connection()) as db:
            return [self.goal_record(r) for r in db.execute('SELECT * FROM learning_goals WHERE telegram_user_id=? ORDER BY created_at DESC,id LIMIT 100',(user_id,))]

    def goal(self, user_id, goal_id):
        with closing(get_connection()) as db:
            row=db.execute('SELECT * FROM learning_goals WHERE id=? AND telegram_user_id=?',(str(goal_id),user_id)).fetchone()
        if not row: raise LookupError('Goal not found.')
        return row

    def generation(self, user_id):
        with closing(get_connection()) as db:
            row=db.execute('SELECT generation FROM google_connections WHERE telegram_user_id=?',(user_id,)).fetchone()
        return row['generation'] if row else None

    def busy(self, user_id, start, end, allow_unchecked):
        status = self.calendar.status(user_id)
        if status['status'] == 'disconnected':
            if not allow_unchecked: raise PlanningError('Connect Google Calendar or explicitly allow a draft without a Calendar check.')
            return [], None, 'not_checked'
        generation = self.generation(user_id)
        if not status['available'] or status['status'] not in {'connected','expired'}:
            raise PlanningError('Your Calendar connection needs attention. Reconnect or disconnect it before planning.')
        if end <= start or end-start > timedelta(days=90):
            raise PlanningError('Use a future range of at most 90 days.')
        value = {'busy': []}
        cursor = start
        while cursor < end:
            stop = min(end, cursor+timedelta(days=31))
            value['busy'].extend(self.calendar.availability(user_id,cursor,stop)['busy'])
            cursor = stop
        if generation != self.generation(user_id): raise PlanningError('Calendar connection changed. Request a fresh draft.')
        return [BusyPeriod.model_validate(p) for p in value['busy']], generation, 'checked'

    def proposals(self, user_id):
        with closing(get_connection()) as db:
            return [self.proposal_record(r) for r in db.execute('SELECT * FROM study_proposals WHERE telegram_user_id=? ORDER BY created_at DESC,id LIMIT 100',(user_id,))]

    @staticmethod
    def proposal_record(row):
        return {'id':row['id'],'goal_id':row['goal_id'],'status':row['status'],'replaces_id':row['replaces_id'],
                'approved_at':row['approved_at'],'event_creation_enabled':False,'proposal_digest':fingerprint(json.loads(row['proposal_json'])), **json.loads(row['proposal_json'])}

    def propose(self, user_id, goal_id, request, replaces_id=None, proposed_sessions=None, *, pending_amendment_digest=None):
        goal_row = self.goal(user_id,goal_id)
        goal = StudyGoal.model_validate_json(goal_row['goal_json'])
        topics = goal.topics
        if not topics and goal.exam and goal.exam.tested_topics.status in {'confirmed','approximate'}:
            known_topics = goal.exam.tested_topics.value
            if isinstance(known_topics,list) and all(isinstance(value,str) and value.strip() for value in known_topics):
                topics = known_topics
        request_data = request.model_dump(mode='json')
        digest = fingerprint({'goal_id':str(goal_id),'request':request_data,'replaces_id':replaces_id,'sessions':proposed_sessions})
        with closing(get_connection()) as db:
            old=db.execute('SELECT * FROM study_proposals WHERE telegram_user_id=? AND request_id=?',(user_id,str(request.request_id))).fetchone()
            if old:
                if old['request_hash']!=digest: raise PlanningError('Request ID already used for different content.')
                return self.proposal_record(old)
            if replaces_id:
                previous=db.execute('SELECT * FROM study_proposals WHERE id=? AND telegram_user_id=? AND goal_id=?',(str(replaces_id),user_id,str(goal_id))).fetchone()
                if not previous or previous['status'] not in ({'draft','rejected'} if pending_amendment_digest else {'draft','approved'}): raise PlanningError('Only your current draft or approved plan can be adjusted.')
        profile = profiles.get_profile(user_id)
        saved_prefs = profile['preferences']
        prefs = effective_preferences(saved_prefs,request.temporary_preferences.model_dump(mode='json'))
        if prefs['notes'].strip() and (not request.restrictions_confirmed or request.reviewed_notes != prefs['notes']):
            raise PlanningError('Review your free-text preferences and map restrictions to days, time windows or exclusions first. They cannot be interpreted automatically yet.')
        if request.adjustment.strip() and not request.restrictions_confirmed:
            raise PlanningError('Translate adjustment notes into explicit constraints and confirm them before drafting.')
        if not prefs['study_windows']: raise PlanningError('Set preferred study time windows in Setup before drafting.')
        zone = ZoneInfo(request.timezone or profile['timezone'])
        now = utc(self.clock())
        start=max(utc(request.start),now)
        end=utc(request.start)+timedelta(days=request.days)
        if end<=start: raise PlanningError('Choose a future planning period.')
        if goal.target_date:
            end=min(end,local_instant(goal.target_date+timedelta(days=1),time(0),zone))
        if goal.exam:
            fact=goal.exam.confirmed_start
            if fact.status=='confirmed': end=min(end,utc(datetime.fromisoformat(str(fact.value).replace('Z','+00:00'))))
            elif goal.exam.date_range.status in {'confirmed','approximate'} and goal.exam.date_range.value:
                # Prepare before the first possible exam day; do not claim an exact date.
                end=min(end,local_instant(datetime.fromisoformat(goal.exam.date_range.value['start']).date(),time(0),zone))
        if end<=start: raise PlanningError('The target/exam period has already begun. Choose another goal.')
        busy,generation,availability=self.busy(user_id,start,end,request.allow_unchecked_calendar)
        busy += request.excluded_periods
        with closing(get_connection()) as db:
            active=db.execute("SELECT id,proposal_json FROM study_proposals WHERE telegram_user_id=? AND status='approved'",(user_id,)).fetchall()
        for row in active:
            if row['id']!=replaces_id:
                busy += [BusyPeriod.model_validate(s) for s in json.loads(row['proposal_json'])['sessions']]
        duration=request.temporary_preferences.session_minutes or goal.session_minutes or prefs['session_minutes']
        session_overrides={i:p.model_dump(mode='json',exclude_none=True) for i,p in request.session_overrides.items()}
        if proposed_sessions is None:
            slots=find_periods(start,end,zone,prefs,duration,request.sessions,request.excluded_days,busy)
            for index,slot in enumerate(slots):
                slot['topic']=topics[index%len(topics)] if topics else goal.subject
                slot['title']=('Study: '+goal.subject+(' - '+slot['topic'] if slot['topic']!=goal.subject else ''))[:200]
        else:
            if len(proposed_sessions)>request.sessions:
                raise PlanningError('More sessions than requested.')
            if any(i>=len(proposed_sessions) for i in session_overrides):raise PlanningError('Unknown session override.')
            validate_periods(proposed_sessions,start,end,zone,prefs,duration,request.excluded_days,busy,session_overrides)
            slots=sorted(proposed_sessions,key=lambda s:datetime.fromisoformat(s['start'].replace('Z','+00:00')))
            # Keep per-session amendment constraints attached to the same exact session
            # even when moving it changes chronological order.
            request_data['session_overrides']={str(slots.index(proposed_sessions[i])):patch for i,patch in session_overrides.items()}
        if not slots: raise PlanningError('No suitable free study periods found. Adjust your constraints; no plan was saved.')
        data={'subject':goal.subject,'timezone':str(zone),'sessions':slots,'requested_sessions':request.sessions,
              'practice_format':goal.practice_format or prefs['practice_format'],'question_count':goal.question_count or prefs['question_count'],
              'difficulty':goal.difficulty,'resources':[r.model_dump() for r in goal.resources],
              'calendar_availability':availability,'calendar_target':'primary','created_at':now.isoformat(),
              'expires_at':(now+timedelta(hours=24)).isoformat(),'preferences_digest':fingerprint(saved_prefs),
              'constraints':request_data,'goal_digest':fingerprint(json.loads(goal_row['goal_json'])),
              'exam_information':goal.exam.model_dump(mode='json') if goal.exam else None,
              'pending_amendment':True if pending_amendment_digest else False,
              'warnings':(['Fewer suitable sessions than requested.'] if len(slots)<request.sessions else []) +
                         (['Calendar was not checked; this is an unverified availability proposal.'] if availability=='not_checked' else [])}
        with closing(get_connection()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM study_proposals WHERE telegram_user_id=? AND request_id=?',(user_id,str(request.request_id))).fetchone()
            if old:
                if old['request_hash']!=digest: raise PlanningError('Request ID already used.')
                return self.proposal_record(old)
            if pending_amendment_digest:
                parent=db.execute('SELECT * FROM study_proposals WHERE id=? AND telegram_user_id=?',(str(replaces_id),user_id)).fetchone()
                latest=db.execute('SELECT id FROM study_proposals WHERE telegram_user_id=? ORDER BY rowid DESC LIMIT 1',(user_id,)).fetchone()
                if (not parent or parent['status'] not in {'draft','rejected'} or latest[0]!=parent['id'] or
                    fingerprint(json.loads(parent['proposal_json']))!=pending_amendment_digest or
                    db.execute('SELECT 1 FROM calendar_write_actions WHERE proposal_id=?',(parent['id'],)).fetchone()):
                    raise PlanningError('The pending plan changed or was approved while validating. Retrieve its current state.')
                db.execute("UPDATE study_proposals SET status='superseded',approved_digest=NULL WHERE id=?",(parent['id'],))
                db.execute("UPDATE calendar_reviews SET status='blocked',expires_at=?,error='Plan revised; review the latest complete schedule.' WHERE proposal_id=? AND telegram_user_id=? AND status IN ('pending','approved','consent','changes')",(now.isoformat(),parent['id'],user_id))
            if replaces_id:
                pending=db.execute('SELECT id,status FROM calendar_write_actions WHERE proposal_id=? AND telegram_user_id=?',(str(replaces_id),user_id)).fetchone()
                if pending:
                    if pending['status']=='processing':
                        raise PlanningError('Calendar execution is in progress. Check its result before requesting changes.')
                    if pending['status']!='completed':
                        db.execute("UPDATE calendar_write_actions SET status='blocked',approval_expires_at=?,lease_until=0 WHERE id=?",(now.isoformat(),pending['id']))
                        db.execute("INSERT INTO calendar_action_audit (action_id,event) VALUES (?,'approval_invalidated_by_change_request')",(pending['id'],))
            identifier=str(uuid4())
            db.execute('INSERT INTO study_proposals (id,telegram_user_id,goal_id,request_id,request_hash,replaces_id,proposal_json,calendar_generation) VALUES (?,?,?,?,?,?,?,?)',
                       (identifier,user_id,str(goal_id),str(request.request_id),digest,replaces_id,encoded(data),generation))
            return self.proposal_record(db.execute('SELECT * FROM study_proposals WHERE id=?',(identifier,)).fetchone())

    def decide(self,user_id,proposal_id,action):
        with closing(get_connection()) as db:
            row=db.execute('SELECT * FROM study_proposals WHERE id=? AND telegram_user_id=?',(str(proposal_id),user_id)).fetchone()
        if not row: raise LookupError('Proposal not found.')
        if action not in {'approve','reject'}: raise PlanningError('Unsupported decision.')
        target='approved' if action=='approve' else 'rejected'
        if row['status']==target: return self.proposal_record(row)
        if row['status']!='draft': raise PlanningError('Only a draft can receive a new decision.')
        data=json.loads(row['proposal_json'])
        if action=='approve':
            now=utc(self.clock())
            if now>=datetime.fromisoformat(data['expires_at']) or now>=datetime.fromisoformat(data['sessions'][0]['start']):
                raise PlanningError('Draft expired or its first session has begun. Request an adjustment.')
            profile=profiles.get_profile(user_id)
            if fingerprint(profile['preferences'])!=data['preferences_digest']: raise PlanningError('Preferences changed. Request a fresh draft.')
            if row['calendar_generation']!=self.generation(user_id): raise PlanningError('Calendar connection changed. Request a fresh draft.')
            self.validate_saved(user_id,row)
            if data['calendar_availability']=='checked':
                periods,generation,unused=self.busy(user_id,datetime.fromisoformat(data['sessions'][0]['start']),datetime.fromisoformat(data['sessions'][-1]['end']),False)
                if any(overlaps(datetime.fromisoformat(s['start']),datetime.fromisoformat(s['end']),periods) for s in data['sessions']):
                    raise PlanningError('Calendar availability changed. Request an adjustment; the draft was not approved.')
        with closing(get_connection()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT status FROM study_proposals WHERE id=? AND telegram_user_id=?',(str(proposal_id),user_id)).fetchone()
            if current['status']==target: return self.proposal_record(db.execute('SELECT * FROM study_proposals WHERE id=?',(str(proposal_id),)).fetchone())
            if current['status']!='draft': raise PlanningError('Proposal already resolved.')
            if action=='approve':
                if row['calendar_generation']!=self.generation(user_id): raise PlanningError('Calendar connection changed.')
                # Recheck preference snapshot under the same write lock as approval.
                from models.preferences import StudyPreferences
                saved=db.execute('SELECT preferences_json FROM users WHERE telegram_user_id=?',(user_id,)).fetchone()
                known={key:value for key,value in json.loads(saved[0]).items() if key in StudyPreferences.model_fields}
                if fingerprint(StudyPreferences.model_validate(known).model_dump(mode='json'))!=data['preferences_digest']:
                    raise PlanningError('Preferences changed during approval. Request a fresh draft.')
                if row['replaces_id']:
                    calendar_action=db.execute('SELECT id,status FROM calendar_write_actions WHERE proposal_id=? AND telegram_user_id=?',(row['replaces_id'],user_id)).fetchone()
                    if calendar_action:
                        written=db.execute("SELECT 1 FROM calendar_event_intents WHERE action_id=? AND status IN ('created','uncertain') LIMIT 1",(calendar_action['id'],)).fetchone()
                        if calendar_action['status']=='processing' or written:
                            raise PlanningError('Resolve existing Calendar execution before replacing this plan; event edits are not supported.')
                        db.execute("UPDATE calendar_write_actions SET status='blocked',lease_until=0 WHERE id=?",(calendar_action['id'],))
                        db.execute("INSERT INTO calendar_action_audit (action_id,event) VALUES (?,'approval_invalidated_by_replacement')",(calendar_action['id'],))
                    replaced=db.execute("UPDATE study_proposals SET status='superseded' WHERE id=? AND telegram_user_id=? AND status IN ('draft','approved')",(row['replaces_id'],user_id))
                    if not replaced.rowcount:
                        parent=db.execute('SELECT status FROM study_proposals WHERE id=? AND telegram_user_id=?',(row['replaces_id'],user_id)).fetchone()
                        if not (data.get('pending_amendment') and parent and parent['status']=='superseded'):
                            raise PlanningError('The original plan changed. Request a fresh adjustment.')
                # Prevent two simultaneous approvals for overlapping slots.
                for existing in db.execute("SELECT proposal_json FROM study_proposals WHERE telegram_user_id=? AND status='approved'",(user_id,)):
                    other=[BusyPeriod.model_validate(s) for s in json.loads(existing[0])['sessions']]
                    if any(overlaps(datetime.fromisoformat(s['start']),datetime.fromisoformat(s['end']),other) for s in data['sessions']): raise PlanningError('Another approved plan occupies these periods.')
            db.execute('UPDATE study_proposals SET status=?,approved_digest=?,approved_at=? WHERE id=? AND telegram_user_id=?',
                       (target,fingerprint(data) if action=='approve' else None,utc(self.clock()).isoformat() if action=='approve' else None,str(proposal_id),user_id))
            return self.proposal_record(db.execute('SELECT * FROM study_proposals WHERE id=?',(str(proposal_id),)).fetchone())

    def adjust(self,user_id,proposal_id,request):
        with closing(get_connection()) as db:
            row=db.execute('SELECT goal_id FROM study_proposals WHERE id=? AND telegram_user_id=?',(str(proposal_id),user_id)).fetchone()
        if not row: raise LookupError('Proposal not found.')
        return self.propose(user_id,row['goal_id'],request,str(proposal_id))

    def validate_saved(self,user_id,row,busy_override=None):
        data=json.loads(row['proposal_json'])
        profile=profiles.get_profile(user_id)
        if fingerprint(profile['preferences'])!=data['preferences_digest']:
            raise PlanningError('Preferences changed. Request a fresh draft.')
        goal=StudyGoal.model_validate_json(self.goal(user_id,row['goal_id'])['goal_json'])
        constraints=data['constraints']
        sessions=data['sessions']
        start=max(utc(self.clock()),datetime.fromisoformat(constraints['start'].replace('Z','+00:00')))
        end=datetime.fromisoformat(constraints['start'].replace('Z','+00:00'))+timedelta(days=constraints['days'])
        zone=ZoneInfo(data['timezone'])
        if goal.target_date: end=min(end,local_instant(goal.target_date+timedelta(days=1),time(0),zone))
        if goal.exam:
            if goal.exam.confirmed_start.status=='confirmed':
                end=min(end,utc(datetime.fromisoformat(str(goal.exam.confirmed_start.value).replace('Z','+00:00'))))
            elif goal.exam.date_range.status in {'confirmed','approximate'} and goal.exam.date_range.value:
                end=min(end,local_instant(datetime.fromisoformat(goal.exam.date_range.value['start']).date(),time(0),zone))
        busy=list(busy_override or [])
        busy += [BusyPeriod.model_validate(p) for p in constraints['excluded_periods']]
        prefs=effective_preferences(profile['preferences'],constraints.get('temporary_preferences'))
        override_minutes=(constraints.get('temporary_preferences') or {}).get('session_minutes')
        return validate_periods(sessions,start,end,zone,prefs,override_minutes or goal.session_minutes or prefs['session_minutes'],constraints['excluded_days'],busy,constraints.get('session_overrides'))
