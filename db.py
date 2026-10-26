"""
Хранилище бота — маленькая база данных SQLite (один файл bot_data.db).

Здесь лежат: тексты постов и черновиков, реакции, комментарии, ваши идеи
и счётчик расходов на ИИ за каждый день.
"""
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import config


@contextmanager
def _подключение():
    """Открывает базу, сохраняет изменения и закрывает."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _сейчас() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def создать_таблицы() -> None:
    """Создаёт таблицы при первом запуске (если они уже есть — ничего не меняет)."""
    with _подключение() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_msg_id INTEGER UNIQUE,      -- номер сообщения в канале (после публикации)
                text TEXT NOT NULL,
                status TEXT NOT NULL,               -- draft / scheduled / published / rejected
                source TEXT NOT NULL,               -- bot (написал бот) / manual (вы сами)
                draft_chat_msg_id INTEGER,          -- сообщение с черновиком в личке
                created_at TEXT NOT NULL,
                published_at TEXT
            );
            CREATE TABLE IF NOT EXISTS reactions (
                channel_msg_id INTEGER NOT NULL,
                emoji TEXT NOT NULL,
                count INTEGER NOT NULL,
                PRIMARY KEY (channel_msg_id, emoji)
            );
            CREATE TABLE IF NOT EXISTS thread_roots (   -- пост канала, пересланный в чат обсуждения
                group_msg_id INTEGER PRIMARY KEY,
                channel_msg_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_msg_id INTEGER NOT NULL,
                group_chat_id INTEGER NOT NULL,
                group_msg_id INTEGER NOT NULL UNIQUE,
                author TEXT,
                text TEXT NOT NULL,
                suggestion TEXT,                    -- предложенный ботом ответ
                status TEXT NOT NULL DEFAULT 'new', -- new / replied / skipped
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ideas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS state (          -- служебные отметки (например, когда был последний отчёт)
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage (
                day TEXT PRIMARY KEY,
                requests INTEGER NOT NULL DEFAULT 0,
                cost REAL NOT NULL DEFAULT 0
            );
            """
        )
        # Обновление старой базы: колонка «когда опубликовать» для отложенных постов.
        колонки = [r["name"] for r in c.execute("PRAGMA table_info(posts)")]
        if "scheduled_at" not in колонки:
            c.execute("ALTER TABLE posts ADD COLUMN scheduled_at TEXT")


# ---------- Посты ----------

def добавить_черновик(текст: str) -> int:
    with _подключение() as c:
        cur = c.execute(
            "INSERT INTO posts (text, status, source, created_at) VALUES (?, 'draft', 'bot', ?)",
            (текст, _сейчас()),
        )
        return cur.lastrowid


def получить_пост(post_id: int):
    with _подключение() as c:
        return c.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()


def пост_по_сообщению_черновика(draft_chat_msg_id: int):
    with _подключение() as c:
        return c.execute(
            "SELECT * FROM posts WHERE draft_chat_msg_id = ? AND status = 'draft'",
            (draft_chat_msg_id,),
        ).fetchone()


def пост_по_сообщению_канала(channel_msg_id: int):
    with _подключение() as c:
        return c.execute(
            "SELECT * FROM posts WHERE channel_msg_id = ?", (channel_msg_id,)
        ).fetchone()


def обновить_текст(post_id: int, текст: str) -> None:
    with _подключение() as c:
        c.execute("UPDATE posts SET text = ? WHERE id = ?", (текст, post_id))


def запомнить_сообщение_черновика(post_id: int, msg_id: int) -> None:
    with _подключение() as c:
        c.execute("UPDATE posts SET draft_chat_msg_id = ? WHERE id = ?", (msg_id, post_id))


def отметить_опубликованным(post_id: int, channel_msg_id: int) -> None:
    with _подключение() as c:
        c.execute(
            "UPDATE posts SET status='published', channel_msg_id=?, published_at=? WHERE id=?",
            (channel_msg_id, _сейчас(), post_id),
        )


