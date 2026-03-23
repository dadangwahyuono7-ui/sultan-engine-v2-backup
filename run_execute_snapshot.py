from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sultan_engine_v2.adapters.exchange.binance_client import BinanceRestClient
from sultan_engine_v2.adapters.mt5.execution.trader import Mt5ExecutionClient
from sultan_engine_v2.core.execution_models import ExecutionMode, Venue
from sultan_engine_v2.runtime.env_loader import load_runtime_env
from sultan_engine_v2.runtime.execution_engine import ExecutionOrchestrator, SnapshotExecutionPlanner

load_runtime_env(__file__)


def _parse_bool(raw_value: str | None, default: bool = False) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "on", "yes"}


def main() -> None:
    snapshot_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "runtime" / "state" / "latest_snapshot.json"
    mode = ExecutionMode(sys.argv[2].upper()) if len(sys.argv) > 2 else ExecutionMode.PAPER
    snapshot = SnapshotExecutionPlanner.load_snapshot(snapshot_path)
    venue = Venue(snapshot.get("venue", "MT5"))
    allow_exchange = _parse_bool(os.getenv("SULTAN_ENABLE_EXCHANGE_EXECUTION"), default=False)

    if venue == Venue.BINANCE and mode == ExecutionMode.LIVE and not allow_exchange:
        print("Binance LIVE execution masih dikunci. Set SULTAN_ENABLE_EXCHANGE_EXECUTION=1 kalau memang mau live crypto.")
        return

    planner = SnapshotExecutionPlanner(default_venue=venue, execution_mode=mode)
    executors = {
        Venue.MT5: Mt5ExecutionClient(),
        Venue.BINANCE: BinanceRestClient(),
    }
    orchestrator = ExecutionOrchestrator(
        planner=planner,
        executors=executors,
    )
    results = orchestrator.execute_snapshot(snapshot)
    if not results:
        print("No execution candidates were produced from this snapshot.")
        return

    for result in results:
        print(
            f"{result.venue} {result.symbol} success={result.success} mode={result.execution_mode} "
            f"order_id={result.order_id or '-'} price={result.executed_price or 0.0} qty={result.quantity or 0.0} msg={result.message}"
        )


if __name__ == "__main__":
    main()
