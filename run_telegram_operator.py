from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sultan_engine_v2.manager.telegram_operator import TelegramOperatorService
from sultan_engine_v2.runtime.env_loader import load_runtime_env
from sultan_engine_v2.runtime.process_lock import FileProcessLock, ProcessAlreadyRunningError

load_runtime_env(__file__)


def main() -> None:
    poll_timeout = int(os.getenv("SULTAN_OPERATOR_POLL_TIMEOUT_SECONDS", "20"))
    idle_sleep = float(os.getenv("SULTAN_OPERATOR_IDLE_SLEEP_SECONDS", "1"))
    service = TelegramOperatorService()
    if not service.is_enabled:
        print(
            "Telegram operator is not configured. Set SULTAN_TELEGRAM_ENABLED=1 with bot token/chat id env vars, or provide the legacy backup config path."
        )
        return

    runtime_dir = Path(__file__).resolve().parent / "runtime"
    lock = FileProcessLock(
        runtime_dir / "state" / "locks" / "telegram_operator.lock",
        service_name="TELEGRAM_OPERATOR",
        command="run_telegram_operator.py",
    )
    try:
        lock.acquire()
    except ProcessAlreadyRunningError as error:
        print(f"Telegram operator already running with pid={error.metadata.pid} on {error.metadata.hostname}.")
        return

    print("Sultan Telegram operator running...")
    try:
        service.run_forever(timeout_seconds=poll_timeout, idle_sleep_seconds=idle_sleep)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
