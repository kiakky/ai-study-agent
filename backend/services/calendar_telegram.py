"""Telegram presentation for per-user Calendar tools; no chat-text approval parsing."""
import asyncio
from datetime import datetime, timedelta
import json
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from telegram.error import TelegramError
from services.calendar_conversation import CalendarConversation, session_text
from services.calendar_management import CalendarSessionManagement
from services.calendar_testing import CalendarTestService, configured_testers
from services.google_oauth import OAuthUnavailable, ReconnectRequired
from services.planning import PlanningError
from services.scheduling_core import find_periods
from services import profiles


def private_user(update):
    if not update.effective_user or not update.effective_chat or update.effective_chat.type!='private':
        return None
    if update.effective_chat.id!=update.effective_user.id:return None
    return update.effective_user.id


def result_text(review):
    return {
        'completed':'Calendar creation completed. Every approved session is recorded in your own Google Calendar.',
        'consent':'Your exact events are approved for 10 minutes. Complete Google consent below. The unchanged, valid approval will resume automatically in the primary Calendar of the Google account you select.',
        'partial':'Some results are not yet confirmed. Retry / check uses the same event IDs and never duplicates confirmed events.',
        'processing':'Calendar creation is in progress. Check again shortly.',
        'rejected':'Proposal rejected. No Calendar write was authorized by this review.',
        'changes':'Adjustment requested; this proposal cannot execute. Send the changes you need. I will use your message to propose a revised plan for fresh approval. No timetable is changed by this request.',
        'cancelled':'Google consent was cancelled or failed. Previous availability access was retained. No event was authorized by consent alone.',
        'blocked':review.get('error') or 'This action needs a fresh review. Use /studyplan; no further events will be created automatically.',
    }.get(review['status'],'Your review is saved. Use /studyplan to check it.')


def result_buttons(review):
    if review.get('authorization_start_url'):
        return Markup([[Button('Authorize Google Calendar',url=review['authorization_start_url'])]])
    if review['status'] in {'partial','processing','approved'}:
        return Markup([[Button('Retry / check approved events',callback_data='cr:retry:'+review['id'])]])
    if review['status']=='completed':
        return Markup([[Button('View my study sessions',callback_data='cs:list')]])
    return None


async def show_review(message,user,proposal_id,service=None):
    service=service or CalendarConversation()
    review,data=await asyncio.to_thread(service.prepare,user,proposal_id)
    heading=(f"Review {len(data['sessions'])} exact study event(s).\n"
             'Calendar: primary Calendar of your connected Google account. If additional consent is needed, these events will use the Google account you select in that consent flow.\n'
             'Approve authorizes only the complete schedule shown below, for 10 minutes. Events will be created after permission and conflict checks.\n')
    chunks=[];chunk=heading
    for index,session in enumerate(data['sessions'],1):
        entry=f"\n{index}. "+session_text(session,data['timezone'])+'\n'
        if len(chunk)+len(entry)>3500:chunks.append(chunk);chunk=''
        chunk+=entry
    if chunk:chunks.append(chunk)
    for part in chunks:await message.reply_text(part)
    # No approval button is delivered until all exact sessions were delivered.
    await asyncio.to_thread(service.presented,user,review['id'])
    await message.reply_text('Review all sessions above, then choose:',reply_markup=Markup([
        [Button('Approve and create events',callback_data='cr:approve:'+review['id'])],
        [Button('Reject',callback_data='cr:reject:'+review['id']),Button('Request changes',callback_data='cr:changes:'+review['id'])]]))


