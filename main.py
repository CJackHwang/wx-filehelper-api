"""
WeChat FileHelper Protocol Bot - FastAPI 服务入口

功能:
- Telegram Bot API 兼容接口
- 插件系统
- 消息持久化
- 文件管理
- 稳定性增强
"""

from contextlib import asynccontextmanager
import asyncio
import mimetypes
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import shutil
import tempfile
import time

from fastapi import FastAPI, HTTPException, Query, Response, UploadFile, File
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import direct_bot
import processor


# === 配置 ===

DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", os.path.join(os.getcwd(), "downloads")))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 按日期分目录存储
FILE_DATE_SUBDIR = os.getenv("FILE_DATE_SUBDIR", "1").strip().lower() in {"1", "true", "yes", "on"}

AUTO_DOWNLOAD = os.getenv("AUTO_DOWNLOAD", "1").strip().lower() in {"1", "true", "yes", "on"}

# 文件保留天数 (0=永久)
FILE_RETENTION_DAYS = int(os.getenv("FILE_RETENTION_DAYS", "0") or "0")

# 稳定性配置
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "30") or "30")
RECONNECT_DELAY = int(os.getenv("RECONNECT_DELAY", "5") or "5")
MAX_RECONNECT_ATTEMPTS = int(os.getenv("MAX_RECONNECT_ATTEMPTS", "10") or "10")


# === 全局实例 ===

wechat_bot = direct_bot.WeChatHelperBot(
    entry_host=os.getenv("WECHAT_ENTRY_HOST", "szfilehelper.weixin.qq.com")
)
command_processor = processor.CommandProcessor(wechat_bot, download_dir=str(DOWNLOAD_DIR))

listener_task: asyncio.Task | None = None
session_saver_task: asyncio.Task | None = None
heartbeat_task: asyncio.Task | None = None
cleanup_task: asyncio.Task | None = None

# 稳定性状态
stability_state = {
    "reconnect_attempts": 0,
    "last_heartbeat": 0,
    "last_message_time": 0,
    "total_messages": 0,
    "errors": [],
}


# === 辅助函数 ===

def get_file_save_path(file_name: str) -> Path:
    """获取文件保存路径 (支持按日期分目录)"""
    if FILE_DATE_SUBDIR:
        date_dir = datetime.now().strftime("%Y-%m-%d")
        target_dir = DOWNLOAD_DIR / date_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / file_name
    return DOWNLOAD_DIR / file_name


def add_error(error: str):
    """记录错误 (保留最近20条)"""
    stability_state["errors"].append({
        "time": datetime.now().isoformat(),
        "error": error,
    })
    if len(stability_state["errors"]) > 20:
        stability_state["errors"] = stability_state["errors"][-20:]


# === 后台任务 ===

async def background_listener():
    """消息监听器 - 带自动重连"""
    processed_order: list[str] = []
    processed_set: set[str] = set()
    sent_buffer: list[str] = []

    print("[Listener] Started")

    while True:
        try:
            if not wechat_bot.is_logged_in:
                await wechat_bot.check_login_status(poll=True)
                if wechat_bot.is_logged_in:
                    stability_state["reconnect_attempts"] = 0
                    print("[Listener] Login restored")

            if wechat_bot.is_logged_in:
                messages = await wechat_bot.get_latest_messages(limit=12)

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
                    if len(processed_order) > 5000:
                        old = processed_order.pop(0)
                        processed_set.discard(old)

                    if content and content in sent_buffer:
                        continue

                    # 自动下载文件
                    if AUTO_DOWNLOAD and msg.get("type") in {"image", "file"}:
                        file_name = msg.get("file_name") or f"download_{msg_id[:8] or len(processed_order)}"
                        if msg.get("type") == "image" and "." not in file_name:
                            file_name += ".jpg"

                        save_path = get_file_save_path(file_name)
                        success = await wechat_bot.download_message_content(msg_id or unique_key, str(save_path))

                        if success:
                            # 更新消息中的文件路径
                            msg["file_path"] = str(save_path)
                            msg["file_size"] = save_path.stat().st_size if save_path.exists() else 0

                            # 保存文件元数据
                            mime_type, _ = mimetypes.guess_type(file_name)
                            command_processor.message_store.save_file(
                                msg_id=msg_id,
                                file_name=file_name,
                                file_path=str(save_path),
                                file_size=msg.get("file_size", 0),
                                mime_type=mime_type,
                            )

                    # 处理消息
                    reply = await command_processor.process(msg)
                    if reply:
                        ok = await wechat_bot.send_text(reply)
                        if ok:
                            sent_buffer.append(reply)
                            if len(sent_buffer) > 40:
                                sent_buffer.pop(0)

                    stability_state["last_message_time"] = time.time()
                    stability_state["total_messages"] += 1

        except Exception as exc:
            error_msg = f"Listener error: {exc}"
            print(f"[Listener] {error_msg}")
            add_error(error_msg)

        await asyncio.sleep(1)


