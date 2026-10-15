"""Solana wallet helpers — keypair, deposit address, SOL + USDC balances.

Private keys stay in local .env only. Never commit them.
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import base58
from src.utils.config import OxaniqConfig, get_config
from src.utils.logger import setup_logger

logger = setup_logger()

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
# Mainnet native USDC (Circle)
DEFAULT_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDC_DECIMALS = 6


def _pubkey_from_seed(seed: bytes) -> bytes:
    sk = Ed25519PrivateKey.from_private_bytes(seed)
    return sk.public_key().public_bytes_raw()


def generate_keypair() -> Tuple[str, str]:
    sk = Ed25519PrivateKey.generate()
    seed = sk.private_bytes_raw()
    pub = sk.public_key().public_bytes_raw()
    secret_64 = seed + pub
    return base58.b58encode(secret_64).decode(), base58.b58encode(pub).decode()


def pubkey_from_secret(secret_b58: str) -> str:
    raw = base58.b58decode(secret_b58)
    if len(raw) == 64:
        pub = raw[32:]
    elif len(raw) == 32:
        pub = _pubkey_from_seed(raw)
    else:
        raise ValueError(f"Unexpected secret length {len(raw)}")
    return base58.b58encode(pub).decode()


def append_env_wallet(secret_b58: str, pubkey_b58: str, env_path: Path = ENV_PATH) -> None:
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    out = []
    seen_sk = seen_pk = False
    for line in lines:
        if line.startswith("SOLANA_PRIVATE_KEY="):
            out.append(f"SOLANA_PRIVATE_KEY={secret_b58}")
            seen_sk = True
        elif line.startswith("WALLET_PUBKEY="):
            out.append(f"WALLET_PUBKEY={pubkey_b58}")
            seen_pk = True
        else:
            out.append(line)
    if not seen_sk:
        out.append(f"SOLANA_PRIVATE_KEY={secret_b58}")
    if not seen_pk:
        out.append(f"WALLET_PUBKEY={pubkey_b58}")
    env_path.write_text("\n".join(out) + "\n")
    os.environ["SOLANA_PRIVATE_KEY"] = secret_b58
    os.environ["WALLET_PUBKEY"] = pubkey_b58
    logger.info(f"Wallet pubkey saved to {env_path.name} (do not commit)")


class SolanaWallet:
    def __init__(self, config: Optional[OxaniqConfig] = None):
        self.config = config or get_config()
        self.secret = (self.config.solana_private_key or "").strip() or None
        self.pubkey = (self.config.wallet_pubkey or "").strip() or None
        self.usdc_mint = getattr(self.config, "usdc_mint", None) or DEFAULT_USDC_MINT
        if self.secret and not self.pubkey:
            try:
                self.pubkey = pubkey_from_secret(self.secret)
            except Exception as e:
                logger.warning(f"Could not derive pubkey: {e}")

    @property
    def has_wallet(self) -> bool:
        return bool(self.pubkey)

    @property
    def deposit_address(self) -> Optional[str]:
        return self.pubkey

    def create_and_persist(self) -> Tuple[str, str]:
        secret, pub = generate_keypair()
        append_env_wallet(secret, pub)
        self.secret = secret
        self.pubkey = pub
        self.config.solana_private_key = secret
        self.config.wallet_pubkey = pub
        return secret, pub

    async def get_sol_balance(self, helius) -> Optional[float]:
        if not self.pubkey:
            return None
        try:
            res = await helius._rpc("getBalance", [self.pubkey])
            if res is None:
                return None
            lamports = res.get("value", 0) if isinstance(res, dict) else int(res)
            return lamports / 1_000_000_000
        except Exception as e:
            logger.warning(f"SOL balance fetch failed: {e}")
            return None

    async def get_usdc_balance(self, helius) -> Optional[float]:
        """SPL USDC balance (mainnet mint) via getTokenAccountsByOwner."""
        if not self.pubkey:
            return None
        try:
            res = await helius._rpc(
                "getTokenAccountsByOwner",
                [
                    self.pubkey,
                    {"mint": self.usdc_mint},
                    {"encoding": "jsonParsed"},
                ],
            )
            if not res:
                return 0.0
            accounts = res.get("value") if isinstance(res, dict) else res
            if not accounts:
                return 0.0
            total = 0.0
            for acc in accounts:
                try:
                    info = (
                        acc.get("account", {})
                        .get("data", {})
                        .get("parsed", {})
                        .get("info", {})
                    )
                    tok = info.get("tokenAmount") or {}
                    # uiAmount is float when present
                    if tok.get("uiAmount") is not None:
                        total += float(tok["uiAmount"])
                    else:
                        amt = int(tok.get("amount") or 0)
                        dec = int(tok.get("decimals") or USDC_DECIMALS)
                        total += amt / (10 ** dec)
                except Exception:
                    continue
            return total
        except Exception as e:
            logger.warning(f"USDC balance fetch failed: {e}")
            return None

    async def get_balances(self, helius) -> Dict[str, Any]:
        sol = await self.get_sol_balance(helius)
        usdc = await self.get_usdc_balance(helius)
        return {
            "pubkey": self.pubkey,
            "sol": sol,
            "usdc": usdc,
            "usdc_mint": self.usdc_mint,
        }

    def summary(self) -> Dict[str, Any]:
        return {
            "has_wallet": self.has_wallet,
            "deposit_address": self.pubkey,
            "has_secret": bool(self.secret),
            "usdc_mint": self.usdc_mint,
        }
