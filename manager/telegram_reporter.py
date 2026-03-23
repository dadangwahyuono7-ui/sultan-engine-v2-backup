from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sultan_engine_v2.runtime.env_loader import load_runtime_env


@dataclass(slots=True)
class TelegramConfig:
    enabled: bool
    bot_token: str | None
    chat_id: str | None
    source: str = "env"


def _parse_bool(raw_value: str | None, default: bool = False) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "on", "yes"}


def _legacy_config_path() -> Path:
    configured_path = os.getenv("SULTAN_TELEGRAM_LEGACY_CONFIG_PATH")
    if configured_path:
        return Path(configured_path)
    return Path(__file__).resolve().parent.parent.parent / "BACKUP_-02-2026_23-20" / "forex" / "telegram_config.py"


def _load_legacy_telegram_config() -> TelegramConfig | None:
    if not _parse_bool(os.getenv("SULTAN_TELEGRAM_ALLOW_LEGACY_CONFIG"), default=True):
        return None

    legacy_path = _legacy_config_path()
    if not legacy_path.exists():
        return None

    spec = importlib.util.spec_from_file_location("sultan_legacy_telegram_config", legacy_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    bot_token = str(getattr(module, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    chat_id = getattr(module, "TELEGRAM_CHAT_ID", None)
    if not bot_token or chat_id in {None, ""}:
        return None

    enabled = bool(getattr(module, "ENABLE_TELEGRAM", True))
    return TelegramConfig(
        enabled=enabled,
        bot_token=bot_token,
        chat_id=str(chat_id),
        source="legacy",
    )


def load_telegram_config_from_env() -> TelegramConfig:
    load_runtime_env(__file__)
    enabled = _parse_bool(os.getenv("SULTAN_TELEGRAM_ENABLED"), default=False)
    bot_token = os.getenv("SULTAN_TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("SULTAN_TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        return TelegramConfig(
            enabled=enabled,
            bot_token=bot_token,
            chat_id=chat_id,
            source="env",
        )

    legacy_config = _load_legacy_telegram_config()
    if legacy_config is not None:
        return legacy_config

    return TelegramConfig(
        enabled=enabled,
        bot_token=bot_token,
        chat_id=chat_id,
        source="env",
    )


class TelegramOperatorReporter:
    def __init__(
        self,
        config: TelegramConfig | None = None,
        sender: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.config = config or load_telegram_config_from_env()
        self.sender = sender or self._default_sender
        self.last_error: str | None = None

    @property
    def is_enabled(self) -> bool:
        return bool(self.config.enabled and self.config.bot_token and self.config.chat_id)

    def send_text(self, message: str) -> bool:
        if not self.is_enabled:
            return False

        endpoint = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        payload = {
            "chat_id": self.config.chat_id,
            "text": message,
        }
        try:
            self.sender(endpoint, payload)
        except Exception as error:
            self.last_error = f"{type(error).__name__}: {error}"
            return False
        self.last_error = None
        return True

    def send_execution_block(self, decision: Any) -> bool:
        return self.send_text(
            "\n".join(
                [
                    "Sultan MT5 Guard Blocked",
                    f"Symbol: {decision.symbol}",
                    f"Reason: {decision.reason}",
                    f"Timeframe: {decision.timeframe}",
                    f"Direction: {decision.direction}",
                ]
            )
        )

    def send_execution_result(self, result: Any) -> bool:
        return self.send_text(
            "\n".join(
                [
                    "Sultan MT5 Execution",
                    f"Symbol: {result.symbol}",
                    f"Success: {result.success}",
                    f"Mode: {result.execution_mode}",
                    f"Message: {result.message}",
                ]
            )
        )

    def send_state_event(self, event: Any, *, symbol: str | None = None) -> bool:
        lines = [
            "Sultan MT5 Structure",
            f"Symbol: {symbol or '-'}",
            f"Event: {getattr(event, 'kind', '-')}",
            f"Timeframe: {getattr(event, 'timeframe', '-')}",
        ]
        parent_timeframe = getattr(event, "parent_timeframe", None)
        if parent_timeframe:
            lines.append(f"Parent: {parent_timeframe}")
        direction = getattr(event, "direction", None)
        if direction:
            lines.append(f"Direction: {direction}")
        event_timestamp = getattr(event, "event_timestamp", None)
        if event_timestamp:
            try:
                rendered_time = datetime.fromtimestamp(int(event_timestamp)).strftime("%Y-%m-%d %H:%M")
            except (TypeError, ValueError, OSError, OverflowError):
                rendered_time = str(event_timestamp)
            lines.append(f"At: {rendered_time}")
        lines.append(f"Message: {getattr(event, 'message', '-')}")
        return self.send_text("\n".join(lines))

    def send_manager_report(self, report: Any, rendered_text: str) -> bool:
        return self.send_text(f"Sultan MT5 Manager Report\n\n{rendered_text}")

    @staticmethod
    def _default_sender(endpoint: str, payload: dict[str, Any]) -> Any:
        request = Request(
            endpoint,
            data=urlencode(payload).encode("utf-8"),
            method="POST",
        )
        with urlopen(request, timeout=15) as response:  # pragma: no cover - network dependent
            return json.loads(response.read().decode("utf-8"))
