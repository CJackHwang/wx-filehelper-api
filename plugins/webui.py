"""
WebUI 插件 - 提供 Web 管理界面

功能:
- 登录二维码展示 (自适应窗口)
- 登录状态实时刷新
- 服务器状态监控
- 定时任务管理

路由:
- GET /webui - 主页面
- GET /webui/qr - 二维码 (Base64 JSON)
- GET /webui/status - 状态 JSON

可通过删除此文件禁用 WebUI。
"""

import base64
import time
from typing import Any

from fastapi.responses import HTMLResponse

from plugin_base import route, get_bot, get_processor, get_config


# === API 路由 ===


@route("GET", "/webui/qr", tags=["WebUI"])
async def webui_qr() -> dict[str, Any]:
    """获取二维码 (Base64 编码, 快速响应)"""
    bot = get_bot()

    # 快速检查: 仅检查内存状态，不做网络请求
    if bot._has_auth() and bot.is_logged_in:
        return {
            "logged_in": True,
            "qr_base64": None,
            "message": "已登录",
        }

    try:
        # 使用 skip_login_check=True 避免重复检查
        png_bytes = await bot.get_login_qr(skip_login_check=True)
        if not png_bytes:
            return {
                "logged_in": True,
                "qr_base64": None,
                "message": "已登录",
            }

        qr_base64 = base64.b64encode(png_bytes).decode("ascii")
        return {
            "logged_in": False,
            "qr_base64": qr_base64,
            "uuid": bot.uuid,
            "uuid_age": int(time.time() - bot.uuid_ts) if bot.uuid_ts else 0,
            "message": "请扫码登录",
        }
    except Exception as exc:
        return {
            "logged_in": False,
            "qr_base64": None,
            "error": str(exc),
            "message": f"获取二维码失败: {exc}",
        }


@route("GET", "/webui/status", tags=["WebUI"])
async def webui_status(poll_login: bool = False) -> dict[str, Any]:
    """
    获取服务器状态

    Args:
        poll_login: 是否主动轮询登录状态 (未登录时自动触发)
    """
    bot = get_bot()
    processor = get_processor()
    config = get_config()

    # 未登录时自动触发登录轮询
    if poll_login or not bot.is_logged_in:
        await bot.check_login_status(poll=True)

    # 获取登录状态详情
    login_detail = await bot.get_login_status_detail()

    uptime = int(time.time() - processor.started_at)

    return {
        "app_name": config.app_name,
        "version": config.version,
        "uptime": uptime,
        "uptime_str": _format_uptime(uptime),
        "logged_in": login_detail.get("logged_in", False),
        "login_status": login_detail.get("status", "unknown"),
        "login_code": login_detail.get("code", 0),
        "has_uuid": login_detail.get("has_uuid", False),
        "uuid_age": login_detail.get("uuid_age_seconds"),
        "chat_enabled": processor.chat_enabled,
        "tasks_count": len(processor.tasks),
        "plugins_count": len(processor.plugin_loader.loaded_plugins),
        "entry_host": login_detail.get("entry_host", ""),
        # 登录状态中文描述
        "login_status_text": _get_login_status_text(login_detail),
    }


@route("GET", "/webui", tags=["WebUI"])
async def webui_page() -> HTMLResponse:
    """WebUI 主页面"""
    config = get_config()
    html = _generate_html(config.app_name, config.version)
    return HTMLResponse(content=html, status_code=200)


# === 辅助函数 ===


def _format_uptime(seconds: int) -> str:
    """格式化运行时间"""
    if seconds < 60:
        return f"{seconds}秒"
    if seconds < 3600:
        return f"{seconds // 60}分{seconds % 60}秒"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}小时{minutes}分"


def _get_login_status_text(login_detail: dict) -> str:
    """根据登录状态返回中文描述"""
    if login_detail.get("logged_in"):
        return "已登录"

    code = login_detail.get("code", 0)
    status = login_detail.get("status", "")

    if code == 201:
        return "已扫码，请在手机上确认"
    if code == 408:
        return "等待扫码..."
    if status == "qr_expired":
        return "二维码已过期，请刷新"
    if status == "need_qr":
        return "请扫描二维码"
    if status == "qr_ready":
        return "二维码已就绪"

    return "等待登录"


