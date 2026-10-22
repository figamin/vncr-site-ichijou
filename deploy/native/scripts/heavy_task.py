#!/usr/bin/env python3
"""Dispatch a heavy backend task on the remote worker.

Run from anywhere after /etc/vnclub/heavy.env has been loaded. The backend
checkout is resolved relative to this file, so the same script works on the
web host and on the remote worker.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "vndb-stats-backend"
sys.path.insert(0, str(BACKEND))


async def main(task: str) -> None:
    if task == "daily":
        from scripts.worker import run_daily_update

        await run_daily_update()
    elif task == "similarity":
        from scripts.worker import recompute_models_only

        await recompute_models_only()
    elif task == "leaderboards":
        from app.leaderboards.compute import refresh_leaderboards

        stats = await refresh_leaderboards()
        print(stats)
    else:
        raise SystemExit(f"Unknown heavy task: {task}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: heavy_task.py daily|similarity|leaderboards")
    asyncio.run(main(sys.argv[1]))
