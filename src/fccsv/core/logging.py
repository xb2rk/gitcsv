"""Logging setup. One format for CLI and future TUI; verbosity via configure()."""

import logging
import sys

_INFO = logging.INFO


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(_INFO)
    return logger


def configure(*, verbose: bool = False, quiet: bool = False) -> None:
    """Settle the fccsv logger family once, after arg parsing."""
    level = logging.DEBUG if verbose else (logging.ERROR if quiet else logging.INFO)
    root = logging.getLogger("fccsv")
    root.setLevel(level)
    for handler in root.handlers:
        handler.setLevel(level)
    # Children created before configure() inherit on next get_logger call.
    for name in ("fccsv.sync", "fccsv.sheets"):
        logging.getLogger(name).setLevel(level)
