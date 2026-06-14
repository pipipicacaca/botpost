"""
Загрузка медиа на публичный хостинг для получения ПРЯМОГО HTTPS-URL.

Telegram при обработке sendRichMessage сам скачивает медиа по URL и кладёт
на свой CDN — далее картинка живёт в Telegram, как обычное фото. Это значит,
что временного URL на 10 минут достаточно: TG успевает забрать файл за секунды.

Поэтому мы намеренно используем КОРОТКИЙ TTL на хостинге, чтобы публичный
файл жил минимум времени и не был доступен по случайно утёкшему URL:
  • imgbb     — expiration=600 (10 минут)
  • Litterbox — time=1h        (минимально доступный тариф)
  • uguu.se   — 3 дня (нерегулируемо)

Цепочка хостеров:
  1. imgbb       — фото; самый стабильный; auto-delete 10 мин; нужен IMGBB_KEY.
  2. Litterbox   — фото/видео/аудио; auto-delete 1 час.
  3. uguu.se     — fallback; 3 дня.
  4. Catbox.moe  — последняя надежда; постоянное хранение.

Лимит Telegram getFile — 20 МБ.

env: IMGBB_KEY (опц.) — ключ с api.imgbb.com.
"""
import os
import base64
import logging
import aiohttp

log = logging.getLogger("uploader")

IMGBB_KEY = os.getenv("IMGBB_KEY", "")
CATBOX_API = "https://catbox.moe/user/api.php"
LITTERBOX_API = "https://litterbox.catbox.moe/resources/internals/api.php"
UGUU_API = "https://uguu.se/upload"
IMGBB_API = "https://api.imgbb.com/1/upload"

TIMEOUT = aiohttp.ClientTimeout(total=60)
# Многие хостеры режут пустой/«пайтоновский» UA как ботов.
UA = "Mozilla/5.0 (compatible; PostBuilder/1.0; +https://t.me/)"

# TTL для файлов на хостингах. Telegram забирает медиа в момент sendRichMessage,
# дальше URL не нужен. Минимизируем окно, в которое случайный URL остаётся живым.
IMGBB_TTL_SEC = 600   # 10 минут
LITTERBOX_TIME = "1h"  # минимально допустимое значение в API Litterbox


# ── Один глобальный ClientSession на весь бот ─────────────────────────────
# Каждый new ClientSession() = TLS handshake (~300–800мс). Переиспользуем
# одну сессию с keep-alive: на 5 фото коллажа экономим 2–4 секунды.

_session: aiohttp.ClientSession | None = None


async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            timeout=TIMEOUT,
            headers={"User-Agent": UA},
            connector=aiohttp.TCPConnector(
                limit=20,         # макс параллельных запросов
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
            ),
        )
    return _session


async def close_session() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


async def _download(bot, file_id: str) -> bytes | None:
    """Скачиваем файл из Telegram по file_id."""
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
    try:
        s = await get_session()
        form = aiohttp.FormData()
        form.add_field("reqtype", "fileupload")
        form.add_field("fileToUpload", data, filename=filename)
        async with s.post(CATBOX_API, data=form) as r:
            text = (await r.text()).strip()
            if text.startswith("http"):
                return text
            log.warning("catbox bad response: %s", text[:200])
            return None
    except Exception as e:
        log.warning("catbox upload failed: %s", e)
        return None


async def _to_litterbox(data: bytes, filename: str) -> str | None:
    """Litterbox — временный (до 72ч) хост Catbox."""
    try:
        s = await get_session()
        form = aiohttp.FormData()
        form.add_field("reqtype", "fileupload")
        form.add_field("time", LITTERBOX_TIME)
        form.add_field("fileToUpload", data, filename=filename)
        async with s.post(LITTERBOX_API, data=form) as r:
            text = (await r.text()).strip()
            if text.startswith("http"):
                return text
            log.warning("litterbox bad response: %s", text[:200])
            return None
    except Exception as e:
        log.warning("litterbox upload failed: %s", e)
        return None


async def _to_uguu(data: bytes, filename: str) -> str | None:
    """uguu.se — 3-дневное хранение."""
    try:
        s = await get_session()
        form = aiohttp.FormData()
        form.add_field("files[]", data, filename=filename)
        async with s.post(UGUU_API, data=form, headers={"Accept": "application/json"}) as r:
            try:
                j = await r.json(content_type=None)
            except Exception:
                log.warning("uguu non-json (%s): %s", r.status, (await r.text())[:200])
                return None
            if j.get("success") and j.get("files"):
                return j["files"][0].get("url")
            log.warning("uguu bad response: %s", str(j)[:200])
            return None
    except Exception as e:
        log.warning("uguu upload failed: %s", e)
        return None


async def _to_imgbb(data: bytes) -> str | None:
    """imgbb — только изображения. expiration → auto-delete."""
    try:
        s = await get_session()
        b64 = base64.b64encode(data).decode()
        form = aiohttp.FormData()
        form.add_field("image", b64)
        url = f"{IMGBB_API}?key={IMGBB_KEY}&expiration={IMGBB_TTL_SEC}"
        async with s.post(url, data=form) as r:
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
    # Порядок: сначала самый короткоживущий и проверенный хост, затем fallback'и.
    # Catbox — последний на случай, если когда-нибудь починят анти-абуз cloud IP.
    for fn in (_to_litterbox, _to_uguu, _to_catbox):
        url = await fn(data, filename)
        if url:
            log.info("uploaded via %s: %s", fn.__name__, url)
            return url
    log.error("ВСЕ хосты упали для %s — проверь IMGBB_KEY или сеть", filename)
    return None


async def upload_photo(bot, token: str, file_id: str) -> str | None:
    data = await _download(bot, file_id)
    if not data:
        return None
    return await _upload_with_fallback(data, "photo.jpg", images_only=True)


async def upload_video(bot, token: str, file_id: str) -> str | None:
    data = await _download(bot, file_id)
    if not data:
        return None
    return await _upload_with_fallback(data, "video.mp4", images_only=False)


async def upload_audio(bot, file_id: str, ext: str = "mp3") -> str | None:
    if ext not in ("mp3", "ogg", "m4a", "wav"):
        ext = "mp3"
    data = await _download(bot, file_id)
    if not data:
        return None
    return await _upload_with_fallback(data, f"audio.{ext}", images_only=False)
