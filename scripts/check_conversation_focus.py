"""Opt-in synthetic conversation-focus checks; no live student or Calendar data."""
import argparse, ast, json, logging, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tests'), str(ROOT/'backend')]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--env-file', type=Path, default=ROOT/'.env')
    parser.add_argument('--baseline-source', type=Path)
    args = parser.parse_args()
    if not args.verify:
        print('No credentials or services accessed. Use --verify for bounded synthetic inference.')
        return 0
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    logging.disable(logging.CRITICAL)
    from test_calendar_native import NativeCalendarTests, ScriptedModel
    import test_calendar_actions as fixtures
    import services.calendar_native as native
    from services.orchestrate_native import OrchestrateNativeProvider
    from services.orchestrate_client import Configuration
    from services.llm_contracts import ChatReply, LLMError
    from services.conversations import ConversationService
    from services.study_tools import SchedulingTools
    if args.baseline_source:
        tree = ast.parse(args.baseline_source.read_text(encoding='utf-8'))
        native.INSTRUCTIONS = next(ast.literal_eval(node.value) for node in tree.body
            if isinstance(node, ast.Assign) and any(isinstance(t,ast.Name) and t.id=='INSTRUCTIONS' for t in node.targets))
    provider = OrchestrateNativeProvider(Configuration.load(args.env_file))
    calls = 0
    failures = 0
    class ReadOnlyReasoning:
        prevent_uncertain_replay = True
        def complete(self, messages, **kwargs):
            nonlocal calls
            calls += 1
            if calls > 8: raise LLMError('verification_budget')
            reply = provider.complete(messages, **kwargs)
            if reply.tool_calls:
                print('FAIL unwanted task tools: '+', '.join(c.name for c in reply.tool_calls))
                raise LLMError('unrequested_task')
            print('Synthetic response: '+reply.content, flush=True)
            if any(term in reply.content.lower() for term in ['14:00','slot_reference','slot `','2 sessions','four sessions','4 sessions', 'two study sessions', 'four study sessions', 'which weekdays', 'preferred days of the week']):
                raise LLMError('old_task_resumed')
            return reply
    try:
        for latest in ['hi', 'new convo', 'Can we wipe the slate clean and talk about something else?', 'Forget the schedule for now. Explain overfitting in one sentence.']:
            fixture = NativeCalendarTests()
            try:
                fixture.setUp()
                def factory(u,m,**kw):
                    return native.NativeCalendarAgent(u,m,SchedulingTools(u,fixture.planner,fixture.actions),**kw)
                service = ConversationService(ScriptedModel([ChatReply('How many sessions each week?'),
                    ChatReply('Got it, two per week. Which days would you prefer?')]),
                    clock=lambda:fixtures.NOW.timestamp(),agent_factory=factory)
                service.respond(101,1,'Help me plan Machine Learning over the next two weeks')
                service.respond(101,2,'2 a week should be enough')
                service = ConversationService(ReadOnlyReasoning(),clock=lambda:fixtures.NOW.timestamp(),agent_factory=factory)
                print('Latest synthetic message: '+latest,flush=True)
                try:
                    service.respond(101,3,latest)
                    if latest == 'new convo':
                        service.respond(101,4,'How can you help me?')
                    print('PASS latest-message focus; no planning tools or Google writes',flush=True)
                except LLMError as error:
                    failures += 1
                    print('FAIL '+error.code,flush=True)
                assert not fixture.provider.inserts
            finally:
                fixture.doCleanups()
    finally:
        provider.close()
    print('Synthetic submissions: '+str(calls)+'; failed scenarios: '+str(failures))
    return 1 if failures else 0

if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as error:
        print('STOP '+type(error).__name__+'; details suppressed; no automatic replay')
        raise SystemExit(1)
