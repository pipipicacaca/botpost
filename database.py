"""
SQLite-слой для бота-конструктора постов (Rich Messages, Bot API 10.1).
WAL-режим + индексы.

Типы блоков:
  heading | text | list | numbered | checklist | quote | code |
  table | math | divider | pullquote | collapsible |
  photo | video | audio | collage | map

Поля блока:
  content  — основной текст / подпись / спец-данные
  media_id — file_id одиночного медиа (фото/видео/аудио)
  extra    — JSON: уровень заголовка, язык кода, список file_id коллажа,
             координаты карты, заголовок details и т.п.
"""
import aiosqlite
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "postbot.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                title      TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                is_active  INTEGER DEFAULT 1
            );
        """)
        await db.execute("""
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
        await db.execute("CREATE INDEX IF NOT EXISTS idx_posts_user ON posts(user_id, is_active);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_blocks_post ON blocks(post_id, position);")
        await db.commit()


# ---------- ПОСТЫ ----------

async def get_or_create_active_post(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id FROM posts WHERE user_id=? AND is_active=1 ORDER BY id DESC LIMIT 1;",
            (user_id,),
        )
        row = await cur.fetchone()
        if row:
            return row[0]
        cur = await db.execute("INSERT INTO posts (user_id) VALUES (?);", (user_id,))
        await db.commit()
        return cur.lastrowid


async def reset_post(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE posts SET is_active=0 WHERE user_id=? AND is_active=1;", (user_id,)
        )
        cur = await db.execute("INSERT INTO posts (user_id) VALUES (?);", (user_id,))
        await db.commit()
        return cur.lastrowid


# ---------- БЛОКИ ----------

async def add_block(post_id: int, btype: str, content: str = "",
                    media_id: str = "", extra: dict | None = None) -> int:
    extra_json = json.dumps(extra, ensure_ascii=False) if extra else ""
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM blocks WHERE post_id=? ORDER BY position;", (post_id,)
        )
        return [_row_to_block(r) for r in await cur.fetchall()]


async def get_block(block_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM blocks WHERE id=?;", (block_id,))
        row = await cur.fetchone()
        return _row_to_block(row) if row else None


async def update_block(block_id: int, content: str = None,
                       media_id: str = None, extra: dict = None):
    async with aiosqlite.connect(DB_PATH) as db:
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


async def delete_block(block_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM blocks WHERE id=?;", (block_id,))
        await db.commit()


async def move_block(block_id: int, direction: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
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
