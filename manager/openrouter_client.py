from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib.request import Request, urlopen

from sultan_engine_v2.runtime.env_loader import load_runtime_env


def _parse_bool(raw_value: str | None, default: bool = False) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "on", "yes"}


@dataclass(slots=True)
class OpenRouterConfig:
    enabled: bool
    api_key: str | None
    model: str
    app_name: str
    referer: str | None
    endpoint: str
    timeout_seconds: float


def load_openrouter_config_from_env() -> OpenRouterConfig:
    load_runtime_env(__file__)
    api_key = os.getenv("SULTAN_OPENROUTER_API_KEY")
    enabled_default = bool(api_key)
    return OpenRouterConfig(
        enabled=_parse_bool(os.getenv("SULTAN_OPENROUTER_ENABLED"), default=enabled_default),
        api_key=api_key,
        model=os.getenv("SULTAN_OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        app_name=os.getenv("SULTAN_OPENROUTER_APP_NAME", "sultan-engine-v2"),
        referer=os.getenv("SULTAN_OPENROUTER_REFERER"),
        endpoint=os.getenv("SULTAN_OPENROUTER_ENDPOINT", "https://openrouter.ai/api/v1/chat/completions"),
        timeout_seconds=float(os.getenv("SULTAN_OPENROUTER_TIMEOUT_SECONDS", "30")),
    )


class OpenRouterClient:
    def __init__(
        self,
        config: OpenRouterConfig | None = None,
        requester: Callable[[str, dict[str, str], dict[str, Any], float], Any] | None = None,
    ) -> None:
        self.config = config or load_openrouter_config_from_env()
        self.requester = requester or self._default_requester
        self.last_error: str | None = None

    @property
    def is_enabled(self) -> bool:
        return bool(self.config.enabled and self.config.api_key and self.config.model)

    def complete_text(self, system_prompt: str, user_prompt: str) -> str | None:
        if not self.is_enabled:
            return None

        payload = {
            "model": self.config.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "X-Title": self.config.app_name,
        }
        if self.config.referer:
            headers["HTTP-Referer"] = self.config.referer

        try:
            raw_response = self.requester(self.config.endpoint, headers, payload, self.config.timeout_seconds)
            if isinstance(raw_response, str):
                data = json.loads(raw_response)
            else:
                data = raw_response
            text = _extract_text_payload(data)
        except Exception as error:
            self.last_error = f"{type(error).__name__}: {error}"
            return None

        self.last_error = None
        return text

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any] | None:
        text = self.complete_text(system_prompt, user_prompt)
        if text is None:
            return None

        return _extract_json_payload(text)

    @staticmethod
    def _default_requester(
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> Any:
        request = Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=timeout_seconds) as response:  # pragma: no cover - network dependent
            return json.loads(response.read().decode("utf-8"))


def _extract_json_payload(content: Any) -> dict[str, Any] | None:
    if isinstance(content, list):
        text = "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )
    else:
        text = str(content)

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].lstrip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None

    try:
        return json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return None


def _extract_text_payload(data: dict[str, Any]) -> str | None:
    content = data.get("choices", [{}])[0].get("message", {}).get("content")
    if content is None:
        return None
    if isinstance(content, list):
        return "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        ).strip() or None
    return str(content).strip() or None

