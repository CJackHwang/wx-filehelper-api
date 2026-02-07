import asyncio
from collections import deque
import hashlib
import html
import json
import mimetypes
import os
import random
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import httpx


class WeChatHelperBot:
    def __init__(self, entry_host: str = "szfilehelper.weixin.qq.com"):
        self.entry_host = entry_host
        self.mmweb_appid = "wx_webfilehelper"
        self.to_user_name = "filehelper"
        self.lang = "zh_CN"
        self.state_path = Path(os.getcwd()) / "state.json"

        self.login_host, self.file_host = self._resolve_hosts(entry_host)

        self.client: httpx.AsyncClient | None = None
        self.lock = asyncio.Lock()
        self.login_callback_url = os.getenv("LOGIN_CALLBACK_URL", "").strip()
        self._login_callback_sent = False

        self.trace_enabled = os.getenv("WECHAT_TRACE_ENABLED", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        self.trace_redact = os.getenv("WECHAT_TRACE_REDACT", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        self.trace_max_body = int(os.getenv("WECHAT_TRACE_MAX_BODY", "4096") or "4096")
        self.trace_dir = Path(os.getenv("WECHAT_TRACE_DIR", os.path.join(os.getcwd(), "trace_logs")))
        self.trace_log_file = self.trace_dir / "wechat_http_trace.jsonl"
        self.trace_lock = asyncio.Lock()
        self.trace_seq = 0

        self.device_id = self._gen_device_id()
        self.uuid = ""
        self.uuid_ts = 0.0

        self.skey = ""
        self.sid = ""
        self.uin = ""
        self.pass_ticket = ""
        self.user_name = ""

        self.synckey: dict[str, Any] = {"Count": 0, "List": []}
        self.is_logged_in = False
        self.last_login_code = 0
        self.last_login_message = "init"

        self._msg_cache: list[dict[str, Any]] = []
        self._raw_by_id: dict[str, dict[str, Any]] = {}
        self._seen_msg_ids: set[str] = set()
        self._send_msg_ids: set[str] = set()

    def _resolve_hosts(self, host: str) -> tuple[str, str]:
        if "cmfilehelper.weixin" in host:
            return "login.wx8.qq.com", "file.wx8.qq.com"
        if "szfilehelper.weixin.qq.com" in host:
            return "login.wx2.qq.com", "file.wx2.qq.com"
        return "login.wx.qq.com", "file.wx.qq.com"

    async def start(self, headless=True, user_data_dir=None):
        timeout = httpx.Timeout(connect=10.0, read=40.0, write=40.0, pool=10.0)
        if self.trace_enabled:
            self.trace_dir.mkdir(parents=True, exist_ok=True)

        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            event_hooks={
                "request": [self._trace_on_request],
                "response": [self._trace_on_response],
            },
        )
        await self._load_session()
        await self.check_login_status(poll=False)

    async def stop(self):
        await self.save_session()
        if self.client:
            await self.client.aclose()
            self.client = None

    async def save_session(self, path=None):
        if not self.client:
            return False

        target = Path(path) if path else self.state_path
        cookies = []
        for cookie in self.client.cookies.jar:
            cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "expires": cookie.expires,
                }
            )

        state = {
            "entry_host": self.entry_host,
            "device_id": self.device_id,
            "uuid": self.uuid,
            "skey": self.skey,
            "sid": self.sid,
            "uin": self.uin,
            "pass_ticket": self.pass_ticket,
            "user_name": self.user_name,
            "synckey": self.synckey,
            "cookies": cookies,
        }

        target.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    async def _load_session(self):
        if not self.client or not self.state_path.exists():
            return

        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return

        self.entry_host = state.get("entry_host", self.entry_host)
        self.login_host, self.file_host = self._resolve_hosts(self.entry_host)
        self.device_id = state.get("device_id", self.device_id)
        self.uuid = state.get("uuid", "")

        self.skey = state.get("skey", "")
        self.sid = state.get("sid", "")
        self.uin = str(state.get("uin", ""))
        self.pass_ticket = state.get("pass_ticket", "")
        self.user_name = state.get("user_name", "")
        self.synckey = state.get("synckey", {"Count": 0, "List": []})

        for item in state.get("cookies", []):
            try:
                self.client.cookies.set(
                    item.get("name", ""),
                    item.get("value", ""),
                    domain=item.get("domain"),
                    path=item.get("path", "/"),
                )
            except Exception:
                continue

    async def get_login_qr(self) -> bytes:
        if not self.client:
            raise RuntimeError("Client not initialized")

        if await self.check_login_status():
            return b""

        if not self.uuid or (time.time() - self.uuid_ts > 240):
            await self._jslogin_get_uuid()
            self.last_login_message = "qr_ready"

        resp = await self.client.get(f"https://login.weixin.qq.com/qrcode/{self.uuid}")
        resp.raise_for_status()
        return resp.content

    async def get_login_status_detail(self) -> dict[str, Any]:
        return {
            "logged_in": self.is_logged_in,
            "code": self.last_login_code,
            "status": self.last_login_message,
            "has_uuid": bool(self.uuid),
            "uuid": self.uuid,
            "uuid_age_seconds": int(time.time() - self.uuid_ts) if self.uuid_ts else None,
            "entry_host": self.entry_host,
            "login_host": self.login_host,
            "trace_enabled": self.trace_enabled,
            "trace_file": str(self.trace_log_file),
        }

    def get_trace_status(self) -> dict[str, Any]:
        size = self.trace_log_file.stat().st_size if self.trace_log_file.exists() else 0
        return {
            "enabled": self.trace_enabled,
            "redact": self.trace_redact,
            "max_body": self.trace_max_body,
            "file": str(self.trace_log_file),
            "exists": self.trace_log_file.exists(),
            "size_bytes": size,
        }

    async def read_recent_traces(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.trace_enabled or not self.trace_log_file.exists():
            return []

        rows: deque[str] = deque(maxlen=max(1, min(limit, 1000)))
        with self.trace_log_file.open("r", encoding="utf-8") as file_obj:
            for line in file_obj:
                line = line.strip()
                if line:
                    rows.append(line)

        records = []
        for line in rows:
            try:
                records.append(json.loads(line))
            except Exception:
                records.append({"raw": line})
        return records

    async def clear_traces(self) -> bool:
        if self.trace_log_file.exists():
            self.trace_log_file.unlink()
        return True

    def _has_auth(self) -> bool:
        return bool(self.skey and self.sid and self.uin and self.pass_ticket)

    async def check_login_status(self, poll: bool = True) -> bool:
        if not self.client:
            return False

        if self._has_auth():
            if not poll:
                self.is_logged_in = True
                self.last_login_code = 200
                if self.last_login_message in {"init", "need_qr", "qr_expired"}:
                    self.last_login_message = "logged_in_cached"
                return True

            status = await self._synccheck()
            if status == "hasMsg":
                await self._webwxsync()
                self.is_logged_in = True
                self.last_login_code = 200
                self.last_login_message = "logged_in"
                await self._notify_login_callback_if_needed()
                return True

            if status == "wait":
                self.is_logged_in = True
                self.last_login_code = 200
                self.last_login_message = "logged_in"
                await self._notify_login_callback_if_needed()
                return True

        if poll and self.uuid:
            code = await self._poll_login_once()
            if code == 200:
                self.is_logged_in = True
                self.last_login_message = "logged_in"
                await self._notify_login_callback_if_needed()
                await self.save_session()
                return True

        self.is_logged_in = False
        if not self.uuid:
            self.last_login_message = "need_qr"
        return False

    async def send_text(self, message: str) -> bool:
        if not message:
            return False
        if not await self.check_login_status(poll=False):
            return False

        async with self.lock:
            url = f"/cgi-bin/mmwebwx-bin/webwxsendmsg?lang={self.lang}&pass_ticket={quote(self.pass_ticket, safe='')}"
            payload = {"Type": 1, "Content": message}
            data = await self._post_message(url, payload)
            if not data:
                return False

            msg_id = str(data.get("MsgID", ""))
            if msg_id:
                self._send_msg_ids.add(msg_id)
            return True

    async def send_file(self, file_path: str) -> bool:
        if not await self.check_login_status(poll=False):
            return False

        path = Path(file_path)
        if not path.exists():
            return False

        file_size = path.stat().st_size
        if file_size > 25 * 1024 * 1024:
            print("Direct mode currently supports files up to 25MB")
            return False

        mime_type, _ = mimetypes.guess_type(path.name)
        mime_type = mime_type or "application/octet-stream"
        media_type = "pic" if mime_type.startswith("image/") else "doc"

        file_md5 = self._md5_file(path)
        client_media_id = self._gen_msg_id()

        media_id = await self._webwxuploadmedia(
            path=path,
            mime_type=mime_type,
            media_type=media_type,
            file_md5=file_md5,
            client_media_id=client_media_id,
        )
        if not media_id:
            return False

        if media_type == "pic":
            url = f"/cgi-bin/mmwebwx-bin/webwxsendmsgimg?fun=async&f=json&pass_ticket={quote(self.pass_ticket, safe='')}"
            payload = {"MediaId": media_id, "Type": 3, "Content": ""}
        else:
            xml_content = self._build_appmsg_xml(path.name, file_size, media_id)
            url = f"/cgi-bin/mmwebwx-bin/webwxsendappmsg?fun=async&f=json&lang={self.lang}&pass_ticket={quote(self.pass_ticket, safe='')}"
            payload = {"Type": 6, "Content": xml_content}

        data = await self._post_message(url, payload)
        if not data:
            return False

        msg_id = str(data.get("MsgID", ""))
        if msg_id:
            self._send_msg_ids.add(msg_id)
        return True

    async def get_latest_messages(self, limit=10):
        if not self.is_logged_in:
            if not await self.check_login_status(poll=True):
                return []
        elif not self._has_auth():
            self.is_logged_in = False
            return []

        status = await self._synccheck()
        if status == "hasMsg":
            await self._webwxsync()
        elif status == "loginout":
            self.is_logged_in = False
            return []

        return self._msg_cache[-limit:]

    async def download_message_content(self, msg_id: str, save_path: str) -> bool:
        if not await self.check_login_status(poll=False):
            return False
        if not self.client:
            return False

        raw = self._raw_by_id.get(str(msg_id))
        if not raw:
            return False

        msg_type = raw.get("MsgType")
        url = ""

        if msg_type == 3:
            url = (
                f"https://{self.entry_host}/cgi-bin/mmwebwx-bin/webwxgetmsgimg"
                f"?MsgID={raw.get('MsgId')}&skey={quote(self.skey, safe='')}&type=slave"
                f"&mmweb_appid={self.mmweb_appid}"
            )
        elif msg_type == 49 and raw.get("AppMsgType") == 6:
            webwx_data_ticket = self._get_cookie("webwx_data_ticket")
            sender = raw.get("FromUserName", "")
            media_id = raw.get("MediaId", "")
            encry_filename = raw.get("EncryFileName", "")
            url = (
                f"https://{self.file_host}/cgi-bin/mmwebwx-bin/webwxgetmedia"
                f"?sender={quote(sender, safe='')}"
                f"&mediaid={quote(media_id, safe='')}"
                f"&encryfilename={quote(encry_filename, safe='')}"
                f"&fromuser={quote(str(self.uin), safe='')}"
                f"&pass_ticket={quote(self.pass_ticket, safe='')}"
                f"&webwx_data_ticket={quote(webwx_data_ticket, safe='')}"
                f"&sid={quote(self.sid, safe='')}"
                f"&mmweb_appid={self.mmweb_appid}"
            )
        else:
            return False

        try:
            resp = await self.client.get(url)
            resp.raise_for_status()
            Path(save_path).write_bytes(resp.content)
            return True
        except Exception as exc:
            print(f"download_message_content failed: {exc}")
            return False

    async def save_screenshot(self, path: str) -> bool:
        return False

    async def get_page_source(self) -> str:
        return json.dumps(
            {
                "mode": "direct_protocol",
                "entry_host": self.entry_host,
                "login_host": self.login_host,
                "file_host": self.file_host,
                "is_logged_in": self.is_logged_in,
                "uin": self.uin,
                "user_name": self.user_name,
                "has_uuid": bool(self.uuid),
            },
            ensure_ascii=False,
            indent=2,
        )

    async def _jslogin_get_uuid(self):
        if not self.client:
            raise RuntimeError("Client not initialized")

        redirect_uri = quote(
            f"https://{self.entry_host}/cgi-bin/mmwebwx-bin/webwxnewloginpage", safe=""
        )
        now = int(time.time() * 1000)
        url = (
            f"https://{self.login_host}/jslogin?appid={self.mmweb_appid}"
            f"&redirect_uri={redirect_uri}&fun=new&lang={self.lang}&_={now}"
        )
        resp = await self.client.get(url)
        resp.raise_for_status()

        uuid = self._regex_group(resp.text, r'window\.QRLogin\.uuid\s*=\s*"([^"]+)"')
        if not uuid:
            raise RuntimeError(f"Cannot parse uuid from jslogin response: {resp.text[:200]}")

        self.uuid = uuid
        self.uuid_ts = time.time()

    async def _poll_login_once(self) -> int:
        if not self.client or not self.uuid:
            return 0

        now = int(time.time() * 1000)
        r_value = ~int(time.time())
        url = (
            f"https://{self.login_host}/cgi-bin/mmwebwx-bin/login"
            f"?loginicon=true&uuid={quote(self.uuid, safe='')}&tip=1"
            f"&r={r_value}&_={now}&appid={self.mmweb_appid}"
        )

        try:
            resp = await self.client.get(url)
            resp.raise_for_status()
            body = resp.text
        except Exception:
            return 0

        code_str = self._regex_group(body, r"window\.code\s*=\s*(\d+)")
        code = int(code_str) if code_str else 0
        self.last_login_code = code

        if code == 200:
            redirect_uri = self._regex_group(body, r'window\.redirect_uri\s*=\s*"([^"]+)"')
            if redirect_uri:
                await self._complete_login(redirect_uri)
            self.last_login_message = "authorized"
        elif code == 201:
            self.last_login_message = "scanned_wait_confirm"
        elif code == 408:
            self.last_login_message = "qr_wait_scan"
        elif code in {400, 500, 0}:
            self.uuid = ""
            self.last_login_message = "qr_expired"

        return code

    async def _complete_login(self, redirect_uri: str):
        if not self.client:
            return

        parsed = urlparse(redirect_uri)
        query = parse_qs(parsed.query)
        domain = parsed.netloc or self.entry_host

        self.entry_host = domain
        self.login_host, self.file_host = self._resolve_hosts(domain)

        url = f"https://{domain}/cgi-bin/mmwebwx-bin/webwxnewloginpage"
        params = {
            "fun": "new",
            "version": "v2",
            "ticket": (query.get("ticket") or [""])[0],
            "uuid": (query.get("uuid") or [self.uuid])[0],
            "lang": (query.get("lang") or [self.lang])[0],
            "scan": (query.get("scan") or [""])[0],
        }

        resp = await self.client.get(url, params=params, headers={"mmweb_appid": self.mmweb_appid})
        resp.raise_for_status()

        xml = resp.text
        self.skey = self._extract_xml_tag(xml, "skey")
        self.sid = self._extract_xml_tag(xml, "wxsid")
        self.uin = self._extract_xml_tag(xml, "wxuin")
        self.pass_ticket = self._extract_xml_tag(xml, "pass_ticket")

        if not all([self.skey, self.sid, self.uin, self.pass_ticket]):
            raise RuntimeError("webwxnewloginpage missing auth fields")

        ok = await self._webwxinit()
        self.is_logged_in = ok
        if ok:
            self.last_login_code = 200
            self.last_login_message = "logged_in"
            self._login_callback_sent = False

    async def _webwxinit(self) -> bool:
        if not self.client:
            return False

        url = f"https://{self.entry_host}/cgi-bin/mmwebwx-bin/webwxinit"
        params = {
            "r": ~int(time.time() * 1000),
            "lang": self.lang,
            "pass_ticket": self.pass_ticket,
        }
        payload = {"BaseRequest": self._base_request()}

        try:
            resp = await self.client.post(
                url,
                params=params,
                json=payload,
                headers={"mmweb_appid": self.mmweb_appid},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"webwxinit failed: {exc}")
            return False

        base = data.get("BaseResponse") or {}
        if base.get("Ret") != 0:
            return False

        user = data.get("User") or {}
        self.user_name = user.get("UserName", self.user_name)
        if user.get("Uin"):
            self.uin = str(user.get("Uin"))

        sync = data.get("SyncKey") or {"Count": 0, "List": []}
        self.synckey = sync
        return True

    async def _synccheck(self) -> str:
        if not self.client:
            return "loginout"
        if not all([self.skey, self.sid, self.uin]):
            return "loginout"

        synckey = self._format_synccheck_key()
        url = f"https://{self.entry_host}/cgi-bin/mmwebwx-bin/synccheck"
        params = {
            "r": int(time.time() * 1000),
            "skey": self.skey,
            "sid": self.sid,
            "uin": self.uin,
            "deviceid": self.device_id,
            "synckey": synckey,
            "mmweb_appid": self.mmweb_appid,
        }

        try:
            resp = await self.client.get(url, params=params)
            resp.raise_for_status()
            body = resp.text
        except Exception:
            return "resync"

        retcode = self._regex_group(body, r'retcode\s*:\s*"?(\d+)"?')
        selector = self._regex_group(body, r'selector\s*:\s*"?(\d+)"?')

        if retcode != "0":
            return "loginout"
        if selector and selector != "0":
            return "hasMsg"
        return "wait"

    async def _webwxsync(self):
        if not self.client:
            return []

        url = f"https://{self.entry_host}/cgi-bin/mmwebwx-bin/webwxsync"
        params = {
            "sid": self.sid,
            "skey": self.skey,
            "pass_ticket": self.pass_ticket,
        }
        payload = {
            "BaseRequest": self._base_request(),
            "SyncKey": self.synckey,
            "rr": ~int(time.time() * 1000),
        }

        try:
            resp = await self.client.post(
                url,
                params=params,
                json=payload,
                headers={"mmweb_appid": self.mmweb_appid},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"webwxsync failed: {exc}")
            return []

        if (data.get("BaseResponse") or {}).get("Ret") != 0:
            return []

        if data.get("SyncKey"):
            self.synckey = data["SyncKey"]

        add_msg_list = data.get("AddMsgList") or []
        normalized = self._normalize_messages(add_msg_list)
        if normalized:
            self._msg_cache.extend(normalized)
            self._msg_cache = self._msg_cache[-200:]
        return normalized

    async def _notify_login_callback_if_needed(self):
        if not self.login_callback_url or self._login_callback_sent:
            return
        if not self.client or not self.is_logged_in:
            return

        payload = {
            "event": "login_success",
            "uin": self.uin,
            "user_name": self.user_name,
            "entry_host": self.entry_host,
            "ts": int(time.time()),
        }
        try:
            resp = await self.client.post(self.login_callback_url, json=payload)
            if 200 <= resp.status_code < 300:
                self._login_callback_sent = True
        except Exception as exc:
            print(f"login callback failed: {exc}")

    async def _trace_on_request(self, request: httpx.Request):
        request.extensions["trace_start"] = time.perf_counter()
        self.trace_seq += 1
        trace_id = f"{int(time.time() * 1000)}-{self.trace_seq}"
        request.extensions["trace_id"] = trace_id

        if not self.trace_enabled:
            return

        content_type = request.headers.get("content-type", "")
        body_preview = ""
        if "multipart/form-data" in content_type:
            body_preview = "<<multipart omitted>>"
        else:
            body_preview = self._request_body_preview(request, content_type)

        await self._append_trace(
            {
                "event": "request",
                "id": trace_id,
                "ts": int(time.time() * 1000),
                "method": request.method,
                "url": self._sanitize_text(str(request.url)),
                "headers": self._sanitize_headers(dict(request.headers.items())),
                "body_preview": body_preview,
            }
        )

    async def _trace_on_response(self, response: httpx.Response):
        request = response.request
        trace_id = request.extensions.get("trace_id", "")
        started = request.extensions.get("trace_start")
        duration_ms = None
        if isinstance(started, float):
            duration_ms = int((time.perf_counter() - started) * 1000)

        if not self.trace_enabled:
            return

        content_type = response.headers.get("content-type", "")
        body_preview = ""
        if self._is_textual_content_type(content_type):
            try:
                raw = await response.aread()
                body_preview = self._bytes_preview(raw, content_type)
            except Exception as exc:
                body_preview = f"<<read error: {exc}>>"
        else:
            body_preview = f"<<binary {content_type or 'unknown'} omitted>>"

        await self._append_trace(
            {
                "event": "response",
                "id": trace_id,
                "ts": int(time.time() * 1000),
                "method": request.method,
                "url": self._sanitize_text(str(request.url)),
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "headers": self._sanitize_headers(dict(response.headers.items())),
                "body_preview": body_preview,
            }
        )

    async def _append_trace(self, row: dict[str, Any]):
        if not self.trace_enabled:
            return

        if not self.trace_dir.exists():
            self.trace_dir.mkdir(parents=True, exist_ok=True)

        line = json.dumps(row, ensure_ascii=False)
        async with self.trace_lock:
            with self.trace_log_file.open("a", encoding="utf-8") as file_obj:
                file_obj.write(line + "\n")

    def _request_body_preview(self, request: httpx.Request, content_type: str) -> str:
        try:
            payload = request.content
            if isinstance(payload, str):
                text = payload
            elif isinstance(payload, (bytes, bytearray)):
                text = self._bytes_preview(bytes(payload), content_type)
            else:
                text = "<<stream omitted>>"
        except Exception:
            return "<<stream omitted>>"

        return self._sanitize_text(text)

    def _bytes_preview(self, payload: bytes, content_type: str) -> str:
        if not payload:
            return ""

        clipped = payload[: self.trace_max_body]
        suffix = ""
        if len(payload) > len(clipped):
            suffix = f" ...<truncated {len(payload) - len(clipped)} bytes>"

        try:
            text = clipped.decode("utf-8")
        except UnicodeDecodeError:
            text = clipped.decode("latin1", errors="replace")

        if not self._is_textual_content_type(content_type):
            return f"<<non-text {content_type or 'unknown'} {len(payload)} bytes>>"
        return self._sanitize_text(text + suffix)

    def _is_textual_content_type(self, content_type: str) -> bool:
        value = (content_type or "").lower()
        keywords = ["json", "text", "xml", "javascript", "html", "x-www-form-urlencoded"]
        return any(word in value for word in keywords)

    def _sanitize_headers(self, headers: dict[str, Any]) -> dict[str, Any]:
        redacted = {}
        for key, value in headers.items():
            lower_key = key.lower()
            if lower_key in {"cookie", "set-cookie", "authorization"}:
                redacted[key] = "***"
            else:
                redacted[key] = self._sanitize_text(str(value))
        return redacted

    def _sanitize_text(self, text: str) -> str:
        if not self.trace_redact:
            return text

        if text is None:
            return ""

        sanitized = str(text)
        patterns = [
            r'(pass_ticket\s*[=:]\s*)([^&\s"\',;]+)',
            r'(webwx_data_ticket\s*[=:]\s*)([^&\s"\',;]+)',
            r'(skey\s*[=:]\s*)([^&\s"\',;]+)',
            r'(sid\s*[=:]\s*)([^&\s"\',;]+)',
            r'(wxsid\s*[=:]\s*)([^&\s"\',;]+)',
            r'(deviceid\s*[=:]\s*)([^&\s"\',;]+)',
            r'(uin\s*[=:]\s*)([^&\s"\',;]+)',
            r'(aeskey\s*[=:]\s*)([^&\s"\',;]+)',
            r'(signature\s*[=:]\s*)([^&\s"\',;]+)',
        ]
        for pattern in patterns:
            sanitized = re.sub(pattern, r'\1***', sanitized, flags=re.IGNORECASE)

        json_patterns = [
            r'("pass_ticket"\s*:\s*")[^"]*(")',
            r'("webwx_data_ticket"\s*:\s*")[^"]*(")',
            r'("Skey"\s*:\s*")[^"]*(")',
            r'("Sid"\s*:\s*")[^"]*(")',
            r'("DeviceID"\s*:\s*")[^"]*(")',
            r'("Signature"\s*:\s*")[^"]*(")',
            r'("AESKey"\s*:\s*")[^"]*(")',
        ]
        for pattern in json_patterns:
            sanitized = re.sub(pattern, r'\1***\2', sanitized)

        return sanitized

    async def _post_message(self, url: str, msg_fields: dict[str, Any]) -> dict[str, Any] | None:
        if not self.client:
            return None

        msg_id = self._gen_msg_id()
        payload = {
            "BaseRequest": self._base_request(),
            "Msg": {
                "ClientMsgId": msg_id,
                "LocalID": msg_id,
                "FromUserName": self.user_name,
                "ToUserName": self.to_user_name,
                **msg_fields,
            },
            "Scene": 0,
        }

        full_url = f"https://{self.entry_host}{url}"
        try:
            resp = await self.client.post(
                full_url,
                json=payload,
                headers={"mmweb_appid": self.mmweb_appid},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"post_message failed: {exc}")
            return None

        base = data.get("BaseResponse") or {}
        if base.get("Ret") != 0:
            return None
        return data

    async def _webwxuploadmedia(
        self,
        path: Path,
        mime_type: str,
        media_type: str,
        file_md5: str,
        client_media_id: str,
    ) -> str:
        if not self.client:
            return ""

        file_size = path.stat().st_size
        webwx_data_ticket = self._get_cookie("webwx_data_ticket")
        if not webwx_data_ticket:
            print("webwx_data_ticket cookie missing")

        upload_req = {
            "UploadType": 2,
            "BaseRequest": self._base_request(),
            "ClientMediaId": client_media_id,
            "TotalLen": file_size,
            "StartPos": 0,
            "DataLen": file_size,
            "MediaType": 4,
            "FromUserName": self.user_name,
            "ToUserName": self.to_user_name,
            "FileMd5": file_md5,
        }

        data = {
            "name": path.name,
            "type": mime_type,
            "lastModifiedDate": "Thu Jan 01 1970 08:00:00 GMT+0800",
            "size": str(file_size),
            "mediatype": media_type,
            "uploadmediarequest": json.dumps(upload_req, ensure_ascii=False),
            "webwx_data_ticket": webwx_data_ticket,
            "pass_ticket": self.pass_ticket,
        }

        upload_url = (
            f"https://{self.file_host}/cgi-bin/mmwebwx-bin/webwxuploadmedia"
            f"?f=json&random={self._random_string(4)}"
        )

        with path.open("rb") as file_obj:
            files = {"filename": (path.name, file_obj, mime_type)}
            try:
                resp = await self.client.post(
                    upload_url,
                    data=data,
                    files=files,
                    headers={"mmweb_appid": self.mmweb_appid},
                )
                resp.raise_for_status()
                result = resp.json()
            except Exception as exc:
                print(f"webwxuploadmedia failed: {exc}")
                return ""

        if (result.get("BaseResponse") or {}).get("Ret") != 0:
            print(f"webwxuploadmedia ret != 0: {result}")
            return ""

        return str(result.get("MediaId", ""))

    def _normalize_messages(self, add_msg_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in add_msg_list:
            msg_id = str(item.get("MsgId", ""))
            if not msg_id:
                continue
            if msg_id in self._seen_msg_ids or msg_id in self._send_msg_ids:
                continue

            from_user = item.get("FromUserName", "")
            to_user = item.get("ToUserName", "")
            if from_user != self.to_user_name and to_user != self.to_user_name:
                continue

            msg_type = item.get("MsgType")
            app_msg_type = item.get("AppMsgType")

            normalized: dict[str, Any] | None = None
            if msg_type == 1:
                normalized = {
                    "id": msg_id,
                    "type": "text",
                    "text": html.unescape(str(item.get("Content", ""))),
                    "is_mine": from_user != self.to_user_name,
                }
            elif msg_type == 3:
                normalized = {
                    "id": msg_id,
                    "type": "image",
                    "text": "[Image]",
                    "file_name": item.get("FileName") or f"img_{msg_id}.jpg",
                    "is_mine": from_user != self.to_user_name,
                }
            elif msg_type == 49 and app_msg_type == 6:
                file_name = item.get("FileName") or f"file_{msg_id}"
                normalized = {
                    "id": msg_id,
                    "type": "file",
                    "text": f"[File: {file_name}]",
                    "file_name": file_name,
                    "is_mine": from_user != self.to_user_name,
                }

            self._seen_msg_ids.add(msg_id)
            self._raw_by_id[msg_id] = item
            if normalized:
                out.append(normalized)

        return out

    def _build_appmsg_xml(self, file_name: str, file_size: int, media_id: str) -> str:
        ext = Path(file_name).suffix.replace(".", "") or "bin"
        return (
            "<appmsg appid='wxeb7ec651dd0aefa9' sdkver=''><title>"
            f"{file_name}</title><des></des><action></action><type>6</type>"
            "<content></content><url></url><lowurl></lowurl><appattach>"
            f"<totallen>{file_size}</totallen><attachid>{media_id}</attachid>"
            f"<fileext>{ext}</fileext></appattach><extinfo></extinfo></appmsg>"
        )

    def _base_request(self) -> dict[str, Any]:
        return {
            "Uin": int(self.uin) if str(self.uin).isdigit() else self.uin,
            "Sid": self.sid,
            "Skey": self.skey,
            "DeviceID": self.device_id,
        }

    def _format_synccheck_key(self) -> str:
        keys = (self.synckey or {}).get("List") or []
        pairs = [f"{item.get('Key')}_{item.get('Val')}" for item in keys if "Key" in item and "Val" in item]
        return "|".join(pairs)

    def _get_cookie(self, name: str) -> str:
        if not self.client:
            return ""
        for cookie in self.client.cookies.jar:
            if cookie.name == name:
                return cookie.value
        return ""

    def _extract_xml_tag(self, xml_text: str, tag: str) -> str:
        return self._regex_group(xml_text, rf"<{tag}>(.*?)</{tag}>", flags=re.S)

    def _gen_device_id(self) -> str:
        return "".join(str(random.randint(0, 9)) for _ in range(15))

    def _gen_msg_id(self) -> str:
        return str(int(time.time() * 1000)) + str(random.randint(100, 999))

    def _md5_file(self, path: Path) -> str:
        digest = hashlib.md5()
        with path.open("rb") as file_obj:
            for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _random_string(self, n: int) -> str:
        alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
        return "".join(random.choice(alphabet) for _ in range(n))

    def _regex_group(self, text: str, pattern: str, flags: int = 0) -> str:
        match = re.search(pattern, text, flags)
        return match.group(1) if match else ""
