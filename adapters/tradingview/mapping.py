from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sultan_engine_v2.core.execution_models import Venue
from sultan_engine_v2.core.models import PriceBar
from sultan_engine_v2.runtime.env_loader import load_runtime_env


TRADINGVIEW_INTERVAL_MAP = {
    "1": "M1",
    "M1": "M1",
    "3": "M1",
    "5": "M5",
    "M5": "M5",
    "15": "M15",
    "M15": "M15",
    "30": "M30",
    "M30": "M30",
    "45": "M30",
    "60": "H1",
    "H1": "H1",
    "120": "H1",
    "180": "H1",
    "240": "H4",
    "H4": "H4",
    "1H": "H1",
    "4H": "H4",
    "D": "D1",
    "1D": "D1",
    "D1": "D1",
    "W": "W1",
    "1W": "W1",
    "W1": "W1",
    "M": "MN1",
    "1M": "MN1",
    "MN1": "MN1",
}

MT5_EXCHANGES = {"OANDA", "FOREXCOM", "FX", "FX_IDC", "TVC", "CAPITALCOM", "PEPPERSTONE", "ICMARKETS"}
BINANCE_EXCHANGES = {"BINANCE", "BINANCEUS"}


@dataclass(slots=True)
class TradingViewSymbolMapping:
    raw_ticker: str
    exchange: str
    chart_symbol: str
    base_symbol: str
    preferred_venue: Venue
    mt5_symbol: str | None
    binance_symbol: str | None
    source: str = "rule"


