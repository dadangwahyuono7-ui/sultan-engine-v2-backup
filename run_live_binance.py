from __future__ import annotations

import os
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sultan_engine_v2.core.execution_models import ExecutionMode
from sultan_engine_v2.runtime.env_loader import load_runtime_env
from sultan_engine_v2.runtime.exchange_live_runtime import BinanceLiveRuntime

load_runtime_env(__file__)


def _parse_bool(raw_value: str | None, default: bool = True) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().lower() not in {"0", "false", "off", "no"}


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else os.getenv("BINANCE_RUNTIME_SYMBOL", "BTCUSDT")
    mode = ExecutionMode((sys.argv[2] if len(sys.argv) > 2 else os.getenv("SULTAN_EXECUTION_MODE", "PAPER")).upper())
    auto_execute = _parse_bool(sys.argv[3] if len(sys.argv) > 3 else os.getenv("SULTAN_AUTO_EXECUTE", "1"))
    runtime = BinanceLiveRuntime(base_symbol=symbol, execution_mode=mode, auto_execute=auto_execute)
    runtime.connect()
    runtime.bootstrap()
    try:
        while True:
            snapshot = runtime.poll()
            timeframes = snapshot.get("timeframes", {})
            summary = " ".join(
                f"{timeframe}:{timeframes[timeframe].get('cmp', '-')}"
                for timeframe in ("D1", "H4", "H1", "M30", "M15", "M5")
                if timeframe in timeframes
            )
            price = snapshot.get("market", {}).get("price") or snapshot.get("market", {}).get("ask") or 0.0
            execution = snapshot.get("execution", {})
            result_count = len(execution.get("recent_results", []))
            print(
                f"{snapshot['timestamp']} | {snapshot['symbol']['actual'] or snapshot['symbol']['requested']} | "
                f"{price:.5f} | {summary} | mode={execution.get('mode', mode)} | exec={result_count}"
            )
            time.sleep(5)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
