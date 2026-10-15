"""Helius health-check and priority-fee helpers for Oxaniq."""

from __future__ import annotations
import asyncio
from typing import Optional, Dict, Any, List
import aiohttp
from src.utils.config import OxaniqConfig, get_config
from src.utils.logger import setup_logger

logger = setup_logger()


class HeliusHelper:
    """Thin wrapper around Helius RPC for health and priority fees."""

    def __init__(self, config: Optional[OxaniqConfig] = None):
        self.config = config or get_config()
        self.rpc_url = self.config.solana_rpc_url
        self.api_key = self.config.helius_api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=12)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _rpc(self, method: str, params: list | None = None) -> Dict[str, Any]:
        session = await self._get_session()
        payload = {
            "jsonrpc": "2.0",
            "id": "oxaniq",
            "method": method,
            "params": params or [],
        }
        async with session.post(self.rpc_url, json=payload) as resp:
            data = await resp.json()
            if "error" in data:
                raise RuntimeError(f"RPC error: {data['error']}")
            return data.get("result")

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------
    async def health_check(self) -> Dict[str, Any]:
        """
        Returns a simple health report:
        - slot
        - block height
        - version
        - latency_ms
        - ok (bool)
        """
        import time
        start = time.perf_counter()
        try:
            slot = await self._rpc("getSlot")
            version = await self._rpc("getVersion")
            block_height = await self._rpc("getBlockHeight")
            latency = (time.perf_counter() - start) * 1000

            report = {
                "ok": True,
                "slot": slot,
                "block_height": block_height,
                "version": version.get("solana-core") if isinstance(version, dict) else version,
                "latency_ms": round(latency, 1),
                "rpc": "helius" if self.api_key else "public",
            }
            logger.info(
                f"[green]Helius healthy[/] | slot={slot} | "
                f"latency={report['latency_ms']}ms | version={report['version']}"
            )
            return report
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            logger.warning(f"[red]Helius health check failed[/]: {e}")
            return {
                "ok": False,
                "error": str(e),
                "latency_ms": round(latency, 1),
                "rpc": "helius" if self.api_key else "public",
            }

    # ------------------------------------------------------------------
    # Priority fee helper
    # ------------------------------------------------------------------
    async def get_priority_fee(
        self,
        account_keys: Optional[List[str]] = None,
        level: str = "medium",
    ) -> Dict[str, Any]:
        """
        Estimate priority fee using Helius getPriorityFeeEstimate (or fallback).

        level: "min" | "low" | "medium" | "high" | "veryHigh" | "unsafeMax"
        Returns microlamports per compute unit and a recommended tip.
        """
        # Preferred: Helius-specific method
        try:
            params = {
                "options": {
                    "priorityLevel": level,
                    "includeAllPriorityFeeLevels": True,
                }
            }
            if account_keys:
                params["accountKeys"] = account_keys

            result = await self._rpc("getPriorityFeeEstimate", [params])

            # Normalise response
            if isinstance(result, dict):
                # Newer Helius response shape
                levels = result.get("priorityFeeLevels") or {}
                estimate = result.get("priorityFeeEstimate")
                chosen = estimate or levels.get(level) or levels.get("medium") or 0
                return {
                    "source": "helius",
                    "level": level,
                    "priority_fee_microlamports": int(chosen),
                    "all_levels": levels,
                    "raw": result,
                }
            else:
                # Older numeric response
                return {
                    "source": "helius",
                    "level": level,
                    "priority_fee_microlamports": int(result or 0),
                    "all_levels": {},
                    "raw": result,
                }
        except Exception as e:
            logger.debug(f"Helius priority fee failed ({e}), using fallback")

        # Fallback: recent prioritization fees
        try:
            fees = await self._rpc("getRecentPrioritizationFees", [account_keys or []])
            if fees and isinstance(fees, list):
                # Take a high percentile
                values = sorted([f.get("prioritizationFee", 0) for f in fees if f])
                if values:
                    idx = min(len(values) - 1, int(len(values) * 0.75))
                    chosen = max(values[idx], 1000)  # never below 1k µL
                    return {
                        "source": "recent_fees_fallback",
                        "level": "p75",
                        "priority_fee_microlamports": int(chosen),
                        "all_levels": {},
                        "raw": {"sample_size": len(values)},
                    }
        except Exception as e2:
            logger.warning(f"Priority fee fallback also failed: {e2}")

        # Last resort hard-coded safe values (microlamports)
        defaults = {
            "min": 1_000,
            "low": 5_000,
            "medium": 50_000,
            "high": 200_000,
            "veryHigh": 500_000,
            "unsafeMax": 1_000_000,
        }
        return {
            "source": "hardcoded",
            "level": level,
            "priority_fee_microlamports": defaults.get(level, 50_000),
            "all_levels": defaults,
            "raw": None,
        }

    async def recommend_fee(self, urgency: str = "normal") -> int:
        """
        Convenience helper.
        urgency: "low" | "normal" | "high" | "critical"
        Returns microlamports per CU.
        """
        mapping = {
            "low": "low",
            "normal": "medium",
            "high": "high",
            "critical": "veryHigh",
        }
        level = mapping.get(urgency, "medium")
        info = await self.get_priority_fee(level=level)
        fee = info["priority_fee_microlamports"]
        logger.debug(f"Recommended priority fee ({urgency}): {fee} µL/CU")
        return fee
