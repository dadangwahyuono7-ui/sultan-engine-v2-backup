from __future__ import annotations

from dataclasses import dataclass

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - optional runtime dependency
    mt5 = None

from sultan_engine_v2.adapters.mt5.discovery.active_terminal import (
    Mt5TerminalSnapshot,
    connect_active_terminal,
    read_active_terminal_snapshot,
    shutdown_terminal,
)
from sultan_engine_v2.adapters.mt5.symbols.resolver import ResolvedSymbol, resolve_symbol
from sultan_engine_v2.core.models import PriceBar


TIMEFRAME_MAP = {
    "MN1": getattr(mt5, "TIMEFRAME_MN1", None),
    "W1": getattr(mt5, "TIMEFRAME_W1", None),
    "D1": getattr(mt5, "TIMEFRAME_D1", None),
    "H4": getattr(mt5, "TIMEFRAME_H4", None),
    "H1": getattr(mt5, "TIMEFRAME_H1", None),
    "M30": getattr(mt5, "TIMEFRAME_M30", None),
    "M15": getattr(mt5, "TIMEFRAME_M15", None),
    "M5": getattr(mt5, "TIMEFRAME_M5", None),
    "M1": getattr(mt5, "TIMEFRAME_M1", None),
}


@dataclass(slots=True)
class Mt5Tick:
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    timestamp: int = 0


class ActiveMt5Session:
    def __init__(self) -> None:
        self.snapshot: Mt5TerminalSnapshot = Mt5TerminalSnapshot(connected=False, error="Not connected")

    @property
    def connected(self) -> bool:
        return self.snapshot.connected

    def connect(self) -> Mt5TerminalSnapshot:
        self.snapshot = connect_active_terminal()
        return self.snapshot

    def refresh(self) -> Mt5TerminalSnapshot:
        if not self.snapshot.connected:
            return self.connect()

        refreshed = read_active_terminal_snapshot(
            terminal_path=self.snapshot.terminal_path,
            path_source=self.snapshot.path_source,
        )
        self.snapshot = refreshed
        return refreshed

    def resolve_symbol(self, base_symbol: str) -> ResolvedSymbol:
        return resolve_symbol(base_symbol)

    def fetch_closed_bars(self, actual_symbol: str, timeframe: str, count: int = 250) -> list[PriceBar]:
        if mt5 is None:
            return []

        tf_id = TIMEFRAME_MAP.get(timeframe)
        if tf_id is None:
            return []

        rates = mt5.copy_rates_from_pos(actual_symbol, tf_id, 0, count)
        if rates is None or len(rates) < 3:
            return []

        closed_rates = rates[:-1]
        bars: list[PriceBar] = []
        for row in closed_rates:
            bars.append(
                PriceBar(
                    open=float(row["open"]),
                    close=float(row["close"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    timestamp=int(row["time"]),
                )
            )
        return bars

    def fetch_tick(self, actual_symbol: str) -> Mt5Tick:
        if mt5 is None:
            return Mt5Tick()

        tick = mt5.symbol_info_tick(actual_symbol)
        if tick is None:
            return Mt5Tick()

        return Mt5Tick(
            bid=float(getattr(tick, "bid", 0.0) or 0.0),
            ask=float(getattr(tick, "ask", 0.0) or 0.0),
            last=float(getattr(tick, "last", 0.0) or 0.0),
            timestamp=int(getattr(tick, "time", 0) or 0),
        )

    def close(self) -> None:
        shutdown_terminal()
