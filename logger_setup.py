import logging
import sys
from datetime import datetime


def setup_logging(level: str = "INFO", log_file: bool = False):
    """Configure logging with console output and optional file logging."""
    log_format = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
    date_format = "%H:%M:%S"

    handlers = []

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    handlers.append(console)

    # Optional file handler
    if log_file:
        filename = f"vibecopy_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        fh = logging.FileHandler(filename)
        fh.setFormatter(logging.Formatter(log_format, datefmt=date_format))
        handlers.append(fh)

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=handlers,
        force=True,
    )

    # Suppress noisy library loggers
    for name in ("httpx", "httpcore", "eth_account", "web3", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
