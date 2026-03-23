from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sultan_engine_v2.core.doctrine import ControlState
from sultan_engine_v2.core.execution_models import ExecutionMode, TradeIntent, Venue
from sultan_engine_v2.runtime.execution_engine import SnapshotExecutionPlanner


@dataclass(slots=True)
class ManagerFinding:
    severity: str
    code: str
    message: str


@dataclass(slots=True)
class ManagerReport:
    created_at: str
    venue: Venue
    snapshot_path: str | None
    summary: str
    findings: list[ManagerFinding] = field(default_factory=list)
    ready_intents: list[TradeIntent] = field(default_factory=list)


class SultanAIManager:
    def __init__(self, report_path: str | Path | None = None) -> None:
        self.report_path = Path(report_path) if report_path else Path(__file__).resolve().parent / "reports" / "latest_manager_report.json"
        self.report_path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def load_snapshot(snapshot_path: str | Path) -> dict:
        return json.loads(Path(snapshot_path).read_text(encoding="utf-8"))

    def audit_snapshot(self, snapshot: dict, snapshot_path: str | None = None) -> ManagerReport:
        venue = self._coerce_venue(snapshot.get("venue"))
        findings: list[ManagerFinding] = []

        symbol = snapshot.get("symbol", {}).get("actual") or snapshot.get("symbol", {}).get("requested")
        if not symbol:
            findings.append(ManagerFinding("HIGH", "SYMBOL_MISSING", "Symbol resolution is missing in the runtime snapshot."))

        market = snapshot.get("market", {})
        if max(float(market.get("bid") or 0.0), float(market.get("ask") or 0.0), float(market.get("price") or 0.0), float(market.get("last") or 0.0)) <= 0:
            findings.append(ManagerFinding("HIGH", "MARKET_PRICE_EMPTY", "Market price is empty, execution must stay blocked."))

        market_time = int(market.get("time") or 0)
        if market_time:
            lag_seconds = int(datetime.now(timezone.utc).timestamp()) - market_time
            if lag_seconds > 1800:
                findings.append(ManagerFinding("MEDIUM", "MARKET_STALE", f"Market data is stale by {lag_seconds} seconds."))

        timeframes = snapshot.get("timeframes", {})
        unseeded = [timeframe for timeframe, node in timeframes.items() if not node.get("cmp")]
        if unseeded:
            findings.append(ManagerFinding("MEDIUM", "CMP_UNSEEDED", f"Timeframes without CMP: {', '.join(unseeded)}"))

        active_vr = [
            parent
            for parent, sequence in snapshot.get("sequences", {}).items()
            if sequence.get("control_state") == ControlState.UNDER_ATTACK.value
        ]
        if len(active_vr) >= 2:
            findings.append(ManagerFinding("MEDIUM", "MULTI_VR", f"Multiple parent sequences are under VR pressure: {', '.join(active_vr)}"))

        direction_chain = [
            timeframes[tf]["cmp"]
            for tf in ("D1", "H4", "H1", "M30", "M15", "M5")
            if tf in timeframes and timeframes[tf].get("cmp")
        ]
        split_count = sum(1 for index in range(1, len(direction_chain)) if direction_chain[index] != direction_chain[index - 1])
        if split_count >= 2:
            findings.append(ManagerFinding("INFO", "FRACTAL_SPLIT", f"Directional chain has {split_count} internal splits."))

        planner = SnapshotExecutionPlanner(default_venue=venue, execution_mode=ExecutionMode.PAPER)
        ready_intents = planner.plan(snapshot)
        if ready_intents:
            findings.append(ManagerFinding("INFO", "TRADE_READY", f"{len(ready_intents)} execution candidate(s) are ready for review."))
        else:
            findings.append(ManagerFinding("INFO", "NO_TRADE_READY", "No qualified CF recovery execution candidate is ready."))

        blocked_decisions = [
            decision
            for decision in snapshot.get("execution", {}).get("recent_decisions", [])
            if not decision.get("allowed", True)
        ]
        if blocked_decisions:
            reasons = sorted({decision.get("reason", "UNKNOWN") for decision in blocked_decisions})
            findings.append(
                ManagerFinding(
                    "INFO",
                    "EXECUTION_GUARD_ACTIVE",
                    f"Recent execution candidates were blocked by CFD guard: {', '.join(reasons)}",
                )
            )

        lifecycle_actions = snapshot.get("lifecycle", {}).get("recent_actions", [])
        if lifecycle_actions:
            findings.append(
                ManagerFinding(
                    "INFO",
                    "POSITION_LIFECYCLE_ACTIVE",
                    f"Recent lifecycle actions recorded: {len(lifecycle_actions)}",
                )
            )

        structure_statuses = snapshot.get("lifecycle", {}).get("position_structure", [])
        exit_now_statuses = [item for item in structure_statuses if item.get("status") == "EXIT_NOW"]
        grace_statuses = [item for item in structure_statuses if item.get("status") == "GRACE"]
        if exit_now_statuses:
            findings.append(
                ManagerFinding(
                    "HIGH",
                    "POSITION_STRUCTURE_EXIT",
                    "Open position(s) require immediate structural exit: "
                    + ", ".join(
                        f"#{item.get('ticket')} {item.get('parent_timeframe')}/{item.get('cf_timeframe')}"
                        for item in exit_now_statuses[:3]
                    ),
                )
            )
        if grace_statuses:
            findings.append(
                ManagerFinding(
                    "INFO",
                    "POSITION_STRUCTURE_GRACE",
                    "Open position(s) are still inside structural grace: "
                    + ", ".join(
                        f"#{item.get('ticket')} {item.get('parent_timeframe')}/{item.get('cf_timeframe')}"
                        for item in grace_statuses[:3]
                    ),
                )
            )

        summary = self._build_summary(symbol or "UNKNOWN", venue, findings, ready_intents)
        return ManagerReport(
            created_at=datetime.now(timezone.utc).isoformat(),
            venue=venue,
            snapshot_path=snapshot_path,
            summary=summary,
            findings=findings,
            ready_intents=ready_intents,
        )

    def write_report(self, report: ManagerReport) -> None:
        payload = {
            "created_at": report.created_at,
            "venue": report.venue,
            "snapshot_path": report.snapshot_path,
            "summary": report.summary,
            "findings": [asdict(item) for item in report.findings],
            "ready_intents": [asdict(item) for item in report.ready_intents],
        }
        self.report_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, default=str), encoding="utf-8")

    def render_text(self, report: ManagerReport) -> str:
        lines = [report.summary, ""]
        lines.append("Findings:")
        for finding in report.findings:
            lines.append(f"- [{finding.severity}] {finding.code}: {finding.message}")

        lines.append("")
        lines.append("Ready Intents:")
        if not report.ready_intents:
            lines.append("- None")
        else:
            for intent in report.ready_intents:
                lines.append(
                    f"- {intent.venue} {intent.symbol} {intent.direction} {intent.timeframe} entry={intent.entry_price:.5f} sl={intent.stop_loss:.5f} tp={intent.take_profit:.5f} rr={intent.metadata.get('rr_ratio')}"
                )
        return "\n".join(lines)

    def audit_snapshot_file(self, snapshot_path: str | Path) -> ManagerReport:
        payload = self.load_snapshot(snapshot_path)
        report = self.audit_snapshot(payload, snapshot_path=str(snapshot_path))
        self.write_report(report)
        return report

    @staticmethod
    def _coerce_venue(raw_venue: str | None) -> Venue:
        if not raw_venue:
            return Venue.MT5
        try:
            return Venue(raw_venue)
        except ValueError:
            return Venue.MT5

    @staticmethod
    def _build_summary(symbol: str, venue: Venue, findings: list[ManagerFinding], ready_intents: list[TradeIntent]) -> str:
        high_count = sum(1 for item in findings if item.severity == "HIGH")
        medium_count = sum(1 for item in findings if item.severity == "MEDIUM")
        return (
            f"Manager audit for {venue} {symbol}: {len(ready_intents)} ready intent(s), "
            f"{high_count} high issue(s), {medium_count} medium issue(s)."
        )