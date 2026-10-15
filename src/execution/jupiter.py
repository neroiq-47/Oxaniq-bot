"""Jupiter quote & swap helpers for Oxaniq.

Recommended live path (not enabled by default):
  1) get_quote  2) build swap tx  3) simulate on Helius
  4) sign with local wallet  5) sendRawTransaction
See docs/JUPITER_WALLET.md
"""

from __future__ import annotations
from typing import Optional, Dict, Any, List
import aiohttp
from src.utils.config import OxaniqConfig, get_config
from src.utils.logger import setup_logger

logger = setup_logger()

# Official Jupiter API endpoints (v6)
JUPITER_QUOTE_URL = "https://api.jup.ag/swap/v1/quote"
JUPITER_SWAP_URL = "https://api.jup.ag/swap/v1/swap"


class JupiterClient:
    """Lightweight Jupiter client for quotes (and future swaps)."""

    def __init__(self, config: Optional[OxaniqConfig] = None):
        self.config = config or get_config()
        self.api_key = self.config.jupiter_api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {}
            if self.api_key:
                # Jupiter accepts the key via header or query; header is cleaner
                headers["x-api-key"] = self.api_key
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers=headers,
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 300,
        swap_mode: str = "ExactIn",
        only_direct_routes: bool = False,
        as_legacy_transaction: bool = False,
        max_accounts: int = 64,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch a swap quote from Jupiter.

        Args:
            input_mint: Source token mint (e.g. So111... for SOL)
            output_mint: Destination token mint
            amount: Amount in the smallest unit (lamports / token base units)
            slippage_bps: Slippage tolerance in basis points (300 = 3%)
            swap_mode: "ExactIn" or "ExactOut"

        Returns:
            Full Jupiter quote response or None on failure.
        """
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": str(slippage_bps),
            "swapMode": swap_mode,
            "onlyDirectRoutes": str(only_direct_routes).lower(),
            "asLegacyTransaction": str(as_legacy_transaction).lower(),
            "maxAccounts": str(max_accounts),
        }

        # Some Jupiter plans also accept the key as a query param
        if self.api_key:
            params["apiKey"] = self.api_key

        try:
            session = await self._get_session()
            async with session.get(JUPITER_QUOTE_URL, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(f"Jupiter quote HTTP {resp.status}: {text[:200]}")
                    return None
                data = await resp.json()
                logger.debug(
                    f"Jupiter quote OK | in={input_mint[:6]}… out={output_mint[:6]}… "
                    f"inAmount={data.get('inAmount')} outAmount={data.get('outAmount')}"
                )
                return data
        except Exception as e:
            logger.warning(f"Jupiter quote failed: {e}")
            return None

    async def quote_sol_to_token(
        self,
        token_mint: str,
        sol_amount: float,
        slippage_bps: int = 300,
    ) -> Optional[Dict[str, Any]]:
        """Convenience: quote buying `token_mint` with `sol_amount` SOL."""
        SOL_MINT = "So11111111111111111111111111111111111111112"
        lamports = int(sol_amount * 1_000_000_000)
        return await self.get_quote(
            input_mint=SOL_MINT,
            output_mint=token_mint,
            amount=lamports,
            slippage_bps=slippage_bps,
        )

    async def quote_token_to_sol(
        self,
        token_mint: str,
        token_amount: int,
        slippage_bps: int = 300,
    ) -> Optional[Dict[str, Any]]:
        """Convenience: quote selling `token_amount` of `token_mint` for SOL."""
        SOL_MINT = "So11111111111111111111111111111111111111112"
        return await self.get_quote(
            input_mint=token_mint,
            output_mint=SOL_MINT,
            amount=token_amount,
            slippage_bps=slippage_bps,
        )

    def summarize_quote(self, quote: Dict[str, Any]) -> Dict[str, Any]:
        """Human-friendly summary of a Jupiter quote."""
        if not quote:
            return {}
        in_amount = int(quote.get("inAmount", 0))
        out_amount = int(quote.get("outAmount", 0))
        price_impact = float(quote.get("priceImpactPct") or 0)
        route_plan = quote.get("routePlan") or []
        return {
            "in_amount": in_amount,
            "out_amount": out_amount,
            "price_impact_pct": price_impact,
            "route_hops": len(route_plan),
            "slippage_bps": quote.get("slippageBps"),
            "other_amount_threshold": quote.get("otherAmountThreshold"),
        }

    async def get_swap_transaction(
        self,
        quote_response: Dict[str, Any],
        user_public_key: str,
        prioritization_fee_lamports: int | None = None,
    ) -> Optional[Dict[str, Any]]:
        """Build unsigned swap tx from a quote. Does not sign or send.

        Live trading must: simulate → sign locally → send via RPC.
        """
        if not quote_response or not user_public_key:
            return None
        session = await self._get_session()
        body: Dict[str, Any] = {
            "quoteResponse": quote_response,
            "userPublicKey": user_public_key,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
        }
        if prioritization_fee_lamports is not None:
            body["prioritizationFeeLamports"] = prioritization_fee_lamports
        try:
            async with session.post(JUPITER_SWAP_URL, json=body) as resp:
                if resp.status != 200:
                    txt = await resp.text()
                    logger.warning(f"Jupiter swap build {resp.status}: {txt[:200]}")
                    return None
                data = await resp.json()
                logger.info("Jupiter swap tx built (unsigned)")
                return data
        except Exception as e:
            logger.warning(f"Jupiter swap build failed: {e}")
            return None

    async def quote_sol_to_usdc(self, sol_amount: float = 0.01, slippage_bps: int = 50):
        """Quote SOL → mainnet USDC."""
        from src.utils.config import get_config
        usdc = getattr(get_config(), "usdc_mint", None) or "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        SOL = "So11111111111111111111111111111111111111112"
        return await self.get_quote(
            input_mint=SOL,
            output_mint=usdc,
            amount=int(sol_amount * 1_000_000_000),
            slippage_bps=slippage_bps,
        )

    async def quote_usdc_to_sol(self, usdc_amount: float = 1.0, slippage_bps: int = 50):
        """Quote USDC → SOL (usdc_amount in whole USDC)."""
        from src.utils.config import get_config
        usdc = getattr(get_config(), "usdc_mint", None) or "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        SOL = "So11111111111111111111111111111111111111112"
        return await self.get_quote(
            input_mint=usdc,
            output_mint=SOL,
            amount=int(usdc_amount * 1_000_000),
            slippage_bps=slippage_bps,
        )
