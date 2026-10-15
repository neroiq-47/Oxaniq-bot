"""Core data models for Oxaniq."""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any, List
from uuid import uuid4


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"
    PAPER = "paper"


@dataclass
class TokenInfo:
    mint: str
    name: str
    symbol: str
    description: Optional[str] = None
    image_uri: Optional[str] = None
    creator: Optional[str] = None
    created_timestamp: Optional[int] = None
    market_cap_sol: Optional[float] = None
    bonding_curve: Optional[str] = None
    associated_bonding_curve: Optional[str] = None
    complete: bool = False  # graduated?
    virtual_sol_reserves: Optional[float] = None
    virtual_token_reserves: Optional[float] = None
    total_supply: float = 1_000_000_000
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Signal:
    token: TokenInfo
    source: str = "scorer"  # "pumpfun", "fomo", "wallet_tracker", etc.
    id: str = field(default_factory=lambda: str(uuid4()))
    score: float = 0.0  # 0-1
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)
    fomo_intensity: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    mint: str
    symbol: str
    id: str = field(default_factory=lambda: str(uuid4()))
    side: Side = Side.BUY
    entry_price_sol: float = 0.0  # price per token in SOL (approx)
    size_sol: float = 0.0  # amount spent
    tokens_held: float = 0.0
    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    current_price_sol: Optional[float] = None
    unrealized_pnl_sol: float = 0.0
    status: str = "open"
    stop_loss_sol: Optional[float] = None
    take_profit_sol: Optional[float] = None
    notes: str = ""


@dataclass
class Trade:
    mint: str
    symbol: str
    side: Side
    size_sol: float
    tokens: float
    price_sol: float
    status: OrderStatus
    id: str = field(default_factory=lambda: str(uuid4()))
    tx_signature: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    paper: bool = True
    pnl_sol: Optional[float] = None
    notes: str = ""
    signal_id: Optional[str] = None


@dataclass
class PortfolioState:
    cash_sol: float = 1.0  # starting paper capital
    positions: Dict[str, Position] = field(default_factory=dict)
    trades: List[Trade] = field(default_factory=list)
    daily_pnl_sol: float = 0.0
    total_pnl_sol: float = 0.0
    last_reset: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
