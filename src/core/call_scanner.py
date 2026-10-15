"""Call scanner with daily cap and risk gates."""

from __future__ import annotations
import json
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Optional
from src.core.models import Signal
from src.utils.logger import setup_logger
from src.utils.audit_log import audit

logger = setup_logger()
STATE_PATH = Path(__file__).resolve().parents[2] / "data" / "call_budget.json"


class CallScanner:
    def __init__(self, max_per_day: int = 20, min_score: float = 0.55, min_confidence: float = 0.5,
                 min_curve_sol: float = 2.0, max_curve_sol: float = 40.0,
                 max_mcap_sol: float = 30.0, max_dev_buy_sol: float = 2.0):
        self.max_per_day = max_per_day
        self.min_score = min_score
        self.min_confidence = min_confidence
        self.min_curve_sol = min_curve_sol
        self.max_curve_sol = max_curve_sol
        self.max_mcap_sol = max_mcap_sol
        self.max_dev_buy_sol = max_dev_buy_sol
        self.day = ""
        self.used = 0
        self._creator_hits = {}
        self._load()

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load(self) -> None:
        if not STATE_PATH.exists():
            self.day = self._today()
            self.used = 0
            return
        try:
            data = json.loads(STATE_PATH.read_text())
            self.day = data.get("day", self._today())
            self.used = int(data.get("used", 0))
        except Exception:
            self.day = self._today()
            self.used = 0
        self._rollover()

    def _rollover(self) -> None:
        today = self._today()
        if self.day != today:
            self.day = today
            self.used = 0
            self._save()

    def _save(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"day": self.day, "used": self.used}))
        os.replace(tmp, STATE_PATH)

    @property
    def remaining(self) -> int:
        self._rollover()
        return max(0, self.max_per_day - self.used)

    def consider(self, signals: List[Signal], killed: bool) -> List[Tuple[Signal, str]]:
        """Return accepted (signal, reason) up to remaining daily budget."""
        self._rollover()
        accepted: List[Tuple[Signal, str]] = []
        if killed:
            audit("call_blocked", reason="killed")
            return accepted
        for sig in sorted(signals, key=lambda s: s.confidence, reverse=True):
            if self.used >= self.max_per_day:
                audit("call_rejected", symbol=sig.token.symbol, reason="daily_cap")
                break
            if sig.score < self.min_score:
                audit("call_rejected", symbol=sig.token.symbol, reason="score", score=sig.score)
                continue
            if sig.confidence < self.min_confidence:
                audit("call_rejected", symbol=sig.token.symbol, reason="confidence", confidence=sig.confidence)
                continue
            raw = sig.token.raw or {}
            if raw.get("mock"):
                audit("call_rejected", symbol=sig.token.symbol, reason="mock_feed")
                continue
            if raw.get("isMayhemMode"):
                audit("call_rejected", symbol=sig.token.symbol, reason="mayhem")
                continue
            if sig.token.complete:
                audit("call_rejected", symbol=sig.token.symbol, reason="graduated")
                continue
            vs = sig.token.virtual_sol_reserves or 0.0
            if vs and vs < self.min_curve_sol:
                audit("call_rejected", symbol=sig.token.symbol, reason="thin_curve", vs=vs)
                continue
            if vs and vs > self.max_curve_sol:
                audit("call_rejected", symbol=sig.token.symbol, reason="late_curve", vs=vs)
                continue
            mc = sig.token.market_cap_sol or 0.0
            if mc and mc > self.max_mcap_sol:
                audit("call_rejected", symbol=sig.token.symbol, reason="mcap", mc=mc)
                continue
            try:
                dev = float(raw.get("initialQuoteAmount") or raw.get("solAmount") or 0)
            except Exception:
                dev = 0.0
            if dev > self.max_dev_buy_sol:
                audit("call_rejected", symbol=sig.token.symbol, reason="dev_buy", dev=dev)
                continue
            creator = sig.token.creator or ""
            if creator:
                last = self._creator_hits.get(creator)
                if last and (datetime.now(timezone.utc) - last).total_seconds() < 3600:
                    audit("call_rejected", symbol=sig.token.symbol, reason="creator_cooldown")
                    continue
                self._creator_hits[creator] = datetime.now(timezone.utc)
            self.used += 1
            reason = f"score={sig.score} conf={sig.confidence}"
            accepted.append((sig, reason))
            audit("call_accepted", symbol=sig.token.symbol, mint=sig.token.mint, score=sig.score, confidence=sig.confidence, used=self.used)
        if accepted:
            self._save()
        return accepted

    def status(self) -> dict:
        self._rollover()
        return {"day": self.day, "used": self.used, "max": self.max_per_day, "remaining": self.remaining}
