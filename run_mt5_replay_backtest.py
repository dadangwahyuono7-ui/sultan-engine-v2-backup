from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sultan_engine_v2.runtime.env_loader import load_runtime_env
from sultan_engine_v2.runtime.mt5_replay_backtest import run_mt5_replay_backtest
from sultan_engine_v2.runtime.replay_backtest import ReplayBacktestConfig

load_runtime_env(__file__)


def main() -> None:
    base_symbol = sys.argv[1] if len(sys.argv) > 1 else os.getenv("MT5_RUNTIME_SYMBOL", "XAUUSD")
    output_path = Path(__file__).resolve().parent / "runtime" / "state" / f"mt5_replay_backtest_{base_symbol.lower()}.json"
    result = run_mt5_replay_backtest(
        base_symbol=base_symbol,
        lookback=int(os.getenv("SULTAN_MT5_REPLAY_LOOKBACK", "500") or "500"),
        execution_timeframe=os.getenv("SULTAN_MT5_REPLAY_EXECUTION_TIMEFRAME") or None,
        config=ReplayBacktestConfig(
            starting_balance=float(os.getenv("SULTAN_REPLAY_STARTING_BALANCE", "10000") or "10000"),
            risk_fraction=float(os.getenv("SULTAN_REPLAY_RISK_FRACTION", "0.005") or "0.005"),
            min_rr=float(os.getenv("SULTAN_REPLAY_MIN_RR", "1.1") or "1.1"),
            break_buffer=float(os.getenv("SULTAN_TRADINGVIEW_REPLAY_BREAK_BUFFER", "0") or "0"),
            spread=float(os.getenv("SULTAN_REPLAY_SPREAD", "0.4") or "0.4"),
            max_open_positions=int(os.getenv("SULTAN_REPLAY_MAX_OPEN_POSITIONS", "1") or "1"),
            prefer_worst_case_same_bar_exit=str(os.getenv("SULTAN_REPLAY_WORST_CASE_EXIT", "1")).strip() not in {"0", "false", "False"},
            enable_break_even_protect=str(os.getenv("SULTAN_REPLAY_BE_PROTECT", "1")).strip() not in {"0", "false", "False"},
            break_even_trigger_pips=float(os.getenv("SULTAN_REPLAY_BE_TRIGGER_PIPS", "10") or "10"),
            pip_size=float(os.getenv("SULTAN_REPLAY_PIP_SIZE", "0.1") or "0.1"),
        ),
        output_path=output_path,
    )
    print(f"MT5 replay backtest | {result.symbol} | tf={result.execution_timeframe}")
    print(f"Trades: {result.trade_count} | Win rate: {result.win_rate_percent:.2f}% | Net PnL: {result.net_pnl:.2f} | Return: {result.net_return_percent:.2f}%")
    print(f"Max DD: {result.max_drawdown_percent:.2f}% | Profit Factor: {result.profit_factor:.2f} | Ending Balance: {result.ending_balance:.2f}")
    print(f"Output: {result.output_path}")
    print(json.dumps({
        "trade_count": result.trade_count,
        "win_rate_percent": result.win_rate_percent,
        "net_pnl": result.net_pnl,
        "net_return_percent": result.net_return_percent,
        "max_drawdown_percent": result.max_drawdown_percent,
        "profit_factor": result.profit_factor,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
