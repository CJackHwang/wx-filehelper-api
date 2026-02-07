"""
插件基础模块 - 提供插件开发所需的基类和装饰器

开发者只需:
1. 在 plugins/ 目录创建 .py 文件
2. 使用 @command 装饰器注册命令
3. 使用 @on_message 装饰器处理消息

示例:
    from plugin_base import command, on_message, CommandContext

    @command("hello", description="打招呼")
    async def hello_cmd(ctx: CommandContext) -> str:
        return f"你好, {ctx.args[0] if ctx.args else '世界'}!"

    @on_message(priority=10)
    async def log_all(ctx: CommandContext) -> str | None:
        print(f"收到消息: {ctx.text}")
        return None  # 返回 None 表示不拦截
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
import functools


@dataclass
class CommandContext:
    """命令执行上下文"""
    text: str                          # 原始文本
    command: str                       # 命令名 (不含 /)
    args: list[str]                    # 参数列表
    msg: dict[str, Any]                # 原始消息对象
    msg_id: str                        # 消息ID
    is_command: bool                   # 是否为命令调用
    bot: Any = None                    # WeChatHelperBot 实例
    processor: Any = None              # CommandProcessor 实例
    reply_to: str | None = None        # 回复的消息ID (如果有)
    extra: dict[str, Any] = field(default_factory=dict)  # 扩展数据


CommandHandler = Callable[[CommandContext], Awaitable[str | None]]
MessageHandler = Callable[[CommandContext], Awaitable[str | None]]


@dataclass
class CommandInfo:
    """命令注册信息"""
    name: str
    handler: CommandHandler
    description: str = ""
    usage: str = ""
    aliases: list[str] = field(default_factory=list)
    hidden: bool = False


@dataclass
class MessageHandlerInfo:
    """消息处理器注册信息"""
    handler: MessageHandler
    priority: int = 0
    name: str = ""


# 全局注册表 - 插件加载时自动填充
_commands: dict[str, CommandInfo] = {}
_message_handlers: list[MessageHandlerInfo] = []


def command(
    name: str,
    description: str = "",
    usage: str = "",
    aliases: list[str] | None = None,
    hidden: bool = False,
):
    """
    命令装饰器 - 注册一个命令处理函数

    Args:
        name: 命令名称 (不含 /)
        description: 命令描述 (用于 /help)
        usage: 使用说明
        aliases: 命令别名列表
        hidden: 是否在 help 中隐藏

    Example:
        @command("ping", description="测试连通性")
        async def ping_cmd(ctx: CommandContext) -> str:
            return "pong"
    """
    def decorator(func: CommandHandler) -> CommandHandler:
        info = CommandInfo(
            name=name.lower(),
            handler=func,
            description=description,
            usage=usage or f"/{name}",
            aliases=aliases or [],
            hidden=hidden,
        )
        _commands[name.lower()] = info
        for alias in info.aliases:
            _commands[alias.lower()] = info

        @functools.wraps(func)
        async def wrapper(ctx: CommandContext) -> str | None:
            return await func(ctx)
        return wrapper

    return decorator


def on_message(priority: int = 0, name: str = ""):
    """
    消息处理器装饰器 - 注册一个消息处理函数

    处理器按 priority 降序执行，返回非 None 时停止后续处理器

    Args:
        priority: 优先级 (越大越先执行)
        name: 处理器名称 (用于调试)

    Example:
        @on_message(priority=100)
        async def filter_spam(ctx: CommandContext) -> str | None:
            if "广告" in ctx.text:
                return "检测到垃圾消息，已忽略"
            return None  # 继续后续处理
    """
    def decorator(func: MessageHandler) -> MessageHandler:
        info = MessageHandlerInfo(
            handler=func,
            priority=priority,
            name=name or func.__name__,
        )
        _message_handlers.append(info)
        _message_handlers.sort(key=lambda x: -x.priority)

        @functools.wraps(func)
        async def wrapper(ctx: CommandContext) -> str | None:
            return await func(ctx)
        return wrapper

    return decorator


def get_registered_commands() -> dict[str, CommandInfo]:
    """获取所有已注册的命令"""
    return _commands.copy()


def get_message_handlers() -> list[MessageHandlerInfo]:
    """获取所有消息处理器 (按优先级排序)"""
    return _message_handlers.copy()


def clear_registry():
    """清空注册表 (用于测试或重新加载)"""
    _commands.clear()
    _message_handlers.clear()


def get_help_text() -> str:
    """生成帮助文本"""
    lines = ["可用命令:"]
    seen = set()
    for info in _commands.values():
        if info.hidden or info.name in seen:
            continue
        seen.add(info.name)
        desc = f" - {info.description}" if info.description else ""
        aliases = f" (别名: {', '.join(info.aliases)})" if info.aliases else ""
        lines.append(f"  /{info.name}{desc}{aliases}")
    return "\n".join(lines)
