"""Opt-in native IBM reasoning with temporary SQLite and mocked Calendar/Telegram."""
import argparse
from datetime import datetime, timedelta
import logging
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tests'), str(ROOT/'backend')]


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--env-file', type=Path, default=ROOT/'.env')
    parser.add_argument('--diagnostics', action='store_true', help='Display credential-redacted schema error text for this synthetic harness only.')
    parser.add_argument('--scenario', choices=['dialogue','custom','move','cancel'], default='dialogue')
    args = parser.parse_args()
    if not args.verify:
        print('No credentials or services accessed. Use --verify for bounded synthetic live reasoning.')
        return 0
    logging.disable(logging.CRITICAL)
    from test_calendar_native import NativeCalendarTests
    import test_calendar_actions as fixtures
    from services.calendar_native import NativeCalendarAgent
    from services.orchestrate_native import OrchestrateNativeProvider
    from services.orchestrate_client import Configuration
    from services.conversations import ConversationService
    from services.study_tools import SchedulingTools
    from services.pending_proposals import PendingProposals
    from services.calendar_conversation import CalendarConversation
    from services.calendar_text_decisions import apply_decision
    from services.llm_contracts import LLMError
    from services import profiles

    fixture = NativeCalendarTests()
    provider = None
    calls = 0
    stage = 'setup'
    try:
        fixture.setUp()
        provider = OrchestrateNativeProvider(Configuration.load(args.env_file))
        if args.diagnostics:
            def diagnostic(response):
                if response.status_code != 400:
                    return
                response.read()
                try:
                    error = response.json().get('error', {})
                    if error.get('code') != 'tool_use_failed':
                        return
                    text = str(error.get('message', ''))[:4000]
                    for secret in (provider.transport.config.api_key, provider.transport.token,
                                   provider.transport.config.url, provider.transport.config.agent_id):
                        if secret:
                            text = text.replace(secret, '[redacted]')
                    print('Synthetic schema diagnostic: '+text, flush=True)
                except (ValueError, AttributeError):
                    pass
            provider.client.event_hooks['response'].append(diagnostic)
        class BoundedProvider:
            native_calendar_tools = True
            prevent_uncertain_replay = True
            def complete(self, messages, **kwargs):
                nonlocal calls
                if calls >= 22:
                    raise LLMError('verification_budget')
                calls += 1
                reply = provider.complete(messages, **kwargs)
                print('Native submission '+str(calls)+': '+(', '.join(c.name for c in reply.tool_calls) or 'natural reply'), flush=True)
                if args.diagnostics:
                    if not reply.tool_calls:
                        print('Synthetic conversational reply: '+reply.content[:700],flush=True)
                    for call in reply.tool_calls:
                        if call.name == 'find_calendar_slots':
                            print('Synthetic availability arguments: '+str(call.arguments),flush=True)
                return reply
        reasoning = BoundedProvider()
        agents = []
        def agent_factory(user, message, **kwargs):
            agent = NativeCalendarAgent(user, message, SchedulingTools(user,fixture.planner,fixture.actions), **kwargs)
            agents.append(agent)
            return agent
        def respond(message, text):
            # Recreate services to verify actor-owned persistent conversation context.
            service = ConversationService(reasoning,clock=lambda:fixtures.NOW.timestamp(),agent_factory=agent_factory)
            return service.respond(101,message,text)
        def pending():
            row = PendingProposals(SchedulingTools(101,fixture.planner,fixture.actions)).latest()
            if not row:
                raise AssertionError('missing synthetic proposal')
            return fixture.planner.proposal_record(row)

        original_preferences = profiles.get_profile(101)['preferences']
        if args.scenario in {'dialogue','custom'}:
            if args.scenario == 'dialogue':
                stage = 'broad-request'
                respond(901,'I need you to help me set up some study sessions over the next 2 weeks on the topic of machine learning')
                stage = 'worded-frequency-range'
                respond(902,'I think 2-3 sessions per week should be enough')
                stage = 'short-frequency-answer'
                respond(903,'2')
            else:
                stage = 'custom-instructions'
                respond(901,'Could you pencil in three Machine Learning revision sessions next week? Tuesday, Thursday and Saturday afternoons, twenty minutes each. These are just for this plan; keep my saved settings.')
            plan = pending()
            expected = 4 if args.scenario=='dialogue' else 3
            zone = ZoneInfo('Asia/Singapore')
            starts = [datetime.fromisoformat(s['start']).astimezone(zone) for s in plan['sessions']]
            print('Synthetic proposal diagnostics: count='+str(len(starts))+'; weekdays='+str([s.weekday() for s in starts])+ '; hours='+str([s.hour for s in starts])+'; durations='+str([(datetime.fromisoformat(s['end'])-datetime.fromisoformat(s['start'])).total_seconds()/60 for s in plan['sessions']]),flush=True)
            assert len(plan['sessions']) == expected
            if args.scenario=='dialogue':
                counts = {}
                for start in starts:
                    week = start.date()-timedelta(days=start.weekday())
                    counts[week] = counts.get(week,0)+1
                assert sorted(counts.values()) == [2,2]
            else:
                assert [start.weekday() for start in starts] == [1,3,5]
                assert all(12 <= start.hour < 18 for start in starts)
                assert all(datetime(2026,10,19).date() <= start.date() < datetime(2026,10,26).date() for start in starts)
                assert all(datetime.fromisoformat(s['end'])-datetime.fromisoformat(s['start'])==timedelta(minutes=20) for s in plan['sessions'])
            assert profiles.get_profile(101)['preferences'] == original_preferences
            assert not fixture.provider.inserts and not fixture.planner.proposals(202)
            print('PASS: correct native proposal count/distribution, own-account isolation and unchanged saved defaults.',flush=True)

            stage = 'selected-amendment'
            review_service = CalendarConversation(fixture.actions)
            review,_ = review_service.prepare(101,plan['id'])
            review_service.presented(101,review['id'])
            target = starts[0]+timedelta(hours=1)
            respond(904,'The rest are fine. Could the first session start at '+target.strftime('%I:%M %p')+' on the same day instead? Please leave all the others alone.')
            revised = pending()
            assert revised['sessions'][1:] == plan['sessions'][1:]
            assert datetime.fromisoformat(revised['sessions'][0]['start']) == target
            assert review_service.get(101,review['id'])['status'] == 'blocked'
            assert not fixture.provider.inserts
            print('PASS: natural selected amendment preserves other sessions and invalidates old approval.',flush=True)
            stage = 'human-rejection'
            review,_ = review_service.prepare(101,revised['id'])
            review_service.presented(101,review['id'])
            assert apply_decision(101,905,"Actually, don't change anything",review_service)['status']=='rejected'
            assert not fixture.provider.inserts
            print('PASS: rejection makes no Calendar write.',flush=True)
        else:
            fixture.setup_events()
            before = list(fixture.provider.inserts)
            stage = 'existing-session-'+args.scenario
            text = ('I cannot make my second booked study session. Please cancel just that one, keeping the others.'
                if args.scenario=='cancel' else
                'Could my second booked study session move to Thursday 15 October 2026 at 7 PM instead? Same duration, leave the other sessions alone.')
            respond(901,text)
            changes = [r for agent in agents for r in agent.references() if r.get('review_kind')=='change']
            assert len(changes)==1 and changes[0]['session_index']==1
            assert changes[0]['kind']==('cancel' if args.scenario=='cancel' else 'reschedule')
            assert fixture.provider.inserts==before and not fixture.moves and not fixture.deletes
            print('PASS: selected existing-session '+args.scenario+' proposed, never executed.',flush=True)
        print('PASS: scenario='+args.scenario+'; native model submissions='+str(calls)+'; no real Calendar/Telegram operations or deployment.',flush=True)
        return 0
    except LLMError as error:
        print('STOP: stage='+stage+'; code='+error.code+'; submissions='+str(calls)+'; no automatic replay or raw payloads.',flush=True)
        if provider and getattr(provider, 'last_error_metadata', None):
            print('Sanitized transport metadata: '+str(provider.last_error_metadata),flush=True)
        return 1
    except Exception as error:
        print('STOP: stage='+stage+'; error='+type(error).__name__+'; submissions='+str(calls)+'; details suppressed.',flush=True)
        return 1
    finally:
        if provider:
            provider.close()
        fixture.doCleanups()


if __name__=='__main__':
    raise SystemExit(main())
