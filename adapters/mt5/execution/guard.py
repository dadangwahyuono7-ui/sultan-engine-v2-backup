from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - optional runtime dependency
    mt5 = None

from sultan_engine_v2.core.execution_models import TradeIntent
from sultan_engine_v2.runtime.env_loader import load_runtime_env


@dataclass(slots=True)
class CfdGuardConfig:
    max_spread_points: float = 150.0
    max_chase_points: float = 80.0
    max_open_positions_per_symbol: int = 1
    cooldown_minutes: int = 30
    allowed_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    blocked_utc_hours: tuple[int, ...] = ()


@dataclass(slots=True)
class GuardEvaluation:
    allowed: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


def load_cfd_guard_config_from_env() -> CfdGuardConfig:
    load_runtime_env(__file__)
    return CfdGuardConfig(
        max_spread_points=float(os.getenv("SULTAN_CFD_MAX_SPREAD_POINTS", "150")),
        max_chase_points=float(os.getenv("SULTAN_CFD_MAX_CHASE_POINTS", "80")),
        max_open_positions_per_symbol=int(os.getenv("SULTAN_CFD_MAX_OPEN_POSITIONS", "1")),
        cooldown_minutes=int(os.getenv("SULTAN_CFD_COOLDOWN_MINUTES", "30")),
        allowed_weekdays=_parse_int_tuple(os.getenv("SULTAN_CFD_ALLOWED_WEEKDAYS"), default=(0, 1, 2, 3, 4)),
        blocked_utc_hours=_parse_int_tuple(os.getenv("SULTAN_CFD_BLOCKED_UTC_HOURS"), default=()),
    )


def _parse_int_tuple(raw_value: str | None, default: tuple[int, ...]) -> tuple[int, ...]:
    if not raw_value:
        return default

    values = []
    for item in raw_value.split(","):
        item = item.strip()
        if not item:
            continue
        values.append(int(item))
    return tuple(values)


