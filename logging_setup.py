"""Structured logging — one configured logger, level from LOG_LEVEL env.
Use `from logging_setup import get_logger; log = get_logger(__name__)` instead of print()
in long-running services (worker, api, run_window)."""
import logging
import os
import sys

_FMT = "%(asctime)s %(levelname)-7s %(name)s :: %(message)s"


def get_logger(name="axis"):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter(_FMT))
    logger.addHandler(h)
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    logger.propagate = False
    return logger
