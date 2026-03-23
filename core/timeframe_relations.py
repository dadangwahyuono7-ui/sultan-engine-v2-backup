from __future__ import annotations

TRADING_TIMEFRAMES = ("MN1", "W1", "D1", "H4", "H1", "M30", "M15", "M5", "M1")

VR_CHILD_BY_PARENT = {
    "MN1": "W1",
    "W1": "D1",
    "D1": "H4",
    "H4": "H1",
    "H1": "M15",
    "M15": "M5",
    "M5": "M1",
}

CF_LOW_RISK_BY_PARENT = dict(VR_CHILD_BY_PARENT)

CF_HIGH_RISK_BY_PARENT = {
    "MN1": "D1",
    "W1": "H4",
    "D1": "H1",
    "H4": "M15",
    "H1": "M5",
    "M15": "M1",
    "M5": "M1",
}

PARENT_BY_CHILD = {child: parent for parent, child in VR_CHILD_BY_PARENT.items()}

TARGET_TF_BY_CF = {
    "M5": "M15",
    "M15": "M30",
    "M30": "H1",
    "H1": "H4",
    "H4": "D1",
}


def get_parent_timeframe(child_timeframe: str) -> str | None:
    return PARENT_BY_CHILD.get(child_timeframe)


def get_vr_child(parent_timeframe: str) -> str | None:
    return VR_CHILD_BY_PARENT.get(parent_timeframe)


def get_high_risk_cf_child(parent_timeframe: str) -> str | None:
    return CF_HIGH_RISK_BY_PARENT.get(parent_timeframe)


def get_target_timeframe(cf_timeframe: str) -> str | None:
    return TARGET_TF_BY_CF.get(cf_timeframe)
