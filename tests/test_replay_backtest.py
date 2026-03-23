from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sultan_engine_v2.core.execution_models import ExecutionMode, TradeIntent, Venue
from sultan_engine_v2.core.models import PriceBar, TradeDirection
from sultan_engine_v2.runtime.replay_backtest import (
    ReplayBacktestConfig,
    _OpenReplayPosition,
    _build_open_position,
    _build_trade,
    _compute_max_drawdown,
    _compute_quantity,
    _derive_h4_m30_m5_cheat_code,
    _force_close_position,
    _summarize_doctrine_audit,
    _try_close_position,
)


def build_intent(direction: TradeDirection = TradeDirection.BUY) -> TradeIntent:
    return TradeIntent(
        intent_key=f"MT5:XAUUSD:H1:M5:{direction}:100.0",
        venue=Venue.MT5,
        symbol="XAUUSD",
        timeframe="M5",
        direction=direction,
        entry_price=100.0,
        stop_loss=95.0 if direction == TradeDirection.BUY else 105.0,
        take_profit=110.0 if direction == TradeDirection.BUY else 90.0,
        risk_fraction=0.01,
        order_reason="replay test",
        execution_mode=ExecutionMode.PAPER,
        metadata={
            "parent_timeframe": "H1",
            "entry_kind": "HIGH_RISK_CF",
            "confirmation_timeframe": "M15",
            "invalidation_timeframe": "M15",
            "rr_ratio": 2.0,
        },
    )


def build_snapshot(*, cmp: str = "BUY", cmp_timestamp: int = 1) -> dict:
    return {
        "setup_focus": {"status": "CF_CONFIRMED", "master_timeframe": "H1", "vr_timeframe": "M15"},
        "timeframes": {
            "M5": {
                "cmp": cmp,
                "cmp_timestamp": cmp_timestamp,
            }
        },
    }


