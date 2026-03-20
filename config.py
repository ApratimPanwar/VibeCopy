import os
from dataclasses import dataclass, field
from typing import Tuple
from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    # Target traders (one or more Polygon addresses, lowercased)
    target_addresses: Tuple[str, ...] = ()

    # Authentication (only needed for live mode)
    private_key: str = ""
    proxy_wallet: str = ""  # Polymarket proxy wallet address (if different from signing key)

    # Execution
    mode: str = "simulate"  # "simulate" or "live"
    risk_pct: float = 1.0  # Max % of balance per trade (cap)
    copy_divisor: float = 10.0  # Bet 1/N of target's bet size (e.g. 10 = 1/10th)

    # Polling
    poll_interval: float = 3.0  # Seconds between polls

    # Status
    status_interval: float = 30.0  # Seconds between status prints

    # Risk controls
    max_daily_loss_usd: float = 50.0
    max_slippage_pct: float = 5.0
    min_trade_delay: float = 1.0  # Cooldown between executions (seconds)

    # API endpoints
    clob_host: str = "https://clob.polymarket.com"
    data_api_host: str = "https://data-api.polymarket.com"

    # Blockchain
    chain_id: int = 137  # Polygon mainnet
    signature_type: int = 0  # 0 = EOA, 1 = Magic/email login, 2 = browser wallet


def _parse_addresses(raw: str) -> Tuple[str, ...]:
    """Parse comma-separated addresses into a tuple of lowercase 0x addresses."""
    if not raw:
        return ()
    addrs = [a.strip().lower() for a in raw.split(",") if a.strip()]
    return tuple(addrs)


def load_config(cli_args=None) -> Config:
    """Load config from .env file, then override with CLI arguments."""
    load_dotenv()

    # Parse addresses from env (comma-separated)
    env_addresses = _parse_addresses(os.getenv("TARGET_ADDRESSES", "") or os.getenv("TARGET_ADDRESS", ""))

    kwargs = {
        "target_addresses": env_addresses,
        "private_key": os.getenv("PRIVATE_KEY", "").strip(),
        "proxy_wallet": os.getenv("PROXY_WALLET", "").strip().lower(),
        "mode": os.getenv("MODE", "simulate").strip().lower(),
        "risk_pct": float(os.getenv("RISK_PCT", "1.0")),
        "copy_divisor": float(os.getenv("COPY_DIVISOR", "10.0")),
        "poll_interval": float(os.getenv("POLL_INTERVAL", "3.0")),
        "status_interval": float(os.getenv("STATUS_INTERVAL", "30.0")),
        "max_daily_loss_usd": float(os.getenv("MAX_DAILY_LOSS_USD", "50.0")),
        "max_slippage_pct": float(os.getenv("MAX_SLIPPAGE_PCT", "5.0")),
        "signature_type": int(os.getenv("SIGNATURE_TYPE", "0")),
    }

    # CLI args override env vars
    if cli_args:
        cli_targets = getattr(cli_args, "target_addresses", None)
        if cli_targets:
            kwargs["target_addresses"] = tuple(a.strip().lower() for a in cli_targets)
        if getattr(cli_args, "mode", None):
            kwargs["mode"] = cli_args.mode.strip().lower()
        if getattr(cli_args, "risk_pct", None) is not None:
            kwargs["risk_pct"] = cli_args.risk_pct
        if getattr(cli_args, "poll_interval", None) is not None:
            kwargs["poll_interval"] = cli_args.poll_interval

    config = Config(**kwargs)
    _validate(config)
    return config


def _validate(config: Config):
    """Fail fast on invalid configuration."""
    if not config.target_addresses:
        raise ValueError(
            "At least one target address is required.\n"
            "  .env: TARGET_ADDRESSES=0xAAA,0xBBB\n"
            "  CLI:  --target 0xAAA --target 0xBBB"
        )

    for addr in config.target_addresses:
        if not addr.startswith("0x") or len(addr) != 42:
            raise ValueError(
                f"Invalid address: {addr} (must be 42-char 0x-prefixed hex)"
            )

    if config.mode not in ("simulate", "live"):
        raise ValueError(f"MODE must be 'simulate' or 'live', got: {config.mode}")

    if config.mode == "live" and not config.private_key:
        raise ValueError("PRIVATE_KEY is required for live mode. Set it in .env")

    if config.poll_interval < 1.0:
        raise ValueError("POLL_INTERVAL must be >= 1.0 seconds")

    if config.risk_pct <= 0 or config.risk_pct > 100:
        raise ValueError(f"RISK_PCT must be between 0 and 100, got: {config.risk_pct}")

    if config.copy_divisor <= 0:
        raise ValueError(f"COPY_DIVISOR must be positive, got: {config.copy_divisor}")

    if config.max_daily_loss_usd <= 0:
        raise ValueError("MAX_DAILY_LOSS_USD must be positive")
