#!/usr/bin/env python3
"""Oxaniq entrypoint — paper mode by default."""

from __future__ import annotations
import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rich.panel import Panel
from src.utils.config import get_config, OxaniqConfig
from src.utils.logger import setup_logger, console
from src.core.agent import run_agent


def main():
    parser = argparse.ArgumentParser(description="Oxaniq — Solana Memecoin Trading AI Agent")
    parser.add_argument("--paper", action="store_true", default=True, help="Paper trading (default)")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Request live mode (blocked unless fully enabled — not production-ready)",
    )
    parser.add_argument("--log-level", default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--cycles", type=int, default=0, help="Stop after N cycles (0 = run forever)")
    parser.add_argument("--cash", type=float, default=None, help="Starting paper cash in SOL (default 1.0 or saved)")
    args = parser.parse_args()

    config = get_config()

    # Live is intentionally hard-gated: scaffold is paper-first
    if args.live:
        console.print(
            "[bold red]LIVE MODE IS NOT ENABLED IN THIS BUILD.[/]\n"
            "Oxaniq v0.1 ships paper trading only. Real signing/swaps are not production-ready.\n"
            "Remove this gate only after you implement and audit live execution."
        )
        config.paper_trading = True
    else:
        config.paper_trading = True

    level = args.log_level or config.log_level
    setup_logger(level)

    console.print(
        Panel.fit(
            "[bold green]OxanIQ v0.1[/]\n"
            "Solana × Pump.fun × Helius × Jupiter\n"
            f"Mode: [bold]PAPER[/] (live gated off)",
            border_style="green",
        )
    )

    try:
        asyncio.run(run_agent(config=config, max_cycles=args.cycles, start_cash=args.cash))
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down Oxaniq...[/]")


if __name__ == "__main__":
    main()
