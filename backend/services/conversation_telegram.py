"""Private authenticated chat and actor-bound tools for the configured audience."""
import asyncio,logging
from services.conversations import ConversationService,ConversationDispatcher,load_settings
from services.llm_providers import create_provider
from services.llm_contracts import LLMError
from services.calendar_testing import configured_testers
from telegram.error import TelegramError, NetworkError, RetryAfter

log=logging.getLogger(__name__)


def conversation_access(user,settings=None):
    """Read an existing /start profile; never provision users or authorize LMS access."""
    if type(user) is not int or user<=0:return False
    try:
        settings=settings or load_settings()
        if settings.audience=='calendar_testers':return user in configured_testers()
        if settings.audience!='onboarded_users':return False
        from contextlib import closing
        from database import get_connection
        with closing(get_connection()) as db:
            return db.execute('SELECT 1 FROM users u JOIN user_onboarding o ON o.telegram_user_id=u.telegram_user_id WHERE u.telegram_user_id=?',(user,)).fetchone() is not None
    except (LLMError,ValueError):return False


def private_user(update):
    if (not update.message or not update.effective_user or not update.effective_chat or
        update.effective_chat.type!='private' or update.effective_chat.id!=update.effective_user.id):return None
    return update.effective_user.id


def dispatcher(context,settings):
    existing=context.bot_data.get('conversation_dispatcher')
    if existing:return existing
    provider=create_provider(settings)
    from services.calendar_agent import CalendarAgent
    if getattr(provider, 'native_calendar_tools', False):
        from services.calendar_native import NativeCalendarAgent
        agent_factory=NativeCalendarAgent
    else:
        agent_factory=CalendarAgent
    service=ConversationDispatcher(ConversationService(provider,agent_factory=agent_factory))
    context.bot_data['conversation_dispatcher']=service
    return service


async def send_text(message,text,**kwargs):
    """Retry Telegram delivery only. Never rerun inference or Calendar operations."""
    for attempt in range(3):
        try:
            return await message.reply_text(text,connect_timeout=5,read_timeout=10,
                write_timeout=10,pool_timeout=5,**kwargs)
        except (NetworkError,RetryAfter) as error:
            delay=getattr(error,'retry_after',1)
            if hasattr(delay,'total_seconds'):delay=delay.total_seconds()
            if attempt==2 or delay>3:raise
            await asyncio.sleep(delay)


async def reply(update,text):
    # Plain text; model output cannot create Telegram markup/buttons or actions.
    return await send_text(update.message,text,parse_mode=None)


class DeliveryMessage:
    def __init__(self,message):self.message=message
    def __getattr__(self,name):return getattr(self.message,name)
    async def reply_text(self,text,**kwargs):return await send_text(self.message,text,**kwargs)


