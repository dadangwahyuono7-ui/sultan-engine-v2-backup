from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from sultan_engine_v2.adapters.exchange.binance_client import BinanceResolvedSymbol, BinanceRestClient
from sultan_engine_v2.core.breakout_detector import bootstrap_latest_breakout, update_breakout_state
from sultan_engine_v2.core.execution_models import ExecutionMode, ExecutionResult, Venue
from sultan_engine_v2.core.state_engine import StateEvent, SultanStateEngine
from sultan_engine_v2.manager.operator_control import JsonOperatorControlStore
from sultan_engine_v2.manager.telegram_reporter import TelegramOperatorReporter
from sultan_engine_v2.runtime.binance_paper_account import BinancePaperAccount, BinancePaperExecutor, JsonBinancePaperAccountStore
from sultan_engine_v2.runtime.execution_engine import IntentDecision, RuntimeAutoExecutor
from sultan_engine_v2.runtime.file_utils import atomic_write_json


DEFAULT_TIMEFRAMES = ("D1", "H4", "H1", "M30", "M15", "M5")


@dataclass(slots=True)
class RuntimePaths:
    state_file: Path
    journal_file: Path


class BinanceLiveRuntime:
    def __init__(
        self,
        base_symbol: str = "BTCUSDT",
        timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
        lookback: int = 250,
        break_buffer: float = 0.0,
        client: BinanceRestClient | None = None,
        execution_mode: ExecutionMode = ExecutionMode.PAPER,
        auto_execute: bool = True,
    ) -> None:
        self.base_symbol = base_symbol
        self.timeframes = timeframes
        self.lookback = lookback
        self.break_buffer = break_buffer
        self.client = client or BinanceRestClient()
        self.execution_mode = execution_mode
        self.auto_execute = auto_execute
        self.engine = SultanStateEngine(timeframes=timeframes)
        self.breakout_states = {}
        self.last_closed_timestamps: dict[str, int] = {}
        self.logs: deque[str] = deque(maxlen=50)
        self.state_events: deque[StateEvent] = deque(maxlen=50)
        self.execution_results: deque[ExecutionResult] = deque(maxlen=20)
        self.execution_decisions: deque[IntentDecision] = deque(maxlen=40)
        self.account_events: deque[dict] = deque(maxlen=30)
        self.symbol_resolution: BinanceResolvedSymbol | None = None
        self.actual_symbol: str | None = None
        self.price: float = 0.0
        self.bid: float = 0.0
        self.ask: float = 0.0
        self.connected = False
        self.bootstrapped = False

        runtime_dir = Path(__file__).resolve().parent
        slug = base_symbol.lower()
        self.paths = RuntimePaths(
            state_file=runtime_dir / "state" / f"binance_{slug}_snapshot.json",
            journal_file=runtime_dir / "journal" / f"binance_{slug}_journal.jsonl",
        )
        self.paper_account = BinancePaperAccount(
            store=JsonBinancePaperAccountStore(runtime_dir / "state" / f"binance_{slug}_paper_account.json")
        )
        self.operator_controls = JsonOperatorControlStore(runtime_dir / "state" / "operator_control.json")
        self.operator_state = self.operator_controls.load()
        self._last_operator_marker: tuple[str | None, str, bool] | None = None
        self.telegram_reporter = TelegramOperatorReporter()
        executor = (
            BinancePaperExecutor(
                self.paper_account,
                price_provider=lambda intent: self.ask if intent.direction.value == "BUY" else self.bid,
            )
            if execution_mode == ExecutionMode.PAPER
            else self.client
        )
        self.auto_executor = RuntimeAutoExecutor(
            venue=Venue.BINANCE,
            execution_mode=execution_mode,
            executors={Venue.BINANCE: executor},
            ledger_path=runtime_dir / "logs" / f"binance_{slug}_execution_ledger.json",
            enabled=auto_execute,
        )
        self._apply_operator_control(log_change=False)

    def log(self, message: str) -> None:
        self.logs.append(f"{datetime.now().strftime('%H:%M:%S')} | {message}")

    def _append_journal(self, entry: dict) -> None:
        self.paths.journal_file.parent.mkdir(parents=True, exist_ok=True)
        with self.paths.journal_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")

    def connect(self) -> bool:
        try:
            self.symbol_resolution = self.client.resolve_symbol(self.base_symbol)
            self.actual_symbol = self.symbol_resolution.actual
            if not self.actual_symbol:
                self.connected = False
                self.log(f"Binance symbol resolution failed | requested={self.base_symbol}")
                return False

            _, book = self.client.fetch_book_ticker(self.actual_symbol)
        except Exception as error:
            self.connected = False
            self.log(f"Binance connect failed | {type(error).__name__}: {error}")
            return False

        self.bid = book["bid"]
        self.ask = book["ask"]
        self.price = self.ask or self.bid
        self.connected = True
        self.log(f"Binance connected | symbol={self.actual_symbol}")
        self.log(f"Execution armed | mode={self.execution_mode} | auto_execute={self.auto_execute}")
        self.log(f"Operator control | paused={self.operator_state.paused} | new_entries={self.operator_state.allow_new_entries}")
        return True

    def bootstrap(self) -> bool:
        if not self.actual_symbol and not self.connect():
            return False

        seeded_any = False
        for timeframe in self.timeframes:
            _, bars = self.client.fetch_klines(self.actual_symbol or self.base_symbol, timeframe, self.lookback)
            if len(bars) < 3:
                self.log(f"Bootstrap skipped | tf={timeframe} | insufficient bars")
                continue

            state = bootstrap_latest_breakout(timeframe, bars, self.break_buffer)
            self.breakout_states[timeframe] = state
            self.last_closed_timestamps[timeframe] = bars[-1].timestamp or 0

            if state.is_bootstrapped:
                seeded_any = True
                self._record_events(self.engine.seed_timeframe(state))
                self.log(f"Bootstrap | tf={timeframe} | cmp={state.active_cmp or 'NONE'}")
            else:
                self.log(f"Bootstrap | tf={timeframe} | no valid breakout found yet")

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
                    "venue": "BINANCE",
                }
            )

    def _record_execution_results(self, results: list[ExecutionResult]) -> None:
        for result in results:
            self.execution_results.append(result)
            self.log(f"Execution {result.execution_mode} | {result.symbol} | success={result.success} | {result.message}")
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

    def _record_execution_decisions(self, decisions: list[IntentDecision]) -> None:
        for decision in decisions:
            self.execution_decisions.append(decision)
            if decision.allowed:
                continue
            self.log(f"Execution blocked | {decision.symbol} | {decision.reason}")
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

    def _record_account_events(self, events: list[dict]) -> None:
        for event in events:
            self.account_events.append(event)
            self.log(
                f"Paper {event.get('kind', 'EVENT')} | {event.get('symbol', self.actual_symbol)} | pnl={float(event.get('realized_pnl') or 0.0):+,.2f}"
            )
            self._append_journal(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "PAPER_ACCOUNT",
                    **event,
                }
            )

    def _apply_operator_control(self, log_change: bool = True) -> None:
        latest_state = self.operator_controls.load()
        marker = (latest_state.last_command_at, latest_state.last_command, latest_state.paused)
        if log_change and marker != self._last_operator_marker and latest_state.last_command_at:
            if latest_state.paused:
                self.log(f"Operator pause | entries blocked | {latest_state.pause_reason or latest_state.last_message or '-'}")
            else:
                self.log(f"Operator command | {latest_state.last_command} | entries={latest_state.allow_new_entries}")

        self.operator_state = latest_state
        self.auto_executor.enabled = bool(self.auto_execute and latest_state.allow_new_entries)
        self._last_operator_marker = marker

    def _finalize_cycle(self, execute_enabled: bool) -> dict:
        self._apply_operator_control()
        account_events = self.paper_account.mark_to_market(self.actual_symbol or self.base_symbol, bid=self.bid, ask=self.ask)
        if account_events:
            self._record_account_events(account_events)

        snapshot = self.snapshot_dict()
        if execute_enabled and self.bootstrapped:
            results = self.auto_executor.execute(snapshot)
            decisions = self.auto_executor.last_cycle_decisions
            if decisions:
                self._record_execution_decisions(decisions)
            if results:
                self._record_execution_results(results)
                account_events = list(self.paper_account.snapshot().get("recent_events", []))
                self.account_events = deque(account_events[-30:], maxlen=30)
                snapshot = self.snapshot_dict()
        self.write_snapshot(snapshot)
        return snapshot

    def poll(self) -> dict:
        if not self.actual_symbol and not self.connect():
            self.connected = False
            snapshot = self.snapshot_dict()
            self.write_snapshot(snapshot)
            return snapshot

        try:
            _, book = self.client.fetch_book_ticker(self.actual_symbol or self.base_symbol)
        except Exception as error:
            self.connected = False
            self.bootstrapped = False
            self.log(f"Binance poll failed | {type(error).__name__}: {error}")
            snapshot = self.snapshot_dict()
            self.write_snapshot(snapshot)
            return snapshot

        self.connected = True
        self.bid = book["bid"]
        self.ask = book["ask"]
        self.price = self.ask or self.bid

        if not self.bootstrapped:
            self.bootstrap()
            return self.snapshot_dict()

        state_changed = False
        for timeframe in self.timeframes:
            _, bars = self.client.fetch_klines(self.actual_symbol or self.base_symbol, timeframe, self.lookback)
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
                self._record_events(self.engine.apply_breakout(updated_state))
                state_changed = True

        return self._finalize_cycle(execute_enabled=state_changed)

    def snapshot_dict(self) -> dict:
        snapshot = self.engine.snapshot()
        paper_account = self.paper_account.snapshot()
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "venue": "BINANCE",
            "symbol": {
                "requested": self.base_symbol,
                "actual": self.actual_symbol,
                "resolution_source": self.symbol_resolution.source if self.symbol_resolution else "unknown",
            },
            "exchange": {
                "name": self.client.venue_label,
                "market_type": self.client.market_type,
                "base_url": self.client.base_url,
                "api_key_configured": bool(self.client.api_key),
            },
            "paper_account": paper_account,
            "market": {
                "bid": self.bid,
                "ask": self.ask,
                "price": self.price,
                "spread": max(self.ask - self.bid, 0.0) if self.ask and self.bid else 0.0,
                "time": int(datetime.now(timezone.utc).timestamp()),
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
                "recent_decisions": [asdict(decision) for decision in list(self.execution_decisions)],
                "recent_results": [asdict(result) for result in list(self.execution_results)],
            },
            "lifecycle": {
                "recent_actions": list(self.account_events),
                "position_structure": paper_account.get("positions", []),
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
                "connected": self.connected,
            },
            "logs": list(self.logs),
        }

    def write_snapshot(self, snapshot: dict | None = None) -> None:
        snapshot = snapshot or self.snapshot_dict()
        atomic_write_json(self.paths.state_file, snapshot, default=str)

    def close(self) -> None:
        self.connected = False
