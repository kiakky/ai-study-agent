
"""Telegram Mini App for all students; Calendar remains owner-only."""

import asyncio
import logging
import os

from contextlib import closing, suppress
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database import get_connection, initialize_database
from deployment import load_deployment
from services import scheduling
from services import profiles
from models.preferences import StudyPreferences
from services.mini_app import dashboard_keyboard, install_dashboard_menu

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("TELEGRAM_OWNER_ID", "0"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("httpcore2").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def safe_edit(query, message, reply_markup=None):
    """Never roll back Calendar actions due to Telegram confirmation errors."""
    try:
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
        )
    except (NetworkError, TimedOut) as exc:
        logger.warning(
            "Telegram confirmation could not be delivered: %s",
            type(exc).__name__,
        )
    except BadRequest as exc:
        logger.warning(
            "Could not update Telegram message: %s",
            exc,
        )


def retry_keyboard(approval_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "Retry / Check",
            callback_data=f"schedule:approve:{approval_id}",
        )
    ]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not user or not update.message:
        return

    if not update.effective_chat or update.effective_chat.type != "private":
        await update.message.reply_text("Open a private chat with this bot and tap Start to use your dashboard.")
        return

    inserted = profiles.ensure_profile(user.id)
    profile = profiles.get_profile(user.id)

    greeting = (
        "Welcome back"
        if not inserted
        else "Welcome to AI Study Agent"
    )

    url = context.bot_data.get("mini_app_url", "")
    message = f"{greeting}, {user.first_name}!\n\nYour study profile is ready. "
    if url:
        await install_dashboard_menu(context.bot, url, chat_id=update.effective_chat.id)
        message += "Tap Dashboard below or beside the message box for demo quizzes and your own saved grades."
    else:
        message += "The dashboard connection is being configured."
    if user.id == OWNER_ID:
        message += "\n\nUse /schedule for your existing developer Calendar. Per-user study proposals use your separately connected Google account."
    stage = profile['onboarding']['stage']
    message += '\n\n' + onboarding_message(stage)
    await update.message.reply_text(message, reply_markup=profile_keyboard(stage, url))


def onboarding_message(stage):
    return {
        'google': 'Setup: connect your own Google Calendar through the Setup button, or skip it. Demo quizzes never require a connection.',
        'brightspace': 'Setup: per-student Brightspace authorization is not available yet. Continue without it; the developer’s account is never shared.',
        'preferences': 'Setup: choose your study preferences in Setup, or explicitly accept the shown defaults: 30-minute MCQ practice, 10 questions, no preferred days/times yet.',
        'completed': 'Your setup is saved. Use /preferences to review or change it, /studyplan to check plans, or /practice for demo practice.',
    }[stage]


def profile_keyboard(stage, url):
    rows = []
    if url:
        rows.append([InlineKeyboardButton('Dashboard', web_app=WebAppInfo(url=url))])
        rows.append([InlineKeyboardButton('Setup / Preferences', web_app=WebAppInfo(url=url + '/setup'))])
        rows.append([InlineKeyboardButton('Study Plan', web_app=WebAppInfo(url=url + '/study'))])
    if stage == 'google':
        rows.append([InlineKeyboardButton('Skip Google Calendar', callback_data='onboard:skip:google')])
    elif stage == 'brightspace':
        rows.append([InlineKeyboardButton('Continue without Brightspace', callback_data='onboard:skip:brightspace')])
    elif stage == 'preferences':
        rows.append([InlineKeyboardButton('Use shown defaults', callback_data='onboard:defaults')])
    return InlineKeyboardMarkup(rows) if rows else None


async def onboarding_callback(update, context):
    query = update.callback_query
    user = update.effective_user
    if not query or not user or not update.effective_chat or update.effective_chat.type != 'private':
        return
    await query.answer()
    profile = profiles.get_profile(user.id)
    if query.data == 'onboard:defaults':
        if profile['onboarding']['stage'] == 'preferences':
            profile = profiles.save_preferences(user.id, StudyPreferences.model_validate(profile['preferences']))
    elif query.data in {'onboard:skip:google', 'onboard:skip:brightspace'}:
        integration = query.data.rsplit(':', 1)[1]
        if profile['onboarding']['stage'] == integration:
            profile = profiles.choose_integration(user.id, integration, 'skipped')
    stage = profile['onboarding']['stage']
    await safe_edit(query, onboarding_message(stage), profile_keyboard(stage, context.bot_data.get('mini_app_url', '')))


