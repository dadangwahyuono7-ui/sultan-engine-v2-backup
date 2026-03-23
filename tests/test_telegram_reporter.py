from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sultan_engine_v2.manager.telegram_reporter import TelegramConfig, TelegramOperatorReporter, load_telegram_config_from_env


class DummyDecision:
    symbol = "XAUUSD"
    reason = "SPREAD_TOO_WIDE"
    timeframe = "M15"
    direction = "BUY"


class DummyResult:
    symbol = "XAUUSD"
    success = True
    execution_mode = "PAPER"
    message = "Paper execution only"


class TelegramReporterTests(unittest.TestCase):
    def test_load_config_falls_back_to_legacy_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_path = Path(temp_dir) / "telegram_config.py"
            legacy_path.write_text(
                "TELEGRAM_BOT_TOKEN = 'legacy-token'\nTELEGRAM_CHAT_ID = -1001234567890\nENABLE_TELEGRAM = True\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "SULTAN_TELEGRAM_BOT_TOKEN": "",
                    "SULTAN_TELEGRAM_CHAT_ID": "",
                    "SULTAN_TELEGRAM_ENABLED": "",
                    "SULTAN_TELEGRAM_LEGACY_CONFIG_PATH": str(legacy_path),
                },
                clear=False,
            ):
                config = load_telegram_config_from_env()

            self.assertTrue(config.enabled)
            self.assertEqual("legacy-token", config.bot_token)
            self.assertEqual("-1001234567890", config.chat_id)
            self.assertEqual("legacy", config.source)

    def test_send_execution_block_uses_sender_when_enabled(self) -> None:
        sent_payloads: list[tuple[str, dict]] = []
        reporter = TelegramOperatorReporter(
            config=TelegramConfig(enabled=True, bot_token="token", chat_id="chat"),
            sender=lambda endpoint, payload: sent_payloads.append((endpoint, payload)),
        )

        delivered = reporter.send_execution_block(DummyDecision())

        self.assertTrue(delivered)
        self.assertEqual(1, len(sent_payloads))
        self.assertIn("SPREAD_TOO_WIDE", sent_payloads[0][1]["text"])

    def test_send_execution_result_uses_sender_when_enabled(self) -> None:
        sent_payloads: list[tuple[str, dict]] = []
        reporter = TelegramOperatorReporter(
            config=TelegramConfig(enabled=True, bot_token="token", chat_id="chat"),
            sender=lambda endpoint, payload: sent_payloads.append((endpoint, payload)),
        )

        delivered = reporter.send_execution_result(DummyResult())

        self.assertTrue(delivered)
        self.assertEqual(1, len(sent_payloads))
        self.assertIn("Paper execution only", sent_payloads[0][1]["text"])

    def test_send_text_returns_false_when_sender_raises(self) -> None:
        def failing_sender(endpoint: str, payload: dict) -> None:
            raise RuntimeError("telegram down")

        reporter = TelegramOperatorReporter(
            config=TelegramConfig(enabled=True, bot_token="token", chat_id="chat"),
            sender=failing_sender,
        )

        delivered = reporter.send_text("hello")

        self.assertFalse(delivered)
        self.assertIn("RuntimeError", reporter.last_error or "")

    def test_send_state_event_uses_sender_when_enabled(self) -> None:
        sent_payloads: list[tuple[str, dict]] = []
        reporter = TelegramOperatorReporter(
            config=TelegramConfig(enabled=True, bot_token="token", chat_id="chat"),
            sender=lambda endpoint, payload: sent_payloads.append((endpoint, payload)),
        )

        delivered = reporter.send_state_event(
            SimpleNamespace(
                kind="VR_ACTIVE",
                timeframe="H1",
                parent_timeframe="H4",
                direction="BUY",
                event_timestamp=1773954000,
                message="H1 is now VR against H4",
            ),
            symbol="XAUUSD",
        )

        self.assertTrue(delivered)
        self.assertEqual(1, len(sent_payloads))
        self.assertIn("VR_ACTIVE", sent_payloads[0][1]["text"])
        self.assertIn("Parent: H4", sent_payloads[0][1]["text"])
        self.assertIn("XAUUSD", sent_payloads[0][1]["text"])


if __name__ == "__main__":
    unittest.main()
