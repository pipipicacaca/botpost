"""
Загрузка медиа на публичный хостинг для получения ПОСТОЯННОГО прямого URL.

Telegram Rich Message (Bot API 10.1, sendRichMessage) встраивает медиа по
HTTPS-URL: `![](https://.../photo.jpg)`. Тип определяется по MIME/расширению,
поэтому имя файла на хосте обязано иметь корректное расширение.

Цепочка хостеров (по приоритету):
  1. imgbb           — самый стабильный для фото; нужен IMGBB_KEY.
  2. Catbox.moe      — без ключа, годится для фото/видео/аудио.
  3. 0x0.st          — без ключа, доступен из РФ как fallback на случай блокировки Catbox.

Лимит Telegram getFile — 20 МБ. Файлы крупнее не скачаем через Bot API,
upload вернёт None, а медиа уйдёт отдельным сообщением.

env: IMGBB_KEY (опц.) — ключ с api.imgbb.com.
"""
import os
import base64
import logging
import aiohttp

log = logging.getLogger("uploader")

IMGBB_KEY = os.getenv("IMGBB_KEY", "")
CATBOX_API = "https://catbox.moe/user/api.php"
IMGBB_API = "https://api.imgbb.com/1/upload"
NULLPOINTER_API = "https://0x0.st"

TIMEOUT = aiohttp.ClientTimeout(total=60)


async def _download(bot, file_id: str) -> bytes | None:
    """Скачиваем файл из Telegram по file_id. Возвращает bytes или None при ошибке."""
    try:
        f = await bot.get_file(file_id)
        if not f.file_path:
            log.warning("get_file: пустой file_path для %s (файл >20МБ?)", file_id)
            return None
        buf = await bot.download_file(f.file_path)
        return buf.read()
    except Exception as e:
        log.warning("download failed (%s): %s", file_id, e)
        return None


async def _to_catbox(data: bytes, filename: str) -> str | None:
    """Заливка на Catbox.moe. Возвращает прямой URL."""
    try:
        form = aiohttp.FormData()
        form.add_field("reqtype", "fileupload")
        form.add_field("fileToUpload", data, filename=filename,
                       content_type="application/octet-stream")
        async with aiohttp.ClientSession(timeout=TIMEOUT) as s:
            async with s.post(CATBOX_API, data=form) as r:
                text = (await r.text()).strip()
                if text.startswith("http"):
                    return text
                log.warning("catbox bad response: %s", text[:200])
                return None
    except Exception as e:
        log.warning("catbox upload failed: %s", e)
        return None


async def _to_nullpointer(data: bytes, filename: str) -> str | None:
    """Заливка на 0x0.st. Доступен из РФ — используется как fallback."""
    try:
        form = aiohttp.FormData()
        form.add_field("file", data, filename=filename,
                       content_type="application/octet-stream")
        async with aiohttp.ClientSession(
            timeout=TIMEOUT,
            headers={"User-Agent": "postbot/1.0 (rich-message-formatter)"},
        ) as s:
            async with s.post(NULLPOINTER_API, data=form) as r:
                text = (await r.text()).strip()
                if text.startswith("http"):
                    return text
                log.warning("0x0.st bad response (%s): %s", r.status, text[:200])
                return None
    except Exception as e:
        log.warning("0x0.st upload failed: %s", e)
        return None


async def _to_imgbb(data: bytes) -> str | None:
    """Заливка на imgbb (только изображения)."""
    try:
        b64 = base64.b64encode(data).decode()
        form = aiohttp.FormData()
        form.add_field("image", b64)
        async with aiohttp.ClientSession(timeout=TIMEOUT) as s:
            async with s.post(f"{IMGBB_API}?key={IMGBB_KEY}", data=form) as r:
                j = await r.json(content_type=None)
                if j.get("success"):
                    return j["data"]["url"]
                log.warning("imgbb error: %s", j)
                return None
    except Exception as e:
        log.warning("imgbb upload failed: %s", e)
        return None


async def _upload_with_fallback(data: bytes, filename: str,
                                images_only: bool = False) -> str | None:
    """Пробуем хостеры по очереди. Возвращает первый успешный URL."""
    if IMGBB_KEY and images_only:
        url = await _to_imgbb(data)
        if url:
            return url
    url = await _to_catbox(data, filename)
    if url:
        return url
    return await _to_nullpointer(data, filename)


async def upload_photo(bot, token: str, file_id: str) -> str | None:
    """Фото → публичный URL. imgbb → catbox → 0x0.st."""
    data = await _download(bot, file_id)
    if not data:
        return None
    return await _upload_with_fallback(data, "photo.jpg", images_only=True)


async def upload_video(bot, token: str, file_id: str) -> str | None:
    """Видео → публичный URL. catbox → 0x0.st (imgbb видео не хостит)."""
    data = await _download(bot, file_id)
    if not data:
        return None
    return await _upload_with_fallback(data, "video.mp4", images_only=False)


async def upload_audio(bot, file_id: str, ext: str = "mp3") -> str | None:
    """Аудио → публичный URL. Расширение важно: Telegram читает MIME из URL."""
    if ext not in ("mp3", "ogg", "m4a", "wav"):
        ext = "mp3"
    data = await _download(bot, file_id)
    if not data:
        return None
    return await _upload_with_fallback(data, f"audio.{ext}", images_only=False)