def preference_summary(profile):
    preferences = profile['preferences']
    days = ', '.join(preferences['study_days']) or 'Not chosen'
    windows = ', '.join(window['start'][:5] + '–' + window['end'][:5] for window in preferences['study_windows']) or 'Not chosen'
    label = 'Saved preferences' if profile['preferences_saved'] else 'Suggested defaults (not yet saved)'
    return (f"{label}\nDays: {days}\nLocal times ({profile['timezone']}): {windows}\n"
            f"Session: {preferences['session_minutes']} minutes\nFormat: {preferences['practice_format']}\n"
            f"Questions: {preferences['question_count']}\nOther preferences: {preferences['notes'] or 'None saved'}\n\n"
            'Available quizzes currently use demo MCQs; other formats are preferences for future practice.')


async def preferences_command(update, context):
    if not update.message or not update.effective_user or not update.effective_chat or update.effective_chat.type != 'private':
        return
    profile = profiles.get_profile(update.effective_user.id)
    keyboard = profile_keyboard(profile['onboarding']['stage'], context.bot_data.get('mini_app_url', ''))
    rows = list(keyboard.inline_keyboard) if keyboard else []
    rows.append([InlineKeyboardButton('Add / replace other preferences', callback_data='preferences:notes')])
    await update.message.reply_text(preference_summary(profile), reply_markup=InlineKeyboardMarkup(rows))


async def preference_notes_callback(update, context):
    if not update.callback_query or not update.effective_user or not update.effective_chat or update.effective_chat.type != 'private':
        return
    profiles.expect_preference_notes(update.effective_user.id)
    await update.callback_query.answer()
    await safe_edit(update.callback_query, 'Send your other study preferences in your next text message (up to 2,000 characters). These are saved as notes, not interpreted by an LLM.')


async def preference_text(update, context):
    if not update.message or not update.effective_user or not update.effective_chat or update.effective_chat.type != 'private':
        return
    from services.calendar_conversation import CalendarConversation
    try:
        if CalendarConversation().consume_adjustment(update.effective_user.id, update.message.text):
            from services.conversation_telegram import handle_text
            await handle_text(update,context)
            return
        saved = profiles.consume_preference_notes(update.effective_user.id, update.message.text)
    except ValueError:
        await update.message.reply_text('Please keep preference notes within 2,000 characters. Your previous preferences are unchanged.')
        return
    if saved:
        await update.message.reply_text('Your preference notes are saved. Use /preferences to review them.')
    else:
        from services.conversation_telegram import handle_text
        await handle_text(update,context)


# Telegram is the human approval boundary for the per-user Calendar tools.
from services.calendar_telegram import (
    calendar_test as calendar_test_command, study_plans as studyplan_command,
    review_callback, consent_loop, sessions_command, session_callback,
)


async def practice_command(update, context):
    if not update.message or not update.effective_chat or update.effective_chat.type != 'private':
        return
    url = context.bot_data.get('mini_app_url', '')
    if not url:
        await update.message.reply_text('The Mini App URL is not configured yet.')
        return
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton('Open demo practice', web_app=WebAppInfo(url=url + '/?view=quizzes'))]])
    await update.message.reply_text('Open spontaneous demo MCQ practice. Saved preferences will guide future generated practice; these sample questions are unchanged.', reply_markup=keyboard)


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.message:
        await update.message.reply_text(
            "Tap Dashboard beside the message box for quizzes and grades.\n"
            "/start - Open your study profile\n"
            "/preferences - Review and change study preferences\n"
            "/studyplan - View your current study-plan state\n"
            "/practice - Open demo practice\n"
            "/schedule - Propose a study session (connected owner only)\n"
            "/help - Show available commands"
        )