async def calendar_test(update,context):
    user=private_user(update)
    if user is None:return
    args=getattr(context,'args',[])
    try:
        if any(arg not in {'batch','confirm-notes'} for arg in args):raise PlanningError('Use /calendartest, optionally batch (3 sessions) and confirm-notes after mapping restrictions to saved preferences.')
        proposal=await asyncio.to_thread(CalendarTestService().prepare,user,'confirm-notes' in args,3 if 'batch' in args else 1)
        actions=CalendarConversation().actions.list(user)
        existing=next((a for a in actions if a['proposal_id']==proposal['id'] and a['status']=='completed'),None)
        if existing:
            await update.message.reply_text('This test is already completed. Use /calendarsessions to manage the individual events. No duplicate test will be created.')
            return
        await show_review(update.message,user,proposal['id'])
    except (ValueError,PlanningError,LookupError) as error:await update.message.reply_text(str(error))
    except (OAuthUnavailable,ReconnectRequired):await update.message.reply_text('Your Calendar permission needs attention. Open Settings. Your saved proposal is retained.')
    except Exception:await update.message.reply_text('Calendar review could not be prepared. No event was approved. Please retry.')


async def study_plans(update,context):
    user=private_user(update)
    if user is None:return
    service=CalendarConversation()
    proposals=service.planner.proposals(user)
    active=[p for p in proposals if p['status'] in {'draft','approved'}]
    if not active:
        await update.message.reply_text('No study plan has been proposed yet. Your AI Study Agent will prepare one when it has the necessary information.')
        return
    for proposal in active[:10]:
        completed=next((a for a in service.actions.list(user) if a['proposal_id']==proposal['id'] and a['status']=='completed'),None)
        if completed:
            await update.message.reply_text(f"{proposal['subject']}: {len(completed['sessions'])} Calendar events confirmed. View them in Study Plan or /calendarsessions.")
        else:
            try:await show_review(update.message,user,proposal['id'],service)
            except (PlanningError,LookupError) as error:await update.message.reply_text(str(error))


async def review_callback(update,context):
    user=private_user(update)
    if user is None or not update.callback_query:return
    query=update.callback_query
    await query.answer()
    _,decision,identifier=query.data.split(':')
    service=CalendarConversation()
    try:
        result=await asyncio.to_thread(service.execute if decision=='retry' else service.decide,user,identifier,*([] if decision=='retry' else [decision]))
    except LookupError:
        await query.message.reply_text('This Calendar review is not available to your account.');return
    except (PlanningError,OAuthUnavailable,ReconnectRequired) as error:
        # Service errors are sanitized; no provider exception text reaches chat.
        await query.message.reply_text(str(error));return
    except Exception:
        await query.message.reply_text('The result could not be confirmed. Use /studyplan to check the saved action before retrying.');return
    try:
        await query.edit_message_text(result_text(result),reply_markup=result_buttons(result))
        service.notified(user,identifier)
    except TelegramError:
        # Calendar outcomes are already persisted; delivery cannot roll them back.
        pass


async def consent_loop(app):
    while True:
        try:
            service=CalendarConversation()
            for item in service.pending_consent():
                user,identifier=item['telegram_user_id'],item['id']
                try:
                    result=await asyncio.to_thread(service.resume_consent,user,identifier)
                except (PlanningError,OAuthUnavailable,ReconnectRequired) as error:
                    service.fail(user,identifier,str(error));result=service.get(user,identifier)
                except Exception:
                    service.fail(user,identifier,'Calendar continuation could not be confirmed. Check /studyplan before retrying.');result=service.get(user,identifier)
                if result['status']=='consent':continue
                try:
                    await app.bot.send_message(chat_id=user,text=result_text(result),reply_markup=result_buttons(result))
                    service.notified(user,identifier)
                except TelegramError:pass
        except Exception:
            # No private exception payloads in logs; next pass retries persisted work.
            pass
        await asyncio.sleep(10)


async def session_list(message,user):
    if user not in configured_testers():raise PlanningError('Temporary session controls are restricted to configured testers.')
    sessions=CalendarSessionManagement().list_sessions(user)
    if not sessions:await message.reply_text('No confirmed study sessions yet.');return
    for session in sessions[:100]:
        markup=None
        if session['status']=='scheduled':
            key=session['action_id']+':'+str(session['session_index'])
            markup=Markup([[Button('Propose new time',callback_data='cs:move:'+key),Button('Propose cancellation',callback_data='cs:cancel:'+key)]])
        await message.reply_text(session_text(session,session['timezone'])+'\nStatus: '+session['status'],reply_markup=markup)


