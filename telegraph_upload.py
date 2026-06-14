"""
Загрузка медиа на публичный хостинг для получения ПОСТОЯННОГО прямого URL.

Нужно, потому что rich message требует нормальный публичный URL картинки.
Внутренние getFile-ссылки Telegram и Telegraph не подходят
(ошибка RICH_MESSAGE_PHOTO_NO_MEDIA_FOUND / блокировка).

Стратегия:
  - если задан IMGBB_KEY → грузим на imgbb (самый стабильный, для фото);
  - иначе → Catbox.moe (без ключа, годится и для фото, и для видео).

Переменная окружения (опционально): IMGBB_KEY — ключ с api.imgbb.com.
"""
import os
import base64
import aiohttp

IMGBB_KEY = os.getenv("IMGBB_KEY", "")
CATBOX_API = "https://catbox.moe/user/api.php"
IMGBB_API = "https://api.imgbb.com/1/upload"


async def _download(bot, file_id: str) -> bytes | None:
    """Скачиваем файл из Telegram по file_id."""
    try:
        f = await bot.get_file(file_id)
        buf = await bot.download_file(f.file_path)
        return buf.read()
    except Exception:
        return None


async def _to_catbox(data: bytes, filename: str) -> str | None:
    """Заливка на Catbox.moe. Возвращает прямой URL текстом."""
    try:
        form = aiohttp.FormData()
        form.add_field("reqtype", "fileupload")
        form.add_field("fileToUpload", data, filename=filename)
        async with aiohttp.ClientSession() as s:
            async with s.post(CATBOX_API, data=form, timeout=60) as r:
                text = (await r.text()).strip()
                return text if text.startswith("http") else None
    except Exception:
        return None


async def _to_imgbb(data: bytes) -> str | None:
    """Заливка на imgbb (только изображения). Возвращает прямой URL."""
    try:
        b64 = base64.b64encode(data).decode()
        form = aiohttp.FormData()
        form.add_field("image", b64)
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{IMGBB_API}?key={IMGBB_KEY}", data=form, timeout=60) as r:
                j = await r.json(content_type=None)
                if j.get("success"):
                    return j["data"]["url"]
                return None
    except Exception:
        return None


async def upload_photo(bot, token: str, file_id: str) -> str | None:
    """Фото → публичный URL. imgbb (если ключ) с fallback на Catbox."""
    data = await _download(bot, file_id)
    if not data:
        return None
    if IMGBB_KEY:
        url = await _to_imgbb(data)
        if url:
            return url
    return await _to_catbox(data, "photo.jpg")


async def upload_video(bot, token: str, file_id: str) -> str | None:
    """Видео → публичный URL. Только Catbox (imgbb не хостит видео)."""
    data = await _download(bot, file_id)
    if not data:
        return None
    return await _to_catbox(data, "video.mp4")
