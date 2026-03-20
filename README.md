# VibeCopy

A lightweight Polymarket copy trading bot that monitors target traders and mirrors their trades in real-time.

## Features

- **Real-time trade detection** — polls Polymarket's Data API every 3 seconds for new trades
- **Multi-target tracking** — follow one or more traders simultaneously
- **Live & simulated modes** — paper trade to test strategies, or go live with real orders
- **Configurable sizing** — exact copy, divisor-based, or percentage-of-balance sizing
- **Risk management** — daily loss limits, staleness filters, extreme price filters
- **Share balance awareness** — checks owned shares before selling; places minimum buy if no position exists

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

Copy `.env.example` to `.env` and fill in your details:

```bash
cp .env.example .env
```

Required fields:
- `TARGET_ADDRESSES` — Polygon wallet address(es) of the trader(s) you want to copy
- `PRIVATE_KEY` — your wallet's private key (use a dedicated wallet!)
- `PROXY_WALLET` — your Polymarket proxy/trading wallet address
- `SIGNATURE_TYPE` — `0` for EOA, `1` for Magic/email login, `2` for browser wallet

### 3. Run

**Simulated mode (paper trading):**
```bash
python main.py --mode simulate
```

**Live mode (real orders):**
```bash
python main.py --mode live
```

**CLI overrides:**
```bash
python main.py --target 0xABC... --target 0xDEF... --mode simulate --risk-pct 3 --interval 5
```

## How It Works

1. **Tracker** polls the Polymarket Data API for each target address's recent trades
2. **Copier** deduplicates, filters (staleness, risk limits), and queues new trades
3. **Executor** places matching orders via the Polymarket CLOB API using `py-clob-client`
4. Status summaries print every 30 seconds showing uptime, copied trades, and P&L

## Configuration

| Variable | Default | Description |
|---|---|---|
| `TARGET_ADDRESSES` | — | Comma-separated wallet addresses to copy |
| `PRIVATE_KEY` | — | Your wallet private key |
| `PROXY_WALLET` | — | Your Polymarket proxy wallet |
| `SIGNATURE_TYPE` | `0` | Signature type (0=EOA, 1=Magic, 2=Browser) |
| `MODE` | `simulate` | `simulate` or `live` |
| `COPY_DIVISOR` | `10` | Copy 1/N of target's bet size |
| `RISK_PCT` | `2` | Max % of balance per trade |
| `POLL_INTERVAL` | `3.0` | Seconds between polls |
| `STATUS_INTERVAL` | `30.0` | Seconds between status prints |
| `MAX_DAILY_LOSS_USD` | `50.0` | Stop trading after this daily loss |
| `MAX_SLIPPAGE_PCT` | `5.0` | Max acceptable slippage |

## Project Structure

```
VibeCopy/
  main.py           — CLI entry point
  config.py         — Configuration loading (.env + CLI args)
  copier.py         — Multi-target orchestrator and risk filters
  executor.py       — Trade execution (simulated + live via CLOB API)
  tracker.py        — Per-address trade detection and deduplication
  models.py         — Data models (DetectedTrade, CopyResult, Position)
  logger_setup.py   — Logging configuration
  .env.example      — Configuration template
  requirements.txt  — Python dependencies
```

## Disclaimer

This bot trades with real money in live mode. Use at your own risk. Always start with simulated mode to verify behavior. Use a dedicated wallet with only the funds you're willing to lose.
