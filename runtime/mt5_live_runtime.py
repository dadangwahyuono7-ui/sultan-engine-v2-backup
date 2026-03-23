from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from sultan_engine_v2.adapters.mt5.execution.guard import Mt5CfdExecutionGuard, load_cfd_guard_config_from_env
from sultan_engine_v2.adapters.mt5.execution.position_manager import (
    Mt5PositionLifecycleManager,
    PositionManagementAction,
    load_position_lifecycle_config_from_env,
)
from sultan_engine_v2.adapters.mt5.execution.trader import Mt5ExecutionClient
from sultan_engine_v2.adapters.mt5.session.client import ActiveMt5Session, Mt5Tick
from sultan_engine_v2.core.breakout_detector import bootstrap_latest_breakout, update_breakout_state
from sultan_engine_v2.core.execution_models import ExecutionMode, ExecutionResult, Venue
from sultan_engine_v2.core.state_engine import StateEvent, SultanStateEngine
from sultan_engine_v2.manager.operator_control import JsonOperatorControlStore
from sultan_engine_v2.manager.telegram_reporter import TelegramOperatorReporter
from sultan_engine_v2.runtime.file_utils import atomic_write_json
from sultan_engine_v2.runtime.execution_engine import IntentDecision, RuntimeAutoExecutor


DEFAULT_TIMEFRAMES = ("D1", "H4", "H1", "M30", "M15", "M5")


@dataclass(slots=True)
class RuntimePaths:
    state_file: Path
    journal_file: Path


