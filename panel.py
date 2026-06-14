"""
Живая панель: ОДНО сообщение, которое редактируется по ходу работы.

Идея: вместо того чтобы плодить новые сообщения после каждого нажатия,
у пользователя в чате одна «панель управления» — её правим через
editMessageText. Чат остаётся чистым, всё видно в одном месте.

Хранилище message_id — in-memory dict user_id → message_id.
Это устраивает: при рестарте бота старая панель не редактируется,
helper увидит ошибку edit и отправит новую. Юзер увидит свежую,
а старая просто «застынет» в истории.
"""
import logging
from typing import Union

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)

log = logging.getLogger("panel")

# user_id -> (chat_id, message_id) активной панели
_panels: dict[int, tuple[int, int]] = {}


Target = Union[Message, CallbackQuery]


def _ctx(target: Target) -> tuple[int, int]:
    """Достаём user_id и chat_id из Message или CallbackQuery."""
    if isinstance(target, CallbackQuery):
        return target.from_user.id, target.message.chat.id
    return target.from_user.id, target.chat.id


async def show(
    bot: Bot,
    target: Target,
    text: str,
    keyboard: InlineKeyboardMarkup,
) -> None:
    """
    Показать/обновить панель пользователя. Если активной нет — создаём.
    parse_mode='HTML' зашит — внутри ждём <tg-emoji>, <b> и т.п.
    """
    user_id, chat_id = _ctx(target)
    saved = _panels.get(user_id)

    if saved and saved[0] == chat_id:
        try:
            await bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=saved[1],
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        except TelegramBadRequest as e:
            # «message is not modified» — содержимое то же, всё ок.
            if "not modified" in str(e).lower():
                return
            # «message to edit not found» / удалена / истекла — отправим новую.
            log.info("panel edit fell back to send: %s", e)

    msg = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    _panels[user_id] = (chat_id, msg.message_id)


async def reset(bot: Bot, user_id: int) -> None:
    """Забыть старую панель — следующий show() создаст новую."""
    _panels.pop(user_id, None)


async def delete_user_message(message: Message) -> None:
    """
    Попытаться удалить сообщение пользователя (после того как мы его обработали),
    чтобы чат не засорялся. В приватных чатах Telegram это не разрешает —
    тогда просто молча игнорим ошибку.
    """
    try:
        await message.delete()
    except Exception:
        pass
