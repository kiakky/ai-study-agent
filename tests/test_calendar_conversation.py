"""Synthetic two-account regressions; never touches live credentials or Google."""
import asyncio
from contextlib import closing
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import unittest
from urllib.parse import parse_qs,urlsplit
from uuid import uuid4

import test_calendar_actions as fixtures
from services.calendar_conversation import CalendarConversation
from services.calendar_management import CalendarSessionManagement
from services.calendar_telegram import show_review,review_callback,private_user
from services.planning import PlanningError
from services.google_oauth import ReconnectRequired
from models.planning import StudyGoal,ProposalInput
from services import profiles
import database


class CalendarConversationTests(unittest.TestCase):
    setUp=fixtures.CalendarActionTests.setUp
    connect=fixtures.CalendarActionTests.connect

    def draft(self,user=101,sessions=1):
        goal=self.goal if user==101 else self.planner.save_goal(user,StudyGoal(subject='SQL'),uuid4())
        return self.planner.propose(user,goal['id'],ProposalInput(request_id=uuid4(),start=fixtures.NOW,sessions=sessions))

    def review(self,user=101,sessions=1):
        service=CalendarConversation(self.actions)
        review,data=service.prepare(user,self.draft(user,sessions)['id'])
        service.presented(user,review['id'])
        return service,review

    def finish_consent(self,link,code='a',error=None):
        ticket=parse_qs(urlsplit(link).query)['ticket'][0]
        url,cookie=self.calendar.start_browser(ticket)
        state=parse_qs(urlsplit(url).query)['state'][0]
        return self.calendar.callback(state,cookie,code=None if error else code,error=error)

    def test_one_telegram_approval_executes_for_each_user_and_duplicate_is_noop(self):
        for user in (101,202):
            service,review=self.review(user)
            result=service.decide(user,review['id'],'approve')
            self.assertEqual(result['status'],'completed')
            self.assertEqual(service.decide(user,review['id'],'approve')['status'],'completed')
        self.assertEqual([owner for owner,key in self.provider.inserts],['synthetic-access-a','synthetic-access-b'])

    def test_rejection_and_changes_prevent_writes(self):
        for user,decision in ((101,'reject'),(202,'changes')):
            service,review=self.review(user)
            result=service.decide(user,review['id'],decision)
            self.assertIn(result['status'],{'rejected','changes'})
            service.decide(user,review['id'],'approve')
        self.assertEqual(self.provider.inserts,[])

    def test_other_user_and_unpresented_review_cannot_approve(self):
        service=CalendarConversation(self.actions)
        review,data=service.prepare(101,self.draft()['id'])
        self.assertRaises(LookupError,service.decide,202,review['id'],'approve')
        self.assertRaises(PlanningError,service.decide,101,review['id'],'approve')
        self.assertEqual(self.provider.inserts,[])

    def test_upgrade_continues_exact_approval_and_persists_across_restart(self):
        self.connect(101,'a',False)
        service,review=self.review()
        generation=self.planner.generation(101)
        result=service.decide(101,review['id'],'approve')
        self.assertEqual(result['status'],'consent')
        self.assertEqual(self.planner.generation(101),generation)
        self.assertEqual(self.provider.inserts,[])
        self.finish_consent(result['authorization_start_url'])
        restarted=CalendarConversation(self.actions)
        self.assertEqual(restarted.resume_consent(101,review['id'])['status'],'completed')
        restarted.resume_consent(101,review['id'])
        self.assertEqual(len(self.provider.inserts),1)

    def test_cancelled_consent_retains_connection_and_generation_no_write(self):
        self.connect(101,'a',False)
        service,review=self.review();generation=self.planner.generation(101)
        result=service.decide(101,review['id'],'approve')
        self.finish_consent(result['authorization_start_url'],error='access_denied')
        self.assertEqual(service.resume_consent(101,review['id'])['status'],'cancelled')
        self.assertEqual(self.planner.generation(101),generation)
        self.assertEqual(self.calendar.status(101)['status'],'connected')
        self.calendar.availability(101,fixtures.NOW,fixtures.NOW+timedelta(hours=1))
        self.assertEqual(self.provider.inserts,[])

    def test_expired_or_tampered_consent_review_never_executes(self):
        self.connect(101,'a',False)
        service,review=self.review();result=service.decide(101,review['id'],'approve')
        self.finish_consent(result['authorization_start_url'])
        service.clock=lambda:fixtures.NOW+timedelta(minutes=11)
        self.assertRaises(PlanningError,service.resume_consent,101,review['id'])
        self.assertEqual(self.provider.inserts,[])

    def test_unrelated_reconnection_cannot_resume_pending_approval(self):
        self.connect(101,'a',False)
        service,review=self.review();result=service.decide(101,review['id'],'approve')
        self.finish_consent(result['authorization_start_url'])
        self.connect(101,'another-account',True)
        self.assertRaises(PlanningError,service.resume_consent,101,review['id'])
        self.assertEqual(self.provider.inserts,[])

    def test_expired_review_can_reconcile_lost_success_but_not_insert_new_events(self):
        service,review=self.review()
        self.provider.lose_response=True
        self.assertEqual(service.decide(101,review['id'],'approve')['status'],'partial')
        service.clock=lambda:fixtures.NOW+timedelta(minutes=11)
        self.actions.clock=service.clock
        self.assertEqual(service.execute(101,review['id'])['status'],'completed')
        self.assertEqual(len(self.provider.inserts),1)

    def test_normal_expiry_refreshes_without_reconnection(self):
        with closing(database.get_connection()) as db,db:db.execute('UPDATE google_connections SET expires_at=0 WHERE telegram_user_id=101')
        self.assertEqual(self.calendar.status(101)['status'],'connected')
        service,review=self.review()
        self.assertEqual(service.decide(101,review['id'],'approve')['status'],'completed')
        self.assertEqual(self.provider.inserts[0][0],'synthetic-access-a')

    def test_reconnect_preserves_previously_granted_event_permission(self):
        link=self.calendar.begin(101)['authorization_start_url']
        self.finish_consent(link)
        self.assertTrue(self.calendar.status(101)['event_permission_granted'])
        self.assertEqual(self.provider.inserts,[])

    def test_changes_text_is_persistent_and_user_isolated(self):
        service,review=self.review()
        service.decide(101,review['id'],'changes')
        self.assertFalse(service.consume_adjustment(202,'Wrong account'))
        self.assertTrue(service.consume_adjustment(101,'Please use Friday instead.'))
        self.assertEqual(service.adjustments(202),[])
        self.assertEqual(CalendarConversation(self.actions).adjustments(101)[0]['adjustment_text'],'Please use Friday instead.')
        self.assertEqual(self.provider.inserts,[])

    def test_callback_uses_authenticated_user_and_delivery_failure_does_not_repeat_write(self):
        service,review=self.review()
        query=SimpleNamespace(data='cr:approve:'+review['id'],answer=AsyncMock(),edit_message_text=AsyncMock(),message=SimpleNamespace(reply_text=AsyncMock()))
        update=SimpleNamespace(effective_user=SimpleNamespace(id=202),effective_chat=SimpleNamespace(id=202,type='private'),callback_query=query)
        with patch('services.calendar_telegram.CalendarConversation',return_value=service):
            asyncio.run(review_callback(update,None))
            self.assertEqual(self.provider.inserts,[])
            update.effective_user.id=101;update.effective_chat.id=101
            from telegram.error import NetworkError
            query.edit_message_text.side_effect=NetworkError('synthetic')
            asyncio.run(review_callback(update,None))
            asyncio.run(review_callback(update,None))
        self.assertEqual(len(self.provider.inserts),1)
        self.assertEqual(service.get(101,review['id'])['status'],'completed')

    def test_18_exact_sessions_over_six_weeks_partial_retry(self):
        sessions=[]
        for week in range(6):
            for day in (0,2,4):
                start=fixtures.NOW+timedelta(days=week*7+day,hours=10)
                sessions.append({'title':'Study Python','topic':'Python','start':start.isoformat(),'end':(start+timedelta(minutes=30)).isoformat()})
        proposal=self.planner.propose(101,self.goal['id'],ProposalInput(request_id=uuid4(),start=fixtures.NOW,days=42,sessions=18),proposed_sessions=sessions)
        service=CalendarConversation(self.actions)
        review,data=service.prepare(101,proposal['id']);service.presented(101,review['id'])
        original=self.provider.create_event;calls=[]
        def fail_once(value,key,body):
            calls.append(key)
            if len(calls)==8:raise TimeoutError('synthetic')
            return original(value,key,body)
        self.provider.create_event=fail_once
        self.assertEqual(service.decide(101,review['id'],'approve')['status'],'partial')
        self.assertEqual(len(self.provider.inserts),7)
        self.provider.create_event=original
        self.assertEqual(service.execute(101,review['id'])['status'],'completed')
        self.assertEqual(len(self.provider.inserts),18)
        self.assertEqual(len(set(self.provider.inserts)),18)
        with closing(database.get_connection()) as db:
            self.assertGreater(db.execute('SELECT COUNT(*) FROM calendar_action_audit').fetchone()[0],18)

    def test_all_details_delivered_before_batch_approval_button(self):
        proposal=self.draft(sessions=3);message=SimpleNamespace(reply_text=AsyncMock())
        asyncio.run(show_review(message,101,proposal['id'],CalendarConversation(self.actions)))
        calls=message.reply_text.call_args_list
        text='\n'.join(call.args[0] for call in calls[:-1])
        for session in proposal['sessions']:self.assertIn(session['title'],text)
        self.assertNotIn('T10:',text)
        self.assertIn('reply_markup',calls[-1].kwargs)
        self.assertEqual(self.provider.inserts,[])

    def test_failed_delivery_does_not_present_approval_buttons(self):
        proposal=self.draft(sessions=3);message=SimpleNamespace(reply_text=AsyncMock(side_effect=RuntimeError('synthetic')))
        with self.assertRaises(RuntimeError):asyncio.run(show_review(message,101,proposal['id'],CalendarConversation(self.actions)))
        with closing(database.get_connection()) as db:
            self.assertEqual(db.execute('SELECT presented FROM calendar_reviews').fetchone()[0],0)
        self.assertEqual(self.provider.inserts,[])


