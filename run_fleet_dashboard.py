from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sultan_engine_v2.runtime.env_loader import load_runtime_env
from sultan_engine_v2.runtime.fleet_dashboard import main as fleet_dashboard_main
from sultan_engine_v2.runtime.process_lock import FileProcessLock, ProcessAlreadyRunningError

load_runtime_env(__file__)


def build_fleet_dashboard_lock(mt5_symbol: str, binance_symbol: str) -> FileProcessLock:
    runtime_dir = Path(__file__).resolve().parent / "runtime"
    return FileProcessLock(
        runtime_dir / "state" / "locks" / f"fleet_dashboard_{mt5_symbol.lower()}_{binance_symbol.lower()}.lock",
        service_name=f"FLEET_DASHBOARD_{mt5_symbol.upper()}_{binance_symbol.upper()}",
        command="run_fleet_dashboard.py",
    )


def main() -> None:
    mt5_symbol = os.getenv("MT5_RUNTIME_SYMBOL", "XAUUSD")
    binance_symbol = os.getenv("BINANCE_RUNTIME_SYMBOL", "BTCUSDT")
    lock = build_fleet_dashboard_lock(mt5_symbol, binance_symbol)
    try:
        lock.acquire()
    except ProcessAlreadyRunningError as error:
        print(f"Sultan fleet dashboard already running with pid={error.metadata.pid}.")
        return

    try:
        fleet_dashboard_main(mt5_symbol=mt5_symbol, binance_symbol=binance_symbol)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