async def periodic_session_saver():
    """定期保存会话"""
    while True:
        await asyncio.sleep(60)
        try:
            if wechat_bot.is_logged_in:
                await wechat_bot.save_session()
        except Exception as exc:
            print(f"[SessionSaver] Error: {exc}")


async def heartbeat_monitor():
    """心跳监控 - 检测掉线并触发重连"""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            stability_state["last_heartbeat"] = time.time()

            if wechat_bot.is_logged_in:
                # 检查连接状态
                status = await wechat_bot._synccheck()
                if status == "loginout":
                    print("[Heartbeat] Detected logout, will reconnect")
                    wechat_bot.is_logged_in = False
                    stability_state["reconnect_attempts"] += 1

                    if stability_state["reconnect_attempts"] <= MAX_RECONNECT_ATTEMPTS:
                        await asyncio.sleep(RECONNECT_DELAY)
                        # 尝试使用已保存的会话重新登录
                        await wechat_bot._load_session()
                        await wechat_bot.check_login_status(poll=True)
                    else:
                        add_error(f"Max reconnect attempts ({MAX_RECONNECT_ATTEMPTS}) reached")

        except Exception as exc:
            add_error(f"Heartbeat error: {exc}")


async def file_cleanup_task():
    """定期清理过期文件"""
    if FILE_RETENTION_DAYS <= 0:
        return

    while True:
        await asyncio.sleep(3600)  # 每小时检查一次
        try:
            deleted_count = command_processor.message_store.cleanup_old_files(
                days=FILE_RETENTION_DAYS,
                delete_files=True,
            )
            if deleted_count > 0:
                print(f"[Cleanup] Deleted {deleted_count} old files")
        except Exception as exc:
            add_error(f"Cleanup error: {exc}")


# === 生命周期 ===

@asynccontextmanager
async def lifespan(app: FastAPI):
    await wechat_bot.start(headless=True)
    await command_processor.start()

    global listener_task, session_saver_task, heartbeat_task, cleanup_task
    listener_task = asyncio.create_task(background_listener())
    session_saver_task = asyncio.create_task(periodic_session_saver())
    heartbeat_task = asyncio.create_task(heartbeat_monitor())

    if FILE_RETENTION_DAYS > 0:
        cleanup_task = asyncio.create_task(file_cleanup_task())

    yield

    for task in [listener_task, session_saver_task, heartbeat_task, cleanup_task]:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    await command_processor.stop()
    await wechat_bot.stop()


app = FastAPI(
    lifespan=lifespan,
    title="WeChat FileHelper Protocol Bot",
    description="Telegram Bot API 兼容的微信文件传输助手机器人框架",
    version="2.0.0",
)
app.mount("/static", StaticFiles(directory=str(DOWNLOAD_DIR)), name="static")


# === 请求模型 ===

class Message(BaseModel):
    content: str


class ChatModePayload(BaseModel):
    enabled: bool


class TaskCreatePayload(BaseModel):
    time_hm: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    command: str = Field(min_length=1)
    description: str = ""


