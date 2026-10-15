"""Persist paper portfolio to disk. Atomic writes."""

from __future__ import annotations
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from src.core.models import PortfolioState, Position, Trade, Side, OrderStatus
from src.utils.logger import setup_logger

logger = setup_logger()
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "portfolio.json"


def _dt(s: Optional[str]) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def save_portfolio(portfolio: PortfolioState, path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "cash_sol": portfolio.cash_sol,
        "daily_pnl_sol": portfolio.daily_pnl_sol,
        "total_pnl_sol": portfolio.total_pnl_sol,
        "last_reset": portfolio.last_reset.isoformat(),
        "positions": {
            mint: {
                "mint": p.mint,
                "symbol": p.symbol,
                "id": p.id,
                "side": p.side.value if hasattr(p.side, "value") else str(p.side),
                "entry_price_sol": p.entry_price_sol,
                "size_sol": p.size_sol,
                "tokens_held": p.tokens_held,
                "entry_time": p.entry_time.isoformat(),
                "status": p.status,
                "notes": p.notes,
            }
            for mint, p in portfolio.positions.items()
            if p.status == "open"
        },
        "trades": [
            {
                "mint": t.mint,
                "symbol": t.symbol,
                "side": t.side.value if hasattr(t.side, "value") else str(t.side),
                "size_sol": t.size_sol,
                "tokens": t.tokens,
                "price_sol": t.price_sol,
                "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                "id": t.id,
                "timestamp": t.timestamp.isoformat(),
                "paper": t.paper,
                "pnl_sol": t.pnl_sol,
                "notes": t.notes,
                "signal_id": t.signal_id,
            }
            for t in portfolio.trades[-200:]
        ],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)
    logger.debug(f"Portfolio saved → {path}")


def load_portfolio(path: Path = DEFAULT_PATH, default_cash: float = 1.0) -> PortfolioState:
    if not path.exists():
        return PortfolioState(cash_sol=default_cash)
    try:
        data = json.loads(path.read_text())
        positions = {}
        for mint, raw in (data.get("positions") or {}).items():
            positions[mint] = Position(
                mint=raw["mint"],
                symbol=raw["symbol"],
                id=raw.get("id", mint),
                side=Side.BUY if raw.get("side") == "buy" else Side.SELL,
                entry_price_sol=float(raw.get("entry_price_sol", 0)),
                size_sol=float(raw.get("size_sol", 0)),
                tokens_held=float(raw.get("tokens_held", 0)),
                entry_time=_dt(raw.get("entry_time")),
                status=raw.get("status", "open"),
                notes=raw.get("notes", ""),
            )
        trades = []
        for raw in data.get("trades") or []:
            side = Side.BUY if raw.get("side") == "buy" else Side.SELL
            try:
                status = OrderStatus(raw.get("status", "paper"))
            except Exception:
                status = OrderStatus.PAPER
            trades.append(
                Trade(
                    mint=raw["mint"],
                    symbol=raw["symbol"],
                    side=side,
                    size_sol=float(raw.get("size_sol", 0)),
                    tokens=float(raw.get("tokens", 0)),
                    price_sol=float(raw.get("price_sol", 0)),
                    status=status,
                    id=raw.get("id", ""),
                    timestamp=_dt(raw.get("timestamp")),
                    paper=bool(raw.get("paper", True)),
                    pnl_sol=raw.get("pnl_sol"),
                    notes=raw.get("notes", ""),
                    signal_id=raw.get("signal_id"),
                )
            )
        state = PortfolioState(
            cash_sol=float(data.get("cash_sol", default_cash)),
            positions=positions,
            trades=trades,
            daily_pnl_sol=float(data.get("daily_pnl_sol", 0)),
            total_pnl_sol=float(data.get("total_pnl_sol", 0)),
            last_reset=_dt(data.get("last_reset")),
        )
        logger.info(f"Loaded portfolio from {path} | cash={state.cash_sol:.4f} open={len(state.positions)}")
        return state
    except Exception as e:
        logger.warning(f"Failed to load portfolio ({e}) — starting fresh")
        return PortfolioState(cash_sol=default_cash)
