"""Provider-independent, authenticated actor-bound scheduling tool interface.

No actor ID in tool arguments. Human approval operations are deliberately absent
from model-callable tools; the verified Telegram UI collects those decisions.
"""
from contextlib import closing
from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo
import json

from pydantic import BaseModel, ConfigDict, Field, AwareDatetime
from typing import Literal
from database import get_connection
from models.planning import StudyRange, SessionValidation, ExactProposalInput, BusyPeriod
from services import profiles
from services.planning import PlanningService, PlanningError
from services.calendar_actions import CalendarActions
from services.scheduling_core import find_periods, validate_periods, utc
from models.preferences import effective_preferences


class EmptyInput(BaseModel):
    model_config=ConfigDict(extra='forbid')


class DraftInput(ExactProposalInput):
    goal_id: UUID
    replaces_id: UUID | None = None


class ActionInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action_id: UUID


class SessionChangeInput(ActionInput):
    session_index: int = Field(ge=0, le=99, strict=True)
    kind: Literal['reschedule','cancel']
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None


class ChangeInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    change_id: UUID


class SchedulingTools:
    def __init__(self,authenticated_user_id,planner=None,actions=None,human_interaction=False):
        if type(authenticated_user_id) is not int or authenticated_user_id<=0:
            raise ValueError('A verified positive Telegram identity is required.')
        self.user_id=authenticated_user_id
        self.planner=planner or PlanningService()
        self.actions=actions or CalendarActions(self.planner.calendar,self.planner,self.planner.clock)
        self.human_interaction=human_interaction

    def context(self,request):
        profile=profiles.get_profile(self.user_id)
        prefs=effective_preferences(profile['preferences'],request.temporary_preferences.model_dump(mode='json'))
        if prefs['notes'].strip() and (not request.restrictions_confirmed or request.reviewed_notes!=prefs['notes']):
            raise PlanningError('Map free-text restrictions to structured constraints and confirm them first.')
        start=max(utc(request.start),utc(self.planner.clock()))
        end=utc(request.end)
        if end<=start: raise PlanningError('Choose a future study range.')
        busy,generation,state=self.planner.busy(self.user_id,start,end,request.allow_unchecked_calendar)
        busy+=request.excluded_periods
        with closing(get_connection()) as db:
            for row in db.execute("SELECT proposal_json FROM study_proposals WHERE telegram_user_id=? AND status='approved'",(self.user_id,)):
                busy += [BusyPeriod.model_validate(s) for s in json.loads(row[0])['sessions']]
        return prefs,start,end,busy,state

    def availability(self,request):
        periods, unused, state = self.planner.busy(self.user_id,request.start,request.end,False)
        return {'busy': [p.model_dump(mode='json') for p in periods], 'state': state}

    def find(self,request):
        prefs,start,end,busy,state=self.context(request)
        slots=find_periods(start,end,ZoneInfo(request.timezone),prefs,prefs['session_minutes'],request.sessions,request.excluded_days,busy)
        return {'periods':slots,'timezone':request.timezone,'calendar_availability':state,'saved':False}

    def validate(self,request):
        prefs,start,end,busy,state=self.context(request)
        result=validate_periods([s.model_dump(mode='json') for s in request.proposed_sessions],start,end,ZoneInfo(request.timezone),prefs,prefs['session_minutes'],request.excluded_days,busy)
        return {**result,'calendar_availability':state}

    def draft(self,request):
        return self.planner.propose(self.user_id,str(request.goal_id),request,
                                    str(request.replaces_id) if request.replaces_id else None,
                                    [s.model_dump(mode='json') for s in request.proposed_sessions])

    def management(self):
        from services.calendar_management import CalendarSessionManagement
        return CalendarSessionManagement(self.actions)

    @staticmethod
    def change_result(row):
        return {key:row[key] for key in ('id','action_id','session_index','kind','status','expires_at','error')}

    def propose_change(self,request):
        result=self.management().propose(self.user_id,str(request.action_id),request.session_index,request.kind,
            request.start.isoformat() if request.start else None,request.end.isoformat() if request.end else None)
        return self.change_result(result)

    @classmethod
    def specifications(cls):
        schemas={'calendar_availability':StudyRange,'find_study_periods':StudyRange,
                 'validate_sessions':SessionValidation,'create_draft':DraftInput,
                 'get_proposals':EmptyInput,'get_calendar_actions':EmptyInput,
                 'execute_approved_calendar_action':ActionInput,'get_study_sessions':EmptyInput,
                 'propose_session_change':SessionChangeInput,'execute_approved_session_change':ChangeInput}
        descriptions={
            'calendar_availability':'Read only the authenticated student’s primary Calendar busy intervals.',
            'find_study_periods':'Find bounded periods satisfying saved preferences and explicit restrictions; do not save a plan.',
            'validate_sessions':'Validate exact suggested sessions without moving them or saving a plan.',
            'create_draft':'Validate and persist exact titled sessions for human review; never approve them.',
            'get_proposals':'Retrieve only this student’s stored draft and finalized proposals.',
            'get_calendar_actions':'Retrieve only this student’s approved Calendar action results.',
            'execute_approved_calendar_action':'Execute or reconcile an already human-approved action; enforce permission, digest, expiry and ownership.'}
        descriptions.update({'get_study_sessions':"List this user's agent-created sessions and current states.",
            'propose_session_change':'Validate and freeze a single-session change for Telegram approval; never approve.',
            'execute_approved_session_change':'Execute only an already human-approved exact session change.'})
        return [{'name':name,'description':descriptions[name],'input_schema':model.model_json_schema()} for name,model in schemas.items()]

    def invoke(self,name,arguments):
        operations={'calendar_availability':(StudyRange,self.availability),
                    'find_study_periods':(StudyRange,self.find),
                    'validate_sessions':(SessionValidation,self.validate),
                    'create_draft':(DraftInput,self.draft),
                    'get_proposals':(EmptyInput,lambda request:self.planner.proposals(self.user_id)),
                    'get_calendar_actions':(EmptyInput,lambda request:self.actions.list(self.user_id)),
                    'execute_approved_calendar_action':(ActionInput,lambda request:self.actions.execute(self.user_id,str(request.action_id))),
                    'get_study_sessions':(EmptyInput,lambda request:self.management().list_sessions(self.user_id)),
                    'propose_session_change':(SessionChangeInput,self.propose_change),
                    'execute_approved_session_change':(ChangeInput,lambda request:self.change_result(self.management().execute(self.user_id,str(request.change_id))))}
        if name not in operations: raise LookupError('Unknown scheduling tool.')
        schema,operation=operations[name]
        request=schema.model_validate(arguments)
        if not self.human_interaction and hasattr(request,'restrictions_confirmed'):
            # Model arguments cannot impersonate an interactive user confirmation.
            request.restrictions_confirmed=False
        return operation(request)
