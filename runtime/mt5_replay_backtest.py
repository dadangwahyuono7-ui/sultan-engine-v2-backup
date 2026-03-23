from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sultan_engine_v2.adapters.mt5.session.client import ActiveMt5Session
from sultan_engine_v2.core.breakout_detector import bootstrap_latest_breakout
from sultan_engine_v2.core.execution_models import ExecutionMode
from sultan_engine_v2.core.state_engine import SultanStateEngine
from sultan_engine_v2.core.timeframe_relations import TRADING_TIMEFRAMES
from sultan_engine_v2.runtime.file_utils import atomic_write_json
from sultan_engine_v2.runtime.replay_backtest import (
    ReplayBacktestConfig,
    ReplayBacktestResult,
    _OpenReplayPosition,
    _build_open_position,
    _build_trade,
    _compute_max_drawdown,
    _compute_quantity,
    _derive_h4_m30_m5_cheat_code,
    _force_close_position,
    _resolve_execution_timeframe,
    _summarize_doctrine_audit,
    _try_close_position,
)
from sultan_engine_v2.runtime.execution_engine import SnapshotExecutionPlanner


def run_mt5_replay_backtest(
    *,
    base_symbol: str,
    timeframes: tuple[str, ...] = ("D1", "H4", "H1", "M30", "M15", "M5"),
    lookback: int = 500,
    execution_timeframe: str | None = None,
    config: ReplayBacktestConfig | None = None,
    output_path: str | Path | None = None,
) -> ReplayBacktestResult:
    cfg = config or ReplayBacktestConfig()
    session = ActiveMt5Session()
    snapshot = session.connect()
    if not snapshot.connected:
        raise RuntimeError(f"MT5 not connected: {snapshot.error}")

    resolved = session.resolve_symbol(base_symbol)
    actual_symbol = resolved.actual
    if not actual_symbol:
        raise RuntimeError(f"Unable to resolve MT5 symbol for {base_symbol}")

    bars_by_tf = {
        timeframe: session.fetch_closed_bars(actual_symbol, timeframe, lookback)
        for timeframe in timeframes
    }
    bars_by_tf = {key: value for key, value in bars_by_tf.items() if value}
    if not bars_by_tf:
        raise RuntimeError(f"No MT5 bars available for {actual_symbol}")

    chosen_execution_tf = execution_timeframe or _resolve_execution_timeframe(bars_by_tf)
    execution_bars = bars_by_tf.get(chosen_execution_tf)
    if not execution_bars:
        raise RuntimeError(f"Execution timeframe not available from MT5: {chosen_execution_tf}")

    planner = SnapshotExecutionPlanner(
        execution_mode=ExecutionMode.PAPER,
        risk_fraction=cfg.risk_fraction,
        min_rr=cfg.min_rr,
    )

    balance = float(cfg.starting_balance)
    open_positions: list[_OpenReplayPosition] = []
    seen_intents: set[str] = set()
    trades = []

    for bar in execution_bars:
        if bar.timestamp is None:
            continue

        replay_snapshot = _build_mt5_snapshot_for_timestamp(
            actual_symbol=actual_symbol,
            base_symbol=base_symbol,
            bars_by_tf=bars_by_tf,
            timestamp=int(bar.timestamp),
            break_buffer=cfg.break_buffer,
            market_bar=bar,
            spread=cfg.spread,
        )

        if open_positions:
            remaining_positions: list[_OpenReplayPosition] = []
            for position in open_positions:
                closed_trade = _try_close_position(position, bar, cfg, snapshot=replay_snapshot)
                if closed_trade is None:
                    remaining_positions.append(position)
                    continue
                trades.append(closed_trade)
                balance += closed_trade.pnl
            open_positions = remaining_positions

        if len(open_positions) >= cfg.max_open_positions:
            continue

        for intent in planner.plan(replay_snapshot):
            if intent.intent_key in seen_intents:
                continue
            seen_intents.add(intent.intent_key)
            quantity = _compute_quantity(balance, intent)
            if quantity <= 0:
                continue
            open_positions.append(
                _build_open_position(
                    intent,
                    quantity=quantity,
                    entry_time=int(bar.timestamp),
                    entry_price=float(intent.entry_price),
                    snapshot=replay_snapshot,
                )
            )
            if len(open_positions) >= cfg.max_open_positions:
                break

    if execution_bars:
        final_bar = execution_bars[-1]
        final_snapshot = _build_mt5_snapshot_for_timestamp(
            actual_symbol=actual_symbol,
            base_symbol=base_symbol,
            bars_by_tf=bars_by_tf,
            timestamp=int(final_bar.timestamp or 0),
            break_buffer=cfg.break_buffer,
            market_bar=final_bar,
            spread=cfg.spread,
        )
        for position in open_positions:
            forced = _force_close_position(position, final_bar, snapshot=final_snapshot)
            trades.append(forced)
            balance += forced.pnl

    gross_profit = sum(max(item.pnl, 0.0) for item in trades)
    gross_loss = sum(-min(item.pnl, 0.0) for item in trades)
    win_count = sum(1 for item in trades if item.pnl >= 0)
    loss_count = sum(1 for item in trades if item.pnl < 0)
    doctrine_audit = _summarize_doctrine_audit(trades)
    result = ReplayBacktestResult(
        ticker=f"MT5:{actual_symbol}",
        venue="MT5",
        symbol=actual_symbol,
        execution_timeframe=chosen_execution_tf,
        config={**asdict(cfg), "lookback": lookback, "resolved_symbol": actual_symbol, "resolution_source": resolved.source},
        trade_count=len(trades),
        win_count=win_count,
        loss_count=loss_count,
        win_rate_percent=(win_count / len(trades) * 100.0) if trades else 0.0,
        net_pnl=round(balance - cfg.starting_balance, 8),
        net_return_percent=round(((balance - cfg.starting_balance) / cfg.starting_balance) * 100.0, 8) if cfg.starting_balance > 0 else 0.0,
        ending_balance=round(balance, 8),
        max_drawdown_percent=round(_compute_max_drawdown(cfg.starting_balance, trades), 8),
        profit_factor=round((gross_profit / gross_loss), 8) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0),
        trades=[asdict(item) for item in trades],
        doctrine_audit=doctrine_audit,
        output_path=None,
    )
    if output_path:
        target = Path(output_path)
        payload = asdict(result)
        payload["generated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(target, payload, default=str)
        result.output_path = str(target)
    return result


def _build_mt5_snapshot_for_timestamp(
    *,
    actual_symbol: str,
    base_symbol: str,
    bars_by_tf: dict[str, list],
    timestamp: int,
    break_buffer: float,
    market_bar,
    spread: float,
) -> dict[str, Any]:
    engine = SultanStateEngine(timeframes=tuple(tf for tf in TRADING_TIMEFRAMES if tf in bars_by_tf))
    states_by_tf: dict[str, Any] = {}
    for timeframe in [tf for tf in TRADING_TIMEFRAMES if tf in bars_by_tf]:
        bars = [item for item in bars_by_tf[timeframe] if (item.timestamp or 0) <= timestamp]
        if len(bars) < 3:
            continue
        state = bootstrap_latest_breakout(timeframe, bars, break_buffer)
        if not state.is_bootstrapped:
            continue
        states_by_tf[timeframe] = state
        engine.seed_timeframe(state)

    events = engine.rebuild_sequences_from_current_nodes()
    snapshot = engine.snapshot()
    bid = float(market_bar.close or 0.0) - (spread / 2.0)
    ask = float(market_bar.close or 0.0) + (spread / 2.0)
    cheat_code = _derive_h4_m30_m5_cheat_code(
        bars_by_tf=bars_by_tf,
        states_by_tf=states_by_tf,
        timestamp=timestamp,
        break_buffer=break_buffer,
    )
    return {
        "timestamp": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
        "venue": "MT5",
        "analysis_source": "MT5_REPLAY_BACKTEST",
        "symbol": {
            "requested": base_symbol,
            "actual": actual_symbol,
            "resolution_source": "active-terminal",
        },
        "market": {
            "bid": bid,
            "ask": ask,
            "price": float(market_bar.close or 0.0),
            "time": timestamp,
        },
        "timeframes": {
            timeframe: {
                "cmp": node.cmp_direction,
                "role": node.role_to_parent,
                "domain": node.setup_domain,
                "target_tf": node.target_timeframe,
                "cmp_timestamp": node.cmp_timestamp,
                "support": node.support,
                "resistance": node.resistance,
                "breakout_level": node.breakout_level,
                "source_file": f"MT5:{timeframe}",
            }
            for timeframe, node in snapshot.nodes.items()
        },
        "sequences": {
            parent: {
                "direction": sequence.parent_direction,
                "parent_breakout_timestamp": sequence.parent_breakout_timestamp,
                "vr_child": sequence.vr_child_timeframe,
                "vr_active": sequence.vr_active,
                "vr_started_at": sequence.vr_started_at,
                "cf_count": sequence.cf_count,
                "control_state": sequence.control_state,
                "active_controller": sequence.active_controller,
                "last_cf_timeframe": sequence.last_cf_timeframe,
                "last_cf_timestamp": sequence.last_cf_timestamp,
            }
            for parent, sequence in snapshot.sequences.items()
        },
        "setup_focus": snapshot.setup_focus,
        "events": [asdict(event) for event in events],
        "cheat_code": cheat_code,
        "execution": {
            "mode": "PAPER",
            "auto_execute": True,
            "entries_enabled": True,
            "recent_decisions": [],
            "recent_results": [],
        },
        "lifecycle": {
            "recent_actions": [],
            "position_structure": [],
        },
        "health": {
            "bootstrapped": True,
            "connected": True,
        },
        "logs": [],
    }