def запланировать(post_id: int, когда: datetime) -> None:
    """Отложенная публикация: пост уйдёт в канал в указанное время."""
    with _подключение() as c:
        c.execute(
            "UPDATE posts SET status='scheduled', scheduled_at=? WHERE id=?",
            (когда.astimezone(timezone.utc).isoformat(timespec="seconds"), post_id),
        )


def снять_с_расписания(post_id: int) -> None:
    """Отменить отложенную публикацию — пост снова становится черновиком."""
    with _подключение() as c:
        c.execute("UPDATE posts SET status='draft', scheduled_at=NULL WHERE id=?", (post_id,))


def запланированные() -> list:
    with _подключение() as c:
        return c.execute(
            "SELECT * FROM posts WHERE status='scheduled' ORDER BY scheduled_at"
        ).fetchall()


def пора_публиковать() -> list:
    """Запланированные посты, время которых уже наступило."""
    with _подключение() as c:
        return c.execute(
            "SELECT * FROM posts WHERE status='scheduled' AND scheduled_at <= ? ORDER BY scheduled_at",
            (_сейчас(),),
        ).fetchall()


def отметить_отклонённым(post_id: int) -> None:
    with _подключение() as c:
        c.execute("UPDATE posts SET status='rejected' WHERE id=?", (post_id,))


def записать_пост_из_канала(channel_msg_id: int, текст: str) -> None:
    """Пост, который вы выложили в канал сами (не через бота) — тоже учитываем в отчётах."""
    with _подключение() as c:
        c.execute(
            "INSERT OR IGNORE INTO posts (channel_msg_id, text, status, source, created_at, published_at)"
            " VALUES (?, ?, 'published', 'manual', ?, ?)",
            (channel_msg_id, текст, _сейчас(), _сейчас()),
        )


def последние_опубликованные(сколько: int = 8) -> list:
    with _подключение() as c:
        return c.execute(
            "SELECT * FROM posts WHERE status='published' ORDER BY published_at DESC LIMIT ?",
            (сколько,),
        ).fetchall()


# ---------- Реакции и комментарии ----------

def сохранить_реакции(channel_msg_id: int, реакции: dict[str, int]) -> None:
    """Telegram присылает полную картину по посту, поэтому старые строки заменяем."""
    with _подключение() as c:
        c.execute("DELETE FROM reactions WHERE channel_msg_id = ?", (channel_msg_id,))
        c.executemany(
            "INSERT INTO reactions (channel_msg_id, emoji, count) VALUES (?, ?, ?)",
            [(channel_msg_id, e, n) for e, n in реакции.items() if n > 0],
        )


def реакции_поста(channel_msg_id: int) -> dict[str, int]:
    with _подключение() as c:
        строки = c.execute(
            "SELECT emoji, count FROM reactions WHERE channel_msg_id = ?", (channel_msg_id,)
        ).fetchall()
    return {r["emoji"]: r["count"] for r in строки}


def запомнить_корень_обсуждения(group_msg_id: int, channel_msg_id: int) -> None:
    with _подключение() as c:
        c.execute(
            "INSERT OR REPLACE INTO thread_roots (group_msg_id, channel_msg_id) VALUES (?, ?)",
            (group_msg_id, channel_msg_id),
        )


def пост_канала_для_сообщения_чата(group_msg_id: int):
    """По номеру сообщения в чате обсуждения находим, к какому посту канала оно относится."""
    with _подключение() as c:
        r = c.execute(
            "SELECT channel_msg_id FROM thread_roots WHERE group_msg_id = ?", (group_msg_id,)
        ).fetchone()
        if r:
            return r["channel_msg_id"]
        r = c.execute(
            "SELECT channel_msg_id FROM comments WHERE group_msg_id = ?", (group_msg_id,)
        ).fetchone()
        return r["channel_msg_id"] if r else None


def добавить_комментарий(channel_msg_id, group_chat_id, group_msg_id, автор, текст):
    """Возвращает id комментария или None, если такой уже записан."""
    with _подключение() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO comments"
            " (channel_msg_id, group_chat_id, group_msg_id, author, text, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (channel_msg_id, group_chat_id, group_msg_id, автор, текст, _сейчас()),
        )
        return cur.lastrowid if cur.rowcount else None


