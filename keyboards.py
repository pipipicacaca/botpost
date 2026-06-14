"""
Инлайн-клавиатуры с custom-emoji иконками (Bot API 10.x).

Каждая кнопка строится через _btn(): задаём семантическую иконку как
обычный unicode-эмодзи, билдер пытается достать соответствующий
`custom_emoji_id` из загруженных паков (TgAndroidIcons / tgiosicons)
и кладёт его в `icon_custom_emoji_id`. Если пак не загрузился — fallback
на обычный эмодзи-префикс прямо в text.

Цветные кнопки задаются через `style`:
  • "success" — зелёная (главное действие — собрать пост)
  • "danger"  — красная (удаление, очистка)
  • "primary" — синяя (добавление)
"""
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

import emoji_pack as emoji


def _btn(builder: InlineKeyboardBuilder, label: str, icons,
         callback_data: str, style: str | None = None) -> None:
    """
    Универсальная кнопка с цепочкой кандидатов:
      icons может быть строкой ИЛИ списком unicode-эмодзи. Берём первый,
      для которого в паках найдётся custom_emoji_id. Если ни один не нашёлся —
      печатаем первый в текст как fallback.
    """
    if isinstance(icons, str):
        icons = [icons]
    for ic in icons:
        cid = emoji.icon_id(ic)
        if cid:
            builder.button(
                text=label,
                callback_data=callback_data,
                icon_custom_emoji_id=cid,
                style=style,
            )
            return
    builder.button(
        text=f"{icons[0]} {label}",
        callback_data=callback_data,
        style=style,
    )


def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    _btn(kb, "Добавить блок",  ["➕", "✚", "🆕"],       "add",       style="primary")
    _btn(kb, "Редактировать",  ["✏️", "📝", "🖊"],       "edit_list")
    _btn(kb, "Предпросмотр",   ["👁", "👀", "🔍"],       "preview")
    _btn(kb, "Собрать пост",   ["📤", "📨", "🚀", "✈️"], "export",    style="success")
    _btn(kb, "Очистить всё",   ["🗑", "🚮", "❌"],       "reset",     style="danger")
    kb.adjust(1, 2, 2)
    return kb.as_markup()


def block_types_menu() -> InlineKeyboardMarkup:
    """Все типы блоков на одном экране, сгруппированы adjust'ом."""
    kb = InlineKeyboardBuilder()
    # Цепочки кандидатов: первый эмодзи, для которого custom_emoji_id найдётся
    # в паках TgAndroidIcons/tgiosicons, и будет иконкой кнопки.
    # Текст и структура
    _btn(kb, "Заголовок",   ["🔠", "🆎", "🅰️", "📰"], "new:heading")
    _btn(kb, "Абзац",       ["📝", "📄", "📃", "✏️"], "new:text")
    _btn(kb, "Список",      ["•", "📋", "📜", "≡"], "new:list")
    _btn(kb, "Нумер.",      ["🔢", "1️⃣", "📑", "📋", "🗒"], "new:numbered")
    _btn(kb, "Чеклист",     ["☑️", "✅", "✔️", "📋", "📝"], "new:checklist")
    _btn(kb, "Цитата",      ["❝", "💬", "🗨️", "📜"], "new:quote")
    # Продвинутое
    _btn(kb, "Таблица",     ["▦", "📊", "📈", "🔲"], "new:table")
    _btn(kb, "Формула",     ["∑", "🧮", "📐", "📏"], "new:math")
    _btn(kb, "Код",         ["💻", "⌨️", "🖥", "📟"], "new:code")
    _btn(kb, "Pull-quote",  ["❞", "💬", "🗨️", "📜"], "new:pullquote")
    _btn(kb, "Спойлер",     ["🙈", "👁‍🗨", "🔽", "▶️", "📂", "📁"], "new:collapsible")
    _btn(kb, "Разделитель", ["➖", "—", "━", "─", "〰️", "▬"], "new:divider")
    # Медиа
    _btn(kb, "Фото",            ["🖼", "📷", "📸", "🌄"], "new:photo")
    _btn(kb, "Фото-поиск",      ["🔍", "🔎", "🔭", "🖼", "📸"], "new:photosearch")
    _btn(kb, "Видео",           ["🎬", "🎥", "📹", "📽", "▶️"], "new:video")
    _btn(kb, "Аудио",           ["🎵", "🎶", "🎧", "🔊", "🎤"], "new:audio")
    _btn(kb, "Альбом (свайп)",  ["🖼", "📷", "📸", "🌅"], "new:collage")
    _btn(kb, "Карта",           ["📍", "🗺", "🌍", "📌"], "new:map")
    _btn(kb, "Назад",           ["⬅️", "◀️", "↩️"], "back")
    kb.adjust(2, 2, 2, 2, 2, 2, 2, 2, 2, 1)
    return kb.as_markup()


def edit_list_menu(blocks: list[dict]) -> InlineKeyboardMarkup:
    """Список блоков для выбора + кнопка назад."""
    # Иконки по типу блока с теми же fallback-цепочками, что в block_types_menu.
    icon_for_type: dict[str, list[str]] = {
        "heading":     ["🔠", "🆎", "🅰️", "📰"],
        "text":        ["📝", "📄", "📃", "✏️"],
        "list":        ["•", "📋", "📜", "≡"],
        "numbered":    ["🔢", "1️⃣", "📑", "📋", "🗒"],
        "checklist":   ["☑️", "✅", "✔️", "📋", "📝"],
        "quote":       ["❝", "💬", "🗨️", "📜"],
        "code":        ["💻", "⌨️", "🖥", "📟"],
        "table":       ["▦", "📊", "📈", "🔲"],
        "math":        ["∑", "🧮", "📐", "📏"],
        "divider":     ["➖", "—", "━", "─", "〰️", "▬"],
        "pullquote":   ["❞", "💬", "🗨️", "📜"],
        "collapsible": ["🙈", "👁‍🗨", "🔽", "▶️", "📂", "📁"],
        "photo":       ["🖼", "📷", "📸", "🌄"],
        "video":       ["🎬", "🎥", "📹", "📽", "▶️"],
        "audio":       ["🎵", "🎶", "🎧", "🔊", "🎤"],
        "collage":     ["🖼", "📷", "📸", "🌅"],
        "map":         ["📍", "🗺", "🌍", "📌"],
    }
    kb = InlineKeyboardBuilder()
    for i, b in enumerate(blocks, 1):
        icons = icon_for_type.get(b["type"], ["▫️"])
        prev = (b.get("content") or "").replace("\n", " ")[:24]
        label = f"{i}. {prev}" if prev else f"{i}."
        _btn(kb, label, icons, f"sel:{b['id']}")
    _btn(kb, "Назад", ["⬅️", "◀️", "↩️"], "back")
    kb.adjust(1)
    return kb.as_markup()


def block_actions_menu(block_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    _btn(kb, "Изменить",  ["✏️", "📝", "🖊"],         f"editblock:{block_id}")
    _btn(kb, "Вверх",     ["🔼", "⬆️", "🔝"],         f"up:{block_id}")
    _btn(kb, "Вниз",      ["🔽", "⬇️", "🔻"],         f"down:{block_id}")
    _btn(kb, "Удалить",   ["🗑", "🚮", "❌"],         f"del:{block_id}", style="danger")
    _btn(kb, "К списку",  ["⬅️", "◀️", "↩️"],         "edit_list")
    kb.adjust(1, 2, 1, 1)
    return kb.as_markup()


def back_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    _btn(kb, "Отмена", ["⬅️", "◀️", "↩️", "❌"], "back")
    return kb.as_markup()
