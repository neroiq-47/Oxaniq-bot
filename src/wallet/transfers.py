"""Sign and send SOL / USDC transfers for a per-user wallet."""

from __future__ import annotations
from typing import Optional
import base58
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import transfer, TransferParams
from solders.message import Message
from solders.transaction import Transaction
from solders.hash import Hash
from solders.instruction import Instruction, AccountMeta
from src.utils.logger import setup_logger

logger = setup_logger()

TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
USDC_MINT = Pubkey.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
ASSOCIATED_TOKEN = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")


def keypair_from_secret(secret_b58: str) -> Keypair:
    raw = base58.b58decode(secret_b58)
    return Keypair.from_bytes(raw[:64] if len(raw) >= 64 else raw)


async def _recent_blockhash(helius) -> str:
    res = await helius._rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
    if isinstance(res, dict):
        return res["value"]["blockhash"]
    raise RuntimeError("no blockhash")


async def send_sol(helius, secret_b58: str, dest: str, sol_amount: float) -> str:
    kp = keypair_from_secret(secret_b58)
    lamports = int(sol_amount * 1_000_000_000)
    ix = transfer(TransferParams(
        from_pubkey=kp.pubkey(),
        to_pubkey=Pubkey.from_string(dest),
        lamports=lamports,
    ))
    bh = await _recent_blockhash(helius)
    msg = Message.new_with_blockhash([ix], kp.pubkey(), Hash.from_string(bh))
    tx = Transaction.new_unsigned(msg)
    tx.sign([kp], Hash.from_string(bh))
    raw = base58.b58encode(bytes(tx)).decode()
    sig = await helius._rpc("sendTransaction", [raw, {"encoding": "base58", "skipPreflight": False}])
    logger.info(f"SOL withdraw sent {sig}")
    return str(sig)


def _ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    """Derive associated token address."""
    from solders.pubkey import Pubkey as P
    seeds = [bytes(owner), bytes(TOKEN_PROGRAM), bytes(mint)]
    # solders has find_program_address
    from solders.pubkey import Pubkey
    addr, _ = Pubkey.find_program_address(seeds, ASSOCIATED_TOKEN)
    return addr


async def send_usdc(helius, secret_b58: str, dest_owner: str, usdc_amount: float) -> str:
    """Transfer USDC from user's ATA to destination owner's ATA.
    Creates destination ATA if missing (payer = user).
    """
    kp = keypair_from_secret(secret_b58)
    dest = Pubkey.from_string(dest_owner)
    src_ata = _ata(kp.pubkey(), USDC_MINT)
    dst_ata = _ata(dest, USDC_MINT)
    amount = int(usdc_amount * 1_000_000)

    ixs = []
    # Create dest ATA if needed (idempotent create)
    create_ata = Instruction(
        program_id=ASSOCIATED_TOKEN,
        accounts=[
            AccountMeta(kp.pubkey(), True, True),   # payer
            AccountMeta(dst_ata, False, True),
            AccountMeta(dest, False, False),
            AccountMeta(USDC_MINT, False, False),
            AccountMeta(Pubkey.from_string("11111111111111111111111111111111"), False, False),
            AccountMeta(TOKEN_PROGRAM, False, False),
        ],
        data=bytes([1]),  # CreateIdempotent
    )
    ixs.append(create_ata)

    # TransferChecked (index 12) amount u64 + decimals u8
    import struct
    data = bytes([12]) + struct.pack("<Q", amount) + bytes([6])
    transfer_ix = Instruction(
        program_id=TOKEN_PROGRAM,
        accounts=[
            AccountMeta(src_ata, False, True),
            AccountMeta(USDC_MINT, False, False),
            AccountMeta(dst_ata, False, True),
            AccountMeta(kp.pubkey(), True, False),
        ],
        data=data,
    )
    ixs.append(transfer_ix)

    bh = await _recent_blockhash(helius)
    msg = Message.new_with_blockhash(ixs, kp.pubkey(), Hash.from_string(bh))
    tx = Transaction.new_unsigned(msg)
    tx.sign([kp], Hash.from_string(bh))
    raw = base58.b58encode(bytes(tx)).decode()
    sig = await helius._rpc("sendTransaction", [raw, {"encoding": "base58", "skipPreflight": False}])
    logger.info(f"USDC withdraw sent {sig}")
    return str(sig)
