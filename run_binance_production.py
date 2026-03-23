from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sultan_engine_v2.core.execution_models import ExecutionMode
from sultan_engine_v2.runtime.env_loader import load_runtime_env
from sultan_engine_v2.runtime.process_lock import FileProcessLock, ProcessAlreadyRunningError
from sultan_engine_v2.runtime.production_supervisor import ProductionSupervisor, load_production_supervisor_config_from_env

load_runtime_env(__file__)


def _parse_bool(raw_value: str | None, default: bool = True) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().lower() not in {"0", "false", "off", "no"}


def parse_binance_production_options(args: list[str] | None = None) -> tuple[str, ExecutionMode, bool]:
    args = args or []
    base_symbol = args[0] if len(args) > 0 else os.getenv("BINANCE_RUNTIME_SYMBOL", "BTCUSDT")
    execution_mode = ExecutionMode((args[1] if len(args) > 1 else os.getenv("SULTAN_EXECUTION_MODE", "PAPER")).upper())
    auto_execute = _parse_bool(args[2] if len(args) > 2 else os.getenv("SULTAN_AUTO_EXECUTE", "1"))
    return base_symbol, execution_mode, auto_execute


def main() -> None:
    base_symbol, execution_mode, auto_execute = parse_binance_production_options(sys.argv[1:])
    config = load_production_supervisor_config_from_env(
        base_symbol=base_symbol,
        execution_mode=execution_mode,
        auto_execute=auto_execute,
        child_script="run_binance_stack.py",
        heartbeat_file_template="binance_stack_{symbol}_status.json",
        state_file_template="binance_production_{symbol}_status.json",
        service_name="SULTAN_BINANCE_PRODUCTION_SUPERVISOR",
        symbol_env_key="BINANCE_RUNTIME_SYMBOL",
        child_max_cycles_env_key="SULTAN_BINANCE_STACK_MAX_CYCLES",
    )

    runtime_dir = Path(__file__).resolve().parent / "runtime"
    lock = FileProcessLock(
        runtime_dir / "state" / "locks" / f"binance_production_{base_symbol.lower()}.lock",
        service_name=f"BINANCE_PRODUCTION_{base_symbol.upper()}",
        command="run_binance_production.py",
    )
    try:
        lock.acquire()
    except ProcessAlreadyRunningError as error:
        print(f"Sultan Binance production supervisor already running for {base_symbol} with pid={error.metadata.pid} on {error.metadata.hostname}.")
        return

    supervisor = ProductionSupervisor(config=config, process_lock=lock)
    print(f"Sultan Binance production supervisor running for {base_symbol}...")
    supervisor.run_forever()


if __name__ == "__main__":
    main()
