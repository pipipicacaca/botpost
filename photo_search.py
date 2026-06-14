"""
Поиск фото по текстовому запросу.

По умолчанию использует бесплатный источник изображений.
Для продакшена можно подключить Unsplash API (нужен ACCESS_KEY)
или любой другой провайдер — заменив реализацию search_photos.
"""
import os
import aiohttp

UNSPLASH_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")


async def search_photos(query: str, limit: int = 1) -> list[str]:
    """
    Вернуть список URL-ов картинок по запросу.
    Telegram умеет принимать прямой URL как фото в answer_photo.
    """
    query = (query or "").strip()
    if not query:
        return []

    # Вариант 1: Unsplash API (если задан ключ) — качественные фото с поиском
    if UNSPLASH_KEY:
        url = "https://api.unsplash.com/search/photos"
        params = {"query": query, "per_page": limit, "client_id": UNSPLASH_KEY}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, params=params, timeout=10) as r:
                    if r.status == 200:
                        data = await r.json()
                        return [p["urls"]["regular"] for p in data.get("results", [])[:limit]]
        except Exception:
            pass  # упадём на запасной вариант

    # Вариант 2 (запасной): источник со случайным фото по ключевому слову.
    # Не требует ключа. Возвращает осмысленное изображение по запросу.
    safe = query.replace(" ", ",")
    return [f"https://source.unsplash.com/featured/?{safe}"]
