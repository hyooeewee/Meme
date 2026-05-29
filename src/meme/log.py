"""Meme logging configuration."""

import logging
import sys

from meme.constants import MEME_HOME

_LOG_DIR = MEME_HOME / "log"
_LOG_FILE = _LOG_DIR / "meme.log"


def setup_logging(verbose: bool = False, quiet: bool = False):
    """Configure logging for the meme CLI.

    Levels:
      --quiet   → WARNING and above
      default   → INFO and above
      --verbose → DEBUG and above
    """
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Root logger for meme package
    logger = logging.getLogger("meme")
    logger.setLevel(level)

    # Clear existing handlers to avoid duplicates on re-entry
    logger.handlers.clear()

    # File handler — always logs DEBUG and above
    file_fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s:%(funcName)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_fmt)
    logger.addHandler(file_handler)

    # Console handler — respects level
    console_fmt = logging.Formatter("%(levelname)s: %(message)s")
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_handler.setFormatter(console_fmt)
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str = "meme") -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(name)