class TaskEnabledPayload(BaseModel):
    enabled: bool


class ExecutePayload(BaseModel):
    command: str = Field(min_length=1)
    send_back: bool = False


class SendMessagePayload(BaseModel):
    """Telegram sendMessage 风格 - 完全兼容 TG 参数名"""
    text: str = Field(min_length=1)
    chat_id: str | int | None = None  # 兼容 TG，忽略 (只有 filehelper)
    reply_to_message_id: str | int | None = None
    parse_mode: str | None = None  # 兼容 TG，暂不处理
    disable_notification: bool = False  # 兼容 TG，忽略


class SendDocumentPayload(BaseModel):
    """Telegram sendDocument 风格"""
    document: str | None = None  # TG 原生参数名 (file_id 或 URL)
    file_path: str | None = None  # 本框架扩展: 本地路径
    chat_id: str | int | None = None
    reply_to_message_id: str | int | None = None
    caption: str | None = None  # 文件说明


class SendPhotoPayload(BaseModel):
    """Telegram sendPhoto 风格"""
    photo: str | None = None
    file_path: str | None = None
    chat_id: str | int | None = None
    reply_to_message_id: str | int | None = None
    caption: str | None = None


# === 基础 API ===

@app.get("/")
async def root():
    is_logged_in = await wechat_bot.check_login_status(poll=False)
    login = await wechat_bot.get_login_status_detail()
    framework_state = command_processor.get_state()
    return {
        "service": "WeChat FileHelper Protocol Bot",
        "version": "2.0.0",
        "backend": "direct-protocol",
        "logged_in": is_logged_in,
        "login": login,
        "framework": framework_state,
        "stability": {
            "reconnect_attempts": stability_state["reconnect_attempts"],
            "last_heartbeat": stability_state["last_heartbeat"],
            "total_messages": stability_state["total_messages"],
            "recent_errors": len(stability_state["errors"]),
        },
    }


@app.get("/qr")
async def get_qr():
    """Alias for /wechat/qr"""
    return await wechat_get_qr()


@app.get("/login/status")
async def login_status(auto_poll: bool = Query(default=True)):
    """Alias for /wechat/login/status"""
    return await wechat_login_status(auto_poll)


@app.post("/send")
async def send_message_simple(msg: Message):
    """Simple send endpoint - use /bot/sendMessage for standard API"""
    if not await wechat_bot.check_login_status(poll=False):
        raise HTTPException(status_code=401, detail="Unauthorized")

    success = await wechat_bot.send_text(msg.content)
    if not success:
        raise HTTPException(status_code=500, detail="send_text failed")
    return {"ok": True, "result": {"text": msg.content}}


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload and send file"""
    if not await wechat_bot.check_login_status(poll=False):
        raise HTTPException(status_code=401, detail="Unauthorized")

    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        success = await wechat_bot.send_file(tmp_path)
        if not success:
            raise HTTPException(status_code=500, detail="send_file failed")
        return {"status": "sent", "filename": file.filename}
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.get("/messages")
async def get_messages(limit: int = Query(default=10, ge=1, le=100)):
    """Get recent messages from memory cache"""
    messages = await wechat_bot.get_latest_messages(limit)
    return {"ok": True, "result": messages}


@app.post("/save_session")
async def trigger_save_session():
    """Alias for /wechat/session/save"""
    return await wechat_save_session()


# === Telegram Bot API ===
# 标准实现，参数与返回格式与 Telegram 一致
# 微信特有功能见下方 "WeChat 扩展" 部分

@app.get("/bot/getUpdates")
async def bot_get_updates(
    offset: int = Query(default=0),
    limit: int = Query(default=100, ge=1, le=100),
    timeout: int = Query(default=0),
    allowed_updates: list[str] | None = Query(default=None),
):
    """https://core.telegram.org/bots/api#getupdates"""
    updates = command_processor.get_updates(offset=offset, limit=limit)
    return {"ok": True, "result": updates}