class ReplayBacktestTests(unittest.TestCase):
    def test_compute_quantity_uses_risk_fraction_and_stop_distance(self) -> None:
        quantity = _compute_quantity(10_000.0, build_intent())
        self.assertEqual(20.0, quantity)

    def test_try_close_position_prefers_worst_case_when_tp_and_sl_hit_same_bar(self) -> None:
        position = _OpenReplayPosition(
            intent=build_intent(),
            quantity=10.0,
            entry_time=1,
            entry_price=100.0,
        )
        bar = PriceBar(open=100.0, high=111.0, low=94.0, close=103.0, timestamp=2)
        trade = _try_close_position(position, bar, ReplayBacktestConfig())
        self.assertIsNotNone(trade)
        self.assertEqual("STOP_LOSS", trade.exit_reason)
        self.assertLess(trade.pnl, 0.0)

    def test_try_close_position_can_take_profit_when_config_allows_best_case(self) -> None:
        position = _OpenReplayPosition(
            intent=build_intent(),
            quantity=10.0,
            entry_time=1,
            entry_price=100.0,
        )
        bar = PriceBar(open=100.0, high=111.0, low=94.0, close=103.0, timestamp=2)
        trade = _try_close_position(
            position,
            bar,
            ReplayBacktestConfig(prefer_worst_case_same_bar_exit=False),
        )
        self.assertIsNotNone(trade)
        self.assertEqual("TAKE_PROFIT", trade.exit_reason)
        self.assertGreater(trade.pnl, 0.0)

    def test_dynamic_exit_triggers_after_two_opposite_breakouts(self) -> None:
        position = _build_open_position(
            build_intent(direction=TradeDirection.BUY),
            quantity=10.0,
            entry_time=1,
            entry_price=100.0,
            snapshot=build_snapshot(cmp="BUY", cmp_timestamp=1),
        )
        bar = PriceBar(open=100.0, high=101.0, low=99.0, close=100.5, timestamp=2)

        first = _try_close_position(position, bar, ReplayBacktestConfig(), snapshot=build_snapshot(cmp="SELL", cmp_timestamp=2))
        self.assertIsNone(first)
        second = _try_close_position(position, bar, ReplayBacktestConfig(), snapshot=build_snapshot(cmp="SELL", cmp_timestamp=3))
        self.assertIsNotNone(second)
        self.assertEqual("DOCTRINE_2X_OPPOSITE_BO", second.exit_reason)

    def test_build_open_position_tags_doctrine_metadata(self) -> None:
        position = _build_open_position(
            build_intent(),
            quantity=10.0,
            entry_time=1,
            entry_price=100.0,
            snapshot=build_snapshot(),
        )
        self.assertEqual("H1", position.doctrine_tags["doctrine_parent_timeframe"])
        self.assertEqual("M15", position.doctrine_tags["doctrine_expected_guardian"])
        self.assertFalse(position.doctrine_tags["doctrine_guardian_aligned"])

    def test_force_close_position_uses_last_close(self) -> None:
        position = _build_open_position(
            build_intent(direction=TradeDirection.SELL),
            quantity=5.0,
            entry_time=1,
            entry_price=100.0,
            snapshot=build_snapshot(cmp="SELL"),
        )
        trade = _force_close_position(position, PriceBar(open=100.0, high=102.0, low=96.0, close=97.0, timestamp=3))
        self.assertEqual("FORCED_CLOSE", trade.exit_reason)
        self.assertGreater(trade.pnl, 0.0)
        self.assertIn("doctrine_parent_timeframe", trade.metadata)

    def test_compute_max_drawdown_tracks_equity_curve(self) -> None:
        position = _build_open_position(build_intent(), quantity=10.0, entry_time=1, entry_price=100.0, snapshot=build_snapshot())
        trade1 = _build_trade(position, 2, 95.0, "STOP_LOSS")
        trade2 = _build_trade(position, 3, 110.0, "TAKE_PROFIT")
        drawdown = _compute_max_drawdown(10_000.0, [trade1, trade2])
        self.assertGreater(drawdown, 0.0)

    def test_summarize_doctrine_audit_counts_paths_and_exit_reasons(self) -> None:
        position = _build_open_position(build_intent(), quantity=10.0, entry_time=1, entry_price=100.0, snapshot=build_snapshot())
        trades = [
            _build_trade(position, 2, 95.0, "STOP_LOSS"),
            _build_trade(position, 3, 101.0, "DOCTRINE_2X_OPPOSITE_BO"),
        ]
        summary = _summarize_doctrine_audit(trades)
        self.assertEqual(2, summary["trade_count"])
        self.assertEqual(1, summary["exit_reason_counts"]["STOP_LOSS"])
        self.assertEqual(2, summary["hierarchy_path_counts"]["H1->M5"])

    def test_cheat_code_chain_confirmed_when_parent_survives_until_cf(self) -> None:
        bars_by_tf = {
            "M5": [
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=210),
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=220),
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=230),
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=240),
            ],
            "M30": [
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=170),
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=180),
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=200),
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=230),
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=240),
            ],
        }
        states_by_tf = {
            "H4": SimpleNamespace(active_cmp=TradeDirection.BUY, breakout_timestamp=100),
            "M30": SimpleNamespace(active_cmp=TradeDirection.SELL, breakout_timestamp=200),
            "M5": SimpleNamespace(active_cmp=TradeDirection.SELL, breakout_timestamp=240),
        }

        def fake_bootstrap(timeframe, bars, break_buffer):
            ts = int(bars[-1].timestamp or 0)
            if timeframe == "M5":
                if ts < 240:
                    return SimpleNamespace(is_bootstrapped=True, active_cmp=TradeDirection.BUY, breakout_timestamp=230)
                return SimpleNamespace(is_bootstrapped=True, active_cmp=TradeDirection.SELL, breakout_timestamp=240)
            if timeframe == "M30":
                return SimpleNamespace(is_bootstrapped=True, active_cmp=TradeDirection.SELL, breakout_timestamp=200)
            return SimpleNamespace(is_bootstrapped=False, active_cmp=None, breakout_timestamp=None)

        with patch("sultan_engine_v2.runtime.replay_backtest.bootstrap_latest_breakout", side_effect=fake_bootstrap):
            cheat = _derive_h4_m30_m5_cheat_code(
                bars_by_tf=bars_by_tf,
                states_by_tf=states_by_tf,
                timestamp=240,
                break_buffer=0.0,
            )

        self.assertEqual("CF_CONFIRMED", cheat["status"])
        self.assertEqual(230, cheat["m5_vr_time"])
        self.assertEqual(240, cheat["m5_cf_time"])

    def test_cheat_code_invalid_when_m30_parent_dies_before_cf(self) -> None:
        bars_by_tf = {
            "M5": [
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=210),
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=220),
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=230),
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=240),
            ],
            "M30": [
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=170),
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=180),
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=200),
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=230),
                PriceBar(open=1.0, high=1.0, low=1.0, close=1.0, timestamp=240),
            ],
        }
        states_by_tf = {
            "H4": SimpleNamespace(active_cmp=TradeDirection.BUY, breakout_timestamp=100),
            "M30": SimpleNamespace(active_cmp=TradeDirection.SELL, breakout_timestamp=200),
            "M5": SimpleNamespace(active_cmp=TradeDirection.SELL, breakout_timestamp=240),
        }

        def fake_bootstrap(timeframe, bars, break_buffer):
            ts = int(bars[-1].timestamp or 0)
            if timeframe == "M5":
                if ts < 240:
                    return SimpleNamespace(is_bootstrapped=True, active_cmp=TradeDirection.BUY, breakout_timestamp=230)
                return SimpleNamespace(is_bootstrapped=True, active_cmp=TradeDirection.SELL, breakout_timestamp=240)
            if timeframe == "M30":
                if ts >= 240:
                    return SimpleNamespace(is_bootstrapped=True, active_cmp=TradeDirection.BUY, breakout_timestamp=240)
                return SimpleNamespace(is_bootstrapped=True, active_cmp=TradeDirection.SELL, breakout_timestamp=200)
            return SimpleNamespace(is_bootstrapped=False, active_cmp=None, breakout_timestamp=None)

        with patch("sultan_engine_v2.runtime.replay_backtest.bootstrap_latest_breakout", side_effect=fake_bootstrap):
            cheat = _derive_h4_m30_m5_cheat_code(
                bars_by_tf=bars_by_tf,
                states_by_tf=states_by_tf,
                timestamp=240,
                break_buffer=0.0,
            )

        self.assertEqual("INVALID", cheat["status"])
        self.assertEqual("m30_parent_invalidated_before_m5_cf", cheat["reason"])


if __name__ == "__main__":
    unittest.main()
