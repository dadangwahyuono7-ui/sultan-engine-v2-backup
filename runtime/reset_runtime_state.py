from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sultan_engine_v2.runtime.file_utils import atomic_write_json
from sultan_engine_v2.runtime.process_lock import FileProcessLock, ProcessLockMetadata


@dataclass(slots=True)
class LockInspection:
    path: Path
    metadata: ProcessLockMetadata | None
    is_active: bool
    reason: str


@dataclass(slots=True)
class ResetPlan:
    runtime_dir: Path
    archive_dir: Path
    target_paths: list[Path]
    active_locks: list[LockInspection]
    stale_locks: list[LockInspection]
    mt5_symbol: str
    binance_symbol: str
    include_operator_state: bool = False
    include_tradingview_state: bool = False


@dataclass(slots=True)
class ResetExecution:
    archive_dir: Path
    moved_paths: list[str]
    skipped_paths: list[str]
    manifest_path: Path


def inspect_lock_files(lock_dir: Path, *, pid_checker: Callable[[int], bool] | None = None) -> list[LockInspection]:
    inspections: list[LockInspection] = []
    if not lock_dir.exists():
        return inspections

    for lock_path in sorted(lock_dir.glob("*.lock")):
        lock = FileProcessLock(lock_path, service_name=lock_path.stem, pid_checker=pid_checker)
        metadata = lock.read()
        if metadata is None:
            inspections.append(LockInspection(path=lock_path, metadata=None, is_active=False, reason="unreadable"))
            continue

        is_active = lock.pid_checker(metadata.pid)
        inspections.append(
            LockInspection(
                path=lock_path,
                metadata=metadata,
                is_active=is_active,
                reason="active" if is_active else "stale",
            )
        )
    return inspections


def build_reset_plan(
    *,
    runtime_dir: str | Path,
    mt5_symbol: str = "XAUUSD",
    binance_symbol: str = "BTCUSDT",
    include_operator_state: bool = False,
    include_tradingview_state: bool = False,
    clear_locks: bool = True,
    timestamp: datetime | None = None,
    pid_checker: Callable[[int], bool] | None = None,
) -> ResetPlan:
    runtime_root = Path(runtime_dir)
    state_dir = runtime_root / "state"
    lock_dir = state_dir / "locks"
    inspections = inspect_lock_files(lock_dir, pid_checker=pid_checker)
    active_locks = [item for item in inspections if item.is_active]
    stale_locks = [item for item in inspections if not item.is_active]

    mt5_slug = mt5_symbol.lower()
    binance_slug = binance_symbol.lower()
    target_paths = [
        state_dir / "latest_snapshot.json",
        state_dir / f"mt5_{mt5_slug}_service_status.json",
        state_dir / f"vps_stack_{mt5_slug}_status.json",
        state_dir / f"production_stack_{mt5_slug}_status.json",
        state_dir / f"production_guard_{mt5_slug}_status.json",
        state_dir / f"performance_mt5_{mt5_slug}.json",
        state_dir / f"binance_{binance_slug}_snapshot.json",
        state_dir / f"binance_{binance_slug}_service_status.json",
        state_dir / f"binance_stack_{binance_slug}_status.json",
        state_dir / f"binance_production_{binance_slug}_status.json",
        state_dir / f"production_guard_{binance_slug}_status.json",
        state_dir / f"performance_binance_{binance_slug}.json",
        state_dir / f"binance_{binance_slug}_paper_account.json",
        runtime_root / "logs" / f"mt5_{mt5_slug}_execution_ledger.json",
        runtime_root / "logs" / f"binance_{binance_slug}_execution_ledger.json",
        runtime_root / "history" / f"performance_mt5_{mt5_slug}.jsonl",
        runtime_root / "history" / f"performance_binance_{binance_slug}.jsonl",
    ]

    if include_operator_state:
        target_paths.extend(
            [
                state_dir / "operator_agent_threads.json",
                state_dir / "operator_control.json",
                state_dir / "telegram_operator_cursor.json",
            ]
        )

    if include_tradingview_state:
        target_paths.extend(sorted(state_dir.glob("tradingview_replay_*.json")))

    if clear_locks:
        target_paths.extend(item.path for item in stale_locks)

    archive_stamp = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_dir = runtime_root / "archive" / f"reset_{archive_stamp}_{mt5_slug}_{binance_slug}"

    return ResetPlan(
        runtime_dir=runtime_root,
        archive_dir=archive_dir,
        target_paths=_dedupe_paths(target_paths),
        active_locks=active_locks,
        stale_locks=stale_locks,
        mt5_symbol=mt5_symbol,
        binance_symbol=binance_symbol,
        include_operator_state=include_operator_state,
        include_tradingview_state=include_tradingview_state,
    )


def execute_reset_plan(plan: ResetPlan) -> ResetExecution:
    if plan.active_locks:
        raise RuntimeError("Cannot reset runtime state while active Sultan processes are still running.")

    moved_paths: list[str] = []
    skipped_paths: list[str] = []

    plan.archive_dir.mkdir(parents=True, exist_ok=True)
    for target_path in plan.target_paths:
        relative = _safe_relative_path(target_path, plan.runtime_dir)
        if not target_path.exists():
            skipped_paths.append(relative)
            continue

        destination = plan.archive_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target_path), str(destination))
        moved_paths.append(relative)

    for directory in (
        plan.runtime_dir / "state",
        plan.runtime_dir / "state" / "locks",
        plan.runtime_dir / "logs",
        plan.runtime_dir / "history",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    manifest_path = plan.archive_dir / "reset_manifest.json"
    atomic_write_json(
        manifest_path,
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mt5_symbol": plan.mt5_symbol,
            "binance_symbol": plan.binance_symbol,
            "include_operator_state": plan.include_operator_state,
            "include_tradingview_state": plan.include_tradingview_state,
            "moved_paths": moved_paths,
            "skipped_paths": skipped_paths,
            "active_lock_count": len(plan.active_locks),
            "stale_lock_count": len(plan.stale_locks),
        },
    )
    return ResetExecution(
        archive_dir=plan.archive_dir,
        moved_paths=moved_paths,
        skipped_paths=skipped_paths,
        manifest_path=manifest_path,
    )


def describe_active_locks(active_locks: list[LockInspection]) -> list[str]:
    lines: list[str] = []
    for item in active_locks:
        metadata = item.metadata
        if metadata is None:
            lines.append(f"{item.path.name} | status=UNKNOWN")
            continue
        lines.append(
            f"{item.path.name} | pid={metadata.pid} | service={metadata.service_name} | host={metadata.hostname}"
        )
    return lines


def _safe_relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def render_reset_manifest(manifest_path: Path) -> dict[str, object]:
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
