#!/usr/bin/env python3
"""Product readiness smoke test — paper path only."""

from __future__ import annotations
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.config import get_config
from src.utils.logger import setup_logger, console
from src.utils.solana_helpers import HeliusHelper
from src.utils.portfolio_store import load_portfolio, save_portfolio
from src.core.models import TokenInfo, PortfolioState
from src.brain.scorer import Scorer
from src.risk.manager import RiskManager
from src.execution.paper import PaperExecutor
from src.ingestion.pumpfun import PumpFunIngester
from src.core.agent import run_agent


async def main():
    setup_logger("INFO")
    console.print("[bold green]Oxaniq smoke test[/]\n")
    cfg = get_config()
    ok = True

    # Config
    console.print(f"paper_trading={cfg.paper_trading} helius={bool(cfg.helius_api_key)} jupiter={bool(cfg.jupiter_api_key)}")

    # Helius
    h = HeliusHelper(cfg)
    health = await h.health_check()
    if not health.get("ok"):
        console.print("[yellow]Helius health failed (non-fatal for paper)[/]")
    await h.close()

    # Scorer + risk + paper buy/sell
    scorer = Scorer()
    port = PortfolioState(cash_sol=1.0)
    risk = RiskManager(cfg.risk, port)
    exe = PaperExecutor(port)
    t = TokenInfo(
        mint="SmokeMint1",
        name="SmokeTest",
        symbol="SMK",
        creator="x",
        created_timestamp=1,
        virtual_sol_reserves=5.0,
        virtual_token_reserves=800_000_000,
    )
    sig = scorer.score(t, fomo_score=0.7)
    size = risk.size_position(sig)
    can, reason = risk.can_open_position(sig, size)
    console.print(f"can_open={can} reason={reason} size={size}")
    if not can:
        ok = False
    trade = exe.execute_buy(sig, size)
    if not trade:
        ok = False
        console.print("[red]paper buy failed[/]")
    can2, reason2 = risk.can_open_position(sig, size)
    console.print(f"dedup can_open={can2} ({reason2})")
    if can2:
        ok = False
        console.print("[red]dedup failed[/]")
    sell = exe.execute_sell(t.mint, 1.0)
    if not sell:
        ok = False

    # Persist
    save_portfolio(port)
    loaded = load_portfolio()
    console.print(f"persist cash={loaded.cash_sol:.4f} trades={len(loaded.trades)}")

    # Ingest mocks
    ing = PumpFunIngester(cfg)
    tokens = await ing.fetch_new_tokens(3)
    console.print(f"ingest tokens={len(tokens)} first={tokens[0].symbol if tokens else None}")
    await ing.stop()

    # Short agent run
    console.print("\n[bold]Running agent for 2 cycles...[/]")
    await run_agent(config=cfg, max_cycles=2, start_cash=1.0)

    console.print("\n[bold green]SMOKE PASS[/]" if ok else "\n[bold red]SMOKE ISSUES[/]")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
