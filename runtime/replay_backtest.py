from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sultan_engine_v2.adapters.tradingview.mapping import map_tradingview_symbol, parse_tradingview_csv
from sultan_engine_v2.core.breakout_detector import bootstrap_latest_breakout
from sultan_engine_v2.core.execution_models import ExecutionMode, TradeIntent
from sultan_engine_v2.core.models import PriceBar, TradeDirection
from sultan_engine_v2.core.state_engine import SultanStateEngine
from sultan_engine_v2.core.timeframe_relations import TRADING_TIMEFRAMES
from sultan_engine_v2.runtime.execution_engine import SnapshotExecutionPlanner
from sultan_engine_v2.runtime.file_utils import atomic_write_json

DOCTRINE_GUARDIAN_BY_PARENT = {
    "W1": "D1",
    "D1": "H4",
    "H4": "H1",
    "H1": "M15",
    "M15": "M5",
}


@dataclass(slots=True)
class ReplayBacktestConfig:
    starting_balance: float = 10_000.0
    risk_fraction: float = 0.005
    min_rr: float = 1.1
    break_buffer: float = 0.0
    spread: float = 0.0
    max_open_positions: int = 1
    prefer_worst_case_same_bar_exit: bool = True
    enable_dynamic_exit: bool = True
    dynamic_exit_opposite_breakouts: int = 2
    enable_break_even_protect: bool = True
    break_even_trigger_pips: float = 10.0
    pip_size: float = 0.1


@dataclass(slots=True)
class ReplayTrade:
    intent_key: str
    symbol: str
    direction: str
    timeframe: str
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    quantity: float
    pnl: float
    exit_reason: str
    rr_ratio: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ReplayBacktestResult:
    ticker: str
    venue: str
    symbol: str
    execution_timeframe: str
    config: dict[str, Any]
    trade_count: int
    win_count: int
    loss_count: int
    win_rate_percent: float
    net_pnl: float
    net_return_percent: float
    ending_balance: float
    max_drawdown_percent: float
    profit_factor: float
    trades: list[dict[str, Any]]
    doctrine_audit: dict[str, Any] = field(default_factory=dict)
    output_path: str | None = None


@dataclass(slots=True)
class _OpenReplayPosition:
    intent: TradeIntent
    quantity: float
    entry_time: int
    entry_price: float
    doctrine_tags: dict[str, Any] = field(default_factory=dict)
    monitored_timeframe: str = ""
    last_cmp_timestamp: int | None = None
    opposite_breakout_count: int = 0
    break_even_armed: bool = False
    break_even_armed_at: int | None = None


