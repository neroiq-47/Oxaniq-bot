"""x402 client — probe, parse 402, budget, and settlement scaffold.

Full ExactSvmScheme signing is gated. When X402_ENABLED and a wallet key
exist, we record the requirement and attempt a structured pay step.
"""

from __future__ import annotations
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import aiohttp
from src.utils.config import OxaniqConfig
from src.utils.logger import setup_logger
from src.utils.audit_log import audit

logger = setup_logger()
LEDGER = Path(__file__).resolve().parents[2] / "data" / "x402_ledger.json"


class X402Client:
    def __init__(self, config: OxaniqConfig):
        self.config = config
        self.enabled = config.x402_enabled
        self._session: Optional[aiohttp.ClientSession] = None
        self.daily_spent_usdc = 0.0
        self.day = ""
        self.last_status: Dict[str, Any] = {"probes": 0, "paid": 0, "last": None}
        self._load()

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load(self) -> None:
        if not LEDGER.exists():
            self.day = self._today()
            return
        try:
            data = json.loads(LEDGER.read_text())
            self.day = data.get("day", self._today())
            self.daily_spent_usdc = float(data.get("spent", 0))
            self.last_status = data.get("status") or self.last_status
        except Exception:
            self.day = self._today()
        if self.day != self._today():
            self.day = self._today()
            self.daily_spent_usdc = 0.0
            self._save()

    def _save(self) -> None:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        tmp = LEDGER.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "day": self.day,
            "spent": self.daily_spent_usdc,
            "status": self.last_status,
        }))
        os.replace(tmp, LEDGER)

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12))
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def can_spend(self, amount_usdc: float) -> bool:
        self._load()
        if not self.enabled:
            return False
        return (self.daily_spent_usdc + amount_usdc) <= self.config.x402_daily_spend_limit_usdc

    def _parse_requirement(self, headers: Dict[str, str], status: int) -> Dict[str, Any]:
        """Extract payment requirement from 402 headers (best-effort)."""
        req = {
            "http_status": status,
            "payment_required": status == 402,
            "scheme": None,
            "network": "solana",
            "asset": self.config.x402_usdc_mint,
            "max_amount": None,
            "facilitator": self.config.x402_facilitator_url,
            "raw_headers": {k: headers[k] for k in headers if "payment" in k or k.startswith("www-")},
        }
        # Common header shapes
        for key in ("payment-required", "www-authenticate", "x-payment-required"):
            if key in headers:
                req["scheme"] = headers[key][:200]
                break
        return req

    async def pay_for_resource(self, url: str, max_amount_usdc: float = 0.01) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            logger.debug("x402 disabled")
            return None
        if not self.can_spend(max_amount_usdc):
            logger.warning("x402 daily spend limit reached")
            audit("x402_blocked", url=url, reason="daily_limit")
            return {"status": "blocked", "reason": "daily_limit"}

        self.last_status["probes"] = self.last_status.get("probes", 0) + 1
        try:
            session = await self._sess()
            async with session.get(url, allow_redirects=False) as resp:
                headers = {k.lower(): v for k, v in resp.headers.items()}
                req = self._parse_requirement(headers, resp.status)
                body_preview = (await resp.text())[:200]
                result: Dict[str, Any] = {**req, "url": url, "body_preview": body_preview}

                if resp.status == 402:
                    # Budget reservation. Chain settle is attempted if operator key present.
                    self.daily_spent_usdc += max_amount_usdc
                    self.last_status["paid"] = self.last_status.get("paid", 0) + 1
                    result["status"] = "402_recorded"
                    result["amount_usdc"] = max_amount_usdc
                    result["settle"] = await self._attempt_settle(req, max_amount_usdc)
                    audit("x402_402", url=url, amount=max_amount_usdc, settle=result["settle"].get("status"))
                    logger.info(f"[cyan]x402 402[/] {url} reserved {max_amount_usdc} USDC")
                elif 200 <= resp.status < 300:
                    result["status"] = "free_ok"
                    audit("x402_free", url=url, status=resp.status)
                else:
                    result["status"] = f"http_{resp.status}"
                    audit("x402_other", url=url, status=resp.status)

                self.last_status["last"] = result
                self._save()
                return result
        except Exception as e:
            logger.warning(f"x402 probe failed: {e}")
            self.last_status["last"] = {"error": str(e), "url": url}
            audit("x402_error", url=url, error=str(e))
            self._save()
            return {"status": "error", "error": str(e)}

    async def _attempt_settle(self, requirement: Dict[str, Any], amount: float) -> Dict[str, Any]:
        """Settlement scaffold. Full ExactSvmScheme lands when signer is wired.
        For now we record intent and return a structured receipt if no key,
        or a signed-intent placeholder if operator key exists.
        """
        has_key = bool(self.config.solana_private_key)
        if not has_key:
            return {
                "status": "recorded_no_signer",
                "note": "Set SOLANA_PRIVATE_KEY to enable chain settlement",
                "amount_usdc": amount,
                "asset": requirement.get("asset"),
                "facilitator": requirement.get("facilitator"),
            }
        # Placeholder for ExactSvmScheme: do not broadcast unsigned junk
        return {
            "status": "signer_present_pending_scheme",
            "note": "Operator key present; ExactSvmScheme broadcast not enabled in this build",
            "amount_usdc": amount,
            "asset": requirement.get("asset"),
        }

    def status(self) -> Dict[str, Any]:
        self._load()
        return {
            "enabled": self.enabled,
            "day": self.day,
            "daily_spent_usdc": self.daily_spent_usdc,
            "limit_usdc": self.config.x402_daily_spend_limit_usdc,
            "facilitator": self.config.x402_facilitator_url,
            "probes": self.last_status.get("probes", 0),
            "paid": self.last_status.get("paid", 0),
            "last": (self.last_status.get("last") or {}).get("status"),
        }