async def handle_text(update,context):
    user=private_user(update)
    if user is None:return
    try:
        settings=load_settings()
        if not settings.enabled:
            await reply(update,'Study conversation is currently unavailable. Your quizzes, settings and Calendar commands remain available.');return
        if not conversation_access(user,settings):
            await reply(update,'Send /start in this private chat to set up your study profile. Study chat must also be enabled for this installation.');return
        from services.calendar_text_decisions import explicit_decision
        if explicit_decision(update.message.text):
            await plan_text_decision(update,context,user)
            return
        service=dispatcher(context,settings)
        generation=service.service.generation(user)
        message_id=update.message.message_id
    except (LLMError,ValueError):
        await reply(update,'Study conversation is not configured yet. Existing quizzes and Calendar commands remain available.');return

    async def work():
        try:
            await service.deliver(user,message_id,update.message.text,generation,lambda text:reply(update,text),lambda u,m:present_tools(DeliveryMessage(update.message),u,m),getattr(update.effective_user,'first_name',None))
        except LLMError as error:
            log.warning('Conversation request failed (%s).',error.code)
            messages={
                'practice_delivery':'I could not confirm a saved Mini App quiz for that request. Please try again or provide your own notes/PDF. No quiz answers were sent. Your other saved quizzes are unchanged.',
                'invalid_tool_call':'The model returned an invalid tool request. No action was approved; please try again.',
                'invalid_intent_response':'I could not interpret that message reliably. No Calendar action occurred; please try again.',
                'request_timeout':'This request took too long and was stopped. No new Calendar action was approved. Please send a new message; your saved context and Google connection are retained.',
                'timeout':'This request reached its time limit and was stopped. Please try a narrower request. No new Calendar action was approved.',
                'tool_loop':'I got stuck repeating a tool request and could not finish this turn. Please send the request again. On-demand quizzes do not require a study schedule. No Calendar changes were approved by this message.',
                'tool_limit':'The model did not finish within the safe tool-call limit. No new Calendar action was approved. Please retry.',
                'tool_retry_uncertain':'A previous tool result is uncertain. Check /studyplan or /calendarsessions before requesting another proposal; no Calendar write was approved by this message.',
                'input_too_long':'Please keep each message within 3,000 characters.',
                'busy':'Your previous message is still being processed. Please try again shortly.',
                'rate_limited':'Study chat is temporarily rate-limited. Please try again shortly.',
                'quota_exceeded':'Study chat has reached its provider token quota. It can resume when the quota is available again. Your existing Calendar buttons, quizzes and saved plans remain available.',
                'authentication':'Study chat needs a configuration check. Your other bot features remain available.',
                'unsafe_agent_configuration':'Study chat is paused because its provider agent configuration changed. Existing commands remain available.',
                'inference_uncertain':'The provider response could not be confirmed. This message will not be submitted again automatically. Send a new message to continue; your completed history is retained.',
                'unsupported_model':'The configured study-chat model is unavailable. Your other bot features remain available.',
            }
            try:await reply(update,messages.get(error.code,'Study chat is temporarily unavailable. Please resend your message shortly; your completed conversation is retained.'))
            except TelegramError:log.warning('Conversation error delivery failed.')
        except TelegramError:
            log.warning('Conversation reply delivery could not be confirmed. Reply is saved for retry.')
        except Exception:
            log.warning('Conversation operation failed; private exception details withheld.')
            try:await reply(update,'Study chat could not respond. Please try again shortly.')
            except TelegramError:pass
    application=getattr(context,'application',None)
    if application:
        # PTB keeps polling/Calendar handlers responsive while IBM is slow. Tasks
        # are managed by Application and awaited on a normal shutdown.
        application.create_task(work(),update=update)
    else:await work()


async def clear_history(update,context):
    user=private_user(update)
    if user is None:return
    if not conversation_access(user):
        await reply(update,'Send /start to set up your study profile.');return
    # Clearing never consumes IBM credentials or makes an inference request.
    ConversationService(None).clear(user,update.message.message_id)
    await reply(update,'Your conversational history is cleared. Saved preferences, practice attempts and topic progress, grades, and Calendar plans are unchanged.')


async def present_tools(message,user,message_id):
    """Only backend-validated stored references can create approval keyboards."""
    from contextlib import closing
    import json
    from database import get_connection
    from services.calendar_telegram import show_review,show_change
    from telegram import InlineKeyboardButton as Button,InlineKeyboardMarkup as Markup
    with closing(get_connection()) as db:
        rows=[dict(r) for r in db.execute("SELECT * FROM conversation_tool_effects WHERE telegram_user_id=? AND message_id=? AND status='completed' AND presented=0 ORDER BY ordinal",(user,message_id))]
    for row in rows:
        result=json.loads(row['result_json'])
        kind=result.get('review_kind')
        if result.get('practice_job_id'):
            from deployment import load_deployment
            from telegram import WebAppInfo
            origin=load_deployment().mini_app_url
            if origin:
                await message.reply_text('Your practice request is saved. Open Generated Practice to check preparation and continue any unfinished quizzes.',
                    reply_markup=Markup([[Button('Open Generated Practice',web_app=WebAppInfo(url=origin+'/practice'))]]))
        elif kind=='plan':await show_review(message,user,result['proposal_id'])
        elif kind=='change':await show_change(message,user,result['change_id'])
        elif result.get('status') in {'disconnected','revoked','reconnect_required','unavailable'}:
            from deployment import load_deployment
            from telegram import WebAppInfo
            url=load_deployment().mini_app_url
            if url:
                await message.reply_text('Connect your own Google Calendar in Settings / Connections, then say "Continue with that study plan".',
                    reply_markup=Markup([[Button('Calendar connection / Settings',web_app=WebAppInfo(url=url+'/setup'))]]))
        elif kind=='preferences':
            details={k:v for k,v in result['new'].items() if v!=result['old'][k]}
            warning=('\nThis also removes the existing free-text notes: '+result['old']['notes']) if 'notes' in details else ''
            await message.reply_text('Save these exact study settings? Other preferences remain unchanged.\n'+json.dumps(details,ensure_ascii=False)+warning,
                reply_markup=Markup([[Button('Save preferences',callback_data=f"ap:approve:{message_id}:{row['ordinal']}"),
                                     Button('Keep existing',callback_data=f"ap:reject:{message_id}:{row['ordinal']}")]]))
        with closing(get_connection()) as db,db:
            db.execute('UPDATE conversation_tool_effects SET presented=1 WHERE telegram_user_id=? AND message_id=? AND ordinal=?',(user,message_id,row['ordinal']))


