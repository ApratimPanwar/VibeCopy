import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from config import Config
from models import DetectedTrade, CopyResult
from tracker import TradeTracker
from executor import BaseExecutor, SimulatedExecutor, LiveExecutor

logger = logging.getLogger(__name__)


class CopyTrader:
    """Orchestrates trade detection across multiple targets and execution with risk management."""

    def __init__(self, config: Config):
        self.config = config
        self.trackers: Dict[str, TradeTracker] = {}  # address -> tracker
        self.tracker_labels: Dict[str, str] = {}  # address -> display name
        self.executor: BaseExecutor = self._create_executor()
        self.running = False
        self.copy_results: List[CopyResult] = []
        self._last_status_time: float = 0.0
        self._start_time: float = 0.0

        # Create one tracker per target address
        for addr in config.target_addresses:
            tracker = TradeTracker(config, addr)
            self.trackers[addr] = tracker

    def _create_executor(self) -> BaseExecutor:
        if self.config.mode == "simulate":
            logger.info("Mode: SIMULATED (paper trading)")
            return SimulatedExecutor(self.config)
        else:
            logger.info("Mode: LIVE (real orders)")
            return LiveExecutor(self.config)

    def run(self):
        """Main polling loop. Blocks until Ctrl+C."""
        self.running = True
        self._start_time = time.monotonic()
        self._last_status_time = time.monotonic()

        # Resolve display names and show startup info
        self._resolve_labels()
        self._print_startup_info()

        # Initialize all trackers (seed seen trades)
        for addr, tracker in self.trackers.items():
            tracker.label = self.tracker_labels.get(addr, tracker.label)
            tracker.initialize()

        logger.info(f"Monitoring {len(self.trackers)} account(s) for new trades... (Ctrl+C to stop)")
        print()

        try:
            while self.running:
                self._poll_cycle()
                self._maybe_print_status()
                time.sleep(self.config.poll_interval)
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            self.shutdown()

    def _resolve_labels(self):
        """Look up profile names for all targets."""
        for addr, tracker in self.trackers.items():
            profile = tracker.get_profile()
            if profile:
                name = (
                    profile.get("username")
                    or profile.get("pseudonym")
                    or profile.get("name")
                )
                if name:
                    self.tracker_labels[addr] = name
                    tracker.label = name
                    continue
            # Fallback: short address
            self.tracker_labels[addr] = f"{addr[:6]}...{addr[-4:]}"

    def _print_startup_info(self):
        """Display target list and config on startup."""
        logger.info(f"Tracking {len(self.trackers)} account(s):")
        for addr in self.config.target_addresses:
            label = self.tracker_labels.get(addr, addr)
            logger.info(f"  [{label}] {addr}")

        logger.info(
            f"Config: auto-size 1-3% of balance per trade | "
            f"Poll: {self.config.poll_interval}s | "
            f"Status: every {self.config.status_interval:.0f}s | "
            f"Max loss: ${self.config.max_daily_loss_usd}/day"
        )

    def _poll_cycle(self):
        """Single polling cycle: detect from all trackers -> filter -> execute."""
        self.executor.reset_daily_stats_if_needed()

        # Collect new trades from ALL trackers
        all_new_trades: List[DetectedTrade] = []
        for tracker in self.trackers.values():
            new_trades = tracker.poll_new_trades()
            all_new_trades.extend(new_trades)

        # Sort by timestamp so we process oldest first across all accounts
        all_new_trades.sort(key=lambda t: t.timestamp)

        for trade in all_new_trades:
            # Apply risk filters
            skip_reason = self._check_risk_filters(trade)
            if skip_reason:
                logger.warning(
                    f"SKIP: {skip_reason} | [{trade.source_label}] "
                    f"{trade.side} {trade.outcome} on '{trade.market_slug}'"
                )
                continue

            # Enforce cooldown between executions
            if not self.executor.check_cooldown():
                remaining = self.config.min_trade_delay - (
                    time.monotonic() - self.executor.last_execution_time
                )
                if remaining > 0:
                    time.sleep(remaining)

            # Execute the copy
            logger.info(f"Copying [{trade.source_label}] ...")
            result = self.executor.execute_copy(trade)
            self.copy_results.append(result)
            self.executor.record_execution()

            # Update per-tracker counter
            if result.success:
                tracker = self.trackers.get(trade.source_address)
                if tracker:
                    tracker.trades_copied += 1

            if not result.success:
                logger.error(f"Copy FAILED: {result.error}")

    def _check_risk_filters(self, trade: DetectedTrade) -> Optional[str]:
        """Returns a reason string if the trade should be skipped, or None if OK."""
        stats = self.executor.daily_stats

        # 1. Daily loss limit
        if stats.realized_pnl < -self.config.max_daily_loss_usd:
            return f"Daily loss limit exceeded (${stats.realized_pnl:.2f})"

        # 2. Staleness - skip trades older than 60 seconds
        age = (datetime.now(tz=timezone.utc) - trade.timestamp).total_seconds()
        if age > 60:
            return f"Trade is {age:.0f}s old (stale, >60s)"

        # 3. Invalid price or size
        if trade.price <= 0 or trade.size <= 0:
            return f"Invalid price ({trade.price}) or size ({trade.size})"

        # 4. Extreme prices (near-certain outcomes, bad risk/reward)
        if trade.price >= 0.99:
            return f"Price too high ({trade.price:.4f} >= 0.99)"
        if trade.price <= 0.01:
            return f"Price too low ({trade.price:.4f} <= 0.01)"

        # 5. Missing token ID
        if not trade.token_id:
            return "Missing token_id (cannot place order)"

        return None

    def _maybe_print_status(self):
        """Print a status summary every status_interval seconds."""
        now = time.monotonic()
        if now - self._last_status_time < self.config.status_interval:
            return

        self._last_status_time = now
        uptime_s = now - self._start_time
        uptime_str = self._format_uptime(uptime_s)

        stats = self.executor.daily_stats
        total_copied = sum(1 for r in self.copy_results if r.success)
        total_failed = sum(1 for r in self.copy_results if not r.success)

        # Build status block
        lines = [
            "",
            "=" * 58,
            f"  STATUS  |  Uptime: {uptime_str}  |  {datetime.now(tz=timezone.utc).strftime('%H:%M:%S')} UTC",
            "-" * 58,
        ]

        # Per-account stats
        for addr in self.config.target_addresses:
            tracker = self.trackers.get(addr)
            label = self.tracker_labels.get(addr, addr[:10])
            copied = tracker.trades_copied if tracker else 0
            seen = len(tracker.seen_ids) if tracker else 0
            lines.append(f"  [{label:>15}]  copied: {copied}  |  seen: {seen}")

        lines.append("-" * 58)

        # Aggregate stats
        lines.append(
            f"  Total copied: {total_copied}  |  Failed: {total_failed}  |  "
            f"Today volume: ${stats.total_usd_traded:.2f}"
        )

        # Simulation portfolio info
        if isinstance(self.executor, SimulatedExecutor):
            lines.append(
                f"  Balance: ${self.executor.virtual_balance:.2f}  |  "
                f"Positions: {len(self.executor.positions)}  |  "
                f"P&L: ${stats.realized_pnl:+.2f}"
            )

        lines.append("=" * 58)
        lines.append("")

        print("\n".join(lines))

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        """Format seconds into a human-readable uptime string."""
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}h {m}m {s}s"
        elif m > 0:
            return f"{m}m {s}s"
        else:
            return f"{s}s"

    def shutdown(self):
        """Clean shutdown with session summary."""
        self.running = False
        for tracker in self.trackers.values():
            tracker.close()

        uptime_s = time.monotonic() - self._start_time if self._start_time else 0
        uptime_str = self._format_uptime(uptime_s)

        print()
        logger.info("=" * 50)
        logger.info(f"SESSION SUMMARY  (uptime: {uptime_str})")
        logger.info("=" * 50)

        total = len(self.copy_results)
        successful = sum(1 for r in self.copy_results if r.success)
        failed = total - successful

        # Per-account breakdown
        for addr in self.config.target_addresses:
            tracker = self.trackers.get(addr)
            label = self.tracker_labels.get(addr, addr[:10])
            copied = tracker.trades_copied if tracker else 0
            logger.info(f"  [{label}] {copied} trades copied")

        logger.info(f"Total: {successful}/{total} trades copied ({failed} failed)")

        if isinstance(self.executor, SimulatedExecutor):
            logger.info("Portfolio:")
            logger.info(self.executor.get_portfolio_summary())