@app.post("/bot/sendMessage")
async def bot_send_message(payload: SendMessagePayload):
    """https://core.telegram.org/bots/api#sendmessage"""
    if not await wechat_bot.check_login_status(poll=False):
        return {"ok": False, "error_code": 401, "description": "Unauthorized"}

    reply_to = str(payload.reply_to_message_id) if payload.reply_to_message_id else None
    result = await command_processor.send_message(
        text=payload.text,
        reply_to_message_id=reply_to,
    )
    return result


@app.post("/bot/sendDocument")
async def bot_send_document(payload: SendDocumentPayload):
    """https://core.telegram.org/bots/api#senddocument"""
    if not await wechat_bot.check_login_status(poll=False):
        return {"ok": False, "error_code": 401, "description": "Unauthorized"}

    file_path = payload.document or payload.file_path
    if not file_path:
        return {"ok": False, "error_code": 400, "description": "Bad Request: document is required"}

    reply_to = str(payload.reply_to_message_id) if payload.reply_to_message_id else None
    result = await command_processor.send_document(
        file_path=file_path,
        reply_to_message_id=reply_to,
    )

    if result.get("ok") and payload.caption:
        await command_processor.send_message(text=payload.caption)

    return result


@app.post("/bot/sendPhoto")
async def bot_send_photo(payload: SendPhotoPayload):
    """https://core.telegram.org/bots/api#sendphoto"""
    if not await wechat_bot.check_login_status(poll=False):
        return {"ok": False, "error_code": 401, "description": "Unauthorized"}

    file_path = payload.photo or payload.file_path
    if not file_path:
        return {"ok": False, "error_code": 400, "description": "Bad Request: photo is required"}

    reply_to = str(payload.reply_to_message_id) if payload.reply_to_message_id else None
    result = await command_processor.send_document(
        file_path=file_path,
        reply_to_message_id=reply_to,
    )

    if result.get("ok") and payload.caption:
        await command_processor.send_message(text=payload.caption)

    return result


@app.get("/bot/getMe")
async def bot_get_me():
    """https://core.telegram.org/bots/api#getme"""
    return {
        "ok": True,
        "result": {
            "id": int(wechat_bot.uin) if wechat_bot.uin and wechat_bot.uin.isdigit() else 0,
            "is_bot": True,
            "first_name": "文件传输助手",
            "username": "filehelper",
            "can_join_groups": False,
            "can_read_all_group_messages": False,
            "supports_inline_queries": False,
        },
    }


@app.get("/bot/getChat")
async def bot_get_chat(chat_id: str | int | None = Query(default=None)):
    """https://core.telegram.org/bots/api#getchat"""
    return {
        "ok": True,
        "result": {
            "id": int(wechat_bot.uin) if wechat_bot.uin and wechat_bot.uin.isdigit() else 0,
            "type": "private",
            "first_name": "文件传输助手",
            "username": "filehelper",
        },
    }


@app.post("/bot/setWebhook")
async def bot_set_webhook(
    url: str = "",
    certificate: str | None = None,
    ip_address: str | None = None,
    max_connections: int = 40,
    allowed_updates: list[str] | None = None,
    drop_pending_updates: bool = False,
    secret_token: str | None = None,
):
    """https://core.telegram.org/bots/api#setwebhook"""
    command_processor.message_webhook_url = url.strip()
    return {"ok": True, "result": True, "description": "Webhook was set"}


@app.post("/bot/deleteWebhook")
async def bot_delete_webhook(drop_pending_updates: bool = False):
    """https://core.telegram.org/bots/api#deletewebhook"""
    command_processor.message_webhook_url = ""
    return {"ok": True, "result": True}


@app.get("/bot/getWebhookInfo")
async def bot_get_webhook_info():
    """https://core.telegram.org/bots/api#getwebhookinfo"""
    url = command_processor.message_webhook_url
    return {
        "ok": True,
        "result": {
            "url": url,
            "has_custom_certificate": False,
            "pending_update_count": 0,
            "max_connections": 40,
            "ip_address": None,
        },
    }


