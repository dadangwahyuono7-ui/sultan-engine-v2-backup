from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sultan_engine_v2.adapters.mt5.session import client as mt5_client
from sultan_engine_v2.core.models import BreakoutState, PriceBar, TradeDirection
from sultan_engine_v2.core.state_engine import StateEvent
from sultan_engine_v2.runtime import mt5_live_runtime as runtime_module
from sultan_engine_v2.runtime.mt5_live_runtime import Mt5LiveRuntime


class Mt5SessionCloseOnlyTests(unittest.TestCase):
    def test_fetch_closed_bars_discards_current_forming_candle(self) -> None:
        rates = [
            {"open": 100.0, "close": 101.0, "high": 102.0, "low": 99.0, "time": 100},
            {"open": 101.0, "close": 102.0, "high": 103.0, "low": 100.0, "time": 200},
            {"open": 102.0, "close": 103.0, "high": 104.0, "low": 101.0, "time": 300},
            {"open": 103.0, "close": 104.0, "high": 105.0, "low": 102.0, "time": 400},
        ]
        fake_mt5 = SimpleNamespace(copy_rates_from_pos=lambda symbol, timeframe, start, count: rates)

        with patch.object(mt5_client, "mt5", fake_mt5), patch.dict(mt5_client.TIMEFRAME_MAP, {"M15": 15}, clear=False):
            session = mt5_client.ActiveMt5Session()
            bars = session.fetch_closed_bars("XAUUSD", "M15", count=4)

        self.assertEqual(3, len(bars))
        self.assertEqual([100, 200, 300], [bar.timestamp for bar in bars])
        self.assertEqual([101.0, 102.0, 103.0], [bar.close for bar in bars])


class Mt5RuntimeCloseOnlyTests(unittest.TestCase):
    def _build_runtime(self, bars: list[PriceBar], last_closed_timestamp: int) -> Mt5LiveRuntime:
        runtime = Mt5LiveRuntime(timeframes=("M15",), auto_execute=False)
        runtime.session = SimpleNamespace(
            refresh=lambda: SimpleNamespace(connected=True),
            fetch_tick=lambda symbol: runtime_module.Mt5Tick(bid=100.0, ask=100.2, last=100.1, timestamp=last_closed_timestamp),
            fetch_closed_bars=lambda symbol, timeframe, count: bars,
        )
        runtime.actual_symbol = "XAUUSD"
        runtime.bootstrapped = True
        runtime.breakout_states = {
            "M15": BreakoutState(
                timeframe="M15",
                active_cmp=TradeDirection.BUY,
                support=99.0,
                resistance=103.0,
                last_breakout_level=103.0,
                breakout_timestamp=last_closed_timestamp,
            )
        }
        runtime.last_closed_timestamps = {"M15": last_closed_timestamp}
        return runtime

    def test_poll_waits_for_new_closed_candle_before_reprocessing(self) -> None:
        bars = [
            PriceBar(open=100.0, close=101.0, high=102.0, low=99.0, timestamp=100),
            PriceBar(open=101.0, close=102.0, high=103.0, low=100.0, timestamp=200),
            PriceBar(open=102.0, close=103.0, high=104.0, low=101.0, timestamp=300),
        ]
        runtime = self._build_runtime(bars, last_closed_timestamp=300)

        with patch.object(runtime_module, "update_breakout_state", side_effect=AssertionError("should not reprocess without a new closed candle")), patch.object(
            runtime,
            "_finalize_cycle",
            side_effect=lambda execute_enabled: {"execute_enabled": execute_enabled},
        ) as finalize_cycle:
            result = runtime.poll()

        finalize_cycle.assert_called_once_with(execute_enabled=False)
        self.assertEqual({"execute_enabled": False}, result)
        self.assertEqual(300, runtime.last_closed_timestamps["M15"])

    def test_poll_reprocesses_after_new_closed_candle_arrives(self) -> None:
        bars = [
            PriceBar(open=100.0, close=101.0, high=102.0, low=99.0, timestamp=100),
            PriceBar(open=101.0, close=102.0, high=103.0, low=100.0, timestamp=200),
            PriceBar(open=102.0, close=103.0, high=104.0, low=101.0, timestamp=400),
        ]
        updated_state = BreakoutState(
            timeframe="M15",
            active_cmp=TradeDirection.SELL,
            support=100.0,
            resistance=104.0,
            last_breakout_level=100.0,
            breakout_timestamp=400,
            changed=True,
        )
        runtime = self._build_runtime(bars, last_closed_timestamp=300)
        previous_state = runtime.breakout_states["M15"]

        with patch.object(runtime_module, "update_breakout_state", return_value=updated_state) as update_breakout, patch.object(
            runtime.engine,
            "apply_breakout",
            return_value=[],
        ) as apply_breakout, patch.object(
            runtime,
            "_finalize_cycle",
            side_effect=lambda execute_enabled: {"execute_enabled": execute_enabled},
        ) as finalize_cycle:
            result = runtime.poll()

        update_breakout.assert_called_once_with(previous_state, bars, runtime.break_buffer)
        apply_breakout.assert_called_once_with(updated_state)
        finalize_cycle.assert_called_once_with(execute_enabled=True)
        self.assertEqual({"execute_enabled": True}, result)
        self.assertEqual(400, runtime.last_closed_timestamps["M15"])

    def test_record_events_sends_structure_alert_for_vr_and_cf(self) -> None:
        runtime = Mt5LiveRuntime(timeframes=("H1",), auto_execute=False)
        captured: list[tuple[str, str | None]] = []
        runtime.actual_symbol = "XAUUSD"
        runtime.telegram_reporter = SimpleNamespace(
            send_state_event=lambda event, symbol=None: (captured.append((event.kind, symbol)) or True)
        )

        runtime._record_events(
            [
                StateEvent(kind="CMP_SEEDED", timeframe="H1", message="seeded"),
                StateEvent(kind="VR_ACTIVE", timeframe="H1", parent_timeframe="H4", message="vr", direction=TradeDirection.BUY, event_timestamp=123),
                StateEvent(kind="CF_CONFIRMED", timeframe="M15", parent_timeframe="H1", message="cf", direction=TradeDirection.SELL, event_timestamp=124),
            ]
        )

        self.assertEqual([("VR_ACTIVE", "XAUUSD"), ("CF_CONFIRMED", "XAUUSD")], captured)


if __name__ == "__main__":
    unittest.main()