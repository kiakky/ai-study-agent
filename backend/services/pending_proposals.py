"""Immutable, actor-bound revisions of unexecuted plans; no Calendar writes."""
from contextlib import closing
from datetime import datetime, timedelta, date, time
from uuid import UUID, uuid5, NAMESPACE_URL
from zoneinfo import ZoneInfo
import json
from pydantic import BaseModel, ConfigDict, Field
from database import get_connection
from models.planning import ExactProposalInput
from services.planning import PlanningError, fingerprint
from services.scheduling_core import local_instant


class AmendmentInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    proposal_id: UUID
    proposal_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    session_index: int = Field(ge=0,le=99,strict=True)
    local_start: time
    local_end: time | None = None
    local_date: date | None = None


class PendingProposals:
    def __init__(self,tools):self.tools=tools;self.user=tools.user_id

    def latest(self):
        with closing(get_connection()) as db:
            row=db.execute('SELECT * FROM study_proposals WHERE telegram_user_id=? ORDER BY rowid DESC LIMIT 1',(self.user,)).fetchone()
            if not row or row['status'] not in {'draft','rejected'}:return None
            if db.execute('SELECT 1 FROM calendar_write_actions WHERE telegram_user_id=? AND proposal_id=?',(self.user,row['id'])).fetchone():return None
            return dict(row)

    def snapshot(self):
        row=self.latest()
        if not row:return None
        data=json.loads(row['proposal_json']);zone=ZoneInfo(data['timezone'])
        return {'proposal_id':row['id'],'proposal_digest':fingerprint(data),'status':row['status'],'timezone':data['timezone'],
            'sessions':[{'session_index':i,**s,'weekday':datetime.fromisoformat(s['start']).astimezone(zone).strftime('%A'),
                         'local_start':datetime.fromisoformat(s['start']).astimezone(zone).isoformat(),
                         'local_end':datetime.fromisoformat(s['end']).astimezone(zone).isoformat()} for i,s in enumerate(data['sessions'])]}

    def amend(self,request,message_id,ordinal):
        row=self.latest()
        if not row or row['id']!=str(request.proposal_id):raise PlanningError('Use the exact latest pending proposal; retrieve it again if selection is unclear.')
        data=json.loads(row['proposal_json'])
        if fingerprint(data)!=request.proposal_digest:raise PlanningError('The proposal changed. Review the latest sessions before amending.')
        if request.session_index>=len(data['sessions']):raise PlanningError('That session is not in this pending proposal.')
        sessions=[dict(s) for s in data['sessions']];old=sessions[request.session_index];zone=ZoneInfo(data['timezone'])
        a=datetime.fromisoformat(old['start']);b=datetime.fromisoformat(old['end'])
        day=request.local_date or a.astimezone(zone).date()
        for value in (request.local_start,request.local_end):
            if value and (value.tzinfo or value.second or value.microsecond):raise PlanningError('Use whole-minute local times in your saved timezone.')
        start=local_instant(day,request.local_start,zone)
        end=local_instant(day,request.local_end,zone) if request.local_end else start+(b-a)
        minutes=(end-start).total_seconds()/60
        if not 5<=minutes<=180 or minutes!=int(minutes):raise PlanningError('Use a session duration between 5 and 180 whole minutes.')
        sessions[request.session_index]={**old,'start':start.isoformat(),'end':end.isoformat()}
        constraints=dict(data['constraints']);constraints.pop('proposed_sessions',None)
        # Explicitly moving this selected session is a local exception to soft
        # defaults; every other session retains its original constraints.
        selected_day=('mon','tue','wed','thu','fri','sat','sun')[day.weekday()]
        constraints['excluded_days']=[d for d in constraints['excluded_days'] if d!=selected_day]
        constraints['session_overrides']={**constraints.get('session_overrides',{}),str(request.session_index):{
            'study_days':[selected_day],
            'study_windows':[{'start':start.astimezone(zone).strftime('%H:%M'),'end':end.astimezone(zone).strftime('%H:%M')}],
            'session_minutes':int(minutes)}}
        constraints['request_id']=str(uuid5(NAMESPACE_URL,f'pending-amend:{self.user}:{message_id}:{ordinal}'))
        constraints['proposed_sessions']=sessions
        request_data=ExactProposalInput.model_validate(constraints)
        proposal=self.tools.planner.propose(self.user,row['goal_id'],request_data,row['id'],sessions,
            pending_amendment_digest=request.proposal_digest)
        return {'review_kind':'plan','proposal_id':proposal['id'],'sessions':proposal['sessions'],'approved':False}