async def schedule(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not update.message:
        return

    if user.id != OWNER_ID:
        await update.message.reply_text(
            "Per-user Calendar event scheduling is not enabled yet. "
            "Your Google connection can provide availability; demo practice is always available."
        )
        return

    try:
        start_time, end_time = await asyncio.to_thread(
            scheduling.find_available_session
        )

        approval_id = scheduling.save_proposal(
            user.id,
            start_time,
            end_time,
        )

    except scheduling.SlotOccupied as exc:
        await update.message.reply_text(str(exc))
        return

    except Exception:
        logger.exception("Could not propose study session")

        await update.message.reply_text(
            "Couldn't check your Calendar right now. "
            "Please try /schedule later."
        )
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "Approve",
            callback_data=f"schedule:approve:{approval_id}",
        ),
        InlineKeyboardButton(
            "Reject",
            callback_data=f"schedule:reject:{approval_id}",
        ),
    ]])

    await update.message.reply_text(
        "Proposed Study Session (existing developer Calendar)\n\n"
        f"{start_time:%d %b %Y}\n"
        f"{start_time:%I:%M %p} – "
        f"{end_time:%I:%M %p} (Singapore)\n\n"
        "Expires in 10 minutes. "
        "A new /schedule replaces this proposal.\n"
        "Nothing has been added to Calendar yet. Approve?",
        reply_markup=keyboard,
    )


async def approval_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    user = update.effective_user

    if not query or not user or not query.data:
        return

    try:
        _, decision, raw_id = query.data.split(":")
        approval_id = int(raw_id)
    except ValueError:
        return

    request = scheduling.get_request(approval_id)

    if (
        not request
        or user.id != OWNER_ID
        or user.id != request["telegram_user_id"]
    ):
        try:
            await query.answer(
                "Not your approval request.",
                show_alert=True,
            )
        except (NetworkError, TimedOut):
            logger.warning(
                "Couldn't acknowledge unauthorized callback"
            )
        return

    # Telegram acknowledgement failures must not block decisions.
    try:
        await query.answer()
    except (NetworkError, TimedOut) as exc:
        logger.warning(
            "Callback acknowledgement failed: %s",
            type(exc).__name__,
        )

    scheduling.cleanup_expired()
    request = scheduling.get_request(approval_id)

    if request["approval_status"] == "expired":
        reason = request["resolution_reason"]

        message = (
            "This proposal was replaced by a newer one."
            if reason == "superseded"
            else "This proposal expired. "
                 "Send /schedule to generate a new one."
        )

        await safe_edit(query, message)
        return

    if request["approval_status"] == "rejected":
        await safe_edit(
            query,
            "This proposal was already rejected.",
        )
        return

    if request["calendar_status"] == "created":
        await safe_edit(
            query,
            "This session is already saved in Google Calendar.",
        )
        return

    if decision == "reject":
        if scheduling.reject(approval_id, user.id):
            await safe_edit(
                query,
                "Study session rejected. "
                "No Calendar changes made.",
            )
        else:
            await safe_edit(
                query,
                "Already approved or processing. "
                "Use Retry / Check after two minutes.",
                retry_keyboard(approval_id),
            )
        return

    if decision != "approve":
        return

    state, event = scheduling.claim(
        approval_id,
        user.id,
    )

    if state != "claimed":
        messages = {
            "processing": (
                "This approval is being processed. "
                "Retry / Check in two minutes."
            ),
            "created": (
                "This session is already in Google Calendar."
            ),
            "expired": (
                "This proposal expired. "
                "Send /schedule for a new one."
            ),
            "rejected": (
                "This proposal was rejected."
            ),
        }

        await safe_edit(
            query,
            messages.get(
                state,
                "This approval is no longer available.",
            ),
            (
                retry_keyboard(approval_id)
                if state == "processing"
                else None
            ),
        )
        return

    try:
        result = await asyncio.to_thread(
            scheduling.ensure_event,
            approval_id,
            event,
        )

    except scheduling.SlotOccupied as exc:
        scheduling.mark_failed(
            approval_id,
            str(exc),
        )

        await safe_edit(
            query,
            f"Study session not created: {exc}\n"
            "Send /schedule for a new time.",
        )
        return

    except Exception:
        logger.exception(
            "Calendar operation uncertain for approval %s",
            approval_id,
        )

        try:
            scheduling.mark_failed(
                approval_id,
                "Calendar request failed or outcome uncertain",
            )
        except Exception:
            logger.exception(
                "Couldn't save failed status"
            )

        await safe_edit(
            query,
            "Could not confirm the Calendar action. "
            "Please wait a minute, then use Retry / Check "
            "on this request. It reuses the same event ID.",
            retry_keyboard(approval_id),
        )
        return

    try:
        scheduling.mark_created(
            approval_id,
            user.id,
            result["id"],
        )

    except Exception:
        logger.exception(
            "Calendar created but SQLite confirmation "
            "failed for %s",
            approval_id,
        )

        await safe_edit(
            query,
            "Calendar may have been updated, "
            "but recording it failed. "
            "Wait two minutes, then use Retry / Check.",
            retry_keyboard(approval_id),
        )
        return

    await safe_edit(
        query,
        "Study session approved and added "
        "to Google Calendar!\n\n"
        f"Event: {result.get('summary', 'Study session')}",
    )


