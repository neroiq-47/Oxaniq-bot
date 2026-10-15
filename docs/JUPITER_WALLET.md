# Jupiter + Wallet — recommendations

## Wallet (deposit funds)

1. Create a **dedicated** trading wallet (not your main bag).
2. Run once:
   ```bash
   PYTHONPATH=. python3 scripts/create_wallet.py
   ```
   or Telegram `/wallet create`
3. Deposit **small** SOL to the printed address (rent + fees + size caps).
4. Private key lives only in local `.env` as `SOLANA_PRIVATE_KEY`. **Never commit.**

## Jupiter integration path

**Already in repo**
- Quote client (`src/execution/jupiter.py`) — v6 quote API
- Telegram `/jup` — 0.01 SOL → USDC connectivity check
- API key via `JUPITER_API_KEY`

**Recommended next steps (before real size)**

| Step | Why |
|------|-----|
| 1. Paper until logs look sane | No real loss while scoring/risk settle |
| 2. Quote → simulate only | `swap` build + `simulateTransaction` on Helius |
| 3. Priority fees from Helius | Avoid stuck txs on congested slots |
| 4. Slippage caps per mint | Memecoins move; hard max bps |
| 5. Explicit live flag + size ceiling | Separate from paper; never auto-enable |
| 6. Sign with local key only | No remote custodial signing |

**Swap flow (when you enable live later)**

```
quote (Jupiter) → build swap tx → simulate → sign locally → send via Helius
```

Do **not** remove the live gate until that path is audited end-to-end.

## Deposit ability (what “funded” means)

- **Address**: `WALLET_PUBKEY` / `/deposit`
- **Balance**: `/balance` (Helius `getBalance`)
- **Spend**: only after live swap path exists; paper mode never touches chain

## Security checklist

- [ ] Unique wallet for the bot  
- [ ] `.env` gitignored  
- [ ] Tiny first deposit  
- [ ] `/kill` tested in Telegram  
- [ ] No token/key in GitHub or screenshots  
