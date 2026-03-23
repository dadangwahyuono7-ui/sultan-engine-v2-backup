from __future__ import annotations

import json
import os
import socket
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


@dataclass(slots=True)
class ProcessLockMetadata:
    service_name: str
    pid: int
    hostname: str
    acquired_at: str
    command: str | None = None


class ProcessLockError(RuntimeError):
    pass


class ProcessAlreadyRunningError(ProcessLockError):
    def __init__(self, metadata: ProcessLockMetadata) -> None:
        self.metadata = metadata
        super().__init__(
            f"Service {metadata.service_name} is already running with pid={metadata.pid} on host={metadata.hostname}."
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FileProcessLock:
    def __init__(
        self,
        path: str | Path,
        service_name: str,
        *,
        pid_provider: Callable[[], int] | None = None,
        command: str | None = None,
        hostname: str | None = None,
        time_provider: Callable[[], datetime] | None = None,
        pid_checker: Callable[[int], bool] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.service_name = service_name
        self.pid_provider = pid_provider or os.getpid
        self.command = command
        self.hostname = hostname or socket.gethostname()
        self.time_provider = time_provider or _utc_now
        self.pid_checker = pid_checker or _pid_is_running
        self._owner_pid: int | None = None

    def acquire(self) -> ProcessLockMetadata:
        metadata = ProcessLockMetadata(
            service_name=self.service_name,
            pid=self.pid_provider(),
            hostname=self.hostname,
            acquired_at=self.time_provider().isoformat(),
            command=self.command,
        )

        for _ in range(3):
            existing = self.read()
            if existing is not None and self.pid_checker(existing.pid):
                if existing.command and not _pid_command_contains(existing.pid, existing.command):
                    self.path.unlink(missing_ok=True)
                else:
                    raise ProcessAlreadyRunningError(existing)
            elif existing is not None:
                self.path.unlink(missing_ok=True)

            try:
                with self.path.open("x", encoding="utf-8") as handle:
                    json.dump(asdict(metadata), handle, ensure_ascii=True, indent=2)
                self._owner_pid = metadata.pid
                return metadata
            except FileExistsError:
                continue

        latest = self.read()
        if latest is not None:
            raise ProcessAlreadyRunningError(latest)
        raise ProcessLockError(f"Could not acquire lock for {self.service_name} at {self.path}.")

    def read(self) -> ProcessLockMetadata | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        try:
            return ProcessLockMetadata(
                service_name=str(payload.get("service_name") or self.service_name),
                pid=int(payload.get("pid") or 0),
                hostname=str(payload.get("hostname") or "unknown"),
                acquired_at=str(payload.get("acquired_at") or ""),
                command=payload.get("command"),
            )
        except (TypeError, ValueError):
            return None

    def release(self) -> None:
        current = self.read()
        if current is None:
            return
        owner_pid = self._owner_pid if self._owner_pid is not None else self.pid_provider()
        if current.pid == owner_pid and self.path.exists():
            self.path.unlink(missing_ok=True)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_is_running_windows(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _pid_is_running_windows(pid: int) -> bool:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=creation_flags,
        )
    except OSError:
        return False

    output = (result.stdout or "").strip()
    if not output or output.upper().startswith("INFO:"):
        return False
    return f'"{pid}"' in output


def _pid_command_contains(pid: int, command_hint: str) -> bool:
    if pid <= 0 or not command_hint:
        return False

    if os.name == "nt":
        command_line = _get_process_commandline_windows(pid)
    else:
        command_line = _get_process_commandline_posix(pid)

    if not command_line:
        return False

    return command_hint.lower() in command_line.lower()


def _get_process_commandline_windows(pid: int) -> str:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    script = f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine'
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
            creationflags=creation_flags,
        )
    except OSError:
        return ""

    return (result.stdout or "").strip()


def _get_process_commandline_posix(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""

    return (result.stdout or "").strip()
