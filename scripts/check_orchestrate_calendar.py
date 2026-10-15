"""Bounded real WXO reasoning with synthetic users, temp SQLite and mocked Google."""
import argparse
from datetime import datetime, timedelta
import logging
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tests'),str(ROOT/'backend')]


def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify',action='store_true')
    parser.add_argument('--env-file',type=Path,default=ROOT/'.env')
    parser.add_argument('--scenario',choices=['plan-amend','amend','cancel','move','two-week'],default='plan-amend')
    args=parser.parse_args()
    if not args.verify:
        print('No configuration or API access. Use --verify for the named synthetic scenario.')
        return 0
    logging.disable(logging.CRITICAL)
    from test_orchestrate_calendar import OrchestrateCalendarTests
    from services.orchestrate import OrchestrateProvider
    from services.orchestrate_client import Configuration
    from services.calendar_agent import CalendarAgent
    from services.study_tools import SchedulingTools
    from services.conversations import system_instructions
    from services.calendar_conversation import CalendarConversation
    from services.llm_contracts import LLMError
    from services import profiles
    from models.preferences import StudyPreferences
    import test_calendar_actions as fixtures
    fixture=OrchestrateCalendarTests()
    provider=None
    calls=0
    last_response=''
    try:
        fixture.setUp()
        provider=OrchestrateProvider(Configuration.load(args.env_file))
        original_infer=provider._infer
        def capture(prompt):
            nonlocal last_response, calls
            calls+=1
            if calls>6:raise LLMError('verification_budget')
            last_response=original_infer(prompt)
            return last_response
        provider._infer=capture
        class BoundedProvider:
            structured_actions=True
            def complete(self,messages,**kwargs):
                nonlocal calls
                result=provider.complete(messages,**kwargs)
                print('Synthetic inference '+str(calls)+': '+(', '.join(c.name for c in result.tool_calls) or 'clarification/read reply'))
                return result
        reasoning=BoundedProvider()
        def run(user,message_id,text,history=()):
            agent=CalendarAgent(user,message_id,SchedulingTools(user,fixture.planner,fixture.actions))
            reply=agent.complete(reasoning,[{'role':'system','content':system_instructions(fixtures.NOW,'Asia/Singapore',True)},*history,{'role':'user','content':text}])
            return agent,reply
        if args.scenario=='two-week':
            text='Can you help me set up study sessions for the next two weeks for machine learning?'
            agent,reply=run(101,710,text)
            assert not agent.references() and 'sessions per week' in reply.content.lower()
            agent,reply=run(101,711,'Three sessions per week.',[
                {'role':'user','content':text},{'role':'assistant','content':reply.content}])
            plan=next(r for r in agent.references() if r.get('review_kind')=='plan')
            starts=[datetime.fromisoformat(item['start']).astimezone(ZoneInfo('Asia/Singapore')) for item in plan['sessions']]
            assert len(starts)==6 and sum(value.date()<datetime(2026,10,26).date() for value in starts)==3
            assert not fixture.provider.inserts
            print('PASS: broad two-week request clarified frequency, then proposed three sessions in each week using actual synthetic availability. No Calendar writes.')
        elif args.scenario in {'plan-amend','amend'}:
            profiles.save_preferences(101,StudyPreferences(study_days=['mon','wed','fri'],session_minutes=20,
                study_windows=[{'start':'09:00','end':'11:00'}]))
            before=profiles.get_profile(101)['preferences']
            if args.scenario=='amend':
                plan,_,_=fixture.plan(study_days=['mon','wed','fri'],study_windows=[{'start':'13:00','end':'18:00'}],session_minutes=20)
            else:
                agent,reply=run(101,701,'Schedule three Python revision sessions next week on Monday, Wednesday and Friday in the afternoon, 20 minutes each. Use my Calendar availability.')
                plan=next(r for r in agent.references() if r.get('review_kind')=='plan')
            starts=[datetime.fromisoformat(s['start']).astimezone(ZoneInfo('Asia/Singapore')) for s in plan['sessions']]
            assert len(starts)==3 and [s.weekday() for s in starts]==[0,2,4] and all(s.hour>=13 for s in starts)
            assert profiles.get_profile(101)['preferences']==before and not fixture.provider.inserts
            review_service=CalendarConversation(fixture.actions)
            review,_=review_service.prepare(101,plan['proposal_id']);review_service.presented(101,review['id'])
            agent,reply=run(101,702,'Monday and Wednesday are fine, but move Friday to 3 PM.',
                [{'role':'user','content':'Schedule three Python revision sessions next week.'},{'role':'assistant','content':'Pending Monday, Wednesday, Friday afternoon sessions; nothing is approved.'}])
            revised=next(r for r in agent.references() if r.get('review_kind')=='plan')
            assert revised['sessions'][:2]==plan['sessions'][:2]
            assert datetime.fromisoformat(revised['sessions'][2]['start']).astimezone(ZoneInfo('Asia/Singapore')).hour==15
            assert review_service.get(101,review['id'])['status']=='blocked'
            assert not fixture.provider.inserts and profiles.get_profile(101)['preferences']==before
            print('PASS: selected Friday amendment, unchanged other sessions, temporary overrides and invalidated old review. No Google writes.')
        else:
            fixture.setup_events() # Exclusively synthetic in-memory Google fixtures.
            original=list(fixture.provider.inserts)
            text='Cancel only my second study session.' if args.scenario=='cancel' else 'Move only my second study session to Thursday 15 October 2026 at 7 PM, keeping its 30-minute duration.'
            agent,reply=run(101,703,text)
            change=next((r for r in agent.references() if r.get('review_kind')=='change'),None)
            if change is None:
                print('Synthetic non-proposal reply: '+reply.content)
                raise LLMError('missing_change_proposal')
            assert change['session_index']==1
            assert change['kind']==('cancel' if args.scenario=='cancel' else 'reschedule')
            assert fixture.provider.inserts==original and not fixture.moves and not fixture.deletes
            print('PASS: real Orchestrate resolved only the second actor-owned session and proposed '+change['kind']+' without execution.')
        print('Inference submissions: '+str(calls)+'; at most one format repair per response; production untouched.')
        return 0
    except LLMError as error:
        print('STOP: synthetic verification failed ('+error.code+'); no automatic repeat or private payloads.')
        if error.code=='invalid_tool_call' and last_response:
            # Only this explicit synthetic harness displays the already-redacted
            # synthetic model output. Production never logs provider payloads.
            print('Synthetic response diagnostic: '+last_response[:6000])
        return 1
    except Exception as error:
        print('STOP: synthetic verification failed ('+type(error).__name__+'); details suppressed; no automatic repeat.')
        return 1
    finally:
        if provider:provider.close()
        fixture.doCleanups()


if __name__=='__main__':raise SystemExit(main())
