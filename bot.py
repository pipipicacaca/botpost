"""
🤖 PostBuilder Bot — конструктор постов в Telegram Rich Markdown (Bot API 10.1).

Сборка постов происходит в «живой панели» — одно сообщение, которое
редактируется по ходу работы. Чат остаётся чистым, всё видно сразу.

Текст панели (HTML) использует custom emoji из паков TgAndroidIcons и
tgiosicons — у Premium-пользователей они рендерятся как красивые иконки,
у остальных деградируют до обычного Unicode.

Стек: aiogram 3 (async, FSM), SQLite (WAL).
"""
import asyncio
import html as _html
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import database as db
import emoji_pack as emoji
import keyboards as kb
import panel
from photo_search import search_photos
from renderer import BLOCK_NAMES, render_post, render_preview
from sender import send_rich
from telegraph_upload import (
    close_session,
    upload_audio,
    upload_photo,
    upload_video,
)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN не задан. Экспортируй env-переменную BOT_TOKEN=...")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Защита от двойного нажатия «Собрать пост».
_exporting: set[int] = set()

# Фоновые upload-таски коллажа: user_id -> list[Task[str|None]].
# Каждое присланное фото сразу запускает upload, не блокируя обработку
# следующего сообщения. На «Готово» делаем gather() — все аплоады
# завершаются параллельно, а не один за другим.
_collage_tasks: dict[int, list[asyncio.Task]] = {}


class Flow(StatesGroup):
    waiting_content = State()
    waiting_photo = State()
    waiting_photo_query = State()
    waiting_video = State()
    waiting_audio = State()
    waiting_collage = State()
    waiting_map = State()
    waiting_edit = State()


PROMPTS = {
    "heading":     "📝 <b>Заголовок</b>\n\nМожно в начале указать уровень 1–6 и двоеточие, напр. «2: Новости». Без цифры — будет H2.",
    "text":        "📝 <b>Абзац</b>\n\nНапиши текст:",
    "list":        "• <b>Маркированный список</b>\n\nПункты — каждый с новой строки:",
    "numbered":    "🔢 <b>Нумерованный список</b>\n\nПункты — каждый с новой строки, я пронумерую сам:",
    "checklist":   "☑️ <b>Чеклист</b>\n\nПункты — каждый с новой строки. Поставь «+ » в начале строки для отмеченного.",
    "quote":       "❝ <b>Цитата</b>\n\nНапиши текст:",
    "code":        "💻 <b>Код</b>\n\nПервая строка может быть языком в формате «lang: python»:",
    "table":       "▦ <b>Таблица</b>\n\nРяды по строкам, ячейки через «;». Первый ряд — шапка.\nПример:\n<code>Дата;Матч;Счёт\n11.06;Мексика-ЮАР;2:0</code>",
    "math":        "∑ <b>Формула</b>\n\nLaTeX без $. Пример:\n<code>\\sum_{i=1}^n i = \\frac{n(n+1)}2</code>",
    "pullquote":   "❞ <b>Pull-quote</b>\n\nКрупная выделенная цитата:",
    "collapsible": "▸ <b>Спойлер-секция</b>\n\nПервая строка — заголовок, затем с новой строки — содержимое.",
}

MEDIA_PROMPT_STATE = {
    "photo": (Flow.waiting_photo, "🖼 <b>Фото</b>\n\nПришли фото (можно с подписью):"),
    "video": (Flow.waiting_video, "🎬 <b>Видео</b>\n\nПришли видео (можно с подписью):"),
    "audio": (Flow.waiting_audio, "🎵 <b>Аудио</b>\n\nПришли трек:"),
    "collage": (Flow.waiting_collage,
                "🖼 <b>Альбом со свайпом</b>\n\nПришли несколько фото по одному. "
                "Когда закончишь — нажми «Готово»."),
    "map": (Flow.waiting_map,
            "📍 <b>Карта</b>\n\nПришли геопозицию (скрепка → Геопозиция) "
            "или координаты «55.75, 37.61»."),
}


# ────────────────────── view-билдеры (текст панели) ──────────────────────

def _h(text: str) -> str:
    """HTML-escape + подстановка custom emoji."""
    return emoji.html(_html.escape(text, quote=False))


