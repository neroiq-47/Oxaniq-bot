# OxanIQ

Solana memecoin agent. Paper first. Don’t be a hero.

## Setup (2 min)

```bash
git clone https://github.com/neroiq-47/Oxaniq-bot.git
cd Oxaniq-bot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp config/example.env .env
```

Optional in `.env`:
- `HELIUS_API_KEY=...`
- `JUPITER_API_KEY=...`
- `TELEGRAM_BOT_TOKEN=...`
- `TELEGRAM_CHAT_ID=...`  (run telegram_ping after messaging the bot)

## Go paper (this is “live” for v0.1)

```bash
python -m src.main --paper
# or
./run.sh
```

Short test run:
```bash
python -m src.main --paper --cycles 5
```

Smoke check:
```bash
PYTHONPATH=. python3 scripts/smoke_test.py
```

Telegram (optional):
```bash
# 1. Add bot to group, send /start
# 2. Discover chat id
PYTHONPATH=. python3 scripts/telegram_ping.py
# 3. Put TELEGRAM_CHAT_ID in .env, run agent — cmds: /status /kill /revive
```

Bot loops: ingest → score → risk → paper buy/sell.  
State saves to `data/portfolio.json`. Ctrl+C to stop.

## About real live trading

**Not enabled in this build.**  
`--live` is blocked on purpose. No signed swaps yet.

When you’re ready to wire real SOL later:
1. Keep paper running until you trust the logs  
2. Add wallet key only in local `.env` (never commit it)  
3. Implement + audit Jupiter swap + signing  
4. Remove the live gate only after that  

Until then: paper = the product.

## Risk defaults

Small size caps, max open positions, daily loss kill switch, no double-buy same mint.  
Most Pump.fun coins still go to zero. Size like it.

## Repo

https://github.com/neroiq-47/Oxaniq-bot
