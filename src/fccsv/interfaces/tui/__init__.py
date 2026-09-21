"""lazygit-style TUI over `app/`. Thin view layer: every transfer runs the
same `cli._run_sync` / `cmd_*` orchestration as the CLI, so output and
semantics stay identical. No merge logic lives in widgets."""

from __future__ import annotations
