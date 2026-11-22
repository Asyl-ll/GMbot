import os
from datetime import date

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ["DATABASE_URL"]


def _connect():
    # prepare_threshold=None: совместимо с пулерами соединений (Neon, Supabase)
    return psycopg.AsyncConnection.connect(
        DATABASE_URL, autocommit=True, row_factory=dict_row, prepare_threshold=None
    )


async def init_db() -> None:
    async with await _connect() as c:
        await c.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                due DATE NOT NULL,
                text TEXT NOT NULL,
                done BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )"""
        )
        await c.execute(
            "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )


async def add_task(due: date, text: str) -> int:
    async with await _connect() as c:
        cur = await c.execute(
            "INSERT INTO tasks (due, text) VALUES (%s, %s) RETURNING id", (due, text)
        )
        row = await cur.fetchone()
        return row["id"]


async def tasks_on(d: date) -> list[dict]:
    async with await _connect() as c:
        cur = await c.execute(
            "SELECT id, due, text FROM tasks WHERE due = %s AND NOT done ORDER BY id", (d,)
        )
        return await cur.fetchall()


async def overdue(today: date) -> list[dict]:
    async with await _connect() as c:
        cur = await c.execute(
            "SELECT id, due, text FROM tasks WHERE due < %s AND NOT done ORDER BY due, id",
            (today,),
        )
        return await cur.fetchall()


async def between(start: date, end: date) -> list[dict]:
    async with await _connect() as c:
        cur = await c.execute(
            "SELECT id, due, text FROM tasks WHERE due >= %s AND due <= %s AND NOT done "
            "ORDER BY due, id",
            (start, end),
        )
        return await cur.fetchall()


async def open_tasks(limit: int = 50) -> list[dict]:
    async with await _connect() as c:
        cur = await c.execute(
            "SELECT id, due, text FROM tasks WHERE NOT done ORDER BY due, id LIMIT %s", (limit,)
        )
        return await cur.fetchall()


async def finish(task_id: int) -> bool:
    async with await _connect() as c:
        cur = await c.execute(
            "UPDATE tasks SET done = TRUE WHERE id = %s AND NOT done", (task_id,)
        )
        return cur.rowcount > 0


async def remove(task_id: int) -> bool:
    async with await _connect() as c:
        cur = await c.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
        return cur.rowcount > 0


async def claim_digest(day_iso: str) -> bool:
    """True, если сводка за этот день ещё не отправлялась (и мы её «забронировали»)."""
    async with await _connect() as c:
        cur = await c.execute(
            """INSERT INTO kv (key, value) VALUES ('last_digest', %s)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
               WHERE kv.value <> EXCLUDED.value
               RETURNING key""",
            (day_iso,),
        )
        return await cur.fetchone() is not None


async def release_digest() -> None:
    async with await _connect() as c:
        await c.execute("UPDATE kv SET value = '' WHERE key = 'last_digest'")
