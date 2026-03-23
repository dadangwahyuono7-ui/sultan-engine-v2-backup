from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sultan_engine_v2.core.execution_models import ExecutionMode
from sultan_engine_v2.runtime.env_loader import load_runtime_env
from sultan_engine_v2.runtime.mt5_service import Mt5AutonomousService
from sultan_engine_v2.runtime.process_lock import FileProcessLock, ProcessAlreadyRunningError

load_runtime_env(__file__)


def _parse_bool(raw_value: str | None, default: bool = True) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().lower() not in {"0", "false", "off", "no"}


def main() -> None:
    base_symbol = sys.argv[1] if len(sys.argv) > 1 else os.getenv("MT5_RUNTIME_SYMBOL", "XAUUSD")
    execution_mode = ExecutionMode((sys.argv[2] if len(sys.argv) > 2 else os.getenv("SULTAN_EXECUTION_MODE", "PAPER")).upper())
    auto_execute = _parse_bool(sys.argv[3] if len(sys.argv) > 3 else os.getenv("SULTAN_AUTO_EXECUTE", "1"))
    poll_interval_seconds = float(os.getenv("SULTAN_SERVICE_POLL_SECONDS", "1"))
    reconnect_backoff_seconds = float(os.getenv("SULTAN_SERVICE_RECONNECT_BACKOFF_SECONDS", "5"))
    max_cycles = int(os.getenv("SULTAN_SERVICE_MAX_CYCLES", "0") or "0")

    runtime_dir = Path(__file__).resolve().parent / "runtime"
    lock = FileProcessLock(
        runtime_dir / "state" / "locks" / f"mt5_service_{base_symbol.lower()}.lock",
        service_name=f"MT5_SERVICE_{base_symbol.upper()}",
        command="run_mt5_service.py",
    )
    try:
        lock.acquire()
    except ProcessAlreadyRunningError as error:
        print(
            f"MT5 autonomous service already running for {base_symbol} with pid={error.metadata.pid} on {error.metadata.hostname}."
        )
        return

    service = Mt5AutonomousService(
        base_symbol=base_symbol,
        execution_mode=execution_mode,
        auto_execute=auto_execute,
        poll_interval_seconds=poll_interval_seconds,
        reconnect_backoff_seconds=reconnect_backoff_seconds,
        process_lock=lock,
    )
    print(f"Sultan MT5 autonomous service running for {base_symbol}...")
    service.run_forever(max_cycles=max_cycles or None)


if __name__ == "__main__":
    main()