class Mt5CfdExecutionGuard:
    def __init__(
        self,
        config: CfdGuardConfig | None = None,
        positions_provider: Callable[[str], list[Any]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config or load_cfd_guard_config_from_env()
        self.positions_provider = positions_provider or self._default_positions_provider
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def evaluate(self, snapshot: dict, intent: TradeIntent, ledger: Any | None = None) -> GuardEvaluation:
        now = self.clock()
        if now.weekday() not in self.config.allowed_weekdays:
            return GuardEvaluation(False, "WEEKDAY_BLOCKED", {"weekday": now.weekday()})

        if now.hour in self.config.blocked_utc_hours:
            return GuardEvaluation(False, "BLOCKED_UTC_HOUR", {"hour": now.hour})

        bid = float(snapshot.get("market", {}).get("bid") or 0.0)
        ask = float(snapshot.get("market", {}).get("ask") or 0.0)
        if bid <= 0 or ask <= 0:
            return GuardEvaluation(False, "MARKET_PRICE_EMPTY", {"bid": bid, "ask": ask})

        point = self._resolve_point(intent.symbol, intent.entry_price)
        spread_points = abs(ask - bid) / point if point else 0.0
        if spread_points > self.config.max_spread_points:
            return GuardEvaluation(
                False,
                "SPREAD_TOO_WIDE",
                {
                    "spread_points": round(spread_points, 4),
                    "max_spread_points": self.config.max_spread_points,
                },
            )

        trigger_evaluation = self._evaluate_cf_breakout_trigger(snapshot, intent, point, bid, ask)
        if trigger_evaluation is not None:
            return trigger_evaluation

        open_positions = self.positions_provider(intent.symbol)
        open_count = len(open_positions)
        if open_count >= self.config.max_open_positions_per_symbol:
            return GuardEvaluation(
                False,
                "MAX_OPEN_POSITIONS",
                {
                    "open_positions": open_count,
                    "max_open_positions_per_symbol": self.config.max_open_positions_per_symbol,
                },
            )

        cooldown_remaining = self._cooldown_remaining_minutes(now, intent.symbol, ledger)
        if cooldown_remaining > 0:
            return GuardEvaluation(
                False,
                "COOLDOWN_ACTIVE",
                {
                    "cooldown_minutes": self.config.cooldown_minutes,
                    "remaining_minutes": cooldown_remaining,
                },
            )

        return GuardEvaluation(
            True,
            "ALLOWED",
            {
                "spread_points": round(spread_points, 4),
                "open_positions": open_count,
            },
        )

    def _evaluate_cf_breakout_trigger(
        self,
        snapshot: dict,
        intent: TradeIntent,
        point: float,
        bid: float,
        ask: float,
    ) -> GuardEvaluation | None:
        breakout_level = float(intent.metadata.get("breakout_level") or 0.0)
        if breakout_level <= 0 or point <= 0:
            return None

        trigger_price = ask if intent.direction.value == "BUY" else bid
        chase_limit = self.config.max_chase_points * point
        if intent.direction.value == "BUY":
            if trigger_price < breakout_level:
                return GuardEvaluation(
                    False,
                    "TRIGGER_NOT_ARMED",
                    {
                        "trigger_price": trigger_price,
                        "breakout_level": breakout_level,
                    },
                )
            if trigger_price > breakout_level + chase_limit:
                return GuardEvaluation(
                    False,
                    "TRIGGER_CHASED_TOO_FAR",
                    {
                        "trigger_price": trigger_price,
                        "breakout_level": breakout_level,
                        "max_chase_points": self.config.max_chase_points,
                    },
                )
            return None

        if trigger_price > breakout_level:
            return GuardEvaluation(
                False,
                "TRIGGER_NOT_ARMED",
                {
                    "trigger_price": trigger_price,
                    "breakout_level": breakout_level,
                },
            )
        if trigger_price < breakout_level - chase_limit:
            return GuardEvaluation(
                False,
                "TRIGGER_CHASED_TOO_FAR",
                {
                    "trigger_price": trigger_price,
                    "breakout_level": breakout_level,
                    "max_chase_points": self.config.max_chase_points,
                },
            )
        return None

    def _cooldown_remaining_minutes(self, now: datetime, symbol: str, ledger: Any | None) -> int:
        if ledger is None or self.config.cooldown_minutes <= 0:
            return 0

        last_success: datetime | None = None
        for entry in getattr(ledger, "entries", []):
            if not entry.get("success"):
                continue
            if entry.get("symbol") != symbol:
                continue

            recorded_at = entry.get("recorded_at")
            if not recorded_at:
                continue

            try:
                parsed = datetime.fromisoformat(recorded_at)
            except ValueError:
                continue

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            if last_success is None or parsed > last_success:
                last_success = parsed

        if last_success is None:
            return 0

        elapsed = now - last_success
        if elapsed >= timedelta(minutes=self.config.cooldown_minutes):
            return 0

        remaining = timedelta(minutes=self.config.cooldown_minutes) - elapsed
        return max(1, int(remaining.total_seconds() // 60))

    @staticmethod
    def _default_positions_provider(symbol: str) -> list[Any]:
        if mt5 is None:
            return []

        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            return []
        return list(positions)

    @staticmethod
    def _resolve_point(symbol: str, price: float) -> float:
        if mt5 is not None:
            info = mt5.symbol_info(symbol)
            point = float(getattr(info, "point", 0.0) or 0.0) if info is not None else 0.0
            if point > 0:
                return point
        return Mt5CfdExecutionGuard._infer_point(price)

    @staticmethod
    def _infer_point(price: float) -> float:
        if price <= 0:
            return 0.01

        text = f"{price:.10f}".rstrip("0")
        if "." not in text:
            return 1.0

        decimals = len(text.split(".", 1)[1])
        return 10 ** (-decimals)
