from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

from sultan_engine_v2.manager.ai_manager import SultanAIManager
from sultan_engine_v2.manager.operator_agent import OperatorAgentAnswer
from sultan_engine_v2.manager.operator_control import JsonOperatorControlStore
from sultan_engine_v2.manager.telegram_operator import TelegramOperatorService
from sultan_engine_v2.manager.telegram_reporter import TelegramConfig, TelegramOperatorReporter


class DisabledOpenRouter:
    is_enabled = False

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict | None:
        return None

    def complete_text(self, system_prompt: str, user_prompt: str) -> str | None:
        return None


class FakeOpenRouter:
    is_enabled = True

    def __init__(self, payload: dict, answer_text: str = "Agent answer") -> None:
        self.payload = payload
        self.answer_text = answer_text

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict | None:
        return self.payload

    def complete_text(self, system_prompt: str, user_prompt: str) -> str | None:
        return self.answer_text


class FakeOperatorAgent:
    def answer(self, question: str, sender: str = "operator", snapshot: dict | None = None) -> OperatorAgentAnswer:
        return OperatorAgentAnswer(
            question=question,
            response_text="Analisa agent: master SELL masih in control, belum ada CF recovery baru.",
            interpreter="OPENROUTER_AGENT",
            created_at="2026-03-19T00:00:00+00:00",
            snapshot_timestamp="2026-03-19T00:00:00+00:00",
        )


