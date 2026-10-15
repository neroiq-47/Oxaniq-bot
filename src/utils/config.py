"""Oxaniq configuration loader."""

from __future__ import annotations
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "config" / "example.env")


@dataclass
class RiskConfig:
    max_position_size_sol: float = 0.05
    max_open_positions: int = 3
    daily_loss_limit_sol: float = 0.2
    max_slippage_bps: int = 300
    min_confidence_score: float = 0.40
    # Phase 1 exits
    take_profit_pct: float = 0.15          # +15%
    stop_loss_pct: float = 0.08            # -8%
    max_hold_seconds: int = 300            # 5 min default


@dataclass
class OxaniqConfig:
    paper_trading: bool = True
    log_level: str = "INFO"
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    helius_api_key: Optional[str] = None
    jupiter_api_key: Optional[str] = None
    solana_private_key: Optional[str] = None
    wallet_pubkey: Optional[str] = None
    risk: RiskConfig = field(default_factory=RiskConfig)
    pumpfun_api: str = "https://frontend-api.pump.fun"
    pumpportal_ws: str = "wss://pumpportal.fun/api/data"
    pumpdev_ws: str = "wss://pumpdev.io/ws"
    pumpdev_api_key: Optional[str] = None
    vybe_api: str = "https://api.vybenetwork.xyz"
    vybe_api_key: Optional[str] = None
    fomo_api_url: Optional[str] = None
    fomo_api_key: Optional[str] = None
    usdc_mint: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    x402_facilitator_url: str = "https://payai.network"
    x402_usdc_mint: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    x402_daily_spend_limit_usdc: float = 5.0
    x402_enabled: bool = False
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_channel_id: Optional[str] = None
    telegram_admin_ids: List[str] = field(default_factory=list)
    platform_fee_bps: int = 50  # 0.5%
    platform_vault: str = "92gMu7MDKw5CuGxGKjUbaDx2BeuFBo9ttUkJSb7yABM1"
    access_price_usd: float = 11.99
    access_required: bool = True
    free_quotes_per_day: int = 1
    max_calls_per_day: int = 300
    min_call_score: float = 0.30
    min_call_confidence: float = 0.28
    call_mode: str = "trade"  # alert | trade
    live_enabled: bool = False
    min_curve_sol: float = 2.0
    max_curve_sol: float = 40.0
    max_call_mcap_sol: float = 30.0
    max_dev_buy_sol: float = 2.0
    tg_rate_per_min: int = 30

    @classmethod
    def from_env(cls) -> "OxaniqConfig":
        risk = RiskConfig(
            max_position_size_sol=float(os.getenv("MAX_POSITION_SIZE_SOL", 0.05)),
            max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", 3)),
            daily_loss_limit_sol=float(os.getenv("DAILY_LOSS_LIMIT_SOL", 0.2)),
            max_slippage_bps=int(os.getenv("MAX_SLIPPAGE_BPS", 300)),
            min_confidence_score=float(os.getenv("MIN_CONFIDENCE_SCORE", 0.40)),
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", 0.15)),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", 0.08)),
            max_hold_seconds=int(os.getenv("MAX_HOLD_SECONDS", 300)),
        )
        helius_key = os.getenv("HELIUS_API_KEY") or None
        rpc_url = os.getenv("SOLANA_RPC_URL")
        if not rpc_url and helius_key:
            rpc_url = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
        if not rpc_url:
            rpc_url = "https://api.mainnet-beta.solana.com"

        admin_raw = os.getenv("TELEGRAM_ADMIN_IDS", "") or ""
        admin_ids = [x.strip() for x in admin_raw.split(",") if x.strip()]

        return cls(
            paper_trading=os.getenv("PAPER_TRADING", "true").lower() in ("1", "true", "yes"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            solana_rpc_url=rpc_url,
            helius_api_key=helius_key,
            jupiter_api_key=os.getenv("JUPITER_API_KEY") or None,
            solana_private_key=os.getenv("SOLANA_PRIVATE_KEY") or None,
            wallet_pubkey=os.getenv("WALLET_PUBKEY") or None,
            risk=risk,
            pumpfun_api=os.getenv("PUMPFUN_API", "https://frontend-api.pump.fun"),
            pumpportal_ws=os.getenv("PUMPPORTAL_WS", "wss://pumpportal.fun/api/data"),
            pumpdev_ws=os.getenv("PUMPDEV_WS", "wss://pumpdev.io/ws"),
            pumpdev_api_key=os.getenv("PUMPDEV_API_KEY") or None,
            vybe_api=os.getenv("VYBE_API", "https://api.vybenetwork.xyz"),
            vybe_api_key=os.getenv("VYBE_API_KEY") or None,
            fomo_api_url=os.getenv("FOMO_API_URL") or None,
            fomo_api_key=os.getenv("FOMO_API_KEY") or None,
            usdc_mint=os.getenv("USDC_MINT", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"),
            x402_facilitator_url=os.getenv("X402_FACILITATOR_URL", "https://payai.network"),
            x402_usdc_mint=os.getenv("X402_USDC_MINT", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"),
            x402_daily_spend_limit_usdc=float(os.getenv("X402_DAILY_SPEND_LIMIT_USDC", 5.0)),
            x402_enabled=os.getenv("X402_ENABLED", "false").lower() in ("1", "true", "yes"),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
            telegram_channel_id=os.getenv("TELEGRAM_CHANNEL_ID") or os.getenv("TELEGRAM_CHANNEL") or None,
            telegram_admin_ids=admin_ids,
            platform_fee_bps=int(os.getenv("PLATFORM_FEE_BPS", 50)),
            platform_vault=os.getenv("PLATFORM_VAULT", "92gMu7MDKw5CuGxGKjUbaDx2BeuFBo9ttUkJSb7yABM1"),
            access_price_usd=float(os.getenv("ACCESS_PRICE_USD", 11.99)),
            access_required=os.getenv("ACCESS_REQUIRED", "true").lower() in ("1", "true", "yes"),
            free_quotes_per_day=int(os.getenv("FREE_QUOTES_PER_DAY", 1)),
            max_calls_per_day=int(os.getenv("MAX_CALLS_PER_DAY", 300)),
            min_call_score=float(os.getenv("MIN_CALL_SCORE", 0.30)),
            min_call_confidence=float(os.getenv("MIN_CALL_CONFIDENCE", 0.28)),
            call_mode=os.getenv("CALL_MODE", "trade").lower(),
            live_enabled=os.getenv("LIVE_ENABLED", "false").lower() in ("1", "true", "yes"),
            min_curve_sol=float(os.getenv("MIN_CURVE_SOL", 2.0)),
            max_curve_sol=float(os.getenv("MAX_CURVE_SOL", 40.0)),
            max_call_mcap_sol=float(os.getenv("MAX_CALL_MCAP_SOL", 30.0)),
            max_dev_buy_sol=float(os.getenv("MAX_DEV_BUY_SOL", 2.0)),
            tg_rate_per_min=int(os.getenv("TG_RATE_PER_MIN", 30)),
        )


def get_config() -> OxaniqConfig:
    return OxaniqConfig.from_env()
