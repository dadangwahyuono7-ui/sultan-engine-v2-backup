import unittest

from sultan_engine_v2.core.doctrine import ControlState, SequenceRole
from sultan_engine_v2.core.models import BreakoutState, TradeDirection
from sultan_engine_v2.core.state_engine import SultanStateEngine


class StateEngineTests(unittest.TestCase):
    def test_rebuild_sequences_restores_bootstrap_vr_state(self) -> None:
        engine = SultanStateEngine(timeframes=("D1", "H4", "H1"))
        engine.seed_timeframe(BreakoutState(timeframe="D1", active_cmp=TradeDirection.SELL, breakout_timestamp=100))
        engine.seed_timeframe(BreakoutState(timeframe="H4", active_cmp=TradeDirection.SELL, breakout_timestamp=200))
        engine.seed_timeframe(BreakoutState(timeframe="H1", active_cmp=TradeDirection.BUY, breakout_timestamp=300))

        events = engine.rebuild_sequences_from_current_nodes()

        self.assertEqual(ControlState.UNDER_ATTACK, engine.sequences["H4"].control_state)
        self.assertTrue(engine.sequences["H4"].vr_active)
        self.assertEqual("H1", engine.sequences["H4"].active_controller)
        self.assertEqual(300, engine.sequences["H4"].vr_started_at)
        self.assertEqual(SequenceRole.VR, engine.nodes["H1"].role_to_parent)
        self.assertIn(("VR_ACTIVE", "H4"), [(event.kind, event.parent_timeframe) for event in events])

    def test_vr_puts_parent_under_attack(self) -> None:
        engine = SultanStateEngine(timeframes=("D1", "H4", "H1"))
        engine.seed_timeframe(BreakoutState(timeframe="D1", active_cmp=TradeDirection.SELL))
        engine.seed_timeframe(BreakoutState(timeframe="H4", active_cmp=TradeDirection.SELL))

        engine.apply_breakout(BreakoutState(timeframe="H4", active_cmp=TradeDirection.BUY, changed=True))

        sequence = engine.sequences["D1"]
        self.assertEqual(sequence.control_state, ControlState.UNDER_ATTACK)
        self.assertEqual(engine.nodes["H4"].role_to_parent, SequenceRole.VR)

    def test_cf_recovers_parent_after_vr(self) -> None:
        engine = SultanStateEngine(timeframes=("D1", "H4", "H1"))
        engine.seed_timeframe(BreakoutState(timeframe="D1", active_cmp=TradeDirection.SELL))
        engine.seed_timeframe(BreakoutState(timeframe="H4", active_cmp=TradeDirection.SELL))

        engine.apply_breakout(BreakoutState(timeframe="H4", active_cmp=TradeDirection.BUY, changed=True))
        engine.apply_breakout(BreakoutState(timeframe="H4", active_cmp=TradeDirection.SELL, changed=True))

        sequence = engine.sequences["D1"]
        self.assertEqual(sequence.control_state, ControlState.RECOVERED_BY_CF)
        self.assertEqual(sequence.cf_count, 1)
        self.assertEqual(engine.nodes["H4"].role_to_parent, SequenceRole.CF)
        self.assertEqual(engine.nodes["H4"].target_timeframe, "D1")

    def test_h1_guardian_is_now_m15_not_m30(self) -> None:
        engine = SultanStateEngine(timeframes=("H1", "M15", "M5", "M30"))
        engine.seed_timeframe(BreakoutState(timeframe="H1", active_cmp=TradeDirection.SELL, breakout_timestamp=10))
        engine.seed_timeframe(BreakoutState(timeframe="M15", active_cmp=TradeDirection.SELL, breakout_timestamp=10))
        engine.seed_timeframe(BreakoutState(timeframe="M5", active_cmp=TradeDirection.SELL, breakout_timestamp=10))
        engine.seed_timeframe(BreakoutState(timeframe="M30", active_cmp=TradeDirection.SELL, breakout_timestamp=10))

        engine.apply_breakout(BreakoutState(timeframe="M15", active_cmp=TradeDirection.BUY, breakout_timestamp=100, changed=True))
        snap = engine.snapshot()

        self.assertEqual(ControlState.UNDER_ATTACK, snap.sequences["H1"].control_state)
        self.assertEqual("M15", snap.sequences["H1"].active_controller)
        self.assertEqual(SequenceRole.VR, snap.nodes["M15"].role_to_parent)
        self.assertEqual(SequenceRole.CMP, snap.nodes["M30"].role_to_parent)

    def test_high_risk_m5_cf_can_recover_h1_after_m15_vr(self) -> None:
        engine = SultanStateEngine(timeframes=("H1", "M15", "M5"))
        engine.seed_timeframe(BreakoutState(timeframe="H1", active_cmp=TradeDirection.SELL, breakout_timestamp=10))
        engine.seed_timeframe(BreakoutState(timeframe="M15", active_cmp=TradeDirection.SELL, breakout_timestamp=10))
        engine.seed_timeframe(BreakoutState(timeframe="M5", active_cmp=TradeDirection.BUY, breakout_timestamp=10))

        engine.apply_breakout(BreakoutState(timeframe="M15", active_cmp=TradeDirection.BUY, breakout_timestamp=100, changed=True))
        engine.apply_breakout(BreakoutState(timeframe="M5", active_cmp=TradeDirection.SELL, breakout_timestamp=120, changed=True))
        snap = engine.snapshot()

        self.assertEqual(ControlState.RECOVERED_BY_CF, snap.sequences["H1"].control_state)
        self.assertEqual("M5", snap.sequences["H1"].last_cf_timeframe)
        self.assertEqual(ControlState.UNDER_ATTACK, snap.sequences["M15"].control_state)
        self.assertEqual(SequenceRole.VR, snap.nodes["M5"].role_to_parent)
        self.assertEqual("M15", snap.nodes["M5"].setup_domain)

    def test_direct_vr_requires_child_break_after_parent_cmp(self) -> None:
        engine = SultanStateEngine(timeframes=("H1", "M15"))
        engine.seed_timeframe(BreakoutState(timeframe="H1", active_cmp=TradeDirection.BUY, breakout_timestamp=600))
        engine.seed_timeframe(BreakoutState(timeframe="M15", active_cmp=TradeDirection.SELL, breakout_timestamp=600))

        engine.apply_breakout(BreakoutState(timeframe="H1", active_cmp=TradeDirection.SELL, breakout_timestamp=1000, changed=True))
        engine.apply_breakout(BreakoutState(timeframe="M15", active_cmp=TradeDirection.BUY, breakout_timestamp=900, changed=True))

        sequence = engine.sequences["H1"]
        self.assertEqual(ControlState.IN_CONTROL, sequence.control_state)
        self.assertFalse(sequence.vr_active)
        self.assertEqual(SequenceRole.CMP, engine.nodes["M15"].role_to_parent)

    def test_stale_breakout_is_ignored_and_cf_must_be_newer_than_vr(self) -> None:
        engine = SultanStateEngine(timeframes=("H1", "M15"))
        engine.seed_timeframe(BreakoutState(timeframe="H1", active_cmp=TradeDirection.SELL, breakout_timestamp=1000))
        engine.seed_timeframe(BreakoutState(timeframe="M15", active_cmp=TradeDirection.SELL, breakout_timestamp=1000))

        engine.apply_breakout(BreakoutState(timeframe="M15", active_cmp=TradeDirection.BUY, breakout_timestamp=1100, changed=True))
        stale_events = engine.apply_breakout(BreakoutState(timeframe="M15", active_cmp=TradeDirection.SELL, breakout_timestamp=1050, changed=True))

        sequence = engine.sequences["H1"]
        self.assertEqual(ControlState.UNDER_ATTACK, sequence.control_state)
        self.assertEqual(TradeDirection.BUY, engine.nodes["M15"].cmp_direction)
        self.assertEqual(1100, engine.nodes["M15"].cmp_timestamp)
        self.assertEqual(["STALE_IGNORED"], [event.kind for event in stale_events])

        engine.apply_breakout(BreakoutState(timeframe="M15", active_cmp=TradeDirection.SELL, breakout_timestamp=1150, changed=True))
        self.assertEqual(ControlState.RECOVERED_BY_CF, sequence.control_state)
        self.assertEqual(1150, sequence.last_cf_timestamp)
        self.assertEqual(1, sequence.cf_count)


if __name__ == "__main__":
    unittest.main()
