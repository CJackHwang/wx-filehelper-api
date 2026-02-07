"""
框架管理路由

- 任务调度 (定时任务)
- 插件管理
- 聊天模式
- 命令执行
- 健康检查
- 稳定性监控
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from direct_bot import WeChatHelperBot
    from processor import CommandProcessor

router = APIRouter(tags=["Framework"])

# 依赖注入
_processor: "CommandProcessor | None" = None
_bot: "WeChatHelperBot | None" = None

# 稳定性状态 (共享引用)
_stability_state: dict[str, Any] | None = None


def init(
    processor: "CommandProcessor",
    bot: "WeChatHelperBot",
    stability_state: dict[str, Any],
):
    """初始化路由依赖"""
    global _processor, _bot, _stability_state
    _processor = processor
    _bot = bot
    _stability_state = stability_state


def _get_processor() -> "CommandProcessor":
    if _processor is None:
        raise RuntimeError("Processor not initialized")
    return _processor


def _get_bot() -> "WeChatHelperBot":
    if _bot is None:
        raise RuntimeError("Bot not initialized")
    return _bot


def _get_stability() -> dict[str, Any]:
    if _stability_state is None:
        raise RuntimeError("Stability state not initialized")
    return _stability_state


# === 请求模型 ===


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


# === Framework API ===


@router.get("/framework/state")
async def framework_state() -> dict[str, Any]:
    """获取框架状态"""
    return _get_processor().get_state()


@router.post("/framework/chat_mode")
async def framework_set_chat_mode(payload: ChatModePayload) -> dict[str, Any]:
    """设置聊天模式"""
    _get_processor().set_chat_mode(payload.enabled)
    return {"status": "ok", "enabled": payload.enabled}


@router.post("/framework/execute")
async def framework_execute(payload: ExecutePayload) -> dict[str, Any]:
    """执行命令"""
    processor = _get_processor()
    result = await processor.execute_command_text(payload.command, source="api_execute")
    if payload.send_back and result:
        await _get_bot().send_text(result)
    return {"status": "ok", "command": payload.command, "result": result}


# === 任务调度 API ===


@router.get("/framework/tasks")
async def framework_tasks() -> dict[str, Any]:
    """列出所有定时任务"""
    return {"tasks": _get_processor().list_tasks()}


@router.post("/framework/tasks")
async def framework_add_task(payload: TaskCreatePayload) -> dict[str, Any]:
    """添加定时任务"""
    try:
        task = _get_processor().add_task(
            time_hm=payload.time_hm,
            command_text=payload.command,
            description=payload.description,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "task": task}


@router.delete("/framework/tasks/{task_id}")
async def framework_delete_task(task_id: str) -> dict[str, str]:
    """删除定时任务"""
    ok = _get_processor().delete_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="task not found")
    return {"status": "deleted", "task_id": task_id}


@router.post("/framework/tasks/{task_id}/enabled")
async def framework_set_task_enabled(
    task_id: str, payload: TaskEnabledPayload
) -> dict[str, Any]:
    """启用/禁用定时任务"""
    ok = _get_processor().set_task_enabled(task_id, payload.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail="task not found")
    return {"status": "ok", "task_id": task_id, "enabled": payload.enabled}


@router.post("/framework/tasks/{task_id}/run")
async def framework_run_task(task_id: str) -> dict[str, str]:
    """立即运行指定任务"""
    ok = await _get_processor().run_task_now(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="task not found")
    return {"status": "ok", "task_id": task_id, "trigger": "manual"}


# === 插件 API ===


@router.get("/plugins")
async def list_plugins() -> dict[str, Any]:
    """列出已加载的插件"""
    return _get_processor().plugin_loader.get_status()


@router.post("/plugins/reload")
async def reload_plugins() -> dict[str, Any]:
    """重新加载所有插件"""
    _get_processor().plugin_loader.reload_all()
    return _get_processor().plugin_loader.get_status()


# === 健康与稳定性 API ===


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """健康检查端点"""
    processor = _get_processor()
    bot = _get_bot()
    stability = _get_stability()

    is_logged_in = await bot.check_login_status(poll=False)
    return {
        "status": "healthy" if is_logged_in else "degraded",
        "logged_in": is_logged_in,
        "uptime": int(time.time() - processor.started_at),
        "stability": stability,
    }


@router.get("/stability")
async def stability_status() -> dict[str, Any]:
    """稳定性状态"""
    from config import settings

    stability = _get_stability()
    return {
        "reconnect_attempts": stability["reconnect_attempts"],
        "max_reconnect_attempts": settings.max_reconnect_attempts,
        "last_heartbeat": stability["last_heartbeat"],
        "last_message_time": stability["last_message_time"],
        "total_messages": stability["total_messages"],
        "recent_errors": stability["errors"],
        "config": {
            "heartbeat_interval": settings.heartbeat_interval,
            "reconnect_delay": settings.reconnect_delay,
            "file_retention_days": settings.file_retention_days,
        },
    }


# === Trace API ===


@router.get("/trace/status")
async def trace_status() -> dict[str, Any]:
    """获取追踪状态"""
    return _get_bot().get_trace_status()


@router.get("/trace/recent")
async def trace_recent(
    limit: int = Query(default=100, ge=1, le=1000)
) -> dict[str, Any]:
    """获取最近的追踪记录"""
    rows = await _get_bot().read_recent_traces(limit=limit)
    return {"count": len(rows), "rows": rows}


@router.post("/trace/clear")
async def trace_clear() -> dict[str, str]:
    """清除追踪记录"""
    await _get_bot().clear_traces()
    return {"status": "cleared"}


# === 调试 API ===


@router.get("/debug_html")
async def debug_html() -> dict[str, Any]:
    """获取页面源码 (调试用)"""
    from fastapi.responses import Response
    source = await _get_bot().get_page_source()
    return Response(content=source, media_type="application/json")
