"""Pack Template contract (plan.md section 3.3).

The deterministic skeleton of a pack: its ordered slots, which are ``fixed`` (single-rarity,
no sampling) vs ``lottery`` (need a derived distribution), and the attribution predicates that
recognise which physical card fills each lottery slot.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SlotKind = Literal["fixed", "lottery"]


class SlotDef(BaseModel):
    """One slot in the pack layout. ``count`` cards fill this slot per pack.

    ``distinguish`` lists the card types that *split* this lottery slot's outcomes (plan.md
    section 5): e.g. ``["Ruler"]`` makes a Ruler card a distinct outcome from a non-Ruler of the
    same rarity, and ``["Magic Stone"]`` restricts the slot's pool to magic stones. Empty (the
    default) means outcomes are keyed by rarity alone. Ignored for ``fixed`` slots.
    """

    slot: str
    kind: SlotKind
    count: int = 1
    distinguish: list[str] = Field(default_factory=list)


class MatchPredicate(BaseModel):
    """A card-matching predicate for an attribution rule.

    All present fields must hold (logical AND). Field semantics:
    ``isFoil`` matches the foil flag; ``anyType``/``anyRace`` match if the card has *any* of the
    listed types/races; ``rarityIn`` matches membership; ``cardIdPrefix`` matches an id prefix.
    """

    isFoil: bool | None = None
    anyType: list[str] | None = None
    anyRace: list[str] | None = None
    rarityIn: list[str] | None = None
    cardIdPrefix: str | None = None


class AttributionRule(BaseModel):
    """Assign a card to ``slot`` when ``match`` holds. Rules are evaluated first-match-wins."""

    slot: str
    match: MatchPredicate


class Attribution(BaseModel):
    """How to assign identified cards in a pack to lottery slots.

    Rules are evaluated top-to-bottom; first match wins. Cards matching no rule fall to the
    fixed slots (plan.md section 3.3 / stage [6]).
    """

    rules: list[AttributionRule] = Field(default_factory=list)


class PackTemplate(BaseModel):
    setCode: str
    packSize: int
    layout: list[SlotDef] = Field(default_factory=list)
    attribution: Attribution = Field(default_factory=Attribution)

    def lottery_slots(self) -> list[SlotDef]:
        return [s for s in self.layout if s.kind == "lottery"]

    def fixed_slots(self) -> list[SlotDef]:
        return [s for s in self.layout if s.kind == "fixed"]
