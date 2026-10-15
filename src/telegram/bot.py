"""Telegram notify + simple command control for Oxaniq."""

from __future__ import annotations
import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional, Callable, Awaitable, Any
import aiohttp
from src.utils.audit_log import audit
from src.utils.config import OxaniqConfig
from src.utils.logger import setup_logger

logger = setup_logger()

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramBot:
    def __init__(self, config: OxaniqConfig):
        self.token = (config.telegram_bot_token or "").strip()
        self.chat_id = (config.telegram_chat_id or "").strip() or None
        self.channel_id = (getattr(config, "telegram_channel_id", None) or "").strip() or None
        self.enabled = bool(self.token)
        self._session: Optional[aiohttp.ClientSession] = None
        self._offset = 0
        self._poll_task: Optional[asyncio.Task] = None
        self._on_command: Optional[Callable[..., Awaitable[None]]] = None
        self._hits = defaultdict(list)
        self._rate = getattr(config, "tg_rate_per_min", 30)
        self._offset_path = Path(__file__).resolve().parents[2] / "data" / "tg_offset.json"
        self._load_offset()

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35))
        return self._session


    def _load_offset(self):
        try:
            if self._offset_path.exists():
                self._offset = int(json.loads(self._offset_path.read_text()).get("offset", 0))
        except Exception:
            self._offset = 0

    def _save_offset(self):
        try:
            self._offset_path.parent.mkdir(parents=True, exist_ok=True)
            self._offset_path.write_text(json.dumps({"offset": self._offset}))
        except Exception:
            pass

    def _allow(self, user_id: str) -> bool:
        now = time.time()
        window = self._hits[user_id]
        self._hits[user_id] = [ts for ts in window if now - ts < 60]
        if len(self._hits[user_id]) >= self._rate:
            return False
        self._hits[user_id].append(now)
        return True

    async def close(self):
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()

    async def _call(self, method: str, **params) -> Optional[dict]:
        if not self.enabled:
            return None
        url = API.format(token=self.token, method=method)
        try:
            session = await self._session_get()
            async with session.post(url, json=params) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logger.warning(f"Telegram {method} failed: {data}")
                    return None
                return data.get("result")
        except Exception as e:
            logger.warning(f"Telegram {method} error: {e}")
            return None

    async def discover_chat_id(self) -> Optional[str]:
        """Pull recent updates and pick the first private/group chat id."""
        result = await self._call("getUpdates", offset=0, limit=20, timeout=0)
        if not result:
            return None
        for upd in reversed(result):
            msg = upd.get("message") or upd.get("channel_post") or {}
            chat = msg.get("chat") or {}
            cid = chat.get("id")
            if cid is not None:
                self.chat_id = str(cid)
                logger.info(f"Telegram chat_id discovered: {self.chat_id} ({chat.get('title') or chat.get('type')})")
                return self.chat_id
        return None

    async def send(self, text: str, chat_id: Optional[str] = None, reply_markup: Optional[dict] = None, parse_mode: Optional[str] = None) -> bool:
        cid = chat_id or self.chat_id
        if not self.enabled:
            return False
        if not cid:
            await self.discover_chat_id()
            cid = self.chat_id
        if not cid:
            logger.debug("Telegram: no chat_id yet — message the bot/group first")
            return False
        text = text[:4000]
        payload = {
            "chat_id": cid,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        res = await self._call("sendMessage", **payload)
        return res is not None

    async def send_photo_file(self, path: str, caption: str, chat_id: Optional[str] = None, reply_markup: Optional[dict] = None, parse_mode: str = "HTML") -> bool:
        cid = chat_id or self.chat_id
        if not self.enabled or not cid:
            return False
        import aiohttp
        from pathlib import Path as P
        data = aiohttp.FormData()
        data.add_field("chat_id", str(cid))
        data.add_field("caption", caption[:1000])
        data.add_field("parse_mode", parse_mode)
        if reply_markup:
            import json
            data.add_field("reply_markup", json.dumps(reply_markup))
        data.add_field("photo", P(path).read_bytes(), filename="welcome.jpg", content_type="image/jpeg")
        session = await self._session_get()
        url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
        try:
            async with session.post(url, data=data) as r:
                body = await r.json()
                if not body.get("ok"):
                    logger.warning(f"Telegram sendPhoto file failed: {body}")
                    return False
                return True
        except Exception as e:
            logger.warning(f"Telegram sendPhoto file error: {e}")
            return False

    async def send_photo(self, photo: str, caption: str, chat_id: Optional[str] = None, reply_markup: Optional[dict] = None, parse_mode: str = "HTML") -> bool:
        cid = chat_id or self.chat_id
        if not self.enabled or not cid or not photo:
            return False
        payload = {
            "chat_id": cid,
            "photo": photo,
            "caption": caption[:1000],
            "parse_mode": parse_mode,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        res = await self._call("sendPhoto", **payload)
        return res is not None

    def set_command_handler(self, handler: Callable[[str, str], Awaitable[None]]):
        """handler(command, chat_id) e.g. command='/status'."""
        self._on_command = handler

    async def _set_commands(self):
        commands = [
            {"command": "help", "description": "Command list"},
            {"command": "status", "description": "Cash, PnL, open trades"},
            {"command": "pos", "description": "Open positions"},
            {"command": "trades", "description": "Recent fills"},
            {"command": "calls", "description": "Calls used today"},
            {"command": "feed", "description": "Launch source"},
            {"command": "wallet", "description": "Your deposit address"},
            {"command": "balance", "description": "SOL and USDC"},
            {"command": "pay", "description": "Unlock for $11.99"},
            {"command": "verify", "description": "Confirm your payment"},
            {"command": "quote", "description": "Price a mint"},
            {"command": "health", "description": "RPC status"},
            {"command": "slot", "description": "Current Solana slot"},
            {"command": "withdraw", "description": "Send SOL or USDC"},
            {"command": "live", "description": "Paper or live (admin)"},
        ]
        await self._call("setMyCommands", commands=commands)

    async def start_polling(self):
        if not self.enabled:
            return
        await self._set_commands()
        if not self.chat_id:
            await self.discover_chat_id()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="tg-poll")

    async def _poll_loop(self):
        while True:
            try:
                result = await self._call(
                    "getUpdates",
                    offset=self._offset,
                    timeout=25,
                    allowed_updates=["message", "callback_query"],
                )
                if result is None:
                    await asyncio.sleep(3)
                    continue
                for upd in result:
                    self._offset = max(self._offset, int(upd.get("update_id", 0)) + 1)
                    self._save_offset()
                    cb = upd.get("callback_query")
                    if cb and self._on_command:
                        data = (cb.get("data") or "").strip()
                        from_user = cb.get("from") or {}
                        uid = str(from_user.get("id") or "")
                        msgc = cb.get("message") or {}
                        cid = str((msgc.get("chat") or {}).get("id") or "")
                        await self._call("answerCallbackQuery", callback_query_id=cb.get("id"))
                        if data.startswith("live:") or data.startswith("pay:") or data.startswith("holders:"):
                            try:
                                await self._on_command("/cb", cid, data, uid)
                            except TypeError:
                                await self._on_command("/cb", cid, data)
                        continue
                    msg = upd.get("message") or {}
                    text = (msg.get("text") or "").strip()
                    chat = msg.get("chat") or {}
                    cid = str(chat.get("id", ""))
                    if cid and not self.chat_id:
                        self.chat_id = cid
                        logger.info(f"Telegram chat_id set from message: {cid}")
                    # If a chat_id is configured, ignore commands from other chats
                    if self.chat_id and cid and str(cid) != str(self.chat_id):
                        logger.debug(f"Ignoring command from foreign chat {cid}")
                        continue
                    if text.startswith("/") and self._on_command:
                        cmd = text.split()[0].lower().split("@")[0]
                        from_user = msg.get("from") or {}
                        uid = str(from_user.get("id") or "")
                        if uid and not self._allow(uid):
                            audit("tg_rate_limited", user_id=uid, cmd=cmd)
                            await self.send("Slow down. Rate limit hit.", chat_id=cid)
                            continue
                        audit("tg_command", user_id=uid, cmd=cmd, chat_id=cid)
                        try:
                            await self._on_command(cmd, cid, text, uid)
                        except TypeError:
                            try:
                                await self._on_command(cmd, cid, text)
                            except TypeError:
                                await self._on_command(cmd, cid)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Telegram poll: {e}")
                await asyncio.sleep(5)