def load_tradingview_symbol_overrides(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    load_runtime_env(__file__)
    override_path = Path(path or os.getenv("SULTAN_TRADINGVIEW_MAP_PATH", "")).expanduser() if (path or os.getenv("SULTAN_TRADINGVIEW_MAP_PATH")) else None
    if override_path is None or not override_path.exists():
        return {}
    try:
        payload = json.loads(override_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw_symbols = payload.get("symbols", payload)
    if not isinstance(raw_symbols, dict):
        return {}
    return {str(key).upper(): dict(value) for key, value in raw_symbols.items() if isinstance(value, dict)}


def map_tradingview_interval(raw_interval: str) -> str:
    normalized = str(raw_interval or "").strip().upper()
    if normalized in TRADINGVIEW_INTERVAL_MAP:
        return TRADINGVIEW_INTERVAL_MAP[normalized]
    raise ValueError(f"Unsupported TradingView interval: {raw_interval}")


def map_tradingview_symbol(
    ticker: str,
    *,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> TradingViewSymbolMapping:
    normalized_ticker = str(ticker or "").strip().upper()
    if not normalized_ticker:
        raise ValueError("TradingView ticker is empty")

    overrides = overrides or load_tradingview_symbol_overrides()
    direct_override = overrides.get(normalized_ticker)
    if direct_override is not None:
        return _mapping_from_override(normalized_ticker, direct_override)

    exchange, chart_symbol = _split_ticker(normalized_ticker)
    chart_override = overrides.get(chart_symbol)
    if chart_override is not None:
        mapping = _mapping_from_override(normalized_ticker, chart_override)
        return TradingViewSymbolMapping(
            raw_ticker=normalized_ticker,
            exchange=exchange,
            chart_symbol=chart_symbol,
            base_symbol=mapping.base_symbol,
            preferred_venue=mapping.preferred_venue,
            mt5_symbol=mapping.mt5_symbol,
            binance_symbol=mapping.binance_symbol,
            source="override",
        )

    cleaned_symbol = _normalize_chart_symbol(chart_symbol)
    preferred_venue = _infer_venue(exchange=exchange, chart_symbol=cleaned_symbol)
    mt5_symbol = cleaned_symbol if preferred_venue == Venue.MT5 or _looks_like_fx_or_cfd(cleaned_symbol) else None
    binance_symbol = (
        _infer_binance_symbol(cleaned_symbol)
        if preferred_venue == Venue.BINANCE or (_looks_like_crypto(cleaned_symbol) and not _looks_like_fx_or_cfd(cleaned_symbol))
        else None
    )
    base_symbol = binance_symbol or mt5_symbol or cleaned_symbol

    return TradingViewSymbolMapping(
        raw_ticker=normalized_ticker,
        exchange=exchange,
        chart_symbol=cleaned_symbol,
        base_symbol=base_symbol,
        preferred_venue=preferred_venue,
        mt5_symbol=mt5_symbol,
        binance_symbol=binance_symbol,
        source="rule",
    )


def parse_tradingview_csv(path: str | Path) -> list[PriceBar]:
    rows: list[PriceBar] = []
    csv_path = Path(path)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            normalized = {str(key or "").strip().lower(): value for key, value in row.items()}
            timestamp = _parse_csv_timestamp(
                normalized.get("time")
                or normalized.get("date")
                or normalized.get("timestamp")
            )
            rows.append(
                PriceBar(
                    open=float(normalized["open"]),
                    high=float(normalized["high"]),
                    low=float(normalized["low"]),
                    close=float(normalized["close"]),
                    timestamp=timestamp,
                )
            )
    rows.sort(key=lambda item: int(item.timestamp or 0))
    return rows


def _mapping_from_override(raw_ticker: str, payload: dict[str, Any]) -> TradingViewSymbolMapping:
    preferred_venue = Venue(str(payload.get("preferred_venue") or "MT5").upper())
    exchange, chart_symbol = _split_ticker(raw_ticker)
    return TradingViewSymbolMapping(
        raw_ticker=raw_ticker,
        exchange=exchange,
        chart_symbol=str(payload.get("chart_symbol") or chart_symbol),
        base_symbol=str(payload.get("base_symbol") or payload.get("mt5_symbol") or payload.get("binance_symbol") or chart_symbol),
        preferred_venue=preferred_venue,
        mt5_symbol=payload.get("mt5_symbol"),
        binance_symbol=payload.get("binance_symbol"),
        source="override",
    )


def _split_ticker(ticker: str) -> tuple[str, str]:
    if ":" in ticker:
        exchange, chart_symbol = ticker.split(":", 1)
        return exchange.strip().upper(), chart_symbol.strip().upper()
    return "", ticker.strip().upper()


def _normalize_chart_symbol(chart_symbol: str) -> str:
    cleaned = chart_symbol.upper().replace("/", "").replace("-", "").replace("_", "").strip()
    for suffix in (".P", ".PERP", "PERP", "USDTPERP", "USDPERP"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    if cleaned.endswith("."):
        cleaned = cleaned[:-1]
    return cleaned


def _infer_venue(*, exchange: str, chart_symbol: str) -> Venue:
    if exchange in BINANCE_EXCHANGES:
        return Venue.BINANCE
    if exchange in MT5_EXCHANGES:
        return Venue.MT5
    if _looks_like_crypto(chart_symbol):
        return Venue.BINANCE
    return Venue.MT5


def _looks_like_fx_or_cfd(symbol: str) -> bool:
    return symbol.endswith(("USD", "JPY", "EUR", "GBP", "AUD", "NZD", "CHF", "CAD")) or symbol.startswith(("XAU", "XAG", "US30", "NAS"))


def _looks_like_crypto(symbol: str) -> bool:
    return symbol.endswith(("USDT", "FDUSD", "BUSD", "BTC", "ETH", "BNB", "USD"))


def _infer_binance_symbol(chart_symbol: str) -> str:
    if chart_symbol.endswith("USDT"):
        return chart_symbol
    if chart_symbol.endswith("USD"):
        return f"{chart_symbol[:-3]}USDT"
    if chart_symbol.endswith(("BTC", "ETH", "BNB", "FDUSD", "BUSD")):
        return chart_symbol
    return f"{chart_symbol}USDT"


def _parse_csv_timestamp(raw_value: str | None) -> int:
    raw = str(raw_value or "").strip()
    if not raw:
        return 0
    try:
        if raw.isdigit():
            numeric = int(raw)
            return int(numeric / 1000) if numeric > 10_000_000_000 else numeric
        if "." in raw:
            numeric = float(raw)
            return int(numeric / 1000) if numeric > 10_000_000_000 else int(numeric)
    except ValueError:
        pass

    for candidate in (raw, raw.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    raise ValueError(f"Unsupported TradingView CSV timestamp: {raw_value}")
