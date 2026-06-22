"""Pack Observation contract (plan.md section 3.4).

One record per grouped pack after identification + attribution. The audit trail behind every
derived number.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

IdMethod = Literal["ocr", "name", "embedding"]


class IdentifiedCard(BaseModel):
    """An identified card within a pack, tagged with its attributed slot."""

    cardId: str
    rarity: str
    types: list[str] = Field(default_factory=list)
    races: list[str] = Field(default_factory=list)
    isFoil: bool = False
    assignedSlot: str
    idMethod: IdMethod
    confidence: float


class PackObservation(BaseModel):
    """One grouped pack. ``groupingConfidence`` is 1.0 for controlled one-pack-per-shot."""

    sourceId: str
    setCode: str
    packIndex: int
    groupingConfidence: float = 1.0
    cards: list[IdentifiedCard] = Field(default_factory=list)
    unresolved: int = 0  # detections that could not be identified
