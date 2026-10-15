#!/usr/bin/env python3
"""Create a Solana keypair for Oxaniq deposits. Writes local .env only."""

from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.wallet.solana_wallet import SolanaWallet, generate_keypair, append_env_wallet
from src.utils.config import get_config
from src.utils.logger import setup_logger, console


def main():
    setup_logger("INFO")
    cfg = get_config()
    w = SolanaWallet(cfg)
    if w.has_wallet and w.secret:
        console.print(f"[yellow]Wallet already set[/]\nDeposit address:\n[bold green]{w.pubkey}[/]")
        console.print("Re-run with --force to generate a NEW key (old key stays in .env until replaced).")
        if "--force" not in sys.argv:
            return 0
    secret, pub = generate_keypair()
    append_env_wallet(secret, pub)
    console.print("[bold green]New wallet created[/]")
    console.print(f"Deposit address (public):\n[bold]{pub}[/]")
    console.print("\nPrivate key saved to local .env as SOLANA_PRIVATE_KEY")
    console.print("[red]Never share or commit the private key.[/]")
    console.print("Send a small amount of SOL to the address above for fees + paper→live later.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
