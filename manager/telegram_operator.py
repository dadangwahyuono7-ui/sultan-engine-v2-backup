from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError

from sultan_engine_v2.manager.ai_manager import SultanAIManager
from sultan_engine_v2.manager.openrouter_client import OpenRouterClient
from sultan_engine_v2.manager.operator_agent import OperatorAgentAnswer, SultanOperatorAgent
from sultan_engine_v2.manager.operator_control import JsonOperatorControlStore
from sultan_engine_v2.manager.telegram_reporter import TelegramConfig, TelegramOperatorReporter, load_telegram_config_from_env
from sultan_engine_v2.runtime.file_utils import atomic_write_json


class OperatorAction(StrEnum):
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    STATUS = "STATUS"
    AUDIT = "AUDIT"
    HELP = "HELP"
    QUESTION = "QUESTION"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class OperatorIntent:
    action: OperatorAction
    reason: str
    response_text: str | None = None
    confidence: float = 1.0
    interpreter: str = "RULE"


@dataclass(slots=True)
class TelegramMessage:
    update_id: int
    chat_id: str
    text: str
    sender: str


class TelegramOperatorService:
    def __init__(
        self,
        config: TelegramConfig | None = None,
        snapshot_path: str | Path | None = None,
        control_store: JsonOperatorControlStore | None = None,
        manager: SultanAIManager | None = None,
        reporter: TelegramOperatorReporter | None = None,
        openrouter: OpenRouterClient | None = None,
        operator_agent: SultanOperatorAgent | None = None,
        api_requester: Callable[[str, dict[str, Any]], Any] | None = None,
        cursor_path: str | Path | None = None,
    ) -> None:
        self.config = config or load_telegram_config_from_env()
        runtime_dir = Path(__file__).resolve().parent.parent / "runtime"
        self.snapshot_path = Path(snapshot_path) if snapshot_path else runtime_dir / "state" / "latest_snapshot.json"
        self.control_store = control_store or JsonOperatorControlStore(runtime_dir / "state" / "operator_control.json")
        self.manager = manager or SultanAIManager()
        self.reporter = reporter or TelegramOperatorReporter(config=self.config)
        self.openrouter = openrouter or OpenRouterClient()
        self.operator_agent = operator_agent or SultanOperatorAgent(
            snapshot_path=self.snapshot_path,
            openrouter=self.openrouter,
            manager=self.manager,
        )
        self.api_requester = api_requester or TelegramOperatorReporter._default_sender
        self.cursor_path = Path(cursor_path) if cursor_path else runtime_dir / "state" / "telegram_operator_cursor.json"
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        self.last_poll_error: str | None = None
        self._last_reported_poll_error: str | None = None

    @property
    def is_enabled(self) -> bool:
        return bool(self.config.enabled and self.config.bot_token and self.config.chat_id)

    def fetch_updates(self, timeout_seconds: int = 20) -> list[TelegramMessage]:
        if not self.is_enabled:
            return []

        payload: dict[str, Any] = {
            "timeout": timeout_seconds,
            "allowed_updates": json.dumps(["message"]),
        }
        offset = self._load_cursor()
        if offset is not None:
            payload["offset"] = offset

        endpoint = f"https://api.telegram.org/bot{self.config.bot_token}/getUpdates"
        try:
            response = self.api_requester(endpoint, payload)
            self.last_poll_error = None
        except HTTPError as error:
            if error.code == 409:
                self.last_poll_error = "Telegram getUpdates conflict. Another bot poller is already using this token."
                return []
            self.last_poll_error = f"Telegram polling failed with HTTP {error.code}: {error.reason}"
            return []
        except URLError as error:
            self.last_poll_error = f"Telegram polling connection error: {error.reason}"
            return []
        except Exception as error:
            self.last_poll_error = f"Telegram polling error: {error}"
            return []

        results = response.get("result", []) if isinstance(response, dict) else []

        messages: list[TelegramMessage] = []
        max_update_id = offset - 1 if offset is not None else -1
        for item in results:
            update_id = int(item.get("update_id") or 0)
            if update_id > max_update_id:
                max_update_id = update_id
            parsed = self._parse_update(item)
            if parsed is not None:
                messages.append(parsed)

        if max_update_id >= 0:
            self._save_cursor(max_update_id + 1)
        return messages

    def poll_once(self, timeout_seconds: int = 20) -> list[str]:
        responses: list[str] = []
        for message in self.fetch_updates(timeout_seconds=timeout_seconds):
            if message.chat_id != str(self.config.chat_id):
                continue
            response = self.handle_message(message.text, sender=message.sender)
            if response:
                self._send_chunks(response)
                responses.append(response)
        return responses

    def run_forever(self, timeout_seconds: int = 20, idle_sleep_seconds: float = 1.0) -> None:
        while True:
            self.poll_once(timeout_seconds=timeout_seconds)
            if self.last_poll_error and self.last_poll_error != self._last_reported_poll_error:
                print(f"[telegram-operator] {self.last_poll_error}")
                self._last_reported_poll_error = self.last_poll_error
            elif not self.last_poll_error and self._last_reported_poll_error is not None:
                print("[telegram-operator] polling recovered")
                self._last_reported_poll_error = None

            sleep_seconds = max(idle_sleep_seconds, 5.0) if self.last_poll_error else idle_sleep_seconds
            time.sleep(sleep_seconds)

    def handle_message(self, text: str, *, sender: str = "telegram") -> str:
        cleaned_text = text.strip()
        if not cleaned_text:
            return "Pesan kosong. Pakai STATUS, AUDIT, PAUSE, RESUME, atau HELP."

        snapshot = self._load_snapshot()
        intent = self._interpret(cleaned_text, snapshot)
        if intent.action == OperatorAction.PAUSE:
            response = f"Operator pause aktif. New entry diblok. Reason: {intent.reason}"
            self.control_store.record_command(
                intent.action.value,
                source=sender,
                message=cleaned_text,
                interpreter=intent.interpreter,
                response=response,
                reason=intent.reason,
            )
            return response

        if intent.action == OperatorAction.RESUME:
            response = "Operator resume aktif. New entry boleh jalan lagi sesuai guard dan aturan engine."
            self.control_store.record_command(
                intent.action.value,
                source=sender,
                message=cleaned_text,
                interpreter=intent.interpreter,
                response=response,
                reason=intent.reason,
            )
            return response

        if intent.action == OperatorAction.STATUS:
            response = self._build_status_text(snapshot)
            self.control_store.record_command(
                intent.action.value,
                source=sender,
                message=cleaned_text,
                interpreter=intent.interpreter,
                response=response,
                reason=intent.reason,
            )
            return response

        if intent.action == OperatorAction.AUDIT:
            response = self._build_audit_text(snapshot)
            self.control_store.record_command(
                intent.action.value,
                source=sender,
                message=cleaned_text,
                interpreter=intent.interpreter,
                response=response,
                reason=intent.reason,
            )
            return response

        if intent.action == OperatorAction.QUESTION and intent.response_text:
            if intent.interpreter == "RULE":
                self.control_store.record_command(
                    intent.action.value,
                    source=sender,
                    message=cleaned_text,
                    interpreter=intent.interpreter,
                    response=intent.response_text,
                    reason=intent.reason,
                )
                return intent.response_text

        if intent.action == OperatorAction.QUESTION:
            answer = self.operator_agent.answer(cleaned_text, sender=sender, snapshot=snapshot)
            self.control_store.record_command(
                intent.action.value,
                source=sender,
                message=cleaned_text,
                interpreter=answer.interpreter,
                response=answer.response_text,
                reason=intent.reason,
            )
            return answer.response_text

        if intent.action == OperatorAction.HELP:
            response = self._help_text()
            self.control_store.record_command(
                intent.action.value,
                source=sender,
                message=cleaned_text,
                interpreter=intent.interpreter,
                response=response,
                reason=intent.reason,
            )
            return response

        response = self._help_text()
        self.control_store.record_command(
            OperatorAction.HELP.value,
            source=sender,
            message=cleaned_text,
            interpreter=intent.interpreter,
            response=response,
            reason=intent.reason,
        )
        return response

    def _interpret(self, text: str, snapshot: dict | None) -> OperatorIntent:
        direct_intent = self._rule_based_intent(text)
        if direct_intent.action != OperatorAction.UNKNOWN:
            return direct_intent

        if not self.openrouter.is_enabled:
            return OperatorIntent(action=OperatorAction.QUESTION, reason="Route to local operator agent fallback.", confidence=0.0, interpreter="RULE")

        ai_intent = self._ai_intent(text, snapshot)
        if ai_intent is not None:
            return ai_intent

        return OperatorIntent(
            action=OperatorAction.HELP,
            reason="OpenRouter could not classify the operator message.",
            response_text=self._help_text(),
            confidence=0.0,
            interpreter="OPENROUTER",
        )

    def _ai_intent(self, text: str, snapshot: dict | None) -> OperatorIntent | None:
        snapshot_summary = self._build_snapshot_summary(snapshot)
        payload = self.openrouter.complete_json(
            system_prompt=(
                "You are the operator intent router for a live MT5 CFD trading engine. "
                "You only classify operator messages into safe bounded actions. "
                "Never generate trade signals, entries, exits, or risk overrides. "
                "Allowed actions are PAUSE, RESUME, STATUS, AUDIT, HELP, QUESTION, UNKNOWN. "
                "Return JSON only with keys action, reason, response_text, confidence."
            ),
            user_prompt=(
                f"Operator message: {text}\n\n"
                f"Runtime summary:\n{snapshot_summary}\n\n"
                "If the message asks to stop new entries or hold trading, use PAUSE. "
                "If it asks to continue new entries, use RESUME. "
                "If it asks current condition, use STATUS. "
                "If it asks for analysis/check/report, use AUDIT. "
                "If it asks about engine behavior, market structure, SOP, CMP, VR, CF, or asks why something happened, use QUESTION."
            ),
        )
        if not payload:
            return None

        action = self._coerce_action(payload.get("action"))
        return OperatorIntent(
            action=action,
            reason=str(payload.get("reason") or "OpenRouter classification"),
            response_text=str(payload.get("response_text") or "").strip() or None,
            confidence=float(payload.get("confidence") or 0.0),
            interpreter="OPENROUTER",
        )

    @staticmethod
    def _coerce_action(raw_action: Any) -> OperatorAction:
        normalized = str(raw_action or "UNKNOWN").strip().upper().replace("-", "_").replace(" ", "_")
        alias_map = {
            "REQUEST_STATUS": OperatorAction.STATUS,
            "GET_STATUS": OperatorAction.STATUS,
            "SHOW_STATUS": OperatorAction.STATUS,
            "CHECK_STATUS": OperatorAction.STATUS,
            "REQUEST_AUDIT": OperatorAction.AUDIT,
            "GET_AUDIT": OperatorAction.AUDIT,
            "REPORT": OperatorAction.AUDIT,
            "CHECK": OperatorAction.AUDIT,
            "STOP": OperatorAction.PAUSE,
            "HOLD": OperatorAction.PAUSE,
            "STOP_ENTRY": OperatorAction.PAUSE,
            "START": OperatorAction.RESUME,
            "CONTINUE": OperatorAction.RESUME,
            "GO": OperatorAction.RESUME,
            "MENU": OperatorAction.HELP,
            "COMMANDS": OperatorAction.HELP,
            "EXPLAIN": OperatorAction.QUESTION,
            "WHY": OperatorAction.QUESTION,
        }
        if normalized in alias_map:
            return alias_map[normalized]

        try:
            return OperatorAction(normalized)
        except ValueError:
            return OperatorAction.UNKNOWN

    @staticmethod
    def _rule_based_intent(text: str) -> OperatorIntent:
        normalized = " ".join(text.strip().lower().split())
        reason = "Matched operator command"

        if normalized in {"tes", "test", "ping", "halo", "hai", "hello", "bro", "bro?"}:
            return OperatorIntent(
                OperatorAction.QUESTION,
                "Operator connectivity probe",
                response_text=(
                    "Masuk bro. Operator hidup. Kalau mau pakai AI, coba tanya misalnya: "
                    "'kenapa belum ada entry sekarang?', 'status market sekarang gimana?', atau 'tolong audit snapshot terbaru'."
                ),
                confidence=1.0,
                interpreter="RULE",
            )

        if normalized.startswith("/"):
            command, _, remainder = normalized[1:].partition(" ")
            if command in {"pause", "hold", "stop"}:
                return OperatorIntent(OperatorAction.PAUSE, remainder or reason)
            if command in {"resume", "start", "go"}:
                return OperatorIntent(OperatorAction.RESUME, remainder or reason)
            if command in {"status", "state"}:
                return OperatorIntent(OperatorAction.STATUS, reason)
            if command in {"audit", "report", "check"}:
                return OperatorIntent(OperatorAction.AUDIT, reason)
            if command in {"help", "menu", "commands"}:
                return OperatorIntent(OperatorAction.HELP, reason)

        if normalized in {"status", "cek status", "state", "gimana status"}:
            return OperatorIntent(OperatorAction.STATUS, reason)
        if normalized in {"audit", "cek", "report", "laporan"}:
            return OperatorIntent(OperatorAction.AUDIT, reason)
        if any(keyword in normalized for keyword in ("pause", "jeda", "stop entry", "hold dulu", "jangan entry")):
            return OperatorIntent(OperatorAction.PAUSE, reason)
        if any(keyword in normalized for keyword in ("resume", "lanjut", "jalan lagi", "boleh entry")):
            return OperatorIntent(OperatorAction.RESUME, reason)
        if normalized in {"help", "bantuan", "menu"}:
            return OperatorIntent(OperatorAction.HELP, reason)

        tokens = set(normalized.replace("?", " ").split())
        question_tokens = {
            "analisa",
            "analysis",
            "engine",
            "market",
            "sop",
            "cmp",
            "vr",
            "cf",
            "kenapa",
            "gimana",
            "bagaimana",
            "jelasin",
            "jelaskan",
            "kondisi",
            "siap",
            "trend",
        }
        if "?" in normalized or tokens.intersection(question_tokens):
            return OperatorIntent(
                OperatorAction.QUESTION,
                "Likely operator analysis question.",
                confidence=0.7,
                interpreter="RULE",
            )

        return OperatorIntent(OperatorAction.UNKNOWN, "No direct operator command matched.", confidence=0.0)

    @staticmethod
    def _parse_update(payload: dict[str, Any]) -> TelegramMessage | None:
        message = payload.get("message") or {}
        text = message.get("text")
        if not text:
            return None

        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        sender_name = sender.get("username") or sender.get("first_name") or str(chat.get("id") or "telegram")
        return TelegramMessage(
            update_id=int(payload.get("update_id") or 0),
            chat_id=str(chat.get("id") or ""),
            text=str(text),
            sender=str(sender_name),
        )

    def _build_audit_text(self, snapshot: dict | None) -> str:
        if snapshot is None or not self.snapshot_path.exists():
            return "Snapshot belum ada. Jalankan runtime MT5 dulu baru audit bisa dibaca."
        report = self.manager.audit_snapshot_file(self.snapshot_path)
        return self.manager.render_text(report)

    def _build_status_text(self, snapshot: dict | None) -> str:
        state = self.control_store.load()
        if snapshot is None:
            paused = "YES" if state.paused else "NO"
            return (
                "Sultan Operator Status\n"
                f"Snapshot: missing\n"
                f"Paused: {paused}\n"
                f"Last Command: {state.last_command}\n"
                "Runtime belum menulis snapshot terbaru."
            )

        symbol = snapshot.get("symbol", {}).get("actual") or snapshot.get("symbol", {}).get("requested") or "UNKNOWN"
        market = snapshot.get("market", {})
        execution = snapshot.get("execution", {})
        operator = snapshot.get("operator", {})
        decisions = execution.get("recent_decisions", [])
        blocked = [decision for decision in decisions if not decision.get("allowed", True)]
        last_block = blocked[-1]["reason"] if blocked else "-"
        cmp_chain = []
        for timeframe in ("D1", "H4", "H1", "M30", "M15", "M5"):
            direction = snapshot.get("timeframes", {}).get(timeframe, {}).get("cmp") or "-"
            cmp_chain.append(f"{timeframe}:{direction}")

        paused = "YES" if operator.get("paused", state.paused) else "NO"
        auto_entry = "ON" if operator.get("allow_new_entries", state.allow_new_entries) else "OFF"
        return "\n".join(
            [
                "Sultan Operator Status",
                f"Symbol: {symbol}",
                f"Bid/Ask: {market.get('bid', 0.0)} / {market.get('ask', 0.0)}",
                f"Execution Mode: {execution.get('mode', '-')}",
                f"Auto Entry: {auto_entry}",
                f"Paused: {paused}",
                f"Pause Reason: {operator.get('pause_reason') or state.pause_reason or '-'}",
                f"Last Command: {operator.get('last_command') or state.last_command}",
                f"Last Block: {last_block}",
                f"CMP Chain: {' | '.join(cmp_chain)}",
            ]
        )

    def _build_snapshot_summary(self, snapshot: dict | None) -> str:
        if snapshot is None:
            return "Snapshot is missing. Only operator control state is available."

        execution = snapshot.get("execution", {})
        operator = snapshot.get("operator", {})
        lines = [
            f"Symbol: {snapshot.get('symbol', {}).get('actual') or snapshot.get('symbol', {}).get('requested') or 'UNKNOWN'}",
            f"Venue: {snapshot.get('venue') or 'MT5'}",
            f"Execution mode: {execution.get('mode', '-')}",
            f"Auto entry: {'ON' if operator.get('allow_new_entries', True) else 'OFF'}",
            f"Paused: {'YES' if operator.get('paused', False) else 'NO'}",
            f"Last command: {operator.get('last_command') or '-'}",
        ]
        timeframes = snapshot.get("timeframes", {})
        for timeframe in ("D1", "H4", "H1", "M30", "M15", "M5"):
            if timeframe in timeframes:
                lines.append(f"{timeframe} CMP: {timeframes[timeframe].get('cmp') or '-'}")
        return "\n".join(lines)

    @staticmethod
    def _help_text() -> str:
        return "\n".join(
            [
                "Sultan Operator Commands",
                "- STATUS : lihat kondisi runtime terbaru",
                "- AUDIT : jalankan manager audit dari snapshot terbaru",
                "- PAUSE <alasan> : blok new entry, lifecycle posisi tetap jalan",
                "- RESUME : buka lagi new entry sesuai guard engine",
                "- HELP : tampilkan daftar command",
                "Freeform question bisa dipakai buat tanya engine atau market sesuai SOP CMP-VR-CF.",
                "AI agent hanya untuk analisa operator yang grounded ke snapshot dan doctrine, bukan pengambil keputusan trade.",
            ]
        )

    def _load_snapshot(self) -> dict | None:
        if not self.snapshot_path.exists():
            return None
        try:
            return json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _load_cursor(self) -> int | None:
        if not self.cursor_path.exists():
            return None
        try:
            payload = json.loads(self.cursor_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        raw_value = payload.get("offset")
        return int(raw_value) if raw_value is not None else None

    def _save_cursor(self, offset: int) -> None:
        atomic_write_json(self.cursor_path, {"offset": offset})

    def _send_chunks(self, message: str, chunk_size: int = 3500) -> None:
        if not self.reporter.is_enabled:
            return
        text = message.strip()
        if not text:
            return
        for start in range(0, len(text), chunk_size):
            self.reporter.send_text(text[start : start + chunk_size])
