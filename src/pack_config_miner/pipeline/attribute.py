"""Stage [6] -- Slot attribution (plan.md section 4).

Run the Pack Template's ``attribution.rules`` (first-match-wins) to tag each resolved card with
its ``assignedSlot``; cards matching no rule fall to the fixed slot named for their rarity. Then
validate the pack against the template (right card count per slot) -- **mismatches are flagged,
not dropped** -- and emit a :class:`~..contracts.observation.PackObservation`.
"""

from __future__ import annotations

from collections import Counter

from ..contracts.observation import IdentifiedCard, PackObservation
from ..contracts.template import MatchPredicate, PackTemplate
from .group import PackGroup
from .resolve import ResolvedDetection


def card_matches(
    pred: MatchPredicate,
    *,
    rarity: str | None,
    types: list[str],
    races: list[str],
    is_foil: bool | None,
    card_id: str | None,
) -> bool:
    """True if every present field of ``pred`` holds for the card (logical AND, plan.md 3.3).

    Each line reads "field unset, or field matches" -- an absent predicate field is a wildcard.
    """
    return (
        (pred.isFoil is None or bool(is_foil) == pred.isFoil)
        and (pred.anyType is None or bool(set(types) & set(pred.anyType)))
        and (pred.anyRace is None or bool(set(races) & set(pred.anyRace)))
        and (pred.rarityIn is None or rarity in pred.rarityIn)
        and (pred.cardIdPrefix is None or (card_id or "").startswith(pred.cardIdPrefix))
    )


# Rough rarity ranking (rarer = higher) so the best card claims a contested slot first -- this is
# what lets a guaranteed-R slot and the variable RARE hit each land correctly (a SR hit takes RARE
# before a plain R does). Unknown rarities rank 0.
_RARITY_RANK = {"N": 0, "C": 0, "U": 1, "R": 2, "SR": 3, "MR": 4, "XR": 5}


def _rarity_rank(rarity: str | None) -> int:
    return _RARITY_RANK.get(rarity or "", 0)


def _assign_slot(
    r: ResolvedDetection,
    template: PackTemplate,
    fixed_rarities: set[str],
    capacity: dict[str, int],
) -> str | None:
    """First rule whose slot still has capacity (else the fixed slot for the rarity, if free)."""
    for rule in template.attribution.rules:
        if capacity.get(rule.slot, 0) > 0 and card_matches(
            rule.match,
            rarity=r.rarity,
            types=r.types,
            races=r.races,
            is_foil=r.detection.isFoil,
            card_id=r.cardId,
        ):
            return rule.slot
    if r.rarity in fixed_rarities and capacity.get(r.rarity, 0) > 0:
        return r.rarity
    return None


def attribute_pack(group: PackGroup, template: PackTemplate) -> tuple[PackObservation, list[str]]:
    """Attribute one pack's cards to slots and validate counts. Returns the observation + flags.

    Capacity-aware and rarity-ranked: cards are placed rarest-first and each slot fills only to its
    template ``count``, so the guaranteed-R card and the variable RARE hit each find their own slot
    instead of both piling into the lottery.
    """
    flags = list(group.flags)
    fixed_rarities = {s.slot for s in template.fixed_slots()}
    capacity = {s.slot: s.count for s in template.layout}
    unresolved = 0

    # Place rarest cards first (stable for ties), but keep the original reveal order in the output.
    order = sorted(
        range(len(group.resolved)),
        key=lambda i: _rarity_rank(group.resolved[i].rarity),
        reverse=True,
    )
    placed: dict[int, IdentifiedCard] = {}
    for i in order:
        r = group.resolved[i]
        if r.cardId is None or r.rarity is None or r.idMethod is None:
            unresolved += 1
            continue
        slot = _assign_slot(r, template, fixed_rarities, capacity)
        if slot is None:
            flags.append(f"pack {group.packIndex}: card {r.cardId} ({r.rarity}) matched no slot")
            continue
        capacity[slot] -= 1
        placed[i] = IdentifiedCard(
            cardId=r.cardId,
            rarity=r.rarity,
            types=r.types,
            races=r.races,
            isFoil=bool(r.detection.isFoil),
            assignedSlot=slot,
            idMethod=r.idMethod,
            confidence=r.detection.confidence,
        )

    cards = [placed[i] for i in sorted(placed)]  # restore original reveal order

    counts = Counter(c.assignedSlot for c in cards)
    for slot_def in template.layout:
        actual = counts.get(slot_def.slot, 0)
        if actual != slot_def.count:
            flags.append(
                f"pack {group.packIndex}: slot {slot_def.slot} has {actual} cards, "
                f"expected {slot_def.count}"
            )

    observation = PackObservation(
        sourceId=group.sourceId,
        setCode=group.setCode,
        packIndex=group.packIndex,
        groupingConfidence=group.groupingConfidence,
        cards=cards,
        unresolved=unresolved,
    )
    return observation, flags
