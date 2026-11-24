"""Laufzeitdaten je Eintrag: Client, Stand der Anrufe, Verbindung des Ereignisstroms."""
from __future__ import annotations

from dataclasses import dataclass, field

from .api import LieschenClient


@dataclass
class LieschenData:
    client: LieschenClient
    devices: list[dict] = field(default_factory=list)
    agent: str = ""
    calls: dict[int, dict] = field(default_factory=dict)
    connected: bool = False
    last: dict | None = None