@app.get("/bot/getFile")
async def bot_get_file(file_id: str = Query(...)):
    """https://core.telegram.org/bots/api#getfile"""
    file_info = command_processor.message_store.get_file_by_msg_id(file_id)
    if not file_info:
        return {"ok": False, "error_code": 400, "description": "Bad Request: file not found"}

    return {
        "ok": True,
        "result": {
            "file_id": file_info.msg_id,
            "file_unique_id": file_info.msg_id,
            "file_size": file_info.file_size,
            "file_path": file_info.file_path,
        },
    }


# === WeChat 扩展 API ===
# 以下接口为微信特有功能，Telegram 无对应接口

@app.get("/wechat/qr")
async def wechat_get_qr():
    """[WeChat] 获取登录二维码"""
    try:
        if await wechat_bot.check_login_status(poll=False):
            return Response(content="Already logged in", media_type="text/plain")
        png_bytes = await wechat_bot.get_login_qr()
        if not png_bytes:
            return Response(content="Already logged in", media_type="text/plain")
        return Response(content=png_bytes, media_type="image/png")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/wechat/login/status")
async def wechat_login_status(auto_poll: bool = Query(default=True)):
    """[WeChat] 获取登录状态"""
    if auto_poll:
        await wechat_bot.check_login_status(poll=True)
    return await wechat_bot.get_login_status_detail()


@app.post("/wechat/session/save")
async def wechat_save_session():
    """[WeChat] 保存会话"""
    success = await wechat_bot.save_session()
    return {"ok": success}


# === 文件管理 API ===

@app.get("/downloads")
async def list_downloads(
    limit: int = Query(default=100, ge=1, le=1000),
    include_subdirs: bool = Query(default=True),
):
    """列出下载的文件"""
    files = []

    if include_subdirs:
        for path in DOWNLOAD_DIR.rglob("*"):
            if path.is_file() and not path.name.startswith("."):
                rel_path = path.relative_to(DOWNLOAD_DIR)
                files.append({
                    "name": path.name,
                    "path": str(rel_path),
                    "size": path.stat().st_size,
                    "modified": path.stat().st_mtime,
                })
    else:
        for path in DOWNLOAD_DIR.iterdir():
            if path.is_file() and not path.name.startswith("."):
                files.append({
                    "name": path.name,
                    "path": path.name,
                    "size": path.stat().st_size,
                    "modified": path.stat().st_mtime,
                })

    files.sort(key=lambda x: x["modified"], reverse=True)
    return {
        "files": files[:limit],
        "total": len(files),
        "base_url": "/static/",
    }


