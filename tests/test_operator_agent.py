from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sultan_engine_v2.manager.ai_manager import SultanAIManager
from sultan_engine_v2.manager.operator_agent import JsonOperatorConversationStore, SultanOperatorAgent


class DisabledOpenRouter:
    is_enabled = False

    def complete_text(self, system_prompt: str, user_prompt: str) -> str | None:
        return None


class FakeOpenRouter:
    is_enabled = True

    def __init__(self, answer: str) -> None:
        self.answer = answer

    def complete_text(self, system_prompt: str, user_prompt: str) -> str | None:
        return self.answer


class SultanOperatorAgentTests(unittest.TestCase):
    def test_agent_uses_openrouter_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            snapshot_path = temp_path / "latest_snapshot.json"
            doctrine_path = temp_path / "daily_deploy_rules.md"
            snapshot_path.write_text(json.dumps(_sample_snapshot(), ensure_ascii=True), encoding="utf-8")
            doctrine_path.write_text("CMP -> VR -> CF", encoding="utf-8")

            agent = SultanOperatorAgent(
                snapshot_path=snapshot_path,
                doctrine_path=doctrine_path,
                openrouter=FakeOpenRouter("Kesimpulan bro: market masih dikuasai SELL master."),
                manager=SultanAIManager(report_path=temp_path / "report.json"),
                thread_store=JsonOperatorConversationStore(temp_path / "threads.json"),
            )

            answer = agent.answer("market sekarang gimana bro", sender="dadang")

            self.assertEqual("OPENROUTER_AGENT", answer.interpreter)
            self.assertIn("SELL master", answer.response_text)

    def test_agent_falls_back_to_grounded_summary_without_openrouter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            snapshot_path = temp_path / "latest_snapshot.json"
            snapshot_path.write_text(json.dumps(_sample_snapshot(), ensure_ascii=True), encoding="utf-8")

            agent = SultanOperatorAgent(
                snapshot_path=snapshot_path,
                doctrine_path=temp_path / "missing_doctrine.md",
                openrouter=DisabledOpenRouter(),
                manager=SultanAIManager(report_path=temp_path / "report.json"),
                thread_store=JsonOperatorConversationStore(temp_path / "threads.json"),
            )

            answer = agent.answer("kenapa belum ada entry", sender="dadang")

            self.assertEqual("RULE_AGENT", answer.interpreter)
            self.assertIn("CMP chain", answer.response_text)
            self.assertIn("ready", answer.response_text)

    def test_agent_rejects_hallucinated_vr_cf_and_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            snapshot_path = temp_path / "latest_snapshot.json"
            snapshot_path.write_text(json.dumps(_sample_snapshot(), ensure_ascii=True), encoding="utf-8")

            agent = SultanOperatorAgent(
                snapshot_path=snapshot_path,
                doctrine_path=temp_path / "missing_doctrine.md",
                openrouter=FakeOpenRouter("M30 adalah parent aktif yang sedang dalam status VR terhadap M15 dan M5 telah menghasilkan CF valid."),
                manager=SultanAIManager(report_path=temp_path / "report.json"),
                thread_store=JsonOperatorConversationStore(temp_path / "threads.json"),
            )

            answer = agent.answer("analisa market sekarang", sender="dadang")

            self.assertEqual("RULE_AGENT", answer.interpreter)
            self.assertIn("belum ada VR aktif", answer.response_text)


def _sample_snapshot() -> dict:
    return {
        "timestamp": "2026-03-19T16:56:10.344911+00:00",
        "venue": "MT5",
        "symbol": {"requested": "XAUUSD", "actual": "XAUUSD"},
        "market": {"bid": 4588.17, "ask": 4588.27, "last": 0.0, "time": 1773950190},
        "timeframes": {
            "D1": {"cmp": "SELL"},
            "H4": {"cmp": "SELL"},
            "H1": {"cmp": "SELL"},
            "M30": {"cmp": "SELL"},
            "M15": {"cmp": "SELL"},
            "M5": {"cmp": "SELL"},
        },
        "sequences": {
            "D1": {"control_state": "IN_CONTROL", "active_controller": "D1", "vr_active": False},
            "H4": {"control_state": "IN_CONTROL", "active_controller": "H4", "vr_active": False},
        },
        "execution": {"mode": "PAPER", "recent_decisions": []},
        "operator": {"allow_new_entries": True, "paused": False},
        "logs": ["23:22:27 | D1 seeded as CMP SELL"],
        "lifecycle": {"recent_actions": []},
    }


if __name__ == "__main__":
    unittest.main()