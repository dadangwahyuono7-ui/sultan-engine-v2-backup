from __future__ import annotations

import unittest

from sultan_engine_v2.runtime.fleet_dashboard import build_sequence_rows, build_state_rows


class FleetDashboardTests(unittest.TestCase):
    def test_build_state_rows_uses_timeframe_order(self) -> None:
        snapshot = {
            "timeframes": {
                "D1": {"cmp": "SELL", "role": "CMP", "domain": "W1", "cmp_timestamp": 1773878400, "breakout_level": 5136.66},
                "H4": {"cmp": "BUY", "role": "VR", "domain": "D1", "cmp_timestamp": 1774008000, "breakout_level": 70633.5},
            }
        }

        rows = build_state_rows(snapshot)

        self.assertEqual("D1", rows[0][0])
        self.assertEqual("SELL", rows[0][1])
        self.assertEqual("H4", rows[1][0])
        self.assertEqual("VR", rows[1][2])

    def test_build_sequence_rows_uses_parent_order_and_cf_label(self) -> None:
        snapshot = {
            "sequences": {
                "D1": {
                    "direction": "BUY",
                    "control_state": "RECOVERED_BY_CF",
                    "active_controller": "D1",
                    "parent_breakout_timestamp": 1773619200,
                    "vr_started_at": 1774008000,
                    "last_cf_timestamp": 1774098000,
                    "cf_count": 1,
                }
            }
        }

        rows = build_sequence_rows(snapshot)

        self.assertEqual("D1", rows[0][0])
        self.assertEqual("BUY", rows[0][1])
        self.assertEqual("CF_OK", rows[0][2])
        self.assertEqual("1", rows[0][7])


if __name__ == "__main__":
    unittest.main()
