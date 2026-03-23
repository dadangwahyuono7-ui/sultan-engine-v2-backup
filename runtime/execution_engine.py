from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from sultan_engine_v2.core.doctrine import ControlState
from sultan_engine_v2.core.execution_models import ExecutionMode, ExecutionResult, TradeIntent, Venue
from sultan_engine_v2.core.models import TradeDirection
from sultan_engine_v2.core.timeframe_relations import CF_HIGH_RISK_BY_PARENT, CF_LOW_RISK_BY_PARENT
from sultan_engine_v2.runtime.file_utils import atomic_write_json


@dataclass(slots=True)
class IntentDecision:
    intent_key: str
    venue: Venue
    symbol: str
    timeframe: str
    direction: TradeDirection
    execution_mode: ExecutionMode
    allowed: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


class SnapshotExecutionPlanner:
    def __init__(
        self,
        default_venue: Venue = Venue.MT5,
        execution_mode: ExecutionMode = ExecutionMode.PAPER,
        risk_fraction: float = 0.005,
        min_rr: float = 1.1,
    ) -> None:
        self.default_venue = default_venue
        self.execution_mode = execution_mode
        self.risk_fraction = risk_fraction
        self.min_rr = min_rr

    @staticmethod
    def load_snapshot(snapshot_path: str | Path) -> dict:
        return json.loads(Path(snapshot_path).read_text(encoding="utf-8"))

    def plan(self, snapshot: dict) -> list[TradeIntent]:
        venue = self._coerce_venue(snapshot.get("venue"))
        symbol = snapshot.get("symbol", {}).get("actual") or snapshot.get("symbol", {}).get("requested")
        if not symbol:
            return []

        market = snapshot.get("market", {})
        timeframes = snapshot.get("timeframes", {})
        sequences = snapshot.get("sequences", {})

        intents: list[TradeIntent] = []
        for parent, sequence in sequences.items():
            if sequence.get("control_state") != ControlState.RECOVERED_BY_CF.value:
                continue

            cf_timeframe = sequence.get("last_cf_timeframe")
            direction_value = sequence.get("direction")
            if not cf_timeframe or not direction_value:
                continue

            cf_node = timeframes.get(cf_timeframe)
            if not cf_node:
                continue

            direction = TradeDirection(direction_value)
            intent = self._build_intent(
                venue=venue,
                symbol=symbol,
                market=market,
                timeframes=timeframes,
                parent=parent,
                cf_timeframe=cf_timeframe,
                direction=direction,
                breakout_level=cf_node.get("breakout_level") or 0.0,
                entry_kind=(
                    "LOW_RISK_CF"
                    if cf_timeframe == CF_LOW_RISK_BY_PARENT.get(parent)
                    else "HIGH_RISK_CF"
                    if cf_timeframe == CF_HIGH_RISK_BY_PARENT.get(parent)
                    else "CF"
                ),
                confirmation_timeframe=(parent if cf_timeframe == CF_LOW_RISK_BY_PARENT.get(parent) else CF_LOW_RISK_BY_PARENT.get(parent)),
                invalidation_timeframe=(cf_timeframe if cf_timeframe == CF_LOW_RISK_BY_PARENT.get(parent) else CF_LOW_RISK_BY_PARENT.get(parent)),
                order_reason=f"{parent} recovered by CF on {cf_timeframe}",
                setup_focus_status=(snapshot.get("setup_focus") or {}).get("status"),
            )
            if intent is not None:
                intents.append(intent)

        cheat_code = snapshot.get("cheat_code") or {}
        if cheat_code.get("status") == "CF_CONFIRMED":
            direction_value = cheat_code.get("direction")
            cf_timeframe = str(cheat_code.get("execution_timeframe") or "M5")
            parent = str(cheat_code.get("parent_timeframe") or "H4")
            if direction_value and cf_timeframe in timeframes:
                intent = self._build_intent(
                    venue=venue,
                    symbol=symbol,
                    market=market,
                    timeframes=timeframes,
                    parent=parent,
                    cf_timeframe=cf_timeframe,
                    direction=TradeDirection(direction_value),
                    breakout_level=timeframes.get(cf_timeframe, {}).get("breakout_level") or 0.0,
                    entry_kind="CHEAT_CODE",
                    confirmation_timeframe=str(cheat_code.get("trigger_timeframe") or "M30"),
                    invalidation_timeframe=str(cheat_code.get("target_timeframe") or cheat_code.get("trigger_timeframe") or "M30"),
                    order_reason="H4->M30->M5 cheat code confirmed",
                    setup_focus_status=(snapshot.get("setup_focus") or {}).get("status"),
                    intent_suffix="CHEAT",
                    target_timeframe=str(cheat_code.get("target_timeframe") or "M30"),
                )
                if intent is not None:
                    intents.append(intent)

        return intents

    def _build_intent(
        self,
        *,
        venue: Venue,
        symbol: str,
        market: dict,
        timeframes: dict,
        parent: str,
        cf_timeframe: str,
        direction: TradeDirection,
        breakout_level: float,
        entry_kind: str,
        confirmation_timeframe: str | None,
        invalidation_timeframe: str | None,
        order_reason: str,
        setup_focus_status: str | None,
        intent_suffix: str = "",
        target_timeframe: str | None = None,
    ) -> TradeIntent | None:
        cf_node = timeframes.get(cf_timeframe)
        if not cf_node:
            return None
        entry_price = self._get_entry_price(direction, market)
        stop_loss = self._get_stop_price(direction, cf_node)
        target_tf = target_timeframe or cf_node.get("target_tf") or parent
        target_node = timeframes.get(target_tf) or timeframes.get(parent)
        take_profit = self._get_target_price(direction, target_node)

        if not self._is_valid_bracket(direction, entry_price, stop_loss, take_profit):
            return None

        risk_distance = abs(entry_price - stop_loss)
        reward_distance = abs(take_profit - entry_price)
        rr_ratio = reward_distance / risk_distance if risk_distance else 0.0
        if rr_ratio < self.min_rr:
            return None

        intent_key = f"{venue}:{symbol}:{parent}:{cf_timeframe}:{direction}:{breakout_level}:{intent_suffix}"
        return TradeIntent(
            intent_key=intent_key,
            venue=venue,
            symbol=symbol,
            timeframe=cf_timeframe,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_fraction=self.risk_fraction,
            order_reason=order_reason,
            execution_mode=self.execution_mode,
            metadata={
                "parent_timeframe": parent,
                "target_timeframe": target_tf,
                "rr_ratio": round(rr_ratio, 4),
                "breakout_level": breakout_level,
                "entry_kind": entry_kind,
                "low_risk_cf_timeframe": CF_LOW_RISK_BY_PARENT.get(parent),
                "high_risk_cf_timeframe": CF_HIGH_RISK_BY_PARENT.get(parent),
                "confirmation_timeframe": confirmation_timeframe,
                "invalidation_timeframe": invalidation_timeframe,
                "setup_focus_status": setup_focus_status,
            },
        )

    def _coerce_venue(self, raw_venue: str | None) -> Venue:
        if not raw_venue:
            return self.default_venue
        try:
            return Venue(raw_venue)
        except ValueError:
            return self.default_venue

    @staticmethod
    def _get_entry_price(direction: TradeDirection, market: dict) -> float:
        if direction == TradeDirection.BUY:
            return float(market.get("ask") or market.get("price") or market.get("last") or market.get("bid") or 0.0)
        return float(market.get("bid") or market.get("price") or market.get("last") or market.get("ask") or 0.0)

    @staticmethod
    def _get_stop_price(direction: TradeDirection, node: dict) -> float:
        if direction == TradeDirection.BUY:
            return float(node.get("support") or 0.0)
        return float(node.get("resistance") or 0.0)

    @staticmethod
    def _get_target_price(direction: TradeDirection, node: dict | None) -> float:
        if not node:
            return 0.0
        if direction == TradeDirection.BUY:
            return float(node.get("resistance") or 0.0)
        return float(node.get("support") or 0.0)

    @staticmethod
    def _is_valid_bracket(
        direction: TradeDirection,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
    ) -> bool:
        if entry_price <= 0 or stop_loss <= 0 or take_profit <= 0:
            return False
        if direction == TradeDirection.BUY:
            return stop_loss < entry_price < take_profit
        return take_profit < entry_price < stop_loss


