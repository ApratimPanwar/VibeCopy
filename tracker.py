import logging
import httpx
from datetime import datetime, timezone
from typing import List, Optional, Set

from config import Config
from models import DetectedTrade

logger = logging.getLogger(__name__)


class TradeTracker:
    """Polls the Polymarket Data API to detect new trades from a single target user."""

    def __init__(self, config: Config, address: str):
        self.config = config
        self.address = address.lower()
        self.label = f"{self.address[:6]}...{self.address[-4:]}"  # Short display label
        self.seen_ids: Set[str] = set()
        self.client = httpx.Client(timeout=10.0)
        self.base_url = config.data_api_host
        self._initialized = False
        self.trades_copied: int = 0

    def initialize(self):
        """Seed seen_ids with existing trades to avoid copying historical trades on startup."""
        logger.info(f"[{self.label}] Initializing tracker")
        trades = self._fetch_recent_activity(limit=50)
        for trade in trades:
            trade_id = self._extract_id(trade)
            if trade_id:
                self.seen_ids.add(trade_id)
        logger.info(f"[{self.label}] Seeded {len(self.seen_ids)} existing trade IDs")
        self._initialized = True

    def poll_new_trades(self) -> List[DetectedTrade]:
        """Fetch recent trades and return only new ones (oldest first)."""
        if not self._initialized:
            self.initialize()
            return []

        raw_trades = self._fetch_recent_activity(limit=10)
        new_trades = []

        for raw in raw_trades:
            trade_id = self._extract_id(raw)
            if trade_id and trade_id not in self.seen_ids:
                self.seen_ids.add(trade_id)
                detected = self._parse_trade(raw, trade_id)
                if detected:
                    new_trades.append(detected)

        # Return oldest first so we copy in chronological order
        new_trades.reverse()

        if new_trades:
            logger.info(f"[{self.label}] Detected {len(new_trades)} new trade(s)")
            for t in new_trades:
                logger.info(
                    f"  -> {t.side} {t.outcome} on '{t.market_slug}' "
                    f"@ {t.price:.4f} | {t.size:.2f} shares (${t.usd_value:.2f})"
                )

        return new_trades

    def _fetch_recent_activity(self, limit: int = 10) -> list:
        """GET /activity?user={addr}&type=TRADE&limit=N&sortBy=TIMESTAMP&sortDirection=DESC"""
        url = f"{self.base_url}/activity"
        params = {
            "user": self.address,
            "type": "TRADE",
            "limit": limit,
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        }
        try:
            resp = self.client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except httpx.HTTPStatusError as e:
            logger.error(f"[{self.label}] Data API HTTP error: {e.response.status_code}")
            return []
        except Exception as e:
            logger.error(f"[{self.label}] Data API request failed: {e}")
            return []

    def _extract_id(self, raw: dict) -> Optional[str]:
        """Extract a unique ID from the activity entry for deduplication.

        Multiple trades can share the same transactionHash (batch settlements),
        so we combine hash + asset + outcomeIndex for uniqueness.
        """
        tx_hash = raw.get("transactionHash") or raw.get("transaction_hash") or raw.get("id")
        if not tx_hash:
            return None
        asset = raw.get("asset") or raw.get("token_id") or ""
        outcome_idx = str(raw.get("outcomeIndex", ""))
        return f"{tx_hash}:{asset[-8:]}:{outcome_idx}"

    def _parse_trade(self, raw: dict, trade_id: str) -> Optional[DetectedTrade]:
        """Parse raw activity JSON into a DetectedTrade."""
        try:
            # Resolve side
            side = (raw.get("side") or "").upper()
            if side not in ("BUY", "SELL"):
                # Some responses use "Bought"/"Sold" or "action" field
                action = (raw.get("action") or raw.get("type") or "").lower()
                if "buy" in action or "bought" in action:
                    side = "BUY"
                elif "sell" in action or "sold" in action:
                    side = "SELL"
                else:
                    logger.warning(f"[{self.label}] Could not determine side from: {raw}")
                    return None

            price = float(raw.get("price") or 0)
            size = float(raw.get("size") or raw.get("amount") or 0)
            usd_value = float(raw.get("usdcSize") or raw.get("cashAmount") or (price * size))

            # Parse timestamp
            ts_raw = raw.get("timestamp") or raw.get("createdAt")
            timestamp = self._parse_timestamp(ts_raw)

            # Token ID - the asset identifier needed for CLOB orders
            token_id = raw.get("asset") or raw.get("token_id") or raw.get("assetId") or ""

            # Condition ID - identifies the market
            condition_id = raw.get("conditionId") or raw.get("condition_id") or ""

            return DetectedTrade(
                transaction_hash=trade_id,
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                usd_value=usd_value,
                market_slug=raw.get("slug") or raw.get("market_slug") or raw.get("title") or "",
                condition_id=condition_id,
                outcome=raw.get("outcome") or raw.get("outcomeLabel") or "",
                timestamp=timestamp,
                source_address=self.address,
                source_label=self.label,
            )
        except Exception as e:
            logger.error(f"[{self.label}] Failed to parse trade: {e} | raw keys: {list(raw.keys())}")
            return None

    def _parse_timestamp(self, ts_raw) -> datetime:
        """Parse various timestamp formats into a datetime."""
        if ts_raw is None:
            return datetime.now(tz=timezone.utc)
        if isinstance(ts_raw, (int, float)):
            # Unix timestamp - could be seconds or milliseconds
            if ts_raw > 1e12:
                ts_raw = ts_raw / 1000  # Convert ms to s
            return datetime.fromtimestamp(ts_raw, tz=timezone.utc)
        if isinstance(ts_raw, str):
            try:
                return datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                return datetime.now(tz=timezone.utc)
        return datetime.now(tz=timezone.utc)

    def get_profile(self) -> Optional[dict]:
        """Look up the target user's public profile."""
        url = f"{self.base_url}/profile"
        try:
            resp = self.client.get(url, params={"address": self.address})
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.debug(f"[{self.label}] Could not fetch profile: {e}")
            return None

    def close(self):
        """Close the HTTP client."""
        self.client.close()
