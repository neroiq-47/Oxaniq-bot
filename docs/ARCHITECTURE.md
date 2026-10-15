# Oxaniq Architecture

## Overview

Oxaniq is a modular, low-memory autonomous agent for Solana memecoin trading.

```
┌─────────────────┐     ┌──────────────┐     ┌────────────────┐
│  Ingestion      │────▶│  Brain       │────▶│  Decision +    │
│  Pump.fun       │     │  Scorer      │     │  Risk Manager  │
│  FOMO (stub)    │     │  (rules)     │     │                │
└─────────────────┘     └──────────────┘     └───────┬────────┘
                                                     │
┌─────────────────┐     ┌──────────────┐             ▼
│  x402 Client    │◀───▶│  Execution   │◀──── Paper / Live
│  (micropayments)│     │  Engine      │
└─────────────────┘     └──────────────┘
```

## Key Design Choices for ≤4GB

- No local large LLM in v0.1 (rules + heuristics only). Can later plug Ollama / llama.cpp for 1-3B models.
- Async I/O (aiohttp) for non-blocking data.
- SQLite / in-memory state (easy to swap for LanceDB later).
- Strict position & daily loss limits.

## Extending

- Replace `Scorer` with LLM-backed version.
- Implement real Solana transaction signing in `execution/`.
- Add FOMO app WebSocket / API client.
- Wire real x402 ExactSvmScheme + facilitator.
- Add Telegram control bot.
- Persist portfolio to `data/portfolio.json` or SQLite.

## Safety First

Always run with `--paper` until thoroughly tested.
