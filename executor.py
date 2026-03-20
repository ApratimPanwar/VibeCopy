import logging
import math
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from config import Config
from models import DetectedTrade, CopyResult, Position, DailyStats

logger = logging.getLogger(__name__)


class BaseExecutor(ABC):
    """Abstract base for trade executors."""

    def __init__(self, config: Config):
        self.config = config
        self.daily_stats = DailyStats(
            date=datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        )
        self.last_execution_time: float = 0.0

    @abstractmethod
    def execute_copy(self, trade: DetectedTrade) -> CopyResult:
        pass

    @abstractmethod
    def get_balance(self) -> float:
        pass

    # Polymarket minimum order size in USD (API enforces $1 for BUY)
    MIN_BET = 1.00

    def compute_trade_amount(self, trade: DetectedTrade) -> float:
        """Copy exact same bet as the target, capped at available balance."""
        balance = self.get_balance()
        if balance <= 0:
            return 0.0

        amount = trade.usd_value if trade.usd_value > 0 else 0.0

        # Floor
        if amount < self.MIN_BET:
            amount = self.MIN_BET

        amount = min(amount, balance)
        return round(amount, 2)

    def check_cooldown(self) -> bool:
        """Returns True if enough time has passed since last execution."""
        elapsed = time.monotonic() - self.last_execution_time
        return elapsed >= self.config.min_trade_delay

    def record_execution(self):
        self.last_execution_time = time.monotonic()

    def reset_daily_stats_if_needed(self):
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        if self.daily_stats.date != today:
            logger.info(f"New day ({today}), resetting daily stats")
            self.daily_stats = DailyStats(date=today)


class SimulatedExecutor(BaseExecutor):
    """Paper trading executor - logs trades and tracks virtual P&L."""

    def __init__(self, config: Config):
        super().__init__(config)
        self.virtual_balance: float = 1000.0
        self.positions: dict[str, Position] = {}
        self.trade_log: list[CopyResult] = []

    def execute_copy(self, trade: DetectedTrade) -> CopyResult:
        amount = self.compute_trade_amount(trade)

        if amount <= 0:
            return CopyResult(
                detected_trade=trade, success=False,
                error="Zero balance", simulated=True
            )

        if trade.side == "BUY":
            if trade.price <= 0:
                return CopyResult(
                    detected_trade=trade, success=False, error="Invalid price", simulated=True
                )

            shares = amount / trade.price
            self.virtual_balance -= amount

            if trade.token_id in self.positions:
                pos = self.positions[trade.token_id]
                total_cost = pos.usd_invested + amount
                total_shares = pos.size + shares
                pos.avg_price = total_cost / total_shares if total_shares > 0 else 0
                pos.size = total_shares
                pos.usd_invested = total_cost
            else:
                self.positions[trade.token_id] = Position(
                    token_id=trade.token_id,
                    market_slug=trade.market_slug,
                    outcome=trade.outcome,
                    side="BUY",
                    avg_price=trade.price,
                    size=shares,
                    usd_invested=amount,
                )
        else:  # SELL
            if trade.token_id in self.positions:
                pos = self.positions[trade.token_id]
                sell_shares = min(pos.size, amount / trade.price if trade.price > 0 else 0)
                proceeds = sell_shares * trade.price
                pnl = proceeds - (sell_shares * pos.avg_price)
                self.virtual_balance += proceeds
                self.daily_stats.realized_pnl += pnl
                pos.size -= sell_shares
                pos.usd_invested -= sell_shares * pos.avg_price
                if pos.size <= 0.001:
                    del self.positions[trade.token_id]
            else:
                # No position to sell - still record the trade
                self.virtual_balance += amount
                logger.debug(f"[SIM] SELL without position for {trade.token_id}")

        result = CopyResult(
            detected_trade=trade,
            success=True,
            executed_price=trade.price,
            executed_amount=amount,
            simulated=True,
        )

        self.trade_log.append(result)
        self.daily_stats.total_trades += 1
        self.daily_stats.total_usd_traded += amount

        target_bet = trade.usd_value
        logger.info(
            f"[SIM] {trade.side} | {trade.outcome} on '{trade.market_slug}' "
            f"@ {trade.price:.4f} | ${amount:.2f} "
            f"(target bet ${target_bet:.2f} / {self.config.copy_divisor:.0f}) "
            f"| Balance: ${self.virtual_balance:.2f}"
        )

        return result

    def get_balance(self) -> float:
        return self.virtual_balance

    def get_portfolio_summary(self) -> str:
        bal_cap = self.virtual_balance * (self.config.risk_pct / 100.0)
        lines = [
            f"  Virtual Balance: ${self.virtual_balance:.2f}",
            f"  Sizing: 1/{self.config.copy_divisor:.0f} of target's bet, capped at {self.config.risk_pct}% of bal (${bal_cap:.2f})",
            f"  Open Positions:  {len(self.positions)}",
        ]
        for pos in self.positions.values():
            lines.append(
                f"    {pos.outcome} on '{pos.market_slug}': "
                f"{pos.size:.2f} shares @ avg ${pos.avg_price:.4f} "
                f"(invested: ${pos.usd_invested:.2f})"
            )
        lines.append(
            f"  Today: {self.daily_stats.total_trades} trades, "
            f"${self.daily_stats.total_usd_traded:.2f} volume, "
            f"P&L: ${self.daily_stats.realized_pnl:.2f}"
        )
        return "\n".join(lines)


