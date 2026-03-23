from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sultan_engine_v2.manager.ai_manager import SultanAIManager


def build_manager_snapshot() -> dict:
    return {
        "venue": "MT5",
        "symbol": {"requested": "XAUUSD", "actual": "XAUUSD", "resolution_source": "legacy-suffix"},
        "market": {"bid": 1999.5, "ask": 2000.0, "last": 1999.75, "time": 1},
        "timeframes": {
            "D1": {"cmp": "BUY", "support": 1900.0, "resistance": 2100.0, "target_tf": None},
            "H4": {"cmp": "BUY", "support": 1950.0, "resistance": 2060.0, "target_tf": None},
            "H1": {"cmp": "BUY", "support": 1986.0, "resistance": 2030.0, "target_tf": None},
            "M30": {"cmp": "BUY", "support": 1990.0, "resistance": 2026.0, "target_tf": None},
            "M15": {"cmp": "BUY", "support": 1994.0, "resistance": 2008.0, "target_tf": "M30"},
            "M5": {"cmp": None, "support": 0.0, "resistance": 0.0, "target_tf": None},
        },
        "sequences": {
            "H1": {
                "direction": "BUY",
                "vr_child": "M30",
                "vr_active": False,
                "cf_count": 1,
                "control_state": "RECOVERED_BY_CF",
                "active_controller": "H1",
                "last_cf_timeframe": "M15",
            },
            "H4": {
                "direction": "BUY",
                "vr_child": "H1",
                "vr_active": False,
                "cf_count": 0,
                "control_state": "IN_CONTROL",
                "active_controller": "H4",
                "last_cf_timeframe": None,
            },
        },
    }


class AIManagerTests(unittest.TestCase):
    def test_manager_audit_reports_ready_trade_and_stale_market(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = SultanAIManager(report_path=Path(temp_dir) / "manager_report.json")
            report = manager.audit_snapshot(build_manager_snapshot(), snapshot_path="runtime/state/latest_snapshot.json")

        codes = {finding.code for finding in report.findings}

        self.assertIn("TRADE_READY", codes)
        self.assertIn("MARKET_STALE", codes)
        self.assertIn("CMP_UNSEEDED", codes)
        self.assertEqual(1, len(report.ready_intents))

    def test_manager_audit_flags_structure_exit_and_grace(self) -> None:
        snapshot = build_manager_snapshot()
        snapshot["lifecycle"] = {
            "recent_actions": [],
            "position_structure": [
                {
                    "ticket": 101,
                    "status": "EXIT_NOW",
                    "parent_timeframe": "M15",
                    "cf_timeframe": "M5",
                },
                {
                    "ticket": 202,
                    "status": "GRACE",
                    "parent_timeframe": "H1",
                    "cf_timeframe": "M15",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = SultanAIManager(report_path=Path(temp_dir) / "manager_report.json")
            report = manager.audit_snapshot(snapshot, snapshot_path="runtime/state/latest_snapshot.json")

        codes = {finding.code for finding in report.findings}
        self.assertIn("POSITION_STRUCTURE_EXIT", codes)
        self.assertIn("POSITION_STRUCTURE_GRACE", codes)


if __name__ == "__main__":
    unittest.main()