class TelegramOperatorServiceTests(unittest.TestCase):
    def test_pause_command_updates_control_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = JsonOperatorControlStore(temp_path / "operator_control.json")
            snapshot_path = temp_path / "latest_snapshot.json"
            snapshot_path.write_text(json.dumps(_sample_snapshot(), ensure_ascii=True), encoding="utf-8")

            service = TelegramOperatorService(
                config=TelegramConfig(enabled=True, bot_token="token", chat_id="42"),
                snapshot_path=snapshot_path,
                control_store=store,
                manager=SultanAIManager(report_path=temp_path / "report.json"),
                openrouter=DisabledOpenRouter(),
            )

            response = service.handle_message("/pause spread lagi jelek", sender="dadang")
            state = store.load()

            self.assertIn("Operator pause aktif", response)
            self.assertTrue(state.paused)
            self.assertFalse(state.allow_new_entries)
            self.assertEqual("PAUSE", state.last_command)

    def test_poll_once_replies_to_authorized_chat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = JsonOperatorControlStore(temp_path / "operator_control.json")
            snapshot_path = temp_path / "latest_snapshot.json"
            snapshot_path.write_text(json.dumps(_sample_snapshot(), ensure_ascii=True), encoding="utf-8")
            sent_payloads: list[tuple[str, dict]] = []

            reporter = TelegramOperatorReporter(
                config=TelegramConfig(enabled=True, bot_token="token", chat_id="42"),
                sender=lambda endpoint, payload: sent_payloads.append((endpoint, payload)),
            )
            updates = {
                "result": [
                    {
                        "update_id": 1,
                        "message": {
                            "text": "status",
                            "chat": {"id": "42"},
                            "from": {"username": "dadang"},
                        },
                    }
                ]
            }
            service = TelegramOperatorService(
                config=TelegramConfig(enabled=True, bot_token="token", chat_id="42"),
                snapshot_path=snapshot_path,
                control_store=store,
                manager=SultanAIManager(report_path=temp_path / "report.json"),
                reporter=reporter,
                openrouter=DisabledOpenRouter(),
                api_requester=lambda endpoint, payload: updates,
                cursor_path=temp_path / "cursor.json",
            )

            responses = service.poll_once(timeout_seconds=0)

            self.assertEqual(1, len(responses))
            self.assertEqual(1, len(sent_payloads))
            self.assertIn("Sultan Operator Status", sent_payloads[0][1]["text"])

    def test_openrouter_fallback_handles_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store = JsonOperatorControlStore(temp_path / "operator_control.json")
            snapshot_path = temp_path / "latest_snapshot.json"
            snapshot_path.write_text(json.dumps(_sample_snapshot(), ensure_ascii=True), encoding="utf-8")

            service = TelegramOperatorService(
                config=TelegramConfig(enabled=True, bot_token="token", chat_id="42"),
                snapshot_path=snapshot_path,
                control_store=store,
                manager=SultanAIManager(report_path=temp_path / "report.json"),
                openrouter=FakeOpenRouter(
                    {
                        "action": "QUESTION",
                        "reason": "asked operational question",
                        "response_text": "Engine lagi nunggu CF yang valid, jadi belum ada entry baru.",
                        "confidence": 0.94,
                    },
                    answer_text="Analisa agent: engine lagi nunggu CF yang valid, jadi belum ada entry baru.",
                ),
            )

            response = service.handle_message("kenapa belum entry bro", sender="dadang")
            state = store.load()

            self.assertIn("Analisa agent", response)
            self.assertEqual("QUESTION", state.last_command)
            self.assertEqual("OPENROUTER_AGENT", state.last_interpreter)

    def test_fetch_updates_handles_telegram_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            service = TelegramOperatorService(
                config=TelegramConfig(enabled=True, bot_token="token", chat_id="42"),
                snapshot_path=temp_path / "latest_snapshot.json",
                control_store=JsonOperatorControlStore(temp_path / "operator_control.json"),
                manager=SultanAIManager(report_path=temp_path / "report.json"),
                openrouter=DisabledOpenRouter(),
                api_requester=lambda endpoint, payload: _raise_http_error(409, "Conflict"),
                cursor_path=temp_path / "cursor.json",
            )

            updates = service.fetch_updates(timeout_seconds=0)

            self.assertEqual([], updates)
            self.assertIn("conflict", service.last_poll_error.lower())

    def test_openrouter_alias_action_is_coerced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            snapshot_path = temp_path / "latest_snapshot.json"
            snapshot_path.write_text(json.dumps(_sample_snapshot(), ensure_ascii=True), encoding="utf-8")
            store = JsonOperatorControlStore(temp_path / "operator_control.json")
            service = TelegramOperatorService(
                config=TelegramConfig(enabled=True, bot_token="token", chat_id="42"),
                snapshot_path=snapshot_path,
                control_store=store,
                manager=SultanAIManager(report_path=temp_path / "report.json"),
                openrouter=FakeOpenRouter(
                    {
                        "action": "request_status",
                        "reason": "alias from model",
                        "response_text": "",
                        "confidence": 0.77,
                    }
                ),
            )

            response = service.handle_message("kasih status bro", sender="dadang")

            self.assertIn("Sultan Operator Status", response)

    def test_short_probe_message_returns_local_ready_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            snapshot_path = temp_path / "latest_snapshot.json"
            snapshot_path.write_text(json.dumps(_sample_snapshot(), ensure_ascii=True), encoding="utf-8")
            store = JsonOperatorControlStore(temp_path / "operator_control.json")
            service = TelegramOperatorService(
                config=TelegramConfig(enabled=True, bot_token="token", chat_id="42"),
                snapshot_path=snapshot_path,
                control_store=store,
                manager=SultanAIManager(report_path=temp_path / "report.json"),
                openrouter=DisabledOpenRouter(),
            )

            response = service.handle_message("Tes", sender="dadang")

            self.assertIn("Masuk bro. Operator hidup.", response)

    def test_rule_question_routes_to_operator_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            snapshot_path = temp_path / "latest_snapshot.json"
            snapshot_path.write_text(json.dumps(_sample_snapshot(), ensure_ascii=True), encoding="utf-8")
            store = JsonOperatorControlStore(temp_path / "operator_control.json")
            service = TelegramOperatorService(
                config=TelegramConfig(enabled=True, bot_token="token", chat_id="42"),
                snapshot_path=snapshot_path,
                control_store=store,
                manager=SultanAIManager(report_path=temp_path / "report.json"),
                openrouter=DisabledOpenRouter(),
                operator_agent=FakeOperatorAgent(),
            )

            response = service.handle_message("analisa engine sekarang gimana bro", sender="dadang")
            state = store.load()

            self.assertIn("Analisa agent", response)
            self.assertEqual("QUESTION", state.last_command)
            self.assertEqual("OPENROUTER_AGENT", state.last_interpreter)


def _sample_snapshot() -> dict:
    return {
        "venue": "MT5",
        "symbol": {"requested": "XAUUSD", "actual": "XAUUSD"},
        "market": {"bid": 3020.1, "ask": 3020.4, "last": 3020.2, "time": 1710000000},
        "timeframes": {
            "D1": {"cmp": "BUY"},
            "H4": {"cmp": "BUY"},
            "H1": {"cmp": "BUY"},
            "M30": {"cmp": "BUY"},
            "M15": {"cmp": "SELL"},
            "M5": {"cmp": "SELL"},
        },
        "execution": {
            "mode": "PAPER",
            "recent_decisions": [
                {"allowed": False, "reason": "SPREAD_TOO_WIDE"},
            ],
        },
        "operator": {
            "paused": False,
            "allow_new_entries": True,
            "last_command": "STATUS",
        },
        "sequences": {},
        "lifecycle": {"recent_actions": []},
    }


def _raise_http_error(code: int, reason: str):
    raise HTTPError(url="https://api.telegram.org", code=code, msg=reason, hdrs=None, fp=None)


if __name__ == "__main__":
    unittest.main()