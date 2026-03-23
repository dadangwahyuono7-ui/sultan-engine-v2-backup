from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sultan_engine_v2.runtime.env_loader import load_runtime_env
from sultan_engine_v2.runtime.reset_runtime_state import (
    build_reset_plan,
    describe_active_locks,
    execute_reset_plan,
)

load_runtime_env(__file__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive Sultan runtime paper/demo state so burn-in and winrate tests can restart from a clean baseline."
    )
    parser.add_argument("--mt5-symbol", default=os.getenv("MT5_RUNTIME_SYMBOL", "XAUUSD"))
    parser.add_argument("--binance-symbol", default=os.getenv("BINANCE_RUNTIME_SYMBOL", "BTCUSDT"))
    parser.add_argument("--include-operator-state", action="store_true")
    parser.add_argument("--include-tradingview-state", action="store_true")
    parser.add_argument("--keep-locks", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runtime_dir = Path(__file__).resolve().parent / "runtime"
    plan = build_reset_plan(
        runtime_dir=runtime_dir,
        mt5_symbol=args.mt5_symbol,
        binance_symbol=args.binance_symbol,
        include_operator_state=args.include_operator_state,
        include_tradingview_state=args.include_tradingview_state,
        clear_locks=not args.keep_locks,
    )

    if plan.active_locks:
        print("Active Sultan processes detected. Stop the running windows first, then run reset again.")
        for line in describe_active_locks(plan.active_locks):
            print(f"- {line}")
        return 1

    print("Sultan runtime reset plan")
    print(f"- MT5 symbol: {plan.mt5_symbol}")
    print(f"- Binance symbol: {plan.binance_symbol}")
    print(f"- Archive dir: {plan.archive_dir}")
    print(f"- Targets: {len(plan.target_paths)}")
    if plan.include_operator_state:
        print("- Operator state: included")
    if plan.include_tradingview_state:
        print("- TradingView replay state: included")

    if args.dry_run:
        for target in plan.target_paths:
            print(f"  * {target}")
        return 0

    result = execute_reset_plan(plan)
    print(f"Reset completed. Archived {len(result.moved_paths)} files.")
    print(f"Manifest: {result.manifest_path}")
    if result.skipped_paths:
        print(f"Skipped missing files: {len(result.skipped_paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