@app.get("/files/metadata")
async def get_files_metadata(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """获取文件元数据 (从数据库)"""
    files = command_processor.message_store.get_files(limit=limit, offset=offset)
    return {
        "files": [asdict(f) for f in files],
        "count": len(files),
    }


@app.delete("/files/{msg_id}")
async def delete_file(msg_id: str):
    """删除文件"""
    file_info = command_processor.message_store.get_file_by_msg_id(msg_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        path = Path(file_info.file_path)
        if path.exists():
            path.unlink()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Delete failed: {exc}")

    return {"status": "deleted", "msg_id": msg_id}


@app.post("/files/cleanup")
async def cleanup_files(days: int = Query(default=30, ge=1)):
    """清理过期文件"""
    deleted_messages = command_processor.message_store.cleanup_old_messages(days=days)
    deleted_files = command_processor.message_store.cleanup_old_files(days=days, delete_files=True)
    return {
        "deleted_messages": deleted_messages,
        "deleted_files": deleted_files,
    }


# === 消息存储 API ===

@app.get("/store/stats")
async def store_stats():
    """获取消息存储统计"""
    return command_processor.message_store.get_stats()


@app.get("/store/messages")
async def store_messages(
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    msg_type: str | None = Query(default=None),
    since: int | None = Query(default=None, description="Unix timestamp"),
):
    """查询历史消息"""
    messages = command_processor.message_store.get_updates(
        offset=offset,
        limit=limit,
        msg_type=msg_type,
        since=since,
    )
    return {
        "messages": [asdict(m) for m in messages],
        "count": len(messages),
    }


# === Trace API ===

@app.get("/trace/status")
async def trace_status():
    return wechat_bot.get_trace_status()


@app.get("/trace/recent")
async def trace_recent(limit: int = Query(default=100, ge=1, le=1000)):
    rows = await wechat_bot.read_recent_traces(limit=limit)
    return {"count": len(rows), "rows": rows}


@app.post("/trace/clear")
async def trace_clear():
    await wechat_bot.clear_traces()
    return {"status": "cleared"}


# === Framework API ===

@app.get("/framework/state")
async def framework_state():
    return command_processor.get_state()


@app.post("/framework/chat_mode")
async def framework_set_chat_mode(payload: ChatModePayload):
    command_processor.set_chat_mode(payload.enabled)
    return {"status": "ok", "enabled": payload.enabled}


@app.post("/framework/execute")
async def framework_execute(payload: ExecutePayload):
    result = await command_processor.execute_command_text(payload.command, source="api_execute")
    if payload.send_back and result:
        await wechat_bot.send_text(result)
    return {"status": "ok", "command": payload.command, "result": result}


@app.get("/framework/tasks")
async def framework_tasks():
    return {"tasks": command_processor.list_tasks()}


@app.post("/framework/tasks")
async def framework_add_task(payload: TaskCreatePayload):
    try:
        task = command_processor.add_task(
            time_hm=payload.time_hm,
            command_text=payload.command,
            description=payload.description,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "task": task}


@app.delete("/framework/tasks/{task_id}")
async def framework_delete_task(task_id: str):
    ok = command_processor.delete_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="task not found")
    return {"status": "deleted", "task_id": task_id}


@app.post("/framework/tasks/{task_id}/enabled")
async def framework_set_task_enabled(task_id: str, payload: TaskEnabledPayload):
    ok = command_processor.set_task_enabled(task_id, payload.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail="task not found")
    return {"status": "ok", "task_id": task_id, "enabled": payload.enabled}


@app.post("/framework/tasks/{task_id}/run")
async def framework_run_task(task_id: str):
    ok = await command_processor.run_task_now(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="task not found")
    return {"status": "ok", "task_id": task_id, "trigger": "manual"}


# === 插件 API ===

@app.get("/plugins")
async def list_plugins():
    """列出已加载的插件"""
    return command_processor.plugin_loader.get_status()


@app.post("/plugins/reload")
async def reload_plugins():
    """重新加载所有插件"""
    command_processor.plugin_loader.reload_all()
    return command_processor.plugin_loader.get_status()


# === 稳定性 API ===

@app.get("/health")
async def health_check():
    """健康检查端点"""
    is_logged_in = await wechat_bot.check_login_status(poll=False)
    return {
        "status": "healthy" if is_logged_in else "degraded",
        "logged_in": is_logged_in,
        "uptime": int(time.time() - command_processor.started_at),
        "stability": stability_state,
    }


@app.get("/stability")
async def stability_status():
    """稳定性状态"""
    return {
        "reconnect_attempts": stability_state["reconnect_attempts"],
        "max_reconnect_attempts": MAX_RECONNECT_ATTEMPTS,
        "last_heartbeat": stability_state["last_heartbeat"],
        "last_message_time": stability_state["last_message_time"],
        "total_messages": stability_state["total_messages"],
        "recent_errors": stability_state["errors"],
        "config": {
            "heartbeat_interval": HEARTBEAT_INTERVAL,
            "reconnect_delay": RECONNECT_DELAY,
            "file_retention_days": FILE_RETENTION_DAYS,
        },
    }


@app.get("/debug_html")
async def debug_html():
    source = await wechat_bot.get_page_source()
    return Response(content=source, media_type="application/json")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
