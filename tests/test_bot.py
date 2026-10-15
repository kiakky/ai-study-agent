"""Synthetic Telegram updates and temporary SQLite; all remote operations mocked."""
import asyncio
from contextlib import closing
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
with patch('dotenv.load_dotenv'), patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'synthetic', 'TELEGRAM_OWNER_ID': '101'}):
    import bot
import database
from services.mini_app import install_dashboard_menu
from telegram import MenuButtonCommands, MenuButtonWebApp, WebAppInfo
from telegram.error import NetworkError

URL = 'https://study.example'


class BotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='quiz-test-', dir=ROOT / 'tests')
        self.addCleanup(self.temp.cleanup)
        self.db_patch = patch.object(database, 'DB_PATH', Path(self.temp.name) / 'test.db')
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.owner_patch = patch.object(bot, 'OWNER_ID', 101)
        self.owner_patch.start()
        self.addCleanup(self.owner_patch.stop)
        database.initialize_database()
        self.remote_bot = SimpleNamespace(
            set_chat_menu_button=AsyncMock(return_value=True),
            get_chat_menu_button=AsyncMock(return_value=MenuButtonWebApp(text='Dashboard', web_app=WebAppInfo(url=URL))),
        )
        self.context = SimpleNamespace(bot=self.remote_bot, bot_data={'mini_app_url': URL})

    def update(self, user_id=101, chat_type='private', callback=None):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id, first_name='Synthetic Student'),
            effective_chat=SimpleNamespace(id=user_id, type=chat_type),
            message=SimpleNamespace(reply_text=AsyncMock()),
            callback_query=SimpleNamespace(data=callback, answer=AsyncMock(), edit_message_text=AsyncMock()) if callback else None,
        )

    async def propose(self):
        update = self.update()
        await bot.start(update, self.context)
        with patch.object(bot.scheduling, 'list_events', return_value=[]):
            await bot.schedule(update, self.context)
        markup = update.message.reply_text.call_args.kwargs['reply_markup']
        return int(markup.inline_keyboard[0][0].callback_data.split(':')[-1])

    async def test_new_students_register_dynamically_and_receive_webapp_buttons(self):
        for user_id in (101, 202, 999999):
            update = self.update(user_id)
            await bot.start(update, self.context)
            keyboard = update.message.reply_text.call_args.kwargs['reply_markup']
            self.assertEqual(keyboard.inline_keyboard[0][0].web_app.url, URL)
            self.assertIsNone(keyboard.inline_keyboard[0][0].url)
            self.assertEqual(self.remote_bot.set_chat_menu_button.call_args.kwargs['chat_id'], user_id)
        with closing(database.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0], 3)
        update = self.update(202)
        await bot.start(update, self.context)
        self.assertIn('Welcome back', update.message.reply_text.call_args.args[0])

    async def test_group_onboarding_does_not_send_unsupported_webapp_button(self):
        update = self.update(chat_type='group')
        await bot.start(update, self.context)
        self.assertIn('private chat', update.message.reply_text.call_args.args[0])
        self.remote_bot.set_chat_menu_button.assert_not_awaited()

    async def test_default_menu_is_verified_and_failed_delivery_does_not_break_start(self):
        self.assertTrue(await install_dashboard_menu(self.remote_bot, URL))
        self.assertIsNone(self.remote_bot.get_chat_menu_button.call_args.kwargs['chat_id'])
        self.remote_bot.get_chat_menu_button.return_value = MenuButtonCommands()
        self.assertFalse(await install_dashboard_menu(self.remote_bot, URL))
        self.remote_bot.set_chat_menu_button.side_effect = NetworkError('synthetic failure')
        update = self.update(202)
        await bot.start(update, self.context)
        update.message.reply_text.assert_awaited_once()
        self.assertIsNotNone(update.message.reply_text.call_args.kwargs['reply_markup'])

    async def test_background_menu_retries_and_shutdown_cancels_tasks(self):
        app = SimpleNamespace(bot=self.remote_bot, bot_data={'mini_app_url': URL})
        with patch.object(bot, 'install_dashboard_menu', new=AsyncMock(side_effect=[False, True])) as install, patch.object(bot.asyncio, 'sleep', new=AsyncMock()):
            await bot.dashboard_menu_loop(app)
            self.assertEqual(install.await_count, 2)
        async def wait_forever(*args):
            await asyncio.Event().wait()
        with patch.object(bot, 'maintenance_loop', new=wait_forever), patch.object(bot, 'dashboard_menu_loop', new=wait_forever):
            await bot.post_init(app)
            self.assertIn('maintenance_task', app.bot_data)
            self.assertIn('dashboard_menu_task', app.bot_data)
            await bot.post_shutdown(app)
            self.assertTrue(app.bot_data['maintenance_task'].cancelled())
            self.assertTrue(app.bot_data['dashboard_menu_task'].cancelled())

    async def test_other_students_cannot_schedule_or_approve_owner_calendar(self):
        approval_id = await self.propose()
        with patch.object(bot.scheduling, 'find_available_session') as find, patch.object(bot.scheduling, 'ensure_event') as ensure:
            update = self.update(202)
            await bot.schedule(update, self.context)
            find.assert_not_called()
            callback = self.update(202, callback=f'schedule:approve:{approval_id}')
            await bot.approval_callback(callback, self.context)
            ensure.assert_not_called()
            self.assertEqual(bot.scheduling.get_request(approval_id)['approval_status'], 'pending')

    async def test_owner_approval_is_idempotent_despite_telegram_delivery_failure(self):
        approval_id = await self.propose()
        update = self.update(callback=f'schedule:approve:{approval_id}')
        update.callback_query.edit_message_text.side_effect = NetworkError('synthetic delivery failure')
        with patch.object(bot.scheduling, 'get_google_event', return_value=None), patch.object(bot.scheduling, 'list_events', return_value=[]), patch.object(bot.scheduling, 'create_event', side_effect=lambda event: event) as create:
            await bot.approval_callback(update, self.context)
            await bot.approval_callback(update, self.context)
            create.assert_called_once()
        request = bot.scheduling.get_request(approval_id)
        self.assertEqual(request['approval_status'], 'approved')
        self.assertEqual(request['calendar_status'], 'created')

    async def test_owner_rejection_never_creates_event(self):
        approval_id = await self.propose()
        with patch.object(bot.scheduling, 'ensure_event') as ensure:
            await bot.approval_callback(self.update(callback=f'schedule:reject:{approval_id}'), self.context)
            ensure.assert_not_called()
        self.assertEqual(bot.scheduling.get_request(approval_id)['approval_status'], 'rejected')

    async def test_onboarding_resumes_and_completed_users_do_not_repeat_setup(self):
        update = self.update(202)
        await bot.start(update, self.context)
        self.assertIn('Setup: connect', update.message.reply_text.call_args.args[0])
        await bot.onboarding_callback(self.update(202,callback='onboard:skip:google'),self.context)
        await bot.start(update,self.context)
        self.assertIn('per-student Brightspace',update.message.reply_text.call_args.args[0])
        await bot.onboarding_callback(self.update(202,callback='onboard:skip:brightspace'),self.context)
        await bot.onboarding_callback(self.update(202,callback='onboard:defaults'),self.context)
        await bot.start(update,self.context)
        self.assertIn('Your setup is saved',update.message.reply_text.call_args.args[0])

    async def test_preferences_notes_persist_and_commands_do_not_schedule(self):
        update = self.update(202)
        await bot.preferences_command(update,self.context)
        self.assertIn('Suggested defaults',update.message.reply_text.call_args.args[0])
        await bot.preference_notes_callback(self.update(202,callback='preferences:notes'),self.context)
        update.message.text = 'Synthetic note: prefer shorter weekday practice.'
        await bot.preference_text(update,self.context)
        await bot.preferences_command(update,self.context)
        self.assertIn('prefer shorter weekday',update.message.reply_text.call_args.args[0])
        with patch.object(bot.scheduling,'find_available_session') as find:
            await bot.studyplan_command(update,self.context)
            self.assertIn('No study plan',update.message.reply_text.call_args.args[0])
            await bot.practice_command(update,self.context)
            markup=update.message.reply_text.call_args.kwargs['reply_markup']
            self.assertIn('view=quizzes',markup.inline_keyboard[0][0].web_app.url)
            find.assert_not_called()


if __name__ == '__main__':
    unittest.main()
