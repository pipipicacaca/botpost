"""
Отправка готового поста как Rich Message (Bot API 10.1, метод sendRichMessage).

Спека (core.telegram.org/bots/api#sendrichmessage):
  sendRichMessage(chat_id, rich_message: InputRichMessage, ...)
  InputRichMessage: ровно ОДНО из полей — markdown ИЛИ html.

Тело запроса (application/json):
  {"chat_id": <id>, "rich_message": {"markdown": "<GFM-текст>"}}
"""
import aiohttp

API = "https://api.telegram.org"
TIMEOUT = aiohttp.ClientTimeout(total=30)


async def send_rich(token: str, chat_id: int, markdown: str,
                    reply_markup: dict | None = None) -> tuple[bool, str]:
    """
    Отправляет Rich Message с контентом в Markdown (GFM).
    Возвращает (ok, error_description). При ok=False вызывающий код
    применяет fallback на обычное сообщение.
    """
    if not markdown.strip():
        return True, ""
    url = f"{API}/bot{token}/sendRichMessage"
    payload: dict = {
        "chat_id": chat_id,
        "rich_message": {"markdown": markdown},
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as s:
            async with s.post(url, json=payload) as r:
                try:
                    data = await r.json(content_type=None)
                except Exception:
                    return False, f"HTTP {r.status}: {(await r.text())[:200]}"
                if data.get("ok"):
                    return True, ""
                # Telegram отдаёт error_code + description — полезно для диагностики.
                code = data.get("error_code", r.status)
                desc = data.get("description", "unknown error")
                return False, f"[{code}] {desc}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
