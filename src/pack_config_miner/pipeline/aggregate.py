"""Stage [7] -- Aggregate & emit (plan.md sections 4-6).

Fold Pack Observations into a :class:`~..contracts.pack_config.PackConfig`:

1. **Signature** each lottery-slot card by the minimal outcome descriptor -- its rarity plus the
   conditions implied by the slot's ``distinguish`` types (plan.md section 5).
2. **Tally** identical signatures per slot across every pack.
3. **Minimise**: within a (slot, rarity), if no card carries a distinguishing type the
   ``equals:false`` conditions are vacuous, so they are stripped -- the split only survives when
   both branches are actually observed.
4. **Normalise** each slot's counts to integer ``chance`` via **largest-remainder** so they sum
   to exactly 100, and emit the outcomes (most frequent first).

The fixed slots need no observation; they are emitted as bare-rarity tokens in ``slots``.
``SlotTally`` carries the raw counts forward so the Confidence Report (M6) can compute CIs.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..contracts.observation import PackObservation
from ..contracts.pack_config import Condition, Outcome, PackConfig
from ..contracts.template import PackTemplate, SlotDef
from .resolve import base_card_id

# An outcome signature: rarity plus ordered (equals, type) condition pairs.
Conditions = tuple[tuple[bool, str], ...]
Signature = tuple[str | None, Conditions]


@dataclass(frozen=True)
class OutcomeTally:
    """One folded outcome of a lottery slot, with its raw count and normalised chance."""

    label: str
    rarity: str | None
    conditions: Conditions
    count: int
    chance: int


@dataclass(frozen=True)
class SlotTally:
    """Per-lottery-slot rollup: total cards observed and the folded outcomes."""

    slot: str
    samples: int
    outcomes: list[OutcomeTally] = field(default_factory=list)


@dataclass(frozen=True)
class AggregateResult:
    """The emitted config plus the tallies behind it (the audit trail for confidence)."""

    config: PackConfig
    tallies: list[SlotTally] = field(default_factory=list)
    debiased: int = 0  # lingered hits dropped (same card in consecutive heuristic packs)


def signature(rarity: str | None, types: list[str], distinguish: list[str]) -> Signature:
    """Minimal outcome descriptor for a card (plan.md section 5)."""
    specials = [t for t in distinguish if t in types]
    if specials:
        conditions: Conditions = tuple((True, t) for t in specials)
    elif distinguish:
        conditions = tuple((False, t) for t in distinguish)
    else:
        conditions = ()
    return (rarity, conditions)


def _minimise(counts: Counter[Signature]) -> Counter[Signature]:
    """Strip vacuous ``equals:false`` conditions for a rarity that never split (no special seen)."""
    by_rarity: dict[str | None, list[Signature]] = defaultdict(list)
    for sig in counts:
        by_rarity[sig[0]].append(sig)

    folded: Counter[Signature] = Counter()
    for _rarity, sigs in by_rarity.items():
        has_true = any(any(equals for equals, _ in conds) for _, conds in sigs)
        for sig in sigs:
            rarity, conds = sig
            new_sig: Signature = (rarity, conds if has_true else ())
            folded[new_sig] += counts[sig]
    return folded


def _signature_str(sig: Signature) -> str:
    rarity, conds = sig
    return f"{rarity}|" + ",".join(f"{int(eq)}:{t}" for eq, t in conds)


def largest_remainder(counts: Counter[Signature], total: int = 100) -> dict[Signature, int]:
    """Distribute ``total`` over ``counts`` as integers summing to ``total`` (largest-remainder).

    Ties in the fractional remainder break by raw count (desc) then a stable signature string, so
    the result is fully deterministic.
    """
    n = sum(counts.values())
    if n == 0:
        return {}
    quotas = {sig: counts[sig] * total / n for sig in counts}
    floors = {sig: math.floor(q) for sig, q in quotas.items()}
    remainder = total - sum(floors.values())
    order = sorted(
        counts,
        key=lambda sig: (quotas[sig] - floors[sig], counts[sig], _signature_str(sig)),
        reverse=True,
    )
    for sig in order[:remainder]:
        floors[sig] += 1
    return floors


def _label(sig: Signature) -> str:
    rarity, conds = sig
    base = rarity or "?"
    if not conds:
        return base
    parts = [("" if equals else "non-") + t for equals, t in conds]
    return f"{base} ({', '.join(parts)})"


def _to_conditions(conds: Conditions) -> list[Condition] | None:
    if not conds:
        return None
    return [Condition(equals=equals, type=t) for equals, t in conds]


def _floor_observed(chances: dict[Signature, int]) -> dict[Signature, int]:
    """Guarantee every *observed* outcome shows at least 1 (an observed hit is never 0%, §6).

    Largest-remainder can round a rare-but-real outcome (e.g. one XR seen) down to 0; bump those to
    1, taking the points back from the largest outcomes so the slot still sums to 100.
    """
    zeros = [sig for sig, c in chances.items() if c == 0]
    for sig in zeros:
        chances[sig] = 1
    for _ in zeros:
        donor = max((s for s in chances if chances[s] > 1), key=lambda s: chances[s], default=None)
        if donor is None:
            break
        chances[donor] -= 1
    return chances


def _tally_slot(slot: SlotDef, counts: Counter[Signature]) -> tuple[SlotTally, list[Outcome]]:
    folded = _minimise(counts)
    chances = _floor_observed(largest_remainder(folded))
    # Emit most-frequent first; deterministic tie-break by rarity then conditions.
    order = sorted(folded, key=lambda sig: (-folded[sig], sig[0] or "", _signature_str(sig)))
    outcomes = [
        Outcome(chance=chances[sig], rarity=sig[0], conditions=_to_conditions(sig[1]))
        for sig in order
    ]
    tally = SlotTally(
        slot=slot.slot,
        samples=sum(folded.values()),
        outcomes=[
            OutcomeTally(
                label=_label(sig),
                rarity=sig[0],
                conditions=sig[1],
                count=folded[sig],
                chance=chances[sig],
            )
            for sig in order
        ],
    )
    return tally, outcomes


def aggregate(
    observations: list[PackObservation],
    template: PackTemplate,
    *,
    max_hits_per_card: int = 1,
) -> AggregateResult:
    """Fold observations into a :class:`PackConfig` plus per-slot tallies.

    **Per-card hit cap (table-bleed de-biasing):** in *heuristically grouped* footage
    (``groupingConfidence < 1``), a pulled rare the host leaves on the table is re-detected as the
    "hit" in many later packs, over-counting it. So within a lottery slot each distinct card
    (:func:`base_card_id`) is counted at most ``max_hits_per_card`` times (default 1); further
    appearances are dropped as bleed. Gated on grouping confidence so controlled (one-shot-per-pack)
    footage -- where two real packs can legitimately pull the same card -- is never de-biased.
    """
    lottery_slots = {s.slot: s for s in template.lottery_slots()}
    raw: dict[str, Counter[Signature]] = {slot: Counter() for slot in lottery_slots}
    hits: dict[str, Counter[str]] = {slot: Counter() for slot in lottery_slots}
    debiased = 0

    for obs in observations:
        heuristic = obs.groupingConfidence < 1.0
        for card in obs.cards:
            slot = lottery_slots.get(card.assignedSlot)
            if slot is None:
                continue
            if heuristic:
                base = base_card_id(card.cardId)
                if hits[card.assignedSlot][base] >= max_hits_per_card:
                    debiased += 1  # this rare already counted -> a table-bleed re-detection
                    continue
                hits[card.assignedSlot][base] += 1
            raw[card.assignedSlot][signature(card.rarity, card.types, slot.distinguish)] += 1

    lottery: dict[str, list[Outcome]] = {}
    tallies: list[SlotTally] = []
    for slot_name, slot_def in lottery_slots.items():
        tally, outcomes = _tally_slot(slot_def, raw[slot_name])
        lottery[slot_name] = outcomes
        tallies.append(tally)

    slot_tokens = [s.slot for s in template.layout for _ in range(s.count)]
    config = PackConfig(slots=slot_tokens, lottery=lottery)
    return AggregateResult(config=config, tallies=tallies, debiased=debiased)
