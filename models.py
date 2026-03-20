from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class DetectedTrade:
    """A trade detected from the target user via the Data API."""

    transaction_hash: str
    token_id: str
    side: str  # "BUY" or "SELL"
    price: float  # Execution price (0-1 range)
    size: float  # Number of shares
    usd_value: float  # Approximate USDC value
    market_slug: str
    condition_id: str
    outcome: str  # "Yes" or "No"
    timestamp: datetime
    source_address: str = ""  # Which target wallet this trade came from
    source_label: str = ""  # Display name for the source (username or short addr)
    neg_risk: Optional[bool] = None


@dataclass
class CopyResult:
    """Result of attempting to copy a trade."""

    detected_trade: DetectedTrade
    success: bool
    order_id: Optional[str] = None
    executed_price: Optional[float] = None
    executed_amount: Optional[float] = None
    error: Optional[str] = None
    simulated: bool = False


@dataclass
class Position:
    """Tracks a position in simulation mode."""

    token_id: str
    market_slug: str
    outcome: str
    side: str
    avg_price: float
    size: float
    usd_invested: float


@dataclass
class DailyStats:
    """Running daily statistics for risk management."""

    date: str  # YYYY-MM-DD
    total_trades: int = 0
    total_usd_traded: float = 0.0
    realized_pnl: float = 0.0
    positions: dict = field(default_factory=dict)