async def preference_confirmation(update,context):
    from contextlib import closing
    import json
    from datetime import datetime,timezone
    from database import get_connection
    from models.preferences import StudyPreferences
    query=update.callback_query
    if not query or not update.effective_user or not update.effective_chat:return
    user=update.effective_user.id
    if update.effective_chat.type!='private' or update.effective_chat.id!=user or not conversation_access(user):return
    await query.answer()
    _,decision,message_id,ordinal=query.data.split(':')
    with closing(get_connection()) as db,db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM conversation_tool_effects WHERE telegram_user_id=? AND message_id=? AND ordinal=?',(user,int(message_id),int(ordinal))).fetchone()
        if not row or not row['presented'] or row['decided'] or row['status']!='completed':
            text='This preference review is unavailable or already decided.'
        else:
            result=json.loads(row['result_json'])
            if result.get('review_kind')!='preferences':return
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(row['created_at']).replace(tzinfo=timezone.utc)).total_seconds()
            current=db.execute('SELECT preferences_json FROM users WHERE telegram_user_id=?',(user,)).fetchone()[0]
            original=json.loads(current)
            known={k:v for k,v in original.items() if k in StudyPreferences.model_fields}
            if age>600 or StudyPreferences.model_validate(known).model_dump(mode='json')!=result['old']:
                text='Preferences changed or this review expired. Please request a fresh change.'
            elif decision=='approve':
                original.update(StudyPreferences.model_validate(result['new']).model_dump(mode='json'))
                db.execute('UPDATE users SET preferences_json=? WHERE telegram_user_id=?',(json.dumps(original),user))
                text='Your exact study preferences are saved. Existing Calendar events are unchanged.'
            else:text='Existing preferences retained.'
            db.execute('UPDATE conversation_tool_effects SET decided=1 WHERE telegram_user_id=? AND message_id=? AND ordinal=?',(user,int(message_id),int(ordinal)))
    await query.message.reply_text(text)


async def plan_text_decision(update,context,user):
    from services.calendar_text_decisions import apply_decision
    from services.calendar_telegram import result_text,result_buttons
    from services.planning import PlanningError
    if getattr(update.message,'forward_origin',None) or getattr(update.message,'external_reply',None):
        await reply(update,'Forwarded or external text cannot approve your Calendar actions. Please review your own latest proposal and use its approval controls.');return
    reference=None
    parent=getattr(update.message,'reply_to_message',None)
    if parent:
        markup=getattr(parent,'reply_markup',None)
        buttons=[b for row in getattr(markup,'inline_keyboard',[]) for b in row]
        references=[b.callback_data.split(':')[-1] for b in buttons if getattr(b,'callback_data',None) and b.callback_data.startswith(('cr:approve:','cm:approve:'))]
        if len(references)!=1 or getattr(getattr(parent,'from_user',None),'id',None)!=context.bot.id:
            await reply(update,'Please approve the exact latest plan using its approval message. This reply does not identify a complete review.');return
        reference=references[0]
    try:
        result=await asyncio.to_thread(apply_decision,user,update.message.message_id,update.message.text,reply_review=reference)
        if result.get('review_kind')=='change':
            text={'completed':'The approved session change is confirmed in your own Calendar.',
                  'rejected':'Session change rejected; your Calendar is unchanged.',
                  'uncertain':'The Calendar result is uncertain. Check /calendarsessions and use the exact review to retry.',
                  'processing':'That exact session change is still processing.',
                  'blocked':result.get('error','This review is unavailable.')}.get(result['status'],result.get('error') or 'Check /calendarsessions for the saved change status.')
            await reply(update,text)
            return
        await update.message.reply_text(result_text(result),reply_markup=result_buttons(result))
    except (PlanningError,LookupError) as error:await reply(update,str(error))
    except Exception:
        log.warning('Conversational Calendar decision could not be confirmed; private details withheld.')
        await reply(update,'The saved Calendar result could not be confirmed. Check /studyplan before retrying; repeated approval cannot create duplicate events.')
