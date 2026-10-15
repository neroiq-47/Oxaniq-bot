"""PumpDev live websocket: wss://pumpdev.io/ws?key=YOUR_KEY"""

from __future__ import annotations
import asyncio
import json
from typing import Optional, List, Dict, Any
import websockets
from src.utils.config import OxaniqConfig, get_config
from src.utils.logger import setup_logger
from src.core.models import TokenInfo

logger = setup_logger()


class PumpDevStream:
    def __init__(self, config: Optional[OxaniqConfig] = None):
        self.config = config or get_config()
        self.base = (self.config.pumpdev_ws or "wss://pumpdev.io/ws").rstrip("/")
        self.api_key = (self.config.pumpdev_api_key or "").strip() or None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.recent: List[TokenInfo] = []
        self.connected = False
        self.last_error: Optional[str] = None

    def _url(self) -> str:
        if self.api_key:
            return f"{self.base}?key={self.api_key}"
        return self.base

    def _to_token(self, event: Dict[str, Any]) -> Optional[TokenInfo]:
        if event.get("txType") != "create":
            return None
        mint = event.get("mint")
        if not mint:
            return None
        return TokenInfo(
            mint=mint,
            name=event.get("name") or "Unknown",
            symbol=event.get("symbol") or "???",
            creator=event.get("traderPublicKey"),
            created_timestamp=event.get("timestamp"),
            bonding_curve=event.get("bondingCurveKey"),
            complete=False,
            virtual_sol_reserves=event.get("vSolInBondingCurve"),
            virtual_token_reserves=event.get("vTokensInBondingCurve"),
            market_cap_sol=event.get("marketCapSol"),
            raw={**event, "live": True, "source": "pumpdev"},
        )

    async def start(self):
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"PumpDev stream starting → {self.base}?key={'set' if self.api_key else 'none'}")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.connected = False

    async def _loop(self):
        url = self._url()
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    self.connected = True
                    self.last_error = None
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    logger.info("PumpDev subscribed: subscribeNewToken")
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            event = json.loads(raw)
                        except Exception:
                            continue
                        tok = self._to_token(event)
                        if tok:
                            self.recent.insert(0, tok)
                            self.recent = self.recent[:50]
                            logger.info(f"PumpDev create {tok.symbol} {tok.mint[:8]}…")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.connected = False
                self.last_error = str(e)
                logger.warning(f"PumpDev ws error: {e}")
                await asyncio.sleep(5)

    def snapshot(self, limit: int = 20) -> List[TokenInfo]:
        return self.recent[:limit]
