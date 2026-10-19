"""Opt-in synthetic IBM review/conflict acceptance; Google/Telegram remain mocked."""
import argparse, logging, sys
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tests'),str(ROOT/'backend')]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify',action='store_true')
    parser.add_argument('--env-file',type=Path,default=ROOT/'.env')
    args=parser.parse_args()
    if not args.verify:
        print('No credentials or services accessed. Use --verify for bounded synthetic checks.'); return 0
    logging.disable(logging.CRITICAL)
    from test_calendar_native import NativeCalendarTests,ScriptedModel
    import test_calendar_actions as fixtures
    from services.calendar_native import NativeCalendarAgent
    from services.orchestrate_native import OrchestrateNativeProvider
    from services.orchestrate_client import Configuration
    from services.conversations import ConversationService
    from services.study_tools import SchedulingTools
    from services.llm_contracts import ChatReply,LLMError
    from services import profiles
    from models.preferences import StudyPreferences
    from services.calendar_conversation import CalendarConversation
    from services.calendar_text_decisions import apply_decision
    from zoneinfo import ZoneInfo
    provider=OrchestrateNativeProvider(Configuration.load(args.env_file)); calls=0
    class Bounded:
        prevent_uncertain_replay=True
        def complete(self,messages,**kwargs):
            nonlocal calls
            calls+=1
            if calls>22: raise LLMError('verification_budget')
            reply=provider.complete(messages,**kwargs)
            print('Submission '+str(calls)+': '+(', '.join(c.name for c in reply.tool_calls) or 'natural reply'),flush=True)
            return reply
    try:
        for scenario in ['direct','approved-prose','conflicts']:
            fixture=NativeCalendarTests()
            try:
                fixture.setUp()
                profiles.save_preferences(101,StudyPreferences(session_minutes=20,study_days=['mon','wed','fri'],study_windows=[{'start':'14:00','end':'18:00'}]))
                busy=[]
                if scenario=='conflicts':
                    busy=[{'start':'2026-10-19T00:00:00+08:00','end':'2026-10-20T00:00:00+08:00'},
                          {'start':'2026-10-21T14:00:00+08:00','end':'2026-10-21T15:00:00+08:00'},
                          {'start':'2026-10-26T14:00:00+08:00','end':'2026-10-26T15:30:00+08:00'}]
                    fixture.provider.freebusy=lambda *args:busy
                def factory(u,m,**kw):return NativeCalendarAgent(u,m,SchedulingTools(u,fixture.planner,fixture.actions),**kw)
                def service(model):return ConversationService(model,clock=lambda:fixtures.NOW.timestamp(),agent_factory=factory)
                request='Please arrange five Machine Learning sessions, two during 19-25 October 2026 and three during 26 October-1 November 2026. Use my saved duration and days/times; choose free slots.'
                if scenario=='approved-prose':
                    service(ScriptedModel([ChatReply('I suggest five twenty-minute Machine Learning sessions: October 19 and 21 at 14:00, then October 26, 28 and 30 at 14:00. Shall I prepare these for approval?')])).respond(101,1,request)
                    text='Approved. Go ahead and set those 5 events in my calendar'
                else:text=request
                print('Scenario: '+scenario,flush=True)
                result=service(Bounded()).respond(101,2,text)
                plans=fixture.planner.proposals(101)
                assert len(plans)==1,'Model failed to prepare review in the same turn'
                plan=plans[0]; assert len(plan['sessions'])==5
                assert not fixture.provider.inserts
                counts={}
                for session in plan['sessions']:
                    start=datetime.fromisoformat(session['start']); end=datetime.fromisoformat(session['end'])
                    local=start.astimezone(ZoneInfo('Asia/Singapore'))
                    week=1 if local.day<26 else 2
                    counts[week]=counts.get(week,0)+1
                    assert local.weekday() in [0,2,4]
                    for occupied in busy:
                        assert not (start<datetime.fromisoformat(occupied['end']) and end>datetime.fromisoformat(occupied['start']))
                assert counts=={1:2,2:3}
                assert 'Nothing has been written' not in result['text']
                review_service=CalendarConversation(fixture.actions)
                review,_=review_service.prepare(101,plan['id']);review_service.presented(101,review['id'])
                approved=apply_decision(101,3,'Approve all five',review_service)
                assert approved['status']=='completed' and len(fixture.provider.inserts)==5
                assert apply_decision(101,3,'Approve all five',review_service)['status']=='completed'
                assert len(fixture.provider.inserts)==5
                print('PASS immediate exact review, 2+3 distribution, busy-period exclusion, human approval and duplicate protection (mocked writes only)',flush=True)
            finally:fixture.doCleanups()
    finally:provider.close()
    print('PASS all review scenarios; submissions='+str(calls)+'; no real Calendar/Telegram writes')
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print('STOP '+type(error).__name__+'; private details suppressed; no automatic replay')
        raise SystemExit(1)
