from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sultan_engine_v2.runtime.env_loader import load_runtime_env
from sultan_engine_v2.runtime.process_lock import FileProcessLock, ProcessAlreadyRunningError
from sultan_engine_v2.runtime.web_monitor import run_server

load_runtime_env(__file__)


def parse_monitor_options() -> tuple[str, int, str, str]:
    host = os.getenv("SULTAN_WEB_MONITOR_HOST", "127.0.0.1")
    port = int(os.getenv("SULTAN_WEB_MONITOR_PORT", "5000"))
    symbol = os.getenv("SULTAN_MONITOR_BASE_SYMBOL") or os.getenv("MT5_RUNTIME_SYMBOL") or os.getenv("BINANCE_RUNTIME_SYMBOL") or "XAUUSD"
    token = os.getenv("SULTAN_WEB_MONITOR_TOKEN", "").strip()
    return host, port, symbol, token


def build_monitor_lock(base_symbol: str, port: int) -> FileProcessLock:
    runtime_dir = Path(__file__).resolve().parent / "runtime"
    return FileProcessLock(
        runtime_dir / "state" / "locks" / f"web_monitor_{base_symbol.lower()}_{port}.lock",
        service_name=f"WEB_MONITOR_{base_symbol.upper()}_{port}",
        command="run_web_monitor.py",
    )


def main() -> None:
    host, port, symbol, token = parse_monitor_options()
    lock = build_monitor_lock(symbol, port)
    try:
        lock.acquire()
    except ProcessAlreadyRunningError as error:
        print(f"Sultan web monitor already running for {symbol}:{port} with pid={error.metadata.pid}.")
        return

    try:
        run_server(host=host, port=port, base_symbol=symbol, access_token=token)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
