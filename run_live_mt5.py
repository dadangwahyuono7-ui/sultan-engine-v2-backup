from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sultan_engine_v2.core.execution_models import ExecutionMode
from sultan_engine_v2.runtime.env_loader import load_runtime_env
from sultan_engine_v2.runtime.dashboard import main

load_runtime_env(__file__)


def _parse_bool(raw_value: str | None, default: bool = True) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().lower() not in {"0", "false", "off", "no"}


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else os.getenv("MT5_RUNTIME_SYMBOL", "XAUUSD")
    mode = ExecutionMode((sys.argv[2] if len(sys.argv) > 2 else os.getenv("SULTAN_EXECUTION_MODE", "PAPER")).upper())
    auto_execute = _parse_bool(sys.argv[3] if len(sys.argv) > 3 else os.getenv("SULTAN_AUTO_EXECUTE", "1"))
    main(base_symbol=symbol, execution_mode=mode, auto_execute=auto_execute)
