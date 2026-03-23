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
    lookback = int(os.getenv("SULTAN_MT5_REPLAY_LOOKBACK", "2000") or "2000")

    be_values = [6, 8, 10, 12, 14, 16]
    spreads = [0.4, 0.8]

    rows = []
    for spread in spreads:
        for be in be_values:
            cfg = ReplayBacktestConfig(
                starting_balance=float(os.getenv("SULTAN_REPLAY_STARTING_BALANCE", "10000") or "10000"),
                risk_fraction=float(os.getenv("SULTAN_REPLAY_RISK_FRACTION", "0.005") or "0.005"),
                min_rr=float(os.getenv("SULTAN_REPLAY_MIN_RR", "1.1") or "1.1"),
                break_buffer=float(os.getenv("SULTAN_TRADINGVIEW_REPLAY_BREAK_BUFFER", "0") or "0"),
                spread=float(spread),
                max_open_positions=int(os.getenv("SULTAN_REPLAY_MAX_OPEN_POSITIONS", "1") or "1"),
                prefer_worst_case_same_bar_exit=str(os.getenv("SULTAN_REPLAY_WORST_CASE_EXIT", "1")).strip() not in {"0", "false", "False"},
                enable_break_even_protect=str(os.getenv("SULTAN_REPLAY_BE_PROTECT", "1")).strip() not in {"0", "false", "False"},
                break_even_trigger_pips=float(be),
                pip_size=float(os.getenv("SULTAN_REPLAY_PIP_SIZE", "0.1") or "0.1"),
            )
            result = run_mt5_replay_backtest(
                base_symbol=base_symbol,
                lookback=lookback,
                config=cfg,
                output_path=None,
            )
            row = {
                "symbol": result.symbol,
                "execution_timeframe": result.execution_timeframe,
                "lookback": lookback,
                "spread": spread,
                "be_trigger_pips": be,
                "trade_count": result.trade_count,
                "win_rate_percent": result.win_rate_percent,
                "profit_factor": result.profit_factor,
                "max_drawdown_percent": result.max_drawdown_percent,
                "net_pnl": result.net_pnl,
                "net_return_percent": result.net_return_percent,
                "ending_balance": result.ending_balance,
            }
            rows.append(row)
            print(
                f"spread={spread} be={be} | trades={result.trade_count} "
                f"win={result.win_rate_percent:.2f}% pf={result.profit_factor:.4f} "
                f"maxdd={result.max_drawdown_percent:.4f}% net={result.net_pnl:.2f}"
            )

    output_path = Path(__file__).resolve().parent / "runtime" / "state" / f"mt5_be_spread_sweep_{base_symbol.lower()}.json"
    payload = {
        "symbol": base_symbol,
        "lookback": lookback,
        "be_values": be_values,
        "spreads": spreads,
        "results": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"SWEEP_OUTPUT={output_path}")
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
