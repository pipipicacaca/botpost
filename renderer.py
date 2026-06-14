"""
Рендер блоков в ЕДИНЫЙ Telegram Rich Markdown пост (Bot API 10.1).

Весь пост — одно сообщение sendRichMessage с полем markdown.
Медиа встраиваются прямо в текст по URL (Telegraph):
  фото:    ![](url "подпись")
  видео:   ![](url.mp4 "подпись")
  коллаж:  <tg-collage> ![](u1) ![](u2) </tg-collage>
  карта:   <tg-map lat=".." long=".." zoom="14"/>
Rich Markdown допускает HTML-теги внутри (см. док-цию Rich Message Formatting).

Аудио Telegraph не хостит → аудио-блок не встраивается (уходит отдельно).
"""
BLOCK_NAMES = {
    "heading":     "🔠 Заголовок",
    "text":        "📝 Абзац",
    "list":        "• Список",
    "numbered":    "1. Нумерованный",
    "checklist":   "☑️ Чеклист",
    "quote":       "❝ Цитата",
    "code":        "💻 Код",
    "table":       "▦ Таблица",
    "math":        "∑ Формула",
    "divider":     "➖ Разделитель",
    "pullquote":   "❞ Pull-quote",
    "collapsible": "▸ Спойлер-секция",
    "photo":       "🖼 Фото",
    "video":       "🎬 Видео",
    "audio":       "🎵 Аудио",
    "collage":     "🖼🖼 Коллаж",
    "map":         "📍 Карта",
}

# Аудио не встраивается в rich (Telegraph не хостит) → шлём отдельным сообщением.
SEPARATE_TYPES = {"audio"}


def _url(block: dict) -> str:
    """URL медиа из extra['url'] (одиночное медиа)."""
    return (block.get("extra") or {}).get("url", "")


def render_block(block: dict) -> str:
    t = block["type"]
    content = (block.get("content") or "").strip()
    extra = block.get("extra") or {}

    if t == "heading":
        level = min(max(int(extra.get("level", 2)), 1), 6)
        return f"{'#' * level} {content}"

    if t == "text":
        return content

    if t == "list":
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        return "\n".join(f"- {l}" for l in lines)

    if t == "numbered":
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        return "\n".join(f"{i}. {l}" for i, l in enumerate(lines, 1))

    if t == "checklist":
        out = []
        for l in content.splitlines():
            l = l.strip()
            if not l:
                continue
            if l.startswith("+ "):
                out.append(f"- [x] {l[2:].strip()}")
            else:
                out.append(f"- [ ] {l}")
        return "\n".join(out)

    if t == "quote":
        return "\n".join(f"> {l}" for l in content.splitlines())

    if t == "code":
        return f"```{extra.get('lang', '')}\n{content}\n```"

    if t == "table":
        rows = [r for r in content.splitlines() if r.strip()]
        if not rows:
            return ""
        parsed = [[c.strip() for c in r.split(";")] for r in rows]
        width = max(len(r) for r in parsed)
        parsed = [r + [""] * (width - len(r)) for r in parsed]
        head = "| " + " | ".join(parsed[0]) + " |"
        sep = "| " + " | ".join([":---:"] * width) + " |"
        body = "\n".join("| " + " | ".join(r) + " |" for r in parsed[1:])
        return "\n".join([head, sep] + ([body] if body else []))

    if t == "math":
        return f"$$\n{content}\n$$"

    if t == "divider":
        return "---"

    if t == "pullquote":
        return f"<aside>{content}</aside>"

    if t == "collapsible":
        title = extra.get("title", "Подробнее")
        return f"<details>\n<summary>{title}</summary>\n\n{content}\n</details>"

    # ---- Медиа, встраиваемое по URL ----
    if t == "photo":
        url = _url(block)
        if not url:
            return ""
        return f'![]({url} "{content}")' if content else f"![]({url})"

    if t == "video":
        url = _url(block)
        if not url:
            return ""
        return f'![]({url} "{content}")' if content else f"![]({url})"

    if t == "collage":
        urls = extra.get("urls", [])
        if not urls:
            return ""
        inner = "\n".join(f"![]({u})" for u in urls)
        return f"<tg-collage>\n\n{inner}\n\n</tg-collage>"

    if t == "map":
        if "lat" not in extra:
            return ""
        zoom = extra.get("zoom", 14)
        return f'<tg-map lat="{extra["lat"]}" long="{extra["lon"]}" zoom="{zoom}"/>'

    return content


def render_post(blocks: list[dict]) -> str:
    """Все блоки (включая медиа) -> единый Rich Markdown пост."""
    parts = []
    for b in blocks:
        if b["type"] in SEPARATE_TYPES:
            continue  # аудио уходит отдельным сообщением
        s = render_block(b)
        if s:
            parts.append(s)
    return "\n\n".join(parts)


def render_preview(blocks: list[dict]) -> str:
    if not blocks:
        return "_Пост пуст. Добавь первый блок 👇_"
    lines = []
    for i, b in enumerate(blocks, 1):
        name = BLOCK_NAMES.get(b["type"], b["type"])
        prev = (b.get("content") or "").replace("\n", " ")[:32]
        media = b["type"] in ("photo", "video", "audio", "collage", "map")
        if media and not prev:
            tag = name
        else:
            tag = f"{name}: {prev}" if prev else name
        lines.append(f"{i}. {tag}")
    return "\n".join(lines)
