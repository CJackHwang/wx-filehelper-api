# WeChat FileHelper Bot API

基于微信文件传输助手的 Bot API，接口设计遵循 [Telegram Bot API](https://core.telegram.org/bots/api) 标准。

## 快速开始

```bash
pip install -r requirements.txt
python main.py
```

服务启动后访问 `http://127.0.0.1:8000`

## 登录

```bash
# 获取二维码
curl http://127.0.0.1:8000/wechat/qr -o qr.png

# 检查登录状态
curl http://127.0.0.1:8000/wechat/login/status
```

---

## Available Methods

### Getting Updates

#### getUpdates

```
GET /bot/getUpdates
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| offset | Integer | Optional | Identifier of the first update to be returned |
| limit | Integer | Optional | Limits the number of updates (1-100, default 100) |
| timeout | Integer | Optional | Timeout in seconds for long polling |
| allowed_updates | Array of String | Optional | List of update types to receive |

**Returns:** Array of [Update](#update) objects

---

### Available Methods

#### getMe

```
GET /bot/getMe
```

Returns basic information about the bot.

**Returns:** [User](#user) object

---

#### sendMessage

```
POST /bot/sendMessage
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| chat_id | Integer or String | Optional | Ignored (only filehelper) |
| text | String | Yes | Text of the message |
| parse_mode | String | Optional | Ignored |
| reply_to_message_id | Integer | Optional | Message ID to reply to |
| disable_notification | Boolean | Optional | Ignored |

**Returns:** [Message](#message) object

---

#### sendPhoto

```
POST /bot/sendPhoto
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| chat_id | Integer or String | Optional | Ignored |
| photo | String | Yes | File path to the photo |
| caption | String | Optional | Photo caption |
| reply_to_message_id | Integer | Optional | Message ID to reply to |

**Returns:** [Message](#message) object

---

#### sendDocument

```
POST /bot/sendDocument
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| chat_id | Integer or String | Optional | Ignored |
| document | String | Yes | File path to the document |
| caption | String | Optional | Document caption |
| reply_to_message_id | Integer | Optional | Message ID to reply to |

**Returns:** [Message](#message) object

---

#### getFile

```
GET /bot/getFile
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| file_id | String | Yes | File identifier |

**Returns:** [File](#file) object

---

#### getChat

```
GET /bot/getChat
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| chat_id | Integer or String | Optional | Ignored |

**Returns:** [Chat](#chat) object

---

#### setWebhook

```
POST /bot/setWebhook
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| url | String | Yes | HTTPS URL to send updates to |
| certificate | String | Optional | Ignored |
| ip_address | String | Optional | Ignored |
| max_connections | Integer | Optional | Ignored |
| allowed_updates | Array of String | Optional | Ignored |
| drop_pending_updates | Boolean | Optional | Ignored |
| secret_token | String | Optional | Ignored |

**Returns:** True on success

---

#### deleteWebhook

```
POST /bot/deleteWebhook
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| drop_pending_updates | Boolean | Optional | Ignored |

**Returns:** True on success

---

#### getWebhookInfo

```
GET /bot/getWebhookInfo
```

**Returns:** [WebhookInfo](#webhookinfo) object

---

## Types

### Update

| Field | Type | Description |
|-------|------|-------------|
| update_id | Integer | Update's unique identifier |
| message | Message | Optional. New incoming message |

### User

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | User identifier |
| is_bot | Boolean | True if this is a bot |
| first_name | String | User's first name |
| username | String | Optional. Username |

### Chat

| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Chat identifier |
| type | String | Type of chat: "private" |
| first_name | String | Optional. First name |
| username | String | Optional. Username |

### Message

| Field | Type | Description |
|-------|------|-------------|
| message_id | String | Unique message identifier |
| date | Integer | Unix timestamp |
| text | String | Optional. Text content |
| document | Document | Optional. Document info |
| reply_to_message_id | String | Optional. Original message ID |

### Document

| Field | Type | Description |
|-------|------|-------------|
| file_id | String | File identifier |
| file_unique_id | String | Unique file identifier |
| file_name | String | Optional. Original filename |
| file_size | Integer | Optional. File size in bytes |

### File

| Field | Type | Description |
|-------|------|-------------|
| file_id | String | File identifier |
| file_unique_id | String | Unique file identifier |
| file_size | Integer | Optional. File size |
| file_path | String | File path for downloading |

### WebhookInfo

| Field | Type | Description |
|-------|------|-------------|
| url | String | Webhook URL |
| has_custom_certificate | Boolean | Always false |
| pending_update_count | Integer | Number of pending updates |

---

## WeChat 扩展接口

以下接口为微信特有功能，Telegram 无对应接口。

### 登录

```
GET /wechat/qr                    # 获取登录二维码 (返回 PNG)
GET /wechat/login/status          # 获取登录状态
POST /wechat/session/save         # 保存会话
```

### 框架功能

```
GET /framework/state              # 框架状态
POST /framework/execute           # 执行命令
GET /framework/tasks              # 定时任务列表
POST /framework/tasks             # 添加定时任务
DELETE /framework/tasks/{id}      # 删除定时任务
```

### 消息存储

```
GET /store/stats                  # 存储统计
GET /store/messages               # 查询历史消息
```

### 文件管理

```
GET /downloads                    # 下载目录文件列表
GET /files/metadata               # 文件元数据
DELETE /files/{msg_id}            # 删除文件
POST /files/cleanup               # 清理过期文件
```

### 插件管理

```
GET /plugins                      # 已加载插件
POST /plugins/reload              # 重新加载插件
```

### 健康检查

```
GET /health                       # 健康状态
GET /stability                    # 稳定性信息
```

---

## 不支持的 Telegram 方法

以下方法因微信限制无法实现：

| Method | Reason |
|--------|--------|
| forwardMessage | 微信不支持转发 |
| copyMessage | 微信不支持复制 |
| editMessageText | 微信不支持编辑已发送消息 |
| deleteMessage | 微信不支持撤回 (超时后) |
| sendLocation | 微信文件助手不支持位置 |
| sendContact | 微信文件助手不支持联系人 |
| sendPoll | 微信不支持投票 |
| sendDice | 微信不支持骰子 |
| Inline Mode | 微信不支持 |
| Payments | 微信不支持 |
| Games | 微信不支持 |

---

## Python SDK

```python
from filehelper_sdk import Bot

bot = Bot("http://127.0.0.1:8000")

# Send message
bot.send_message(text="Hello!")

# Send document
bot.send_document(document="/path/to/file.pdf", caption="Check this out")

# Get updates
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
        bot.send_message(text="pong", reply_to_message_id=update.message.message_id)

updater = Updater(bot)
updater.add_handler(handle_message)
updater.start_polling()
```

---

## 插件开发

在 `plugins/` 目录创建 `.py` 文件：

```python
from plugin_base import command, CommandContext

@command("hello", description="Say hello")
async def cmd_hello(ctx: CommandContext) -> str:
    return f"Hello, {ctx.args[0] if ctx.args else 'World'}!"
```

重启服务或调用 `POST /plugins/reload` 生效。

---

## 环境变量

| Variable | Default | Description |
|----------|---------|-------------|
| `WECHAT_ENTRY_HOST` | `szfilehelper.weixin.qq.com` | WeChat entry host |
| `DOWNLOAD_DIR` | `./downloads` | Download directory |
| `MESSAGE_DB_PATH` | `./messages.db` | SQLite database path |
| `PLUGINS_DIR` | `./plugins` | Plugins directory |
| `MESSAGE_WEBHOOK_URL` | - | Webhook URL for updates |
| `CHATBOT_WEBHOOK_URL` | - | Webhook for chat replies |
| `HEARTBEAT_INTERVAL` | `30` | Heartbeat interval (seconds) |
| `MAX_RECONNECT_ATTEMPTS` | `10` | Max reconnection attempts |

---

## License

MIT
