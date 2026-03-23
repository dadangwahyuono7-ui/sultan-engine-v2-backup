from __future__ import annotations

from sultan_engine_v2.core.models import BreakoutState, PriceBar, TradeDirection, ensure_price_bars


def get_minor_snr_flip(rows: list[PriceBar] | tuple[PriceBar, ...] | list[dict]) -> tuple[float, float]:
    """Return the latest body-based minor support/resistance flip.

    The logic follows the legacy live engine grammar:
    - previous bearish + current bullish => minor support
    - previous bullish + current bearish => minor resistance

    The caller should provide closed candles only.
    """
    bars = ensure_price_bars(rows)
    if len(bars) < 2:
        return 0.0, 0.0

    curr = bars[-1]
    prev = bars[-2]
    support = 0.0
    resistance = 0.0

    if prev.open > prev.close and curr.close > curr.open:
        support = min(prev.open, prev.close)

    if prev.close > prev.open and curr.open > curr.close:
        resistance = max(prev.close, prev.open)

    return support, resistance


def bootstrap_latest_breakout(
    timeframe: str,
    rows: list[PriceBar] | tuple[PriceBar, ...] | list[dict],
    break_buffer: float = 0.0,
) -> BreakoutState:
    """Scan backward and find the latest valid breakout that owns the timeframe CMP."""
    bars = ensure_price_bars(rows)
    state = BreakoutState(timeframe=timeframe)
    if len(bars) < 3:
        return state

    for index in range(len(bars) - 1, 1, -1):
        temp_support = 0.0
        temp_resistance = 0.0

        for pivot in range(index, 1, -1):
            curr = bars[pivot]
            prev = bars[pivot - 1]

            if temp_support == 0.0 and prev.open > prev.close and curr.close > curr.open:
                temp_support = min(prev.open, prev.close)

            if temp_resistance == 0.0 and prev.close > prev.open and curr.open > curr.close:
                temp_resistance = max(prev.close, prev.open)

            if temp_support > 0.0 and temp_resistance > 0.0:
                break

        close_price = bars[index].close

        if temp_resistance > 0.0 and close_price > temp_resistance + break_buffer:
            state.active_cmp = TradeDirection.BUY
            state.support = temp_support
            state.resistance = temp_resistance
            state.last_breakout_level = temp_resistance
            state.breakout_timestamp = bars[index].timestamp
            return state

        if temp_support > 0.0 and close_price < temp_support - break_buffer:
            state.active_cmp = TradeDirection.SELL
            state.support = temp_support
            state.resistance = temp_resistance
            state.last_breakout_level = temp_support
            state.breakout_timestamp = bars[index].timestamp
            return state

    return state


def update_breakout_state(
    current_state: BreakoutState,
    rows: list[PriceBar] | tuple[PriceBar, ...] | list[dict],
    break_buffer: float = 0.0,
) -> BreakoutState:
    """Update a timeframe CMP from the latest close-break against the latest minor SNR."""
    bars = ensure_price_bars(rows)
    updated = BreakoutState(
        timeframe=current_state.timeframe,
        active_cmp=current_state.active_cmp,
        support=current_state.support,
        resistance=current_state.resistance,
        last_breakout_level=current_state.last_breakout_level,
        breakout_timestamp=current_state.breakout_timestamp,
        changed=False,
    )

    if len(bars) < 2:
        return updated

    new_support, new_resistance = get_minor_snr_flip(bars)
    if new_support > 0.0:
        updated.support = new_support
    if new_resistance > 0.0:
        updated.resistance = new_resistance

    last_close = bars[-1].close

    if updated.resistance > 0.0 and last_close > updated.resistance + break_buffer:
        if updated.active_cmp != TradeDirection.BUY:
            updated.active_cmp = TradeDirection.BUY
            updated.last_breakout_level = updated.resistance
            updated.breakout_timestamp = bars[-1].timestamp
            updated.changed = True
    elif updated.support > 0.0 and last_close < updated.support - break_buffer:
        if updated.active_cmp != TradeDirection.SELL:
            updated.active_cmp = TradeDirection.SELL
            updated.last_breakout_level = updated.support
            updated.breakout_timestamp = bars[-1].timestamp
            updated.changed = True

    return updated