def получить_комментарий(comment_id: int):
    with _подключение() as c:
        return c.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()


def обновить_комментарий(comment_id: int, *, suggestion=None, status=None) -> None:
    with _подключение() as c:
        if suggestion is not None:
            c.execute("UPDATE comments SET suggestion = ? WHERE id = ?", (suggestion, comment_id))
        if status is not None:
            c.execute("UPDATE comments SET status = ? WHERE id = ?", (status, comment_id))


def комментарии_поста(channel_msg_id: int, сколько: int = 10) -> list:
    with _подключение() as c:
        return c.execute(
            "SELECT * FROM comments WHERE channel_msg_id = ? ORDER BY id LIMIT ?",
            (channel_msg_id, сколько),
        ).fetchall()


def все_опубликованные() -> list:
    """Все опубликованные посты, от старых к новым (для отчёта за всё время)."""
    with _подключение() as c:
        return c.execute(
            "SELECT * FROM posts WHERE status='published' ORDER BY published_at"
        ).fetchall()


def посты_за_дни(дней: int) -> list:
    """Опубликованные посты за последние N дней (для отчётов)."""
    граница = (datetime.now(timezone.utc) - timedelta(days=дней)).isoformat(timespec="seconds")
    with _подключение() as c:
        return c.execute(
            "SELECT * FROM posts WHERE status='published' AND published_at >= ? ORDER BY published_at",
            (граница,),
        ).fetchall()


# ---------- Идеи ----------

def добавить_идею(текст: str) -> int:
    with _подключение() as c:
        return c.execute(
            "INSERT INTO ideas (text, created_at) VALUES (?, ?)", (текст, _сейчас())
        ).lastrowid


def неиспользованные_идеи() -> list:
    with _подключение() as c:
        return c.execute("SELECT * FROM ideas WHERE used = 0 ORDER BY id").fetchall()


def взять_идею():
    """Берёт самую старую неиспользованную идею и помечает её использованной."""
    with _подключение() as c:
        r = c.execute("SELECT * FROM ideas WHERE used = 0 ORDER BY id LIMIT 1").fetchone()
        if r:
            c.execute("UPDATE ideas SET used = 1 WHERE id = ?", (r["id"],))
        return r


# ---------- Расходы на ИИ ----------

def расход_за_сегодня() -> tuple[int, float]:
    """Сколько запросов и долларов потрачено сегодня."""
    with _подключение() as c:
        r = c.execute("SELECT requests, cost FROM usage WHERE day = ?", (str(date.today()),)).fetchone()
    return (r["requests"], r["cost"]) if r else (0, 0.0)


def записать_расход(доллары: float) -> None:
    with _подключение() as c:
        c.execute(
            "INSERT INTO usage (day, requests, cost) VALUES (?, 1, ?)"
            " ON CONFLICT(day) DO UPDATE SET requests = requests + 1, cost = cost + excluded.cost",
            (str(date.today()), доллары),
        )


def расход_за_30_дней() -> float:
    граница = str(date.today() - timedelta(days=30))
    with _подключение() as c:
        r = c.execute("SELECT COALESCE(SUM(cost), 0) AS s FROM usage WHERE day >= ?", (граница,)).fetchone()
    return r["s"]


# ---------- Служебные отметки (чтобы бот мог «догонять» пропущенное) ----------

def отметка(ключ: str) -> datetime | None:
    """Когда в последний раз было событие с этим именем (или None)."""
    with _подключение() as c:
        r = c.execute("SELECT value FROM state WHERE key = ?", (ключ,)).fetchone()
    return datetime.fromisoformat(r["value"]) if r else None


def записать_отметку(ключ: str) -> None:
    with _подключение() as c:
        c.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (ключ, _сейчас()))


def последний_черновик_бота() -> datetime | None:
    """Когда бот в последний раз создавал черновик (любой: по расписанию или по вашей команде)."""
    with _подключение() as c:
        r = c.execute("SELECT MAX(created_at) AS m FROM posts WHERE source = 'bot'").fetchone()
    return datetime.fromisoformat(r["m"]) if r and r["m"] else None
