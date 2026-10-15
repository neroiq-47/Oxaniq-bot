"""Risk management for Oxaniq."""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional, Tuple
from src.core.models import Signal, PortfolioState, Position
from src.utils.config import RiskConfig
from src.utils.logger import setup_logger

logger = setup_logger()


class RiskManager:
    def __init__(self, config: RiskConfig, portfolio: PortfolioState):
        self.config = config
        self.portfolio = portfolio
        self.killed = False
        self.daily_loss = 0.0

    def reset_daily_if_needed(self) -> None:
        now = datetime.now(timezone.utc)
        if (now - self.portfolio.last_reset).days >= 1:
            self.portfolio.daily_pnl_sol = 0.0
            self.portfolio.last_reset = now
            self.daily_loss = 0.0
            logger.info("Daily risk counters reset")

    def can_open_position(self, signal: Signal, size_sol: float) -> Tuple[bool, str]:
        self.reset_daily_if_needed()

        if self.killed:
            return False, "Agent is killed (manual or loss limit)"

        if self.portfolio.daily_pnl_sol <= -self.config.daily_loss_limit_sol:
            self.killed = True
            return False, f"Daily loss limit reached ({self.portfolio.daily_pnl_sol:.4f} SOL)"

        # Reject if we already hold this mint
        existing = self.portfolio.positions.get(signal.token.mint)
        if existing and existing.status == "open":
            return False, f"Already holding {signal.token.symbol}"

        open_count = sum(1 for p in self.portfolio.positions.values() if p.status == "open")
        if open_count >= self.config.max_open_positions:
            return False, f"Max open positions ({self.config.max_open_positions}) reached"

        if size_sol > self.config.max_position_size_sol:
            return False, f"Size {size_sol:.4f} exceeds max {self.config.max_position_size_sol}"

        if size_sol > self.portfolio.cash_sol * 0.95:
            return False, "Insufficient cash (keeping 5% reserve)"

        if signal.confidence < self.config.min_confidence_score:
            return False, f"Confidence {signal.confidence:.2f} < min {self.config.min_confidence_score}"

        # Basic rug heuristics
        if signal.token.complete:
            return False, "Token already graduated (prefer early curve)"

        return True, "OK"

    def size_position(self, signal: Signal) -> float:
        """Conservative fixed fractional sizing."""
        base = min(self.config.max_position_size_sol, self.portfolio.cash_sol * 0.15)
        # Scale by confidence
        sized = base * max(0.3, signal.confidence)
        return round(min(sized, self.config.max_position_size_sol), 4)

    def kill(self, reason: str = "manual") -> None:
        self.killed = True
        logger.warning(f"[bold red]KILL SWITCH activated: {reason}[/]")

    def revive(self) -> None:
        self.killed = False
        logger.info("Kill switch cleared")