class Mt5LiveRuntime:
    def __init__(
        self,
        base_symbol: str = "XAUUSD",
        timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
        lookback: int = 250,
        break_buffer: float = 0.0,
        execution_mode: ExecutionMode = ExecutionMode.PAPER,
        auto_execute: bool = True,
    ) -> None:
        self.base_symbol = base_symbol
        self.timeframes = timeframes
        self.lookback = lookback
        self.break_buffer = break_buffer
        self.execution_mode = execution_mode
        self.auto_execute = auto_execute
        self.session = ActiveMt5Session()
        self.engine = SultanStateEngine(timeframes=timeframes)
        self.breakout_states = {}
        self.last_closed_timestamps: dict[str, int] = {}
        self.logs: deque[str] = deque(maxlen=50)
        self.state_events: deque[StateEvent] = deque(maxlen=50)
        self.execution_results: deque[ExecutionResult] = deque(maxlen=20)
        self.execution_decisions: deque[IntentDecision] = deque(maxlen=40)
        self.position_actions: deque[PositionManagementAction] = deque(maxlen=30)
        self.symbol_resolution = None
        self.actual_symbol: str | None = None
        self.tick = Mt5Tick()
        self.recent_ticks: deque[dict[str, float | int]] = deque(maxlen=24)
        self.bootstrapped = False
        self.notified_alert_keys: deque[str] = deque()
        self.notified_alert_lookup: set[str] = set()

        runtime_dir = Path(__file__).resolve().parent
        self.paths = RuntimePaths(
            state_file=runtime_dir / "state" / "latest_snapshot.json",
            journal_file=runtime_dir / "journal" / f"journal_{base_symbol.lower()}.jsonl",
        )
        self.execution_guard = Mt5CfdExecutionGuard(config=load_cfd_guard_config_from_env())
        self.position_manager = Mt5PositionLifecycleManager(
            config=load_position_lifecycle_config_from_env(),
            state_path=runtime_dir / "logs" / f"mt5_{base_symbol.lower()}_position_lifecycle.json",
        )
        self.operator_controls = JsonOperatorControlStore(runtime_dir / "state" / "operator_control.json")
        self.operator_state = self.operator_controls.load()
        self._last_operator_marker: tuple[str | None, str, bool] | None = None
        self.telegram_reporter = TelegramOperatorReporter()
        self.auto_executor = RuntimeAutoExecutor(
            venue=Venue.MT5,
            execution_mode=execution_mode,
            executors={Venue.MT5: Mt5ExecutionClient()},
            ledger_path=runtime_dir / "logs" / f"mt5_{base_symbol.lower()}_execution_ledger.json",
            enabled=auto_execute,
            guard=self.execution_guard,
        )
        self._apply_operator_control(log_change=False)

    def log(self, message: str) -> None:
        self.logs.append(f"{datetime.now().strftime('%H:%M:%S')} | {message}")

    def _record_tick(self, tick: Mt5Tick) -> None:
        if tick.timestamp <= 0:
            return

        bid = float(tick.bid or 0.0)
        ask = float(tick.ask or 0.0)
        last_entry = self.recent_ticks[-1] if self.recent_ticks else None
        if last_entry and int(last_entry.get("time") or 0) == tick.timestamp and float(last_entry.get("bid") or 0.0) == bid and float(last_entry.get("ask") or 0.0) == ask:
            return

        self.recent_ticks.append(
            {
                "time": int(tick.timestamp),
                "bid": bid,
                "ask": ask,
                "mid": (bid + ask) / 2 if bid > 0 and ask > 0 else bid or ask,
                "spread": max(ask - bid, 0.0) if ask > 0 and bid > 0 else 0.0,
            }
        )

    def _append_journal(self, entry: dict) -> None:
        with self.paths.journal_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")

    def connect(self) -> bool:
        snapshot = self.session.connect()
        if not snapshot.connected:
            self.log(f"MT5 connect failed | {snapshot.error}")
            return False

        self.symbol_resolution = self.session.resolve_symbol(self.base_symbol)
        self.actual_symbol = self.symbol_resolution.actual
        if not self.actual_symbol:
            self.log(f"Symbol resolution failed | requested={self.base_symbol}")
            return False

        self.log(
            f"MT5 connected | login={snapshot.login} | server={snapshot.server or 'UNKNOWN'} | symbol={self.actual_symbol}"
        )
        self.log(f"Execution armed | mode={self.execution_mode} | auto_execute={self.auto_execute}")
        self.log(
            f"Operator control | paused={self.operator_state.paused} | new_entries={self.operator_state.allow_new_entries}"
        )
        self.log(f"Telegram alerts | enabled={self.telegram_reporter.is_enabled}")
        return True

    def bootstrap(self) -> bool:
        if not self.session.connected and not self.connect():
            return False

        if not self.actual_symbol:
            return False

        seeded_any = False
        for timeframe in self.timeframes:
            bars = self.session.fetch_closed_bars(self.actual_symbol, timeframe, self.lookback)
            if len(bars) < 3:
                self.log(f"Bootstrap skipped | tf={timeframe} | insufficient bars")
                continue

            state = bootstrap_latest_breakout(timeframe, bars, self.break_buffer)
            self.breakout_states[timeframe] = state
            self.last_closed_timestamps[timeframe] = bars[-1].timestamp or 0

            if state.is_bootstrapped:
                seeded_any = True
                events = self.engine.seed_timeframe(state)
                self._record_events(events)
                self.log(f"Bootstrap | tf={timeframe} | cmp={state.active_cmp or 'NONE'}")
            else:
                self.log(f"Bootstrap | tf={timeframe} | no valid breakout found yet")

        self.tick = self.session.fetch_tick(self.actual_symbol)
        self._record_tick(self.tick)
        self.bootstrapped = seeded_any
        if seeded_any:
            self._record_events(self.engine.rebuild_sequences_from_current_nodes())
        self._finalize_cycle(execute_enabled=seeded_any)
        return seeded_any

    def _record_events(self, events: list[StateEvent]) -> None:
        for event in events:
            self.state_events.append(event)
            self.log(event.message)
            self._append_journal(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": event.kind,
                    "timeframe": event.timeframe,
                    "parent_timeframe": event.parent_timeframe,
                    "direction": event.direction,
                    "message": event.message,
                    "symbol": self.actual_symbol,
                }
            )
            if event.kind in {"CMP_CHANGED", "VR_ACTIVE", "CF_CONFIRMED"}:
                alert_key = f"STATE:{event.kind}:{event.parent_timeframe or '-'}:{event.timeframe}:{event.event_timestamp or 0}"
                self._send_alert_once(
                    alert_key,
                    lambda event=event: self.telegram_reporter.send_state_event(event, symbol=self.actual_symbol),
                )

    def _record_execution_results(self, results: list[ExecutionResult]) -> None:
        for result in results:
            self.execution_results.append(result)
            self.log(
                f"Execution {result.execution_mode} | {result.symbol} | success={result.success} | {result.message}"
            )
            self._append_journal(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "EXECUTION_RESULT",
                    "venue": result.venue,
                    "symbol": result.symbol,
                    "intent_key": result.intent_key,
                    "success": result.success,
                    "execution_mode": result.execution_mode,
                    "order_id": result.order_id,
                    "executed_price": result.executed_price,
                    "quantity": result.quantity,
                    "message": result.message,
                }
            )
            self._send_alert_once(f"RESULT:{result.intent_key}:{result.success}", lambda result=result: self.telegram_reporter.send_execution_result(result))

    def _record_execution_decisions(self, decisions: list[IntentDecision]) -> None:
        for decision in decisions:
            self.execution_decisions.append(decision)
            if decision.allowed:
                continue
            self.log(f"Guard blocked | {decision.symbol} | {decision.reason}")
            self._append_journal(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "EXECUTION_BLOCKED",
                    "venue": decision.venue,
                    "symbol": decision.symbol,
                    "intent_key": decision.intent_key,
                    "reason": decision.reason,
                    "details": decision.details,
                }
            )
            if decision.reason != "ALREADY_EXECUTED":
                alert_key = f"BLOCK:{decision.intent_key}:{decision.reason}"
                self._send_alert_once(alert_key, lambda decision=decision: self.telegram_reporter.send_execution_block(decision))

    def _record_position_actions(self, actions: list[PositionManagementAction]) -> None:
        for action in actions:
            self.position_actions.append(action)
            self.log(f"Lifecycle {action.action} | {action.symbol} | success={action.success} | {action.message}")
            self._append_journal(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "POSITION_LIFECYCLE",
                    "ticket": action.ticket,
                    "symbol": action.symbol,
                    "action": action.action,
                    "success": action.success,
                    "message": action.message,
                    "details": action.details,
                }
            )

    def _send_alert_once(self, key: str, sender: Callable[[], bool]) -> None:
        if key in self.notified_alert_lookup:
            return
        if sender():
            if len(self.notified_alert_keys) >= 100:
                removed = self.notified_alert_keys.popleft()
                self.notified_alert_lookup.discard(removed)
            self.notified_alert_lookup.add(key)
            self.notified_alert_keys.append(key)

    def _apply_operator_control(self, log_change: bool = True) -> None:
        latest_state = self.operator_controls.load()
        marker = (latest_state.last_command_at, latest_state.last_command, latest_state.paused)
        if log_change and marker != self._last_operator_marker and latest_state.last_command_at:
            if latest_state.paused:
                self.log(f"Operator pause | entries blocked | {latest_state.pause_reason or latest_state.last_message or '-'}")
            else:
                self.log(
                    f"Operator command | {latest_state.last_command} | entries={latest_state.allow_new_entries}"
                )

        self.operator_state = latest_state
        self.auto_executor.enabled = bool(self.auto_execute and latest_state.allow_new_entries)
        self._last_operator_marker = marker

    def _finalize_cycle(self, execute_enabled: bool) -> dict:
        self._apply_operator_control()
        snapshot = self.snapshot_dict()
        if execute_enabled and self.bootstrapped:
            results = self.auto_executor.execute(snapshot)
            decisions = self.auto_executor.last_cycle_decisions
            if decisions:
                self._record_execution_decisions(decisions)
            if results:
                self._record_execution_results(results)
        if self.bootstrapped and self.actual_symbol:
            snapshot = self.snapshot_dict()
            actions = self.position_manager.manage_symbol(self.actual_symbol, self.execution_mode, snapshot=snapshot)
            if actions:
                self._record_position_actions(actions)
        snapshot = self.snapshot_dict()
        self.write_snapshot(snapshot)
        return snapshot

    def poll(self) -> dict:
        refreshed = self.session.refresh()
        if not refreshed.connected:
            self.bootstrapped = False
            return self._finalize_cycle(execute_enabled=False)

        if not self.actual_symbol or not self.bootstrapped:
            self.bootstrap()
            return self.snapshot_dict()

        self.tick = self.session.fetch_tick(self.actual_symbol)
        self._record_tick(self.tick)
        state_changed = False
        for timeframe in self.timeframes:
            bars = self.session.fetch_closed_bars(self.actual_symbol, timeframe, self.lookback)
            if len(bars) < 3:
                continue

            last_closed_ts = bars[-1].timestamp or 0
            previous_timestamp = self.last_closed_timestamps.get(timeframe)
            if previous_timestamp == last_closed_ts:
                continue

            self.last_closed_timestamps[timeframe] = last_closed_ts
            previous_state = self.breakout_states.get(timeframe)
            if previous_state is None:
                seeded = bootstrap_latest_breakout(timeframe, bars, self.break_buffer)
                self.breakout_states[timeframe] = seeded
                self._record_events(self.engine.seed_timeframe(seeded))
                state_changed = True
                continue

            updated_state = update_breakout_state(previous_state, bars, self.break_buffer)
            self.breakout_states[timeframe] = updated_state

            if updated_state.changed:
                events = self.engine.apply_breakout(updated_state)
                self._record_events(events)
                state_changed = True

        return self._finalize_cycle(execute_enabled=state_changed)

    def snapshot_dict(self) -> dict:
        snapshot = self.engine.snapshot()
        mt5_snapshot = self.session.snapshot
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "venue": "MT5",
            "symbol": {
                "requested": self.base_symbol,
                "actual": self.actual_symbol,
                "resolution_source": getattr(self.symbol_resolution, "source", "unknown"),
            },
            "mt5": asdict(mt5_snapshot),
            "market": {
                "bid": self.tick.bid,
                "ask": self.tick.ask,
                "last": self.tick.last,
                "mid": ((self.tick.bid + self.tick.ask) / 2) if self.tick.bid and self.tick.ask else (self.tick.bid or self.tick.ask or self.tick.last),
                "spread": max((self.tick.ask or 0.0) - (self.tick.bid or 0.0), 0.0) if self.tick.ask and self.tick.bid else 0.0,
                "time": self.tick.timestamp,
            },
            "footprint": {
                "recent_ticks": list(self.recent_ticks),
            },
            "timeframes": {
                timeframe: {
                    "cmp": node.cmp_direction,
                    "role": node.role_to_parent,
                    "domain": node.setup_domain,
                    "target_tf": node.target_timeframe,
                    "cmp_timestamp": node.cmp_timestamp,
                    "support": node.support,
                    "resistance": node.resistance,
                    "breakout_level": node.breakout_level,
                }
                for timeframe, node in snapshot.nodes.items()
            },
            "sequences": {
                parent: {
                    "direction": sequence.parent_direction,
                    "parent_breakout_timestamp": sequence.parent_breakout_timestamp,
                    "vr_child": sequence.vr_child_timeframe,
                    "vr_active": sequence.vr_active,
                    "vr_started_at": sequence.vr_started_at,
                    "cf_count": sequence.cf_count,
                    "control_state": sequence.control_state,
                    "active_controller": sequence.active_controller,
                    "last_cf_timeframe": sequence.last_cf_timeframe,
                    "last_cf_timestamp": sequence.last_cf_timestamp,
                }
                for parent, sequence in snapshot.sequences.items()
            },
            "setup_focus": snapshot.setup_focus,
            "events": [asdict(event) for event in list(self.state_events)],
            "execution": {
                "mode": self.execution_mode,
                "auto_execute": self.auto_execute,
                "entries_enabled": self.auto_executor.enabled,
                "ledger_path": str(self.auto_executor.ledger.ledger_path),
                "guard_config": asdict(self.execution_guard.config),
                "recent_decisions": [asdict(decision) for decision in list(self.execution_decisions)],
                "recent_results": [asdict(result) for result in list(self.execution_results)],
            },
            "lifecycle": {
                "config": asdict(self.position_manager.config),
                "recent_actions": [asdict(action) for action in list(self.position_actions)],
                "position_structure": [],
            },
            "operator": {
                "telegram_enabled": self.telegram_reporter.is_enabled,
                "paused": self.operator_state.paused,
                "allow_new_entries": self.operator_state.allow_new_entries,
                "pause_reason": self.operator_state.pause_reason,
                "last_command": self.operator_state.last_command,
                "last_command_at": self.operator_state.last_command_at,
                "last_source": self.operator_state.last_source,
                "last_message": self.operator_state.last_message,
                "last_interpreter": self.operator_state.last_interpreter,
                "last_response": self.operator_state.last_response,
            },
            "health": {
                "bootstrapped": self.bootstrapped,
                "connected": mt5_snapshot.connected,
            },
            "logs": list(self.logs),
        }

        if self.actual_symbol:
            payload["lifecycle"]["position_structure"] = [
                asdict(item) for item in self.position_manager.assess_symbol(self.actual_symbol, snapshot=payload)
            ]

        return payload

    def write_snapshot(self, snapshot: dict | None = None) -> None:
        snapshot = snapshot or self.snapshot_dict()
        atomic_write_json(self.paths.state_file, snapshot, default=str)

    def close(self) -> None:
        self.session.close()
