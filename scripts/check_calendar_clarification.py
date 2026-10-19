"""Opt-in live IBM acceptance using synthetic actors, temporary SQLite and mocked Google."""
import argparse
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'backend')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--scenario', choices=['dialogue','amend','planning'], default='dialogue')
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    args = parser.parse_args()
    if not args.verify:
        print('No credentials or services accessed. Use --verify for synthetic live inference.')
        return 0
    logging.disable(logging.CRITICAL)
    from test_orchestrate_calendar import OrchestrateCalendarTests
    from services.orchestrate import OrchestrateProvider
    from services.orchestrate_client import Configuration
    from services.orchestrate_actions import definitions, NAMES
    from services.calendar_tool_inputs import FREQUENCY_QUESTION
    from services.llm_contracts import LLMError, strict_json
    from jsonschema import Draft202012Validator

    fixture = OrchestrateCalendarTests()
    provider = None
    calls = 0
    last_response = ''
    stage = 'setup'
    try:
        fixture.setUp()
        provider = OrchestrateProvider(Configuration.load(args.env_file))
        original = provider._infer

        def bounded_infer(prompt):
            nonlocal calls, last_response
            if calls >= 10:
                raise LLMError('verification_budget')
            calls += 1
            last_response = original(prompt)
            return last_response

        provider._infer = bounded_infer

        class Reasoning:
            structured_actions = True
            prevent_uncertain_replay = True

            def complete(self, messages, **kwargs):
                try:
                    result = provider.complete(messages, **kwargs)
                except LLMError:
                    # Static schema keywords and known action names only. No raw output.
                    try:
                        schemas, _ = definitions(kwargs.get('tools'), kwargs.get('tool_choice'))
                        raw = strict_json(last_response)
                        for item in raw.get('tool_calls', []):
                            name = item.get('name')
                            if name in schemas and name in NAMES:
                                from services.calendar_tool_inputs import normalize_arguments
                                arguments=normalize_arguments(item.get('arguments'),schemas[name])
                                errors=list(Draft202012Validator(schemas[name]).iter_errors(arguments))
                                rules = sorted({e.validator for e in errors})
                                props=schemas[name].get('properties',{})
                                print('Argument type diagnostics: '+json.dumps({k:type(v).__name__ for k,v in item.get('arguments',{}).items() if k in props}),flush=True)
                                print('Rejected fields: '+','.join(sorted({str(e.path[0]) for e in errors if e.path and e.path[0] in props})),flush=True)
                                print('Validation diagnostic: ' + name + '; rules=' + ','.join(rules), flush=True)
                    except Exception:
                        pass
                    raise
                for call in result.tool_calls:
                    if call.name == 'route_request':
                        a=call.arguments
                        print('Routing diagnostic: ' + json.dumps({k:a.get(k) for k in ('intent','relative_range','sessions_per_week','session_count')}) + '; clarification_present=' + str(bool(a.get('clarification'))), flush=True)
                print('Inference submissions=' + str(calls) + '; actions=' +
                      (','.join(c.name for c in result.tool_calls) or 'read reply'), flush=True)
                return result

        reasoning = Reasoning()

        def respond(message, text):
            # Recreate service to prove continuation relies on actor-scoped SQLite history.
            return fixture.service(reasoning).respond(101, message, text)

        if args.scenario == 'amend':
            plan, _ = fixture.two_week_plan(weekly=2)
            plan = next(p for p in fixture.planner.proposals(101) if p['id'] == plan['proposal_id'])
            sessions = plan['sessions']
            starts = [datetime.fromisoformat(item['start']).astimezone(ZoneInfo('Asia/Singapore')) for item in sessions]
        else:
            if args.scenario == 'planning':
                from test_orchestrate_calendar import JSONModel
                fixture.service(fixture.provider_for(JSONModel(relative='next_two_weeks'))).respond(101, 801, 'Can you help me set up study sessions for the next 2 weeks on Machine Learning?')
            stage = 'initial-request'
            result = respond(801, 'Can you help me set up study sessions for the next 2 weeks on Machine Learning?')
            assert result['text'] == FREQUENCY_QUESTION, 'expected frequency question'
            print('PASS: initial request asks frequency.', flush=True)
            stage = 'frequency-range'
            before = calls
            result = respond(802, '2-3 for each week should be enough')
            assert result['text'] == 'Would you prefer 2 or 3 study sessions per week over those two weeks?'
            assert calls == before and not fixture.provider.reads
            print('PASS: frequency range asks one specific choice without another inference.', flush=True)
            stage = 'exact-frequency'
            respond(803, '2 a week should be enough')
            plans = fixture.planner.proposals(101)
            assert len(plans) == 1
            plan = plans[0]
            sessions = plan['sessions']
            starts = [datetime.fromisoformat(s['start']).astimezone(ZoneInfo('Asia/Singapore')) for s in sessions]
            assert len(starts) == 4
            assert sum(s.date() < datetime(2026, 10, 26).date() for s in starts) == 2
            assert sum(s.date() >= datetime(2026, 10, 26).date() for s in starts) == 2
            assert all('machine' in s['title'].lower() for s in sessions)
            assert not fixture.provider.inserts
            assert not fixture.planner.proposals(202)
            print('PASS: four unapproved Machine Learning sessions, two in each week; actor isolation.', flush=True)
    
        stage = 'pending-amendment'
        review_service, old_review = fixture.present_plan(101, {'proposal_id': plan['id']})
        target = starts[0] + timedelta(hours=1)
        respond(804, 'Move only the first proposed session to ' + target.strftime('%I:%M %p') +
                ' on the same day. Keep the other three sessions unchanged.')
        revised = next(p for p in fixture.planner.proposals(101) if p['id'] != plan['id'])
        print('Amendment diagnostics: changed_indices=' + str([i for i, (a,b) in enumerate(zip(sessions,revised['sessions'])) if a != b]) + '; old_review_status=' + review_service.get(101, old_review['id'])['status'], flush=True)
        assert revised['sessions'][1:] == sessions[1:]
        print('Amendment diagnostics: first_session_shift_minutes=' + str((datetime.fromisoformat(revised['sessions'][0]['start']) - starts[0]).total_seconds()/60), flush=True)
        assert datetime.fromisoformat(revised['sessions'][0]['start']) == target
        assert review_service.get(101, old_review['id'])['status'] == 'blocked'
        assert not fixture.provider.inserts
        print('PASS: first-session amendment preserves other three and invalidates old review.', flush=True)

        stage = 'rejection'
        from services.calendar_text_decisions import apply_decision
        fixture.present_plan(101, {'proposal_id': revised['id']})
        rejected = apply_decision(101, 805, "Actually, don't change anything", review_service)
        assert rejected['status'] == 'rejected'
        assert not fixture.provider.inserts
        print('PASS: rejection produces no Google writes.', flush=True)
        print('PASS: synthetic live IBM test completed; scenario=' + args.scenario + '; submissions=' + str(calls) +
              '; no real Calendar/Telegram operations; production untouched.', flush=True)
        return 0
    except LLMError as error:
        print('STOP: stage=' + stage + '; code=' + error.code + '; submissions=' + str(calls) +
              '; no automatic replay or raw payload output.', flush=True)
        return 1
    except Exception as error:
        print('STOP: stage=' + stage + '; error=' + type(error).__name__ +
              '; submissions=' + str(calls) + '; details suppressed.', flush=True)
        return 1
    finally:
        if provider:
            provider.close()
        fixture.doCleanups()


if __name__ == '__main__':
    raise SystemExit(main())
