"""Logging: one format for CLI and future TUI. Warnings surface; info on -v."""

from __future__ import annotations

import logging

_configured = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"fccsv.{name}")


def configure(*, verbose: bool = False, quiet: bool = False) -> None:
    global _configured
    level = logging.DEBUG if verbose else (logging.ERROR if quiet else logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("fccsv")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    _configured = True
