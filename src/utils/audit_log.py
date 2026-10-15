"""Append-only audit log for privileged Telegram and call decisions."""

from __future__ import annotations
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = ROOT / "data" / "audit.jsonl"


def audit(event: str, **fields: Any) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        rec: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        with LOG_PATH.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        return
