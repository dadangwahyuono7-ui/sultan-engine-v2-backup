from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sultan_engine_v2.core.execution_models import ExecutionMode
from sultan_engine_v2.runtime.env_loader import load_runtime_env
from sultan_engine_v2.runtime.dashboard import main as dashboard_main
from sultan_engine_v2.runtime.process_lock import FileProcessLock, ProcessAlreadyRunningError

load_runtime_env(__file__)


def _parse_bool(raw_value: str | None, default: bool = True) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().lower() not in {"0", "false", "off", "no"}


def parse_dashboard_options() -> tuple[str, ExecutionMode, bool]:
    base_symbol = os.getenv("MT5_RUNTIME_SYMBOL", "XAUUSD")
    execution_mode = ExecutionMode(os.getenv("SULTAN_EXECUTION_MODE", "PAPER").upper())
    auto_execute = _parse_bool(os.getenv("SULTAN_AUTO_EXECUTE", "1"))
    return base_symbol, execution_mode, auto_execute


def build_dashboard_lock(base_symbol: str) -> FileProcessLock:
    runtime_dir = Path(__file__).resolve().parent / "runtime"
    return FileProcessLock(
        runtime_dir / "state" / "locks" / f"dashboard_{base_symbol.lower()}.lock",
        service_name=f"DASHBOARD_{base_symbol.upper()}",
        command="run_dashboard.py",
    )


def main() -> None:
    base_symbol, execution_mode, auto_execute = parse_dashboard_options()
    lock = build_dashboard_lock(base_symbol)
    try:
        lock.acquire()
    except ProcessAlreadyRunningError as error:
        print(f"Sultan dashboard already running for {base_symbol} with pid={error.metadata.pid}.")
        return

    try:
        dashboard_main(
            base_symbol=base_symbol,
            execution_mode=execution_mode,
            auto_execute=auto_execute,
        )
    finally:
        lock.release()


if __name__ == "__main__":
    main()
