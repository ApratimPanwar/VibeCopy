"""
VibeCopy - Polymarket Copy Trading Bot

Monitors one or more traders on Polymarket and instantly copies their trades.
Supports simulated (paper trading) and live (real orders) modes.

Usage:
    python main.py --target 0xAAA --target 0xBBB --mode simulate
    python main.py --target 0xAAA --mode live --amount 25
    python main.py --help
"""

import argparse
import sys

from config import load_config
from copier import CopyTrader
from logger_setup import setup_logging


BANNER = r"""
 __      ___ _          ___
 \ \    / (_) |__  ___ / __|___ _ __ _  _
  \ \/\/ /| | '_ \/ -_) (__/ _ \ '_ \ || |
   \_/\_/ |_|_.__/\___|\___\___/ .__/\_, |
                               |_|   |__/
  Polymarket Copy Trading Bot
"""


def parse_args():
    parser = argparse.ArgumentParser(
        description="VibeCopy - Polymarket Copy Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Copy a single trader (simulate):
    python main.py --target 0x6af7...0ff1 --mode simulate

  Copy multiple traders:
    python main.py --target 0xAAA... --target 0xBBB... --mode simulate

  Live copy risking 5% of balance per trade:
    python main.py --target 0x6af7...0ff1 --mode live --risk-pct 5

  Custom poll interval and file logging:
    python main.py --target 0x6af7...0ff1 --interval 5 --log-file

  Use .env file for all config (comma-separated TARGET_ADDRESSES):
    python main.py
        """,
    )

    parser.add_argument(
        "--target",
        dest="target_addresses",
        action="append",
        help="Polygon wallet address to copy. Can be specified multiple times (overrides .env)",
    )
    parser.add_argument(
        "--mode",
        choices=["simulate", "live"],
        default=None,
        help="Execution mode: simulate (paper) or live (real orders). Default: simulate",
    )
    parser.add_argument(
        "--risk-pct",
        dest="risk_pct",
        type=float,
        default=None,
        help="Percent of balance to risk per copied trade. Default: 2",
    )
    parser.add_argument(
        "--interval",
        dest="poll_interval",
        type=float,
        default=None,
        help="Seconds between polls. Default: 3",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity. Default: INFO",
    )
    parser.add_argument(
        "--log-file",
        action="store_true",
        help="Also write logs to a timestamped file",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    setup_logging(level=args.log_level, log_file=args.log_file)

    print(BANNER)

    try:
        config = load_config(args)
    except ValueError as e:
        print(f"Configuration error: {e}")
        print("\nSee .env.example for required settings, or use --target flag.")
        sys.exit(1)

    # Safety confirmation for live mode
    if config.mode == "live":
        print("=" * 58)
        print("  WARNING: LIVE MODE - Real money will be used!")
        print(f"  Targets:        {len(config.target_addresses)} account(s)")
        for addr in config.target_addresses:
            print(f"                  {addr}")
        print(f"  Sizing:         1/{config.copy_divisor:.0f} of target bet, cap {config.risk_pct}% of bal")
        print(f"  Max daily loss: ${config.max_daily_loss_usd}")
        print("=" * 58)
        confirm = input("\nType 'yes' to confirm live trading: ").strip().lower()
        if confirm != "yes":
            print("Aborted. Run with --mode simulate for paper trading.")
            sys.exit(0)
        print()

    try:
        trader = CopyTrader(config)
        trader.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\nFatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
