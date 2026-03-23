from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sultan_engine_v2.adapters.mt5.execution.guard import CfdGuardConfig, Mt5CfdExecutionGuard
from sultan_engine_v2.core.execution_models import ExecutionMode, TradeIntent, Venue
from sultan_engine_v2.core.models import TradeDirection


class FakeLedger:
    def __init__(self, entries: list[dict] | None = None) -> None:
        self.entries = entries or []


def build_intent(metadata: dict | None = None) -> TradeIntent:
    return TradeIntent(
        intent_key="MT5:XAUUSD:H1:M15:BUY:1998.0",
        venue=Venue.MT5,
        symbol="XAUUSD",
        timeframe="M15",
        direction=TradeDirection.BUY,
        entry_price=2000.01,
        stop_loss=1994.0,
        take_profit=2026.0,
        risk_fraction=0.005,
        order_reason="H1 recovered by CF on M15",
        execution_mode=ExecutionMode.PAPER,
        metadata=metadata or {},
    )


class Mt5CfdGuardTests(unittest.TestCase):
    def test_blocks_when_spread_is_too_wide(self) -> None:
        guard = Mt5CfdExecutionGuard(
            config=CfdGuardConfig(max_spread_points=20.0),
            positions_provider=lambda symbol: [],
            clock=lambda: datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc),
        )
        snapshot = {"market": {"bid": 2000.0, "ask": 2000.5}}

        evaluation = guard.evaluate(snapshot, build_intent(), FakeLedger())

        self.assertFalse(evaluation.allowed)
        self.assertEqual("SPREAD_TOO_WIDE", evaluation.reason)

    def test_blocks_when_open_positions_limit_is_hit(self) -> None:
        guard = Mt5CfdExecutionGuard(
            config=CfdGuardConfig(max_open_positions_per_symbol=1),
            positions_provider=lambda symbol: [object()],
            clock=lambda: datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc),
        )
        snapshot = {"market": {"bid": 2000.0, "ask": 2000.1}}

        evaluation = guard.evaluate(snapshot, build_intent(), FakeLedger())

        self.assertFalse(evaluation.allowed)
        self.assertEqual("MAX_OPEN_POSITIONS", evaluation.reason)

    def test_blocks_when_symbol_is_in_cooldown(self) -> None:
        guard = Mt5CfdExecutionGuard(
            config=CfdGuardConfig(cooldown_minutes=30),
            positions_provider=lambda symbol: [],
            clock=lambda: datetime(2026, 3, 19, 12, 10, tzinfo=timezone.utc),
        )
        snapshot = {"market": {"bid": 2000.0, "ask": 2000.1}}
        ledger = FakeLedger(
            [
                {
                    "symbol": "XAUUSD",
                    "success": True,
                    "recorded_at": "2026-03-19T12:00:00+00:00",
                }
            ]
        )

        evaluation = guard.evaluate(snapshot, build_intent(), ledger)

        self.assertFalse(evaluation.allowed)
        self.assertEqual("COOLDOWN_ACTIVE", evaluation.reason)

    def test_allows_trade_when_guards_are_clean(self) -> None:
        guard = Mt5CfdExecutionGuard(
            config=CfdGuardConfig(max_spread_points=50.0, cooldown_minutes=30),
            positions_provider=lambda symbol: [],
            clock=lambda: datetime(2026, 3, 19, 12, 45, tzinfo=timezone.utc),
        )
        snapshot = {"market": {"bid": 2000.0, "ask": 2000.1}}
        ledger = FakeLedger(
            [
                {
                    "symbol": "XAUUSD",
                    "success": True,
                    "recorded_at": "2026-03-19T12:00:00+00:00",
                }
            ]
        )

        evaluation = guard.evaluate(snapshot, build_intent(), ledger)

        self.assertTrue(evaluation.allowed)
        self.assertEqual("ALLOWED", evaluation.reason)

    def test_blocks_when_cf_trigger_is_not_armed(self) -> None:
        guard = Mt5CfdExecutionGuard(
            config=CfdGuardConfig(max_spread_points=50.0, max_chase_points=50.0),
            positions_provider=lambda symbol: [],
            clock=lambda: datetime(2026, 3, 19, 12, 45, tzinfo=timezone.utc),
        )
        snapshot = {"market": {"bid": 1999.9, "ask": 1999.95}}

        evaluation = guard.evaluate(snapshot, build_intent(metadata={"breakout_level": 2000.0}), FakeLedger())

        self.assertFalse(evaluation.allowed)
        self.assertEqual("TRIGGER_NOT_ARMED", evaluation.reason)

    def test_blocks_when_cf_trigger_is_chased_too_far(self) -> None:
        guard = Mt5CfdExecutionGuard(
            config=CfdGuardConfig(max_spread_points=300.0, max_chase_points=50.0),
            positions_provider=lambda symbol: [],
            clock=lambda: datetime(2026, 3, 19, 12, 45, tzinfo=timezone.utc),
        )
        snapshot = {"market": {"bid": 2001.49, "ask": 2001.5}}

        evaluation = guard.evaluate(snapshot, build_intent(metadata={"breakout_level": 2000.0}), FakeLedger())

        self.assertFalse(evaluation.allowed)
        self.assertEqual("TRIGGER_CHASED_TOO_FAR", evaluation.reason)


if __name__ == "__main__":
    unittest.main()