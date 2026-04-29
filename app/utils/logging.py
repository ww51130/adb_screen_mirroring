"""Logging configuration."""
import logging
import sys
import os
from pathlib import Path


def setup_logging(level=logging.INFO):
    """Set up logging to both console and file."""
    log_dir = Path(__file__).parent.parent.parent
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "screen_mirroring.log"

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )

    # Reduce noise from some libraries
    logging.getLogger("PIL").setLevel(logging.WARNING)
    return logging.getLogger("ScreenMirroring")
