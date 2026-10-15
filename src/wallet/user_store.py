"""Per-user Solana wallets for Oxaniq Telegram community.

One keypair per Telegram user_id. Secrets encrypted at rest with WALLET_MASTER_KEY.
"""

from __future__ import annotations
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from cryptography.fernet import Fernet
from src.wallet.solana_wallet import generate_keypair
from src.utils.logger import setup_logger

logger = setup_logger()

ROOT = Path(__file__).resolve().parents[2]
STORE_PATH = ROOT / "data" / "wallets.json"
ENV_PATH = ROOT / ".env"


def _ensure_master_key() -> str:
    key = (os.getenv("WALLET_MASTER_KEY") or "").strip()
    if key:
        return key
    # Generate and persist once
    key = Fernet.generate_key().decode()
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    out = [ln for ln in lines if not ln.startswith("WALLET_MASTER_KEY=")]
    out.append(f"WALLET_MASTER_KEY={key}")
    ENV_PATH.write_text("\n".join(out) + "\n")
    os.environ["WALLET_MASTER_KEY"] = key
    logger.info("Generated WALLET_MASTER_KEY and saved to .env (do not commit)")
    return key


class UserWalletStore:
    def __init__(self, path: Path = STORE_PATH):
        self.path = path
        self._fernet = Fernet(_ensure_master_key().encode() if not _ensure_master_key().startswith("gAAAA") else _ensure_master_key().encode())
        # Fernet key must be urlsafe base64 32-byte. Fernet.generate_key() already returns that.
        raw = _ensure_master_key()
        self._fernet = Fernet(raw.encode())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except Exception as e:
            logger.warning(f"wallet store load failed: {e}")
            return {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        os.replace(tmp, self.path)

    def get(self, user_id: str) -> Optional[Dict[str, Any]]:
        rec = self._data.get(str(user_id))
        if not rec:
            return None
        return {"pubkey": rec["pubkey"], "created_at": rec.get("created_at"), "user_id": str(user_id)}

    def get_or_create(self, user_id: str) -> Dict[str, Any]:
        uid = str(user_id)
        if uid in self._data:
            return self.get(uid)
        secret, pub = generate_keypair()
        enc = self._fernet.encrypt(secret.encode()).decode()
        self._data[uid] = {
            "pubkey": pub,
            "secret_enc": enc,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()
        logger.info(f"Created wallet for user {uid} → {pub}")
        return self.get(uid)

    def decrypt_secret(self, user_id: str) -> Optional[str]:
        rec = self._data.get(str(user_id))
        if not rec:
            return None
        try:
            return self._fernet.decrypt(rec["secret_enc"].encode()).decode()
        except Exception as e:
            logger.error(f"decrypt failed for {user_id}: {e}")
            return None