class JsonExecutionLedger:
    def __init__(self, ledger_path: str | Path | None = None) -> None:
        self.ledger_path = Path(ledger_path) if ledger_path else Path(__file__).resolve().parent / "logs" / "execution_ledger.json"
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.entries: list[dict] = []
        self.seen_intent_keys: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.ledger_path.exists():
            self.entries = []
            self.seen_intent_keys = set()
            return

        try:
            self.entries = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.entries = []
        self._rebuild_success_index()

    def _rebuild_success_index(self) -> None:
        self.seen_intent_keys = {
            entry.get("intent_key")
            for entry in self.entries
            if entry.get("intent_key") and bool(entry.get("success"))
        }

    def seen(self, intent_key: str) -> bool:
        return intent_key in self.seen_intent_keys

    def mark(self, result: ExecutionResult) -> None:
        payload = {"recorded_at": datetime.now(timezone.utc).isoformat(), **asdict(result)}
        self.entries.append(payload)
        if result.success:
            self.seen_intent_keys.add(result.intent_key)
        atomic_write_json(self.ledger_path, self.entries, default=str)


class PaperExecutor:
    def execute_intent(self, intent: TradeIntent) -> ExecutionResult:
        return ExecutionResult(
            intent_key=intent.intent_key,
            venue=intent.venue,
            symbol=intent.symbol,
            success=True,
            execution_mode=intent.execution_mode,
            message="Paper execution only",
            executed_price=intent.entry_price,
            quantity=intent.quantity,
            metadata=intent.metadata,
        )


