"""Oxaniq main agent loop — paper-first with MTM exits, TG ops, feed status."""

from __future__ import annotations
from urllib.parse import quote
import asyncio
from datetime import datetime, timezone
from typing import Optional
from rich.table import Table
from src.utils.config import get_config, OxaniqConfig
from src.utils.logger import setup_logger, console
from src.utils.solana_helpers import HeliusHelper
from src.utils.portfolio_store import load_portfolio, save_portfolio
from src.core.models import PortfolioState, Signal
from src.risk.manager import RiskManager
from src.brain.scorer import Scorer
from src.core.call_scanner import CallScanner
from src.utils.audit_log import audit
from src.ingestion.pumpfun import PumpFunIngester
from src.execution.paper import PaperExecutor
from src.execution.live import LiveExecutor
from src.execution.jupiter import JupiterClient
from src.x402.client import X402Client
from src.telegram.bot import TelegramBot
from src.wallet.solana_wallet import SolanaWallet
from src.wallet.user_store import UserWalletStore
from src.payments.access import AccessRail
from src.wallet.transfers import send_sol, send_usdc

logger = setup_logger()
SOL_MINT = "So11111111111111111111111111111111111111112"


class OxaniqAgent:
    def __init__(self, config: Optional[OxaniqConfig] = None, start_cash: Optional[float] = None):
        self.config = config or get_config()
        cash = start_cash if start_cash is not None else 1.0
        self.portfolio = load_portfolio(default_cash=cash)
        if start_cash is not None and not self.portfolio.positions:
            self.portfolio.cash_sol = start_cash
        self.risk = RiskManager(self.config.risk, self.portfolio)
        self.scorer = Scorer()
        self.scanner = CallScanner(
            max_per_day=getattr(self.config, "max_calls_per_day", 20),
            min_score=getattr(self.config, "min_call_score", 0.55),
            min_confidence=getattr(self.config, "min_call_confidence", 0.50),
            min_curve_sol=getattr(self.config, "min_curve_sol", 2.0),
            max_curve_sol=getattr(self.config, "max_curve_sol", 40.0),
            max_mcap_sol=getattr(self.config, "max_call_mcap_sol", 30.0),
            max_dev_buy_sol=getattr(self.config, "max_dev_buy_sol", 2.0),
        )
        self.call_mode = getattr(self.config, "call_mode", "trade")
        self.live = None  # set after helius
        self.ingester = PumpFunIngester(self.config)
        self.executor = PaperExecutor(
            self.portfolio,
            fee_bps=getattr(self.config, "platform_fee_bps", 50),
            vault=getattr(self.config, "platform_vault", ""),
        )
        self.x402 = X402Client(self.config)
        self.helius = HeliusHelper(self.config)
        self.live = LiveExecutor(self.config, self.helius)
        self.jupiter = JupiterClient(self.config)
        self.tg = TelegramBot(self.config)
        self.wallet = SolanaWallet(self.config)
        self.user_wallets = UserWalletStore()
        self.access = AccessRail(
            vault=getattr(self.config, "platform_vault", ""),
            price_usd=getattr(self.config, "access_price_usd", 11.99),
        )
        self.running = False
        self.cycle = 0
        self.max_cycles = 0

    def _is_admin(self, user_id: Optional[str]) -> bool:
        admins = self.config.telegram_admin_ids or []
        if not admins:
            return True
        if not user_id:
            return False
        return str(user_id) in admins


    def _share_keyboard(self, symbol: str, mint: str, score_line: str) -> dict:
        pump = f"https://pump.fun/coin/{mint}"
        dex = f"https://dexscreener.com/solana/{mint}"
        blurb = f"OxanIQ CALL ${symbol}\n{score_line}\n{pump}"
        x_url = "https://twitter.com/intent/tweet?text=" + quote(blurb)
        tg_url = "https://t.me/share/url?url=" + quote(pump) + "&text=" + quote(f"OxanIQ CALL ${symbol}")
        return {
            "inline_keyboard": [
                [
                    {"text": "Share X", "url": x_url},
                    {"text": "Share TG", "url": tg_url},
                ],
                [
                    {"text": "Pump.fun", "url": pump},
                    {"text": "DexScreener", "url": dex},
                ],
            ]
        }

    async def _alert(self, text: str, reply_markup=None):
        if self.tg.enabled:
            try:
                await self.tg.send(text, reply_markup=reply_markup)
            except Exception:
                pass

    async def start(self):
        logger.info("[bold green]Oxaniq Agent starting...[/]")
        logger.info(f"Mode: {'PAPER' if self.config.paper_trading else 'LIVE'}")
        r = self.config.risk
        logger.info(
            f"Risk: max pos {r.max_position_size_sol} SOL | max open {r.max_open_positions} | "
            f"daily loss {r.daily_loss_limit_sol} | TP {r.take_profit_pct*100:.0f}% | "
            f"SL {r.stop_loss_pct*100:.0f}% | hold {r.max_hold_seconds}s"
        )
        health = await self.helius.health_check()
        if not health.get("ok"):
            logger.warning("Helius RPC unhealthy — continuing with degraded connectivity")
        self.tg.set_command_handler(self._on_tg_command)
        if self.tg.enabled:
            await self.tg.start_polling()
            await self.tg.send(
                "OxanIQ up (PAPER).\n"
                f"TP {r.take_profit_pct*100:.0f}% / SL {r.stop_loss_pct*100:.0f}% / hold {r.max_hold_seconds}s\n"
                "/help for commands"
            )
            logger.info("Telegram control enabled")
        await self.ingester.start()
        self.running = True
        await self._main_loop()

    async def stop(self):
        self.running = False
        try:
            save_portfolio(self.portfolio)
        except Exception as e:
            logger.warning(f"Could not save portfolio: {e}")
        try:
            await self.tg.send("OxanIQ stopped.")
        except Exception:
            pass
        await self.tg.close()
        await self.ingester.stop()
        await self.helius.close()
        await self.jupiter.close()
        logger.info("Oxaniq stopped")

    async def _main_loop(self):
        while self.running:
            self.cycle += 1
            try:
                await self._tick()
            except Exception as e:
                logger.exception(f"Tick error: {e}")
            if self.max_cycles and self.cycle >= self.max_cycles:
                logger.info(f"Reached max cycles ({self.max_cycles}) — stopping")
                self.running = False
                break
            await asyncio.sleep(12)

    async def _mark_price(self, mint: str, tokens_held: float, entry: float) -> Optional[float]:
        if not mint or mint.startswith("Mock"):
            return None
        try:
            amt = max(1, int(tokens_held))
            if amt > 10**15:
                amt = 10**12
            q = await self.jupiter.quote_token_to_sol(mint, amt, slippage_bps=500)
            if not q:
                return None
            out = int(q.get("outAmount") or 0)
            if out <= 0 or tokens_held <= 0:
                return None
            return (out / 1_000_000_000) / tokens_held
        except Exception as e:
            logger.debug(f"mark_price fail: {e}")
            return None

    async def _tick(self):
        tokens = await self.ingester.fetch_new_tokens(8)
        candidates: list[Signal] = []
        for tok in tokens:
            extra = {}
            if isinstance(tok.raw, dict):
                for k in ("top10_holder_pct", "holder_count", "buy_count", "tx_count"):
                    if k in tok.raw:
                        extra[k] = tok.raw[k]
            intensity = min(0.9, (tok.virtual_sol_reserves or 1) / 20.0)
            sig = self.scorer.score(tok, fomo_score=intensity, extra=extra)
            candidates.append(sig)

        accepted = self.scanner.consider(candidates, killed=self.risk.killed)
        for sig, call_reason in accepted:
            left = self.scanner.remaining
            call_text = (
                f"CALL {sig.token.symbol}\n"
                f"{call_reason}\n"
                f"mint={sig.token.mint}\n"
                f"mode={self.call_mode}\n"
                f"calls left today: {left}"
            )
            kb = self._share_keyboard(sig.token.symbol, sig.token.mint, call_reason)
            await self._alert(call_text, reply_markup=kb)
            if self.call_mode != "trade":
                continue
            # access gate is per-user at command time; paper loop stays operator-side
            size = self.risk.size_position(sig)
            ok, reason = self.risk.can_open_position(sig, size)
            if not ok:
                audit("call_risk_skip", symbol=sig.token.symbol, reason=reason)
                logger.debug(f"Skip {sig.token.symbol}: {reason}")
                continue
            if self.config.paper_trading or not getattr(self.config, "live_enabled", False):
                trade = self.executor.execute_buy(sig, size)
                if trade:
                    await self._alert(
                        f"PAPER BUY {trade.symbol}\n"
                        f"{trade.size_sol:.4f} SOL (after 0.5% fee) @ {trade.price_sol:.10g}"
                    )
            else:
                result = await self.live.buy(sig.token.mint, size)
                if result.get("ok"):
                    await self._alert(f"LIVE BUY {sig.token.symbol}\nsig={result.get('signature')}")
                else:
                    await self._alert(f"LIVE BUY failed {sig.token.symbol}\n{result.get('error')}")

        if self.config.paper_trading and self.portfolio.positions:
            now = datetime.now(timezone.utc)
            r = self.config.risk
            for mint, pos in list(self.portfolio.positions.items()):
                if pos.status != "open":
                    continue
                age = (now - pos.entry_time).total_seconds()
                mark = await self._mark_price(mint, pos.tokens_held, pos.entry_price_sol)
                if mark is None:
                    if age >= r.max_hold_seconds:
                        trade = self.executor.execute_sell(
                            mint, 1.0, mark_price=pos.entry_price_sol, reason="max_hold"
                        )
                        if trade:
                            await self._alert(f"SELL {pos.symbol} max_hold\nPnL {trade.pnl_sol:+.4f} SOL")
                    continue
                pct = (mark / pos.entry_price_sol - 1.0) if pos.entry_price_sol > 0 else 0.0
                reason = None
                if pct >= r.take_profit_pct:
                    reason = f"tp_{pct*100:.1f}%"
                elif pct <= -r.stop_loss_pct:
                    reason = f"sl_{pct*100:.1f}%"
                elif age >= r.max_hold_seconds:
                    reason = "max_hold"
                if reason:
                    trade = self.executor.execute_sell(mint, 1.0, mark_price=mark, reason=reason)
                    if trade:
                        await self._alert(
                            f"SELL {pos.symbol} {reason}\nPnL {trade.pnl_sol:+.4f} SOL ({pct*100:+.1f}%)"
                        )

        if self.cycle % 5 == 0 and self.x402.enabled:
            await self.x402.pay_for_resource("https://example-data.api/premium", 0.005)

        if self.cycle % 3 == 0:
            try:
                save_portfolio(self.portfolio)
            except Exception as e:
                logger.debug(f"save skip: {e}")
        self._print_status()

        if self.tg.enabled and self.cycle % 10 == 0:
            open_pos = sum(1 for p in self.portfolio.positions.values() if p.status == "open")
            await self.tg.send(
                f"OxanIQ c{self.cycle} | cash {self.portfolio.cash_sol:.4f} | "
                f"open {open_pos} | pnl {self.portfolio.daily_pnl_sol:+.4f} | "
                f"feed={getattr(self.ingester, 'last_source', '?')}\n"
                f"fees={self.executor.total_fees_sol:.4f} SOL"
            )

    async def _on_tg_command(self, cmd: str, chat_id: str, raw: str = "", user_id: str = ""):
        parts = (raw or cmd).strip().split()
        cmd0 = (parts[0] if parts else cmd).lower().split("@")[0]
        args = parts[1:] if len(parts) > 1 else []

        async def reply(msg: str):
            await self.tg.send(msg, chat_id=chat_id)

        admin = self._is_admin(user_id) if user_id else self._is_admin(None)
        if getattr(self.config, "access_required", True) and not admin and user_id and not self.access.is_paid(user_id):
            if cmd0 in ("/withdraw", "/quote", "/calls"):
                await reply("Locked. Send /pay to unlock ($11.99 in SOL).")
                return

        if cmd0 in ("/help", "/start"):
            await reply(
                "OxanIQ commands\n────────────\n"
                "/status /pos /trades /risk /feed /calls\n"
                "/health /slot /jup /x402\n/quote <mint>\n"
                "/wallet /deposit /balance /usdc /pay /verify\n"
                "/withdraw sol|usdc <amt> <addr> confirm\n"
                "/live /kill /revive (admin if allowlist)\n"
                "/set risk <key> <val> (admin)\n"
                "/sellall confirm /shutdown confirm (admin)\n/help"
            )
            return
        if cmd0 == "/status":
            open_n = sum(1 for p in self.portfolio.positions.values() if p.status == "open")
            await reply(
                f"OxanIQ status\nmode={'PAPER' if self.config.paper_trading else 'LIVE'}\n"
                f"cycle={self.cycle}\ncash={self.portfolio.cash_sol:.4f} SOL\nopen={open_n}\n"
                f"daily_pnl={self.portfolio.daily_pnl_sol:+.4f}\ntotal_pnl={self.portfolio.total_pnl_sol:+.4f}\n"
                f"trades={len(self.portfolio.trades)}\nkilled={self.risk.killed}\n"
                f"feed={getattr(self.ingester, 'last_source', '?')}\n"                f"fees={self.executor.total_fees_sol:.4f} SOL"
            )
            return
        if cmd0 == "/feed":
            await reply(
                f"Launch feed\nsource={getattr(self.ingester, 'last_source', '?')}\n"
                f"last_count={getattr(self.ingester, 'last_count', 0)}\napi={self.config.pumpfun_api}"
            )
            return
        if cmd0 == "/calls":
            if args and args[0].lower() == "mode" and len(args) > 1:
                if not admin:
                    await reply("Admin only.")
                    return
                mode = args[1].lower()
                if mode not in ("alert", "trade"):
                    await reply("Use: /calls mode alert|trade")
                    return
                self.call_mode = mode
                await reply(f"Call mode = {mode}")
                return
            st = self.scanner.status()
            await reply(
                f"Call budget\n"
                f"day={st['day']}\n"
                f"used={st['used']}/{st['max']}\n"
                f"remaining={st['remaining']}\n"
                f"mode={self.call_mode}\n"
                f"min_score={self.scanner.min_score}\n"
                f"min_conf={self.scanner.min_confidence}\n"
                f"curve={self.scanner.min_curve_sol}-{self.scanner.max_curve_sol} SOL"
            )
            return
        if cmd0 in ("/pos", "/positions"):
            open_pos = [p for p in self.portfolio.positions.values() if p.status == "open"]
            if not open_pos:
                await reply("No open positions.")
                return
            lines = ["Open positions"] + [
                f"• {p.symbol} | {p.size_sol:.4f} SOL | {p.tokens_held:.0f} tok" for p in open_pos
            ]
            await reply("\n".join(lines))
            return
        if cmd0 == "/trades":
            trades = self.portfolio.trades[-8:]
            if not trades:
                await reply("No trades yet.")
                return
            lines = ["Last trades"]
            for t in reversed(trades):
                side = t.side.value if hasattr(t.side, "value") else str(t.side)
                pnl = f" pnl={t.pnl_sol:+.4f}" if t.pnl_sol is not None else ""
                lines.append(f"• {side.upper()} {t.symbol} {t.size_sol:.4f} SOL{pnl}")
            await reply("\n".join(lines))
            return
        if cmd0 == "/risk":
            r = self.config.risk
            await reply(
                f"Risk limits\nmax_pos={r.max_position_size_sol} SOL\nmax_open={r.max_open_positions}\n"
                f"daily_loss_limit={r.daily_loss_limit_sol} SOL\nmin_confidence={r.min_confidence_score}\n"
                f"tp={r.take_profit_pct*100:.1f}%\nsl={r.stop_loss_pct*100:.1f}%\n"
                f"max_hold={r.max_hold_seconds}s\nkilled={self.risk.killed}"
            )
            return
        if cmd0 == "/set":
            if not admin:
                await reply("Admin only.")
                return
            if len(args) < 3 or args[0].lower() != "risk":
                await reply(
                    "Usage: /set risk <key> <val>\nkeys: max_position_size_sol max_open_positions "
                    "daily_loss_limit_sol min_confidence_score take_profit_pct stop_loss_pct max_hold_seconds"
                )
                return
            key, val = args[1], args[2]
            allowed = {
                "max_position_size_sol": float, "max_open_positions": int,
                "daily_loss_limit_sol": float, "min_confidence_score": float,
                "take_profit_pct": float, "stop_loss_pct": float, "max_hold_seconds": int,
            }
            if key not in allowed:
                await reply(f"Unknown key {key}")
                return
            try:
                cast = allowed[key](val)
                setattr(self.config.risk, key, cast)
                setattr(self.risk.config, key, cast)
                await reply(f"Set risk.{key} = {cast}")
            except Exception as e:
                await reply(f"Bad value: {e}")
            return
        if cmd0 == "/x402":
            st = self.x402.status()
            await reply(
                f"x402\n"
                f"enabled={st['enabled']}\n"
                f"spent={st['daily_spent_usdc']:.4f}/{st['limit_usdc']} USDC\n"
                f"day={st['day']}\n"
                f"probes={st['probes']} paid={st['paid']}\n"
                f"last={st['last']}\n"
                f"facilitator={st['facilitator']}"
            )
            return
        if cmd0 == "/health":
            try:
                h = await self.helius.health_check()
                if h.get("ok"):
                    await reply(f"Helius OK\nslot={h.get('slot')}\nlatency={h.get('latency_ms')}ms\nver={h.get('version')}")
                else:
                    await reply(f"Helius down: {h.get('error', 'unknown')}")
            except Exception as e:
                await reply(f"Health check failed: {e}")
            return
        if cmd0 == "/slot":
            try:
                h = await self.helius.health_check()
                await reply(f"Solana slot\nslot={h.get('slot')}\nlatency={h.get('latency_ms')}ms" if h.get("ok") else f"Slot unavailable")
            except Exception as e:
                await reply(f"Slot check failed: {e}")
            return
        if cmd0 in ("/jup", "/jupiter"):
            try:
                USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
                q = await self.jupiter.get_quote(input_mint=SOL_MINT, output_mint=USDC, amount=10_000_000, slippage_bps=50)
                if not q:
                    await reply("Jupiter: no quote")
                    return
                usdc = int(q.get("outAmount") or 0) / 1_000_000
                await reply(f"Jupiter OK\n0.01 SOL → {usdc:.4f} USDC\nhops={len(q.get('routePlan') or [])}")
            except Exception as e:
                await reply(f"Jupiter failed: {e}")
            return
        if cmd0 == "/quote":
            if not args:
                await reply("Usage: /quote <mint>")
                return
            try:
                q = await self.jupiter.quote_sol_to_token(args[0], 0.01, slippage_bps=300)
                if not q:
                    await reply("No quote for that mint")
                    return
                await reply(f"Quote 0.01 SOL → mint\noutAmount={q.get('outAmount')}\nimpact={q.get('priceImpactPct')}%\nmint={args[0][:12]}…")
            except Exception as e:
                await reply(f"Quote failed: {e}")
            return
        if cmd0 == "/pay":
            if not user_id:
                await reply("Could not read your user id.")
                return
            if self.access.is_paid(user_id) or admin:
                await reply("Access already active.")
                return
            amt = await self.access.sol_amount()
            if not amt:
                await reply("Price feed down. Try /pay again.")
                return
            vault = self.access.vault
            await reply(
                f"OxanIQ access ${self.access.price_usd}\n"
                f"Send {amt} SOL to:\n{vault}\n\n"
                f"Then: /verify <tx_signature>\n"
                f"One-time unlock."
            )
            return
        if cmd0 == "/verify":
            if not user_id:
                await reply("Could not read your user id.")
                return
            if self.access.is_paid(user_id):
                await reply("Already unlocked.")
                return
            if not args:
                await reply("Usage: /verify <tx_signature>")
                return
            sig = args[0]
            amt = await self.access.sol_amount()
            if not amt:
                await reply("Price feed down.")
                return
            res = await self.access.verify_tx(self.helius, sig, amt)
            if not res.get("ok"):
                await reply(f"Not verified: {res.get('error')}")
                return
            self.access.grant(user_id, sig, res.get("sol") or amt)
            await reply(f"Unlocked.\nPaid {res.get('sol'):.4f} SOL\nWelcome to OxanIQ.")
            return
        if cmd0 in ("/wallet", "/deposit"):
            if not user_id:
                await reply("Could not read your Telegram user id. Try again.")
                return
            rec = self.user_wallets.get_or_create(user_id)
            await reply(
                f"Your OxanIQ wallet\n"
                f"{rec['pubkey']}\n\n"
                f"Deposit SOL or USDC (mainnet) to this address.\n"
                f"USDC mint: EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v\n"
                f"This wallet is yours only."
            )
            return
        if cmd0 in ("/balance", "/usdc"):
            if not user_id:
                await reply("Could not read your user id.")
                return
            rec = self.user_wallets.get(user_id)
            if not rec:
                await reply("No wallet yet. Send /wallet to create one.")
                return
            # temporary wallet view for this user
            self.wallet.pubkey = rec["pubkey"]
            bals = await self.wallet.get_balances(self.helius)
            sol = bals.get("sol")
            usdc = bals.get("usdc")
            sol_s = f"{sol:.6f} SOL" if sol is not None else "SOL n/a"
            usdc_s = f"{usdc:.4f} USDC" if usdc is not None else "USDC n/a"
            await reply(
                f"Your balances\n"
                f"{sol_s}\n"
                f"{usdc_s}\n"
                f"{rec['pubkey']}"
            )
            return
        if cmd0 == "/withdraw":
            if not user_id:
                await reply("Could not read your user id.")
                return
            if len(args) < 4 or args[-1].lower() != "confirm":
                await reply(
                    "Withdraw (confirm required)\n"
                    "/withdraw sol <amount> <dest> confirm\n"
                    "/withdraw usdc <amount> <dest> confirm\n"
                    "Keep ~0.01 SOL for fees."
                )
                return
            asset, amt_s, dest = args[0].lower(), args[1], args[2]
            try:
                amt = float(amt_s)
            except Exception:
                await reply("Bad amount.")
                return
            if amt <= 0:
                await reply("Amount must be > 0")
                return
            secret = self.user_wallets.decrypt_secret(user_id)
            rec = self.user_wallets.get(user_id)
            if not secret or not rec:
                await reply("No wallet for you. /wallet first.")
                return
            try:
                fee_bps = getattr(self.config, "platform_fee_bps", 50)
                vault = getattr(self.config, "platform_vault", "")
                fee = round(amt * fee_bps / 10_000, 8)
                net = round(amt - fee, 8)
                if asset == "sol":
                    if net < 0.001:
                        await reply("Min SOL withdraw 0.001 after fee")
                        return
                    sig = await send_sol(self.helius, secret, dest, net)
                    fee_sig = ""
                    if fee > 0 and vault:
                        try:
                            fee_sig = await send_sol(self.helius, secret, vault, fee)
                        except Exception as fe:
                            fee_sig = f"fee_fail:{fe}"
                    await reply(
                        f"SOL sent\nnet {net} → {dest[:8]}…\n"
                        f"fee {fee} (0.5%) → vault\nsig={sig}\nfee_sig={fee_sig}"
                    )
                elif asset == "usdc":
                    if net < 0.1:
                        await reply("Min USDC withdraw 0.1 after fee")
                        return
                    sig = await send_usdc(self.helius, secret, dest, net)
                    fee_sig = ""
                    if fee > 0 and vault:
                        try:
                            fee_sig = await send_usdc(self.helius, secret, vault, fee)
                        except Exception as fe:
                            fee_sig = f"fee_fail:{fe}"
                    await reply(
                        f"USDC sent\nnet {net} → {dest[:8]}…\n"
                        f"fee {fee} (0.5%) → vault\nsig={sig}\nfee_sig={fee_sig}"
                    )
                else:
                    await reply("Asset must be sol or usdc")
            except Exception as e:
                await reply(f"Withdraw failed: {e}")
            return
        if cmd0 in ("/live", "/live_cb"):
            if cmd0 == "/live_cb":
                action = (raw or "").split(":")[-1]
            else:
                action = args[0].lower() if args else ""
            live_on = bool(getattr(self.config, "live_enabled", False))
            if action in ("on", "enable"):
                if not admin:
                    await reply("Admin only.")
                    return
                self.config.live_enabled = True
                self.config.paper_trading = False
                if self.live:
                    self.live.enabled = bool(self.config.solana_private_key)
                await reply("LIVE ENABLED.\nPaper off. Real swaps will use the operator wallet.")
                return
            if action in ("off", "disable"):
                if not admin:
                    await reply("Admin only.")
                    return
                self.config.live_enabled = False
                self.config.paper_trading = True
                if self.live:
                    self.live.enabled = False
                await reply("LIVE OFF.\nBack to paper.")
                return
            kb = {
                "inline_keyboard": [[
                    {"text": "Enable LIVE", "callback_data": "live:on"},
                    {"text": "Paper", "callback_data": "live:off"},
                ]]
            }
            await self.tg.send(
                f"Execution mode\nlive={'ON' if live_on else 'OFF'}\n"
                f"paper={'ON' if self.config.paper_trading else 'OFF'}\n"
                f"Admin: tap a button.",
                chat_id=chat_id,
                reply_markup=kb,
            )
            return
        if cmd0 == "/kill":
            if not admin:
                await reply("Admin only.")
                return
            self.risk.kill("telegram")
            await reply("Kill switch ON.")
            return
        if cmd0 == "/revive":
            if not admin:
                await reply("Admin only.")
                return
            self.risk.revive()
            await reply("Kill switch OFF.")
            return
        if cmd0 == "/sellall":
            if not admin:
                await reply("Admin only.")
                return
            if not args or args[0].lower() != "confirm":
                await reply("Close ALL paper positions?\n/sellall confirm")
                return
            closed = 0
            for mint in list(self.portfolio.positions.keys()):
                pos = self.portfolio.positions.get(mint)
                if pos and pos.status == "open":
                    self.executor.execute_sell(mint, 1.0, mark_price=pos.entry_price_sol, reason="sellall")
                    closed += 1
            await reply(f"Closed {closed} paper position(s).")
            return
        if cmd0 == "/shutdown":
            if not admin:
                await reply("Admin only.")
                return
            if not args or args[0].lower() != "confirm":
                await reply("To stop:\n/shutdown confirm")
                return
            await reply("Shutting down OxanIQ…")
            self.running = False
            return
        await reply("Unknown cmd. Try /help")

    def _print_status(self):
        table = Table(title=f"Oxaniq Status — Cycle {self.cycle}", show_header=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        open_pos = sum(1 for p in self.portfolio.positions.values() if p.status == "open")
        table.add_row("Mode", "PAPER" if self.config.paper_trading else "LIVE")
        table.add_row("Feed", getattr(self.ingester, "last_source", "?"))
        table.add_row("Cash (SOL)", f"{self.portfolio.cash_sol:.4f}")
        table.add_row("Open Positions", str(open_pos))
        table.add_row("Daily PnL", f"{self.portfolio.daily_pnl_sol:+.4f}")
        table.add_row("Total PnL", f"{self.portfolio.total_pnl_sol:+.4f}")
        table.add_row("Trades", str(len(self.portfolio.trades)))
        table.add_row("Killed", str(self.risk.killed))
        table.add_row("Fees (SOL)", f"{self.executor.total_fees_sol:.4f}")
        console.print(table)


async def run_agent(config: Optional[OxaniqConfig] = None, max_cycles: int = 0, start_cash: Optional[float] = None):
    agent = OxaniqAgent(config=config, start_cash=start_cash)
    agent.max_cycles = max_cycles or 0
    try:
        await agent.start()
    except KeyboardInterrupt:
        pass
    finally:
        await agent.stop()
