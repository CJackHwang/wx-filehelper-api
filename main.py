"""
WeChat FileHelper Protocol Bot - FastAPI 服务入口

功能:
- Telegram Bot API 兼容接口
- 插件系统
- 消息持久化
- 文件管理
- 稳定性增强
"""

from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Query, Response, UploadFile, File
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import direct_bot
import processor
from background import BackgroundTasks
from config import settings
from routes import bot_router, wechat_router, files_router, framework_router
from routes.bot import init as init_bot_routes
from routes.wechat import init as init_wechat_routes
from routes.files import init as init_files_routes
from routes.framework import init as init_framework_routes


# === 全局实例 ===

wechat_bot = direct_bot.WeChatHelperBot(entry_host=settings.wechat_entry_host)
command_processor = processor.CommandProcessor(wechat_bot, download_dir=str(settings.download_dir))

# 稳定性状态
stability_state = {
    "reconnect_attempts": 0,
    "last_heartbeat": 0,
    "last_message_time": 0,
    "total_messages": 0,
    "errors": [],
}

# 后台任务管理器
background_tasks: BackgroundTasks | None = None


# === 生命周期 ===

@asynccontextmanager
async def lifespan(app: FastAPI):
    global background_tasks

    # 启动核心服务
    await wechat_bot.start(headless=True)
    await command_processor.start()

    # 初始化路由依赖
    init_bot_routes(wechat_bot, command_processor)
    init_wechat_routes(wechat_bot)
    init_files_routes(command_processor)
    init_framework_routes(command_processor, wechat_bot, stability_state)

    # 启动后台任务
    background_tasks = BackgroundTasks(
        bot=wechat_bot,
        processor=command_processor,
        download_dir=settings.download_dir,
        stability_state=stability_state,
        auto_download=settings.auto_download,
        file_date_subdir=settings.file_date_subdir,
        heartbeat_interval=settings.heartbeat_interval,
        reconnect_delay=settings.reconnect_delay,
        max_reconnect_attempts=settings.max_reconnect_attempts,
        file_retention_days=settings.file_retention_days,
    )
    background_tasks.start_all()

    yield

    # 停止后台任务
    if background_tasks:
        await background_tasks.stop_all()

    # 停止核心服务
    await command_processor.stop()
    await wechat_bot.stop()


app = FastAPI(
    lifespan=lifespan,
    title=settings.app_name,
    description="Telegram Bot API 兼容的微信文件传输助手机器人框架",
    version=settings.version,
)

# 静态文件
app.mount("/static", StaticFiles(directory=str(settings.download_dir)), name="static")

# 注册路由
app.include_router(bot_router)
app.include_router(wechat_router)
app.include_router(files_router)
app.include_router(framework_router)


# === 请求模型 ===

class Message(BaseModel):
    content: str


class SendMessagePayload(BaseModel):
    """Telegram sendMessage 风格 - 完全兼容 TG 参数名"""
    text: str = Field(min_length=1)
    chat_id: str | int | None = None
    reply_to_message_id: str | int | None = None
    parse_mode: str | None = None
    disable_notification: bool = False


class SendDocumentPayload(BaseModel):
    """Telegram sendDocument 风格"""
    document: str | None = None
    file_path: str | None = None
    chat_id: str | int | None = None
    reply_to_message_id: str | int | None = None
    caption: str | None = None


class SendPhotoPayload(BaseModel):
    """Telegram sendPhoto 风格"""
    photo: str | None = None
    file_path: str | None = None
    chat_id: str | int | None = None
    reply_to_message_id: str | int | None = None
    caption: str | None = None


# === 基础 API (快捷入口，同时保留兼容性) ===


@app.get("/")
async def root():
    """服务状态概览"""
    is_logged_in = await wechat_bot.check_login_status(poll=False)
    login = await wechat_bot.get_login_status_detail()
    framework_state = command_processor.get_state()
    return {
        "service": settings.app_name,
        "version": settings.version,
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
    """获取登录二维码 (快捷入口)"""
    try:
        if await wechat_bot.check_login_status(poll=False):
            return Response(content="Already logged in", media_type="text/plain")
        png_bytes = await wechat_bot.get_login_qr()
        if not png_bytes:
            return Response(content="Already logged in", media_type="text/plain")
        return Response(content=png_bytes, media_type="image/png")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/login/status")
async def login_status(auto_poll: bool = Query(default=True)):
    """获取登录状态 (快捷入口)"""
    if auto_poll:
        await wechat_bot.check_login_status(poll=True)
    return await wechat_bot.get_login_status_detail()


@app.post("/send")
async def send_message_simple(msg: Message):
    """简单发送接口 - 使用 /bot/sendMessage 获得标准 API"""
    if not await wechat_bot.check_login_status(poll=False):
        raise HTTPException(status_code=401, detail="Unauthorized")

    success = await wechat_bot.send_text(msg.content)
    if not success:
        raise HTTPException(status_code=500, detail="send_text failed")
    return {"ok": True, "result": {"text": msg.content}}


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传并发送文件 (快捷入口)"""
    if not await wechat_bot.check_login_status(poll=False):
        raise HTTPException(status_code=401, detail="Unauthorized")

    suffix = os.path.splitext(file.filename or "file")[1]
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
    """获取最近消息 (内存缓存)"""
    messages = await wechat_bot.get_latest_messages(limit)
    return {"ok": True, "result": messages}


@app.post("/save_session")
async def trigger_save_session():
    """保存会话 (快捷入口)"""
    success = await wechat_bot.save_session()
    return {"ok": success}


# === 旧版 Telegram Bot API 端点 (保持兼容性) ===
# 新代码应使用 /bot/* 路由


@app.get("/bot/getUpdates")
async def bot_get_updates(
    offset: int = Query(default=0),
    limit: int = Query(default=100, ge=1, le=100),
    timeout: int = Query(default=0),
    allowed_updates: list[str] | None = Query(default=None),
):
    """https://core.telegram.org/bots/api#getupdates (兼容入口)"""
    updates = command_processor.get_updates(offset=offset, limit=limit)
    return {"ok": True, "result": updates}


@app.post("/bot/sendMessage")
async def bot_send_message(payload: SendMessagePayload):
    """https://core.telegram.org/bots/api#sendmessage (兼容入口)"""
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
    """https://core.telegram.org/bots/api#senddocument (兼容入口)"""
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
    """https://core.telegram.org/bots/api#sendphoto (兼容入口)"""
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
    """https://core.telegram.org/bots/api#getme (兼容入口)"""
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
    """https://core.telegram.org/bots/api#getchat (兼容入口)"""
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
    """https://core.telegram.org/bots/api#setwebhook (兼容入口)"""
    command_processor.message_webhook_url = url.strip()
    return {"ok": True, "result": True, "description": "Webhook was set"}


@app.post("/bot/deleteWebhook")
async def bot_delete_webhook(drop_pending_updates: bool = False):
    """https://core.telegram.org/bots/api#deletewebhook (兼容入口)"""
    command_processor.message_webhook_url = ""
    return {"ok": True, "result": True}


@app.get("/bot/getWebhookInfo")
async def bot_get_webhook_info():
    """https://core.telegram.org/bots/api#getwebhookinfo (兼容入口)"""
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
    """https://core.telegram.org/bots/api#getfile (兼容入口)"""
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
