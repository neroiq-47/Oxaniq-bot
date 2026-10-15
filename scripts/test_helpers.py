#!/usr/bin/env python3
"""Quick test of Helius health + priority fees + Jupiter quote."""

from __future__ import annotations
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.config import get_config
from src.utils.solana_helpers import HeliusHelper
from src.execution.jupiter import JupiterClient
from rich.console import Console
from rich.table import Table

console = Console()


async def main():
    config = get_config()
    console.print("[bold magenta]Oxaniq Helper Test[/]\n")

    # 1. Helius health
    helius = HeliusHelper(config)
    health = await helius.health_check()
    console.print(health)

    # 2. Priority fees
    console.print("\n[bold]Priority Fee Estimates[/]")
    for level in ["low", "medium", "high", "veryHigh"]:
        fee = await helius.get_priority_fee(level=level)
        console.print(
            f"  {level:10} → {fee['priority_fee_microlamports']:>10,} µL/CU  "
            f"(source: {fee['source']})"
        )

    # 3. Jupiter quote (SOL → USDC as a safe test)
    console.print("\n[bold]Jupiter Quote (0.01 SOL → USDC)[/]")
    jup = JupiterClient(config)
    USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    quote = await jup.quote_sol_to_token(USDC, sol_amount=0.01, slippage_bps=50)

    if quote:
        summary = jup.summarize_quote(quote)
        table = Table(title="Quote Summary")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("In (lamports)", f"{summary['in_amount']:,}")
        table.add_row("Out (USDC base)", f"{summary['out_amount']:,}")
        table.add_row("Price impact %", f"{summary['price_impact_pct']:.4f}")
        table.add_row("Route hops", str(summary["route_hops"]))
        console.print(table)
    else:
        console.print("[red]Quote failed[/]")

    await helius.close()
    await jup.close()
    console.print("\n[green]Done.[/]")


if __name__ == "__main__":
    asyncio.run(main())
