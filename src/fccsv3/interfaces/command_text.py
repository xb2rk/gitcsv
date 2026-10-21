"""Render complete, copyable PowerShell command examples."""

from __future__ import annotations

import re


def table_arg(name: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return name
    return "'" + name.replace("'", "''") + "'"


def table_command(command: str, name: str, *, force: bool = False) -> str:
    value = f"fccsv3 {command} {table_arg(name)}"
    return value + (" --force" if force else "")
