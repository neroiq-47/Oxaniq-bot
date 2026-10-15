"""Lightweight hybrid scorer — rules + liquidity / sniper-style heuristics.
No heavy LLM. FOMO social feed intentionally not required.
"""

from __future__ import annotations
from typing import List, Optional
from src.core.models import TokenInfo, Signal
from src.utils.logger import setup_logger

logger = setup_logger()


class Scorer:
    """Score a new Pump.fun-style token for potential entry."""

    def score(
        self,
        token: TokenInfo,
        fomo_score: float = 0.0,
        extra: dict | None = None,
    ) -> Signal:
        reasons: List[str] = []
        score = 0.0
        confidence = 0.55
        extra = extra or {}

        # 1. Freshness
        if token.created_timestamp:
            score += 0.12
            reasons.append("Fresh launch")


        # PumpDev create is always fresh
        raw = token.raw or {}
        if raw.get("txType") == "create" or raw.get("source") == "pumpdev":
            score += 0.20
            reasons.append("PumpDev create")
            if not token.created_timestamp:
                confidence += 0.05

        # Dev buy size
        dev = raw.get("initialQuoteAmount") or raw.get("solAmount") or raw.get("quoteAmount")
        try:
            dev_f = float(dev) if dev is not None else 0.0
        except Exception:
            dev_f = 0.0
        if 0.1 <= dev_f <= 1.5:
            score += 0.10
            reasons.append(f"Dev buy {dev_f:.2f} SOL")
        elif dev_f > 2.0:
            score -= 0.20
            reasons.append("Heavy dev buy")
            confidence -= 0.1

        # Mayhem / cashback flags
        if raw.get("isMayhemMode"):
            score -= 0.35
            reasons.append("Mayhem mode")
            confidence -= 0.15
        if raw.get("isCashbackEnabled"):
            score += 0.04
            reasons.append("Cashback enabled")

        # Market cap band
        mc = token.market_cap_sol or raw.get("marketCapSol")
        try:
            mc_f = float(mc) if mc is not None else 0.0
        except Exception:
            mc_f = 0.0
        if 0 < mc_f <= 30:
            score += 0.08
            reasons.append(f"MC {mc_f:.1f} SOL")
        elif mc_f > 50:
            score -= 0.10
            reasons.append("High MC")

        # 2. Ticker / name quality
        name = (token.name or "").lower()
        symbol = (token.symbol or "").lower()
        if 2 <= len(symbol) <= 10 and symbol.isalnum():
            score += 0.08
            reasons.append("Clean ticker")
        if any(bad in name for bad in ["test", "scam", "rug", "honeypot", "airdrop"]):
            score -= 0.45
            reasons.append("Suspicious name")
            confidence -= 0.2

        # 3. Early curve liquidity (virtual SOL reserves)
        vs = token.virtual_sol_reserves or 0.0
        vt = token.virtual_token_reserves or 0.0
        if vs > 0:
            if 1.0 < vs < 25:
                score += 0.18
                reasons.append("Healthy early reserves")
            elif 25 <= vs < 50:
                score += 0.08
                reasons.append("Mid-curve liquidity")
            elif vs >= 80:
                score -= 0.12
                reasons.append("Late on curve")

        # 4. Liquidity ratio heuristic (sol vs tokens on curve)
        if vs > 0 and vt > 0:
            # Very skewed curve → potential sniper dump zone
            ratio = vs / (vt / 1_000_000_000) if vt else 0
            if ratio < 0.5:
                score -= 0.08
                reasons.append("Thin SOL side")
            elif 1.0 <= ratio <= 40:
                score += 0.08
                reasons.append("Balanced curve depth")

        # 5. Graduation flag
        if token.complete:
            score -= 0.25
            reasons.append("Already graduated")
            confidence -= 0.1

        # 6. Sniper / concentration proxy from extra metadata when present
        # (holders, top10%, buy count — if feed provides them)
        top10 = extra.get("top10_holder_pct")
        holders = extra.get("holder_count")
        buys = extra.get("buy_count") or extra.get("tx_count")
        if isinstance(top10, (int, float)):
            if top10 > 60:
                score -= 0.2
                reasons.append(f"Concentrated top10 {top10:.0f}%")
                confidence -= 0.1
            elif top10 < 35:
                score += 0.08
                reasons.append("Spread holders")
        if isinstance(holders, int):
            if holders < 5:
                score -= 0.1
                reasons.append("Very few holders")
            elif holders >= 30:
                score += 0.07
                reasons.append(f"Holders {holders}")
        if isinstance(buys, int) and buys >= 20:
            score += 0.05
            reasons.append(f"Active buys {buys}")

        # 7. Optional intensity (caller may pass non-social proxy e.g. reserve velocity)
        intensity = max(0.0, min(1.0, fomo_score))
        score += intensity * 0.2
        if intensity > 0.6:
            reasons.append(f"High intensity ({intensity:.2f})")
            confidence += 0.1

        # 8. Creator present
        if token.creator:
            score += 0.04
            reasons.append("Creator tagged")

        # 9. Mock tokens slightly down-ranked so real feed wins when available
        if (token.raw or {}).get("mock"):
            score -= 0.05
            reasons.append("Mock feed")

        score = max(0.0, min(1.0, score))
        confidence = max(0.1, min(0.95, confidence + (score - 0.5) * 0.25))

        signal = Signal(
            token=token,
            source="scorer",
            score=round(score, 3),
            confidence=round(confidence, 3),
            reasons=reasons,
            fomo_intensity=intensity,
            metadata=extra,
        )
        logger.debug(
            f"Scored {token.symbol}: score={score:.2f} conf={confidence:.2f} | {reasons}"
        )
        return signal
