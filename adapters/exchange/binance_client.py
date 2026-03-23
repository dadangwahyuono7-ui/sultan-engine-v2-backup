from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sultan_engine_v2.core.execution_models import ExecutionMode, ExecutionResult, TradeIntent
from sultan_engine_v2.core.models import PriceBar, TradeDirection
from sultan_engine_v2.runtime.env_loader import load_runtime_env


BINANCE_INTERVAL_BY_TIMEFRAME = {
    "MN1": "1M",
    "W1": "1w",
    "D1": "1d",
    "H4": "4h",
    "H1": "1h",
    "M30": "30m",
    "M15": "15m",
    "M5": "5m",
    "M1": "1m",
}

DEFAULT_SPOT_BASE_URL = "https://api.binance.com"
DEFAULT_FUTURES_BASE_URL = "https://fapi.binance.com"


@dataclass(slots=True)
class BinanceResolvedSymbol:
    requested: str
    actual: str | None
    source: str
    score: int = 0


def normalize_requested_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace("-", "").replace("_", "").strip()


def normalize_binance_market_type(raw_value: str | None) -> str:
    normalized = (raw_value or "FUTURES").strip().upper()
    return "SPOT" if normalized == "SPOT" else "FUTURES"


def parse_binance_klines(rows: list[list[Any]]) -> list[PriceBar]:
    bars: list[PriceBar] = []
    for row in rows:
        bars.append(
            PriceBar(
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                timestamp=int(int(row[0]) / 1000),
            )
        )
    return bars


def resolve_symbol_from_exchange_info(exchange_info: dict, requested: str) -> BinanceResolvedSymbol:
    normalized = normalize_requested_symbol(requested)
    symbols = {item["symbol"]: item for item in exchange_info.get("symbols", []) if item.get("status") == "TRADING"}

    if normalized in symbols:
        return BinanceResolvedSymbol(requested=requested, actual=normalized, source="exact", score=100)

    if normalized.endswith("USD"):
        base = normalized[:-3]
        for quote in ("USDT", "FDUSD", "BUSD"):
            candidate = f"{base}{quote}"
            if candidate in symbols:
                return BinanceResolvedSymbol(requested=requested, actual=candidate, source="usd-alias", score=96)

    if normalized.endswith("USDT"):
        base = normalized[:-4]
        for quote in ("FDUSD", "BUSD"):
            candidate = f"{base}{quote}"
            if candidate in symbols:
                return BinanceResolvedSymbol(requested=requested, actual=candidate, source="stable-alias", score=92)

    if normalized and not normalized.endswith(("USDT", "FDUSD", "BUSD", "BTC", "ETH", "BNB", "USD")):
        candidate = f"{normalized}USDT"
        if candidate in symbols:
            return BinanceResolvedSymbol(requested=requested, actual=candidate, source="base-usdt", score=90)

    return BinanceResolvedSymbol(requested=requested, actual=None, source="missing", score=0)


