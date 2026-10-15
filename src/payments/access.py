"""$11.99 access rail — pay in SOL to the platform vault."""

from __future__ import annotations
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import aiohttp
from src.utils.logger import setup_logger
from src.utils.audit_log import audit

logger = setup_logger()
STORE = Path(__file__).resolve().parents[2] / "data" / "paid_users.json"
PRICE_USD = 11.99


class AccessRail:
    def __init__(self, vault: str, price_usd: float = PRICE_USD):
        self.vault = vault
        self.price_usd = price_usd
        self._paid: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if not STORE.exists():
            return {}
        try:
            return json.loads(STORE.read_text())
        except Exception:
            return {}

    def _save(self) -> None:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._paid, indent=2))
        os.replace(tmp, STORE)

    def is_paid(self, user_id: str) -> bool:
        return str(user_id) in self._paid

    def grant(self, user_id: str, sig: str, sol: float) -> None:
        self._paid[str(user_id)] = {
            "sig": sig,
            "sol": sol,
            "usd": self.price_usd,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()
        audit("access_paid", user_id=str(user_id), sig=sig, sol=sol)

    async def sol_amount(self) -> Optional[float]:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd",
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as r:
                    data = await r.json()
                    usd = float(data["solana"]["usd"])
                    if usd <= 0:
                        return None
                    return round(self.price_usd / usd, 4)
        except Exception as e:
            logger.warning(f"SOL price failed: {e}")
            return None

    async def verify_tx(self, helius, signature: str, min_sol: float) -> Dict[str, Any]:
        try:
            tx = await helius._rpc(
                "getTransaction",
                [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            )
        except Exception as e:
            return {"ok": False, "error": str(e)}
        if not tx:
            return {"ok": False, "error": "tx not found"}
        meta = tx.get("meta") or {}
        if meta.get("err"):
            return {"ok": False, "error": "tx failed"}
        found = 0.0
        msg = (tx.get("transaction") or {}).get("message") or {}
        for ix in msg.get("instructions") or []:
            parsed = ix.get("parsed") or {}
            if parsed.get("type") != "transfer":
                continue
            info = parsed.get("info") or {}
            if info.get("destination") == self.vault:
                found += int(info.get("lamports") or 0) / 1_000_000_000
        for inner in meta.get("innerInstructions") or []:
            for ix in inner.get("instructions") or []:
                parsed = ix.get("parsed") or {}
                if parsed.get("type") != "transfer":
                    continue
                info = parsed.get("info") or {}
                if info.get("destination") == self.vault:
                    found += int(info.get("lamports") or 0) / 1_000_000_000
        if found + 1e-6 < min_sol * 0.98:
            return {"ok": False, "error": f"paid {found:.4f} SOL, need {min_sol:.4f}"}
        return {"ok": True, "sol": found}
