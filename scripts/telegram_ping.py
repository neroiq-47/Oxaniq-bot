#!/usr/bin/env python3
"""Discover chat_id and send a test message. Message the bot/group first."""

from __future__ import annotations
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.config import get_config
from src.utils.logger import setup_logger, console
from src.telegram.bot import TelegramBot


async def main():
    setup_logger("INFO")
    cfg = get_config()
    if not cfg.telegram_bot_token:
        console.print("[red]No TELEGRAM_BOT_TOKEN in .env[/]")
        return 1
    bot = TelegramBot(cfg)
    console.print("Looking for chat via getUpdates (send /start in the group first)...")
    cid = await bot.discover_chat_id()
    if not cid:
        console.print(
            "[yellow]No chat found yet.[/]\n"
            "1. Add the bot to your group\n"
            "2. Send any message (e.g. /start)\n"
            "3. Run this script again\n"
            "Then set TELEGRAM_CHAT_ID in .env"
        )
        await bot.close()
        return 1
    console.print(f"[green]chat_id = {cid}[/]")
    # persist hint
    console.print(f"Add to .env:  TELEGRAM_CHAT_ID={cid}")
    ok = await bot.send(f"OxanIQ online.\nchat_id={cid}\nCommands: /status /kill /revive /help")
    console.print("Ping sent." if ok else "Ping failed.")
    await bot.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
