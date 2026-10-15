"""Paper trading execution engine with optional live mark price."""

from __future__ import annotations
from typing import Optional, Callable, Awaitable
from src.core.models import Signal, Trade, Position, Side, OrderStatus, PortfolioState
from src.utils.logger import setup_logger

logger = setup_logger()


class PaperExecutor:
    def __init__(self, portfolio: PortfolioState, fee_bps: int = 50, vault: str = ""):
        self.portfolio = portfolio
        self.fee_bps = fee_bps
        self.vault = vault
        self.total_fees_sol = 0.0
        self.on_trade: Optional[Callable[[Trade], Awaitable[None]]] = None

    def _fee(self, amount: float) -> float:
        return round(amount * (self.fee_bps / 10_000), 8)

    def _curve_price(self, signal: Signal) -> float:
        token = signal.token
        if token.virtual_sol_reserves and token.virtual_token_reserves and token.virtual_token_reserves > 0:
            return token.virtual_sol_reserves / token.virtual_token_reserves
        return 0.00000003

    def execute_buy(self, signal: Signal, size_sol: float) -> Optional[Trade]:
        if size_sol <= 0 or size_sol > self.portfolio.cash_sol:
            logger.warning("Invalid size or insufficient cash")
            return None

        token = signal.token
        price = self._curve_price(signal)
        fee = self._fee(size_sol)
        net = size_sol - fee
        if net <= 0:
            logger.warning("Size too small after fee")
            return None
        tokens_bought = net / price if price > 0 else 0

        trade = Trade(
            mint=token.mint,
            symbol=token.symbol,
            side=Side.BUY,
            size_sol=net,
            tokens=tokens_bought,
            price_sol=price,
            status=OrderStatus.PAPER,
            paper=True,
            signal_id=signal.id,
            notes=f"Paper buy | score={signal.score} conf={signal.confidence} | fee={fee:.6f}",
        )

        self.portfolio.cash_sol -= size_sol  # net + fee
        self.total_fees_sol += fee
        pos = Position(
            mint=token.mint,
            symbol=token.symbol,
            entry_price_sol=price,
            size_sol=net,
            tokens_held=tokens_bought,
            notes=str(signal.reasons),
        )
        self.portfolio.positions[token.mint] = pos
        self.portfolio.trades.append(trade)

        logger.info(
            f"[green]PAPER BUY[/] {token.symbol} | {net:.4f} SOL (+fee {fee:.4f}) → {tokens_bought:.0f} tokens @ {price:.10f}"
        )
        return trade

    def execute_sell(
        self,
        mint: str,
        fraction: float = 1.0,
        mark_price: Optional[float] = None,
        reason: str = "exit",
    ) -> Optional[Trade]:
        """Sell paper position. Prefer mark_price from Jupiter/curve; else last entry."""
        pos = self.portfolio.positions.get(mint)
        if not pos or pos.status != "open":
            return None

        sell_tokens = pos.tokens_held * fraction
        if mark_price is not None and mark_price > 0:
            current_price = mark_price
            price_src = "mark"
        else:
            # Fallback: no free +5% fantasy — flat to entry if no mark
            current_price = pos.entry_price_sol
            price_src = "entry_fallback"

        gross = sell_tokens * current_price
        fee = self._fee(gross)
        proceeds = gross - fee
        cost = pos.size_sol * fraction
        pnl = proceeds - cost
        self.total_fees_sol += fee
        pct = (current_price / pos.entry_price_sol - 1.0) if pos.entry_price_sol > 0 else 0.0

        trade = Trade(
            mint=mint,
            symbol=pos.symbol,
            side=Side.SELL,
            size_sol=proceeds,
            tokens=sell_tokens,
            price_sol=current_price,
            status=OrderStatus.PAPER,
            paper=True,
            pnl_sol=pnl,
            notes=f"Paper sell {fraction*100:.0f}% | {reason} | {price_src} | {pct*100:+.2f}% | fee={fee:.6f}",
        )

        self.portfolio.cash_sol += proceeds
        self.portfolio.daily_pnl_sol += pnl
        self.portfolio.total_pnl_sol += pnl

        if fraction >= 0.999:
            pos.status = "closed"
            del self.portfolio.positions[mint]
        else:
            pos.tokens_held -= sell_tokens
            pos.size_sol *= (1 - fraction)

        self.portfolio.trades.append(trade)
        logger.info(
            f"[yellow]PAPER SELL[/] {pos.symbol} | PnL {pnl:+.4f} SOL ({pct*100:+.1f}%) | fee {fee:.4f} | {reason}"
        )
        return trade
