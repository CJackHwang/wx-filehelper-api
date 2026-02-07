"""
Telegram Bot API 兼容路由

标准实现，参数与返回格式与 Telegram 一致
https://core.telegram.org/bots/api
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, File, Form, Query, UploadFile
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from direct_bot import WeChatHelperBot
    from processor import CommandProcessor

router = APIRouter(prefix="/bot", tags=["Telegram Bot API"])

# 依赖注入的全局引用 (在 main.py 中设置)
_bot: "WeChatHelperBot | None" = None
_processor: "CommandProcessor | None" = None


def init(bot: "WeChatHelperBot", processor: "CommandProcessor"):
    """初始化路由依赖"""
    global _bot, _processor
    _bot = bot
    _processor = processor


def _get_bot() -> "WeChatHelperBot":
    if _bot is None:
        raise RuntimeError("Bot not initialized")
    return _bot


def _get_processor() -> "CommandProcessor":
    if _processor is None:
        raise RuntimeError("Processor not initialized")
    return _processor


# === Pydantic Models ===

class SendMessagePayload(BaseModel):
    """sendMessage 请求体"""
    text: str = Field(min_length=1)
    chat_id: str | int | None = None
    reply_to_message_id: str | int | None = None
    parse_mode: str | None = None
    disable_notification: bool = False


class SendDocumentPayload(BaseModel):
    """sendDocument 请求体 (JSON 模式)"""
    document: str | None = None
    file_path: str | None = None
    chat_id: str | int | None = None
    reply_to_message_id: str | int | None = None
    caption: str | None = None


class SendPhotoPayload(BaseModel):
    """sendPhoto 请求体 (JSON 模式)"""
    photo: str | None = None
    file_path: str | None = None
    chat_id: str | int | None = None
    reply_to_message_id: str | int | None = None
    caption: str | None = None


class CopyMessagePayload(BaseModel):
    """copyMessage 请求体"""
    chat_id: str | int | None = None
    from_chat_id: str | int | None = None
    message_id: str | int


# === API Endpoints ===

@router.get("/getUpdates")
async def get_updates(
    offset: int = Query(default=0),
    limit: int = Query(default=100, ge=1, le=100),
    timeout: int = Query(default=0),
    allowed_updates: list[str] | None = Query(default=None),
) -> dict[str, Any]:
    """https://core.telegram.org/bots/api#getupdates"""
    processor = _get_processor()
    updates = processor.get_updates(offset=offset, limit=limit)
    return {"ok": True, "result": updates}


@router.get("/getMe")
async def get_me() -> dict[str, Any]:
    """https://core.telegram.org/bots/api#getme"""
    bot = _get_bot()
    return {
        "ok": True,
        "result": {
            "id": int(bot.uin) if bot.uin and bot.uin.isdigit() else 0,
            "is_bot": True,
            "first_name": "文件传输助手",
            "username": "filehelper",
            "can_join_groups": False,
            "can_read_all_group_messages": False,
            "supports_inline_queries": False,
        },
    }


@router.post("/sendMessage")
async def send_message(payload: SendMessagePayload) -> dict[str, Any]:
    """https://core.telegram.org/bots/api#sendmessage"""
    bot = _get_bot()
    processor = _get_processor()

    if not await bot.check_login_status(poll=False):
        return {"ok": False, "error_code": 401, "description": "Unauthorized"}

    reply_to = str(payload.reply_to_message_id) if payload.reply_to_message_id else None
    result = await processor.send_message(
        text=payload.text,
        reply_to_message_id=reply_to,
    )
    return result


@router.post("/sendDocument")
async def send_document_json(payload: SendDocumentPayload) -> dict[str, Any]:
    """https://core.telegram.org/bots/api#senddocument (JSON 模式)"""
    bot = _get_bot()
    processor = _get_processor()

    if not await bot.check_login_status(poll=False):
        return {"ok": False, "error_code": 401, "description": "Unauthorized"}

    file_path = payload.document or payload.file_path
    if not file_path:
        return {"ok": False, "error_code": 400, "description": "Bad Request: document is required"}

    reply_to = str(payload.reply_to_message_id) if payload.reply_to_message_id else None
    result = await processor.send_document(
        file_path=file_path,
        reply_to_message_id=reply_to,
    )

    if result.get("ok") and payload.caption:
        await processor.send_message(text=payload.caption)

    return result


