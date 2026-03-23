from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sultan_engine_v2.runtime.env_loader import load_runtime_env
from sultan_engine_v2.runtime.fleet_monitor import run_server
from sultan_engine_v2.runtime.process_lock import FileProcessLock, ProcessAlreadyRunningError

load_runtime_env(__file__)


def _parse_port(raw_value: str | None, default: int = 5000) -> int:
    try:
        return int(str(raw_value or "").strip() or str(default))
    except (TypeError, ValueError):
        return default


def parse_fleet_monitor_options() -> tuple[str, int, str, str, str]:
    host = os.getenv("SULTAN_WEB_MONITOR_HOST", "127.0.0.1")
    raw_port = os.getenv("SULTAN_FLEET_MONITOR_PORT")
    if not str(raw_port or "").strip():
        raw_port = os.getenv("SULTAN_WEB_MONITOR_PORT", "5000")
    port = _parse_port(raw_port, default=5000)
    mt5_symbol = os.getenv("MT5_RUNTIME_SYMBOL", "XAUUSD")
    binance_symbol = os.getenv("BINANCE_RUNTIME_SYMBOL", "BTCUSDT")
    token = os.getenv("SULTAN_WEB_MONITOR_TOKEN", "").strip()
    return host, port, mt5_symbol, binance_symbol, token


def build_fleet_monitor_lock(port: int) -> FileProcessLock:
    runtime_dir = Path(__file__).resolve().parent / "runtime"
    return FileProcessLock(
        runtime_dir / "state" / "locks" / f"fleet_monitor_{port}.lock",
        service_name=f"FLEET_MONITOR_{port}",
        command="run_fleet_monitor.py",
    )


def main() -> None:
    host, port, mt5_symbol, binance_symbol, token = parse_fleet_monitor_options()
    lock = build_fleet_monitor_lock(port)
    try:
        lock.acquire()
    except ProcessAlreadyRunningError as error:
        print(f"Sultan fleet monitor already running on {port} with pid={error.metadata.pid}.")
        return

    try:
        run_server(host=host, port=port, mt5_symbol=mt5_symbol, binance_symbol=binance_symbol, access_token=token)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
