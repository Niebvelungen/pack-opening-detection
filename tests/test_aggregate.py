"""Tests for stage [7] aggregation primitives: signatures and largest-remainder."""

from __future__ import annotations

from collections import Counter

from pack_config_miner.pipeline.aggregate import (
    Signature,
    largest_remainder,
    signature,
)


def test_signature_distinguish_split() -> None:
    assert signature("R", ["Resonator"], ["Ruler"]) == ("R", ((False, "Ruler"),))
    assert signature("R", ["Ruler"], ["Ruler"]) == ("R", ((True, "Ruler"),))
    assert signature("C", ["Magic Stone"], ["Magic Stone"]) == ("C", ((True, "Magic Stone"),))


def test_signature_no_distinguish_is_rarity_only() -> None:
    assert signature("C", ["Resonator"], []) == ("C", ())


def test_largest_remainder_sums_to_100() -> None:
    counts: Counter[Signature] = Counter({("R", ()): 5, ("SR", ()): 2, ("MR", ()): 1})
    chances = largest_remainder(counts)
    assert sum(chances.values()) == 100
    # 62.5 / 25.0 / 12.5 -> the larger remainder (R, by count tie-break) takes the spare point.
    assert chances == {("R", ()): 63, ("SR", ()): 25, ("MR", ()): 12}


def test_largest_remainder_empty() -> None:
    assert largest_remainder(Counter()) == {}


def test_largest_remainder_single_outcome() -> None:
    assert largest_remainder(Counter({("C", ()): 8})) == {("C", ()): 100}


def test_observed_rare_outcome_never_rounds_to_zero() -> None:
    # One XR among hundreds floors to 0%; an observed outcome must show >= 1% and still sum to 100.
    from pack_config_miner.contracts.observation import IdentifiedCard, PackObservation
    from pack_config_miner.contracts.template import PackTemplate, SlotDef
    from pack_config_miner.pipeline.aggregate import aggregate

    template = PackTemplate(
        setCode="X", packSize=1, layout=[SlotDef(slot="RARE", kind="lottery", count=1)]
    )
    cards = [
        *[
            IdentifiedCard(
                cardId=f"X-{i}",
                rarity="R",
                assignedSlot="RARE",
                idMethod="embedding",
                confidence=1.0,
            )
            for i in range(199)
        ],
        IdentifiedCard(
            cardId="X-XR", rarity="XR", assignedSlot="RARE", idMethod="embedding", confidence=1.0
        ),
    ]
    obs = [PackObservation(sourceId="s", setCode="X", packIndex=0, cards=cards)]
    outcomes = aggregate(obs, template).config.lottery["RARE"]
    by_rarity = {o.rarity: o.chance for o in outcomes}
    assert by_rarity["XR"] >= 1  # the lone XR is not shown as 0%
    assert sum(o.chance for o in outcomes) == 100


def _rare_pack(card_id: str, rarity: str, *, confidence_grouping: float) -> object:
    from pack_config_miner.contracts.observation import IdentifiedCard, PackObservation

    return PackObservation(
        sourceId="s",
        setCode="X",
        packIndex=0,
        groupingConfidence=confidence_grouping,
        cards=[
            IdentifiedCard(
                cardId=card_id,
                rarity=rarity,
                assignedSlot="RARE",
                idMethod="embedding",
                confidence=1.0,
            )
        ],
    )


def _rare_template() -> object:
    from pack_config_miner.contracts.template import PackTemplate, SlotDef

    return PackTemplate(
        setCode="X", packSize=1, layout=[SlotDef(slot="RARE", kind="lottery", count=1)]
    )


def test_table_bleed_cap_counts_each_hit_once() -> None:
    from pack_config_miner.pipeline.aggregate import aggregate

    # A pulled MR left on the table is re-detected as the hit in non-adjacent packs -> count once.
    obs = [
        _rare_pack("X-1", "MR", confidence_grouping=0.6),
        _rare_pack("X-2", "SR", confidence_grouping=0.6),
        _rare_pack("X-1", "MR", confidence_grouping=0.6),  # table bleed of X-1
    ]
    result = aggregate(obs, _rare_template())  # type: ignore[arg-type]
    assert result.debiased == 1
    assert {o.rarity: o.chance for o in result.config.lottery["RARE"]} == {"MR": 50, "SR": 50}


def test_hit_cap_is_per_card_not_per_rarity() -> None:
    from pack_config_miner.pipeline.aggregate import aggregate

    # Two *different* MR cards are two real hits -> both counted (the cap is per card id).
    obs = [
        _rare_pack("X-1", "MR", confidence_grouping=0.6),
        _rare_pack("X-2", "MR", confidence_grouping=0.6),
    ]
    result = aggregate(obs, _rare_template())  # type: ignore[arg-type]
    assert result.debiased == 0
    assert {o.rarity: o.chance for o in result.config.lottery["RARE"]} == {"MR": 100}


def test_hit_cap_kept_when_controlled() -> None:
    from pack_config_miner.pipeline.aggregate import aggregate

    # Controlled footage: the same card in two real packs is genuine -> NOT capped.
    obs = [
        _rare_pack("X-1", "MR", confidence_grouping=1.0),
        _rare_pack("X-1", "MR", confidence_grouping=1.0),
        _rare_pack("X-2", "SR", confidence_grouping=1.0),
    ]
    result = aggregate(obs, _rare_template())  # type: ignore[arg-type]
    assert result.debiased == 0
    assert {o.rarity: o.chance for o in result.config.lottery["RARE"]} == {"MR": 67, "SR": 33}


def test_hit_cap_respects_max_hits_per_card() -> None:
    from pack_config_miner.pipeline.aggregate import aggregate

    # max_hits=2 (e.g. a double box): the same card may legitimately be a hit twice, not thrice.
    obs = [_rare_pack("X-1", "MR", confidence_grouping=0.6) for _ in range(3)]
    obs.append(_rare_pack("X-2", "SR", confidence_grouping=0.6))
    result = aggregate(obs, _rare_template(), max_hits_per_card=2)  # type: ignore[arg-type]
    assert result.debiased == 1  # third X-1 dropped; two kept
    assert {o.rarity: o.chance for o in result.config.lottery["RARE"]} == {"MR": 67, "SR": 33}
