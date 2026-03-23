from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sultan_engine_v2.core.doctrine import ControlState, SequenceRole
from sultan_engine_v2.core.models import BreakoutState, TradeDirection
from sultan_engine_v2.core.timeframe_relations import (
    CF_HIGH_RISK_BY_PARENT,
    CF_LOW_RISK_BY_PARENT,
    TRADING_TIMEFRAMES,
    VR_CHILD_BY_PARENT,
    get_parent_timeframe,
    get_target_timeframe,
)


@dataclass(slots=True)
class TimeframeNode:
    timeframe: str
    cmp_direction: TradeDirection | None = None
    breakout_level: float = 0.0
    cmp_timestamp: int | None = None
    support: float = 0.0
    resistance: float = 0.0
    role_to_parent: SequenceRole = SequenceRole.CMP
    parent_timeframe: str | None = None
    setup_domain: str | None = None
    target_timeframe: str | None = None


@dataclass(slots=True)
class ParentSequence:
    parent_timeframe: str
    parent_direction: TradeDirection | None = None
    parent_breakout_timestamp: int | None = None
    vr_child_timeframe: str | None = None
    vr_active: bool = False
    vr_started_at: int | None = None
    cf_count: int = 0
    control_state: ControlState = ControlState.IN_CONTROL
    active_controller: str | None = None
    last_cf_timeframe: str | None = None
    last_cf_timestamp: int | None = None


@dataclass(slots=True)
class StateEvent:
    kind: str
    timeframe: str
    message: str
    parent_timeframe: str | None = None
    direction: TradeDirection | None = None
    event_timestamp: int | None = None


@dataclass(slots=True)
class EngineSnapshot:
    nodes: dict[str, TimeframeNode] = field(default_factory=dict)
    sequences: dict[str, ParentSequence] = field(default_factory=dict)
    setup_focus: dict[str, Any] = field(default_factory=dict)


