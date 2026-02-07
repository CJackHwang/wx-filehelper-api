"""
Routes package - 模块化路由
"""

from .bot import router as bot_router
from .wechat import router as wechat_router
from .files import router as files_router
from .framework import router as framework_router

__all__ = ["bot_router", "wechat_router", "files_router", "framework_router"]
