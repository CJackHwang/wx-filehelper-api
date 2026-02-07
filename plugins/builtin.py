"""
内置命令插件 - 框架自带的基础命令
"""

import os
import platform
import time
from datetime import datetime

from plugin_base import command, CommandContext, get_config


# === 菜单与导航 ===

@command("start", description="开始使用", aliases=["menu", "主菜单"])
async def cmd_start(ctx: CommandContext) -> str:
    """主菜单 - Telegram /start"""
    config = get_config()
    return f"""🤖 {config.app_name} v{config.version}

欢迎使用文件传输助手机器人！

【Telegram 标准命令】
/help - 命令列表
/settings - 查看设置
/about - 关于本 Bot

【快捷入口】
/status - 服务器状态
/chat on - 开启 AI 聊天
/task list - 定时任务
/sendfile - 发送文件

发送任意文字开始对话 ✨"""


# === Telegram 标准命令 ===


@command("settings", description="查看设置")
async def cmd_settings(ctx: CommandContext) -> str:
    """设置面板 - Telegram 标准命令"""
    processor = ctx.processor
    config = get_config()

    return f"""⚙️ 当前设置

【聊天模式】
状态: {'开启' if processor.chat_enabled else '关闭'}
Webhook: {'已配置' if processor.chat_webhook_url else '未配置'}
切换: /chat on|off

【定时任务】
任务数: {len(processor.tasks)}
管理: /task list

【文件管理】
下载目录: {config.download_dir}
自动下载: {'是' if config.auto_download else '否'}
按日期分目录: {'是' if config.file_date_subdir else '否'}
保留天数: {config.file_retention_days or '永久'}

【服务器】
标签: {processor.server_label}
心跳间隔: {config.heartbeat_interval}s
重连延迟: {config.reconnect_delay}s"""


@command("cancel", description="取消当前操作")
async def cmd_cancel(ctx: CommandContext) -> str:
    """取消操作 - Telegram 标准命令"""
    return "没有正在进行的操作。"


@command("about", description="关于本 Bot")
async def cmd_about(ctx: CommandContext) -> str:
    """关于信息 - Telegram 标准命令"""
    config = get_config()
    return f"""🤖 {config.app_name}

基于微信文件传输助手的 Bot API 框架
兼容 Telegram Bot API 标准

版本: {config.version}
项目: https://github.com/CJackHwang/wx-filehelper-api

【特性】
• Telegram Bot API 兼容
• 插件系统 (命令/消息处理/HTTP路由)
• 消息持久化 (SQLite)
• 自动文件下载
• 定时任务调度
• 心跳检测与自动重连"""


@command("version", description="版本信息", aliases=["ver", "v"])
async def cmd_version(ctx: CommandContext) -> str:
    """版本信息 - Telegram 标准命令"""
    config = get_config()
    return f"{config.app_name} v{config.version}"


@command("help", description="命令列表", aliases=["h", "?"])
async def cmd_help(ctx: CommandContext) -> str:
    """命令列表 - 简洁版"""
    return """📖 命令列表

【Telegram 标准】
/start - 开始使用
/help - 命令列表
/settings - 查看设置
/cancel - 取消操作
/about - 关于本 Bot
/version - 版本信息

【核心功能】
/status - 服务器状态
/chat on|off - 聊天模式
/ask <问题> - AI 问答
/task list - 定时任务
/sendfile <路径> - 发送文件

【管理】
/plugins - 插件状态
/reload - 重载插件"""


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


@command("download", description="文件接收开关", usage="/download on|off|status")
async def cmd_download(ctx: CommandContext) -> str:
    """控制自动文件下载功能"""
    from main import background_tasks

    if not ctx.args:
        status = "开启" if background_tasks.auto_download else "关闭"
        return f"文件自动接收: {status}\n用法: /download on|off"

    action = ctx.args[0].lower()
    if action in {"on", "enable", "1"}:
        background_tasks.auto_download = True
        return "文件自动接收已开启"
    if action in {"off", "disable", "0"}:
        background_tasks.auto_download = False
        return "文件自动接收已关闭"
    if action in {"status", "state"}:
        status = "开启" if background_tasks.auto_download else "关闭"
        return f"文件自动接收: {status}"

    return "用法: /download on|off|status"


@command("debug", description="调试文件传输", usage="/debug", hidden=True)
async def cmd_debug(ctx: CommandContext) -> str:
    """调试命令 - 测试图片和文件发送"""
    from pathlib import Path

    bot = ctx.bot
    results = []

    # 发送测试图片
    test_image = Path(__file__).parent.parent / "bishengke-test.jpg"
    if test_image.exists():
        ok = await bot.send_file(str(test_image))
        results.append(f"图片发送: {'成功' if ok else '失败'}")
    else:
        results.append(f"图片不存在: {test_image}")

    # 创建并发送测试文本文件
    test_txt = Path(__file__).parent.parent / "downloads" / "郑重声明.txt"
    test_txt.parent.mkdir(parents=True, exist_ok=True)
    test_txt.write_text(
        "马爸爸我给你腾讯充那么多钱你别搞我仓库真的求你了",
        encoding="utf-8"
    )
    ok = await bot.send_file(str(test_txt))
    results.append(f"文件发送: {'成功' if ok else '失败'}")

    return "调试完成\n" + "\n".join(results)