def _build_main_view(blocks: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    """Главный экран панели: live-превью + основные действия."""
    head = emoji.html("🛠 <b>Конструктор постов</b>\n\n")
    if not blocks:
        body = emoji.html("📭 Пост пуст. Жми <b>➕ Добавить блок</b>, чтобы начать.")
    else:
        body = emoji.html("📋 <b>Структура поста:</b>\n\n") + _h(render_preview(blocks))
    return head + body, kb.main_menu()


def _build_block_types_view() -> tuple[str, InlineKeyboardMarkup]:
    text = emoji.html(
        "🧱 <b>Какой блок добавить?</b>\n\n"
        "📝 Текст и структура — заголовки, абзацы, списки.\n"
        "🎯 Продвинутое — таблицы, формулы, код, спойлеры.\n"
        "🖼 Медиа — фото, видео, аудио, альбом, карта."
    )
    return text, kb.block_types_menu()


def _build_prompt_view(prompt_html: str) -> tuple[str, InlineKeyboardMarkup]:
    """Экран ожидания ввода: текст-подсказка + кнопка отмены."""
    return emoji.html(prompt_html), kb.back_menu()


def _build_edit_list_view(blocks: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    head = emoji.html("✏️ <b>Редактирование</b>\n\nВыбери блок:\n\n")
    return head + _h(render_preview(blocks)), kb.edit_list_menu(blocks)


def _build_block_actions_view(block: dict) -> tuple[str, InlineKeyboardMarkup]:
    name = BLOCK_NAMES.get(block["type"], block["type"])
    preview = (block.get("content") or "—")[:300]
    text = (
        emoji.html(f"<b>{_html.escape(name)}</b>\n\n")
        + _h(preview)
    )
    return text, kb.block_actions_menu(block["id"])


def _build_collage_view(count: int) -> tuple[str, InlineKeyboardMarkup]:
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    b.button(text=f"✅ Готово ({count} фото)", callback_data="collage_done")
    b.button(text="⬅️ Отмена", callback_data="back")
    b.adjust(1)
    text = emoji.html(
        "🖼 <b>Альбом со свайпом</b>\n\n"
        f"Добавлено фото: <b>{count}</b>.\n"
        "Шли ещё или жми «Готово»."
    )
    return text, b.as_markup()


# ────────────────────── helpers ──────────────────────

async def _show_main(target):
    user_id = target.from_user.id
    post_id = await db.get_or_create_active_post(user_id)
    blocks = await db.get_blocks(post_id)
    text, markup = _build_main_view(blocks)
    await panel.show(bot, target, text, markup)


# ────────────────────── handlers ──────────────────────

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    # /start всегда даёт свежую панель.
    await panel.reset(bot, message.from_user.id)
    await _show_main(message)


@dp.message(Command("emojis"))
async def cmd_emojis(message: Message):
    """
    Диагностика: какие unicode-эмодзи доступны в загруженных паках.
    Внизу — те 6 кнопок, по которым были жалобы, помечены ✅/❌ в зависимости
    от того, нашли мы для них custom_emoji_id или нет.
    """
    keys = emoji.all_keys()
    if not keys:
        await message.answer("❌ Паки не загружены. Проверь логи: `failed to load pack ...`")
        return

    # Цепочки кандидатов из keyboards.py — должны совпадать.
    diag = {
        "Нумер.":      ["🔢", "1️⃣", "📑", "📋", "🗒"],
        "Чеклист":     ["☑️", "✅", "✔️", "📋", "📝"],
        "Спойлер":     ["🙈", "👁‍🗨", "🔽", "▶️", "📂", "📁"],
        "Разделитель": ["➖", "—", "━", "─", "〰️", "▬"],
        "Фото-поиск":  ["🔍", "🔎", "🔭", "🖼", "📸"],
        "Видео":       ["🎬", "🎥", "📹", "📽", "▶️"],
    }
    lines = [f"📦 <b>Загружено эмодзи:</b> {len(keys)}\n"]
    lines.append("🔎 <b>Диагностика проблемных кнопок:</b>\n")
    for label, candidates in diag.items():
        hits = [c for c in candidates if emoji.icon_id(c)]
        status = "✅" if hits else "❌"
        chain = " → ".join(candidates)
        lines.append(f"{status} <b>{label}</b>: {chain}")
        if hits:
            lines.append(f"    │ матч: {hits[0]}")
        lines.append("")

    lines.append("🎨 <b>Все доступные эмодзи в паках:</b>")
    # Группами по 30 — иначе одно длинное сообщение нечитаемо.
    chunk = " ".join(keys)
    lines.append(chunk[:3500])
    if len(chunk) > 3500:
        lines.append(f"\n… +{len(chunk) - 3500} символов")

    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.callback_query(F.data == "back")
async def cb_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await _show_main(call)
    await call.answer()


@dp.callback_query(F.data == "add")
async def cb_add(call: CallbackQuery):
    text, markup = _build_block_types_view()
    await panel.show(bot, call, text, markup)
    await call.answer()


@dp.callback_query(F.data.startswith("new:"))
async def cb_new_block(call: CallbackQuery, state: FSMContext):
    btype = call.data.split(":", 1)[1]
    post_id = await db.get_or_create_active_post(call.from_user.id)

    if btype == "divider":
        await db.add_block(post_id, "divider")
        await _show_main(call)
        await call.answer("Разделитель добавлен")
        return

    if btype == "photosearch":
        await state.set_state(Flow.waiting_photo_query)
        text, markup = _build_prompt_view("🔍 <b>Поиск фото</b>\n\nЧто искать? Напиши запрос:")
        await panel.show(bot, call, text, markup)
        await call.answer()
        return

    if btype in MEDIA_PROMPT_STATE:
        st, prompt_text = MEDIA_PROMPT_STATE[btype]
        await state.set_state(st)
        # Сбрасываем накопленные параллельные таски прошлого коллажа.
        _collage_tasks.pop(call.from_user.id, None)
        text, markup = _build_prompt_view(prompt_text)
        await panel.show(bot, call, text, markup)
        await call.answer()
        return

    # Текстовые/структурные блоки.
    await state.update_data(btype=btype, post_id=post_id)
    await state.set_state(Flow.waiting_content)
    text, markup = _build_prompt_view(PROMPTS.get(btype, "Текст:"))
    await panel.show(bot, call, text, markup)
    await call.answer()


@dp.message(Flow.waiting_content)
async def on_content(message: Message, state: FSMContext):
    data = await state.get_data()
    btype = data["btype"]
    text = message.text or ""
    extra: dict = {}

    if btype == "heading":
        if ":" in text and text.split(":", 1)[0].strip().isdigit():
            lvl, rest = text.split(":", 1)
            extra["level"] = int(lvl.strip())
            text = rest.strip()
        else:
            extra["level"] = 2
    elif btype == "code":
        first, _, rest = text.partition("\n")
        if first.lower().startswith("lang:"):
            extra["lang"] = first.split(":", 1)[1].strip()
            text = rest
    elif btype == "collapsible":
        title, _, body = text.partition("\n")
        extra["title"] = title.strip() or "Подробнее"
        text = body.strip()

    await db.add_block(data["post_id"], btype, content=text, extra=extra or None)
    await state.clear()
    await panel.delete_user_message(message)
    await _show_main(message)


@dp.message(Flow.waiting_photo, F.photo)
async def on_photo(message: Message, state: FSMContext):
    post_id = await db.get_or_create_active_post(message.from_user.id)
    file_id = message.photo[-1].file_id
    # Подсказываем «грузим» прямо в панели.
    await panel.show(bot, message, emoji.html("⏳ <b>Загружаю фото…</b>"), kb.back_menu())
    url = await upload_photo(bot, BOT_TOKEN, file_id)
    if url:
        await db.add_block(post_id, "photo", content=message.caption or "",
                           media_id=file_id, extra={"url": url})
    else:
        await db.add_block(post_id, "photo", content=message.caption or "",
                           media_id=file_id)
    await state.clear()
    await panel.delete_user_message(message)
    await _show_main(message)


@dp.message(Flow.waiting_video, F.video)
async def on_video(message: Message, state: FSMContext):
    post_id = await db.get_or_create_active_post(message.from_user.id)
    file_id = message.video.file_id
    await panel.show(bot, message, emoji.html("⏳ <b>Загружаю видео…</b>"), kb.back_menu())
    url = await upload_video(bot, BOT_TOKEN, file_id)
    if url:
        await db.add_block(post_id, "video", content=message.caption or "",
                           media_id=file_id, extra={"url": url})
    else:
        await db.add_block(post_id, "video", content=message.caption or "",
                           media_id=file_id)
    await state.clear()
    await panel.delete_user_message(message)
    await _show_main(message)


@dp.message(Flow.waiting_audio, F.audio)
async def on_audio(message: Message, state: FSMContext):
    post_id = await db.get_or_create_active_post(message.from_user.id)
    file_id = message.audio.file_id
    await panel.show(bot, message, emoji.html("⏳ <b>Загружаю аудио…</b>"), kb.back_menu())
    mime = (message.audio.mime_type or "").lower()
    ext = "ogg" if "ogg" in mime else "mp3"
    url = await upload_audio(bot, file_id, ext)
    if url:
        await db.add_block(post_id, "audio", content=message.caption or "",
                           media_id=file_id, extra={"url": url})
    else:
        await db.add_block(post_id, "audio", content=message.caption or "",
                           media_id=file_id)
    await state.clear()
    await panel.delete_user_message(message)
    await _show_main(message)


@dp.message(Flow.waiting_collage, F.photo)
async def on_collage_photo(message: Message, state: FSMContext):
    user_id = message.from_user.id
    file_id = message.photo[-1].file_id
    # Стартуем upload фоном — не блокируем приём следующих фото.
    # При media-group от Telegram сообщения приходят пачкой; так аплоады
    # реально идут параллельно (5 фото за время одного).
    task = asyncio.create_task(upload_photo(bot, BOT_TOKEN, file_id))
    tasks = _collage_tasks.setdefault(user_id, [])
    tasks.append(task)
    await panel.delete_user_message(message)
    text, markup = _build_collage_view(len(tasks))
    await panel.show(bot, message, text, markup)


@dp.callback_query(F.data == "collage_done")
async def cb_collage_done(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    tasks = _collage_tasks.pop(user_id, [])
    if not tasks:
        await call.answer("Нет фото", show_alert=True)
        return
    # Дожидаемся ВСЕ параллельные аплоады разом.
    await panel.show(bot, call, emoji.html("⏳ <b>Жду загрузку фото…</b>"), kb.back_menu())
    results = await asyncio.gather(*tasks, return_exceptions=True)
    photos = [r for r in results if isinstance(r, str) and r]
    if not photos:
        await call.answer("Ни одно фото не загрузилось", show_alert=True)
        await _show_main(call)
        return
    post_id = await db.get_or_create_active_post(user_id)
    await db.add_block(post_id, "collage", extra={"urls": photos})
    await state.clear()
    await _show_main(call)
    failed = len(results) - len(photos)
    msg = f"Альбом из {len(photos)} фото добавлен"
    if failed:
        msg += f" (не загрузилось: {failed})"
    await call.answer(msg)


@dp.message(Flow.waiting_map)
async def on_map(message: Message, state: FSMContext):
    lat = lon = None
    if message.location:
        lat, lon = message.location.latitude, message.location.longitude
    elif message.text and "," in message.text:
        try:
            a, b = message.text.split(",", 1)
            lat, lon = float(a.strip()), float(b.strip())
        except ValueError:
            pass
    if lat is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        await panel.delete_user_message(message)
        text, markup = _build_prompt_view(
            "📍 <b>Карта</b>\n\n"
            "Не понял координаты. Пришли геопозицию (скрепка) "
            "или координаты «55.75, 37.61» (широта −90…90, долгота −180…180)."
        )
        await panel.show(bot, message, text, markup)
        return
    post_id = await db.get_or_create_active_post(message.from_user.id)
    await db.add_block(post_id, "map", extra={"lat": lat, "lon": lon})
    await state.clear()
    await panel.delete_user_message(message)
    await _show_main(message)


@dp.message(Flow.waiting_photo_query)
async def on_photo_query(message: Message, state: FSMContext):
    query = message.text or ""
    await panel.delete_user_message(message)
    await panel.show(bot, message, emoji.html("⏳ <b>Ищу фото…</b>"), kb.back_menu())
    results = await search_photos(query)
    await state.clear()
    if not results:
        text, markup = _build_prompt_view(
            "🔍 <b>Поиск фото</b>\n\n😕 Ничего не нашёл. Попробуй другой запрос."
        )
        await panel.show(bot, message, text, markup)
        return
    post_id = await db.get_or_create_active_post(message.from_user.id)
    await db.add_block(post_id, "photo", extra={"url": results[0]})
    await _show_main(message)


@dp.callback_query(F.data == "preview")
async def cb_preview(call: CallbackQuery):
    post_id = await db.get_or_create_active_post(call.from_user.id)
    blocks = await db.get_blocks(post_id)
    if not blocks:
        await call.answer("Пост пуст", show_alert=True)
        return
    text = emoji.html("👁 <b>Структура поста:</b>\n\n") + _h(render_preview(blocks))
    await panel.show(bot, call, text, kb.main_menu())
    await call.answer()


@dp.callback_query(F.data == "edit_list")
async def cb_edit_list(call: CallbackQuery, state: FSMContext):
    await state.clear()
    post_id = await db.get_or_create_active_post(call.from_user.id)
    blocks = await db.get_blocks(post_id)
    if not blocks:
        await call.answer("Пост пуст", show_alert=True)
        await _show_main(call)
        return
    text, markup = _build_edit_list_view(blocks)
    await panel.show(bot, call, text, markup)
    await call.answer()


@dp.callback_query(F.data.startswith("sel:"))
async def cb_select_block(call: CallbackQuery):
    block_id = int(call.data.split(":")[1])
    block = await db.get_block(block_id)
    if not block:
        await call.answer("Не найдено", show_alert=True)
        return
    text, markup = _build_block_actions_view(block)
    await panel.show(bot, call, text, markup)
    await call.answer()


@dp.callback_query(F.data.startswith("editblock:"))
async def cb_edit_block(call: CallbackQuery, state: FSMContext):
    block_id = int(call.data.split(":")[1])
    await state.update_data(edit_block_id=block_id)
    await state.set_state(Flow.waiting_edit)
    text, markup = _build_prompt_view("✏️ <b>Редактирование блока</b>\n\nНовый текст:")
    await panel.show(bot, call, text, markup)
    await call.answer()


@dp.message(Flow.waiting_edit)
async def on_edit(message: Message, state: FSMContext):
    data = await state.get_data()
    await db.update_block(data["edit_block_id"], content=message.text or "")
    await state.clear()
    await panel.delete_user_message(message)
    await _show_main(message)


@dp.callback_query(F.data.startswith("up:"))
async def cb_up(call: CallbackQuery):
    await db.move_block(int(call.data.split(":")[1]), -1)
    await _refresh_edit_list(call)


@dp.callback_query(F.data.startswith("down:"))
async def cb_down(call: CallbackQuery):
    await db.move_block(int(call.data.split(":")[1]), +1)
    await _refresh_edit_list(call)


@dp.callback_query(F.data.startswith("del:"))
async def cb_del(call: CallbackQuery):
    await db.delete_block(int(call.data.split(":")[1]))
    await call.answer("Удалено")
    await _refresh_edit_list(call)


async def _refresh_edit_list(call: CallbackQuery):
    post_id = await db.get_or_create_active_post(call.from_user.id)
    blocks = await db.get_blocks(post_id)
    if not blocks:
        await _show_main(call)
        await call.answer()
        return
    text, markup = _build_edit_list_view(blocks)
    await panel.show(bot, call, text, markup)
    await call.answer()


@dp.callback_query(F.data == "export")
async def cb_export(call: CallbackQuery):
    uid = call.from_user.id
    if uid in _exporting:
        await call.answer("Уже собираю, подожди…")
        return
    # Подтверждаем callback СРАЗУ: у Telegram ~15с лимит на answerCallbackQuery.
    await call.answer()
    _exporting.add(uid)
    try:
        await _do_export(call)
    finally:
        _exporting.discard(uid)


async def _do_export(call: CallbackQuery):
    post_id = await db.get_or_create_active_post(call.from_user.id)
    blocks = await db.get_blocks(post_id)
    if not blocks:
        await call.message.answer("Пост пуст — нечего собирать.")
        return

    # Панель → «собираю», конечный пост уйдёт отдельным сообщением.
    await panel.show(bot, call, emoji.html("📤 <b>Собираю пост…</b>"), kb.back_menu())

    # Кастомные эмодзи в финальном посте — превращаем unicode → tg-emoji
    # внутри markdown (![](tg://emoji?id=...)).
    text = emoji.md(render_post(blocks))
    if text:
        ok, err = await send_rich(BOT_TOKEN, call.message.chat.id, text)
        if not ok:
            logging.warning("sendRichMessage failed: %s", err)
            await call.message.answer(
                "⚠️ Rich-формат не отправился (" + err[:120] + "). Сырой текст ниже:",
                parse_mode=None)
            await call.message.answer(text, parse_mode=None)

    # Аудио без публичного URL — отдельным сообщением.
    for b in blocks:
        if b["type"] == "audio" and not (b.get("extra") or {}).get("url") and b["media_id"]:
            await call.message.answer_audio(b["media_id"], caption=b.get("content") or None)

    await _show_main(call)


@dp.callback_query(F.data == "reset")
async def cb_reset(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await db.reset_post(call.from_user.id)
    await _show_main(call)
    await call.answer("Пост очищен")


async def main():
    await db.init_db()
    await emoji.load_packs(bot)
    logging.info("PostBuilder bot started")
    try:
        await dp.start_polling(bot)
    finally:
        # Аккуратно отпускаем ресурсы: keep-alive соединения и SQLite handle.
        await close_session()
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
