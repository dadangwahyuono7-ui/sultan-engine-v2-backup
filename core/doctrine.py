from __future__ import annotations

from enum import StrEnum


class SequenceRole(StrEnum):
    CMP = "CMP"
    VR = "VR"
    CF = "CF"


class ControlState(StrEnum):
    IN_CONTROL = "IN_CONTROL"
    UNDER_ATTACK = "UNDER_ATTACK"
    RECOVERED_BY_CF = "RECOVERED_BY_CF"
    LOST_TO_NEW_MASTER = "LOST_TO_NEW_MASTER"
