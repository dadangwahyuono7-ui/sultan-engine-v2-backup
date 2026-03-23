from __future__ import annotations

import unittest

from sultan_engine_v2.manager.ai_manager import ManagerFinding, ManagerReport
from sultan_engine_v2.run_vps_stack import build_periodic_summary_message


class VpsStackTests(unittest.TestCase):
    def test_build_periodic_summary_message_contains_focus_and_findings(self) -> None:
        snapshot = {
            "venue": "MT5",
            "symbol": {"actual": "XAUUSD"},
            "market": {"bid": 4654.45, "spread": 0.08},
            "setup_focus": {"status": "WAIT_CF", "master_timeframe": "H4", "vr_timeframe": "H1"},
            "execution": {"recent_results": [{}, {}], "recent_decisions": [{}]},
            "lifecycle": {"position_structure": [{}]},
        }
        report = ManagerReport(
            created_at="2026-03-20T03:00:00+00:00",
            venue="MT5",
            snapshot_path=None,
            summary="Manager audit for MT5 XAUUSD: 0 ready intent(s), 0 high issue(s), 1 medium issue(s).",
            findings=[ManagerFinding("MEDIUM", "FRACTAL_SPLIT", "chain split")],
            ready_intents=[],
        )

        message = build_periodic_summary_message(snapshot, report)

        self.assertIn("Sultan MT5 Periodic Summary", message)
        self.assertIn("Focus: WAIT_CF", message)
        self.assertIn("Master: H4 | VR: H1", message)
        self.assertIn("Findings: FRACTAL_SPLIT", message)


if __name__ == "__main__":
    unittest.main()