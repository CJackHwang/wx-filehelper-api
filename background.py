"""
后台任务模块

- 消息监听器 (动态轮询)
- 心跳监控 (自动重连)
- 会话保存
- 文件清理
"""

from __future__ import annotations

import asyncio
import mimetypes
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from direct_bot import WeChatHelperBot
    from processor import CommandProcessor


class BackgroundTasks:
    """后台任务管理器"""

    def __init__(
        self,
        bot: "WeChatHelperBot",
        processor: "CommandProcessor",
        download_dir: Path,
        stability_state: dict[str, Any],
        *,
        auto_download: bool = True,
        file_date_subdir: bool = True,
        heartbeat_interval: int = 30,
        reconnect_delay: int = 5,
        max_reconnect_attempts: int = 10,
        file_retention_days: int = 0,
    ):
        self.bot = bot
        self.processor = processor
        self.download_dir = download_dir
        self.stability_state = stability_state

        # 配置
        self.auto_download = auto_download
        self.file_date_subdir = file_date_subdir
        self.heartbeat_interval = heartbeat_interval
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_attempts = max_reconnect_attempts
        self.file_retention_days = file_retention_days

        # 任务句柄
        self._listener_task: asyncio.Task | None = None
        self._session_saver_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None

    def start_all(self) -> None:
        """启动所有后台任务"""
        self._listener_task = asyncio.create_task(self._background_listener())
        self._session_saver_task = asyncio.create_task(self._periodic_session_saver())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_monitor())

        if self.file_retention_days > 0:
            self._cleanup_task = asyncio.create_task(self._file_cleanup_task())

    async def stop_all(self) -> None:
        """停止所有后台任务"""
        tasks = [
            self._listener_task,
            self._session_saver_task,
            self._heartbeat_task,
            self._cleanup_task,
        ]
        for task in tasks:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    def _get_file_save_path(self, file_name: str) -> Path:
        """获取文件保存路径 (支持按日期分目录)"""
        if self.file_date_subdir:
            date_dir = datetime.now().strftime("%Y-%m-%d")
            target_dir = self.download_dir / date_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            return target_dir / file_name
        return self.download_dir / file_name

    def _add_error(self, error: str) -> None:
        """记录错误 (保留最近20条)"""
        self.stability_state["errors"].append({
            "time": datetime.now().isoformat(),
            "error": error,
        })
        if len(self.stability_state["errors"]) > 20:
            self.stability_state["errors"] = self.stability_state["errors"][-20:]

    async def _background_listener(self) -> None:
        """消息监听器 - 带自动重连和动态轮询间隔"""
        # 使用 deque 替代 list，pop(0) 复杂度从 O(n) 变为 O(1)
        processed_order: deque[str] = deque(maxlen=5000)
        processed_set: set[str] = set()
        sent_buffer: deque[str] = deque(maxlen=40)

        # 动态轮询间隔
        poll_interval = 1.0
        min_interval = 0.5
        max_interval = 3.0

        print("[Listener] Started")

        while True:
            try:
                had_messages = False

                if not self.bot.is_logged_in:
                    await self.bot.check_login_status(poll=True)
                    if self.bot.is_logged_in:
                        self.stability_state["reconnect_attempts"] = 0
                        print("[Listener] Login restored")

                if self.bot.is_logged_in:
                    messages = await self.bot.get_latest_messages(limit=12)

                    for msg in reversed(messages):
                        content = str(msg.get("text", "")).strip()
                        msg_id = str(msg.get("id", "")).strip()
                        unique_key = msg_id or content

                        if not unique_key:
                            continue
                        if unique_key in processed_set:
                            continue

                        processed_set.add(unique_key)
                        processed_order.append(unique_key)

                        if content and content in sent_buffer:
                            continue

                        had_messages = True

                        # 自动下载文件
                        if self.auto_download and msg.get("type") in {"image", "file"}:
                            await self._handle_file_download(msg, msg_id, unique_key, len(processed_order))

                        # 处理消息
                        reply = await self.processor.process(msg)
                        if reply:
                            ok = await self.bot.send_text(reply)
                            if ok:
                                sent_buffer.append(reply)

                        self.stability_state["last_message_time"] = time.time()
                        self.stability_state["total_messages"] += 1

                    # 同步清理 processed_set (deque 满时旧元素被移除)
                    if len(processed_set) > len(processed_order) + 100:
                        processed_set = set(processed_order)

                # 动态调整轮询间隔
                if had_messages:
                    poll_interval = min_interval
                else:
                    poll_interval = min(poll_interval * 1.2, max_interval)

            except Exception as exc:
                error_msg = f"Listener error: {exc}"
                print(f"[Listener] {error_msg}")
                self._add_error(error_msg)
                poll_interval = max_interval

            await asyncio.sleep(poll_interval)

    async def _handle_file_download(
        self, msg: dict, msg_id: str, unique_key: str, order_len: int
    ) -> None:
        """处理文件下载"""
        file_name = msg.get("file_name") or f"download_{msg_id[:8] or order_len}"
        if msg.get("type") == "image" and "." not in file_name:
            file_name += ".jpg"

        save_path = self._get_file_save_path(file_name)
        success = await self.bot.download_message_content(msg_id or unique_key, str(save_path))

        if success:
            # 更新消息中的文件路径
            msg["file_path"] = str(save_path)
            msg["file_size"] = save_path.stat().st_size if save_path.exists() else 0

            # 保存文件元数据
            mime_type, _ = mimetypes.guess_type(file_name)
            self.processor.message_store.save_file(
                msg_id=msg_id,
                file_name=file_name,
                file_path=str(save_path),
                file_size=msg.get("file_size", 0),
                mime_type=mime_type,
            )

    async def _periodic_session_saver(self) -> None:
        """定期保存会话"""
        while True:
            await asyncio.sleep(60)
            try:
                if self.bot.is_logged_in:
                    await self.bot.save_session()
            except Exception as exc:
                print(f"[SessionSaver] Error: {exc}")

    async def _heartbeat_monitor(self) -> None:
        """心跳监控 - 检测掉线并触发重连"""
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                self.stability_state["last_heartbeat"] = time.time()

                if self.bot.is_logged_in:
                    # 检查连接状态
                    status = await self.bot._synccheck()
                    if status == "loginout":
                        print("[Heartbeat] Detected logout, will reconnect")
                        self.bot.is_logged_in = False
                        self.stability_state["reconnect_attempts"] += 1

                        if self.stability_state["reconnect_attempts"] <= self.max_reconnect_attempts:
                            await asyncio.sleep(self.reconnect_delay)
                            # 尝试使用已保存的会话重新登录
                            await self.bot._load_session()
                            await self.bot.check_login_status(poll=True)
                        else:
                            self._add_error(
                                f"Max reconnect attempts ({self.max_reconnect_attempts}) reached"
                            )

            except Exception as exc:
                self._add_error(f"Heartbeat error: {exc}")

    async def _file_cleanup_task(self) -> None:
        """定期清理过期文件"""
        while True:
            await asyncio.sleep(3600)  # 每小时检查一次
            try:
                deleted_count = self.processor.message_store.cleanup_old_files(
                    days=self.file_retention_days,
                    delete_files=True,
                )
                if deleted_count > 0:
                    print(f"[Cleanup] Deleted {deleted_count} old files")
            except Exception as exc:
                self._add_error(f"Cleanup error: {exc}")
