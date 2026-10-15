"""Vybe Solana analytics — subscription API (X-API-Key)."""

from __future__ import annotations
from typing import Optional, Dict, Any
import aiohttp
from src.utils.config import OxaniqConfig, get_config
from src.utils.logger import setup_logger

logger = setup_logger()


class VybeClient:
    def __init__(self, config: Optional[OxaniqConfig] = None):
        self.config = config or get_config()
        self.base = (self.config.vybe_api or "https://api.vybenetwork.xyz").rstrip("/")
        self.key = (self.config.vybe_api_key or "").strip() or None

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _headers(self) -> Dict[str, str]:
        return {"X-API-Key": self.key or "", "Accept": "application/json"}

    async def get(self, path: str) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        url = path if path.startswith("http") else f"{self.base}{path}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=12)) as r:
                    if r.status != 200:
                        logger.warning(f"Vybe {r.status} {path}")
                        return None
                    return await r.json()
        except Exception as e:
            logger.warning(f"Vybe error: {e}")
            return None

    async def token(self, mint: str) -> Optional[Dict[str, Any]]:
        return await self.get(f"/v4/tokens/{mint}")

    async def top_holders(self, mint: str) -> Optional[Dict[str, Any]]:
        return await self.get(f"/v4/tokens/{mint}/top-holders")
