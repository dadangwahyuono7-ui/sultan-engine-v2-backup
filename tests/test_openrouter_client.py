from __future__ import annotations

import unittest

from sultan_engine_v2.manager.openrouter_client import OpenRouterClient, OpenRouterConfig


class OpenRouterClientTests(unittest.TestCase):
    def test_complete_text_returns_message_content(self) -> None:
        client = OpenRouterClient(
            config=OpenRouterConfig(
                enabled=True,
                api_key="token",
                model="openai/gpt-4o-mini",
                app_name="sultan-test",
                referer=None,
                endpoint="https://openrouter.ai/api/v1/chat/completions",
                timeout_seconds=5.0,
            ),
            requester=lambda endpoint, headers, payload, timeout: {
                "choices": [
                    {
                        "message": {
                            "content": "Agent jawab grounded ke snapshot."
                        }
                    }
                ]
            },
        )

        response = client.complete_text("system", "user")

        self.assertEqual("Agent jawab grounded ke snapshot.", response)

    def test_complete_json_parses_code_fenced_response(self) -> None:
        client = OpenRouterClient(
            config=OpenRouterConfig(
                enabled=True,
                api_key="token",
                model="openai/gpt-4o-mini",
                app_name="sultan-test",
                referer=None,
                endpoint="https://openrouter.ai/api/v1/chat/completions",
                timeout_seconds=5.0,
            ),
            requester=lambda endpoint, headers, payload, timeout: {
                "choices": [
                    {
                        "message": {
                            "content": "```json\n{\"action\": \"STATUS\", \"reason\": \"asked status\", \"response_text\": \"ok\", \"confidence\": 0.92}\n```"
                        }
                    }
                ]
            },
        )

        response = client.complete_json("system", "user")

        self.assertIsNotNone(response)
        self.assertEqual("STATUS", response["action"])
        self.assertEqual("ok", response["response_text"])

    def test_complete_text_returns_none_when_requester_raises(self) -> None:
        def failing_requester(endpoint: str, headers: dict[str, str], payload: dict[str, object], timeout: float) -> None:
            raise RuntimeError("openrouter unavailable")

        client = OpenRouterClient(
            config=OpenRouterConfig(
                enabled=True,
                api_key="token",
                model="openai/gpt-4o-mini",
                app_name="sultan-test",
                referer=None,
                endpoint="https://openrouter.ai/api/v1/chat/completions",
                timeout_seconds=5.0,
            ),
            requester=failing_requester,
        )

        response = client.complete_text("system", "user")

        self.assertIsNone(response)
        self.assertIn("RuntimeError", client.last_error or "")


if __name__ == "__main__":
    unittest.main()