@router.post("/sendDocument/upload")
async def send_document_upload(
    document: UploadFile = File(...),
    chat_id: Annotated[str | None, Form()] = None,
    caption: Annotated[str | None, Form()] = None,
    reply_to_message_id: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """https://core.telegram.org/bots/api#senddocument (Multipart 上传模式)"""
    bot = _get_bot()
    processor = _get_processor()

    if not await bot.check_login_status(poll=False):
        return {"ok": False, "error_code": 401, "description": "Unauthorized"}

    # 保存到临时文件
    suffix = Path(document.filename or "file").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(document.file, tmp)
        tmp_path = tmp.name

    try:
        result = await processor.send_document(
            file_path=tmp_path,
            reply_to_message_id=reply_to_message_id,
        )

        if result.get("ok") and caption:
            await processor.send_message(text=caption)

        return result
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.post("/sendPhoto")
async def send_photo_json(payload: SendPhotoPayload) -> dict[str, Any]:
    """https://core.telegram.org/bots/api#sendphoto (JSON 模式)"""
    bot = _get_bot()
    processor = _get_processor()

    if not await bot.check_login_status(poll=False):
        return {"ok": False, "error_code": 401, "description": "Unauthorized"}

    file_path = payload.photo or payload.file_path
    if not file_path:
        return {"ok": False, "error_code": 400, "description": "Bad Request: photo is required"}

    reply_to = str(payload.reply_to_message_id) if payload.reply_to_message_id else None
    result = await processor.send_document(
        file_path=file_path,
        reply_to_message_id=reply_to,
    )

    if result.get("ok") and payload.caption:
        await processor.send_message(text=payload.caption)

    return result


@router.post("/sendPhoto/upload")
async def send_photo_upload(
    photo: UploadFile = File(...),
    chat_id: Annotated[str | None, Form()] = None,
    caption: Annotated[str | None, Form()] = None,
    reply_to_message_id: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """https://core.telegram.org/bots/api#sendphoto (Multipart 上传模式)"""
    bot = _get_bot()
    processor = _get_processor()

    if not await bot.check_login_status(poll=False):
        return {"ok": False, "error_code": 401, "description": "Unauthorized"}

    suffix = Path(photo.filename or "photo.jpg").suffix or ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(photo.file, tmp)
        tmp_path = tmp.name

    try:
        result = await processor.send_document(
            file_path=tmp_path,
            reply_to_message_id=reply_to_message_id,
        )

        if result.get("ok") and caption:
            await processor.send_message(text=caption)

        return result
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.post("/copyMessage")
async def copy_message(payload: CopyMessagePayload) -> dict[str, Any]:
    """https://core.telegram.org/bots/api#copymessage"""
    bot = _get_bot()
    processor = _get_processor()

    if not await bot.check_login_status(poll=False):
        return {"ok": False, "error_code": 401, "description": "Unauthorized"}

    # 从存储中获取原消息
    msg = processor.message_store.get_message(str(payload.message_id))
    if not msg:
        return {"ok": False, "error_code": 400, "description": "Bad Request: message not found"}

    # 重新发送消息内容
    if msg.type == "text" and msg.text:
        result = await processor.send_message(text=msg.text)
    elif msg.file_path:
        result = await processor.send_document(file_path=msg.file_path)
    else:
        return {"ok": False, "error_code": 400, "description": "Bad Request: message has no content"}

    return result


@router.get("/getChat")
async def get_chat(chat_id: str | int | None = Query(default=None)) -> dict[str, Any]:
    """https://core.telegram.org/bots/api#getchat"""
    bot = _get_bot()
    return {
        "ok": True,
        "result": {
            "id": int(bot.uin) if bot.uin and bot.uin.isdigit() else 0,
            "type": "private",
            "first_name": "文件传输助手",
            "username": "filehelper",
        },
    }


@router.get("/getFile")
async def get_file(file_id: str = Query(...)) -> dict[str, Any]:
    """https://core.telegram.org/bots/api#getfile"""
    processor = _get_processor()
    file_info = processor.message_store.get_file_by_msg_id(file_id)

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


@router.post("/setWebhook")
async def set_webhook(
    url: str = "",
    certificate: str | None = None,
    ip_address: str | None = None,
    max_connections: int = 40,
    allowed_updates: list[str] | None = None,
    drop_pending_updates: bool = False,
    secret_token: str | None = None,
) -> dict[str, Any]:
    """https://core.telegram.org/bots/api#setwebhook"""
    processor = _get_processor()
    processor.message_webhook_url = url.strip()
    return {"ok": True, "result": True, "description": "Webhook was set"}


@router.post("/deleteWebhook")
async def delete_webhook(drop_pending_updates: bool = False) -> dict[str, Any]:
    """https://core.telegram.org/bots/api#deletewebhook"""
    processor = _get_processor()
    processor.message_webhook_url = ""
    return {"ok": True, "result": True}


@router.get("/getWebhookInfo")
async def get_webhook_info() -> dict[str, Any]:
    """https://core.telegram.org/bots/api#getwebhookinfo"""
    processor = _get_processor()
    url = processor.message_webhook_url
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