def _generate_html(app_name: str, version: str) -> str:
    """生成 WebUI HTML"""
    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{app_name} - WebUI</title>
    <style>
        :root {{
            --bg-color: #0f0f0f;
            --card-bg: #1a1a1a;
            --text-color: #e0e0e0;
            --text-muted: #888;
            --accent: #4a9eff;
            --success: #4ade80;
            --warning: #fbbf24;
            --error: #f87171;
        }}

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: var(--bg-color);
            color: var(--text-color);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 20px;
        }}

        .container {{
            width: 100%;
            max-width: 800px;
        }}

        header {{
            text-align: center;
            margin-bottom: 30px;
        }}

        h1 {{
            font-size: 1.5rem;
            font-weight: 600;
            margin-bottom: 5px;
        }}

        .version {{
            color: var(--text-muted);
            font-size: 0.875rem;
        }}

        .card {{
            background: var(--card-bg);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 20px;
        }}

        .card-title {{
            font-size: 0.875rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 16px;
        }}

        /* QR Code Section */
        .qr-container {{
            display: flex;
            flex-direction: column;
            align-items: center;
        }}

        .qr-wrapper {{
            background: #fff;
            padding: 16px;
            border-radius: 12px;
            margin-bottom: 16px;
        }}

        #qr-image {{
            display: block;
            width: min(280px, 60vw);
            height: min(280px, 60vw);
            object-fit: contain;
        }}

        .qr-placeholder {{
            width: min(280px, 60vw);
            height: min(280px, 60vw);
            display: flex;
            align-items: center;
            justify-content: center;
            color: #333;
            font-size: 0.875rem;
        }}

        .status-badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 0.875rem;
            font-weight: 500;
        }}

        .status-badge.logged-in {{
            background: rgba(74, 222, 128, 0.15);
            color: var(--success);
        }}

        .status-badge.waiting {{
            background: rgba(251, 191, 36, 0.15);
            color: var(--warning);
        }}

        .status-badge.error {{
            background: rgba(248, 113, 113, 0.15);
            color: var(--error);
        }}

        .status-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: currentColor;
        }}

        .status-dot.pulse {{
            animation: pulse 2s infinite;
        }}

        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.5; }}
        }}

        /* Status Grid */
        .status-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 16px;
        }}

        .stat-item {{
            text-align: center;
            padding: 12px;
            background: rgba(255,255,255,0.03);
            border-radius: 8px;
        }}

        .stat-value {{
            font-size: 1.5rem;
            font-weight: 600;
            color: var(--accent);
        }}

        .stat-label {{
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-top: 4px;
        }}

        /* Refresh indicator */
        .refresh-info {{
            text-align: center;
            color: var(--text-muted);
            font-size: 0.75rem;
            margin-top: 12px;
        }}

        /* Loading spinner */
        .spinner {{
            width: 40px;
            height: 40px;
            border: 3px solid rgba(255,255,255,0.1);
            border-top-color: var(--accent);
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }}

        @keyframes spin {{
            to {{ transform: rotate(360deg); }}
        }}

        /* Actions */
        .actions {{
            display: flex;
            gap: 12px;
            justify-content: center;
            margin-top: 16px;
        }}

        .btn {{
            padding: 10px 20px;
            border: none;
            border-radius: 8px;
            font-size: 0.875rem;
            cursor: pointer;
            transition: all 0.2s;
        }}

        .btn-primary {{
            background: var(--accent);
            color: #fff;
        }}

        .btn-primary:hover {{
            background: #3a8aee;
        }}

        .btn-secondary {{
            background: rgba(255,255,255,0.1);
            color: var(--text-color);
        }}

        .btn-secondary:hover {{
            background: rgba(255,255,255,0.15);
        }}

        footer {{
            margin-top: auto;
            padding-top: 20px;
            text-align: center;
            color: var(--text-muted);
            font-size: 0.75rem;
        }}

        footer a {{
            color: var(--accent);
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>{app_name}</h1>
            <span class="version">v{version}</span>
        </header>

        <!-- Login Card -->
        <div class="card">
            <div class="card-title">登录状态</div>
            <div class="qr-container">
                <div class="qr-wrapper" id="qr-wrapper">
                    <div class="qr-placeholder" id="qr-placeholder">
                        <div class="spinner"></div>
                    </div>
                    <img id="qr-image" style="display:none;" alt="Login QR Code">
                </div>
                <div id="status-badge" class="status-badge waiting">
                    <span class="status-dot pulse"></span>
                    <span id="status-text">加载中...</span>
                </div>
                <div class="refresh-info" id="refresh-info"></div>
            </div>
            <div class="actions">
                <button class="btn btn-primary" onclick="refreshQR()">刷新二维码</button>
                <button class="btn btn-secondary" onclick="refreshStatus()">刷新状态</button>
            </div>
        </div>

        <!-- Status Card -->
        <div class="card">
            <div class="card-title">服务器状态</div>
            <div class="status-grid">
                <div class="stat-item">
                    <div class="stat-value" id="stat-uptime">--</div>
                    <div class="stat-label">运行时间</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="stat-tasks">--</div>
                    <div class="stat-label">定时任务</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="stat-plugins">--</div>
                    <div class="stat-label">插件数量</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="stat-chat">--</div>
                    <div class="stat-label">聊天模式</div>
                </div>
            </div>
        </div>
    </div>

    <footer>
        <p>Telegram Bot API Compatible &middot; <a href="/docs" target="_blank">API Docs</a></p>
    </footer>

    <script>
        let autoRefreshTimer = null;
        let qrRefreshTimer = null;

        async function fetchJSON(url) {{
            const resp = await fetch(url);
            return resp.json();
        }}

        async function refreshQR() {{
            const placeholder = document.getElementById('qr-placeholder');
            const img = document.getElementById('qr-image');
            const badge = document.getElementById('status-badge');
            const statusText = document.getElementById('status-text');
            const refreshInfo = document.getElementById('refresh-info');

            // Show loading
            placeholder.innerHTML = '<div class="spinner"></div>';
            placeholder.style.display = 'flex';
            img.style.display = 'none';

            try {{
                const data = await fetchJSON('/webui/qr');

                if (data.logged_in) {{
                    placeholder.innerHTML = '✓ 已登录';
                    placeholder.style.display = 'flex';
                    img.style.display = 'none';
                    badge.className = 'status-badge logged-in';
                    badge.innerHTML = '<span class="status-dot"></span><span>已登录</span>';
                    refreshInfo.textContent = '';
                    stopQRAutoRefresh();
                }} else if (data.qr_base64) {{
                    img.src = 'data:image/png;base64,' + data.qr_base64;
                    img.style.display = 'block';
                    placeholder.style.display = 'none';
                    badge.className = 'status-badge waiting';
                    badge.innerHTML = '<span class="status-dot pulse"></span><span>' + data.message + '</span>';

                    const age = data.uuid_age || 0;
                    const remaining = Math.max(0, 240 - age);
                    refreshInfo.textContent = `二维码有效期: ${{remaining}}秒`;

                    startQRAutoRefresh();
                }} else {{
                    placeholder.innerHTML = data.message || '获取失败';
                    placeholder.style.display = 'flex';
                    img.style.display = 'none';
                    badge.className = 'status-badge error';
                    badge.innerHTML = '<span class="status-dot"></span><span>获取失败</span>';
                }}
            }} catch (e) {{
                placeholder.innerHTML = '网络错误';
                placeholder.style.display = 'flex';
                img.style.display = 'none';
                badge.className = 'status-badge error';
                badge.innerHTML = '<span class="status-dot"></span><span>连接失败</span>';
            }}
        }}

        async function refreshStatus() {{
            try {{
                const data = await fetchJSON('/webui/status');

                document.getElementById('stat-uptime').textContent = data.uptime_str || '--';
                document.getElementById('stat-tasks').textContent = data.tasks_count ?? '--';
                document.getElementById('stat-plugins').textContent = data.plugins_count ?? '--';
                document.getElementById('stat-chat').textContent = data.chat_enabled ? '开启' : '关闭';

                // Update login status if logged in
                if (data.logged_in) {{
                    const badge = document.getElementById('status-badge');
                    const placeholder = document.getElementById('qr-placeholder');
                    const img = document.getElementById('qr-image');

                    placeholder.innerHTML = '✓ 已登录';
                    placeholder.style.display = 'flex';
                    img.style.display = 'none';
                    badge.className = 'status-badge logged-in';
                    badge.innerHTML = '<span class="status-dot"></span><span>已登录</span>';
                    document.getElementById('refresh-info').textContent = '';
                    stopQRAutoRefresh();
                }}
            }} catch (e) {{
                console.error('Status fetch failed:', e);
            }}
        }}

        function startQRAutoRefresh() {{
            if (qrRefreshTimer) return;
            let pollInterval = 3000;

            async function poll() {{
                try {{
                    const data = await fetchJSON('/webui/status');
                    const badge = document.getElementById('status-badge');
                    const info = document.getElementById('refresh-info');

                    if (data.logged_in) {{
                        const placeholder = document.getElementById('qr-placeholder');
                        const img = document.getElementById('qr-image');
                        placeholder.innerHTML = '✓ 已登录';
                        placeholder.style.display = 'flex';
                        img.style.display = 'none';
                        badge.className = 'status-badge logged-in';
                        badge.innerHTML = '<span class="status-dot"></span><span>已登录</span>';
                        info.textContent = '';
                        stopQRAutoRefresh();
                        refreshStatus();
                        return;
                    }}

                    const statusText = data.login_status_text || '等待扫码';
                    badge.innerHTML = '<span class="status-dot pulse"></span><span>' + statusText + '</span>';

                    if (data.login_code === 201) {{
                        badge.className = 'status-badge waiting';
                        info.textContent = '请在手机上点击确认';
                        pollInterval = 1000;
                    }} else {{
                        const match = info.textContent.match(/(\d+)/);
                        if (match) {{
                            const remaining = parseInt(match[1]) - Math.round(pollInterval/1000);
                            if (remaining <= 0) {{
                                refreshQR();
                                return;
                            }}
                            info.textContent = `二维码有效期: ${{remaining}}秒`;
                        }}
                        pollInterval = 3000;
                    }}
                }} catch (e) {{
                    console.error('Poll failed:', e);
                }}
                qrRefreshTimer = setTimeout(poll, pollInterval);
            }}
            qrRefreshTimer = setTimeout(poll, pollInterval);
        }}

        function stopQRAutoRefresh() {{
            if (qrRefreshTimer) {{
                clearTimeout(qrRefreshTimer);
                qrRefreshTimer = null;
            }}
        }}

        // Initial load
        refreshQR();
        refreshStatus();

        // Auto refresh status every 10s
        setInterval(refreshStatus, 10000);
    </script>
</body>
</html>'''
