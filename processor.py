"""
命令处理器 - 整合插件系统、消息存储、任务调度
"""

import asyncio
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

import plugin_base
from plugin_base import CommandContext, CommandInfo
from plugin_loader import PluginLoader
from message_store import MessageStore


@dataclass
class ScheduledTask:
    task_id: str
    time_hm: str
    command_text: str
    enabled: bool = True
    description: str = ""
    last_run_date: str | None = None
    created_at: str = ""


class CommandProcessor:
    def __init__(self, bot, download_dir: str | None = None):
        # 延迟导入避免循环依赖
        from config import settings

        self.bot = bot
        self.download_dir = Path(download_dir) if download_dir else settings.download_dir
        self.started_at = time.time()
        self.server_label = settings.server_label

        # 聊天机器人配置
        self.chat_enabled = settings.chatbot_enabled
        self.chat_webhook_url = settings.chatbot_webhook_url
        self.chat_timeout = settings.chatbot_timeout

        # 定时任务
        self.task_file = settings.task_file
        self.tasks: dict[str, ScheduledTask] = {}
        self.scheduler_task: asyncio.Task | None = None

        # HTTP 白名单
        self.http_allowlist = settings.http_allowlist
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(self.chat_timeout))

        # 消息 Webhook 推送
        self.message_webhook_url = settings.message_webhook_url
        self.message_webhook_timeout = settings.message_webhook_timeout

        # 插件系统
        self.plugin_loader = PluginLoader(str(settings.plugins_dir))

        # 消息存储
        self.message_store = MessageStore(str(settings.message_db_path))

        # 确保运行时文件存在
        settings.ensure_runtime_files()
        self._load_tasks()

    async def start(self):
        # 加载插件
        self.plugin_loader.load_all()
        print(f"[Processor] Loaded {len(self.plugin_loader.loaded_plugins)} plugins")

        if not self.scheduler_task:
            self.scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def stop(self):
        if self.scheduler_task:
            self.scheduler_task.cancel()
            try:
                await self.scheduler_task
            except asyncio.CancelledError:
                pass
            self.scheduler_task = None

        self._save_tasks()
        await self.http_client.aclose()

    def get_state(self) -> dict[str, Any]:
        uptime_seconds = int(time.time() - self.started_at)
        plugin_status = self.plugin_loader.get_status()
        store_stats = self.message_store.get_stats()

        return {
            "server_label": self.server_label,
            "chat_enabled": self.chat_enabled,
            "chat_webhook_enabled": bool(self.chat_webhook_url),
            "message_webhook_enabled": bool(self.message_webhook_url),
            "uptime_seconds": uptime_seconds,
            "task_count": len(self.tasks),
            "enabled_task_count": len([task for task in self.tasks.values() if task.enabled]),
            "plugins": plugin_status,
            "message_store": store_stats,
        }

    def list_tasks(self) -> list[dict[str, Any]]:
        return [asdict(task) for task in sorted(self.tasks.values(), key=lambda item: (item.time_hm, item.task_id))]

    def add_task(self, time_hm: str, command_text: str, description: str = "") -> dict[str, Any]:
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", time_hm):
            raise ValueError("Invalid time format, expected HH:MM")

        task_id = f"task_{int(time.time() * 1000)}"
        task = ScheduledTask(
            task_id=task_id,
            time_hm=time_hm,
            command_text=command_text.strip(),
            enabled=True,
            description=description.strip(),
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        self.tasks[task_id] = task
        self._save_tasks()
        return asdict(task)

    def delete_task(self, task_id: str) -> bool:
        if task_id not in self.tasks:
            return False
        del self.tasks[task_id]
        self._save_tasks()
        return True

    def set_task_enabled(self, task_id: str, enabled: bool) -> bool:
        task = self.tasks.get(task_id)
        if not task:
            return False
        task.enabled = enabled
        self._save_tasks()
        return True

    async def run_task_now(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task:
            return False

        await self._run_task(task, trigger="manual")
        return True

    def set_chat_mode(self, enabled: bool):
        self.chat_enabled = bool(enabled)

    async def process(self, msg: dict) -> str | None:
        """处理收到的消息"""
        text = str(msg.get("text", "")).strip()
        msg_id = str(msg.get("id", ""))

        # 保存消息到数据库
        self._save_message_to_store(msg)

        # 推送到 Webhook
        await self._push_to_webhook(msg)

        if not text:
            return None

        if text.lower() == "#ping#":
            return "Pong!"

        return await self._dispatch_text(text, msg)

    async def execute_command_text(self, text: str, source: str = "api") -> str | None:
        return await self._dispatch_text(text=text, msg={"id": source}, allow_chat=True)

    async def _dispatch_text(self, text: str, msg: dict | None = None, allow_chat: bool = True) -> str | None:
        raw = text.strip()
        if not raw:
            return None

        is_command = raw.startswith("/")
        if is_command:
            raw = raw[1:].strip()

        if not raw:
            return None

        parts = raw.split()
        cmd = parts[0].lower()
        args = parts[1:]

        # 构建上下文
        ctx = CommandContext(
            text=text,
            command=cmd,
            args=args,
            msg=msg or {},
            msg_id=str((msg or {}).get("id", "")),
            is_command=is_command,
            bot=self.bot,
            processor=self,
            reply_to=str((msg or {}).get("reply_to_id", "")) or None,
        )

        # 先执行消息处理器
        for handler_info in plugin_base.get_message_handlers():
            try:
                result = await handler_info.handler(ctx)
                if result is not None:
                    return result
            except Exception as exc:
                print(f"[Processor] Message handler {handler_info.name} error: {exc}")

        # 查找命令
        commands = plugin_base.get_registered_commands()
        if cmd in commands:
            try:
                return await commands[cmd].handler(ctx)
            except Exception as exc:
                print(f"[Processor] Command {cmd} error: {exc}")
                return f"命令执行出错: {exc}"

        # 未知命令反馈 (类似 Telegram)
        if is_command:
            return f"未知命令: /{cmd}\n输入 /help 查看可用命令。"

        # 聊天模式
        if allow_chat and self.chat_enabled:
            return await self._chat_reply(text=raw, source_msg=msg or {})

        return None

    def _save_message_to_store(self, msg: dict):
        """保存消息到持久化存储"""
        msg_id = str(msg.get("id", ""))
        if not msg_id:
            return

        msg_type = msg.get("type", "text")
        text = msg.get("text", "")
        is_mine = msg.get("is_mine", False)
        file_name = msg.get("file_name")
        file_path = msg.get("file_path")
        file_size = msg.get("file_size")
        reply_to_id = msg.get("reply_to_id")

        try:
            self.message_store.save_message(
                msg_id=msg_id,
                msg_type=msg_type,
                text=text,
                is_mine=is_mine,
                file_name=file_name,
                file_path=file_path,
                file_size=file_size,
                reply_to_id=reply_to_id,
                raw_data=msg,
            )
        except Exception as exc:
            print(f"[Processor] Save message error: {exc}")

    async def _push_to_webhook(self, msg: dict):
        """推送消息到 Webhook"""
        if not self.message_webhook_url:
            return

        payload = {
            "update_id": self.message_store.get_max_id(),
            "message": {
                "message_id": msg.get("id"),
                "date": int(time.time()),
                "text": msg.get("text", ""),
                "type": msg.get("type", "text"),
            },
        }

        if msg.get("file_name"):
            payload["message"]["document"] = {
                "file_name": msg.get("file_name"),
                "file_path": msg.get("file_path"),
            }

        try:
            await self.http_client.post(
                self.message_webhook_url,
                json=payload,
                timeout=self.message_webhook_timeout,
            )
        except Exception as exc:
            print(f"[Processor] Webhook push error: {exc}")

    async def _chat_reply(self, text: str, source_msg: dict[str, Any]) -> str:
        if self.chat_webhook_url:
            payload = {
                "message": text,
                "from": source_msg.get("id", "filehelper"),
                "timestamp": int(time.time()),
                "server": self.server_label,
            }
            try:
                resp = await self.http_client.post(self.chat_webhook_url, json=payload)
                if resp.status_code >= 400:
                    return f"chat webhook error: status={resp.status_code}"

                content_type = resp.headers.get("content-type", "")
                if "application/json" in content_type:
                    data = resp.json()
                    if isinstance(data, dict):
                        for key in ("reply", "content", "text", "message"):
                            if data.get(key):
                                return str(data[key])
                    return json.dumps(data, ensure_ascii=False)

                return resp.text[:1800]
            except Exception as exc:
                return f"chat webhook request failed: {exc}"

        normalized = text.strip()
        if normalized in {"你好", "hello", "hi", "嗨"}:
            return "你好，我在线。你可以用 /help 查看命令。"
        if normalized.startswith("状态"):
            ctx = CommandContext(
                text=normalized,
                command="status",
                args=[],
                msg=source_msg,
                msg_id=str(source_msg.get("id", "")),
                is_command=False,
                bot=self.bot,
                processor=self,
            )
            commands = plugin_base.get_registered_commands()
            if "status" in commands:
                return await commands["status"].handler(ctx)
        return "已收到。可开启 CHATBOT_WEBHOOK_URL 接入你的服务器智能回复。"

    def _is_url_allowed(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except Exception:
            return False

        if parsed.scheme not in {"http", "https"}:
            return False

        host = (parsed.hostname or "").lower()
        if not host:
            return False

        if self.http_allowlist:
            return host in self.http_allowlist

        if host in {"localhost", "127.0.0.1"}:
            return True
        if host.endswith(".local"):
            return True
        if host.startswith("10.") or host.startswith("192.168."):
            return True
        if host.startswith("172."):
            return True

        return False

    async def _scheduler_loop(self):
        while True:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            hm = now.strftime("%H:%M")
            dirty = False

            for task in self.tasks.values():
                if not task.enabled:
                    continue
                if task.time_hm != hm:
                    continue
                if task.last_run_date == today:
                    continue

                await self._run_task(task, trigger="schedule")
                task.last_run_date = today
                dirty = True

            if dirty:
                self._save_tasks()

            await asyncio.sleep(20)

    async def _run_task(self, task: ScheduledTask, trigger: str):
        result = await self._dispatch_text(task.command_text, msg={"id": f"task:{task.task_id}"}, allow_chat=False)
        if result:
            await self.bot.send_text(f"[task:{task.task_id}:{trigger}] {result}")

    def _load_tasks(self):
        if not self.task_file.exists():
            return

        try:
            data = json.loads(self.task_file.read_text(encoding="utf-8"))
        except Exception:
            return

        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            entries = list(data.values())
        else:
            return

        for item in entries:
            try:
                task = ScheduledTask(**item)
                self.tasks[task.task_id] = task
            except Exception:
                continue

    def _save_tasks(self):
        rows = [asdict(task) for task in self.tasks.values()]
        self.task_file.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    # === Telegram 风格 API 方法 ===

    def get_updates(self, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        """获取消息更新 (Telegram getUpdates 风格)"""
        messages = self.message_store.get_updates(offset=offset, limit=limit)
        return [
            {
                "update_id": msg.id,
                "message": {
                    "message_id": msg.msg_id,
                    "date": msg.timestamp,
                    "text": msg.text,
                    "type": msg.type,
                    "is_from_bot": msg.is_mine,
                    "document": {
                        "file_name": msg.file_name,
                        "file_path": msg.file_path,
                        "file_size": msg.file_size,
                    } if msg.file_name else None,
                    "reply_to_message_id": msg.reply_to_id,
                },
            }
            for msg in messages
        ]

    async def send_message(
        self,
        text: str,
        reply_to_message_id: str | None = None,
    ) -> dict[str, Any]:
        """发送消息 (Telegram sendMessage 风格)"""
        success = await self.bot.send_text(text)

        # 保存发送的消息
        msg_id = f"sent_{int(time.time() * 1000)}"
        self.message_store.save_message(
            msg_id=msg_id,
            msg_type="text",
            text=text,
            is_mine=True,
            reply_to_id=reply_to_message_id,
        )

        return {
            "ok": success,
            "result": {
                "message_id": msg_id,
                "date": int(time.time()),
                "text": text,
                "reply_to_message_id": reply_to_message_id,
            } if success else None,
        }

    async def send_document(
        self,
        file_path: str,
        reply_to_message_id: str | None = None,
    ) -> dict[str, Any]:
        """发送文件 (Telegram sendDocument 风格)"""
        path = Path(file_path)
        if not path.exists():
            return {"ok": False, "error": "file not found"}

        success = await self.bot.send_file(str(path))

        msg_id = f"sent_{int(time.time() * 1000)}"
        if success:
            self.message_store.save_message(
                msg_id=msg_id,
                msg_type="file",
                text=f"[File: {path.name}]",
                is_mine=True,
                file_name=path.name,
                file_path=str(path),
                file_size=path.stat().st_size,
                reply_to_id=reply_to_message_id,
            )

        return {
            "ok": success,
            "result": {
                "message_id": msg_id,
                "date": int(time.time()),
                "document": {
                    "file_name": path.name,
                    "file_size": path.stat().st_size,
                },
                "reply_to_message_id": reply_to_message_id,
            } if success else None,
        }