async def maintenance_loop():
    # Reconcile previously approved requests after restart.
    # Never create new events here.
    try:
        await asyncio.to_thread(
            scheduling.reconcile_incomplete
        )
    except Exception:
        logger.exception(
            "Startup reconciliation failed"
        )

    while True:
        try:
            scheduling.cleanup_expired()
        except Exception:
            logger.exception(
                "Couldn't expire old requests"
            )

        await asyncio.sleep(30)


async def dashboard_menu_loop(app):
    url = app.bot_data["mini_app_url"]
    while True:
        if await install_dashboard_menu(app.bot, url):
            logger.info("Dashboard menu configured and verified.")
            return
        await asyncio.sleep(30)


async def post_init(app):
    from services.practice import enabled,worker
    if enabled():
        app.bot_data['practice_worker'] = asyncio.create_task(worker())
    app.bot_data["calendar_consent_task"] = asyncio.create_task(consent_loop(app))
    app.bot_data["maintenance_task"] = (
        asyncio.create_task(maintenance_loop())
    )
    if app.bot_data.get("mini_app_url"):
        app.bot_data["dashboard_menu_task"] = asyncio.create_task(dashboard_menu_loop(app))


async def post_shutdown(app):
    conversation=app.bot_data.get('conversation_dispatcher')
    if conversation:
        conversation.service.provider.close()
    for name in ("maintenance_task", "dashboard_menu_task", "calendar_consent_task", "practice_worker"):
        task = app.bot_data.get(name)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    err = context.error

    if isinstance(err, (NetworkError, TimedOut)):
        logger.warning(
            "Temporary Telegram network problem: %s",
            type(err).__name__,
        )

    elif err:
        logger.error(
            "Unexpected Telegram error",
            exc_info=(
                type(err),
                err,
                err.__traceback__,
            ),
        )


def main():
    deployment = load_deployment()
    if not BOT_TOKEN:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN in .env"
        )

    if not OWNER_ID:
        raise RuntimeError(
            "Missing TELEGRAM_OWNER_ID in .env"
        )

    initialize_database(backfill_approvals=False)

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(40)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.bot_data["mini_app_url"] = deployment.mini_app_url
    if not deployment.mini_app_url:
        logger.warning("No Mini App URL configured. See config/deployment.example.json.")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    from services.conversation_telegram import clear_history
    app.add_handler(CommandHandler('clearconversation', clear_history))
    app.add_handler(CommandHandler('preferences', preferences_command))
    app.add_handler(CommandHandler('studyplan', studyplan_command))
    app.add_handler(CommandHandler('calendartest', calendar_test_command))
    app.add_handler(CommandHandler('calendarsessions', sessions_command))
    from services.conversation_telegram import preference_confirmation
    app.add_handler(CallbackQueryHandler(preference_confirmation, pattern=r'^ap:(approve|reject):[0-9]+:[0-9]+$'))
    app.add_handler(CallbackQueryHandler(review_callback, pattern=r'^cr:(approve|reject|changes|retry):[0-9a-f-]{36}$'))
    app.add_handler(CallbackQueryHandler(session_callback, pattern=r'^(cs:(list|(move|cancel):[0-9a-f-]{36}:\d+)|cm:(approve|reject|retry):[0-9a-f-]{36})$'))
    app.add_handler(CommandHandler('practice', practice_command))
    app.add_handler(CommandHandler("schedule", schedule))

    app.add_handler(
        CallbackQueryHandler(
            approval_callback,
            pattern=r"^schedule:(approve|reject):\d+$",
        )
    )

    from services.practice_telegram import handle_pdf
    app.add_handler(MessageHandler(filters.Document.ALL, handle_pdf))
    app.add_error_handler(error_handler)
    app.add_handler(CallbackQueryHandler(onboarding_callback, pattern=r'^onboard:(skip:(google|brightspace)|defaults)$'))
    app.add_handler(CallbackQueryHandler(preference_notes_callback, pattern=r'^preferences:notes$'))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, preference_text))

    logger.info("AI Study Agent bot is starting...")

    app.run_polling(
        drop_pending_updates=False,
        bootstrap_retries=5,
    )


if __name__ == "__main__":
    main()
