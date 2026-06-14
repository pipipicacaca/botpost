"""
🤖 PostBuilder Bot — конструктор постов в Telegram Rich Markdown (Bot API 10.1).

Человек собирает пост из блоков по кнопкам, пишет текст руками — бот рендерит
в Rich Markdown (заголовки, таблицы, LaTeX, чеклисты, code, цитаты, спойлеры)
и отправляет как Rich Message, который Telegram рисует нативно. Фото/видео/
аудио/коллаж/карта уходят отдельными сообщениями.

Стек: aiogram 3 (async, FSM), SQLite (WAL), деплой на Railway.
"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery

import database as db
import keyboards as kb
from renderer import render_post, render_preview, BLOCK_NAMES
from photo_search import search_photos
from sender import send_rich
from telegraph_upload import upload_photo, upload_video, upload_audio

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN не задан. Экспортируй env-переменную BOT_TOKEN=...")

# Служебные сообщения шлём БЕЗ parse_mode (plain) — иначе спецсимволы (_ * `)
# в подсказках/превью/тексте пользователя ломают разбор разметки.
# Готовый пост уходит отдельно через sender.send_rich (свой запрос).
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Защита от двойного нажатия «Собрать пост».
_exporting: set[int] = set()


class Flow(StatesGroup):
    waiting_content = State()
    waiting_photo = State()
    waiting_photo_query = State()
    waiting_video = State()
    waiting_audio = State()
    waiting_collage = State()
    waiting_map = State()
    waiting_edit = State()


# Подсказки по вводу для текстовых типов.
PROMPTS = {
    "heading":     "Заголовок. Можно в начале указать уровень 1–6 и двоеточие, напр. «2: Новости». Без цифры — будет H2:",
    "text":        "Текст абзаца:",
    "list":        "Пункты списка — каждый с новой строки:",
    "numbered":    "Пункты — каждый с новой строки (пронумерую сам):",
    "checklist":   "Пункты чеклиста — каждый с новой строки. Поставь «+ » в начале строки для отмеченного пункта:",
    "quote":       "Текст цитаты:",
    "code":        "Код. Первая строка может быть языком в формате «lang: python», иначе без подсветки:",
    "table":       "Таблица: ряды по строкам, ячейки через «;». Первый ряд — шапка.\nПример:\nДата;Матч;Счёт\n11.06;Мексика-ЮАР;2:0",
    "math":        "Формула в LaTeX (без $). Пример: \\sum_{i=1}^n i = \\frac{n(n+1)}2",
    "pullquote":   "Текст pull-quote (крупная выделенная цитата):",
    "collapsible": "Сначала заголовок секции, затем с новой строки — содержимое:",
}

MEDIA_PROMPT_STATE = {
    "photo": (Flow.waiting_photo, "📤 Пришли фото (можно с подписью):"),
    "video": (Flow.waiting_video, "🎬 Пришли видео (можно с подписью):"),
    "audio": (Flow.waiting_audio, "🎵 Пришли аудио/трек:"),
    "collage": (Flow.waiting_collage, "🖼🖼 Пришли несколько фото (по одному). Когда закончишь — нажми «Готово»."),
    "map": (Flow.waiting_map, "📍 Пришли геолокацию (скрепка → Геопозиция) или координаты «55.75, 37.61»:"),
}


@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await db.get_or_create_active_post(message.from_user.id)
    await message.answer(
        "👋 Конструктор постов\n\n"
        "Собираю посты в Rich Markdown — Telegram рисует заголовки, таблицы, "
        "формулы и медиа нативно. Пиши текст, я оформлю.\n\n"
        "Жми ➕ Добавить блок",
        reply_markup=kb.main_menu(),
    )


@dp.callback_query(F.data == "back")
async def cb_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.answer("🛠 Конструктор постов — выбери действие:", reply_markup=kb.main_menu())
    await call.answer()


@dp.callback_query(F.data == "add")
async def cb_add(call: CallbackQuery):
    await call.message.answer("Какой блок добавить?", reply_markup=kb.block_types_menu())
    await call.answer()


@dp.callback_query(F.data.startswith("new:"))
async def cb_new_block(call: CallbackQuery, state: FSMContext):
    btype = call.data.split(":", 1)[1]
    post_id = await db.get_or_create_active_post(call.from_user.id)

    if btype == "divider":
        await db.add_block(post_id, "divider")
        await call.message.answer("✅ Разделитель добавлен.", reply_markup=kb.main_menu())
        await call.answer()
        return

    if btype == "photosearch":
        await state.set_state(Flow.waiting_photo_query)
        await call.message.answer("🔍 Что искать? Напиши запрос:", reply_markup=kb.back_menu())
        await call.answer()
        return

    if btype in MEDIA_PROMPT_STATE:
        st, text = MEDIA_PROMPT_STATE[btype]
        await state.set_state(st)
        await state.update_data(collage=[])
        await call.message.answer(text, reply_markup=kb.back_menu())
        await call.answer()
        return

    # Текстовые/структурные блоки.
    await state.update_data(btype=btype, post_id=post_id)
    await state.set_state(Flow.waiting_content)
    await call.message.answer(PROMPTS.get(btype, "Текст:"), reply_markup=kb.back_menu())
    await call.answer()


@dp.message(Flow.waiting_content)
async def on_content(message: Message, state: FSMContext):
    data = await state.get_data()
    btype = data["btype"]
    text = message.text or ""
    extra = {}

    if btype == "heading":
        # «2: текст» -> level=2; иначе H2.
        if ":" in text and text.split(":", 1)[0].strip().isdigit():
            lvl, rest = text.split(":", 1)
            extra["level"] = int(lvl.strip())
            text = rest.strip()
        else:
            extra["level"] = 2
    elif btype == "code":
        # «lang: python» в первой строке -> язык.
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
    await message.answer(f"✅ Блок «{BLOCK_NAMES.get(btype, btype)}» добавлен.", reply_markup=kb.main_menu())


@dp.message(Flow.waiting_photo, F.photo)
async def on_photo(message: Message, state: FSMContext):
    post_id = await db.get_or_create_active_post(message.from_user.id)
    file_id = message.photo[-1].file_id
    await message.answer("⏳ Загружаю фото...")
    url = await upload_photo(bot, BOT_TOKEN, file_id)
    if not url:
        await message.answer("⚠️ Не удалось загрузить фото. Проверь, что бот — админ канала-хранилища.",
                             reply_markup=kb.main_menu())
        await state.clear()
        return
    await db.add_block(post_id, "photo", content=message.caption or "",
                       media_id=file_id, extra={"url": url})
    await state.clear()
    await message.answer("✅ Фото добавлено (встроится в пост).", reply_markup=kb.main_menu())


@dp.message(Flow.waiting_video, F.video)
async def on_video(message: Message, state: FSMContext):
    post_id = await db.get_or_create_active_post(message.from_user.id)
    file_id = message.video.file_id
    await message.answer("⏳ Загружаю видео...")
    url = await upload_video(bot, BOT_TOKEN, file_id)
    if not url:
        await message.answer("⚠️ Не удалось загрузить видео. Уйдёт отдельным сообщением.",
                             reply_markup=kb.main_menu())
        await db.add_block(post_id, "video", content=message.caption or "",
                           media_id=file_id)  # без url -> отдельно
        await state.clear()
        return
    await db.add_block(post_id, "video", content=message.caption or "",
                       media_id=file_id, extra={"url": url})
    await state.clear()
    await message.answer("✅ Видео добавлено (встроится в пост).", reply_markup=kb.main_menu())


@dp.message(Flow.waiting_audio, F.audio)
async def on_audio(message: Message, state: FSMContext):
    post_id = await db.get_or_create_active_post(message.from_user.id)
    file_id = message.audio.file_id
    await message.answer("⏳ Загружаю аудио...")
    # MIME аудио из Telegram обычно audio/mpeg или audio/ogg.
    # Catbox сохранит расширение из filename — важно для Telegram (определяет тип по URL).
    mime = (message.audio.mime_type or "").lower()
    ext = "ogg" if "ogg" in mime else "mp3"
    url = await upload_audio(bot, file_id, ext)
    if url:
        await db.add_block(post_id, "audio", content=message.caption or "",
                           media_id=file_id, extra={"url": url})
        await message.answer("✅ Аудио добавлено (встроится в пост).", reply_markup=kb.main_menu())
    else:
        # Не залилось → отправим отдельным сообщением при экспорте.
        await db.add_block(post_id, "audio", content=message.caption or "",
                           media_id=file_id)
        await message.answer("⚠️ Не удалось залить аудио на хостинг — уйдёт отдельным сообщением.",
                             reply_markup=kb.main_menu())
    await state.clear()


@dp.message(Flow.waiting_collage, F.photo)
async def on_collage_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    urls = data.get("collage", [])
    url = await upload_photo(bot, BOT_TOKEN, message.photo[-1].file_id)
    if not url:
        await message.answer("⚠️ Это фото не залилось, пропускаю. Шли следующее.")
        return
    urls.append(url)
    await state.update_data(collage=urls)
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    b.button(text=f"✅ Готово ({len(urls)} фото)", callback_data="collage_done")
    b.button(text="⬅️ Отмена", callback_data="back")
    b.adjust(1)
    await message.answer(f"Добавлено фото: {len(urls)}. Шли ещё или жми «Готово».",
                         reply_markup=b.as_markup())


@dp.callback_query(F.data == "collage_done")
async def cb_collage_done(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    photos = data.get("collage", [])  # это список URL (Telegraph)
    if not photos:
        await call.answer("Нет фото", show_alert=True)
        return
    post_id = await db.get_or_create_active_post(call.from_user.id)
    await db.add_block(post_id, "collage", extra={"urls": photos})
    await state.clear()
    await call.message.answer(f"✅ Коллаж из {len(photos)} фото добавлен (встроится в пост).",
                              reply_markup=kb.main_menu())
    await call.answer()


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
        await message.answer("Не понял координаты. Пришли геопозицию или «55.75, 37.61» "
                             "(широта −90…90, долгота −180…180).")
        return
    post_id = await db.get_or_create_active_post(message.from_user.id)
    await db.add_block(post_id, "map", extra={"lat": lat, "lon": lon})
    await state.clear()
    await message.answer("✅ Карта добавлена.", reply_markup=kb.main_menu())


@dp.message(Flow.waiting_photo_query)
async def on_photo_query(message: Message, state: FSMContext):
    results = await search_photos(message.text or "")
    await state.clear()
    if not results:
        await message.answer("😕 Ничего не нашёл. Загрузи фото вручную.", reply_markup=kb.main_menu())
        return
    post_id = await db.get_or_create_active_post(message.from_user.id)
    # URL — это уже публичный https-линк (Unsplash). Кладём в extra.url,
    # чтобы renderer встроил фото в rich-пост через ![](url).
    await db.add_block(post_id, "photo", extra={"url": results[0]})
    await message.answer("✅ Нашёл фото и добавил (встроится в пост).", reply_markup=kb.main_menu())


@dp.callback_query(F.data == "preview")
async def cb_preview(call: CallbackQuery):
    post_id = await db.get_or_create_active_post(call.from_user.id)
    blocks = await db.get_blocks(post_id)
    await call.message.answer(f"👁 Структура поста:\n\n{render_preview(blocks)}", reply_markup=kb.main_menu())
    await call.answer()


@dp.callback_query(F.data == "edit_list")
async def cb_edit_list(call: CallbackQuery, state: FSMContext):
    await state.clear()
    post_id = await db.get_or_create_active_post(call.from_user.id)
    blocks = await db.get_blocks(post_id)
    if not blocks:
        await call.message.answer("Пост пуст.", reply_markup=kb.main_menu())
        await call.answer()
        return
    await call.message.answer("✏️ Выбери блок:\n\n" + render_preview(blocks),
                              reply_markup=kb.edit_list_menu(blocks))
    await call.answer()


@dp.callback_query(F.data.startswith("sel:"))
async def cb_select_block(call: CallbackQuery):
    block_id = int(call.data.split(":")[1])
    block = await db.get_block(block_id)
    if not block:
        await call.answer("Не найдено", show_alert=True)
        return
    name = BLOCK_NAMES.get(block["type"], block["type"])
    prev = (block.get("content") or "—")[:200]
    await call.message.answer(f"Блок: {name}\n\n{prev}", reply_markup=kb.block_actions_menu(block_id))
    await call.answer()


@dp.callback_query(F.data.startswith("editblock:"))
async def cb_edit_block(call: CallbackQuery, state: FSMContext):
    block_id = int(call.data.split(":")[1])
    await state.update_data(edit_block_id=block_id)
    await state.set_state(Flow.waiting_edit)
    await call.message.answer("✏️ Новый текст блока:", reply_markup=kb.back_menu())
    await call.answer()


@dp.message(Flow.waiting_edit)
async def on_edit(message: Message, state: FSMContext):
    data = await state.get_data()
    await db.update_block(data["edit_block_id"], content=message.text or "")
    await state.clear()
    await message.answer("✅ Блок обновлён.", reply_markup=kb.main_menu())


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
    await call.answer("🗑 Удалено")
    await _refresh_edit_list(call)


async def _refresh_edit_list(call: CallbackQuery):
    post_id = await db.get_or_create_active_post(call.from_user.id)
    blocks = await db.get_blocks(post_id)
    if not blocks:
        await call.message.answer("Пост пуст.", reply_markup=kb.main_menu())
        return
    await call.message.answer("✏️ Выбери блок:\n\n" + render_preview(blocks),
                              reply_markup=kb.edit_list_menu(blocks))
    await call.answer()


@dp.callback_query(F.data == "export")
async def cb_export(call: CallbackQuery):
    uid = call.from_user.id
    if uid in _exporting:
        await call.answer("Уже собираю, подожди…")
        return
    _exporting.add(uid)
    try:
        await _do_export(call)
    finally:
        _exporting.discard(uid)


async def _do_export(call: CallbackQuery):
    post_id = await db.get_or_create_active_post(call.from_user.id)
    blocks = await db.get_blocks(post_id)
    if not blocks:
        await call.answer("Пост пуст", show_alert=True)
        return

    await call.message.answer("📤 Готовый пост:")

    # 1) Весь пост (текст + фото + видео + коллаж + карта) — ОДНИМ rich message.
    text = render_post(blocks)
    if text:
        ok, err = await send_rich(BOT_TOKEN, call.message.chat.id, text)
        if not ok:
            logging.warning("sendRichMessage failed: %s", err)
            await call.message.answer(
                "⚠️ Rich-формат не отправился (" + err[:100] + "). Сырой текст ниже:",
                parse_mode=None)
            await call.message.answer(text, parse_mode=None)

    # 2) Аудио без публичного URL — отдельным сообщением (если хост не принял файл).
    for b in blocks:
        if b["type"] == "audio" and not (b.get("extra") or {}).get("url") and b["media_id"]:
            await call.message.answer_audio(b["media_id"], caption=b.get("content") or None)

    await call.answer("✅ Готово!")


@dp.callback_query(F.data == "reset")
async def cb_reset(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await db.reset_post(call.from_user.id)
    await call.message.answer("🗑 Пост очищен.", reply_markup=kb.main_menu())
    await call.answer()


async def main():
    await db.init_db()
    logging.info("PostBuilder bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