def run_replay_backtest(
    *,
    ticker: str,
    timeframe_files: dict[str, Path],
    execution_timeframe: str | None = None,
    config: ReplayBacktestConfig | None = None,
    output_path: str | Path | None = None,
) -> ReplayBacktestResult:
    cfg = config or ReplayBacktestConfig()
    mapping = map_tradingview_symbol(ticker)
    bars_by_tf = {tf: parse_tradingview_csv(path) for tf, path in timeframe_files.items()}
    if not bars_by_tf:
        raise ValueError("No timeframe files supplied for replay backtest")

    chosen_execution_tf = execution_timeframe or _resolve_execution_timeframe(bars_by_tf)
    execution_bars = bars_by_tf.get(chosen_execution_tf)
    if not execution_bars:
        raise ValueError(f"Execution timeframe not found: {chosen_execution_tf}")

    planner = SnapshotExecutionPlanner(
        default_venue=mapping.preferred_venue,
        execution_mode=ExecutionMode.PAPER,
        risk_fraction=cfg.risk_fraction,
        min_rr=cfg.min_rr,
    )

    balance = float(cfg.starting_balance)
    open_positions: list[_OpenReplayPosition] = []
    seen_intents: set[str] = set()
    trades: list[ReplayTrade] = []

    for bar in execution_bars:
        if bar.timestamp is None:
            continue

        snapshot = _build_snapshot_for_timestamp(
            ticker=ticker,
            timeframe_files=timeframe_files,
            bars_by_tf=bars_by_tf,
            timestamp=int(bar.timestamp),
            break_buffer=cfg.break_buffer,
            market_bar=bar,
            spread=cfg.spread,
        )

        if open_positions:
            remaining_positions: list[_OpenReplayPosition] = []
            for position in open_positions:
                closed_trade = _try_close_position(position, bar, cfg, snapshot=snapshot)
                if closed_trade is None:
                    remaining_positions.append(position)
                    continue
                trades.append(closed_trade)
                balance += closed_trade.pnl
            open_positions = remaining_positions

        if len(open_positions) >= cfg.max_open_positions:
            continue

        for intent in planner.plan(snapshot):
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
                    snapshot=snapshot,
                )
            )
            if len(open_positions) >= cfg.max_open_positions:
                break

    if execution_bars:
        final_bar = execution_bars[-1]
        final_snapshot = _build_snapshot_for_timestamp(
            ticker=ticker,
            timeframe_files=timeframe_files,
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
    max_drawdown_percent = _compute_max_drawdown(cfg.starting_balance, trades)
    doctrine_audit = _summarize_doctrine_audit(trades)
    result = ReplayBacktestResult(
        ticker=ticker,
        venue=str(mapping.preferred_venue),
        symbol=mapping.base_symbol,
        execution_timeframe=chosen_execution_tf,
        config=asdict(cfg),
        trade_count=len(trades),
        win_count=win_count,
        loss_count=loss_count,
        win_rate_percent=(win_count / len(trades) * 100.0) if trades else 0.0,
        net_pnl=round(balance - cfg.starting_balance, 8),
        net_return_percent=round(((balance - cfg.starting_balance) / cfg.starting_balance) * 100.0, 8) if cfg.starting_balance > 0 else 0.0,
        ending_balance=round(balance, 8),
        max_drawdown_percent=round(max_drawdown_percent, 8),
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


def _resolve_execution_timeframe(bars_by_tf: dict[str, list[PriceBar]]) -> str:
    ordered = [tf for tf in reversed(TRADING_TIMEFRAMES) if tf in bars_by_tf]
    if not ordered:
        return next(iter(bars_by_tf))
    return ordered[0]


def _build_snapshot_for_timestamp(
    *,
    ticker: str,
    timeframe_files: dict[str, Path],
    bars_by_tf: dict[str, list[PriceBar]],
    timestamp: int,
    break_buffer: float,
    market_bar: PriceBar,
    spread: float,
) -> dict[str, Any]:
    mapping = map_tradingview_symbol(ticker)
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
    cheat_code = _derive_h4_m30_m5_cheat_code(bars_by_tf=bars_by_tf, states_by_tf=states_by_tf, timestamp=timestamp, break_buffer=break_buffer)
    return {
        "timestamp": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
        "venue": str(mapping.preferred_venue),
        "analysis_source": "TRADINGVIEW_REPLAY_BACKTEST",
        "symbol": {
            "requested": mapping.base_symbol,
            "actual": mapping.mt5_symbol if mapping.preferred_venue.value == "MT5" else mapping.binance_symbol,
            "resolution_source": mapping.source,
            "tradingview_ticker": mapping.raw_ticker,
            "tradingview_exchange": mapping.exchange,
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
                "source_file": str(timeframe_files[timeframe]),
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


def _derive_h4_m30_m5_cheat_code(
    *,
    bars_by_tf: dict[str, list[PriceBar]],
    states_by_tf: dict[str, Any],
    timestamp: int,
    break_buffer: float,
) -> dict[str, Any]:
    h4_state = states_by_tf.get("H4")
    m30_state = states_by_tf.get("M30")
    m5_state = states_by_tf.get("M5")
    if not h4_state or not m30_state or not m5_state:
        return {"status": "UNAVAILABLE", "reason": "missing_timeframes"}
    if h4_state.active_cmp is None or m30_state.active_cmp is None or m5_state.active_cmp is None:
        return {"status": "UNAVAILABLE", "reason": "missing_cmp"}
    if m30_state.active_cmp == h4_state.active_cmp:
        return {"status": "INVALID", "reason": "m30_not_vr_to_h4"}
    if not _strictly_after(m30_state.breakout_timestamp, h4_state.breakout_timestamp):
        return {"status": "INVALID", "reason": "m30_not_after_h4"}

    m5_bars = bars_by_tf.get("M5", [])
    m30_direction = m30_state.active_cmp
    vr_direction = TradeDirection.BUY if m30_state.active_cmp == TradeDirection.SELL else TradeDirection.SELL
    m5_vr_time: int | None = None
    m5_cf_time: int | None = None
    m30_invalidated_time: int | None = None
    for index in range(len(m5_bars)):
        bar = m5_bars[index]
        bar_ts = int(bar.timestamp or 0)
        if bar_ts <= int(m30_state.breakout_timestamp or 0):
            continue
        subset = m5_bars[: index + 1]
        if len(subset) < 3:
            continue
        state = bootstrap_latest_breakout("M5", subset, break_buffer)
        if not state.is_bootstrapped or state.breakout_timestamp is None:
            continue
        if m5_vr_time is None:
            if state.active_cmp == vr_direction and _strictly_after(state.breakout_timestamp, m30_state.breakout_timestamp):
                m5_vr_time = int(state.breakout_timestamp)
            continue

        m30_state_at_m5 = _bootstrap_state_for_timestamp(
            bars_by_tf=bars_by_tf,
            timeframe="M30",
            timestamp=bar_ts,
            break_buffer=break_buffer,
        )
        if not m30_state_at_m5 or m30_state_at_m5.active_cmp != m30_direction:
            m30_invalidated_time = int(m30_state_at_m5.breakout_timestamp or bar_ts) if m30_state_at_m5 else bar_ts
            break

        if state.active_cmp == m30_state.active_cmp and _strictly_after(state.breakout_timestamp, m5_vr_time):
            m5_cf_time = int(state.breakout_timestamp)
            break

    if m5_vr_time is None:
        return {
            "status": "WAIT_M5_VR",
            "reason": "m5_vr_not_found_after_m30",
            "direction": m30_state.active_cmp.value,
            "parent_timeframe": "H4",
            "trigger_timeframe": "M30",
            "execution_timeframe": "M5",
            "h4_confirmed_time": h4_state.breakout_timestamp,
            "m30_breakout_time": m30_state.breakout_timestamp,
        }
    if m5_cf_time is None:
        if m30_invalidated_time is not None:
            return {
                "status": "INVALID",
                "reason": "m30_parent_invalidated_before_m5_cf",
                "direction": m30_state.active_cmp.value,
                "parent_timeframe": "H4",
                "trigger_timeframe": "M30",
                "execution_timeframe": "M5",
                "h4_confirmed_time": h4_state.breakout_timestamp,
                "m30_breakout_time": m30_state.breakout_timestamp,
                "m5_vr_time": m5_vr_time,
                "m30_invalidated_time": m30_invalidated_time,
            }
        return {
            "status": "WAIT_M5_CF",
            "reason": "m5_cf_not_found_after_m5_vr",
            "direction": m30_state.active_cmp.value,
            "parent_timeframe": "H4",
            "trigger_timeframe": "M30",
            "execution_timeframe": "M5",
            "h4_confirmed_time": h4_state.breakout_timestamp,
            "m30_breakout_time": m30_state.breakout_timestamp,
            "m5_vr_time": m5_vr_time,
        }

    m30_state_at_cf = _bootstrap_state_for_timestamp(
        bars_by_tf=bars_by_tf,
        timeframe="M30",
        timestamp=m5_cf_time,
        break_buffer=break_buffer,
    )
    if not m30_state_at_cf or m30_state_at_cf.active_cmp != m30_direction:
        return {
            "status": "INVALID",
            "reason": "m30_parent_invalidated_before_m5_cf",
            "direction": m30_state.active_cmp.value,
            "parent_timeframe": "H4",
            "trigger_timeframe": "M30",
            "execution_timeframe": "M5",
            "h4_confirmed_time": h4_state.breakout_timestamp,
            "m30_breakout_time": m30_state.breakout_timestamp,
            "m5_vr_time": m5_vr_time,
            "m5_cf_time": m5_cf_time,
            "m30_invalidated_time": int(m30_state_at_cf.breakout_timestamp or m5_cf_time),
        }
    if m30_state.active_cmp != m5_state.active_cmp:
        return {
            "status": "INVALID",
            "reason": "m30_flipped_before_m5_cf",
            "direction": m30_state.active_cmp.value,
            "parent_timeframe": "H4",
            "trigger_timeframe": "M30",
            "execution_timeframe": "M5",
            "h4_confirmed_time": h4_state.breakout_timestamp,
            "m30_breakout_time": m30_state.breakout_timestamp,
            "m5_vr_time": m5_vr_time,
            "m5_cf_time": m5_cf_time,
        }
    return {
        "status": "CF_CONFIRMED",
        "reason": "h4_m30_m5_cheat_code_valid",
        "direction": m30_state.active_cmp.value,
        "parent_timeframe": "H4",
        "trigger_timeframe": "M30",
        "execution_timeframe": "M5",
        "target_timeframe": "M30",
        "h4_confirmed_time": int(h4_state.breakout_timestamp or 0),
        "m30_breakout_time": int(m30_state.breakout_timestamp or 0),
        "m5_vr_time": m5_vr_time,
        "m5_cf_time": m5_cf_time,
    }


def _strictly_after(event_timestamp: int | None, reference_timestamp: int | None) -> bool:
    if event_timestamp is None or reference_timestamp is None:
        return False
    return int(event_timestamp) > int(reference_timestamp)


def _bootstrap_state_for_timestamp(
    *,
    bars_by_tf: dict[str, list[PriceBar]],
    timeframe: str,
    timestamp: int,
    break_buffer: float,
):
    bars = bars_by_tf.get(timeframe, [])
    subset = [item for item in bars if (item.timestamp or 0) <= int(timestamp)]
    if len(subset) < 3:
        return None
    state = bootstrap_latest_breakout(timeframe, subset, break_buffer)
    if not getattr(state, "is_bootstrapped", False):
        return None
    return state


def _build_open_position(
    intent: TradeIntent,
    *,
    quantity: float,
    entry_time: int,
    entry_price: float,
    snapshot: dict[str, Any],
) -> _OpenReplayPosition:
    monitored_timeframe = str(intent.metadata.get("invalidation_timeframe") or intent.timeframe)
    node = snapshot.get("timeframes", {}).get(monitored_timeframe, {})
    return _OpenReplayPosition(
        intent=intent,
        quantity=quantity,
        entry_time=entry_time,
        entry_price=entry_price,
        doctrine_tags=_build_doctrine_tags(snapshot, intent),
        monitored_timeframe=monitored_timeframe,
        last_cmp_timestamp=int(node.get("cmp_timestamp") or 0) or None,
        opposite_breakout_count=0,
    )


def _build_doctrine_tags(snapshot: dict[str, Any], intent: TradeIntent) -> dict[str, Any]:
    parent_timeframe = str(intent.metadata.get("parent_timeframe") or "")
    expected_guardian = DOCTRINE_GUARDIAN_BY_PARENT.get(parent_timeframe)
    actual_cf = intent.timeframe
    setup_focus = snapshot.get("setup_focus", {})
    cheat_code = snapshot.get("cheat_code", {})
    return {
        "doctrine_status": setup_focus.get("status"),
        "doctrine_master_timeframe": setup_focus.get("master_timeframe"),
        "doctrine_vr_timeframe": setup_focus.get("vr_timeframe"),
        "doctrine_parent_timeframe": parent_timeframe or None,
        "doctrine_expected_guardian": expected_guardian,
        "doctrine_actual_cf": actual_cf,
        "doctrine_guardian_aligned": (actual_cf == expected_guardian) if expected_guardian else None,
        "doctrine_entry_kind": intent.metadata.get("entry_kind"),
        "doctrine_confirmation_timeframe": intent.metadata.get("confirmation_timeframe"),
        "doctrine_invalidation_timeframe": intent.metadata.get("invalidation_timeframe"),
        "doctrine_hierarchy_path": f"{parent_timeframe}->{actual_cf}" if parent_timeframe else actual_cf,
        "doctrine_cheat_code_status": cheat_code.get("status"),
    }


def _compute_quantity(balance: float, intent: TradeIntent) -> float:
    risk = max(balance, 0.0) * max(float(intent.risk_fraction), 0.0)
    stop_distance = abs(float(intent.entry_price) - float(intent.stop_loss))
    if risk <= 0 or stop_distance <= 0:
        return 0.0
    return risk / stop_distance


def _try_close_position(
    position: _OpenReplayPosition,
    bar: PriceBar,
    config: ReplayBacktestConfig,
    *,
    snapshot: dict[str, Any] | None = None,
) -> ReplayTrade | None:
    if snapshot and config.enable_dynamic_exit:
        dynamic_trade = _maybe_dynamic_exit(position, bar, config, snapshot=snapshot)
        if dynamic_trade is not None:
            return dynamic_trade

    high = float(bar.high if bar.high is not None else max(bar.open, bar.close))
    low = float(bar.low if bar.low is not None else min(bar.open, bar.close))
    direction = position.intent.direction
    stop = float(position.intent.stop_loss)
    target = float(position.intent.take_profit)

    if direction == TradeDirection.BUY:
        hit_stop = low <= stop
        hit_target = high >= target
        if not hit_stop and not hit_target:
            return None
        if hit_stop and hit_target:
            exit_price = stop if config.prefer_worst_case_same_bar_exit else target
            exit_reason = "STOP_LOSS" if config.prefer_worst_case_same_bar_exit else "TAKE_PROFIT"
        elif hit_stop:
            exit_price = stop
            exit_reason = "STOP_LOSS"
        else:
            exit_price = target
            exit_reason = "TAKE_PROFIT"
    else:
        hit_stop = high >= stop
        hit_target = low <= target
        if not hit_stop and not hit_target:
            return None
        if hit_stop and hit_target:
            exit_price = stop if config.prefer_worst_case_same_bar_exit else target
            exit_reason = "STOP_LOSS" if config.prefer_worst_case_same_bar_exit else "TAKE_PROFIT"
        elif hit_stop:
            exit_price = stop
            exit_reason = "STOP_LOSS"
        else:
            exit_price = target
            exit_reason = "TAKE_PROFIT"

    return _build_trade(position, int(bar.timestamp or position.entry_time), exit_price, exit_reason)


def _maybe_dynamic_exit(
    position: _OpenReplayPosition,
    bar: PriceBar,
    config: ReplayBacktestConfig,
    *,
    snapshot: dict[str, Any],
) -> ReplayTrade | None:
    bar_ts = int(bar.timestamp or position.entry_time)
    high = float(bar.high if bar.high is not None else max(bar.open, bar.close))
    low = float(bar.low if bar.low is not None else min(bar.open, bar.close))
    close = float(bar.close)

    # Break-even protector: once price has moved favorably by trigger pips,
    # kill the trade at entry if later bar closes back through entry.
    if config.enable_break_even_protect:
        trigger_price = max(0.0, float(config.break_even_trigger_pips)) * max(0.00001, float(config.pip_size))
        if position.intent.direction == TradeDirection.BUY:
            favorable_move = high - float(position.entry_price)
            if not position.break_even_armed and favorable_move >= trigger_price:
                position.break_even_armed = True
                position.break_even_armed_at = bar_ts
            if (
                position.break_even_armed
                and position.break_even_armed_at is not None
                and bar_ts > int(position.break_even_armed_at)
                and close <= float(position.entry_price)
            ):
                return _build_trade(position, bar_ts, float(position.entry_price), "BREAK_EVEN_PROTECT")
        else:
            favorable_move = float(position.entry_price) - low
            if not position.break_even_armed and favorable_move >= trigger_price:
                position.break_even_armed = True
                position.break_even_armed_at = bar_ts
            if (
                position.break_even_armed
                and position.break_even_armed_at is not None
                and bar_ts > int(position.break_even_armed_at)
                and close >= float(position.entry_price)
            ):
                return _build_trade(position, bar_ts, float(position.entry_price), "BREAK_EVEN_PROTECT")

    node = snapshot.get("timeframes", {}).get(position.monitored_timeframe, {})
    cmp_timestamp = int(node.get("cmp_timestamp") or 0) or None
    cmp_direction = _direction_text(node.get("cmp"))
    if cmp_timestamp is None or cmp_timestamp == position.last_cmp_timestamp:
        return None

    position.last_cmp_timestamp = cmp_timestamp
    if cmp_direction == position.intent.direction.value:
        position.opposite_breakout_count = 0
        return None
    if cmp_direction and cmp_direction != position.intent.direction.value:
        position.opposite_breakout_count += 1
        if position.opposite_breakout_count >= max(1, int(config.dynamic_exit_opposite_breakouts)):
            return _build_trade(
                position,
                int(bar.timestamp or position.entry_time),
                float(bar.close),
                f"DOCTRINE_{int(config.dynamic_exit_opposite_breakouts)}X_OPPOSITE_BO",
            )
    return None


def _direction_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, TradeDirection):
        return value.value
    text = str(value).strip().upper()
    return text or None


def _force_close_position(position: _OpenReplayPosition, bar: PriceBar, *, snapshot: dict[str, Any] | None = None) -> ReplayTrade:
    return _build_trade(position, int(bar.timestamp or position.entry_time), float(bar.close), "FORCED_CLOSE")


def _build_trade(position: _OpenReplayPosition, exit_time: int, exit_price: float, exit_reason: str) -> ReplayTrade:
    direction_sign = 1.0 if position.intent.direction == TradeDirection.BUY else -1.0
    pnl = (exit_price - position.entry_price) * position.quantity * direction_sign
    risk = abs(position.entry_price - float(position.intent.stop_loss)) * position.quantity
    rr_ratio = (pnl / risk) if risk > 0 else 0.0
    metadata = {
        **dict(position.intent.metadata),
        **dict(position.doctrine_tags),
        "doctrine_opposite_breakout_count": position.opposite_breakout_count,
        "doctrine_monitored_timeframe": position.monitored_timeframe,
    }
    return ReplayTrade(
        intent_key=position.intent.intent_key,
        symbol=position.intent.symbol,
        direction=position.intent.direction.value,
        timeframe=position.intent.timeframe,
        entry_time=position.entry_time,
        exit_time=exit_time,
        entry_price=position.entry_price,
        exit_price=exit_price,
        stop_loss=float(position.intent.stop_loss),
        take_profit=float(position.intent.take_profit),
        quantity=position.quantity,
        pnl=round(pnl, 8),
        exit_reason=exit_reason,
        rr_ratio=round(rr_ratio, 8),
        metadata=metadata,
    )


def _compute_max_drawdown(starting_balance: float, trades: list[ReplayTrade]) -> float:
    balance = starting_balance
    peak = starting_balance
    max_drawdown = 0.0
    for trade in trades:
        balance += trade.pnl
        peak = max(peak, balance)
        if peak > 0:
            max_drawdown = max(max_drawdown, ((peak - balance) / peak) * 100.0)
    return max_drawdown


def _summarize_doctrine_audit(trades: list[ReplayTrade]) -> dict[str, Any]:
    exit_reasons = Counter(item.exit_reason for item in trades)
    hierarchy_paths = Counter(str(item.metadata.get("doctrine_hierarchy_path")) for item in trades)
    alignment = Counter(str(item.metadata.get("doctrine_guardian_aligned")) for item in trades)
    entry_kinds = Counter(str(item.metadata.get("doctrine_entry_kind")) for item in trades)
    monitored = Counter(str(item.metadata.get("doctrine_monitored_timeframe")) for item in trades)
    return {
        "trade_count": len(trades),
        "exit_reason_counts": dict(exit_reasons),
        "hierarchy_path_counts": dict(hierarchy_paths),
        "guardian_alignment_counts": dict(alignment),
        "entry_kind_counts": dict(entry_kinds),
        "monitored_timeframe_counts": dict(monitored),
    }
