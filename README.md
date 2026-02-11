# WeChat FileHelper Bot API

基于微信文件传输助手的 Bot API 框架，接口设计遵循 [Telegram Bot API](https://core.telegram.org/bots/api) 标准。


![mmexport1770502998277.jpg](https://github-production-user-asset-6210df.s3.amazonaws.com/155826701/548213551-44ece52f-cb95-4db1-a74d-d1f6ecc48f54.jpg)


## 特性

- **Telegram Bot API 兼容** - 支持 sendMessage、sendDocument、getUpdates 等标准接口
- **插件系统** - 命令、消息处理器、HTTP 路由均可插件化
- **消息持久化** - SQLite 存储历史消息和文件元数据
- **自动文件下载** - 收到的文件自动保存到本地
- **定时任务** - 支持 Cron 式定时执行命令
- **稳定性增强** - 心跳检测、自动重连、会话保存

---

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
python main.py
```

服务启动后访问 `http://127.0.0.1:8000`

### 登录微信

```bash
# 获取二维码
curl http://127.0.0.1:8000/qr -o qr.png

# 用微信扫描二维码后检查状态
curl http://127.0.0.1:8000/login/status
```

---

## 项目结构

```
wechat-filehelper-api/
├── main.py              # FastAPI 入口
├── config.py            # 统一配置管理
├── background.py        # 后台任务 (监听/心跳/清理)
├── processor.py         # 命令处理器
├── direct_bot.py        # WeChat 协议实现
├── message_store.py     # SQLite 消息持久化
├── plugin_base.py       # 插件基类
├── plugin_loader.py     # 插件加载器
├── filehelper_sdk.py    # Python SDK
├── routes/
│   ├── bot.py           # Telegram Bot API 兼容
│   ├── wechat.py        # 微信扩展接口
│   └── files.py         # 文件管理接口
└── plugins/             # 插件目录 (文件夹形式)
    ├── builtin/         # 内置命令
    │   └── __init__.py
    ├── example/         # 示例插件
    │   └── __init__.py
    ├── webui/           # Web 管理界面
    │   ├── __init__.py
    │   └── index.html
    └── framework_api/   # 框架管理 API
        └── __init__.py
```

---

## Telegram Bot API

### 获取更新

```
GET /bot/getUpdates
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| offset | Integer | Optional | 起始更新 ID |
| limit | Integer | Optional | 限制数量 (1-100) |
| timeout | Integer | Optional | 长轮询超时 |

**Returns:** Array of Update objects

### 发送消息

```
POST /bot/sendMessage
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| text | String | Yes | 消息内容 |
| chat_id | String | Optional | 忽略 (仅 filehelper) |
| reply_to_message_id | String | Optional | 回复消息 ID |

### 发送文件

```
POST /bot/sendDocument
```

**JSON 模式:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| document | String | Yes | 文件路径 |
| caption | String | Optional | 文件说明 |

**Multipart 上传模式:**

```
POST /bot/sendDocument/upload
Content-Type: multipart/form-data
```

```bash
curl -X POST http://127.0.0.1:8000/bot/sendDocument/upload \
  -F "document=@/path/to/file.pdf" \
  -F "caption=文件说明"
```

### 发送图片

```
POST /bot/sendPhoto
POST /bot/sendPhoto/upload  # Multipart 上传
```

### 其他接口

| Endpoint | Description |
|----------|-------------|
| `GET /bot/getMe` | 获取 Bot 信息 |
| `GET /bot/getChat` | 获取 Chat 信息 |
| `GET /bot/getFile` | 获取文件信息 |
| `POST /bot/setWebhook` | 设置 Webhook |
| `POST /bot/deleteWebhook` | 删除 Webhook |
| `GET /bot/getWebhookInfo` | 获取 Webhook 信息 |
| `POST /bot/copyMessage` | 复制消息 |

---

## 登录接口

```
GET  /qr               # 获取登录二维码 (PNG 图片)
GET  /login/status     # 获取登录状态 (JSON)
GET  /webui            # Web 管理界面 (可视化登录)
```

### WebUI

访问 `http://127.0.0.1:8000/webui` 可使用 Web 界面：
- 自适应二维码展示
- 扫码状态实时反馈
- 服务器状态监控

---

## 微信扩展接口

### 会话管理

```
POST /wechat/session/save    # 保存会话
```

### 文件管理

```
GET    /downloads            # 下载目录文件列表
GET    /files/metadata       # 文件元数据 (数据库)
DELETE /files/{msg_id}       # 删除文件
POST   /files/cleanup        # 清理过期文件
```

### 消息存储

```
GET /store/stats             # 存储统计
GET /store/messages          # 查询历史消息
```

---

## 框架管理 API

> 这些接口由 `plugins/framework_api.py` 提供，删除该文件可禁用。

### 框架状态

```
GET  /framework/state        # 框架状态
POST /framework/chat_mode    # 开关聊天模式
POST /framework/execute      # 执行命令
```

### 定时任务

```
GET    /framework/tasks              # 任务列表
POST   /framework/tasks              # 添加任务
DELETE /framework/tasks/{id}         # 删除任务
POST   /framework/tasks/{id}/enabled # 启用/禁用
POST   /framework/tasks/{id}/run     # 立即执行
```

### 插件管理

```
GET  /plugins                # 已加载插件
POST /plugins/reload         # 重新加载插件
```

### 健康检查

```
GET /health                  # 健康状态
GET /stability               # 稳定性信息
```

### 调试

```
GET  /trace/status           # 追踪状态
GET  /trace/recent           # 最近追踪记录
POST /trace/clear            # 清除追踪
```

---

## 插件开发

### 快速开始

在 `plugins/` 目录创建 `.py` 文件即可：

```python
from plugin_base import command, CommandContext

@command("hello", description="打招呼")
async def hello(ctx: CommandContext) -> str:
    return f"Hello, {ctx.args[0] if ctx.args else 'World'}!"
```

### 完整示例

```python
from plugin_base import (
    command,           # 命令装饰器
    on_message,        # 消息处理器装饰器
    route,             # HTTP 路由装饰器
    on_load,           # 加载钩子
    on_unload,         # 卸载钩子
    CommandContext,    # 命令上下文
    get_bot,           # 获取 Bot 实例
    get_processor,     # 获取处理器
    get_config,        # 获取配置
)


# === 生命周期钩子 ===

@on_load
async def init():
    print("插件已加载")
    # 初始化资源、连接数据库等

@on_unload
async def cleanup():
    print("插件即将卸载")
    # 清理资源


# === 命令 ===

@command("greet", description="问候", aliases=["hi", "hello"])
async def cmd_greet(ctx: CommandContext) -> str:
    name = ctx.args[0] if ctx.args else "World"
    return f"Hello, {name}!"


# === 消息处理器 ===

@on_message(priority=100, name="spam_filter")
async def filter_spam(ctx: CommandContext) -> str | None:
    if "广告" in ctx.text:
        return "检测到垃圾消息"
    return None  # 返回 None 继续后续处理


# === HTTP 路由 ===

@route("GET", "/my-plugin/status", tags=["MyPlugin"])
async def status():
    config = get_config()
    return {
        "plugin": "my-plugin",
        "server": config.server_label,
    }

@route("POST", "/my-plugin/action", tags=["MyPlugin"])
async def action(text: str = ""):
    bot = get_bot()
    await bot.send_text(text)
    return {"sent": True}
```

### 可用 API

| 函数 | 说明 |
|------|------|
| `get_bot()` | 获取 WeChatHelperBot 实例 |
| `get_processor()` | 获取 CommandProcessor 实例 |
| `get_config()` | 获取配置实例 |

### CommandContext 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `text` | str | 原始文本 |
| `command` | str | 命令名 (不含 /) |
| `args` | list[str] | 参数列表 |
| `msg` | dict | 原始消息对象 |
| `msg_id` | str | 消息 ID |
| `is_command` | bool | 是否为命令调用 |
| `bot` | Any | Bot 实例 |
| `processor` | Any | 处理器实例 |

### 热重载

```bash
curl -X POST http://127.0.0.1:8000/plugins/reload
```

---

## Python SDK

```python
from filehelper_sdk import Bot

bot = Bot("http://127.0.0.1:8000")

# 发送消息
bot.send_message(text="Hello!")

# 发送文件
bot.send_document(document="/path/to/file.pdf", caption="附件")

# 获取更新
updates = bot.get_updates()
for update in updates:
    print(update.message.text)
```

### 轮询模式

```python
from filehelper_sdk import Bot, Updater, Update

bot = Bot("http://127.0.0.1:8000")

def handle_message(update: Update):
    if update.message.text == "ping":
        bot.send_message(text="pong")

updater = Updater(bot)
updater.add_handler(handle_message)
updater.start_polling()
```

---

## 环境变量

### 基础配置

| Variable | Default | Description |
|----------|---------|-------------|
| `WECHAT_ENTRY_HOST` | `szfilehelper.weixin.qq.com` | 微信入口主机 |
| `DOWNLOAD_DIR` | `./downloads` | 下载目录 |
| `MESSAGE_DB_PATH` | `./messages.db` | 数据库路径 |
| `PLUGINS_DIR` | `./plugins` | 插件目录 |

### 稳定性

| Variable | Default | Description |
|----------|---------|-------------|
| `HEARTBEAT_INTERVAL` | `30` | 心跳间隔 (秒) |
| `RECONNECT_DELAY` | `5` | 重连延迟 (秒) |
| `MAX_RECONNECT_ATTEMPTS` | `10` | 最大重连次数 |

### 文件管理

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTO_DOWNLOAD` | `true` | 自动下载文件 |
| `FILE_DATE_SUBDIR` | `true` | 按日期分目录 |
| `FILE_RETENTION_DAYS` | `0` | 文件保留天数 (0=永久) |

### Webhook

| Variable | Default | Description |
|----------|---------|-------------|
| `MESSAGE_WEBHOOK_URL` | - | 消息推送 Webhook |
| `CHATBOT_WEBHOOK_URL` | - | AI 聊天 Webhook |
| `CHATBOT_ENABLED` | `false` | 启用 AI 聊天 |

### 调试

| Variable | Default | Description |
|----------|---------|-------------|
| `WECHAT_TRACE_ENABLED` | `true` | 启用请求追踪 |
| `WECHAT_TRACE_REDACT` | `true` | 脱敏敏感数据 |

---

## 不支持的 Telegram 方法

| Method | Reason |
|--------|--------|
| forwardMessage | 微信不支持转发标记 |
| editMessageText | 微信不支持编辑已发送消息 |
| deleteMessage | 不支持撤回 (超时后) |
| sendLocation | 文件助手不支持位置 |
| sendContact | 文件助手不支持联系人 |
| sendPoll | 微信不支持投票 |
| Inline Mode | 微信不支持 |
| Payments | 微信不支持 |

---

内置指令效果

![Screenshot_2026-02-08-05-34-23-944_com.tencent.mm-edit.jpg](https://github.com/user-attachments/assets/dff3abbc-bf1e-461c-b954-64315792b95d)

## License

MIT
