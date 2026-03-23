import unittest

from sultan_engine_v2.core.breakout_detector import (
    bootstrap_latest_breakout,
    get_minor_snr_flip,
    update_breakout_state,
)
from sultan_engine_v2.core.models import BreakoutState, PriceBar, TradeDirection


class BreakoutDetectorTests(unittest.TestCase):
    def test_minor_support_flip_detected(self) -> None:
        bars = [PriceBar(open=10, close=8), PriceBar(open=7, close=11)]
        support, resistance = get_minor_snr_flip(bars)
        self.assertEqual(support, 8)
        self.assertEqual(resistance, 0.0)

    def test_bootstrap_uses_rightmost_valid_breakout(self) -> None:
        bars = [
            PriceBar(open=9, close=9.5, timestamp=100),
            PriceBar(open=10, close=11, timestamp=200),
            PriceBar(open=12, close=10, timestamp=300),
            PriceBar(open=10, close=9, timestamp=400),
            PriceBar(open=8, close=12, timestamp=500),
            PriceBar(open=11, close=12.5, timestamp=600),
        ]
        state = bootstrap_latest_breakout("M15", bars)
        self.assertEqual(state.active_cmp, TradeDirection.BUY)
        self.assertEqual(state.last_breakout_level, 11)
        self.assertEqual(state.breakout_timestamp, 600)

    def test_update_flips_cmp_when_close_breaks_support(self) -> None:
        previous = BreakoutState(
            timeframe="M15",
            active_cmp=TradeDirection.BUY,
            support=9.0,
            resistance=11.0,
            last_breakout_level=11.0,
            breakout_timestamp=400,
        )
        bars = [
            PriceBar(open=10, close=11, timestamp=100),
            PriceBar(open=12, close=10, timestamp=200),
            PriceBar(open=10, close=9, timestamp=300),
            PriceBar(open=8, close=12, timestamp=400),
            PriceBar(open=12, close=8, timestamp=500),
        ]
        updated = update_breakout_state(previous, bars)
        self.assertEqual(updated.active_cmp, TradeDirection.SELL)
        self.assertTrue(updated.changed)
        self.assertEqual(updated.breakout_timestamp, 500)


if __name__ == "__main__":
    unittest.main()