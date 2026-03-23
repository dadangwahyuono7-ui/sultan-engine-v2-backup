from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sultan_engine_v2.adapters.mt5.execution.position_manager import Mt5PositionLifecycleManager, PositionLifecycleConfig
from sultan_engine_v2.core.execution_models import ExecutionMode


def build_position(
    price_open: float,
    sl: float,
    tp: float,
    volume: float = 1.0,
    position_type: int = 0,
    comment: str = "",
    opened_at: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        ticket=101,
        symbol="XAUUSD",
        type=position_type,
        price_open=price_open,
        sl=sl,
        tp=tp,
        volume=volume,
        magic=26031926,
        comment=comment,
        time=opened_at,
    )


class Mt5PositionManagerTests(unittest.TestCase):
    def test_applies_break_even_when_rr_threshold_is_reached(self) -> None:
        position = build_position(price_open=100.0, sl=90.0, tp=130.0)
        modified_levels: list[float] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = Mt5PositionLifecycleManager(
                config=PositionLifecycleConfig(partial_take_rr=9.0, trail_start_rr=9.0),
                state_path=Path(temp_dir) / "lifecycle.json",
                positions_provider=lambda symbol: [position],
                tick_provider=lambda symbol: SimpleNamespace(bid=110.0, ask=110.1),
                symbol_info_provider=lambda symbol: SimpleNamespace(point=0.01, volume_min=0.01, volume_step=0.01),
                modify_position=lambda position, new_sl: (modified_levels.append(new_sl) or True, "BE moved"),
                partial_close=lambda position, volume: (True, "partial"),
            )

            actions = manager.manage_symbol("XAUUSD", ExecutionMode.LIVE)

        self.assertEqual([100.1], modified_levels)
        self.assertEqual("BREAK_EVEN", actions[0].action)
        self.assertTrue(actions[0].success)

    def test_runs_partial_close_and_trailing_after_large_rr(self) -> None:
        position = build_position(price_open=100.0, sl=90.0, tp=140.0)
        closed_volumes: list[float] = []
        modified_levels: list[float] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = Mt5PositionLifecycleManager(
                config=PositionLifecycleConfig(break_even_rr=9.0, partial_take_rr=1.5, trail_start_rr=2.0, trail_buffer_rr=1.0),
                state_path=Path(temp_dir) / "lifecycle.json",
                positions_provider=lambda symbol: [position],
                tick_provider=lambda symbol: SimpleNamespace(bid=125.0, ask=125.1),
                symbol_info_provider=lambda symbol: SimpleNamespace(point=0.01, volume_min=0.01, volume_step=0.01),
                modify_position=lambda position, new_sl: (modified_levels.append(new_sl) or True, "Trail moved"),
                partial_close=lambda position, volume: (closed_volumes.append(volume) or True, "Partial done"),
            )

            actions = manager.manage_symbol("XAUUSD", ExecutionMode.LIVE)

        action_names = [action.action for action in actions]
        self.assertIn("PARTIAL_CLOSE", action_names)
        self.assertIn("TRAIL_SL", action_names)
        self.assertEqual([0.5], closed_volumes)
        self.assertEqual([115.0], modified_levels)

    def test_exits_low_risk_cf_buy_when_parent_structure_fails(self) -> None:
        position = build_position(
            price_open=100.0,
            sl=95.0,
            tp=120.0,
            comment="M15 recovered by CF on M5",
            opened_at=1000,
        )
        closed_tickets: list[int] = []
        snapshot = {
            "market": {"time": 1200},
            "timeframes": {
                "M15": {"cmp": "BUY"},
                "M5": {"cmp": "SELL"},
            },
            "sequences": {
                "M15": {"control_state": "UNDER_ATTACK", "active_controller": "M5"},
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = Mt5PositionLifecycleManager(
                config=PositionLifecycleConfig(break_even_rr=9.0, partial_take_rr=9.0, trail_start_rr=9.0),
                state_path=Path(temp_dir) / "lifecycle.json",
                positions_provider=lambda symbol: [position],
                tick_provider=lambda symbol: SimpleNamespace(bid=100.0, ask=100.1),
                symbol_info_provider=lambda symbol: SimpleNamespace(point=0.01, volume_min=0.01, volume_step=0.01),
                modify_position=lambda position, new_sl: (True, "ignored"),
                partial_close=lambda position, volume: (True, "ignored"),
                close_position=lambda position: (closed_tickets.append(position.ticket) or True, "Structure exit"),
            )

            actions = manager.manage_symbol("XAUUSD", ExecutionMode.LIVE, snapshot=snapshot)

        self.assertEqual([101], closed_tickets)
        self.assertEqual(["STRUCTURE_EXIT"], [action.action for action in actions])
        self.assertEqual("LOW_RISK_CF_FAILED", actions[0].details["reason"])

    def test_exits_high_risk_cf_sell_when_low_risk_confirmation_fails_after_grace(self) -> None:
        position = build_position(
            price_open=100.0,
            sl=105.0,
            tp=90.0,
            position_type=1,
            comment="H1 recovered by CF on M5",
            opened_at=1000,
        )
        closed_tickets: list[int] = []
        snapshot = {
            "market": {"time": 2200},
            "timeframes": {
                "H1": {"cmp": "SELL"},
                "M15": {"cmp": "BUY"},
                "M5": {"cmp": "SELL"},
            },
            "sequences": {
                "H1": {"control_state": "RECOVERED_BY_CF", "active_controller": "H1"},
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = Mt5PositionLifecycleManager(
                config=PositionLifecycleConfig(break_even_rr=9.0, partial_take_rr=9.0, trail_start_rr=9.0),
                state_path=Path(temp_dir) / "lifecycle.json",
                positions_provider=lambda symbol: [position],
                tick_provider=lambda symbol: SimpleNamespace(bid=99.0, ask=99.1),
                symbol_info_provider=lambda symbol: SimpleNamespace(point=0.01, volume_min=0.01, volume_step=0.01),
                modify_position=lambda position, new_sl: (True, "ignored"),
                partial_close=lambda position, volume: (True, "ignored"),
                close_position=lambda position: (closed_tickets.append(position.ticket) or True, "Structure exit"),
            )

            actions = manager.manage_symbol("XAUUSD", ExecutionMode.LIVE, snapshot=snapshot)

        self.assertEqual([101], closed_tickets)
        self.assertEqual("LOW_RISK_CONFIRMATION_FAILED", actions[0].details["reason"])

    def test_keeps_high_risk_cf_trade_alive_during_grace_window(self) -> None:
        position = build_position(
            price_open=100.0,
            sl=105.0,
            tp=90.0,
            position_type=1,
            comment="H1 recovered by CF on M5",
            opened_at=1000,
        )
        snapshot = {
            "market": {"time": 1500},
            "timeframes": {
                "H1": {"cmp": "SELL"},
                "M15": {"cmp": "BUY"},
                "M5": {"cmp": "SELL"},
            },
            "sequences": {
                "H1": {"control_state": "RECOVERED_BY_CF", "active_controller": "H1"},
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = Mt5PositionLifecycleManager(
                config=PositionLifecycleConfig(break_even_rr=9.0, partial_take_rr=9.0, trail_start_rr=9.0),
                state_path=Path(temp_dir) / "lifecycle.json",
                positions_provider=lambda symbol: [position],
                tick_provider=lambda symbol: SimpleNamespace(bid=99.0, ask=99.1),
                symbol_info_provider=lambda symbol: SimpleNamespace(point=0.01, volume_min=0.01, volume_step=0.01),
                modify_position=lambda position, new_sl: (True, "ignored"),
                partial_close=lambda position, volume: (True, "ignored"),
                close_position=lambda position: (True, "should not close"),
            )

            actions = manager.manage_symbol("XAUUSD", ExecutionMode.LIVE, snapshot=snapshot)

        self.assertEqual([], actions)

    def test_assess_symbol_reports_grace_status_for_high_risk_trade(self) -> None:
        position = build_position(
            price_open=100.0,
            sl=105.0,
            tp=90.0,
            position_type=1,
            comment="H1 recovered by CF on M5",
            opened_at=1000,
        )
        snapshot = {
            "market": {"time": 1500},
            "timeframes": {
                "H1": {"cmp": "SELL"},
                "M15": {"cmp": "BUY"},
                "M5": {"cmp": "SELL"},
            },
            "sequences": {
                "H1": {"control_state": "RECOVERED_BY_CF", "active_controller": "H1"},
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = Mt5PositionLifecycleManager(
                config=PositionLifecycleConfig(break_even_rr=9.0, partial_take_rr=9.0, trail_start_rr=9.0),
                state_path=Path(temp_dir) / "lifecycle.json",
                positions_provider=lambda symbol: [position],
                tick_provider=lambda symbol: SimpleNamespace(bid=99.0, ask=99.1),
                symbol_info_provider=lambda symbol: SimpleNamespace(point=0.01, volume_min=0.01, volume_step=0.01),
            )

            statuses = manager.assess_symbol("XAUUSD", snapshot=snapshot)

        self.assertEqual(1, len(statuses))
        self.assertEqual("GRACE", statuses[0].status)
        self.assertGreater(statuses[0].grace_remaining_seconds or 0, 0)

    def test_assess_symbol_reports_healthy_low_risk_trade(self) -> None:
        position = build_position(
            price_open=100.0,
            sl=95.0,
            tp=120.0,
            comment="M15 recovered by CF on M5",
            opened_at=1000,
        )
        snapshot = {
            "market": {"time": 1200},
            "timeframes": {
                "M15": {"cmp": "BUY"},
                "M5": {"cmp": "BUY"},
            },
            "sequences": {
                "M15": {"control_state": "RECOVERED_BY_CF", "active_controller": "M15"},
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = Mt5PositionLifecycleManager(
                config=PositionLifecycleConfig(break_even_rr=9.0, partial_take_rr=9.0, trail_start_rr=9.0),
                state_path=Path(temp_dir) / "lifecycle.json",
                positions_provider=lambda symbol: [position],
                tick_provider=lambda symbol: SimpleNamespace(bid=100.0, ask=100.1),
                symbol_info_provider=lambda symbol: SimpleNamespace(point=0.01, volume_min=0.01, volume_step=0.01),
            )

            statuses = manager.assess_symbol("XAUUSD", snapshot=snapshot)

        self.assertEqual(1, len(statuses))
        self.assertEqual("HEALTHY", statuses[0].status)
        self.assertEqual("LOW_RISK_CF", statuses[0].entry_kind)


if __name__ == "__main__":
    unittest.main()
