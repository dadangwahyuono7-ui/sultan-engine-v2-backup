from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from sultan_engine_v2.adapters.tradingview.mapping import (
    TradingViewSymbolMapping,
    map_tradingview_interval,
    map_tradingview_symbol,
    parse_tradingview_csv,
)
from sultan_engine_v2.core.breakout_detector import bootstrap_latest_breakout
from sultan_engine_v2.core.timeframe_relations import TRADING_TIMEFRAMES
from sultan_engine_v2.core.state_engine import SultanStateEngine
from sultan_engine_v2.manager.ai_manager import ManagerReport, SultanAIManager
from sultan_engine_v2.runtime.file_utils import atomic_write_json


@dataclass(slots=True)
class TradingViewReplayResult:
    mapping: TradingViewSymbolMapping
    snapshot: dict
    report: ManagerReport
    output_path: Path


def parse_timeframe_file_args(items: list[str]) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Invalid timeframe file argument: {item}")
        raw_timeframe, raw_path = item.split("=", 1)
        timeframe = map_tradingview_interval(raw_timeframe)
        mapping[timeframe] = Path(raw_path).expanduser().resolve()
    return mapping


def build_tradingview_replay(
    *,
    ticker: str,
    timeframe_files: dict[str, Path],
    break_buffer: float = 0.0,
) -> TradingViewReplayResult:
    symbol_mapping = map_tradingview_symbol(ticker)
    engine = SultanStateEngine(timeframes=tuple(TRADING_TIMEFRAMES))
    price_by_timeframe: dict[str, float] = {}

    for timeframe in _ordered_timeframes(timeframe_files):
        bars = parse_tradingview_csv(timeframe_files[timeframe])
        if len(bars) < 3:
            continue
        state = bootstrap_latest_breakout(timeframe, bars, break_buffer)
        if not state.is_bootstrapped:
            continue
        engine.seed_timeframe(state)
        price_by_timeframe[timeframe] = float(bars[-1].close)

    events = engine.rebuild_sequences_from_current_nodes()
    snapshot = engine.snapshot()
    market_price = _resolve_market_price(price_by_timeframe)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "venue": str(symbol_mapping.preferred_venue),
        "analysis_source": "TRADINGVIEW_REPLAY",
        "symbol": {
            "requested": symbol_mapping.base_symbol,
            "actual": symbol_mapping.mt5_symbol if symbol_mapping.preferred_venue.value == "MT5" else symbol_mapping.binance_symbol,
            "resolution_source": symbol_mapping.source,
            "tradingview_ticker": symbol_mapping.raw_ticker,
            "tradingview_exchange": symbol_mapping.exchange,
        },
        "mapping": asdict(symbol_mapping),
        "market": {
            "bid": market_price,
            "ask": market_price,
            "price": market_price,
            "time": int(datetime.now(timezone.utc).timestamp()),
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
                "source_file": str(timeframe_files[timeframe]) if timeframe in timeframe_files else "",
            }
            for timeframe, node in snapshot.nodes.items()
            if timeframe in timeframe_files
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
            if parent in timeframe_files
        },
        "setup_focus": snapshot.setup_focus,
        "events": [asdict(event) for event in events],
        "execution": {
            "mode": "PAPER",
            "auto_execute": False,
            "entries_enabled": False,
            "recent_decisions": [],
            "recent_results": [],
        },
        "lifecycle": {
            "recent_actions": [],
            "position_structure": [],
        },
        "health": {
            "bootstrapped": bool(price_by_timeframe),
            "connected": True,
        },
        "logs": [f"TradingView replay loaded for {symbol_mapping.raw_ticker}"],
    }
    manager = SultanAIManager()
    report = manager.audit_snapshot(payload)
    output_path = Path(__file__).resolve().parent / "state" / f"tradingview_replay_{symbol_mapping.base_symbol.lower()}.json"
    atomic_write_json(output_path, payload, default=str)
    return TradingViewReplayResult(mapping=symbol_mapping, snapshot=payload, report=report, output_path=output_path)


def _ordered_timeframes(timeframe_files: dict[str, Path]) -> list[str]:
    ordered = [timeframe for timeframe in TRADING_TIMEFRAMES if timeframe in timeframe_files]
    extras = sorted(timeframe for timeframe in timeframe_files if timeframe not in ordered)
    return ordered + extras


def _resolve_market_price(price_by_timeframe: dict[str, float]) -> float:
    for timeframe in reversed(TRADING_TIMEFRAMES):
        if timeframe in price_by_timeframe:
            return price_by_timeframe[timeframe]
    return next(iter(price_by_timeframe.values()), 0.0)
