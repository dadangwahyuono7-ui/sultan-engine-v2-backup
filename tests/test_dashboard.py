from __future__ import annotations

import unittest

from sultan_engine_v2.runtime.dashboard import build_delta_pressure_summary


class DashboardFootprintTests(unittest.TestCase):
    def test_build_delta_pressure_summary_detects_buy_pressure(self) -> None:
        recent_ticks = [
            {"mid": 100.0},
            {"mid": 100.4},
            {"mid": 100.6},
            {"mid": 100.5},
            {"mid": 100.9},
        ]

        summary = build_delta_pressure_summary(recent_ticks)

        self.assertEqual("BUY_PRESSURE", summary["pressure"])
        self.assertEqual(3, summary["buy_moves"])
        self.assertEqual(1, summary["sell_moves"])
        self.assertAlmostEqual(1.0, float(summary["delta_plus"]), places=6)
        self.assertAlmostEqual(0.1, float(summary["delta_minus"]), places=6)
        self.assertAlmostEqual(0.9, float(summary["net_delta"]), places=6)

    def test_build_delta_pressure_summary_detects_sell_pressure(self) -> None:
        recent_ticks = [
            {"mid": 100.0},
            {"mid": 99.7},
            {"mid": 99.4},
            {"mid": 99.5},
            {"mid": 99.1},
        ]

        summary = build_delta_pressure_summary(recent_ticks)

        self.assertEqual("SELL_PRESSURE", summary["pressure"])
        self.assertEqual(1, summary["buy_moves"])
        self.assertEqual(3, summary["sell_moves"])
        self.assertAlmostEqual(0.1, float(summary["delta_plus"]), places=6)
        self.assertAlmostEqual(1.0, float(summary["delta_minus"]), places=6)
        self.assertAlmostEqual(-0.9, float(summary["net_delta"]), places=6)


if __name__ == "__main__":
    unittest.main()