class SultanStateEngine:
    """Initial event-driven state engine for CMP MASTER / VR / CF control flow.

    This is an intentionally small but deterministic foundation. It keeps the
    vocabulary and parent-child relationships aligned with the trading SOP while
    leaving room for richer domain resolution later.
    """

    def __init__(self, timeframes: tuple[str, ...] = TRADING_TIMEFRAMES) -> None:
        self.nodes = {
            timeframe: TimeframeNode(timeframe=timeframe, parent_timeframe=get_parent_timeframe(timeframe))
            for timeframe in timeframes
        }
        self.sequences = {
            parent: ParentSequence(parent_timeframe=parent, vr_child_timeframe=child)
            for parent, child in VR_CHILD_BY_PARENT.items()
            if parent in self.nodes and child in self.nodes
        }

    def seed_timeframe(self, breakout_state: BreakoutState) -> list[StateEvent]:
        events: list[StateEvent] = []
        node = self.nodes[breakout_state.timeframe]
        previous_direction = node.cmp_direction
        node.cmp_direction = breakout_state.active_cmp
        node.breakout_level = breakout_state.last_breakout_level
        node.cmp_timestamp = breakout_state.breakout_timestamp
        node.support = breakout_state.support
        node.resistance = breakout_state.resistance

        if breakout_state.timeframe in self.sequences:
            sequence = self.sequences[breakout_state.timeframe]
            sequence.parent_direction = breakout_state.active_cmp
            sequence.parent_breakout_timestamp = breakout_state.breakout_timestamp
            sequence.vr_active = False
            sequence.vr_started_at = None
            sequence.control_state = ControlState.IN_CONTROL
            sequence.active_controller = breakout_state.timeframe
            sequence.last_cf_timeframe = None
            sequence.last_cf_timestamp = None
            sequence.cf_count = 0

        self._recompute_roles()
        if breakout_state.active_cmp is not None and previous_direction != breakout_state.active_cmp:
            events.append(
                StateEvent(
                    kind="CMP_SEEDED",
                    timeframe=breakout_state.timeframe,
                    message=f"{breakout_state.timeframe} seeded as CMP {breakout_state.active_cmp}",
                    direction=breakout_state.active_cmp,
                    event_timestamp=breakout_state.breakout_timestamp,
                )
            )
        return events

    def apply_breakout(self, breakout_state: BreakoutState) -> list[StateEvent]:
        events: list[StateEvent] = []
        node = self.nodes[breakout_state.timeframe]
        if self._is_stale_breakout(node.cmp_timestamp, breakout_state.breakout_timestamp):
            events.append(
                StateEvent(
                    kind="STALE_IGNORED",
                    timeframe=breakout_state.timeframe,
                    message=(
                        f"{breakout_state.timeframe} breakout ignored as stale | "
                        f"event={breakout_state.breakout_timestamp} current={node.cmp_timestamp}"
                    ),
                    direction=breakout_state.active_cmp,
                    event_timestamp=breakout_state.breakout_timestamp,
                )
            )
            return events

        previous_direction = node.cmp_direction
        sequence_before = {
            parent: (
                sequence.control_state,
                sequence.cf_count,
                sequence.active_controller,
                sequence.last_cf_timeframe,
            )
            for parent, sequence in self.sequences.items()
        }

        node.cmp_direction = breakout_state.active_cmp
        node.breakout_level = breakout_state.last_breakout_level
        if previous_direction != breakout_state.active_cmp:
            node.cmp_timestamp = breakout_state.breakout_timestamp
        node.support = breakout_state.support
        node.resistance = breakout_state.resistance

        if breakout_state.active_cmp is not None and previous_direction != breakout_state.active_cmp:
            events.append(
                StateEvent(
                    kind="CMP_CHANGED",
                    timeframe=breakout_state.timeframe,
                    message=f"{breakout_state.timeframe} flipped CMP to {breakout_state.active_cmp}",
                    direction=breakout_state.active_cmp,
                    event_timestamp=breakout_state.breakout_timestamp,
                )
            )

        if breakout_state.timeframe in self.sequences:
            sequence = self.sequences[breakout_state.timeframe]
            if previous_direction != breakout_state.active_cmp:
                sequence.parent_direction = breakout_state.active_cmp
                sequence.parent_breakout_timestamp = breakout_state.breakout_timestamp
                sequence.vr_active = False
                sequence.vr_started_at = None
                sequence.cf_count = 0
                sequence.last_cf_timeframe = None
                sequence.last_cf_timestamp = None
                sequence.control_state = ControlState.IN_CONTROL
                sequence.active_controller = breakout_state.timeframe

        for parent, sequence in self.sequences.items():
            parent_direction = self.nodes[parent].cmp_direction
            child_direction = breakout_state.active_cmp
            if parent_direction is None or child_direction is None:
                continue

            vr_child = sequence.vr_child_timeframe
            high_risk_child = CF_HIGH_RISK_BY_PARENT.get(parent)
            parent_breakout_timestamp = sequence.parent_breakout_timestamp or self.nodes[parent].cmp_timestamp

            if breakout_state.timeframe == vr_child:
                if child_direction != parent_direction and self._happened_after(
                    breakout_state.breakout_timestamp,
                    parent_breakout_timestamp,
                ):
                    sequence.vr_active = True
                    sequence.vr_started_at = breakout_state.breakout_timestamp
                    sequence.control_state = ControlState.UNDER_ATTACK
                    sequence.active_controller = breakout_state.timeframe
                    sequence.last_cf_timeframe = None
                    sequence.last_cf_timestamp = None
                elif sequence.vr_active and child_direction == parent_direction and self._happened_after(
                    breakout_state.breakout_timestamp,
                    sequence.vr_started_at,
                ):
                    sequence.control_state = ControlState.RECOVERED_BY_CF
                    sequence.active_controller = parent
                    sequence.last_cf_timeframe = breakout_state.timeframe
                    sequence.last_cf_timestamp = breakout_state.breakout_timestamp
                    sequence.cf_count += 1
                continue

            if (
                breakout_state.timeframe == high_risk_child
                and sequence.vr_active
                and child_direction == parent_direction
                and self._happened_after(breakout_state.breakout_timestamp, sequence.vr_started_at)
            ):
                sequence.control_state = ControlState.RECOVERED_BY_CF
                sequence.active_controller = parent
                sequence.last_cf_timeframe = breakout_state.timeframe
                sequence.last_cf_timestamp = breakout_state.breakout_timestamp
                sequence.cf_count += 1

        self._recompute_roles()
        for parent, sequence in self.sequences.items():
            before = sequence_before[parent]
            if sequence.control_state != before[0]:
                if sequence.control_state == ControlState.UNDER_ATTACK:
                    attacker = sequence.active_controller or sequence.vr_child_timeframe or breakout_state.timeframe
                    direction = self.nodes[attacker].cmp_direction if attacker in self.nodes else None
                    events.append(
                        StateEvent(
                            kind="VR_ACTIVE",
                            timeframe=attacker,
                            parent_timeframe=parent,
                            direction=direction,
                            message=f"{attacker} is now VR against {parent}",
                            event_timestamp=sequence.vr_started_at,
                        )
                    )
                elif sequence.control_state == ControlState.RECOVERED_BY_CF:
                    cf_timeframe = sequence.last_cf_timeframe or breakout_state.timeframe
                    events.append(
                        StateEvent(
                            kind="CF_CONFIRMED",
                            timeframe=cf_timeframe,
                            parent_timeframe=parent,
                            direction=self.nodes[parent].cmp_direction,
                            message=f"{cf_timeframe} confirmed CF for {parent}",
                            event_timestamp=sequence.last_cf_timestamp,
                        )
                    )
            elif sequence.cf_count != before[1] and sequence.control_state == ControlState.RECOVERED_BY_CF:
                cf_timeframe = sequence.last_cf_timeframe or breakout_state.timeframe
                events.append(
                    StateEvent(
                        kind="CF_REENTRY",
                        timeframe=cf_timeframe,
                        parent_timeframe=parent,
                        direction=self.nodes[parent].cmp_direction,
                        message=f"{cf_timeframe} produced another CF for {parent}",
                        event_timestamp=sequence.last_cf_timestamp,
                    )
                )

        return events

    def rebuild_sequences_from_current_nodes(self) -> list[StateEvent]:
        events: list[StateEvent] = []
        sequence_before = {
            parent: (
                sequence.control_state,
                sequence.cf_count,
                sequence.active_controller,
                sequence.last_cf_timeframe,
            )
            for parent, sequence in self.sequences.items()
        }

        for parent, sequence in self.sequences.items():
            parent_node = self.nodes[parent]
            sequence.parent_direction = parent_node.cmp_direction
            sequence.parent_breakout_timestamp = parent_node.cmp_timestamp
            sequence.vr_active = False
            sequence.vr_started_at = None
            sequence.cf_count = 0
            sequence.control_state = ControlState.IN_CONTROL
            sequence.active_controller = parent if parent_node.cmp_direction is not None else None
            sequence.last_cf_timeframe = None
            sequence.last_cf_timestamp = None

        for parent, sequence in self.sequences.items():
            parent_node = self.nodes[parent]
            parent_direction = parent_node.cmp_direction
            vr_child = sequence.vr_child_timeframe
            if parent_direction is None or not vr_child or vr_child not in self.nodes:
                continue

            child_node = self.nodes[vr_child]
            child_direction = child_node.cmp_direction
            if child_direction is None:
                continue

            if child_direction != parent_direction and self._happened_after(
                child_node.cmp_timestamp,
                sequence.parent_breakout_timestamp,
            ):
                sequence.vr_active = True
                sequence.vr_started_at = child_node.cmp_timestamp
                sequence.control_state = ControlState.UNDER_ATTACK
                sequence.active_controller = vr_child

            high_risk_child = CF_HIGH_RISK_BY_PARENT.get(parent)
            if (
                high_risk_child
                and high_risk_child in self.nodes
                and sequence.vr_active
            ):
                cf_node = self.nodes[high_risk_child]
                if (
                    cf_node.cmp_direction == parent_direction
                    and self._happened_after(cf_node.cmp_timestamp, sequence.vr_started_at)
                ):
                    sequence.control_state = ControlState.RECOVERED_BY_CF
                    sequence.active_controller = parent
                    sequence.last_cf_timeframe = high_risk_child
                    sequence.last_cf_timestamp = cf_node.cmp_timestamp
                    sequence.cf_count = 1

        self._recompute_roles()
        for parent, sequence in self.sequences.items():
            before = sequence_before[parent]
            if sequence.control_state != before[0]:
                if sequence.control_state == ControlState.UNDER_ATTACK:
                    attacker = sequence.active_controller or sequence.vr_child_timeframe or parent
                    direction = self.nodes[attacker].cmp_direction if attacker in self.nodes else None
                    events.append(
                        StateEvent(
                            kind="VR_ACTIVE",
                            timeframe=attacker,
                            parent_timeframe=parent,
                            direction=direction,
                            message=f"{attacker} is now VR against {parent}",
                            event_timestamp=sequence.vr_started_at,
                        )
                    )
                elif sequence.control_state == ControlState.RECOVERED_BY_CF:
                    cf_timeframe = sequence.last_cf_timeframe or parent
                    events.append(
                        StateEvent(
                            kind="CF_CONFIRMED",
                            timeframe=cf_timeframe,
                            parent_timeframe=parent,
                            direction=self.nodes[parent].cmp_direction,
                            message=f"{cf_timeframe} confirmed CF for {parent}",
                            event_timestamp=sequence.last_cf_timestamp,
                        )
                    )

        return events

    @staticmethod
    def _happened_after(event_timestamp: int | None, reference_timestamp: int | None) -> bool:
        if event_timestamp is None or reference_timestamp is None:
            return True
        return event_timestamp > reference_timestamp

    @staticmethod
    def _is_stale_breakout(current_timestamp: int | None, event_timestamp: int | None) -> bool:
        if current_timestamp is None or event_timestamp is None:
            return False
        return event_timestamp <= current_timestamp

    def _recompute_roles(self) -> None:
        for node in self.nodes.values():
            node.role_to_parent = SequenceRole.CMP
            node.setup_domain = node.parent_timeframe
            node.target_timeframe = None

        for parent, sequence in self.sequences.items():
            vr_child = sequence.vr_child_timeframe
            if not vr_child:
                continue

            attacker = sequence.active_controller if sequence.active_controller in self.nodes else vr_child
            vr_node = self.nodes.get(attacker) if attacker else None
            if vr_node is None:
                continue

            high_risk_child = CF_HIGH_RISK_BY_PARENT.get(parent)
            if sequence.control_state == ControlState.UNDER_ATTACK:
                vr_node.role_to_parent = SequenceRole.VR
                vr_node.setup_domain = vr_node.parent_timeframe or parent
                vr_node.target_timeframe = None
            elif sequence.control_state == ControlState.RECOVERED_BY_CF and sequence.last_cf_timeframe:
                cf_node = self.nodes[sequence.last_cf_timeframe]
                cf_node.role_to_parent = SequenceRole.CF
                cf_node.setup_domain = parent
                cf_node.target_timeframe = get_target_timeframe(cf_node.timeframe)

                if high_risk_child and sequence.last_cf_timeframe == high_risk_child:
                    cf_node.setup_domain = parent

    @staticmethod
    def _timeframe_rank(timeframe: str | None) -> int:
        if not timeframe:
            return len(TRADING_TIMEFRAMES)
        try:
            return TRADING_TIMEFRAMES.index(timeframe)
        except ValueError:
            return len(TRADING_TIMEFRAMES)

    def _derive_setup_focus(self) -> dict[str, Any]:
        under_attack_sequences = [
            (parent, sequence)
            for parent, sequence in self.sequences.items()
            if sequence.control_state == ControlState.UNDER_ATTACK and sequence.active_controller in self.nodes
        ]
        attacked_parents = {parent for parent, _ in under_attack_sequences}

        if under_attack_sequences:
            leaf_sequences = [
                (parent, sequence)
                for parent, sequence in under_attack_sequences
                if sequence.active_controller not in attacked_parents
            ]
            dominant_parent, dominant_sequence = min(
                leaf_sequences or under_attack_sequences,
                key=lambda item: self._timeframe_rank(item[1].active_controller),
            )
            dominant_vr = dominant_sequence.active_controller
            master_node = self.nodes[dominant_parent]
            vr_node = self.nodes.get(dominant_vr) if dominant_vr else None
            higher_context = [
                f"{parent}<-{sequence.active_controller}"
                for parent, sequence in sorted(
                    under_attack_sequences,
                    key=lambda item: self._timeframe_rank(item[0]),
                )
                if parent != dominant_parent
            ]
            low_risk_cf = CF_LOW_RISK_BY_PARENT.get(dominant_parent)
            high_risk_cf = CF_HIGH_RISK_BY_PARENT.get(dominant_parent)
            summary = (
                f"WAIT_CF {dominant_parent} | VR {dominant_vr or '-'} | "
                f"CF low {low_risk_cf or '-'} | CF high {high_risk_cf or '-'}"
            )
            return {
                "status": "WAIT_CF",
                "master_timeframe": dominant_parent,
                "master_direction": master_node.cmp_direction,
                "master_breakout_timestamp": dominant_sequence.parent_breakout_timestamp,
                "vr_timeframe": dominant_vr,
                "vr_direction": vr_node.cmp_direction if vr_node else None,
                "vr_started_at": dominant_sequence.vr_started_at,
                "cf_low_risk_timeframe": low_risk_cf,
                "cf_high_risk_timeframe": high_risk_cf,
                "target_timeframe": get_target_timeframe(high_risk_cf or low_risk_cf or dominant_vr or ""),
                "higher_context": higher_context,
                "summary": summary,
            }

        recovered_sequences = [
            (parent, sequence)
            for parent, sequence in self.sequences.items()
            if sequence.control_state == ControlState.RECOVERED_BY_CF and sequence.last_cf_timeframe
        ]
        if recovered_sequences:
            dominant_parent, dominant_sequence = min(
                recovered_sequences,
                key=lambda item: self._timeframe_rank(item[1].last_cf_timeframe),
            )
            master_node = self.nodes[dominant_parent]
            cf_timeframe = dominant_sequence.last_cf_timeframe
            cf_node = self.nodes.get(cf_timeframe) if cf_timeframe else None
            summary = f"CF_CONFIRMED {dominant_parent} via {cf_timeframe or '-'}"
            return {
                "status": "CF_CONFIRMED",
                "master_timeframe": dominant_parent,
                "master_direction": master_node.cmp_direction,
                "master_breakout_timestamp": dominant_sequence.parent_breakout_timestamp,
                "confirmed_cf_timeframe": cf_timeframe,
                "confirmed_cf_direction": cf_node.cmp_direction if cf_node else None,
                "confirmed_cf_at": dominant_sequence.last_cf_timestamp,
                "target_timeframe": get_target_timeframe(cf_timeframe or ""),
                "higher_context": [],
                "summary": summary,
            }

        seeded = [
            timeframe
            for timeframe, node in self.nodes.items()
            if node.cmp_direction is not None
        ]
        dominant_master = min(seeded, key=self._timeframe_rank) if seeded else None
        master_node = self.nodes.get(dominant_master) if dominant_master else None
        summary = f"NO_ACTIVE_VR | follow CMP chain from {dominant_master or '-'}"
        return {
            "status": "NO_ACTIVE_VR",
            "master_timeframe": dominant_master,
            "master_direction": master_node.cmp_direction if master_node else None,
            "master_breakout_timestamp": master_node.cmp_timestamp if master_node else None,
            "higher_context": [],
            "summary": summary,
        }

    def snapshot(self) -> EngineSnapshot:
        return EngineSnapshot(
            nodes=self.nodes.copy(),
            sequences=self.sequences.copy(),
            setup_focus=self._derive_setup_focus(),
        )