class BinanceRestClient:
    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str | None = None,
        market_type: str | None = None,
        recv_window: int = 5000,
    ) -> None:
        load_runtime_env(__file__)
        self.api_key = api_key or os.getenv("BINANCE_API_KEY")
        self.api_secret = api_secret or os.getenv("BINANCE_API_SECRET")
        self.market_type = normalize_binance_market_type(market_type or os.getenv("SULTAN_BINANCE_MARKET_TYPE"))
        default_base_url = DEFAULT_FUTURES_BASE_URL if self.market_type == "FUTURES" else DEFAULT_SPOT_BASE_URL
        self.base_url = (base_url or default_base_url).rstrip("/")
        self.recv_window = recv_window
        self._exchange_info_cache: dict | None = None

    def fetch_exchange_info(self) -> dict:
        if self._exchange_info_cache is None:
            self._exchange_info_cache = self._request_json("GET", self._exchange_info_path)
        return self._exchange_info_cache

    def resolve_symbol(self, requested: str) -> BinanceResolvedSymbol:
        return resolve_symbol_from_exchange_info(self.fetch_exchange_info(), requested)

    def fetch_klines(self, requested_symbol: str, timeframe: str, limit: int = 250) -> tuple[BinanceResolvedSymbol, list[PriceBar]]:
        interval = BINANCE_INTERVAL_BY_TIMEFRAME[timeframe]
        resolved = self.resolve_symbol(requested_symbol)
        if not resolved.actual:
            return resolved, []

        rows = self._request_json(
            "GET",
            self._klines_path,
            params={"symbol": resolved.actual, "interval": interval, "limit": limit},
        )
        return resolved, parse_binance_klines(rows[:-1]) if len(rows) > 1 else []

    def fetch_price(self, requested_symbol: str) -> tuple[BinanceResolvedSymbol, float]:
        resolved = self.resolve_symbol(requested_symbol)
        if not resolved.actual:
            return resolved, 0.0

        payload = self._request_json("GET", self._price_path, params={"symbol": resolved.actual})
        return resolved, float(payload["price"])

    def fetch_book_ticker(self, requested_symbol: str) -> tuple[BinanceResolvedSymbol, dict[str, float]]:
        resolved = self.resolve_symbol(requested_symbol)
        if not resolved.actual:
            return resolved, {"bid": 0.0, "ask": 0.0}

        payload = self._request_json("GET", self._book_ticker_path, params={"symbol": resolved.actual})
        return resolved, {"bid": float(payload["bidPrice"]), "ask": float(payload["askPrice"])}

    def execute_intent(self, intent: TradeIntent) -> ExecutionResult:
        resolved = self.resolve_symbol(intent.symbol)
        actual_symbol = resolved.actual or intent.symbol

        if intent.execution_mode == ExecutionMode.PAPER:
            return ExecutionResult(
                intent_key=intent.intent_key,
                venue=intent.venue,
                symbol=actual_symbol,
                success=True,
                execution_mode=intent.execution_mode,
                message=f"Binance {self.market_type.lower()} paper execution simulated",
                executed_price=intent.entry_price,
                quantity=intent.quantity,
                metadata={**intent.metadata, "symbol_resolution": resolved.source, "market_type": self.market_type},
            )

        if not self.api_key or not self.api_secret:
            return ExecutionResult(
                intent_key=intent.intent_key,
                venue=intent.venue,
                symbol=actual_symbol,
                success=False,
                execution_mode=intent.execution_mode,
                message=f"BINANCE_API_KEY or BINANCE_API_SECRET is missing for Binance {self.market_type.lower()} live execution",
                metadata=intent.metadata,
            )

        _, book = self.fetch_book_ticker(actual_symbol)
        entry_price = book["ask"] if intent.direction == TradeDirection.BUY else book["bid"]
        quantity = intent.quantity or self._estimate_quantity(intent, actual_symbol, entry_price)
        if quantity <= 0:
            return ExecutionResult(
                intent_key=intent.intent_key,
                venue=intent.venue,
                symbol=actual_symbol,
                success=False,
                execution_mode=intent.execution_mode,
                message="Binance quantity sizing produced zero",
                metadata=intent.metadata,
            )

        adjusted_quantity = self._adjust_quantity(actual_symbol, quantity)
        payload = self._request_json(
            "POST",
            self._order_path,
            params={
                "symbol": actual_symbol,
                "side": intent.direction.value,
                "type": "MARKET",
                "quantity": adjusted_quantity,
            },
            signed=True,
        )
        return ExecutionResult(
            intent_key=intent.intent_key,
            venue=intent.venue,
            symbol=actual_symbol,
            success=True,
            execution_mode=intent.execution_mode,
            message="Binance market order sent",
            order_id=str(payload.get("orderId")),
            executed_price=entry_price,
            quantity=float(adjusted_quantity),
            metadata={**intent.metadata, "binance_response": payload, "market_type": self.market_type},
        )

    @property
    def venue_label(self) -> str:
        return "BINANCE_FUTURES" if self.market_type == "FUTURES" else "BINANCE"

    @property
    def _exchange_info_path(self) -> str:
        return "/fapi/v1/exchangeInfo" if self.market_type == "FUTURES" else "/api/v3/exchangeInfo"

    @property
    def _klines_path(self) -> str:
        return "/fapi/v1/klines" if self.market_type == "FUTURES" else "/api/v3/klines"

    @property
    def _price_path(self) -> str:
        return "/fapi/v1/ticker/price" if self.market_type == "FUTURES" else "/api/v3/ticker/price"

    @property
    def _book_ticker_path(self) -> str:
        return "/fapi/v1/ticker/bookTicker" if self.market_type == "FUTURES" else "/api/v3/ticker/bookTicker"

    @property
    def _order_path(self) -> str:
        return "/fapi/v1/order" if self.market_type == "FUTURES" else "/api/v3/order"

    @property
    def _account_path(self) -> str:
        return "/fapi/v2/account" if self.market_type == "FUTURES" else "/api/v3/account"

    def _estimate_quantity(self, intent: TradeIntent, symbol: str, entry_price: float) -> float:
        if self.market_type == "FUTURES":
            return self._estimate_futures_quantity(intent, entry_price)
        return self._estimate_spot_quantity(symbol, intent.direction, entry_price, intent.risk_fraction)

    def _estimate_spot_quantity(self, symbol: str, direction: TradeDirection, entry_price: float, risk_fraction: float) -> float:
        account = self._request_json("GET", self._account_path, signed=True)
        symbol_meta = self._get_symbol_meta(symbol)
        base_asset = symbol_meta.get("baseAsset")
        quote_asset = symbol_meta.get("quoteAsset")
        balances = {item["asset"]: float(item["free"]) for item in account.get("balances", [])}

        if direction == TradeDirection.BUY:
            quote_balance = balances.get(quote_asset, 0.0)
            if quote_balance <= 0 or entry_price <= 0:
                return 0.0
            return (quote_balance * risk_fraction) / entry_price

        base_balance = balances.get(base_asset, 0.0)
        if base_balance <= 0:
            return 0.0
        return base_balance * risk_fraction

    def _estimate_futures_quantity(self, intent: TradeIntent, entry_price: float) -> float:
        if entry_price <= 0:
            return 0.0

        stop_distance = abs(entry_price - float(intent.stop_loss or 0.0))
        risk_fraction = max(float(intent.risk_fraction), 0.0)
        leverage_cap = max(float(os.getenv("SULTAN_BINANCE_FUTURES_MAX_LEVERAGE", "3") or "3"), 1.0)
        account = self._request_json("GET", self._account_path, signed=True)
        wallet_balance = float(
            account.get("availableBalance")
            or account.get("totalWalletBalance")
            or account.get("totalMarginBalance")
            or 0.0
        )
        if wallet_balance <= 0:
            return 0.0

        quantity_by_margin = (wallet_balance * leverage_cap) / entry_price
        if stop_distance <= 0 or risk_fraction <= 0:
            return quantity_by_margin

        quantity_by_risk = (wallet_balance * risk_fraction) / stop_distance
        return min(quantity_by_risk, quantity_by_margin)

    def _adjust_quantity(self, symbol: str, quantity: float) -> str:
        symbol_meta = self._get_symbol_meta(symbol)
        lot_filter = next((item for item in symbol_meta.get("filters", []) if item.get("filterType") == "LOT_SIZE"), None)
        if lot_filter is None:
            return str(quantity)

        step_size = Decimal(lot_filter["stepSize"])
        min_qty = Decimal(lot_filter["minQty"])
        desired = Decimal(str(quantity))
        if desired < min_qty:
            desired = min_qty

        steps = (desired / step_size).to_integral_value(rounding=ROUND_DOWN)
        adjusted = steps * step_size
        return format(adjusted.normalize(), "f")

    def _get_symbol_meta(self, symbol: str) -> dict:
        exchange_info = self.fetch_exchange_info()
        for item in exchange_info.get("symbols", []):
            if item.get("symbol") == symbol:
                return item
        raise KeyError(symbol)

    def _request_json(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        params = dict(params or {})
        headers = {}
        if signed:
            if not self.api_key or not self.api_secret:
                raise RuntimeError("Binance signed request requires API credentials")
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = self.recv_window
            query = urlencode(params)
            signature = hmac.new(self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
            params["signature"] = signature
            headers["X-MBX-APIKEY"] = self.api_key

        query_string = urlencode(params)
        url = f"{self.base_url}{path}"
        data = None
        if method.upper() == "GET":
            if query_string:
                url = f"{url}?{query_string}"
        else:
            data = query_string.encode("utf-8")

        request = Request(url=url, data=data, method=method.upper(), headers=headers)
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:  # pragma: no cover - network dependent
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Binance HTTP error {exc.code}: {detail}") from exc
        except URLError as exc:  # pragma: no cover - network dependent
            raise RuntimeError(f"Binance network error: {exc.reason}") from exc