class LiveExecutor(BaseExecutor):
    """Real order execution via py-clob-client."""

    def __init__(self, config: Config):
        super().__init__(config)
        self.clob = None
        self._cached_balance: float = -1.0
        self._balance_cache_time: float = 0.0
        self._init_clob_client()

    def _init_clob_client(self):
        """Initialize and authenticate the CLOB client."""
        from py_clob_client.client import ClobClient

        clob_kwargs = dict(
            host=self.config.clob_host,
            key=self.config.private_key,
            chain_id=self.config.chain_id,
            signature_type=self.config.signature_type,
        )
        if self.config.proxy_wallet:
            clob_kwargs["funder"] = self.config.proxy_wallet
        self.clob = ClobClient(**clob_kwargs)

        # Derive or create L2 API credentials
        logger.info("Deriving CLOB API credentials...")
        creds = self.clob.create_or_derive_api_creds()
        if creds is None:
            raise RuntimeError(
                "Failed to derive CLOB API credentials. "
                "Check your PRIVATE_KEY and network connection."
            )
        self.clob.set_api_creds(creds)
        addr = self.clob.get_address()
        funder_msg = f", Proxy: {self.config.proxy_wallet}" if self.config.proxy_wallet else ""
        logger.info(f"CLOB client authenticated. Signer: {addr}{funder_msg}")

    def _get_share_balance(self, token_id: str) -> float:
        """Check how many shares we own of a specific token."""
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        try:
            result = self.clob.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id)
            )
            raw = result.get("balance", "0") if isinstance(result, dict) else "0"
            return float(raw) / 1e6
        except Exception as e:
            logger.debug(f"Could not check share balance: {e}")
            return 0.0

    def execute_copy(self, trade: DetectedTrade) -> CopyResult:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType

        amount = self.compute_trade_amount(trade)

        if amount <= 0:
            return CopyResult(
                detected_trade=trade, success=False,
                error="Zero balance", simulated=False
            )

        try:
            if trade.side == "BUY":
                # USDC amount: truncate to 2 decimals, minimum $1
                order_amount = math.floor(amount * 100) / 100
                if order_amount < 1.0:
                    order_amount = 1.0
            else:
                # SELL: check how many shares we actually own
                shares_owned = self._get_share_balance(trade.token_id)
                shares_to_sell = amount / trade.price if trade.price > 0 else 0
                if shares_owned <= 0:
                    # No shares? Buy minimum instead to get into the position
                    logger.info(
                        f"[LIVE] No shares to SELL '{trade.market_slug}' — placing min BUY instead"
                    )
                    order_amount = 1.0
                    trade = DetectedTrade(
                        transaction_hash=trade.transaction_hash,
                        token_id=trade.token_id,
                        side="BUY",
                        price=trade.price,
                        size=trade.size,
                        usd_value=trade.usd_value,
                        market_slug=trade.market_slug,
                        condition_id=trade.condition_id,
                        outcome=trade.outcome,
                        timestamp=trade.timestamp,
                        source_address=trade.source_address,
                        source_label=trade.source_label,
                        neg_risk=trade.neg_risk,
                    )
                else:
                    # Sell what we have, truncate to 4 decimals
                    order_amount = min(shares_to_sell, shares_owned)
                    order_amount = math.floor(order_amount * 10000) / 10000
                    # Minimum 0.0001 shares
                    if order_amount <= 0:
                        order_amount = min(0.0001, shares_owned)

            order_args = MarketOrderArgs(
                token_id=trade.token_id,
                amount=order_amount,
                side=trade.side,
                price=0,
                fee_rate_bps=0,
                nonce=0,
                order_type=OrderType.FOK,
            )

            signed_order = self.clob.create_market_order(order_args)
            response = self.clob.post_order(signed_order, OrderType.FOK)

            order_id = None
            if isinstance(response, dict):
                order_id = response.get("orderID") or response.get("id")

            result = CopyResult(
                detected_trade=trade,
                success=True,
                order_id=order_id,
                executed_price=trade.price,
                executed_amount=amount,
                simulated=False,
            )

            self.daily_stats.total_trades += 1
            self.daily_stats.total_usd_traded += amount
            self._cached_balance = -1.0

            target_bet = trade.usd_value
            risk_pct = (amount / self.get_balance() * 100) if self.get_balance() > 0 else 0
            logger.info(
                f"[LIVE] {trade.side} | {trade.outcome} on '{trade.market_slug}' "
                f"@ ~{trade.price:.4f} | ${amount:.2f} ({risk_pct:.1f}% of bal) "
                f"| target ${target_bet:.2f} | OrderID: {order_id}"
            )

            return result

        except Exception as e:
            logger.error(f"[LIVE] Order failed for '{trade.market_slug}': {e}")
            return CopyResult(
                detected_trade=trade,
                success=False,
                error=str(e),
                simulated=False,
            )

    def get_balance(self) -> float:
        """Get USDC balance from the CLOB. Caches for 10 seconds."""
        now = time.monotonic()
        if self._cached_balance >= 0 and (now - self._balance_cache_time) < 10.0:
            return self._cached_balance

        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

        try:
            result = self.clob.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            # Balance is returned as a string in USDC units (6 decimals)
            balance_raw = result.get("balance", "0") if isinstance(result, dict) else "0"
            self._cached_balance = float(balance_raw) / 1e6
            self._balance_cache_time = now
            return self._cached_balance
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return self._cached_balance if self._cached_balance >= 0 else 0.0
