"""Pump.fun data ingestion (real + mock)."""

from __future__ import annotations
import asyncio
import json
from typing import AsyncIterator, Optional, Dict, Any
from datetime import datetime, timezone
import aiohttp
from src.core.models import TokenInfo
from src.ingestion.pumpdev_ws import PumpDevStream
from src.utils.config import OxaniqConfig
from src.utils.logger import setup_logger

logger = setup_logger()


class PumpFunIngester:
    def __init__(self, config: OxaniqConfig):
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_source: str = "none"
        self.last_count: int = 0
        self.pumpdev = PumpDevStream(config)

    async def start(self):
        self.session = aiohttp.ClientSession()
        await self.pumpdev.start()

    async def stop(self):
        await self.pumpdev.stop()
        if self.session:
            await self.session.close()

    async def fetch_new_tokens(self, limit: int = 20) -> list[TokenInfo]:
        """Live Pump.fun data: PumpDev WS first, then DexScreener."""
        if not self.session:
            await self.start()
        live_ws = self.pumpdev.snapshot(limit)
        if live_ws:
            self.last_source = "pumpdev"
            self.last_count = len(live_ws)
            return live_ws
        # 1) DexScreener latest profiles — reliable live source of pump mints
        live = await self._fetch_dexscreener(limit)
        if live:
            self.last_source = "live"
            self.last_count = len(live)
            return live
        # 2) Legacy official endpoints (often 403/530)
        base = self.config.pumpfun_api.rstrip("/")
        urls = [
            f"{base}/coins?offset=0&limit={limit}&sort=created_timestamp&order=DESC",
            f"https://frontend-api-v3.pump.fun/coins?offset=0&limit={limit}&sort=created_timestamp&order=DESC",
        ]
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Origin": "https://pump.fun"}
        for url in urls:
            try:
                async with self.session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    items = data if isinstance(data, list) else (data.get("coins") or data.get("data") or [])
                    tokens = [self._parse_token(i) for i in items if isinstance(i, dict)]
                    if tokens:
                        self.last_source = "live"
                        self.last_count = len(tokens)
                        return tokens[:limit]
            except Exception as e:
                logger.debug(f"Feed err: {e}")
        logger.warning("All launch feeds failed — using mock data")
        self.last_source = "mock"
        mocks = self._mock_tokens(limit)
        self.last_count = len(mocks)
        return mocks

    async def _fetch_dexscreener(self, limit: int) -> list[TokenInfo]:
        url = "https://api.dexscreener.com/token-profiles/latest/v1"
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.debug(f"DexScreener profiles {resp.status}")
                    return []
                data = await resp.json()
        except Exception as e:
            logger.debug(f"DexScreener profiles failed: {e}")
            return []
        if not isinstance(data, list):
            return []
        pumps = [
            x for x in data
            if x.get("chainId") == "solana" and str(x.get("tokenAddress", "")).endswith("pump")
        ][:limit]
        tokens: list[TokenInfo] = []
        for item in pumps:
            mint = item.get("tokenAddress")
            if not mint:
                continue
            enriched = await self._enrich_dex_pair(mint)
            raw = {**item, **(enriched or {}), "live": True, "source": "dexscreener"}
            tokens.append(self._parse_token(raw))
        if tokens:
            logger.info(f"Live Pump.fun feed: {len(tokens)} tokens via DexScreener")
        return tokens

    async def _enrich_dex_pair(self, mint: str) -> dict:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return {}
                body = await resp.json()
        except Exception:
            return {}
        pairs = body.get("pairs") or []
        if not pairs:
            return {}
        # Prefer pumpfun dex, else first
        pair = next((p for p in pairs if p.get("dexId") == "pumpfun"), pairs[0])
        base = pair.get("baseToken") or {}
        liq = pair.get("liquidity") or {}
        return {
            "mint": mint,
            "name": base.get("name"),
            "symbol": base.get("symbol"),
            "usd_market_cap": pair.get("fdv") or pair.get("marketCap"),
            "price_usd": pair.get("priceUsd"),
            "liquidity_usd": liq.get("usd"),
            "dex_id": pair.get("dexId"),
            "complete": pair.get("dexId") == "pumpswap",
            "pair_created_at": pair.get("pairCreatedAt"),
        }

    def _parse_token(self, item: Dict[str, Any]) -> TokenInfo:
        return TokenInfo(
            mint=item.get("mint") or item.get("address") or "unknown",
            name=item.get("name") or "Unknown",
            symbol=item.get("symbol") or "???",
            description=item.get("description"),
            image_uri=item.get("image_uri") or item.get("uri"),
            creator=item.get("creator"),
            created_timestamp=item.get("created_timestamp"),
            market_cap_sol=item.get("usd_market_cap") or item.get("market_cap"),
            bonding_curve=item.get("bonding_curve"),
            associated_bonding_curve=item.get("associated_bonding_curve"),
            complete=bool(item.get("complete")),
            virtual_sol_reserves=item.get("virtual_sol_reserves"),
            virtual_token_reserves=item.get("virtual_token_reserves"),
            raw=item,
        )

    def _mock_tokens(self, n: int = 5) -> list[TokenInfo]:
        """Unique mints per call so paper dedup does not block forever."""
        import uuid
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        batch = uuid.uuid4().hex[:8]
        mocks = []
        for i in range(n):
            mint = f"Mock{batch}{i}pump"
            mocks.append(
                TokenInfo(
                    mint=mint,
                    name=f"Oxaniq Moon {batch[:4]}-{i}",
                    symbol=f"OXN{i}{batch[:2].upper()}",
                    description="Mock token for Oxaniq paper trading",
                    creator="MockCreator111",
                    created_timestamp=now - i * 30_000,
                    market_cap_sol=5.0 + i * 2,
                    virtual_sol_reserves=3.0 + i * 1.5,
                    virtual_token_reserves=800_000_000,
                    complete=False,
                    raw={"mock": True, "batch": batch},
                )
            )
        return mocks

    async def stream_new_launches(self) -> AsyncIterator[TokenInfo]:
        """Placeholder for WebSocket stream (PumpPortal style).
        Currently polls every few seconds for demo.
        """
        while True:
            tokens = await self.fetch_new_tokens(10)
            for t in tokens:
                yield t
            await asyncio.sleep(8)
