from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sultan_engine_v2.manager.ai_manager import SultanAIManager
from sultan_engine_v2.manager.telegram_reporter import TelegramOperatorReporter


def main() -> None:
    default_snapshot = Path(__file__).resolve().parent / "runtime" / "state" / "latest_snapshot.json"
    snapshot_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_snapshot
    manager = SultanAIManager()
    reporter = TelegramOperatorReporter()
    report = manager.audit_snapshot_file(snapshot_path)
    rendered = manager.render_text(report)
    if reporter.is_enabled:
        reporter.send_manager_report(report, rendered)
    print(rendered)


if __name__ == "__main__":
    main()