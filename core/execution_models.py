from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sultan_engine_v2.core.models import TradeDirection


class Venue(StrEnum):
    MT5 = "MT5"
    BINANCE = "BINANCE"


class ExecutionMode(StrEnum):
    PAPER = "PAPER"
    LIVE = "LIVE"


@dataclass(slots=True)
class TradeIntent:
    intent_key: str
    venue: Venue
    symbol: str
    timeframe: str
    direction: TradeDirection
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_fraction: float
    order_reason: str
    execution_mode: ExecutionMode = ExecutionMode.PAPER
    quantity: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionResult:
    intent_key: str
    venue: Venue
    symbol: str
    success: bool
    execution_mode: ExecutionMode
    message: str
    order_id: str | None = None
    executed_price: float | None = None
    quantity: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)