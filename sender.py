"""
Отправка готового поста как Rich Message (Bot API 10.1, метод sendRichMessage).

Схема подтверждена по core.telegram.org/bots/api:
  sendRichMessage(chat_id, rich_message: InputRichMessage, ...)
  InputRichMessage: ровно ОДНО из полей — markdown ИЛИ html.
  Наш контент — GFM, кладём в поле markdown.

Тело запроса:
  {"chat_id": <id>, "rich_message": {"markdown": "<GFM-текст>"}}

rich_message — вложенный JSON-объект, поэтому сериализуем его строкой
(Bot API принимает JSON-serialized объекты как строку в form/url-encoded;
при отправке application/json — как вложенный объект; используем json=...,
то есть передаём настоящий объект).
"""
import aiohttp

API = "https://api.telegram.org"


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
    payload = {
        "chat_id": chat_id,
        "rich_message": {"markdown": markdown},
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, timeout=20) as r:
                data = await r.json()
                if data.get("ok"):
                    return True, ""
                return False, data.get("description", "unknown error")
    except Exception as e:
        return False, str(e)
