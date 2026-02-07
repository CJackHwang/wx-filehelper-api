"""
统一配置模块 - 所有环境变量和配置项集中管理

使用方式:
    from config import settings

    print(settings.download_dir)
    print(settings.heartbeat_interval)
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _env_bool(key: str, default: bool = False) -> bool:
    """解析布尔类型环境变量"""
    val = os.getenv(key, "").strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    """解析整数类型环境变量"""
    val = os.getenv(key, "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_list(key: str, sep: str = ",") -> list[str]:
    """解析列表类型环境变量"""
    val = os.getenv(key, "").strip()
    if not val:
        return []
    return [item.strip() for item in val.split(sep) if item.strip()]


@dataclass
class Settings:
    """应用配置"""

    # === 基础配置 ===
    app_name: str = "WeChat FileHelper Protocol Bot"
    version: str = "2.0.0"
    host: str = "0.0.0.0"
    port: int = 8000

    # === 微信配置 ===
    wechat_entry_host: str = field(
        default_factory=lambda: os.getenv("WECHAT_ENTRY_HOST", "szfilehelper.weixin.qq.com")
    )

    # === 文件存储 ===
    download_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DOWNLOAD_DIR", os.path.join(os.getcwd(), "downloads")))
    )
    file_date_subdir: bool = field(default_factory=lambda: _env_bool("FILE_DATE_SUBDIR", True))
    auto_download: bool = field(default_factory=lambda: _env_bool("AUTO_DOWNLOAD", True))
    file_retention_days: int = field(default_factory=lambda: _env_int("FILE_RETENTION_DAYS", 0))
    max_upload_size: int = field(default_factory=lambda: _env_int("MAX_UPLOAD_SIZE", 25 * 1024 * 1024))  # 25MB

    # === 数据库 ===
    message_db_path: Path = field(
        default_factory=lambda: Path(os.getenv("MESSAGE_DB_PATH", os.path.join(os.getcwd(), "messages.db")))
    )

    # === 插件 ===
    plugins_dir: Path = field(
        default_factory=lambda: Path(os.getenv("PLUGINS_DIR", os.path.join(os.getcwd(), "plugins")))
    )

    # === 定时任务 ===
    task_file: Path = field(
        default_factory=lambda: Path(os.getenv("ROBOT_TASK_FILE", os.path.join(os.getcwd(), "scheduled_tasks.json")))
    )

    # === 稳定性 ===
    heartbeat_interval: int = field(default_factory=lambda: _env_int("HEARTBEAT_INTERVAL", 30))
    reconnect_delay: int = field(default_factory=lambda: _env_int("RECONNECT_DELAY", 5))
    max_reconnect_attempts: int = field(default_factory=lambda: _env_int("MAX_RECONNECT_ATTEMPTS", 10))

    # === Webhook ===
    message_webhook_url: str = field(default_factory=lambda: os.getenv("MESSAGE_WEBHOOK_URL", "").strip())
    message_webhook_timeout: int = field(default_factory=lambda: _env_int("MESSAGE_WEBHOOK_TIMEOUT", 10))

    # === 聊天机器人 ===
    chatbot_enabled: bool = field(default_factory=lambda: _env_bool("CHATBOT_ENABLED", False))
    chatbot_webhook_url: str = field(default_factory=lambda: os.getenv("CHATBOT_WEBHOOK_URL", "").strip())
    chatbot_timeout: int = field(default_factory=lambda: _env_int("CHATBOT_TIMEOUT", 20))

    # === HTTP 安全 ===
    http_allowlist: list[str] = field(default_factory=lambda: _env_list("ROBOT_HTTP_ALLOWLIST"))

    # === Trace ===
    trace_enabled: bool = field(default_factory=lambda: _env_bool("WECHAT_TRACE_ENABLED", True))
    trace_redact: bool = field(default_factory=lambda: _env_bool("WECHAT_TRACE_REDACT", True))
    trace_max_body: int = field(default_factory=lambda: _env_int("WECHAT_TRACE_MAX_BODY", 4096))
    trace_dir: Path = field(
        default_factory=lambda: Path(os.getenv("WECHAT_TRACE_DIR", os.path.join(os.getcwd(), "trace_logs")))
    )

    # === 服务器标识 ===
    server_label: str = field(default_factory=lambda: os.getenv("ROBOT_SERVER_LABEL", "") or __import__("socket").gethostname())

    # === 登录回调 ===
    login_callback_url: str = field(default_factory=lambda: os.getenv("LOGIN_CALLBACK_URL", "").strip())

    def __post_init__(self):
        """初始化后处理 - 确保目录存在"""
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        if self.trace_enabled:
            self.trace_dir.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典 (用于调试)"""
        return {
            "app_name": self.app_name,
            "version": self.version,
            "wechat_entry_host": self.wechat_entry_host,
            "download_dir": str(self.download_dir),
            "file_date_subdir": self.file_date_subdir,
            "auto_download": self.auto_download,
            "file_retention_days": self.file_retention_days,
            "message_db_path": str(self.message_db_path),
            "plugins_dir": str(self.plugins_dir),
            "heartbeat_interval": self.heartbeat_interval,
            "max_reconnect_attempts": self.max_reconnect_attempts,
            "chatbot_enabled": self.chatbot_enabled,
            "trace_enabled": self.trace_enabled,
            "server_label": self.server_label,
        }


# 全局配置实例
settings = Settings()
