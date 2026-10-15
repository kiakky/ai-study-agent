from uuid import UUID
from fastapi import Depends, HTTPException
from models.planning import GoalInput, StudyGoal, ProposalInput, ProposalDecision
from services.planning import PlanningService, PlanningError
from services.google_oauth import OAuthUnavailable, ReconnectRequired
from services.calendar_actions import CalendarActions
from services.study_tools import SchedulingTools
from models.planning import CalendarActionApproval
from pydantic import ValidationError


def planning_service():
    return PlanningService()


def calendar_actions():
    return CalendarActions()


def operation(callback):
    try: return callback()
    except LookupError: raise HTTPException(404,'Study goal or proposal not found.') from None
    except PlanningError as error: raise HTTPException(409,str(error)) from None
    except ValidationError:
        raise HTTPException(422,'Invalid structured scheduling tool input.') from None
    except (OAuthUnavailable,ReconnectRequired):
        raise HTTPException(409,'Calendar access or approval needs attention. Review the action result before retrying; no further writes were authorized.') from None
    except Exception:
        raise HTTPException(503,'Operation unavailable. Check saved action results before retrying.') from None


def register_planning_routes(app,authenticate):
    @app.get('/api/study/tool-schemas')
    def tool_schemas(user=Depends(authenticate)):
        return SchedulingTools.specifications()

    @app.post('/api/study/tools/{name}')
    def tool(name:str,arguments:dict,user=Depends(authenticate)):
        return operation(lambda:SchedulingTools(user['id'],planning_service(),human_interaction=True).invoke(name,arguments))

    @app.get('/api/study/sessions')
    def sessions(user=Depends(authenticate)):
        from services.calendar_management import CalendarSessionManagement
        return operation(lambda:CalendarSessionManagement(calendar_actions()).list_sessions(user['id']))

    @app.get('/api/study/calendar-actions')
    def actions(user=Depends(authenticate)):
        return operation(lambda:calendar_actions().list(user['id']))

    @app.post('/api/study/proposals/{proposal_id}/event-permission')
    def event_permission(proposal_id:UUID,user=Depends(authenticate)):
        return operation(lambda:calendar_actions().permission_link(user['id'],str(proposal_id)))

    @app.post('/api/study/proposals/{proposal_id}/calendar-approval')
    def calendar_approval(proposal_id:UUID,body:CalendarActionApproval,user=Depends(authenticate)):
        # Preserve the route's authentication boundary, but collect new exact-write
        # approvals only in the user-bound Telegram conversation.
        raise HTTPException(409,'Review and approve exact Calendar events in Telegram using /studyplan.')

    @app.post('/api/study/calendar-actions/{action_id}/execute')
    def execute(action_id:UUID,user=Depends(authenticate)):
        return operation(lambda:calendar_actions().execute(user['id'],str(action_id)))

    @app.get('/api/study/goals')
    def goals(user=Depends(authenticate)):
        return operation(lambda:planning_service().goals(user['id']))

    @app.post('/api/study/goals')
    def add_goal(body:GoalInput,user=Depends(authenticate)):
        goal=StudyGoal.model_validate(body.model_dump(exclude={'request_id'}))
        return operation(lambda:planning_service().save_goal(user['id'],goal,body.request_id))

    @app.get('/api/study/proposals')
    def proposals(user=Depends(authenticate)):
        return operation(lambda:planning_service().proposals(user['id']))

    @app.post('/api/study/goals/{goal_id}/proposals')
    def propose(goal_id:UUID,body:ProposalInput,user=Depends(authenticate)):
        return operation(lambda:planning_service().propose(user['id'],str(goal_id),body))

    @app.post('/api/study/proposals/{proposal_id}/decision')
    def decision(proposal_id:UUID,body:ProposalDecision,user=Depends(authenticate)):
        return operation(lambda:planning_service().decide(user['id'],str(proposal_id),body.action))

    @app.post('/api/study/proposals/{proposal_id}/adjust')
    def adjust(proposal_id:UUID,body:ProposalInput,user=Depends(authenticate)):
        return operation(lambda:planning_service().adjust(user['id'],str(proposal_id),body))
