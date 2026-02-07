"""
内置命令插件 - 框架自带的基础命令
"""

import os
import platform
import time
from datetime import datetime

from plugin_base import command, CommandContext


@command("ping", description="测试连通性")
async def cmd_ping(ctx: CommandContext) -> str:
    return "pong"


@command("help", description="显示帮助信息", aliases=["h", "?"])
async def cmd_help(ctx: CommandContext) -> str:
    from plugin_base import get_help_text
    return get_help_text()


@command("echo", description="回显消息", usage="/echo <text>")
async def cmd_echo(ctx: CommandContext) -> str:
    return " ".join(ctx.args) if ctx.args else ""


@command("status", description="显示服务器状态", aliases=["stat", "info"])
async def cmd_status(ctx: CommandContext) -> str:
    processor = ctx.processor
    uptime = int(time.time() - processor.started_at)
    bot_logged_in = bool(getattr(processor.bot, "is_logged_in", False))
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return (
        f"server={processor.server_label}\n"
        f"time={now}\n"
        f"uptime={uptime}s\n"
        f"platform={platform.platform()}\n"
        f"python={platform.python_version()}\n"
        f"pid={os.getpid()}\n"
        f"wechat_logged_in={bot_logged_in}\n"
        f"chat_mode={processor.chat_enabled}\n"
        f"tasks={len(processor.tasks)}\n"
        f"plugins={len(processor.plugin_loader.loaded_plugins)}"
    )


@command("chat", description="聊天模式开关", usage="/chat on|off|status")
async def cmd_chat(ctx: CommandContext) -> str:
    processor = ctx.processor
    if not ctx.args:
        return f"chat_mode={processor.chat_enabled}, webhook={'on' if processor.chat_webhook_url else 'off'}"

    action = ctx.args[0].lower()
    if action in {"on", "enable", "1"}:
        processor.chat_enabled = True
        return "chat mode enabled"
    if action in {"off", "disable", "0"}:
        processor.chat_enabled = False
        return "chat mode disabled"
    if action in {"status", "state"}:
        return f"chat_mode={processor.chat_enabled}, webhook={'on' if processor.chat_webhook_url else 'off'}"

    return "用法: /chat on|off|status"


@command("ask", description="聊天问答", usage="/ask <question>")
async def cmd_ask(ctx: CommandContext) -> str:
    question = " ".join(ctx.args).strip()
    if not question:
        return "用法: /ask 你的问题"
    return await ctx.processor._chat_reply(text=question, source_msg=ctx.msg)


@command("httpget", description="HTTP GET请求", usage="/httpget <url>")
async def cmd_httpget(ctx: CommandContext) -> str:
    processor = ctx.processor
    if not ctx.args:
        return "用法: /httpget https://your-server/path"

    url = ctx.args[0].strip()
    if not processor._is_url_allowed(url):
        return "URL 不在允许范围内，请配置 ROBOT_HTTP_ALLOWLIST"

    try:
        resp = await processor.http_client.get(url)
    except Exception as exc:
        return f"请求失败: {exc}"

    preview = resp.text[:1200]
    if len(resp.text) > 1200:
        preview += "\n...<truncated>"

    return f"status={resp.status_code}\nurl={url}\n{preview}"


@command("sendfile", description="发送服务器文件", usage="/sendfile <path>")
async def cmd_sendfile(ctx: CommandContext) -> str:
    from pathlib import Path
    processor = ctx.processor

    if not ctx.args:
        return "用法: /sendfile /absolute/path 或 /sendfile relative_name"

    candidate = Path(" ".join(ctx.args).strip())
    if not candidate.is_absolute():
        candidate = processor.download_dir / candidate

    if not candidate.exists() or not candidate.is_file():
        return f"文件不存在: {candidate}"

    ok = await processor.bot.send_file(str(candidate))
    return "文件发送成功" if ok else "文件发送失败"


@command("task", description="定时任务管理", usage="/task list|add|del|on|off|run")
async def cmd_task(ctx: CommandContext) -> str:
    processor = ctx.processor
    if not ctx.args:
        return _task_help_text()

    action = ctx.args[0].lower()

    if action == "list":
        if not processor.tasks:
            return "暂无定时任务"
        lines = ["定时任务列表:"]
        for task in sorted(processor.tasks.values(), key=lambda item: (item.time_hm, item.task_id)):
            status = "on" if task.enabled else "off"
            lines.append(f"- {task.task_id} [{status}] {task.time_hm} -> {task.command_text}")
        return "\n".join(lines)

    if action == "add":
        if len(ctx.args) < 3:
            return "用法: /task add HH:MM 命令文本"
        time_hm = ctx.args[1]
        command_text = " ".join(ctx.args[2:]).strip()
        try:
            task = processor.add_task(time_hm=time_hm, command_text=command_text)
        except Exception as exc:
            return f"添加失败: {exc}"
        return f"任务已添加: {task['task_id']}"

    if action in {"del", "delete", "rm"}:
        if len(ctx.args) < 2:
            return "用法: /task del task_id"
        ok = processor.delete_task(ctx.args[1])
        return "删除成功" if ok else "任务不存在"

    if action in {"on", "off"}:
        if len(ctx.args) < 2:
            return "用法: /task on|off task_id"
        ok = processor.set_task_enabled(ctx.args[1], enabled=(action == "on"))
        return "更新成功" if ok else "任务不存在"

    if action == "run":
        if len(ctx.args) < 2:
            return "用法: /task run task_id"
        ok = await processor.run_task_now(ctx.args[1])
        return "任务已执行" if ok else "任务不存在"

    return _task_help_text()


def _task_help_text() -> str:
    return (
        "task 子命令:\n"
        "/task list\n"
        "/task add HH:MM 命令文本\n"
        "/task del task_id\n"
        "/task on task_id\n"
        "/task off task_id\n"
        "/task run task_id"
    )


@command("plugins", description="查看插件状态", aliases=["plugin"])
async def cmd_plugins(ctx: CommandContext) -> str:
    status = ctx.processor.plugin_loader.get_status()
    lines = [
        f"插件目录: {status['plugins_dir']}",
        f"已加载: {status['loaded_count']} 个插件",
        f"命令数: {status['commands_count']}",
        f"处理器: {status['handlers_count']}",
    ]
    if status['loaded_plugins']:
        lines.append(f"插件列表: {', '.join(status['loaded_plugins'])}")
    if status['errors']:
        lines.append("加载错误:")
        for err in status['errors']:
            lines.append(f"  - {err['file']}: {err['error']}")
    return "\n".join(lines)


@command("reload", description="重新加载插件", hidden=True)
async def cmd_reload(ctx: CommandContext) -> str:
    ctx.processor.plugin_loader.reload_all()
    status = ctx.processor.plugin_loader.get_status()
    return f"已重新加载 {status['loaded_count']} 个插件, {status['commands_count']} 个命令"
