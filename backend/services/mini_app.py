"""Telegram menu delivery; deliberately independent of Calendar authorization."""
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, WebAppInfo
from telegram.error import TelegramError

log = logging.getLogger(__name__)


def dashboard_keyboard(url):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Dashboard", web_app=WebAppInfo(url=url)),
    ]])


async def install_dashboard_menu(bot, url, chat_id=None):
    """Set and read back the menu; never claim success from a failed write."""
    try:
        expected = MenuButtonWebApp(text="Dashboard", web_app=WebAppInfo(url=url))
        if not await bot.set_chat_menu_button(chat_id=chat_id, menu_button=expected):
            return False
        actual = await bot.get_chat_menu_button(chat_id=chat_id)
        verified = (
            isinstance(actual, MenuButtonWebApp)
            and actual.text == "Dashboard"
            and actual.web_app.url.rstrip("/") == url.rstrip("/")
        )
        if not verified:
            log.warning("Dashboard menu read-back did not match; will retry.")
        return verified
    except TelegramError as error:
        # Exception strings can include request details; log the type only.
        log.warning("Dashboard menu could not be confirmed (%s).", type(error).__name__)
        return False
