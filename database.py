"""
SQLite-слой для бота-конструктора постов (Rich Messages, Bot API 10.1).
Один долгоживущий aiosqlite.Connection на весь бот: открываем в init_db(),
закрываем в close_db(). Это убирает open-файла + PRAGMA на каждый запрос
(~5–20мс × количество запросов в хендлере).

Типы блоков:
  heading | text | list | numbered | checklist | quote | code |
  table | math | divider | pullquote | collapsible |
  photo | video | audio | collage | map
"""
import json
from pathlib import Path

import aiosqlite

DB_PATH = Path(__file__).parent / "postbot.db"

# Один глобальный коннект. aiosqlite сериализует операции на одной коннекции
# внутри своего worker-thread'а, так что конкурентные await'ы безопасны.
_db: aiosqlite.Connection | None = None

# In-memory кэш горячих чтений. Цель — снять SQLite-запросы с каждого
# нажатия кнопки в панели. Кэш read-through: при write инвалидируется.
#   _active_post: user_id -> post_id активного поста
#   _blocks:      post_id -> список блоков (как возвращает get_blocks)
_active_post: dict[int, int] = {}
_blocks: dict[int, list[dict]] = {}


def _invalidate_post(post_id: int) -> None:
    _blocks.pop(post_id, None)


def _invalidate_user(user_id: int) -> None:
    pid = _active_post.pop(user_id, None)
    if pid is not None:
        _blocks.pop(pid, None)


async def init_db() -> None:
    """Открыть коннекцию и создать таблицы."""
    global _db
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL;")
    await _db.execute("PRAGMA synchronous=NORMAL;")
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            title      TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            is_active  INTEGER DEFAULT 1
        );
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS blocks (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id   INTEGER NOT NULL,
            position  INTEGER NOT NULL,
            type      TEXT NOT NULL,
            content   TEXT DEFAULT '',
            media_id  TEXT DEFAULT '',
            extra     TEXT DEFAULT '',
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
        );
    """)
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_posts_user ON posts(user_id, is_active);")
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_blocks_post ON blocks(post_id, position);")
    await _db.commit()


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def _conn() -> aiosqlite.Connection:
    """Возвращает текущую коннекцию или падает, если init_db() не звали."""
    if _db is None:
        raise RuntimeError("database not initialized — вызови init_db() в main()")
    return _db


# ---------- ПОСТЫ ----------

async def get_or_create_active_post(user_id: int) -> int:
    cached = _active_post.get(user_id)
    if cached is not None:
        return cached
    db = _conn()
    cur = await db.execute(
        "SELECT id FROM posts WHERE user_id=? AND is_active=1 ORDER BY id DESC LIMIT 1;",
        (user_id,),
    )
    row = await cur.fetchone()
    if row:
        _active_post[user_id] = row[0]
        return row[0]
    cur = await db.execute("INSERT INTO posts (user_id) VALUES (?);", (user_id,))
    await db.commit()
    _active_post[user_id] = cur.lastrowid
    return cur.lastrowid


async def reset_post(user_id: int) -> int:
    db = _conn()
    await db.execute(
        "UPDATE posts SET is_active=0 WHERE user_id=? AND is_active=1;", (user_id,)
    )
    cur = await db.execute("INSERT INTO posts (user_id) VALUES (?);", (user_id,))
    await db.commit()
    _invalidate_user(user_id)
    _active_post[user_id] = cur.lastrowid
    return cur.lastrowid


# ---------- БЛОКИ ----------

async def add_block(post_id: int, btype: str, content: str = "",
                    media_id: str = "", extra: dict | None = None) -> int:
    db = _conn()
    extra_json = json.dumps(extra, ensure_ascii=False) if extra else ""
    cur = await db.execute(
        "SELECT COALESCE(MAX(position), 0) FROM blocks WHERE post_id=?;", (post_id,)
    )
    max_pos = (await cur.fetchone())[0]
    cur = await db.execute(
        "INSERT INTO blocks (post_id, position, type, content, media_id, extra) "
        "VALUES (?,?,?,?,?,?);",
        (post_id, max_pos + 1, btype, content, media_id, extra_json),
    )
    await db.commit()
    _invalidate_post(post_id)
    return cur.lastrowid


def _row_to_block(row) -> dict:
    b = dict(row)
    raw = b.get("extra") or ""
    try:
        b["extra"] = json.loads(raw) if raw else {}
    except Exception:
        b["extra"] = {}
    return b


async def get_blocks(post_id: int) -> list[dict]:
    cached = _blocks.get(post_id)
    if cached is not None:
        return cached
    db = _conn()
    cur = await db.execute(
        "SELECT * FROM blocks WHERE post_id=? ORDER BY position;", (post_id,)
    )
    result = [_row_to_block(r) for r in await cur.fetchall()]
    _blocks[post_id] = result
    return result


async def get_block(block_id: int) -> dict | None:
    db = _conn()
    cur = await db.execute("SELECT * FROM blocks WHERE id=?;", (block_id,))
    row = await cur.fetchone()
    return _row_to_block(row) if row else None


async def update_block(block_id: int, content: str = None,
                       media_id: str = None, extra: dict = None) -> None:
    db = _conn()
    if content is not None:
        await db.execute("UPDATE blocks SET content=? WHERE id=?;", (content, block_id))
    if media_id is not None:
        await db.execute("UPDATE blocks SET media_id=? WHERE id=?;", (media_id, block_id))
    if extra is not None:
        await db.execute(
            "UPDATE blocks SET extra=? WHERE id=?;",
            (json.dumps(extra, ensure_ascii=False), block_id),
        )
    await db.commit()
    cur = await db.execute("SELECT post_id FROM blocks WHERE id=?;", (block_id,))
    row = await cur.fetchone()
    if row:
        _invalidate_post(row["post_id"])


async def delete_block(block_id: int) -> None:
    db = _conn()
    cur = await db.execute("SELECT post_id FROM blocks WHERE id=?;", (block_id,))
    row = await cur.fetchone()
    post_id = row["post_id"] if row else None
    await db.execute("DELETE FROM blocks WHERE id=?;", (block_id,))
    await db.commit()
    if post_id is not None:
        _invalidate_post(post_id)


async def move_block(block_id: int, direction: int) -> None:
    db = _conn()
    cur = await db.execute("SELECT post_id, position FROM blocks WHERE id=?;", (block_id,))
    row = await cur.fetchone()
    if not row:
        return
    post_id, pos = row["post_id"], row["position"]
    new_pos = pos + direction
    cur = await db.execute(
        "SELECT id FROM blocks WHERE post_id=? AND position=?;", (post_id, new_pos)
    )
    neighbor = await cur.fetchone()
    if not neighbor:
        return
    await db.execute("UPDATE blocks SET position=? WHERE id=?;", (new_pos, block_id))
    await db.execute("UPDATE blocks SET position=? WHERE id=?;", (pos, neighbor["id"]))
    await db.commit()
    _invalidate_post(post_id)
