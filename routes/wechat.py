"""
WeChat 扩展路由

微信特有功能，Telegram 无对应接口
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Query, Response, HTTPException

if TYPE_CHECKING:
    from direct_bot import WeChatHelperBot

router = APIRouter(prefix="/wechat", tags=["WeChat Extensions"])

# 依赖注入
_bot: "WeChatHelperBot | None" = None


def init(bot: "WeChatHelperBot"):
    """初始化路由依赖"""
    global _bot
    _bot = bot


def _get_bot() -> "WeChatHelperBot":
    if _bot is None:
        raise RuntimeError("Bot not initialized")
    return _bot


@router.get("/qr")
async def get_qr() -> Response:
    """获取登录二维码"""
    bot = _get_bot()
    try:
        if await bot.check_login_status(poll=False):
            return Response(content="Already logged in", media_type="text/plain")
        png_bytes = await bot.get_login_qr()
        if not png_bytes:
            return Response(content="Already logged in", media_type="text/plain")
        return Response(content=png_bytes, media_type="image/png")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/login/status")
async def login_status(auto_poll: bool = Query(default=True)) -> dict[str, Any]:
    """获取登录状态"""
    bot = _get_bot()
    if auto_poll:
        await bot.check_login_status(poll=True)
    return await bot.get_login_status_detail()


@router.post("/session/save")
async def save_session() -> dict[str, bool]:
    """保存会话"""
    bot = _get_bot()
    success = await bot.save_session()
    return {"ok": success}


@router.get("/trace/status")
async def trace_status() -> dict[str, Any]:
    """Trace 状态"""
    bot = _get_bot()
    return bot.get_trace_status()


@router.get("/trace/recent")
async def trace_recent(limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
    """最近的 Trace 记录"""
    bot = _get_bot()
    rows = await bot.read_recent_traces(limit=limit)
    return {"count": len(rows), "rows": rows}


@router.post("/trace/clear")
async def trace_clear() -> dict[str, str]:
    """清除 Trace 日志"""
    bot = _get_bot()
    await bot.clear_traces()
    return {"status": "cleared"}
