from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sultan_engine_v2.runtime.file_utils import atomic_write_json


@dataclass(slots=True)
class ServiceState:
    service_name: str
    status: str
    pid: int
    hostname: str
    updated_at: str
    base_symbol: str
    execution_mode: str
    auto_execute: bool
    cycle_count: int
    error_count: int
    connected: bool
    bootstrapped: bool
    snapshot_timestamp: str | None = None
    last_error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class JsonServiceStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, state: ServiceState) -> None:
        atomic_write_json(self.path, asdict(state))

    def read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None


def build_service_state(
    *,
    service_name: str,
    status: str,
    base_symbol: str,
    execution_mode: str,
    auto_execute: bool,
    cycle_count: int,
    error_count: int,
    connected: bool,
    bootstrapped: bool,
    snapshot_timestamp: str | None = None,
    last_error: str | None = None,
    details: dict[str, Any] | None = None,
) -> ServiceState:
    return ServiceState(
        service_name=service_name,
        status=status,
        pid=os.getpid(),
        hostname=socket.gethostname(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        base_symbol=base_symbol,
        execution_mode=execution_mode,
        auto_execute=auto_execute,
        cycle_count=cycle_count,
        error_count=error_count,
        connected=connected,
        bootstrapped=bootstrapped,
        snapshot_timestamp=snapshot_timestamp,
        last_error=last_error,
        details=details or {},
    )