class ExecutionOrchestrator:
    def __init__(self, planner: SnapshotExecutionPlanner, executors: dict[Venue, object], ledger: JsonExecutionLedger | None = None) -> None:
        self.planner = planner
        self.executors = executors
        self.ledger = ledger or JsonExecutionLedger()

    def execute_snapshot(self, snapshot: dict) -> list[ExecutionResult]:
        results: list[ExecutionResult] = []
        for intent in self.planner.plan(snapshot):
            if self.ledger.seen(intent.intent_key):
                continue

            executor = self.executors.get(intent.venue)
            if executor is None:
                result = ExecutionResult(
                    intent_key=intent.intent_key,
                    venue=intent.venue,
                    symbol=intent.symbol,
                    success=False,
                    execution_mode=intent.execution_mode,
                    message=f"No executor registered for {intent.venue}",
                    metadata=intent.metadata,
                )
            else:
                result = executor.execute_intent(intent)

            self.ledger.mark(result)
            results.append(result)

        return results


class RuntimeAutoExecutor:
    def __init__(
        self,
        venue: Venue,
        execution_mode: ExecutionMode,
        executors: dict[Venue, object],
        ledger_path: str | Path | None = None,
        enabled: bool = True,
        risk_fraction: float = 0.005,
        min_rr: float = 1.1,
        guard: object | None = None,
    ) -> None:
        self.venue = venue
        self.execution_mode = execution_mode
        self.enabled = enabled
        self.guard = guard
        self.ledger = JsonExecutionLedger(ledger_path)
        self.planner = SnapshotExecutionPlanner(
            default_venue=venue,
            execution_mode=execution_mode,
            risk_fraction=risk_fraction,
            min_rr=min_rr,
        )
        self.orchestrator = ExecutionOrchestrator(
            planner=self.planner,
            executors=executors,
            ledger=self.ledger,
        )
        self.recent_results: deque[ExecutionResult] = deque(maxlen=20)
        self.recent_decisions: deque[IntentDecision] = deque(maxlen=40)
        self.last_cycle_decisions: list[IntentDecision] = []

    def execute(self, snapshot: dict) -> list[ExecutionResult]:
        if not self.enabled:
            self.last_cycle_decisions = []
            return []

        results: list[ExecutionResult] = []
        cycle_decisions: list[IntentDecision] = []
        for intent in self.planner.plan(snapshot):
            if self.ledger.seen(intent.intent_key):
                decision = self._build_decision(intent, allowed=False, reason="ALREADY_EXECUTED")
                cycle_decisions.append(decision)
                self.recent_decisions.append(decision)
                continue

            if self.guard is not None:
                evaluation = self.guard.evaluate(snapshot, intent, self.ledger)
                decision = self._build_decision(
                    intent,
                    allowed=evaluation.allowed,
                    reason=evaluation.reason,
                    details=evaluation.details,
                )
            else:
                decision = self._build_decision(intent, allowed=True, reason="ALLOWED")

            cycle_decisions.append(decision)
            self.recent_decisions.append(decision)
            if not decision.allowed:
                continue

            executor = self.orchestrator.executors.get(intent.venue)
            if executor is None:
                result = ExecutionResult(
                    intent_key=intent.intent_key,
                    venue=intent.venue,
                    symbol=intent.symbol,
                    success=False,
                    execution_mode=intent.execution_mode,
                    message=f"No executor registered for {intent.venue}",
                    metadata=intent.metadata,
                )
            else:
                result = executor.execute_intent(intent)

            self.ledger.mark(result)
            results.append(result)

        self.last_cycle_decisions = cycle_decisions
        for result in results:
            self.recent_results.append(result)
        return results

    def _build_decision(
        self,
        intent: TradeIntent,
        allowed: bool,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> IntentDecision:
        return IntentDecision(
            intent_key=intent.intent_key,
            venue=intent.venue,
            symbol=intent.symbol,
            timeframe=intent.timeframe,
            direction=intent.direction,
            execution_mode=intent.execution_mode,
            allowed=allowed,
            reason=reason,
            details=details or {},
        )
