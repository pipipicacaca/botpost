"""
Подгрузка premium-custom-emoji паков и подстановка их в текст сообщений.

Telegram custom emoji работают так:
  • Это «премиум-стикеры», у каждого есть `custom_emoji_id` и обычный
    Unicode-эмодзи как alt (видят non-premium пользователи).
  • Бот может слать кастомные эмодзи без премиума:
      - HTML:     <tg-emoji emoji-id="...">📁</tg-emoji>
      - Rich MD:  ![📁](tg://emoji?id=...)
  • В тексте InlineKeyboardButton custom emoji не поддерживается —
    жёсткое ограничение Telegram Bot API.

Что делаем:
  1. На старте грузим оба пака через bot.get_sticker_set(name).
  2. Индексируем по alt-unicode (Sticker.emoji → Sticker.custom_emoji_id).
  3. Helper md()/html() greedy-меняет в тексте unicode-эмодзи на rich-разметку.

Пользователи с TG Premium увидят красивые кастомные иконки,
non-premium — обычный Unicode (graceful degradation).
"""
import logging
from aiogram import Bot

log = logging.getLogger("emoji_pack")

# Имена паков из URL /addemoji/<name>
PACKS = ("TgAndroidIcons", "tgiosicons")

# alt-unicode -> custom_emoji_id
_map: dict[str, str] = {}
_keys_by_len: list[str] = []  # отсортированные ключи для greedy-матчинга


async def load_packs(bot: Bot) -> None:
    """Один раз при старте бота."""
    for name in PACKS:
        try:
            pack = await bot.get_sticker_set(name=name)
            n = 0
            for st in pack.stickers:
                if st.emoji and st.custom_emoji_id:
                    if _map.setdefault(st.emoji, st.custom_emoji_id) == st.custom_emoji_id:
                        n += 1
            log.info("loaded pack %s: %d emojis indexed (total: %d)",
                     name, n, len(_map))
        except Exception as e:
            log.warning("failed to load pack %s: %s", name, e)
    # Greedy матчинг от длинных к коротким — для multi-codepoint эмодзи.
    _keys_by_len[:] = sorted(_map.keys(), key=len, reverse=True)
    # Логируем что у нас есть — поможет подкрутить маппинги под реальный пак.
    if _map:
        sample = " ".join(list(_map.keys())[:30])
        log.info("emoji pack sample: %s …", sample)


def icon_id(unicode_emoji: str) -> str | None:
    """
    custom_emoji_id для иконки на InlineKeyboardButton.icon_custom_emoji_id.

    Пробуем несколько вариантов написания (с/без VS-16, разные ZWJ-формы),
    т.к. пак может индексироваться чуть иначе.
    """
    if not unicode_emoji:
        return None
    # 1) прямой матч
    if unicode_emoji in _map:
        return _map[unicode_emoji]
    # 2) без variation selector U+FE0F (часто отсутствует в одной из форм)
    stripped = unicode_emoji.replace("️", "")
    if stripped in _map:
        return _map[stripped]
    # 3) с добавленным VS-16
    with_vs = unicode_emoji + "️" if "️" not in unicode_emoji else unicode_emoji
    if with_vs in _map:
        return _map[with_vs]
    return None


def _substitute(text: str, wrapper) -> str:
    """Общий движок подстановки. wrapper(key, cid) -> строка."""
    if not _map:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        hit = None
        for k in _keys_by_len:
            if text.startswith(k, i):
                hit = k
                break
        if hit is not None:
            out.append(wrapper(hit, _map[hit]))
            i += len(hit)
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def html(text: str) -> str:
    """Для parse_mode='HTML' (обычные sendMessage/editMessageText)."""
    return _substitute(text, lambda k, cid: f'<tg-emoji emoji-id="{cid}">{k}</tg-emoji>')


def md(text: str) -> str:
    """Для rich-markdown поля InputRichMessage.markdown."""
    return _substitute(text, lambda k, cid: f'![{k}](tg://emoji?id={cid})')


def loaded() -> bool:
    return bool(_map)


def all_keys() -> list[str]:
    """Все unicode-эмодзи, проиндексированные из паков (для диагностики)."""
    return list(_map.keys())
