from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Sequence


class TradeDirection(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class PriceBar:
    open: float
    close: float
    high: float | None = None
    low: float | None = None
    timestamp: int | None = None

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "PriceBar":
        return cls(
            open=float(row["open"]),
            close=float(row["close"]),
            high=float(row["high"]) if row.get("high") is not None else None,
            low=float(row["low"]) if row.get("low") is not None else None,
            timestamp=int(row["timestamp"]) if row.get("timestamp") is not None else None,
        )


@dataclass(slots=True)
class BreakoutState:
    timeframe: str
    active_cmp: TradeDirection | None = None
    support: float = 0.0
    resistance: float = 0.0
    last_breakout_level: float = 0.0
    breakout_timestamp: int | None = None
    changed: bool = False

    @property
    def is_bootstrapped(self) -> bool:
        return self.active_cmp is not None


def ensure_price_bars(rows: Sequence[PriceBar | Mapping[str, Any]]) -> list[PriceBar]:
    bars: list[PriceBar] = []
    for row in rows:
        if isinstance(row, PriceBar):
            bars.append(row)
        else:
            bars.append(PriceBar.from_mapping(row))
    return bars
