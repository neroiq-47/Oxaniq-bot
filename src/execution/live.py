"""Live execution — PumpDev trade-local, simulate, confirm, fee transfer.

Gated by config.live_enabled. Paper remains the default path.
"""

from __future__ import annotations
from typing import Optional, Dict, Any
import asyncio
import aiohttp
import base58
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from src.utils.config import OxaniqConfig
from src.utils.logger import setup_logger

logger = setup_logger()
TRADE_LOCAL = "https://pumpdev.io/api/trade-local"


class LiveExecutor:
    def __init__(self, config: OxaniqConfig, helius):
        self.config = config
        self.helius = helius
        self.enabled = bool(config.live_enabled and config.solana_private_key)

    def _keypair(self, secret: Optional[str] = None) -> Optional[Keypair]:
        raw = (secret or self.config.solana_private_key or "").strip()
        if not raw:
            return None
        try:
            return Keypair.from_base58_string(raw)
        except Exception as e:
            logger.warning(f"live keypair load failed: {e}")
            return None

    def _decode_tx(self, raw: bytes) -> bytes:
        import json
        try:
            data = json.loads(raw)
        except Exception:
            return raw
        if isinstance(data, str):
            return base58.b58decode(data)
        if isinstance(data, dict):
            b58 = data.get("transaction") or data.get("tx") or data.get("serialized")
            if b58:
                return base58.b58decode(b58)
        raise ValueError("unrecognized trade-local payload")

    async def _priority_sol(self) -> float:
        try:
            info = await self.helius.get_priority_fee(level="high")
            micro = int(info.get("priority_fee_microlamports") or 50_000)
            # rough SOL budget for the PumpDev priorityFee field
            return max(0.0001, min(0.005, micro / 1_000_000_000))
        except Exception:
            return 0.0005

    async def _confirm(self, sig: str, tries: int = 8) -> bool:
        for _ in range(tries):
            try:
                st = await self.helius._rpc("getSignatureStatuses", [[sig]])
                val = ((st or {}).get("value") or [None])[0]
                if val and val.get("confirmationStatus") in ("confirmed", "finalized") and not val.get("err"):
                    return True
                if val and val.get("err"):
                    return False
            except Exception:
                pass
            await asyncio.sleep(1.2)
        return False

    async def _trade_local(self, kp: Keypair, action: str, mint: str, amount, denominated_in_sol: str) -> Dict[str, Any]:
        priority = await self._priority_sol()
        body = {
            "publicKey": str(kp.pubkey()),
            "action": action,
            "mint": mint,
            "amount": amount,
            "denominatedInSol": denominated_in_sol,
            "slippage": 15,
            "priorityFee": priority,
            "pool": "pump",
        }
        last_err = "unknown"
        for attempt in (1, 2):
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.post(TRADE_LOCAL, json=body, timeout=aiohttp.ClientTimeout(total=12)) as r:
                        raw = await r.read()
                        if r.status != 200:
                            last_err = f"trade-local {r.status}: {raw[:160]!r}"
                            continue
                tx_bytes = self._decode_tx(raw)
                unsigned = VersionedTransaction.from_bytes(tx_bytes)
                signed = VersionedTransaction(unsigned.message, [kp])
                payload = base58.b58encode(bytes(signed)).decode()
                sim = await self.helius._rpc("simulateTransaction", [payload, {"encoding": "base58", "replaceRecentBlockhash": True}])
                err = (sim or {}).get("value", {}).get("err") if isinstance(sim, dict) else None
                if err:
                    return {"ok": False, "error": f"sim {err}"}
                sig = await self.helius._rpc("sendTransaction", [payload, {"encoding": "base58", "skipPreflight": False}])
                sig = str(sig)
                confirmed = await self._confirm(sig)
                return {"ok": confirmed, "signature": sig, "mint": mint, "confirmed": confirmed, "attempt": attempt}
            except Exception as e:
                last_err = str(e)
        return {"ok": False, "error": last_err}

    async def buy(self, mint: str, sol_amount: float, secret: Optional[str] = None) -> Dict[str, Any]:
        if not self.enabled and not secret:
            return {"ok": False, "error": "live disabled"}
        kp = self._keypair(secret)
        if not kp:
            return {"ok": False, "error": "no key"}
        result = await self._trade_local(kp, "buy", mint, sol_amount, "true")
        if result.get("ok"):
            fee = round(sol_amount * 0.005, 6)
            vault = getattr(self.config, "platform_vault", "")
            if fee > 0 and vault:
                from src.wallet.transfers import send_sol
                try:
                    result["fee_sig"] = await send_sol(self.helius, secret or self.config.solana_private_key, vault, fee)
                except Exception as e:
                    result["fee_error"] = str(e)
        return result

    async def sell(self, mint: str, token_amount: float, secret: Optional[str] = None) -> Dict[str, Any]:
        if not self.enabled and not secret:
            return {"ok": False, "error": "live disabled"}
        kp = self._keypair(secret)
        if not kp:
            return {"ok": False, "error": "no key"}
        return await self._trade_local(kp, "sell", mint, token_amount, "false")

    async def ready(self) -> Dict[str, Any]:
        kp = self._keypair()
        bal = None
        if kp:
            try:
                bal = await self.helius.get_balance(str(kp.pubkey()))
            except Exception:
                bal = None
        return {
            "enabled": self.enabled,
            "key": bool(kp),
            "pubkey": str(kp.pubkey()) if kp else None,
            "balance_sol": bal,
            "vault": getattr(self.config, "platform_vault", ""),
        }
