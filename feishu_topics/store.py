"""Bounded observed history and durable topic routes, separated by bot and chat."""

import asyncio
import json
import time
from pathlib import Path

import aiosqlite

from .routing import Topic


class Store:
    def __init__(self, path: Path, history_limit: int = 500, retention_days: int = 7):
        self.path = path
        self.history_limit = history_limit
        self.retention_days = retention_days
        self.lock = asyncio.Lock()
        self.db = None

    async def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS topics (
                scope TEXT NOT NULL, chat TEXT NOT NULL, root TEXT NOT NULL,
                thread TEXT NOT NULL DEFAULT '', title TEXT NOT NULL,
                updated REAL NOT NULL, PRIMARY KEY (scope, chat, root)
            );
            CREATE INDEX IF NOT EXISTS topic_thread ON topics(scope, chat, thread);
            CREATE TABLE IF NOT EXISTS aliases (
                scope TEXT NOT NULL, chat TEXT NOT NULL, alias TEXT NOT NULL,
                root TEXT NOT NULL, PRIMARY KEY (scope, chat, alias)
            );
            CREATE TABLE IF NOT EXISTS messages (
                scope TEXT NOT NULL, chat TEXT NOT NULL, id TEXT NOT NULL,
                root TEXT NOT NULL, sender TEXT NOT NULL, name TEXT NOT NULL,
                role TEXT NOT NULL, text TEXT NOT NULL, sent REAL NOT NULL,
                observed REAL NOT NULL, PRIMARY KEY (scope, chat, id)
            );
            CREATE INDEX IF NOT EXISTS history_time ON messages(scope, chat, observed);
        """)
        await self._prune_expired()
        await self.db.commit()

    async def close(self):
        async with self.lock:
            if self.db:
                await self.db.close()
                self.db = None

    async def _prune_expired(self):
        await self.db.execute(
            "DELETE FROM messages WHERE observed < ?",
            (time.time() - self.retention_days * 86400,),
        )

    async def save_topic(self, scope: str, topic: Topic, title: str):
        async with self.lock:
            await self.db.execute(
                """INSERT INTO topics VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, chat, root) DO UPDATE SET
                thread=CASE WHEN excluded.thread<>'' THEN excluded.thread ELSE topics.thread END,
                updated=excluded.updated""",
                (scope, topic.chat_id, topic.root_id, topic.thread_id, title[:100], time.time()),
            )
            await self.db.commit()

    async def find_thread(self, scope: str, chat: str, thread: str) -> Topic | None:
        async with self.lock:
            async with self.db.execute(
                "SELECT root FROM topics WHERE scope=? AND chat=? AND thread=?",
                (scope, chat, thread),
            ) as cursor:
                row = await cursor.fetchone()
        return Topic(chat, row["root"], thread) if row else None

    async def resolve(self, scope: str, chat: str, target: str) -> Topic:
        async with self.lock:
            async with self.db.execute(
                """SELECT * FROM topics WHERE scope=? AND chat=? AND
                (root=? OR thread=? OR root=(SELECT root FROM aliases
                  WHERE scope=? AND chat=? AND alias=?)) LIMIT 1""",
                (scope, chat, target, target, scope, chat, target),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            raise ValueError(
                "当前群没有这个已记录的话题；请先在目标话题中发送 /ft_topic 或绑定别名。"
            )
        return Topic(chat, row["root"], row["thread"])

    async def bind(self, scope: str, topic: Topic, alias: str):
        if not alias or len(alias) > 40 or any(c.isspace() for c in alias):
            raise ValueError("别名须为 1–40 个字符，且不能含空格。")
        if alias.startswith(("om_", "omt_", "oc_")) or "~" in alias:
            raise ValueError("别名不能使用飞书 ID 或路由格式。")
        async with self.lock:
            async with self.db.execute(
                "SELECT root FROM aliases WHERE scope=? AND chat=? AND alias=?",
                (scope, topic.chat_id, alias),
            ) as cursor:
                row = await cursor.fetchone()
            if row and row["root"] != topic.root_id:
                raise ValueError("该别名已绑定其他话题，请使用新别名。")
            await self.db.execute(
                "INSERT OR IGNORE INTO aliases VALUES (?, ?, ?, ?)",
                (scope, topic.chat_id, alias, topic.root_id),
            )
            await self.db.commit()

    async def list_topics(self, scope: str, chat: str, limit: int = 20) -> list[dict]:
        async with self.lock:
            async with self.db.execute(
                """SELECT t.*, (SELECT group_concat(alias, ', ') FROM aliases a
                WHERE a.scope=t.scope AND a.chat=t.chat AND a.root=t.root) AS aliases
                FROM topics t WHERE scope=? AND chat=? ORDER BY updated DESC LIMIT ?""",
                (scope, chat, limit),
            ) as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    async def record(
        self, *, scope, chat, message_id, root, sender, name, role, text, sent, update=False
    ) -> bool:
        async with self.lock:
            conflict = (
                "ON CONFLICT(scope, chat, id) DO UPDATE SET text=excluded.text"
                if update
                else "ON CONFLICT DO NOTHING"
            )
            cursor = await self.db.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) " + conflict,
                (scope, chat, message_id, root, sender, name, role, text[:4000], sent, time.time()),
            )
            inserted = cursor.rowcount > 0
            await cursor.close()
            await self._prune_expired()
            await self.db.execute(
                """DELETE FROM messages WHERE scope=? AND chat=? AND id NOT IN (
                SELECT id FROM messages WHERE scope=? AND chat=?
                ORDER BY observed DESC, rowid DESC LIMIT ?)""",
                (scope, chat, scope, chat, self.history_limit),
            )
            await self.db.commit()
        return inserted

    async def context(
        self, scope: str, chat: str, *, exclude: str, limit: int, max_chars: int
    ) -> str:
        async with self.lock:
            async with self.db.execute(
                """SELECT root AS topic, name AS speaker, role, text, sent AS timestamp
                FROM messages WHERE scope=? AND chat=? AND id<>? AND observed>=?
                ORDER BY observed DESC, rowid DESC LIMIT ?""",
                (scope, chat, exclude, time.time() - self.retention_days * 86400, limit),
            ) as cursor:
                rows = [dict(r) for r in reversed(await cursor.fetchall())]
        while rows:
            payload = json.dumps(rows, ensure_ascii=False)
            if len(payload) <= max_chars:
                return payload
            rows.pop(0)
        return "[]"

    async def forget(self, scope: str, chat: str):
        async with self.lock:
            for table in ("messages", "aliases", "topics"):
                await self.db.execute(
                    f"DELETE FROM {table} WHERE scope=? AND chat=?", (scope, chat)
                )
            await self.db.commit()
