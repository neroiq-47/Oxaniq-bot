"""Live execution scaffold — PumpDev trade-local + local sign.

Gated by config.live_enabled. Paper remains the default path.
"""

from __future__ import annotations
from typing import Optional, Dict, Any
import aiohttp
import base58
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from src.utils.config import OxaniqConfig
from src.utils.logger import setup_logger

logger = setup_logger()


class LiveExecutor:
    def __init__(self, config: OxaniqConfig, helius):
        self.config = config
        self.helius = helius
        self.enabled = bool(config.live_enabled and config.solana_private_key)

    def _keypair(self) -> Optional[Keypair]:
        raw = (self.config.solana_private_key or "").strip()
        if not raw:
            return None
        try:
            return Keypair.from_base58_string(raw)
        except Exception as e:
            logger.warning(f"live keypair load failed: {e}")
            return None

    async def buy(self, mint: str, sol_amount: float, slippage: int = 15) -> Dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "live disabled"}
        kp = self._keypair()
        if not kp:
            return {"ok": False, "error": "no key"}
        body = {
            "publicKey": str(kp.pubkey()),
            "action": "buy",
            "mint": mint,
            "amount": sol_amount,
            "denominatedInSol": "true",
            "slippage": slippage,
            "priorityFee": 0.0005,
            "pool": "pump",
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post("https://pumpdev.io/api/trade-local", json=body, timeout=aiohttp.ClientTimeout(total=12)) as r:
                    if r.status != 200:
                        txt = await r.text()
                        return {"ok": False, "error": f"trade-local {r.status}: {txt[:160]}"}
                    raw = await r.read()
            # response is serialized tx bytes or json
            try:
                import json
                data = json.loads(raw)
                b58 = data if isinstance(data, str) else data.get("transaction") or data.get("tx")
                tx_bytes = base58.b58decode(b58)
            except Exception:
                tx_bytes = raw
            unsigned = VersionedTransaction.from_bytes(tx_bytes)
            signed = VersionedTransaction(unsigned.message, [kp])
            payload = base58.b58encode(bytes(signed)).decode()
            sig = await self.helius._rpc("sendTransaction", [payload, {"encoding": "base58", "skipPreflight": False}])
            logger.info(f"LIVE BUY sent {sig}")
            return {"ok": True, "signature": str(sig), "mint": mint, "amount": sol_amount}
        except Exception as e:
            logger.warning(f"live buy failed: {e}")
            return {"ok": False, "error": str(e)}
