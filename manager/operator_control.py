from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from sultan_engine_v2.runtime.file_utils import atomic_write_json


@dataclass(slots=True)
class OperatorControlState:
    paused: bool = False
    allow_new_entries: bool = True
    pause_reason: str | None = None
    last_command: str = "INIT"
    last_command_at: str | None = None
    last_source: str | None = None
    last_message: str | None = None
    last_interpreter: str = "RULE"
    last_response: str | None = None


class JsonOperatorControlStore:
    def __init__(self, path: str | Path | None = None) -> None:
        default_path = Path(__file__).resolve().parent.parent / "runtime" / "state" / "operator_control.json"
        self.path = Path(path) if path else default_path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> OperatorControlState:
        if not self.path.exists():
            return OperatorControlState()

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return OperatorControlState()

        paused = bool(payload.get("paused", False))
        allow_new_entries = bool(payload.get("allow_new_entries", not paused))
        return OperatorControlState(
            paused=paused,
            allow_new_entries=allow_new_entries,
            pause_reason=payload.get("pause_reason"),
            last_command=str(payload.get("last_command") or "INIT"),
            last_command_at=payload.get("last_command_at"),
            last_source=payload.get("last_source"),
            last_message=payload.get("last_message"),
            last_interpreter=str(payload.get("last_interpreter") or "RULE"),
            last_response=payload.get("last_response"),
        )

    def save(self, state: OperatorControlState) -> OperatorControlState:
        atomic_write_json(self.path, asdict(state))
        return state

    def record_command(
        self,
        action: str,
        *,
        source: str,
        message: str,
        interpreter: str = "RULE",
        response: str | None = None,
        reason: str | None = None,
    ) -> OperatorControlState:
        state = self.load()
        normalized_action = action.strip().upper() or "UNKNOWN"
        if normalized_action == "PAUSE":
            state.paused = True
            state.allow_new_entries = False
            state.pause_reason = reason or state.pause_reason or "Operator pause requested"
        elif normalized_action == "RESUME":
            state.paused = False
            state.allow_new_entries = True
            state.pause_reason = None

        state.last_command = normalized_action
        state.last_command_at = datetime.now(timezone.utc).isoformat()
        state.last_source = source
        state.last_message = message
        state.last_interpreter = interpreter
        state.last_response = response
        return self.save(state)
