"""Инлайн-клавиатуры бота."""
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить блок", callback_data="add")
    kb.button(text="✏️ Редактировать", callback_data="edit_list")
    kb.button(text="👁 Предпросмотр", callback_data="preview")
    kb.button(text="📤 Собрать пост", callback_data="export")
    kb.button(text="🗑 Очистить всё", callback_data="reset")
    kb.adjust(1, 2, 2)
    return kb.as_markup()


def block_types_menu() -> InlineKeyboardMarkup:
    """Все типы блоков (на одной странице, сгруппированы)."""
    kb = InlineKeyboardBuilder()
    # Текст и структура
    kb.button(text="🔠 Заголовок", callback_data="new:heading")
    kb.button(text="📝 Абзац", callback_data="new:text")
    kb.button(text="• Список", callback_data="new:list")
    kb.button(text="1. Нумер.", callback_data="new:numbered")
    kb.button(text="☑️ Чеклист", callback_data="new:checklist")
    kb.button(text="❝ Цитата", callback_data="new:quote")
    # Продвинутое
    kb.button(text="▦ Таблица", callback_data="new:table")
    kb.button(text="∑ Формула", callback_data="new:math")
    kb.button(text="💻 Код", callback_data="new:code")
    kb.button(text="❞ Pull-quote", callback_data="new:pullquote")
    kb.button(text="▸ Спойлер", callback_data="new:collapsible")
    kb.button(text="➖ Разделитель", callback_data="new:divider")
    # Медиа
    kb.button(text="🖼 Фото", callback_data="new:photo")
    kb.button(text="🔍 Фото-поиск", callback_data="new:photosearch")
    kb.button(text="🎬 Видео", callback_data="new:video")
    kb.button(text="🎵 Аудио", callback_data="new:audio")
    kb.button(text="🖼🖼 Коллаж", callback_data="new:collage")
    kb.button(text="📍 Карта", callback_data="new:map")
    kb.button(text="⬅️ Назад", callback_data="back")
    kb.adjust(2, 2, 2, 2, 2, 2, 2, 2, 2, 1)
    return kb.as_markup()


def edit_list_menu(blocks: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for i, b in enumerate(blocks, 1):
        kb.button(text=f"{i}. {b['type']}", callback_data=f"sel:{b['id']}")
    kb.button(text="⬅️ Назад", callback_data="back")
    kb.adjust(1)
    return kb.as_markup()


def block_actions_menu(block_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Изменить", callback_data=f"editblock:{block_id}")
    kb.button(text="🔼 Вверх", callback_data=f"up:{block_id}")
    kb.button(text="🔽 Вниз", callback_data=f"down:{block_id}")
    kb.button(text="🗑 Удалить", callback_data=f"del:{block_id}")
    kb.button(text="⬅️ К списку", callback_data="edit_list")
    kb.adjust(1, 2, 1, 1)
    return kb.as_markup()


def back_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ В меню", callback_data="back")
    return kb.as_markup()