async def sessions_command(update,context):
    user=private_user(update)
    if user is None:return
    try:await session_list(update.message,user)
    except PlanningError as error:await update.message.reply_text(str(error))


async def session_callback(update,context):
    user=private_user(update)
    if user is None or not update.callback_query:return
    query=update.callback_query;await query.answer()
    service=CalendarSessionManagement()
    try:
        if user not in configured_testers():raise PlanningError('Temporary controls are not enabled for this account.')
        if query.data=='cs:list':await session_list(query.message,user);return
        prefix,operation,*parts=query.data.split(':')
        if prefix=='cm':
            identifier=parts[0]
            result=await asyncio.to_thread(service.execute if operation=='retry' else service.decide,user,identifier,*([] if operation=='retry' else [operation]))
            text=('Study session '+result['kind']+': '+result['status']+'. Other sessions are unchanged.')
            if result.get('error'):text+='\n'+result['error']
            markup=Markup([[Button('Retry / check',callback_data='cm:retry:'+identifier)]]) if result['status'] in {'uncertain','processing'} else None
            await query.message.reply_text(text,reply_markup=markup);return
        action_id,index=parts[0],int(parts[1])
        if operation=='cancel':
            result=await asyncio.to_thread(service.propose,user,action_id,index,'cancel')
        elif operation=='move':
            # Temporary tester helper: propose the next valid slot; never approve it.
            current=service.session(user,action_id,index)
            body=json.loads(current['payload_json'])
            proposal=service.actions.proposal(user,service.actions.record(user,action_id)['proposal_id'])
            data=json.loads(proposal['proposal_json']);prefs=profiles.get_profile(user)['preferences']
            start=max(service.clock()+timedelta(hours=1),datetime.fromisoformat(body['end']['dateTime']))
            end=datetime.fromisoformat(data['constraints']['start'].replace('Z','+00:00'))+timedelta(days=data['constraints']['days'])
            periods,unused,state=await asyncio.to_thread(service.planner.busy,user,start,end,False)
            from models.planning import BusyPeriod
            periods += [BusyPeriod.model_validate(p) for p in data['constraints']['excluded_periods']]
            periods += [BusyPeriod.model_validate(p) for p in service.list_sessions(user) if p['status']=='scheduled']
            slots=find_periods(start,end,ZoneInfo(data['timezone']),prefs,int((datetime.fromisoformat(body['end']['dateTime'])-datetime.fromisoformat(body['start']['dateTime'])).total_seconds()/60),1,data['constraints']['excluded_days'],periods)
            if not slots:raise PlanningError('No other suitable slot within this proposal range. No event changed.')
            result=await asyncio.to_thread(service.propose,user,action_id,index,'reschedule',slots[0]['start'],slots[0]['end'])
        else:raise PlanningError('Unknown session operation.')
        await show_change(query.message,user,result['id'],service)
    except (PlanningError,LookupError,ValueError) as error:await query.message.reply_text(str(error))
    except (OAuthUnavailable,ReconnectRequired):await query.message.reply_text('Your Calendar permission needs attention. No unrelated event was changed.')
    except Exception:await query.message.reply_text('Session operation could not be confirmed. Check the saved result before retrying.')


async def show_change(message,user,identifier,service=None):
    service=service or CalendarSessionManagement()
    result=await asyncio.to_thread(service.get,user,identifier)
    old=json.loads(result['old_payload_json'])
    text='Calendar: your connected primary Google Calendar.\nCurrent: '+session_text({'title':old['summary'],'start':old['start']['dateTime'],'end':old['end']['dateTime']},result['timezone'])
    if result['kind']=='reschedule':
        new=json.loads(result['new_payload_json'])
        text+='\n\nProposed: '+session_text({'title':new['summary'],'start':new['start']['dateTime'],'end':new['end']['dateTime']},result['timezone'])
    else:text+='\n\nRemove only this study event from Google Calendar?'
    text+='\nOnly this session will change. Approval expires in 10 minutes.'
    await message.reply_text(text,reply_markup=Markup([[Button('Approve '+result['kind'],callback_data='cm:approve:'+result['id']),Button('Reject',callback_data='cm:reject:'+result['id'])]]))
