"""
消息存储模块 - SQLite 持久化消息历史

功能:
- 消息持久化存储
- 按 message_id 快速查询
- 支持 offset/limit 分页 (Telegram getUpdates 风格)
- 文件元数据管理
"""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


@dataclass
class StoredMessage:
    """存储的消息"""
    id: int                           # 自增ID (用于 offset)
    msg_id: str                       # 微信消息ID
    type: str                         # text, image, file
    text: str                         # 消息文本/描述
    is_mine: bool                     # 是否为机器人发送
    timestamp: int                    # Unix 时间戳
    file_name: str | None = None      # 文件名
    file_path: str | None = None      # 本地文件路径
    file_size: int | None = None      # 文件大小
    reply_to_id: str | None = None    # 回复的消息ID
    raw_data: str | None = None       # 原始数据JSON
    extra: str | None = None          # 扩展数据JSON


@dataclass
class StoredFile:
    """存储的文件元数据"""
    id: int
    msg_id: str                       # 关联的消息ID
    file_name: str
    file_path: str
    file_size: int
    mime_type: str | None
    md5: str | None
    created_at: int
    downloaded: bool = True


class MessageStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or os.path.join(os.getcwd(), "messages.db"))
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    msg_id TEXT UNIQUE NOT NULL,
                    type TEXT NOT NULL,
                    text TEXT,
                    is_mine INTEGER DEFAULT 0,
                    timestamp INTEGER NOT NULL,
                    file_name TEXT,
                    file_path TEXT,
                    file_size INTEGER,
                    reply_to_id TEXT,
                    raw_data TEXT,
                    extra TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_messages_msg_id ON messages(msg_id);
                CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
                CREATE INDEX IF NOT EXISTS idx_messages_type ON messages(type);

                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    msg_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_size INTEGER,
                    mime_type TEXT,
                    md5 TEXT,
                    created_at INTEGER NOT NULL,
                    downloaded INTEGER DEFAULT 1,
                    FOREIGN KEY (msg_id) REFERENCES messages(msg_id)
                );

                CREATE INDEX IF NOT EXISTS idx_files_msg_id ON files(msg_id);
                CREATE INDEX IF NOT EXISTS idx_files_created_at ON files(created_at);

                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at INTEGER
                );
            """)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save_message(
        self,
        msg_id: str,
        msg_type: str,
        text: str,
        is_mine: bool = False,
        timestamp: int | None = None,
        file_name: str | None = None,
        file_path: str | None = None,
        file_size: int | None = None,
        reply_to_id: str | None = None,
        raw_data: dict | None = None,
        extra: dict | None = None,
    ) -> int:
        """保存消息，返回自增ID"""
        ts = timestamp or int(time.time())
        raw_json = json.dumps(raw_data, ensure_ascii=False) if raw_data else None
        extra_json = json.dumps(extra, ensure_ascii=False) if extra else None

        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT OR REPLACE INTO messages
                (msg_id, type, text, is_mine, timestamp, file_name, file_path, file_size, reply_to_id, raw_data, extra)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (msg_id, msg_type, text, int(is_mine), ts, file_name, file_path, file_size, reply_to_id, raw_json, extra_json)
            )
            return cursor.lastrowid or 0

    def get_message(self, msg_id: str) -> StoredMessage | None:
        """按消息ID查询"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE msg_id = ?", (msg_id,)
            ).fetchone()
            return self._row_to_message(row) if row else None

    def get_message_by_id(self, id: int) -> StoredMessage | None:
        """按自增ID查询"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (id,)
            ).fetchone()
            return self._row_to_message(row) if row else None

    def get_updates(
        self,
        offset: int = 0,
        limit: int = 100,
        msg_type: str | None = None,
        since: int | None = None,
    ) -> list[StoredMessage]:
        """
        获取消息更新 (Telegram getUpdates 风格)

        Args:
            offset: 从此 ID 开始 (不包含)
            limit: 最大返回数量
            msg_type: 过滤消息类型
            since: 过滤时间戳 (Unix)
        """
        conditions = ["id > ?"]
        params: list[Any] = [offset]

        if msg_type:
            conditions.append("type = ?")
            params.append(msg_type)

        if since:
            conditions.append("timestamp >= ?")
            params.append(since)

        where = " AND ".join(conditions)
        params.append(min(limit, 1000))

        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM messages WHERE {where} ORDER BY id ASC LIMIT ?",
                params
            ).fetchall()
            return [self._row_to_message(row) for row in rows]

    def get_latest(self, limit: int = 50) -> list[StoredMessage]:
        """获取最新消息"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM messages ORDER BY id DESC LIMIT ?",
                (min(limit, 1000),)
            ).fetchall()
            return [self._row_to_message(row) for row in reversed(rows)]

    def get_max_id(self) -> int:
        """获取最大消息ID"""
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(id) as max_id FROM messages").fetchone()
            return row["max_id"] or 0 if row else 0

    def count(self, since: int | None = None) -> int:
        """统计消息数量"""
        with self._conn() as conn:
            if since:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM messages WHERE timestamp >= ?", (since,)
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) as cnt FROM messages").fetchone()
            return row["cnt"] if row else 0

    def save_file(
        self,
        msg_id: str,
        file_name: str,
        file_path: str,
        file_size: int = 0,
        mime_type: str | None = None,
        md5: str | None = None,
        downloaded: bool = True,
    ) -> int:
        """保存文件元数据"""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO files (msg_id, file_name, file_path, file_size, mime_type, md5, created_at, downloaded)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (msg_id, file_name, file_path, file_size, mime_type, md5, int(time.time()), int(downloaded))
            )
            return cursor.lastrowid or 0

    def get_files(self, limit: int = 100, offset: int = 0) -> list[StoredFile]:
        """获取文件列表"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM files ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
            return [self._row_to_file(row) for row in rows]

    def get_file_by_msg_id(self, msg_id: str) -> StoredFile | None:
        """按消息ID获取文件"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM files WHERE msg_id = ?", (msg_id,)
            ).fetchone()
            return self._row_to_file(row) if row else None

    def set_kv(self, key: str, value: Any):
        """设置键值"""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_store (key, value, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value, ensure_ascii=False), int(time.time()))
            )

    def get_kv(self, key: str, default: Any = None) -> Any:
        """获取键值"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key = ?", (key,)
            ).fetchone()
            if row:
                try:
                    return json.loads(row["value"])
                except Exception:
                    return row["value"]
            return default

    def cleanup_old_messages(self, days: int = 30) -> int:
        """清理旧消息"""
        cutoff = int(time.time()) - days * 86400
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM messages WHERE timestamp < ?", (cutoff,)
            )
            return cursor.rowcount

    def cleanup_old_files(self, days: int = 30, delete_files: bool = False) -> int:
        """清理旧文件记录"""
        cutoff = int(time.time()) - days * 86400
        deleted = 0

        with self._conn() as conn:
            if delete_files:
                rows = conn.execute(
                    "SELECT file_path FROM files WHERE created_at < ?", (cutoff,)
                ).fetchall()
                for row in rows:
                    try:
                        path = Path(row["file_path"])
                        if path.exists():
                            path.unlink()
                    except Exception:
                        pass

            cursor = conn.execute(
                "DELETE FROM files WHERE created_at < ?", (cutoff,)
            )
            deleted = cursor.rowcount

        return deleted

    def _row_to_message(self, row: sqlite3.Row) -> StoredMessage:
        return StoredMessage(
            id=row["id"],
            msg_id=row["msg_id"],
            type=row["type"],
            text=row["text"] or "",
            is_mine=bool(row["is_mine"]),
            timestamp=row["timestamp"],
            file_name=row["file_name"],
            file_path=row["file_path"],
            file_size=row["file_size"],
            reply_to_id=row["reply_to_id"],
            raw_data=row["raw_data"],
            extra=row["extra"],
        )

    def _row_to_file(self, row: sqlite3.Row) -> StoredFile:
        return StoredFile(
            id=row["id"],
            msg_id=row["msg_id"],
            file_name=row["file_name"],
            file_path=row["file_path"],
            file_size=row["file_size"] or 0,
            mime_type=row["mime_type"],
            md5=row["md5"],
            created_at=row["created_at"],
            downloaded=bool(row["downloaded"]),
        )

    def get_stats(self) -> dict[str, Any]:
        """获取存储统计"""
        with self._conn() as conn:
            msg_count = conn.execute("SELECT COUNT(*) as cnt FROM messages").fetchone()["cnt"]
            file_count = conn.execute("SELECT COUNT(*) as cnt FROM files").fetchone()["cnt"]
            max_id = conn.execute("SELECT MAX(id) as max_id FROM messages").fetchone()["max_id"] or 0

            today = int(datetime.now().replace(hour=0, minute=0, second=0).timestamp())
            today_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE timestamp >= ?", (today,)
            ).fetchone()["cnt"]

        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0

        return {
            "db_path": str(self.db_path),
            "db_size_bytes": db_size,
            "message_count": msg_count,
            "file_count": file_count,
            "max_update_id": max_id,
            "today_message_count": today_count,
        }
