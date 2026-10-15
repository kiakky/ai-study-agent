"""Exact live-message reproduction with synthetic users/Google and temporary SQLite."""
from contextlib import closing
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import json,unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import test_calendar_actions as fixtures
from test_calendar_agent import ScriptedIBM
from services.pending_proposals import PendingProposals,AmendmentInput
from services.calendar_agent import CalendarAgent
from services.study_tools import SchedulingTools
from services.calendar_conversation import CalendarConversation
from services.calendar_text_decisions import explicit_decision,apply_decision
from services.calendar_telegram import show_review
from services.planning import PlanningError
from services.llm_contracts import ToolCall,ChatReply,LLMError
from services import profiles
from models.preferences import StudyPreferences
from models.planning import ProposalInput
import database,asyncio


LIVE_MESSAGE="Monday and Wednesday look good but I'd rather friday's study session be from 3pm to 320pm"


class PendingTests(unittest.TestCase):
    setUp=fixtures.CalendarActionTests.setUp
    connect=fixtures.CalendarActionTests.connect

    def plan(self,user=101):
        profiles.save_preferences(user,StudyPreferences(study_days=['mon','wed','fri'],session_minutes=20,
            study_windows=[{'start':'14:00','end':'16:00'}]))
        goal=self.goal if user==101 else self.planner.save_goal(user,__import__('models.planning',fromlist=['StudyGoal']).StudyGoal(subject='SQL'),uuid4())
        proposal=self.planner.propose(user,goal['id'],ProposalInput(request_id=uuid4(),start=datetime(2026,10,19,tzinfo=timezone.utc),sessions=3))
        self.review_service=CalendarConversation(self.actions)
        review,data=self.review_service.prepare(user,proposal['id']);self.review_service.presented(user,review['id'])
        self.old_review=review
        return proposal

    def pending(self,user=101):return PendingProposals(SchedulingTools(user,self.planner,self.actions))

    def request(self,pending,index=2,hour=15):
        snapshot=pending.snapshot()
        return AmendmentInput(proposal_id=snapshot['proposal_id'],proposal_digest=snapshot['proposal_digest'],
            session_index=index,local_start=f'{hour}:00',local_end=f'{hour}:20')

    def present(self,proposal_id,user=101):
        review,data=self.review_service.prepare(user,proposal_id);self.review_service.presented(user,review['id']);return review

    def test_exact_live_message_amends_only_friday_in_one_model_tool(self):
        original=self.plan();pending=self.pending();req=self.request(pending)
        provider=ScriptedIBM([ChatReply('',(ToolCall(id='amend',name='amend_pending_proposal',arguments={
            **req.model_dump(mode='json'),'session_index':'2'}),))],intent='amendment')
        agent=CalendarAgent(101,10,SchedulingTools(101,self.planner,self.actions))
        reply=agent.complete(provider,[{'role':'system','content':'Study assistant.'},{'role':'user','content':LIVE_MESSAGE}])
        result=next(r for r in agent.references() if r.get('review_kind')=='plan')
        self.assertEqual(result['sessions'][:2],original['sessions'][:2])
        self.assertEqual(datetime.fromisoformat(result['sessions'][2]['start']).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Singapore')).hour,15)
        self.assertEqual(len(provider.requests),2)
        self.assertIn('Current pending proposal',provider.requests[1][0]['content'])
        self.assertEqual([d['function']['name'] for d in agent.model_definitions() if d['function']['name'] in agent.allowed_names],['amend_pending_proposal'])
        self.assertIn('Nothing is approved',reply.content);self.assertEqual(self.provider.inserts,[])
        self.assertEqual(self.review_service.get(101,self.old_review['id'])['status'],'blocked')
        self.assertEqual(self.review_service.decide(101,self.old_review['id'],'approve')['status'],'blocked')

    def test_multi_turn_revisions_then_one_natural_approval_creates_all_three(self):
        original=self.plan();pending=self.pending()
        first=pending.amend(self.request(pending),10,0)
        self.present(first['proposal_id'])
        second=pending.amend(self.request(pending,index=0,hour=15),11,0)
        self.assertEqual(second['sessions'][1],original['sessions'][1])
        self.assertEqual(second['sessions'][2],first['sessions'][2])
        self.present(second['proposal_id'])
        result=apply_decision(101,12,'Yes, approve all three',self.review_service)
        self.assertEqual(result['status'],'completed');self.assertEqual(len(self.provider.inserts),3)
        self.assertEqual(apply_decision(101,12,'Yes, approve all three',self.review_service),result)
        self.assertEqual(apply_decision(101,13,'Looks good, add them to my calendar',self.review_service)['status'],'completed')
        self.assertEqual(len(self.provider.inserts),3)

    def test_unseen_adjustment_and_conditional_approval_cannot_authorize_new_schedule(self):
        self.plan();pending=self.pending()
        pending.amend(self.request(pending),10,0)
        self.assertRaises(PlanningError,apply_decision,101,11,'Yes approve all three',self.review_service)
        self.assertIsNone(explicit_decision(LIVE_MESSAGE))
        self.assertIsNone(explicit_decision('Move Friday to 3pm and approve all three'))
        self.assertIsNone(explicit_decision('yes'))
        self.assertIsNone(explicit_decision('looks good'))
        self.assertEqual(self.provider.inserts,[])

    def test_rejection_preserves_context_for_new_revision(self):
        original=self.plan();apply_decision(101,9,'Reject this plan',self.review_service)
        self.assertEqual(self.pending().snapshot()['status'],'rejected')
        revised=self.pending().amend(self.request(self.pending()),10,0)
        self.assertEqual(self.review_service.get(101,self.old_review['id'])['status'],'rejected')
        self.assertEqual(revised['sessions'][:2],original['sessions'][:2])
        self.present(revised['proposal_id'])
        self.assertEqual(apply_decision(101,11,'Yes approve all three',self.review_service)['status'],'completed')
        self.assertEqual(len(self.provider.inserts),3)

    def test_conflict_invalid_duration_and_stale_digest_leave_old_plan_intact(self):
        original=self.plan();pending=self.pending();req=self.request(pending)
        self.assertRaises(PlanningError,pending.amend,req.model_copy(update={'proposal_digest':'0'*64}),10,0)
        self.assertRaises(PlanningError,pending.amend,req.model_copy(update={'local_start':__import__('datetime').time(17),'local_end':__import__('datetime').time(21)}),11,0)
        target=datetime(2026,10,23,7,tzinfo=timezone.utc)
        with patch.object(self.calendar,'availability',return_value={'busy':[{'start':target.isoformat(),'end':(target+timedelta(minutes=20)).isoformat()}]}):
            self.assertRaises(PlanningError,pending.amend,req,12,0)
        self.assertEqual(pending.snapshot()['proposal_id'],original['id'])
        self.assertEqual(self.review_service.get(101,self.old_review['id'])['status'],'pending')

    def test_cross_user_digest_count_reply_binding_and_expiry_fail_closed(self):
        self.plan(101);a=self.pending(101);req=self.request(a)
        self.plan(202)
        self.assertRaises(PlanningError,self.pending(202).amend,req,10,0)
        self.assertRaises(PlanningError,apply_decision,101,11,'Approve all two',self.review_service)
        self.assertRaises(PlanningError,apply_decision,101,12,'Approve all three',self.review_service,reply_review=self.old_review['id'])
        service=CalendarConversation(self.actions);service.clock=lambda:fixtures.NOW+timedelta(minutes=11)
        self.assertRaises(PlanningError,apply_decision,101,13,'Approve all three',service)
        self.assertEqual(self.provider.inserts,[])

    def test_repeated_failed_amendment_stops_before_tool_limit_and_asks_useful_correction(self):
        self.plan();req=self.request(self.pending()).model_copy(update={'session_index':99})
        calls=[ChatReply('',(ToolCall(id=str(n),name='amend_pending_proposal',arguments=req.model_dump(mode='json')),)) for n in range(4)]
        provider=ScriptedIBM(calls,intent='amendment')
        result=CalendarAgent(101,10,SchedulingTools(101,self.planner,self.actions)).complete(provider,[{'role':'system','content':'Study'},{'role':'user','content':'Move the second session.'}])
        self.assertIn('not in this pending proposal',result.content)
        self.assertEqual(len(provider.requests),3);self.assertEqual(self.provider.inserts,[])

    def test_full_revised_schedule_delivered_before_final_approval_button(self):
        self.plan();revised=self.pending().amend(self.request(self.pending()),10,0)
        message=SimpleNamespace(reply_text=AsyncMock())
        asyncio.run(show_review(message,101,revised['proposal_id'],self.review_service))
        text='\n'.join(c.args[0] for c in message.reply_text.call_args_list[:-1])
        self.assertIn('19 Oct 2026',text);self.assertIn('21 Oct 2026',text);self.assertIn('23 Oct 2026, 15:00',text)
        self.assertIn('reply_markup',message.reply_text.call_args_list[-1].kwargs)
        self.assertEqual(self.provider.inserts,[])

    def test_early_approval_replay_does_not_bind_to_later_presented_revision(self):
        self.plan();new=self.pending().amend(self.request(self.pending()),10,0)
        self.assertRaises(PlanningError,apply_decision,101,11,'Approve all three',self.review_service)
        self.present(new['proposal_id'])
        self.assertEqual(apply_decision(101,11,'Approve all three',self.review_service)['status'],'blocked')
        self.assertEqual(self.provider.inserts,[])
        self.assertEqual(apply_decision(101,12,'Approve all three',self.review_service)['status'],'completed')

    def test_approval_rechecks_conflicts_and_batch_retry_does_not_duplicate(self):
        self.plan();pending=self.pending();new=pending.amend(self.request(pending),10,0);self.present(new['proposal_id'])
        conflict=new['sessions'][2]
        with patch.object(self.calendar,'availability',return_value={'busy':[conflict]}):
            self.assertRaises(PlanningError,apply_decision,101,11,'Approve all three',self.review_service)
        self.assertEqual(self.provider.inserts,[])
        original=self.provider.create_event;calls=[]
        def fail_once(value,key,body):
            calls.append(key)
            if len(calls)==2:raise TimeoutError('Synthetic partial failure')
            return original(value,key,body)
        self.provider.create_event=fail_once
        self.assertEqual(apply_decision(101,12,'Approve all three',self.review_service)['status'],'partial')
        self.provider.create_event=original
        self.assertEqual(apply_decision(101,13,'Approve all three',self.review_service)['status'],'completed')
        self.assertEqual(len(self.provider.inserts),3)

    def test_approval_racing_amendment_prevents_any_new_revision(self):
        original=self.plan();pending=self.pending();request=self.request(pending)
        original_busy=self.planner.busy;triggered=[]
        def race(*args,**kwargs):
            if not triggered:
                triggered.append(True)
                apply_decision(101,20,'Approve all three',self.review_service)
            return original_busy(*args,**kwargs)
        with patch.object(self.planner,'busy',side_effect=race):
            self.assertRaises(PlanningError,pending.amend,request,21,0)
        self.assertEqual(len(self.planner.proposals(101)),1)
        self.assertEqual(len(self.provider.inserts),3)

    def test_telegram_natural_approval_bypasses_llm_and_uses_own_review(self):
        from services.conversation_telegram import handle_text
        from services.conversations import ConversationSettings
        self.plan()
        def update(user,id):return SimpleNamespace(effective_user=SimpleNamespace(id=user),effective_chat=SimpleNamespace(id=user,type='private'),
            message=SimpleNamespace(message_id=id,text='Yes approve all three',reply_to_message=None,reply_text=AsyncMock()))
        a=update(101,50);b=update(202,50);context=SimpleNamespace(bot_data={})
        original=apply_decision
        with (patch('services.conversation_telegram.load_settings',return_value=ConversationSettings(True)),patch('services.conversation_telegram.configured_testers',return_value={101,202}),patch('services.conversation_telegram.dispatcher',side_effect=AssertionError('No LLM approval')),
            patch('services.calendar_text_decisions.apply_decision',side_effect=lambda u,m,t,**kwargs:original(u,m,t,self.review_service,**kwargs))):
            asyncio.run(handle_text(b,context));self.assertEqual(self.provider.inserts,[])
            asyncio.run(handle_text(a,context))
        self.assertEqual(len(self.provider.inserts),3)
        self.assertIn('completed',a.message.reply_text.call_args.args[0])

    def test_forwarded_and_quoted_text_is_not_user_approval(self):
        from services.conversation_telegram import plan_text_decision
        self.plan()
        self.assertIsNone(explicit_decision('"Yes approve all three"'))
        update=SimpleNamespace(message=SimpleNamespace(text='Yes approve all three',forward_origin=object(),reply_text=AsyncMock()))
        with patch('services.calendar_text_decisions.apply_decision',side_effect=AssertionError('No forwarded approval')):
            asyncio.run(plan_text_decision(update,None,101))
        self.assertEqual(self.provider.inserts,[])

    def test_second_session_reference_preserves_first_and_third(self):
        original=self.plan();pending=self.pending();revised=pending.amend(self.request(pending,index=1),10,0)
        self.assertEqual(revised['sessions'][0],original['sessions'][0])
        self.assertEqual(revised['sessions'][2],original['sessions'][2])
        self.assertNotEqual(revised['sessions'][1]['start'],original['sessions'][1]['start'])
