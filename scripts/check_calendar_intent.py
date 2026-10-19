"""Opt-in IBM diagnostic: synthetic users, temporary DB, mocked Google only."""
import argparse
import json
import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'backend')]
import test_calendar_actions as fixtures
from services.calendar_agent import CalendarAgent
from services.study_tools import SchedulingTools
from services.watsonx import WatsonxCredentials, WatsonxProvider
from services.conversations import system_instructions
from services.llm_contracts import LLMError
from services import profiles
from models.preferences import StudyPreferences
from services.pending_proposals import PendingProposals
from dotenv import load_dotenv

REQUEST = "Can you address me as kiakky from now on? Also I want to study more on game development, so could you help me schedule a few study sessions next week? I might be busy next Tuesday, so try to target Thursday and the weekends' afternoons."


class TraceProvider:
    def __init__(self, provider): self.provider = provider; self.calls = 0
    def complete(self, messages, **kwargs):
        self.calls += 1
        result = self.provider.complete(messages, **kwargs)
        # These arguments and messages are exclusively synthetic fixtures.
        print('IBM synthetic response:', json.dumps({'call': self.calls,
            'tools': [{'name': c.name, 'arguments': c.arguments} for c in result.tool_calls],
            'prose': result.content}, ensure_ascii=True))
        return result


def verify_scenarios(provider, fixture):
    """Additional actual IBM requests, still entirely synthetic and write-free."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    zone=ZoneInfo('Asia/Singapore')
    before={user:profiles.get_profile(user)['preferences'] for user in (101,202)}
    def run(user, message_id, text, history=()):
        agent=CalendarAgent(user,message_id,SchedulingTools(user,fixture.planner,fixture.actions))
        trace=TraceProvider(provider)
        reply=agent.complete(trace,[{'role':'system','content':system_instructions(fixtures.NOW,'Asia/Singapore',True)},
            *history,{'role':'user','content':text}])
        assert not any(name in reply.content for name in agent.registry._tools)
        assert not fixture.provider.inserts
        return agent,reply
    # Latest current request wins conflicting earlier context and saved settings.
    agent,reply=run(202,502,'Schedule three game development sessions next week on Thursday and the weekend evenings, 45 minutes each.',
        [{'role':'user','content':'I like mornings for revision.'},{'role':'assistant','content':'I will remember that conversational preference.'}])
    plan=next(r for r in agent.references() if r.get('review_kind')=='plan')
    assert all(datetime.fromisoformat(s['start']).astimezone(zone).hour>=18 for s in plan['sessions'])
    assert all((datetime.fromisoformat(s['end'])-datetime.fromisoformat(s['start'])).total_seconds()==2700 for s in plan['sessions'])
    # Account A's original request and exact adjustment, with narrow saved windows.
    profiles.save_preferences(101,StudyPreferences(study_days=['mon','wed','fri'],session_minutes=20,
        study_windows=[{'start':'14:00','end':'14:30'}]))
    agent,reply=run(101,503,'Propose three Python revision sessions next week using my saved preferences and Calendar availability.')
    old=next(r for r in agent.references() if r.get('review_kind')=='plan')
    amendment,reply=run(101,504,"Monday and Wednesday look good but I'd rather friday's study session be from 3pm to 320pm",
        [{'role':'user','content':'Propose three Python sessions next week.'},
         {'role':'assistant','content':'Pending three Python sessions Monday, Wednesday, Friday 2:00-2:20 PM; Asia/Singapore.'}])
    new=next(r for r in amendment.references() if r.get('review_kind')=='plan')
    assert new['sessions'][:2]==old['sessions'][:2]
    assert datetime.fromisoformat(new['sessions'][2]['start']).astimezone(zone).hour==15
    # Lasting changes produce only the existing confirmation review, not a save.
    agent,reply=run(202,505,'From now on, I prefer studying evenings from 6pm to 10pm. Please save that preference.')
    assert next(r for r in agent.references() if r.get('review_kind')=='preferences')['saved'] is False
    agent,reply=run(202,506,'hello')
    assert not agent.references()
    agent,reply=run(202,507,'What name did I ask you to use?',
        [{'role':'user','content':REQUEST},{'role':'assistant','content':'Your proposed game development sessions are ready for review.'}])
    assert 'kiakky' in reply.content.casefold() and not agent.references()
    assert profiles.get_profile(202)['preferences']==before[202]
    assert not fixture.provider.inserts
    print('PASS: actual IBM context overrides, 45-minute evenings, Account A planning/amendment, lasting-change confirmation, greeting and preferred-name follow-up. No writes.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    load_dotenv(args.env_file, override=False)
    provider = WatsonxProvider(WatsonxCredentials.from_environment(), 'meta-llama/llama-3-3-70b-instruct')
    def safe_error_metadata(response):
        if response.status_code>=400:
            response.read()
            try:
                body=response.json()
                codes={e.get('code') for e in body.get('errors',[]) if isinstance(e,dict)}
            except (ValueError,AttributeError,TypeError):codes=set()
            # Never display error payloads/messages or arbitrary provider strings.
            quota=bool(codes & {'token_quota_reached','token_quota_exceeded','quota_exceeded'})
            print('IBM synthetic error metadata: HTTP '+str(response.status_code)+'; known token-quota code: '+str(quota))
    provider.client.event_hooks['response'].append(safe_error_metadata)
    fixture = fixtures.CalendarActionTests()
    try:
        fixture.setUp()
        profiles.save_preferences(202, StudyPreferences(study_days=['mon','wed','fri'],
            study_windows=[{'start':'09:00','end':'11:00'}], session_minutes=30))
        before = profiles.get_profile(202)['preferences']
        agent = CalendarAgent(202, 501, SchedulingTools(202, fixture.planner, fixture.actions))
        trace = TraceProvider(provider)
        messages = [{'role':'system', 'content':system_instructions(fixtures.NOW,'Asia/Singapore',True)},
                    {'role':'user','content':REQUEST}]
        try:
            reply = agent.complete(trace, messages)
            print('Synthetic final reply:', reply.content)
        except LLMError as error:
            print('Synthetic orchestration failure:', error.code)
            if args.verify: raise
        results = agent.references()
        print('Executed synthetic tools:', json.dumps([{'review_kind':r.get('review_kind'),
            'error_code':r.get('error_code'),'period_count':len(r.get('periods',[]))} for r in results]))
        assert not fixture.provider.inserts
        assert profiles.get_profile(202)['preferences'] == before
        if args.verify:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            plan = next(r for r in results if r.get('review_kind') == 'plan')
            sessions = plan['sessions']
            assert len(sessions) == 3
            assert [datetime.fromisoformat(s['start']).astimezone(ZoneInfo('Asia/Singapore')).weekday() for s in sessions] == [3,5,6]
            assert all(12 <= datetime.fromisoformat(s['start']).astimezone(ZoneInfo('Asia/Singapore')).hour < 18 for s in sessions)
            print('PASS: one-request proposal; Thursday/weekend afternoons; saved settings unchanged; no Calendar writes.')
            verify_scenarios(provider,fixture)
        return 0
    except LLMError as error:
        print('STOP: IBM synthetic verification failed ('+error.code+'); no provider payloads or secrets displayed.')
        return 1
    except (AssertionError,StopIteration):
        print('STOP: synthetic behavior did not meet assertions; no private data or Calendar writes involved.')
        return 1
    finally:
        fixture.doCleanups()
        provider.close()


if __name__ == '__main__': raise SystemExit(main())
