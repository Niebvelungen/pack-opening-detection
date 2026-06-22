"""Tests for stage [6] attribution: first-match-wins rules, fixed leftovers, count validation."""

from __future__ import annotations

from collections.abc import Sequence

from pack_config_miner.contracts.template import (
    Attribution,
    AttributionRule,
    MatchPredicate,
    PackTemplate,
    SlotDef,
)
from pack_config_miner.pipeline.attribute import attribute_pack, card_matches
from pack_config_miner.pipeline.group import PackGroup
from pack_config_miner.pipeline.identify.base import Detection
from pack_config_miner.pipeline.resolve import ResolvedDetection

TEMPLATE = PackTemplate(
    setCode="GLD",
    packSize=4,
    layout=[
        SlotDef(slot="C", kind="fixed", count=1),
        SlotDef(slot="BS", kind="lottery", count=1, distinguish=["Magic Stone"]),
        SlotDef(slot="R-SR-MR", kind="lottery", count=1),
        SlotDef(slot="FOIL", kind="lottery", count=1),
    ],
    attribution=Attribution(
        rules=[
            AttributionRule(slot="FOIL", match=MatchPredicate(isFoil=True)),
            AttributionRule(slot="BS", match=MatchPredicate(anyType=["Magic Stone"])),
            AttributionRule(slot="R-SR-MR", match=MatchPredicate(rarityIn=["R", "SR", "MR"])),
        ]
    ),
)


def _rc(
    rarity: str | None,
    *,
    types: Sequence[str] = (),
    is_foil: bool = False,
    card_id: str | None = "GLD-001",
) -> ResolvedDetection:
    det = Detection(sourceId="s1", frameOrdinal=0, cardId=card_id, isFoil=is_foil, confidence=0.9)
    if card_id is None:
        return ResolvedDetection(detection=det)  # unresolved
    return ResolvedDetection(
        detection=det, cardId=card_id, rarity=rarity, types=list(types), idMethod="ocr"
    )


def _group(resolved: list[ResolvedDetection]) -> PackGroup:
    return PackGroup(
        sourceId="s1", setCode="GLD", packIndex=0, groupingConfidence=1.0, resolved=resolved
    )


def test_card_matches_predicate_fields() -> None:
    assert card_matches(
        MatchPredicate(isFoil=True), rarity="R", types=[], races=[], is_foil=True, card_id="x"
    )
    assert not card_matches(
        MatchPredicate(isFoil=True), rarity="R", types=[], races=[], is_foil=None, card_id="x"
    )
    assert card_matches(
        MatchPredicate(rarityIn=["R", "SR"]),
        rarity="SR",
        types=[],
        races=[],
        is_foil=False,
        card_id="x",
    )
    assert card_matches(
        MatchPredicate(anyType=["Magic Stone"]),
        rarity="C",
        types=["Magic Stone"],
        races=[],
        is_foil=False,
        card_id="x",
    )
    assert card_matches(
        MatchPredicate(cardIdPrefix="GLD-"),
        rarity="C",
        types=[],
        races=[],
        is_foil=False,
        card_id="GLD-1",
    )


def test_attribution_first_match_wins_and_fixed_leftover() -> None:
    cards = [
        _rc("C", card_id="GLD-001"),  # common -> fixed slot "C"
        _rc("C", types=["Magic Stone"], card_id="GLD-010"),  # -> BS
        _rc("R", card_id="GLD-020"),  # -> R-SR-MR
        _rc("SR", is_foil=True, card_id="GLD-022"),  # foil rule wins over rarityIn -> FOIL
    ]
    obs, flags = attribute_pack(_group(cards), TEMPLATE)
    slots = {c.cardId: c.assignedSlot for c in obs.cards}
    assert slots == {"GLD-001": "C", "GLD-010": "BS", "GLD-020": "R-SR-MR", "GLD-022": "FOIL"}
    assert obs.unresolved == 0
    assert flags == []  # counts all match (1 each)


def test_capacity_aware_overflow_is_flagged() -> None:
    # Two rares but only one R-SR-MR slot: the rarer (SR) claims it; the R can't be placed.
    cards = [_rc("R", card_id="GLD-020"), _rc("SR", card_id="GLD-022")]
    obs, flags = attribute_pack(_group(cards), TEMPLATE)
    assert {c.cardId: c.assignedSlot for c in obs.cards} == {"GLD-022": "R-SR-MR"}
    assert any("GLD-020 (R) matched no slot" in f for f in flags)  # flagged, not silent
    assert any("slot C has 0 cards, expected 1" in f for f in flags)


def test_guaranteed_r_and_rare_hit_each_get_a_slot() -> None:
    # The structure the user described: a guaranteed R (fixed) + the variable RARE hit + a foil.
    template = PackTemplate(
        setCode="JRV",
        packSize=3,
        layout=[
            SlotDef(slot="R", kind="fixed", count=1),
            SlotDef(slot="RARE", kind="lottery", count=1),
            SlotDef(slot="FOIL", kind="lottery", count=1),
        ],
        attribution=Attribution(
            rules=[
                AttributionRule(slot="FOIL", match=MatchPredicate(isFoil=True)),
                AttributionRule(
                    slot="RARE", match=MatchPredicate(rarityIn=["R", "SR", "MR", "XR"])
                ),
            ]
        ),
    )
    cards = [
        _rc("R", card_id="JRV-001"),  # the guaranteed R
        _rc("SR", card_id="JRV-002"),  # the RARE hit (rarer -> claims RARE first)
        _rc("R", is_foil=True, card_id="JRV-003"),  # the foil
    ]
    obs, flags = attribute_pack(_group(cards), template)
    assert {c.cardId: c.assignedSlot for c in obs.cards} == {
        "JRV-001": "R",
        "JRV-002": "RARE",
        "JRV-003": "FOIL",
    }
    assert not [f for f in flags if "matched no slot" in f]


def test_unresolved_cards_counted_not_attributed() -> None:
    cards = [_rc("C", card_id="GLD-001"), _rc(None, card_id=None)]
    obs, _flags = attribute_pack(_group(cards), TEMPLATE)
    assert obs.unresolved == 1
    assert [c.cardId for c in obs.cards] == ["GLD-001"]