class CalendarManagementTests(unittest.TestCase):
    setUp=fixtures.CalendarActionTests.setUp
    connect=fixtures.CalendarActionTests.connect
    proposal=fixtures.CalendarActionTests.proposal
    approved_action=fixtures.CalendarActionTests.approved_action

    def setup_events(self):
        proposal,action=self.approved_action(3)
        self.actions.execute(101,action['id'])
        self.moves=[];self.deletes=[];self.lose_move=False;self.lose_delete=False
        for event in self.provider.events.values():event['etag']='initial'
        def move(value,key,body,etag):
            from googleapiclient.errors import HttpError
            from httplib2 import Response
            event=self.provider.events[(value['token'],key)]
            if event['etag']!=etag:raise HttpError(Response({'status':'412'}),b'{}')
            event.update(start=body['start'],end=body['end'],etag='moved')
            self.moves.append((value['token'],key))
            if self.lose_move:self.lose_move=False;raise TimeoutError('synthetic lost response')
            return event
        def cancel(value,key,etag):
            event=self.provider.events[(value['token'],key)]
            if event['etag']!=etag:raise RuntimeError('synthetic precondition failure')
            del self.provider.events[(value['token'],key)];self.deletes.append((value['token'],key))
            if self.lose_delete:self.lose_delete=False;raise TimeoutError('synthetic lost response')
        self.provider.reschedule_event=move;self.provider.cancel_event=cancel
        self.management=CalendarSessionManagement(self.actions)
        return proposal,action

    def move_proposal(self,action,index=0):
        start=fixtures.NOW+timedelta(days=3,hours=11)
        return self.management.propose(101,action['id'],index,'reschedule',start.isoformat(),(start+timedelta(minutes=30)).isoformat())

    def test_reschedule_only_selected_session_then_cancel_another(self):
        proposal,action=self.setup_events();before=json.loads(json.dumps(list(self.provider.events.values())))
        change=self.move_proposal(action)
        self.assertEqual(self.moves,[])
        self.assertEqual(self.management.decide(101,change['id'],'approve')['status'],'completed')
        self.management.decide(101,change['id'],'approve')
        self.assertEqual(len(self.moves),1)
        remaining=[event for event in self.provider.events.values() if event['id']!=self.moves[0][1]]
        self.assertEqual(remaining,[event for event in before if event['id']!=self.moves[0][1]])
        cancel=self.management.propose(101,action['id'],1,'cancel')
        self.assertEqual(self.management.decide(101,cancel['id'],'approve')['status'],'completed')
        self.management.decide(101,cancel['id'],'approve')
        self.assertEqual(len(self.deletes),1);self.assertEqual(len(self.provider.events),2)
        self.assertEqual(self.management.list_sessions(101)[1]['status'],'cancelled')
        self.assertEqual(self.management.list_sessions(202),[])

    def test_explicit_selected_move_can_override_soft_saved_window_without_profile_change(self):
        proposal,action=self.setup_events()
        before=profiles.get_profile(101)['preferences']
        events=json.loads(json.dumps(list(self.provider.events.values())))
        start=fixtures.NOW+timedelta(days=3,hours=7)  # 15:00 local, outside saved 18:00-20:00
        change=self.management.propose(101,action['id'],0,'reschedule',start.isoformat(),(start+timedelta(minutes=30)).isoformat())
        self.assertEqual(self.moves,[])
        self.assertRaises(LookupError,self.management.decide,202,change['id'],'approve')
        self.assertEqual(self.management.decide(101,change['id'],'approve')['status'],'completed')
        self.assertEqual(len(self.moves),1)
        self.assertEqual(profiles.get_profile(101)['preferences'],before)
        remaining=[event for event in self.provider.events.values() if event['id']!=self.moves[0][1]]
        self.assertEqual(remaining,[event for event in events if event['id']!=self.moves[0][1]])

    def test_lost_reschedule_and_cancel_response_reconcile_without_duplicate_writes(self):
        proposal,action=self.setup_events();change=self.move_proposal(action)
        self.lose_move=True
        self.assertEqual(self.management.decide(101,change['id'],'approve')['status'],'uncertain')
        restarted=CalendarSessionManagement(self.actions)
        self.assertEqual(restarted.execute(101,change['id'])['status'],'completed')
        self.assertEqual(len(self.moves),1)
        cancel=restarted.propose(101,action['id'],0,'cancel');self.lose_delete=True
        self.assertEqual(restarted.decide(101,cancel['id'],'approve')['status'],'uncertain')
        self.assertEqual(restarted.execute(101,cancel['id'])['status'],'completed')
        self.assertEqual(len(self.deletes),1)

    def test_cross_user_and_unrelated_event_are_never_modified(self):
        proposal,action=self.setup_events();change=self.move_proposal(action)
        self.assertRaises(LookupError,self.management.decide,202,change['id'],'approve')
        self.assertRaises(LookupError,self.management.propose,202,action['id'],0,'cancel')
        first=next(iter(self.provider.events.values()));first['summary']='Unrelated personal event'
        self.assertRaises(PlanningError,self.management.propose,101,action['id'],0,'cancel')
        self.assertEqual(self.management.decide(101,change['id'],'approve')['status'],'blocked')
        self.assertEqual(self.moves,[]);self.assertEqual(self.deletes,[])

    def test_rejection_expiry_and_stale_etag_fail_closed(self):
        proposal,action=self.setup_events();change=self.move_proposal(action)
        self.assertEqual(self.management.decide(101,change['id'],'reject')['status'],'rejected')
        change=self.move_proposal(action);next(iter(self.provider.events.values()))['etag']='external-update'
        self.assertEqual(self.management.decide(101,change['id'],'approve')['status'],'blocked')
        cancel=self.management.propose(101,action['id'],1,'cancel')
        self.management.clock=lambda:fixtures.NOW+timedelta(minutes=11)
        self.assertRaises(PlanningError,self.management.decide,101,cancel['id'],'approve')
        self.assertEqual(self.moves,[]);self.assertEqual(self.deletes,[])

    def test_unknown_outcome_locks_session_against_competing_change(self):
        proposal,action=self.setup_events();first=self.move_proposal(action)
        self.provider.reschedule_event=lambda *args:(_ for _ in ()).throw(TimeoutError('synthetic'))
        self.assertEqual(self.management.decide(101,first['id'],'approve')['status'],'uncertain')
        self.assertRaises(PlanningError,self.move_proposal,action)

    def test_second_account_management_uses_only_its_own_credentials(self):
        proposal,action=self.setup_events()
        goal=self.planner.save_goal(202,StudyGoal(subject='SQL'),uuid4())
        second=self.planner.propose(202,goal['id'],ProposalInput(request_id=uuid4(),start=fixtures.NOW,sessions=1))
        second=self.planner.decide(202,second['id'],'approve')
        action_b=self.actions.approve(202,second['id'],second['proposal_digest'],uuid4())
        self.actions.execute(202,action_b['id'])
        for (owner,key),event in self.provider.events.items():
            if owner=='synthetic-access-b':event['etag']='initial'
        change=self.management.propose(202,action_b['id'],0,'cancel')
        self.assertRaises(LookupError,self.management.decide,101,change['id'],'approve')
        self.assertEqual(self.management.decide(202,change['id'],'approve')['status'],'completed')
        self.assertEqual(self.deletes[0][0],'synthetic-access-b')
        self.assertEqual(len(self.provider.events),3)
        self.assertTrue(all(owner=='synthetic-access-a' for owner,key in self.provider.events))

    def test_revoked_permission_and_conflicts_prevent_session_changes(self):
        proposal,action=self.setup_events();change=self.move_proposal(action)
        self.provider.denied=True
        self.assertEqual(self.management.decide(101,change['id'],'approve')['status'],'blocked')
        self.assertEqual(self.moves,[])

    def test_overlapping_old_slot_is_not_blindly_subtracted_from_freebusy(self):
        proposal,action=self.setup_events();old=proposal['sessions'][0]
        self.assertRaises(PlanningError,self.management.propose,101,action['id'],0,'reschedule',old['start'],old['end'])
        self.assertEqual(self.moves,[])


if __name__=='__main__':unittest.main()
