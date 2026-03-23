from __future__ import annotations

import re
from dataclasses import dataclass

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - optional dependency at runtime
    mt5 = None


@dataclass(frozen=True, slots=True)
class ResolvedSymbol:
    requested: str
    actual: str | None
    source: str
    score: int


def normalize_symbol_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def _score_symbol_candidate(base_symbol: str, candidate: str) -> int:
    base = normalize_symbol_name(base_symbol)
    current = normalize_symbol_name(candidate)

    if current == base:
        return 100
    if current.startswith(base):
        return 90 - max(0, len(current) - len(base))
    if current.endswith(base):
        return 85 - max(0, len(current) - len(base))
    if base in current:
        return 70 - max(0, len(current) - len(base))
    return 0


def resolve_symbol(base_symbol: str, enable_symbol: bool = True) -> ResolvedSymbol:
    if mt5 is None:
        return ResolvedSymbol(requested=base_symbol, actual=None, source="unavailable", score=0)

    legacy_suffixes = ["", "m", ".", "_", "i", ".m", "pro", "-pro", ".r"]
    for suffix in legacy_suffixes:
        candidate = f"{base_symbol}{suffix}"
        info = mt5.symbol_info(candidate)
        if info is not None:
            if enable_symbol and not getattr(info, "visible", True):
                mt5.symbol_select(candidate, True)
            return ResolvedSymbol(requested=base_symbol, actual=candidate, source="legacy-suffix", score=100)

    symbols = mt5.symbols_get() or []
    best_name: str | None = None
    best_score = 0
    for symbol in symbols:
        name = getattr(symbol, "name", "")
        score = _score_symbol_candidate(base_symbol, name)
        if score > best_score:
            best_name = name
            best_score = score

    if best_name:
        info = mt5.symbol_info(best_name)
        if info is not None and enable_symbol and not getattr(info, "visible", True):
            mt5.symbol_select(best_name, True)
        return ResolvedSymbol(requested=base_symbol, actual=best_name, source="symbol-scan", score=best_score)

    return ResolvedSymbol(requested=base_symbol, actual=None, source="missing", score=0)


def resolve_many(base_symbols: list[str]) -> list[ResolvedSymbol]:
    return [resolve_symbol(symbol) for symbol in base_symbols]
