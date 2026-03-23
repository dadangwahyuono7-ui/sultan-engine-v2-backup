from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sultan_engine_v2.runtime.env_loader import load_runtime_env
from sultan_engine_v2.runtime.tradingview_replay import build_tradingview_replay, parse_timeframe_file_args

load_runtime_env(__file__)


def main() -> None:
    args = sys.argv[1:]
    if len(args) < 2:
        print("Usage: python sultan_engine_v2/run_tradingview_replay.py <TICKER> <TF=csv_path> [<TF=csv_path> ...]")
        print("Example: python sultan_engine_v2/run_tradingview_replay.py BINANCE:BTCUSDT D1=d1.csv H4=h4.csv H1=h1.csv M30=m30.csv")
        return

    ticker = args[0]
    timeframe_files = parse_timeframe_file_args(args[1:])
    break_buffer = float(os.getenv("SULTAN_TRADINGVIEW_REPLAY_BREAK_BUFFER", "0") or "0")
    result = build_tradingview_replay(
        ticker=ticker,
        timeframe_files=timeframe_files,
        break_buffer=break_buffer,
    )

    print(f"TradingView replay mapped to {result.mapping.preferred_venue} | base_symbol={result.mapping.base_symbol}")
    print(f"Focus: {result.snapshot.get('setup_focus', {}).get('summary') or '-'}")
    print(f"Snapshot: {result.output_path}")
    print(f"Audit: {result.report.summary}")


if __name__ == "__main__":
    main()
